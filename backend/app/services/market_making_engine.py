"""Market making backtesting engine using OHLCV-simulated order book.

This engine approximates order book dynamics from OHLCV bars:
  - mid_price = (high + low) / 2
  - spread_estimate derived from high-low range, adjusted by volume
  - bid/ask quotes placed around mid
  - fills detected when bar's low touches bid or high touches ask
  - inventory tracking with skew to limit directional exposure

IMPORTANT: This is a simulation approximation, not a realistic backtest.
Real market making requires L2/L3 order book data, queue position modeling,
adverse selection analysis, and latency considerations. OHLCV bars cannot
capture these dynamics. Results should be interpreted as upper-bound estimates
of spread-capture potential, not as tradeable strategy performance.

Design: standalone engine separate from the single-instrument research engine.
Market making is about providing liquidity on both sides, not directional
signals. The existing rule/signal framework (Callable[[Indicators, int], int])
cannot represent "always quote both sides with inventory management."
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
TEN_THOUSAND = Decimal("10000")
HUNDRED = Decimal("100")
HALF = Decimal("0.5")
TENTH = Decimal("0.1")


@dataclass(frozen=True)
class MarketMakingConfig:
    """Parameters for one market making backtest run."""
    spread_target_bps: Decimal = Decimal("10")
    min_spread_bps: Decimal = Decimal("2")
    max_spread_bps: Decimal = Decimal("50")
    order_size_usd: Decimal = Decimal("100")
    max_inventory_usd: Decimal = Decimal("1000")
    inventory_skew_factor: Decimal = Decimal("0.1")
    commission_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.0005")
    starting_cash: Decimal = Decimal("10000")
    volume_sma_window: int = 20
    config_id: str = "balanced"


@dataclass
class SimulatedOrderBook:
    """One bar's estimated order book state."""
    open_time: datetime
    mid_price: Decimal
    spread: Decimal
    bid: Decimal
    ask: Decimal
    volume: Decimal
    volume_ratio: Optional[Decimal]


@dataclass
class MarketMakingFill:
    """One fill event (bid or ask hit)."""
    open_time: datetime
    side: str  # "buy" or "sell"
    price: Decimal
    quantity: Decimal
    notional: Decimal
    commission: Decimal
    inventory_after: Decimal
    spread_at_fill: Decimal


@dataclass
class MarketMakingResult:
    """Full backtest result for one config."""
    config: MarketMakingConfig
    instrument_id: str
    interval_minutes: int
    bars: int
    fills: List[MarketMakingFill]
    fill_count: int
    buy_fills: int
    sell_fills: int
    gross_spread_captured: Decimal
    total_commission: Decimal
    total_slippage: Decimal
    net_pnl: Decimal
    inventory_pnl: Decimal
    total_pnl: Decimal
    max_inventory: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Optional[Decimal]
    win_rate: Decimal
    first_bar_time: Optional[str]
    last_bar_time: Optional[str]
    dataset_provenance: Dict[str, object] = field(default_factory=dict)


def _compute_volume_sma(bars: CandleSeries, window: int) -> List[Optional[Decimal]]:
    """Simple moving average of volume over the given window."""
    volumes = [c.volume for c in bars.completed]
    sma: List[Optional[Decimal]] = [None] * len(volumes)
    for i in range(window - 1, len(volumes)):
        w = volumes[i - window + 1 : i + 1]
        sma[i] = sum(w, ZERO) / Decimal(window)
    return sma


