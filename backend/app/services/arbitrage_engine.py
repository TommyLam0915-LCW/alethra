"""Triangular arbitrage detection and backtesting engine.

Detects price inconsistencies across three crypto pairs:
  BTC-USD, ETH-USD, ETH-BTC

The triangular relationship: ETH-USD = ETH-BTC × BTC-USD
When this equality breaks beyond a configurable threshold (in basis points),
an arbitrage opportunity exists: buy the cheap leg, sell the expensive leg.

This engine fetches real OHLCV from Kraken for all three pairs, aligns them
by timestamp, computes the implied cross rate, and simulates trades when
the spread exceeds the minimum threshold.

Design: separate from the single-instrument research engine because arbitrage
is inherently multi-instrument. The existing rule/signal framework processes
one instrument's indicators; arbitrage needs three price series aligned on
the same time grid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.providers.crypto_registry import get_crypto_provider
from app.services.crypto_series import CandleSeries

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ArbitrageConfig:
    """Parameters for one arbitrage backtest run."""
    min_spread_bps: Decimal = Decimal("20")
    target_atr_mult: Decimal = Decimal("1.5")
    stop_atr_mult: Decimal = Decimal("1.0")
    time_stop_bars: int = 8
    starting_cash: Decimal = Decimal("10000")
    notional_per_trade: Decimal = Decimal("1000")
    commission_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.001")
    config_id: str = "balanced"


@dataclass
class ArbitrageBar:
    """One timestamp-aligned row across all three legs."""
    open_time: datetime
    btc_usd: Decimal
    eth_usd: Decimal
    eth_btc: Decimal
    implied_eth_usd: Decimal
    spread_bps: Decimal
    direction: int  # 1 = buy ETH-USD / sell ETH-BTC route, -1 = opposite, 0 = no signal


@dataclass
class ArbitrageTrade:
    """One completed arbitrage round-trip."""
    entry_time: datetime
    exit_time: datetime
    entry_spread_bps: Decimal
    exit_spread_bps: Decimal
    direction: int
    entry_btc_usd: Decimal
    entry_eth_usd: Decimal
    entry_eth_btc: Decimal
    exit_btc_usd: Decimal
    exit_eth_usd: Decimal
    exit_eth_btc: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    exit_reason: str
    bars_held: int


@dataclass
class ArbitrageResult:
    """Full backtest result for one config."""
    config: ArbitrageConfig
    interval_minutes: int
    bars: int
    signals: int
    trades: List[ArbitrageTrade]
    net_pnl: Decimal
    win_rate: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Optional[Decimal]
    first_bar_time: Optional[str]
    last_bar_time: Optional[str]
    dataset_provenance: Dict[str, object] = field(default_factory=dict)


def _fetch_series(instrument_id: str, interval_minutes: int, limit: int = 720) -> CandleSeries:
    provider = get_crypto_provider()
    return provider.get_candle_series(
        instrument_id,
        interval_minutes=interval_minutes,
        limit=limit,
    )


def _align_series(
    btc_series: CandleSeries,
    eth_usd_series: CandleSeries,
    eth_btc_series: CandleSeries,
) -> List[ArbitrageBar]:
    """Align three CandleSeries by open_time and compute the triangular spread."""
    btc_by_time = {c.open_time: c for c in btc_series.completed}
    eth_usd_by_time = {c.open_time: c for c in eth_usd_series.completed}
    eth_btc_by_time = {c.open_time: c for c in eth_btc_series.completed}

    common_times = sorted(
        set(btc_by_time.keys())
        & set(eth_usd_by_time.keys())
        & set(eth_btc_by_time.keys())
    )

    bars: List[ArbitrageBar] = []
    for t in common_times:
        btc_close = btc_by_time[t].close
        eth_usd_close = eth_usd_by_time[t].close
        eth_btc_close = eth_btc_by_time[t].close

        if btc_close <= ZERO or eth_btc_close <= ZERO:
            continue

        implied = eth_btc_close * btc_close
        if implied <= ZERO:
            continue

        spread = (implied - eth_usd_close).copy_abs() / eth_usd_close * TEN_THOUSAND

        bars.append(ArbitrageBar(
            open_time=t,
            btc_usd=btc_close,
            eth_usd=eth_usd_close,
            eth_btc=eth_btc_close,
            implied_eth_usd=implied,
            spread_bps=spread,
            direction=0,
        ))

    return bars


def _compute_atr(bars: List[ArbitrageBar], window: int = 14) -> List[Optional[Decimal]]:
    """ATR of the ETH-USD close-to-close returns, repurposed as spread volatility proxy."""
    atr: List[Optional[Decimal]] = [None] * len(bars)
    if len(bars) < window + 1:
        return atr
    ranges: List[Decimal] = []
    for i in range(1, len(bars)):
        r = (bars[i].eth_usd - bars[i - 1].eth_usd).copy_abs()
        ranges.append(r)
    for i in range(window, len(ranges)):
        w = ranges[i - window:i]
        atr[i + 1] = sum(w, ZERO) / Decimal(window)
    return atr


def _generate_signals(
    bars: List[ArbitrageBar],
    min_spread_bps: Decimal,
) -> List[ArbitrageBar]:
    """Mark bars where spread exceeds threshold with a direction."""
    for bar in bars:
        if bar.spread_bps > min_spread_bps:
            if bar.implied_eth_usd > bar.eth_usd:
                bar.direction = 1
            else:
                bar.direction = -1
        else:
            bar.direction = 0
    return [b for b in bars if b.direction != 0]


def _simulate_trades(
    aligned_bars: List[ArbitrageBar],
    signal_bars: List[ArbitrageBar],
    atr_values: List[Optional[Decimal]],
    config: ArbitrageConfig,
) -> List[ArbitrageTrade]:
    """Walk through signal bars and simulate entry/exit round-trips."""
    trades: List[ArbitrageTrade] = []
    time_to_bar = {b.open_time: i for i, b in enumerate(aligned_bars)}
    in_position = False
    entry_bar: Optional[ArbitrageBar] = None
    entry_idx: int = 0

    for sig_bar in signal_bars:
        if in_position:
            continue

        entry_bar = sig_bar
        entry_idx = time_to_bar.get(sig_bar.open_time, 0)
        in_position = True

        atr_val = atr_values[entry_idx] if entry_idx < len(atr_values) else None
        if atr_val is None or atr_val == ZERO:
            in_position = False
            continue

        target_dist = atr_val * config.target_atr_mult
        stop_dist = atr_val * config.stop_atr_mult

        exit_bar: Optional[ArbitrageBar] = None
        exit_idx: int = 0
        exit_reason = "time_stop"

        max_lookahead = min(config.time_stop_bars, len(aligned_bars) - entry_idx - 1)
        for offset in range(1, max_lookahead + 1):
            candidate = aligned_bars[entry_idx + offset]
            price_move = (candidate.eth_usd - entry_bar.eth_usd).copy_abs()

            if entry_bar.direction == 1:
                if candidate.eth_usd >= entry_bar.eth_usd + target_dist:
                    exit_bar = candidate
                    exit_idx = entry_idx + offset
                    exit_reason = "target"
                    break
                if candidate.eth_usd <= entry_bar.eth_usd - stop_dist:
                    exit_bar = candidate
                    exit_idx = entry_idx + offset
                    exit_reason = "stop"
                    break
            else:
                if candidate.eth_usd <= entry_bar.eth_usd - target_dist:
                    exit_bar = candidate
                    exit_idx = entry_idx + offset
                    exit_reason = "target"
                    break
                if candidate.eth_usd >= entry_bar.eth_usd + stop_dist:
                    exit_bar = candidate
                    exit_idx = entry_idx + offset
                    exit_reason = "stop"
                    break

        if exit_bar is None and max_lookahead > 0:
            exit_idx = entry_idx + max_lookahead
            exit_bar = aligned_bars[exit_idx]
            exit_reason = "time_stop"

        if exit_bar is None:
            in_position = False
            continue

        gross = (exit_bar.eth_usd - entry_bar.eth_usd) * entry_bar.direction
        notional = config.notional_per_trade
        qty = notional / entry_bar.eth_usd if entry_bar.eth_usd > ZERO else ZERO
        gross_pnl = gross * qty
        round_trip_rate = (config.commission_rate + config.slippage_rate) * Decimal(2)
        commission = notional * round_trip_rate
        net_pnl = gross_pnl - commission

        trades.append(ArbitrageTrade(
            entry_time=entry_bar.open_time,
            exit_time=exit_bar.open_time,
            entry_spread_bps=entry_bar.spread_bps,
            exit_spread_bps=exit_bar.spread_bps,
            direction=entry_bar.direction,
            entry_btc_usd=entry_bar.btc_usd,
            entry_eth_usd=entry_bar.eth_usd,
            entry_eth_btc=entry_bar.eth_btc,
            exit_btc_usd=exit_bar.btc_usd,
            exit_eth_usd=exit_bar.eth_usd,
            exit_eth_btc=exit_bar.eth_btc,
            gross_pnl=gross_pnl.quantize(Decimal("0.01")),
            commission=commission.quantize(Decimal("0.01")),
            net_pnl=net_pnl.quantize(Decimal("0.01")),
            exit_reason=exit_reason,
            bars_held=exit_idx - entry_idx,
        ))

        in_position = False

    return trades


def _compute_stats(trades: List[ArbitrageTrade], starting_cash: Decimal) -> Tuple[Decimal, Decimal, Decimal, Optional[Decimal]]:
    """Compute net PnL, win rate, max drawdown, and Sharpe from trade list."""
    if not trades:
        return ZERO, ZERO, ZERO, None

    net_pnl = sum(t.net_pnl for t in trades)
    wins = sum(1 for t in trades if t.net_pnl > ZERO)
    win_rate = Decimal(wins) / Decimal(len(trades)) * HUNDRED if trades else ZERO

    equity = starting_cash
    peak = starting_cash
    max_dd = ZERO
    for t in trades:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * HUNDRED if peak > ZERO else ZERO
        if dd > max_dd:
            max_dd = dd

    returns = [t.net_pnl / starting_cash for t in trades if starting_cash > ZERO]
    sharpe: Optional[Decimal] = None
    if len(returns) >= 2:
        mean_r = sum(returns, ZERO) / Decimal(len(returns))
        var_r = sum((r - mean_r) ** 2 for r in returns) / Decimal(len(returns) - 1)
        std_r = var_r.sqrt()
        if std_r > ZERO:
            sharpe = (mean_r / std_r).quantize(Decimal("0.0001"))

    return net_pnl.quantize(Decimal("0.01")), win_rate.quantize(Decimal("0.01")), max_dd.quantize(Decimal("0.01")), sharpe


def run_triangular_arbitrage(
    interval_minutes: int = 60,
    limit: int = 720,
    config: Optional[ArbitrageConfig] = None,
) -> ArbitrageResult:
    """Full triangular arbitrage backtest across BTC-USD, ETH-USD, ETH-BTC.

    Fetches real Kraken OHLCV for all three legs, aligns by timestamp,
    computes the implied cross rate, and simulates trades when the spread
    exceeds the configured threshold.
    """
    if config is None:
        config = ArbitrageConfig()

    btc_series = _fetch_series("CRYPTO:KRAKEN:BTC-USD", interval_minutes, limit)
    eth_usd_series = _fetch_series("CRYPTO:KRAKEN:ETH-USD", interval_minutes, limit)
    eth_btc_series = _fetch_series("CRYPTO:KRAKEN:ETH-BTC", interval_minutes, limit)

    aligned = _align_series(btc_series, eth_usd_series, eth_btc_series)
    if not aligned:
        return ArbitrageResult(
            config=config,
            interval_minutes=interval_minutes,
            bars=0,
            signals=0,
            trades=[],
            net_pnl=ZERO,
            win_rate=ZERO,
            max_drawdown=ZERO,
            sharpe_ratio=None,
            first_bar_time=None,
            last_bar_time=None,
        )

    atr_values = _compute_atr(aligned)
    signals = _generate_signals(aligned, config.min_spread_bps)
    trades = _simulate_trades(aligned, signals, atr_values, config)
    net_pnl, win_rate, max_dd, sharpe = _compute_stats(trades, config.starting_cash)

    provenance = {
        "btc_usd": {
            "bars": len(btc_series.completed),
            "fingerprint": btc_series.fingerprint,
            "integrity_ok": btc_series.integrity_ok,
        },
        "eth_usd": {
            "bars": len(eth_usd_series.completed),
            "fingerprint": eth_usd_series.fingerprint,
            "integrity_ok": eth_usd_series.integrity_ok,
        },
        "eth_btc": {
            "bars": len(eth_btc_series.completed),
            "fingerprint": eth_btc_series.fingerprint,
            "integrity_ok": eth_btc_series.integrity_ok,
        },
        "aligned_bars": len(aligned),
        "data_nature": "live",
        "provider_id": "kraken",
    }

    return ArbitrageResult(
        config=config,
        interval_minutes=interval_minutes,
        bars=len(aligned),
        signals=len(signals),
        trades=trades,
        net_pnl=net_pnl,
        win_rate=win_rate,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        first_bar_time=aligned[0].open_time.isoformat() if aligned else None,
        last_bar_time=aligned[-1].open_time.isoformat() if aligned else None,
        dataset_provenance=provenance,
    )


def result_to_dict(result: ArbitrageResult) -> dict:
    """Serialize an ArbitrageResult to a JSON-safe dict."""
    return {
        "lane": "arbitrage_triangular_simulated",
        "research_version": "1.0.0",
        "config": {
            "config_id": result.config.config_id,
            "min_spread_bps": str(result.config.min_spread_bps),
            "target_atr_mult": str(result.config.target_atr_mult),
            "stop_atr_mult": str(result.config.stop_atr_mult),
            "time_stop_bars": result.config.time_stop_bars,
            "commission_rate": str(result.config.commission_rate),
            "slippage_rate": str(result.config.slippage_rate),
        },
        "interval_minutes": result.interval_minutes,
        "bars": result.bars,
        "signals": result.signals,
        "trade_count": len(result.trades),
        "net_pnl": str(result.net_pnl),
        "win_rate_pct": str(result.win_rate),
        "max_drawdown_pct": str(result.max_drawdown),
        "sharpe_ratio": str(result.sharpe_ratio) if result.sharpe_ratio is not None else None,
        "first_bar_time": result.first_bar_time,
        "last_bar_time": result.last_bar_time,
        "dataset": result.dataset_provenance,
        "trades": [
            {
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "direction": t.direction,
                "entry_spread_bps": str(t.entry_spread_bps),
                "exit_spread_bps": str(t.exit_spread_bps),
                "gross_pnl": str(t.gross_pnl),
                "commission": str(t.commission),
                "net_pnl": str(t.net_pnl),
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
            }
            for t in result.trades
        ],
    }
