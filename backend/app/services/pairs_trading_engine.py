"""Pairs trading engine for crypto assets.

Implements statistical arbitrage via z-score mean reversion on asset pairs.
Supports both direct quotes (ETH-BTC) and synthetic ratios (ETH-SOL, BTC-SOL).

Core logic:
1. Calculate rolling z-score of price ratio
2. Entry: z > +threshold → short base/long quote; z < -threshold → long base/short quote
3. Exit: triple barrier (z-score reversion, stop-loss, time-stop)
4. PnL: combined from both legs, dollar-neutral

This is a multi-asset strategy, separate from the single-asset research engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from app.providers.crypto_registry import get_crypto_provider
from app.services.crypto_series import CandleSeries

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
TWO = Decimal("2")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class PairsConfig:
    """Configuration for pairs trading backtest."""
    zscore_window: int = 20
    entry_threshold: Decimal = Decimal("2.0")
    exit_threshold: Decimal = Decimal("0.5")
    stop_threshold: Decimal = Decimal("3.0")
    time_stop_bars: int = 15
    starting_cash: Decimal = Decimal("10000")
    notional_per_leg: Decimal = Decimal("5000")
    commission_rate: Decimal = Decimal("0.001")
    config_id: str = "balanced"


@dataclass
class PairsSignal:
    """One trading signal."""
    bar_index: int
    open_time: datetime
    direction: int  # 1 = long base/short quote, -1 = short base/long quote
    zscore: Decimal
    base_price: Decimal
    quote_price: Decimal
    ratio: Decimal


@dataclass
class PairsTrade:
    """One completed pairs trade."""
    entry_time: datetime
    exit_time: datetime
    entry_direction: int
    entry_zscore: Decimal
    exit_zscore: Decimal
    entry_base_price: Decimal
    entry_quote_price: Decimal
    exit_base_price: Decimal
    exit_quote_price: Decimal
    base_leg_pnl: Decimal
    quote_leg_pnl: Decimal
    total_commission: Decimal
    net_pnl: Decimal
    exit_reason: str  # "reversion", "stop", "time_stop"
    bars_held: int


@dataclass
class PairsResult:
    """Backtest result for one configuration."""
    pair: str
    interval_minutes: int
    bars: int
    signals: int
    trades: List[PairsTrade]
    net_pnl: Decimal
    win_rate: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Optional[Decimal]
    base_leg_pnl: Decimal
    quote_leg_pnl: Decimal
    total_commission: Decimal
    first_bar_time: Optional[str]
    last_bar_time: Optional[str]
    dataset_provenance: Dict[str, object] = field(default_factory=dict)


def _fetch_pair_data(
    pair: str,
    interval_minutes: int,
    limit: int,
) -> Tuple[CandleSeries, CandleSeries]:
    """Fetch OHLCV data for both legs of the pair.
    
    Returns (base_series, quote_series).
    For ETH-BTC: base=ETH-USD, quote=BTC-USD
    For ETH-SOL: base=ETH-USD, quote=SOL-USD
    For BTC-SOL: base=BTC-USD, quote=SOL-USD
    """
    provider = get_crypto_provider()
    
    pair_map = {
        "ETH-BTC": ("CRYPTO:KRAKEN:ETH-USD", "CRYPTO:KRAKEN:BTC-USD"),
        "ETH-SOL": ("CRYPTO:KRAKEN:ETH-USD", "CRYPTO:KRAKEN:SOL-USD"),
        "BTC-SOL": ("CRYPTO:KRAKEN:BTC-USD", "CRYPTO:KRAKEN:SOL-USD"),
    }
    
    if pair not in pair_map:
        raise ValueError(f"Unsupported pair: {pair}. Must be one of {list(pair_map.keys())}")
    
    base_id, quote_id = pair_map[pair]
    
    base_series = provider.get_candle_series(
        base_id,
        interval_minutes=interval_minutes,
        limit=limit,
    )
    quote_series = provider.get_candle_series(
        quote_id,
        interval_minutes=interval_minutes,
        limit=limit,
    )
    
    return base_series, quote_series


def _align_and_compute_ratios(
    base_series: CandleSeries,
    quote_series: CandleSeries,
) -> Tuple[List[datetime], List[Decimal], List[Decimal], List[Decimal]]:
    """Align two series by timestamp and compute price ratios.
    
    Returns (times, base_prices, quote_prices, ratios).
    ratio = base_price / quote_price
    """
    base_by_time = {c.open_time: c for c in base_series.completed}
    quote_by_time = {c.open_time: c for c in quote_series.completed}
    
    common_times = sorted(set(base_by_time.keys()) & set(quote_by_time.keys()))
    
    times: List[datetime] = []
    base_prices: List[Decimal] = []
    quote_prices: List[Decimal] = []
    ratios: List[Decimal] = []
    
    for t in common_times:
        base_close = base_by_time[t].close
        quote_close = quote_by_time[t].close
        
        if quote_close <= ZERO:
            continue
        
        ratio = base_close / quote_close
        
        times.append(t)
        base_prices.append(base_close)
        quote_prices.append(quote_close)
        ratios.append(ratio)
    
    return times, base_prices, quote_prices, ratios


def _compute_rolling_zscore(
    values: List[Decimal],
    window: int,
) -> List[Optional[Decimal]]:
    """Compute rolling z-score with given window.
    
    zscore = (value - SMA) / STD
    Returns None for indices where window is not yet full.
    """
    zscores: List[Optional[Decimal]] = [None] * len(values)
    
    for i in range(window - 1, len(values)):
        window_values = values[i - window + 1 : i + 1]
        
        # Compute mean
        mean = sum(window_values, ZERO) / Decimal(window)
        
        # Compute standard deviation
        variance = sum((v - mean) ** 2 for v in window_values) / Decimal(window)
        std = variance.sqrt()
        
        if std > ZERO:
            zscore = (values[i] - mean) / std
            zscores[i] = zscore
        else:
            zscores[i] = ZERO
    
    return zscores


def _generate_signals(
    times: List[datetime],
    base_prices: List[Decimal],
    quote_prices: List[Decimal],
    ratios: List[Decimal],
    zscores: List[Optional[Decimal]],
    entry_threshold: Decimal,
) -> List[PairsSignal]:
    """Generate trading signals based on z-score thresholds.
    
    z > +entry_threshold → short base/long quote (direction=-1)
    z < -entry_threshold → long base/short quote (direction=1)
    """
    signals: List[PairsSignal] = []
    
    for i, zscore in enumerate(zscores):
        if zscore is None:
            continue
        
        if zscore > entry_threshold:
            # Ratio is high → short base, long quote
            signals.append(PairsSignal(
                bar_index=i,
                open_time=times[i],
                direction=-1,
                zscore=zscore,
                base_price=base_prices[i],
                quote_price=quote_prices[i],
                ratio=ratios[i],
            ))
        elif zscore < -entry_threshold:
            # Ratio is low → long base, short quote
            signals.append(PairsSignal(
                bar_index=i,
                open_time=times[i],
                direction=1,
                zscore=zscore,
                base_price=base_prices[i],
                quote_price=quote_prices[i],
                ratio=ratios[i],
            ))
    
    return signals


def _simulate_trades(
    times: List[datetime],
    base_prices: List[Decimal],
    quote_prices: List[Decimal],
    ratios: List[Decimal],
    zscores: List[Optional[Decimal]],
    signals: List[PairsSignal],
    config: PairsConfig,
) -> List[PairsTrade]:
    """Simulate trades with triple barrier exit.
    
    Exit conditions:
    1. Z-score reversion: |z| < exit_threshold
    2. Stop-loss: |z| > stop_threshold
    3. Time-stop: held > time_stop_bars
    """
    trades: List[PairsTrade] = []
    in_position = False
    entry_signal: Optional[PairsSignal] = None
    entry_idx = 0
    
    for signal in signals:
        if in_position:
            continue
        
        # Enter position
        entry_signal = signal
        entry_idx = signal.bar_index
        in_position = True
        
        # Look ahead for exit
        exit_idx = None
        exit_reason = None
        
        max_lookahead = min(config.time_stop_bars, len(zscores) - entry_idx - 1)
        
        for offset in range(1, max_lookahead + 1):
            future_idx = entry_idx + offset
            future_z = zscores[future_idx]
            
            if future_z is None:
                continue
            
            # Check exit conditions
            if entry_signal.direction == 1:
                # Long base/short quote → exit when z reverts up
                if future_z > -config.exit_threshold:
                    exit_idx = future_idx
                    exit_reason = "reversion"
                    break
                if future_z < -config.stop_threshold:
                    exit_idx = future_idx
                    exit_reason = "stop"
                    break
            else:
                # Short base/long quote → exit when z reverts down
                if future_z < config.exit_threshold:
                    exit_idx = future_idx
                    exit_reason = "reversion"
                    break
                if future_z > config.stop_threshold:
                    exit_idx = future_idx
                    exit_reason = "stop"
                    break
        
        # Time-stop if no other exit
        if exit_idx is None and max_lookahead > 0:
            exit_idx = entry_idx + max_lookahead
            exit_reason = "time_stop"
        
        if exit_idx is None:
            in_position = False
            continue
        
        # Calculate PnL
        entry_base = base_prices[entry_idx]
        entry_quote = quote_prices[entry_idx]
        exit_base = base_prices[exit_idx]
        exit_quote = quote_prices[exit_idx]
        
        # Notional per leg
        notional = config.notional_per_leg
        
        # Base leg: long if direction=1, short if direction=-1
        base_qty = notional / entry_base if entry_base > ZERO else ZERO
        base_pnl = (exit_base - entry_base) * base_qty * Decimal(entry_signal.direction)
        
        # Quote leg: short if direction=1, long if direction=-1
        quote_qty = notional / entry_quote if entry_quote > ZERO else ZERO
        quote_pnl = (exit_quote - entry_quote) * quote_qty * Decimal(-entry_signal.direction)
        
        # Commission on both legs (entry + exit)
        base_commission = notional * config.commission_rate * TWO
        quote_commission = notional * config.commission_rate * TWO
        total_commission = base_commission + quote_commission
        
        net_pnl = base_pnl + quote_pnl - total_commission
        
        trades.append(PairsTrade(
            entry_time=times[entry_idx],
            exit_time=times[exit_idx],
            entry_direction=entry_signal.direction,
            entry_zscore=entry_signal.zscore,
            exit_zscore=zscores[exit_idx] if zscores[exit_idx] is not None else ZERO,
            entry_base_price=entry_base,
            entry_quote_price=entry_quote,
            exit_base_price=exit_base,
            exit_quote_price=exit_quote,
            base_leg_pnl=base_pnl.quantize(Decimal("0.01")),
            quote_leg_pnl=quote_pnl.quantize(Decimal("0.01")),
            total_commission=total_commission.quantize(Decimal("0.01")),
            net_pnl=net_pnl.quantize(Decimal("0.01")),
            exit_reason=exit_reason,
            bars_held=exit_idx - entry_idx,
        ))
        
        in_position = False
    
    return trades


def _compute_stats(
    trades: List[PairsTrade],
    starting_cash: Decimal,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, Optional[Decimal]]:
    """Compute aggregate statistics from trades.
    
    Returns (net_pnl, win_rate, max_drawdown, base_leg_pnl, quote_leg_pnl, sharpe_ratio).
    """
    if not trades:
        return ZERO, ZERO, ZERO, ZERO, ZERO, None
    
    net_pnl = sum(t.net_pnl for t in trades)
    base_leg_pnl = sum(t.base_leg_pnl for t in trades)
    quote_leg_pnl = sum(t.quote_leg_pnl for t in trades)
    total_commission = sum(t.total_commission for t in trades)
    
    wins = sum(1 for t in trades if t.net_pnl > ZERO)
    win_rate = Decimal(wins) / Decimal(len(trades)) * HUNDRED if trades else ZERO
    
    # Max drawdown
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
    
    # Sharpe ratio
    returns = [t.net_pnl / starting_cash for t in trades if starting_cash > ZERO]
    sharpe: Optional[Decimal] = None
    
    if len(returns) >= 2:
        mean_r = sum(returns, ZERO) / Decimal(len(returns))
        variance = sum((r - mean_r) ** 2 for r in returns) / Decimal(len(returns) - 1)
        std_r = variance.sqrt()
        
        if std_r > ZERO:
            raw_sharpe = mean_r / std_r
            try:
                sharpe = raw_sharpe.quantize(Decimal("0.0001"))
            except Exception:
                sharpe = Decimal(str(round(float(raw_sharpe), 4)))
        elif mean_r == ZERO:
            sharpe = ZERO
        elif mean_r > ZERO:
            sharpe = Decimal("0.0001")
        else:
            sharpe = Decimal("-0.0001")
    
    return (
        net_pnl.quantize(Decimal("0.01")),
        win_rate.quantize(Decimal("0.01")),
        max_dd.quantize(Decimal("0.01")),
        base_leg_pnl.quantize(Decimal("0.01")),
        quote_leg_pnl.quantize(Decimal("0.01")),
        total_commission.quantize(Decimal("0.01")),
        sharpe,
    )


def run_pairs_backtest(
    pair: str = "ETH-BTC",
    interval_minutes: int = 60,
    limit: int = 720,
    config: Optional[PairsConfig] = None,
) -> PairsResult:
    """Run pairs trading backtest.
    
    Args:
        pair: Trading pair (ETH-BTC, ETH-SOL, BTC-SOL)
        interval_minutes: Candle interval
        limit: Number of candles
        config: Pairs trading configuration
    
    Returns:
        PairsResult with backtest statistics
    """
    if config is None:
        config = PairsConfig()
    
    # Fetch data for both legs
    base_series, quote_series = _fetch_pair_data(pair, interval_minutes, limit)
    
    # Align and compute ratios
    times, base_prices, quote_prices, ratios = _align_and_compute_ratios(
        base_series, quote_series
    )
    
    if len(times) < config.zscore_window:
        return PairsResult(
            pair=pair,
            interval_minutes=interval_minutes,
            bars=len(times),
            signals=0,
            trades=[],
            net_pnl=ZERO,
            win_rate=ZERO,
            max_drawdown=ZERO,
            sharpe_ratio=None,
            base_leg_pnl=ZERO,
            quote_leg_pnl=ZERO,
            total_commission=ZERO,
            first_bar_time=times[0].isoformat() if times else None,
            last_bar_time=times[-1].isoformat() if times else None,
        )
    
    # Compute z-scores
    zscores = _compute_rolling_zscore(ratios, config.zscore_window)
    
    # Generate signals
    signals = _generate_signals(
        times, base_prices, quote_prices, ratios, zscores, config.entry_threshold
    )
    
    # Simulate trades
    trades = _simulate_trades(
        times, base_prices, quote_prices, ratios, zscores, signals, config
    )
    
    # Compute statistics
    (
        net_pnl,
        win_rate,
        max_drawdown,
        base_leg_pnl,
        quote_leg_pnl,
        total_commission,
        sharpe_ratio,
    ) = _compute_stats(trades, config.starting_cash)
    
    # Build provenance
    provenance = {
        "pair": pair,
        "base_series": {
            "instrument_id": base_series.instrument_id,
            "bars": len(base_series.completed),
            "fingerprint": base_series.fingerprint,
            "integrity_ok": base_series.integrity_ok,
        },
        "quote_series": {
            "instrument_id": quote_series.instrument_id,
            "bars": len(quote_series.completed),
            "fingerprint": quote_series.fingerprint,
            "integrity_ok": quote_series.integrity_ok,
        },
        "aligned_bars": len(times),
        "data_nature": "live",
        "provider_id": "kraken",
    }
    
    return PairsResult(
        pair=pair,
        interval_minutes=interval_minutes,
        bars=len(times),
        signals=len(signals),
        trades=trades,
        net_pnl=net_pnl,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        base_leg_pnl=base_leg_pnl,
        quote_leg_pnl=quote_leg_pnl,
        total_commission=total_commission,
        first_bar_time=times[0].isoformat() if times else None,
        last_bar_time=times[-1].isoformat() if times else None,
        dataset_provenance=provenance,
    )


def result_to_dict(result: PairsResult) -> dict:
    """Serialize PairsResult to JSON-safe dict."""
    return {
        "lane": "pairs_trading_simulated",
        "research_version": "1.0.0",
        "pair": result.pair,
        "interval_minutes": result.interval_minutes,
        "bars": result.bars,
        "signals": result.signals,
        "trade_count": len(result.trades),
        "net_pnl": str(result.net_pnl),
        "win_rate_pct": str(result.win_rate),
        "max_drawdown_pct": str(result.max_drawdown),
        "sharpe_ratio": str(result.sharpe_ratio) if result.sharpe_ratio is not None else None,
        "base_leg_pnl": str(result.base_leg_pnl),
        "quote_leg_pnl": str(result.quote_leg_pnl),
        "total_commission": str(result.total_commission),
        "first_bar_time": result.first_bar_time,
        "last_bar_time": result.last_bar_time,
        "dataset": result.dataset_provenance,
        "trades": [
            {
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "direction": t.entry_direction,
                "entry_zscore": str(t.entry_zscore.quantize(Decimal("0.0001"))),
                "exit_zscore": str(t.exit_zscore.quantize(Decimal("0.0001"))),
                "base_leg_pnl": str(t.base_leg_pnl),
                "quote_leg_pnl": str(t.quote_leg_pnl),
                "total_commission": str(t.total_commission),
                "net_pnl": str(t.net_pnl),
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
            }
            for t in result.trades
        ],
    }