def simulate_order_book_from_ohlcv(
    candle,
    volume_sma: Optional[Decimal],
    config: MarketMakingConfig,
) -> SimulatedOrderBook:
    """Estimate bid/ask/spread from one OHLCV bar.

    Spread model:
      - Base spread = (high - low) * 0.1 (10% of range)
      - Volume adjustment: higher volume → tighter spread
        volume_ratio = volume / sma(volume)
        if volume_ratio > 1: spread *= 1/volume_ratio (tighter)
        if volume_ratio < 1: spread *= min(2, 1/volume_ratio) (wider, capped)
      - Clamp to [min_spread, max_spread] from config
    """
    mid = (candle.high + candle.low) * HALF

    base_range = candle.high - candle.low
    if base_range <= ZERO or mid <= ZERO:
        return SimulatedOrderBook(
            open_time=candle.open_time,
            mid_price=mid,
            spread=ZERO,
            bid=mid,
            ask=mid,
            volume=candle.volume,
            volume_ratio=None,
        )

    spread = base_range * TENTH

    if volume_sma is not None and volume_sma > ZERO:
        volume_ratio = candle.volume / volume_sma
        if volume_ratio > ZERO:
            if volume_ratio > Decimal("1"):
                spread = spread / volume_ratio
            else:
                adjustment = min(Decimal("2"), Decimal("1") / volume_ratio)
                spread = spread * adjustment
    else:
        volume_ratio = None

    min_spread = mid * config.min_spread_bps / TEN_THOUSAND
    max_spread = mid * config.max_spread_bps / TEN_THOUSAND
    spread = max(min_spread, min(max_spread, spread))

    bid = mid - spread * HALF
    ask = mid + spread * HALF

    return SimulatedOrderBook(
        open_time=candle.open_time,
        mid_price=mid,
        spread=spread,
        bid=bid,
        ask=ask,
        volume=candle.volume,
        volume_ratio=volume_ratio,
    )


def generate_market_making_quotes(
    order_book: SimulatedOrderBook,
    inventory: Decimal,
    max_inventory: Decimal,
    skew_factor: Decimal,
) -> Tuple[Decimal, Decimal]:
    """Adjust bid/ask quotes based on inventory position (skew).

    When long inventory: lower bid (less eager to buy), lower ask (more eager to sell)
    When short inventory: raise bid (more eager to buy), raise ask (less eager to sell)

    Returns (adjusted_bid, adjusted_ask).
    """
    if max_inventory <= ZERO:
        return order_book.bid, order_book.ask

    inventory_ratio = inventory / max_inventory
    skew = inventory_ratio * skew_factor * order_book.spread

    adjusted_bid = order_book.bid - skew
    adjusted_ask = order_book.ask - skew

    if adjusted_bid <= ZERO:
        adjusted_bid = order_book.mid_price * Decimal("0.999")
    if adjusted_ask <= adjusted_bid:
        adjusted_ask = adjusted_bid + order_book.spread * Decimal("0.1")

    return adjusted_bid, adjusted_ask


def detect_fills(
    candle,
    adjusted_bid: Decimal,
    adjusted_ask: Decimal,
) -> Tuple[bool, bool]:
    """Check if the bar's price action would fill our bid or ask.

    Bid fill: bar's low <= our bid (price came down to our buy quote)
    Ask fill: bar's high >= our ask (price went up to our sell quote)

    Returns (bid_filled, ask_filled). Both can be true in a volatile bar.
    """
    bid_filled = candle.low <= adjusted_bid
    ask_filled = candle.high >= adjusted_ask
    return bid_filled, ask_filled


def backtest_market_making(
    instrument_id: str,
    interval_minutes: int = 5,
    limit: int = 720,
    config: Optional[MarketMakingConfig] = None,
) -> MarketMakingResult:
    """Full market making backtest on one instrument.

    Fetches real Kraken OHLCV, simulates order book from each bar,
    generates quotes with inventory skew, detects fills, and tracks PnL.
    """
    if config is None:
        config = MarketMakingConfig()

    provider = get_crypto_provider()
    series = provider.get_candle_series(
        instrument_id,
        interval_minutes=interval_minutes,
        limit=limit,
    )

    if len(series.completed) < config.volume_sma_window + 1:
        return MarketMakingResult(
            config=config,
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            bars=len(series.completed),
            fills=[],
            fill_count=0,
            buy_fills=0,
            sell_fills=0,
            gross_spread_captured=ZERO,
            total_commission=ZERO,
            total_slippage=ZERO,
            net_pnl=ZERO,
            inventory_pnl=ZERO,
            total_pnl=ZERO,
            max_inventory=ZERO,
            max_drawdown=ZERO,
            sharpe_ratio=None,
            win_rate=ZERO,
            first_bar_time=series.completed[0].open_time.isoformat() if series.completed else None,
            last_bar_time=series.completed[-1].open_time.isoformat() if series.completed else None,
        )

    volume_sma = _compute_volume_sma(series, config.volume_sma_window)

    fills: List[MarketMakingFill] = []
    inventory = ZERO
    cash = config.starting_cash
    peak_cash = cash
    max_dd = ZERO
    max_inv_seen = ZERO
    buy_count = 0
    sell_count = 0
    gross_spread = ZERO

    entry_prices: List[Decimal] = []

    for i, candle in enumerate(series.completed):
        vol_sma = volume_sma[i] if i < len(volume_sma) else None
        if vol_sma is None:
            continue

        ob = simulate_order_book_from_ohlcv(candle, vol_sma, config)
        if ob.spread <= ZERO:
            continue

        adj_bid, adj_ask = generate_market_making_quotes(
            ob, inventory, config.max_inventory_usd, config.inventory_skew_factor
        )

        bid_filled, ask_filled = detect_fills(candle, adj_bid, adj_ask)

        if bid_filled and inventory < config.max_inventory_usd:
            qty = config.order_size_usd / adj_bid if adj_bid > ZERO else ZERO
            notional = qty * adj_bid
            commission = notional * config.commission_rate
            slippage = notional * config.slippage_rate
            cash -= (notional + commission + slippage)
            inventory += notional
            if inventory > max_inv_seen:
                max_inv_seen = inventory
            buy_count += 1
            entry_prices.append(adj_bid)
            gross_spread += ob.spread * qty

            fills.append(MarketMakingFill(
                open_time=candle.open_time,
                side="buy",
                price=adj_bid,
                quantity=qty,
                notional=notional.quantize(Decimal("0.01")),
                commission=commission.quantize(Decimal("0.01")),
                inventory_after=inventory.quantize(Decimal("0.01")),
                spread_at_fill=ob.spread,
            ))

        if ask_filled and inventory > ZERO:
            avg_entry = sum(entry_prices) / Decimal(len(entry_prices)) if entry_prices else adj_ask
            qty = min(config.order_size_usd / adj_ask, inventory / avg_entry) if adj_ask > ZERO and avg_entry > ZERO else ZERO
            notional = qty * adj_ask
            commission = notional * config.commission_rate
            slippage = notional * config.slippage_rate
            cash += (notional - commission - slippage)
            inventory -= qty * avg_entry
            if inventory < ZERO:
                inventory = ZERO
            sell_count += 1
            if entry_prices:
                entry_prices.pop(0)
            spread_capture = (adj_ask - avg_entry) * qty if qty > ZERO else ZERO
            gross_spread += spread_capture

            fills.append(MarketMakingFill(
                open_time=candle.open_time,
                side="sell",
                price=adj_ask,
                quantity=qty,
                notional=notional.quantize(Decimal("0.01")),
                commission=commission.quantize(Decimal("0.01")),
                inventory_after=inventory.quantize(Decimal("0.01")),
                spread_at_fill=ob.spread,
            ))

        total_value = cash + inventory
        if total_value > peak_cash:
            peak_cash = total_value
        dd = (peak_cash - total_value) / peak_cash * HUNDRED if peak_cash > ZERO else ZERO
        if dd > max_dd:
            max_dd = dd

    last_candle = series.completed[-1]
    if inventory > ZERO and last_candle.close > ZERO:
        inventory_value = inventory
    else:
        inventory_value = ZERO

    net_pnl = (cash + inventory_value) - config.starting_cash
    total_commission = sum(f.commission for f in fills)
    total_slippage_cost = ZERO

    returns: List[Decimal] = []
    if len(fills) >= 2:
        round_trips = min(buy_count, sell_count)
        if round_trips > 0:
            per_trade_pnl = net_pnl / Decimal(round_trips)
            returns = [per_trade_pnl / config.starting_cash] * round_trips

    sharpe: Optional[Decimal] = None
    if len(returns) >= 2:
        mean_r = sum(returns, ZERO) / Decimal(len(returns))
        var_r = sum((r - mean_r) ** 2 for r in returns) / Decimal(len(returns) - 1)
        std_r = var_r.sqrt()
        if std_r > Decimal("1e-10"):
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

    win_rate = Decimal(sell_count) / Decimal(buy_count) * HUNDRED if buy_count > ZERO else ZERO

    provenance = {
        "instrument_id": instrument_id,
        "bars": len(series.completed),
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "data_nature": "live",
        "provider_id": "kraken",
        "simulation_type": "ohlcv_order_book_approximation",
    }

    return MarketMakingResult(
        config=config,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        bars=len(series.completed),
        fills=fills,
        fill_count=len(fills),
        buy_fills=buy_count,
        sell_fills=sell_count,
        gross_spread_captured=gross_spread.quantize(Decimal("0.01")),
        total_commission=total_commission.quantize(Decimal("0.01")),
        total_slippage=total_slippage_cost,
        net_pnl=net_pnl.quantize(Decimal("0.01")),
        inventory_pnl=inventory_value.quantize(Decimal("0.01")),
        total_pnl=net_pnl.quantize(Decimal("0.01")),
        max_inventory=max_inv_seen.quantize(Decimal("0.01")),
        max_drawdown=max_dd.quantize(Decimal("0.01")),
        sharpe_ratio=sharpe,
        win_rate=win_rate.quantize(Decimal("0.01")),
        first_bar_time=series.completed[0].open_time.isoformat() if series.completed else None,
        last_bar_time=series.completed[-1].open_time.isoformat() if series.completed else None,
        dataset_provenance=provenance,
    )


def result_to_dict(result: MarketMakingResult) -> dict:
    """Serialize a MarketMakingResult to a JSON-safe dict."""
    return {
        "lane": "market_making_simulated",
        "research_version": "1.0.0",
        "simulation_type": "ohlcv_order_book_approximation",
        "disclaimer": (
            "This is a simulation approximation using OHLCV bars, not a realistic "
            "backtest. Real market making requires L2/L3 order book data, queue "
            "position modeling, and adverse selection analysis."
        ),
        "config": {
            "config_id": result.config.config_id,
            "spread_target_bps": str(result.config.spread_target_bps),
            "order_size_usd": str(result.config.order_size_usd),
            "max_inventory_usd": str(result.config.max_inventory_usd),
            "inventory_skew_factor": str(result.config.inventory_skew_factor),
            "commission_rate": str(result.config.commission_rate),
            "slippage_rate": str(result.config.slippage_rate),
        },
        "instrument_id": result.instrument_id,
        "interval_minutes": result.interval_minutes,
        "bars": result.bars,
        "fill_count": result.fill_count,
        "buy_fills": result.buy_fills,
        "sell_fills": result.sell_fills,
        "gross_spread_captured": str(result.gross_spread_captured),
        "total_commission": str(result.total_commission),
        "net_pnl": str(result.net_pnl),
        "inventory_pnl": str(result.inventory_pnl),
        "total_pnl": str(result.total_pnl),
        "max_inventory": str(result.max_inventory),
        "max_drawdown_pct": str(result.max_drawdown),
        "sharpe_ratio": str(result.sharpe_ratio) if result.sharpe_ratio is not None else None,
        "win_rate_pct": str(result.win_rate),
        "first_bar_time": result.first_bar_time,
        "last_bar_time": result.last_bar_time,
        "dataset": result.dataset_provenance,
        "fills": [
            {
                "open_time": f.open_time.isoformat(),
                "side": f.side,
                "price": str(f.price.quantize(Decimal("0.01"))),
                "quantity": str(f.quantity.quantize(Decimal("0.00000001"))),
                "notional": str(f.notional),
                "commission": str(f.commission),
                "inventory_after": str(f.inventory_after),
                "spread_at_fill_bps": str(f.spread_at_fill.quantize(Decimal("0.0001"))),
            }
            for f in result.fills[:100]
        ],
    }
