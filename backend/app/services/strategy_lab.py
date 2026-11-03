"""Strategy Lab: a 1,000-trade-per-strategy replay on real completed bars.

This is a self-contained research simulator. It is not the paper ledger, it is
not a broker, it reads no trading key, sends no order and moves no real money.
Its one job is to answer the customer's question in a way that can be audited:

* every strategy runs in its own wallet, so one strategy's cash, positions and
  P&L cannot leak into another's;
* every price is a completed candle the venue printed, never a synthetic or
  interpolated bar;
* every trade is a one-bar round trip at a fixed notional with a declared
  commission and slippage assumption;
* the run stops at the requested trade count and reports the remaining funds,
  or stops early and says exactly why.

Why the dataset is stitched from several intervals
--------------------------------------------------
Kraken serves only its most recent ~720 candles per interval and cannot page
older history. A single interval therefore cannot supply 1,000 round trips.
The lab reads 4h, 1h, 5m and 1m windows and keeps only the part of each coarser
window that ends before the finer window begins. The result is one chronological
sequence of real bars with no overlapping timestamps. Because the segments have
different intervals, one-bar holds cover different wall-clock durations; that is
a property of the available data and is reported, not hidden.

Signal families are deliberately different, but they still read the same market.
"Separate environments" means separate wallets and ledgers, not separate prices.
The payload reports pairwise signal agreement so the owner can judge how much
the rules actually differ.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

LAB_VERSION = "1.0.0"
LANE = "strategy_lab_simulated"

# The coarsest window is the oldest; the finest window is the newest. Assembly
# walks finest -> coarsest so a finer, newer segment always wins an overlap.
DEFAULT_INTERVALS: Tuple[int, ...] = (240, 60, 5, 1)
SUPPORTED_INTERVALS = frozenset(DEFAULT_INTERVALS)

DEFAULT_TRADES_PER_STRATEGY = 1000
MAX_TRADES_PER_STRATEGY = 2000
DEFAULT_STARTING_CASH = Decimal("10000")
DEFAULT_NOTIONAL_PER_TRADE = Decimal("1000")
# Declared lab assumptions, not a venue fee schedule. Each side pays both.
DEFAULT_COMMISSION_RATE = Decimal("0.001")
DEFAULT_SLIPPAGE_RATE = Decimal("0.001")
HOLDING_BARS = 1
MAX_WARMUP_BARS = 20

MONEY_QUANT = Decimal("0.00000001")
QTY_QUANT = Decimal("0.00000001")
RATE_QUANT = Decimal("0.000001")

INTERVAL_LABELS: Dict[int, str] = {240: "4h", 60: "1h", 5: "5m", 1: "1m"}


class StrategyLabError(Exception):
    """A refusal the API can explain without guessing at a cause."""

    def __init__(self, reason: str, message: str, **context):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.context = context

    def as_dict(self) -> dict:
        return {"reason": self.reason, "message": self.message, **self.context}


@dataclass(frozen=True)
class LabBar:
    open_time: datetime
    interval_minutes: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(minutes=self.interval_minutes)


@dataclass(frozen=True)
class DatasetSegment:
    interval_minutes: int
    bars: Tuple[LabBar, ...]
    start_index: int
    end_index: int
    fingerprint: str
    integrity_ok: bool
    gap_count: int
    missing_intervals: int

    @property
    def first_open_time(self) -> datetime:
        return self.bars[0].open_time

    @property
    def last_close_time(self) -> datetime:
        return self.bars[-1].close_time


@dataclass(frozen=True)
class StrategyDataset:
    instrument_id: str
    segments: Tuple[DatasetSegment, ...]
    bars: Tuple[LabBar, ...]
    provider_id: str
    data_nature: str
    fetched_at: datetime
    fingerprint: str

    @property
    def first_open_time(self) -> datetime:
        return self.bars[0].open_time

    @property
    def last_close_time(self) -> datetime:
        return self.bars[-1].close_time


@dataclass(frozen=True)
class LabSettings:
    trades_per_strategy: int
    starting_cash: Decimal
    notional_per_trade: Decimal
    commission_rate: Decimal
    slippage_rate: Decimal


@dataclass(frozen=True)
class TradeRecord:
    trade_index: int
    segment_interval_minutes: int
    signal_open_time: datetime
    entry_open_time: datetime
    exit_open_time: datetime
    side: str
    direction: int
    quantity: Decimal
    signal_bar_close: Decimal
    mid_entry: Decimal
    mid_exit: Decimal
    exec_entry: Decimal
    exec_exit: Decimal
    gross_pnl: Decimal
    commission: Decimal
    slippage_cost: Decimal
    net_pnl: Decimal
    cash_after: Decimal
    liquidated: bool


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANT), "f")


def _rate(value: Decimal) -> str:
    return format(value.quantize(RATE_QUANT), "f")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _fingerprint(parts: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode())
        digest.update(b";")
    return digest.hexdigest()


# -- indicators -------------------------------------------------------------


def _sma(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _stdev(values: Sequence[Decimal]) -> Decimal:
    mean = _sma(values)
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / Decimal(len(values))
    return variance.sqrt()


def _rsi_simple(bars: Sequence[LabBar], end: int, window: int = 14) -> Optional[Decimal]:
    """Simple-average RSI (not Wilder-smoothed). Declared in the plan."""
    if end < window:
        return None
    gains = Decimal(0)
    losses = Decimal(0)
    for index in range(end - window + 1, end + 1):
        change = bars[index].close - bars[index - 1].close
        if change > 0:
            gains += change
        elif change < 0:
            losses -= change
    if losses == 0:
        return Decimal(100)
    rs = gains / losses
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


# -- signal families --------------------------------------------------------
#
# Every rule returns +1 (long), -1 (short) or 0 (no trade). To reach 1,000
# one-bar trades from the ~2,400 real bars the venue can serve, each rule has
# to take a side on most eligible bars. These are therefore *directional bias*
# rules, not rare event filters. The plan says so; the results are not a claim
# that any rule is profitable.


def _signal_sma_trend(bars: Sequence[LabBar], index: int) -> int:
    fast = _sma([bar.close for bar in bars[index - 4 : index + 1]])
    slow = _sma([bar.close for bar in bars[index - 19 : index + 1]])
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def _signal_donchian_position(bars: Sequence[LabBar], index: int) -> int:
    window = bars[index - 19 : index + 1]
    high = max(bar.high for bar in window)
    low = min(bar.low for bar in window)
    midpoint = (high + low) / Decimal(2)
    close = bars[index].close
    if close > midpoint:
        return 1
    if close < midpoint:
        return -1
    return 0


def _signal_bollinger_reversion(bars: Sequence[LabBar], index: int) -> int:
    window = [bar.close for bar in bars[index - 19 : index + 1]]
    mean = _sma(window)
    deviation = _stdev(window)
    if deviation == 0:
        return 0
    z = (bars[index].close - mean) / deviation
    # Contrarian: below the mean buys, above the mean sells.
    if z < 0:
        return 1
    if z > 0:
        return -1
    return 0


def _signal_rsi_midline(bars: Sequence[LabBar], index: int) -> int:
    rsi = _rsi_simple(bars, index, 14)
    if rsi is None:
        return 0
    if rsi < Decimal(50):
        return 1
    if rsi > Decimal(50):
        return -1
    return 0


def _signal_momentum_roc(bars: Sequence[LabBar], index: int) -> int:
    if index < 10:
        return 0
    previous = bars[index - 10].close
    if previous == 0:
        return 0
    roc = bars[index].close / previous - Decimal(1)
    if roc > 0:
        return 1
    if roc < 0:
        return -1
    return 0


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    label: str
    family: str
    thesis: str
    rule: str
    parameters: Dict[str, object]
    warmup_bars: int
    signal: Callable[[Sequence[LabBar], int], int]

    def plan_payload(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "label": self.label,
            "family": self.family,
            "thesis": self.thesis,
            "rule": self.rule,
            "parameters": self.parameters,
            "warmup_bars": self.warmup_bars,
            "signal_sides": {"long": 1, "flat": 0, "short": -1},
            "does_not_do": [
                "No profitability claim; the lab measures outcomes, it does not optimise them.",
                "No stop-loss, take-profit or leverage; every position is closed on the next bar open.",
                "No order is sent to a venue and no real money moves.",
            ],
        }


STRATEGIES: Tuple[StrategySpec, ...] = (
    StrategySpec(
        strategy_id="sma_trend_follow",
        label="SMA(5/20) trend follow",
        family="Trend following",
        thesis=(
            "A short average above a long average says recent prices are rising; "
            "the lab holds that side for one bar."
        ),
        rule="side = +1 if SMA(close,5) > SMA(close,20), -1 if below, 0 if equal.",
        parameters={"fast_window": 5, "slow_window": 20},
        warmup_bars=20,
        signal=_signal_sma_trend,
    ),
    StrategySpec(
        strategy_id="donchian_channel_position",
        label="Donchian(20) channel position",
        family="Breakout / channel",
        thesis=(
            "Where the close sits inside the last 20 bars' high-low channel "
            "describes whether buyers or sellers control the range."
        ),
        rule=(
            "side = +1 if close > midpoint of the 20-bar high/low channel, "
            "-1 if below, 0 if equal."
        ),
        parameters={"channel_bars": 20},
        warmup_bars=20,
        signal=_signal_donchian_position,
    ),
    StrategySpec(
        strategy_id="bollinger_mean_reversion",
        label="Bollinger(20) mean reversion",
        family="Mean reversion",
        thesis=(
            "A close below its 20-bar mean is treated as a temporary discount and "
            "bought; a close above the mean is treated as a temporary premium and sold."
        ),
        rule=(
            "z = (close - SMA(close,20)) / stdev(close,20); "
            "side = +1 if z < 0, -1 if z > 0, 0 if z = 0 or stdev = 0. "
            "The band threshold is the mean, not a fixed 2σ event."
        ),
        parameters={"window": 20, "trigger": "z-score sign against the 20-bar mean"},
        warmup_bars=20,
        signal=_signal_bollinger_reversion,
    ),
    StrategySpec(
        strategy_id="rsi_midline_reversal",
        label="RSI(14) midline reversal",
        family="Oscillator / reversal",
        thesis=(
            "RSI below its midline says recent losses dominate; the lab buys that "
            "weakness and sells the mirror case."
        ),
        rule=(
            "RSI(14) from simple average gains/losses; side = +1 if RSI < 50, "
            "-1 if RSI > 50, 0 if exactly 50."
        ),
        parameters={"rsi_window": 14, "midline": 50},
        warmup_bars=20,
        signal=_signal_rsi_midline,
    ),
    StrategySpec(
        strategy_id="momentum_roc",
        label="10-bar momentum (ROC)",
        family="Momentum",
        thesis=(
            "Price persistence: if the last 10 bars produced a positive return, "
            "the next bar is taken long; a negative return is taken short."
        ),
        rule="side = +1 if close / close[-10] - 1 > 0, -1 if < 0, 0 if equal.",
        parameters={"lookback_bars": 10},
        warmup_bars=20,
        signal=_signal_momentum_roc,
    ),
)

STRATEGY_BY_ID: Dict[str, StrategySpec] = {spec.strategy_id: spec for spec in STRATEGIES}


def select_strategies(strategy_ids: Optional[Sequence[str]]) -> Tuple[StrategySpec, ...]:
    if not strategy_ids:
        return STRATEGIES
    unknown = [sid for sid in strategy_ids if sid not in STRATEGY_BY_ID]
    if unknown:
        raise StrategyLabError(
            "unknown_strategy",
            f"Unknown strategy id(s): {unknown}",
            declared=sorted(STRATEGY_BY_ID),
        )
    seen: List[str] = []
    for sid in strategy_ids:
        if sid not in seen:
            seen.append(sid)
    return tuple(STRATEGY_BY_ID[sid] for sid in seen)


# -- dataset assembly -------------------------------------------------------


def assemble_dataset(
    instrument_id: str,
    series_by_interval: Mapping[int, object],
    *,
    provider_id: str,
    data_nature: str,
    fetched_at: Optional[datetime] = None,
) -> StrategyDataset:
    """Stitch non-overlapping real windows into one chronological sequence.

    ``series_by_interval`` maps interval minutes to a ``CandleSeries``-shaped
    object with ``completed`` candles and integrity fields. The newest, finest
    window is kept whole; each coarser window keeps only the bars that close at
    or before the first bar of the next finer window. No bar is interpolated and
    no bar is duplicated.
    """
    if not series_by_interval:
        raise StrategyLabError("insufficient_dataset", "No candle series were supplied.")

    intervals = sorted(series_by_interval)
    unsupported = [i for i in intervals if i not in SUPPORTED_INTERVALS]
    if unsupported:
        raise StrategyLabError(
            "unsupported_interval",
            f"Intervals not supported by the lab: {unsupported}",
            supported=sorted(SUPPORTED_INTERVALS),
        )

    kept: Dict[int, List[LabBar]] = {}
    cutoff: Optional[datetime] = None
    series_meta: Dict[int, object] = {}
    # Finest/newest first (smallest interval first). The first bar of each kept
    # segment becomes the exclusive cutoff for the next coarser segment.
    for interval in sorted(intervals):
        series = series_by_interval[interval]
        completed = list(getattr(series, "completed", ()))
        if not completed:
            raise StrategyLabError(
                "insufficient_dataset",
                f"The venue returned no completed {INTERVAL_LABELS.get(interval, interval)} bars; "
                "the lab will not substitute synthetic bars.",
                interval_minutes=interval,
            )
        bars = [
            LabBar(
                open_time=candle.open_time,
                interval_minutes=interval,
                open=_dec(candle.open),
                high=_dec(candle.high),
                low=_dec(candle.low),
                close=_dec(candle.close),
                volume=_dec(candle.volume),
            )
            for candle in completed
        ]
        if cutoff is not None:
            bars = [bar for bar in bars if bar.close_time <= cutoff]
        if not bars:
            raise StrategyLabError(
                "insufficient_dataset",
                f"The {INTERVAL_LABELS.get(interval, interval)} window does not predate "
                "the finer window, so it contributes no non-overlapping bars.",
                interval_minutes=interval,
            )
        bars.sort(key=lambda bar: bar.open_time)
        kept[interval] = bars
        series_meta[interval] = series
        cutoff = bars[0].open_time

    ordered_intervals = sorted(kept, reverse=True)  # coarsest/oldest first
    all_bars: List[LabBar] = []
    segments: List[DatasetSegment] = []
    for interval in ordered_intervals:
        bars = kept[interval]
        start_index = len(all_bars)
        all_bars.extend(bars)
        end_index = len(all_bars) - 1
        series = series_meta[interval]
        gaps = tuple(getattr(series, "gaps", ()) or ())
        segment_fingerprint = _fingerprint(
            f"{instrument_id}|{interval}m|{bar.open_time.isoformat()}|"
            f"{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}"
            for bar in bars
        )
        segments.append(
            DatasetSegment(
                interval_minutes=interval,
                bars=tuple(bars),
                start_index=start_index,
                end_index=end_index,
                fingerprint=segment_fingerprint,
                integrity_ok=bool(getattr(series, "integrity_ok", False)),
                gap_count=len(gaps),
                missing_intervals=int(getattr(series, "missing_intervals", 0) or 0),
            )
        )

    # A cross-segment overlap would silently double-count market time.
    for previous, current in zip(all_bars, all_bars[1:]):
        if current.open_time < previous.close_time:
            raise StrategyLabError(
                "overlapping_dataset",
                "Two assembled bars overlap in time; the lab refuses to double-count market time.",
                previous_open_time=previous.open_time.isoformat(),
                previous_close_time=previous.close_time.isoformat(),
                next_open_time=current.open_time.isoformat(),
            )
        if current.open_time <= previous.open_time:
            raise StrategyLabError(
                "unordered_dataset",
                "Assembled bars are not strictly ordered by open time.",
                previous_open_time=previous.open_time.isoformat(),
                next_open_time=current.open_time.isoformat(),
            )

    timestamps = [getattr(series_meta[i], "fetched_at", None) for i in ordered_intervals]
    usable = [value for value in timestamps if isinstance(value, datetime)]
    effective_fetched_at = fetched_at or (max(usable) if usable else datetime.now(timezone.utc))

    return StrategyDataset(
        instrument_id=instrument_id,
        segments=tuple(segments),
        bars=tuple(all_bars),
        provider_id=provider_id,
        data_nature=data_nature,
        fetched_at=effective_fetched_at,
        fingerprint=_fingerprint(segment.fingerprint for segment in segments),
    )


# -- simulation -------------------------------------------------------------


def _execution_price(mid: Decimal, direction: int, side: str, slippage: Decimal) -> Decimal:
    """Worse-than-mid price for the trader, on both entry and exit."""
    if side == "entry":
        return mid * (Decimal(1) + slippage) if direction > 0 else mid * (Decimal(1) - slippage)
    return mid * (Decimal(1) - slippage) if direction > 0 else mid * (Decimal(1) + slippage)


def _simulate_trade(
    *,
    trade_index: int,
    segment: DatasetSegment,
    bars: Sequence[LabBar],
    signal_index: int,
    direction: int,
    settings: LabSettings,
    cash: Decimal,
) -> Tuple[Optional[TradeRecord], Optional[str]]:
    signal_bar = bars[signal_index]
    entry_bar = bars[signal_index + 1]
    exit_bar = bars[signal_index + 2]
    mid_entry = entry_bar.open
    mid_exit = exit_bar.open
    if mid_entry <= 0 or mid_exit <= 0:
        return None, "non_positive_price"

    quantity = (settings.notional_per_trade / mid_entry).quantize(QTY_QUANT, rounding=ROUND_DOWN)
    if quantity <= 0:
        return None, "quantity_rounds_to_zero"

    exec_entry = _execution_price(mid_entry, direction, "entry", settings.slippage_rate)
    exec_exit = _execution_price(mid_exit, direction, "exit", settings.slippage_rate)
    commission = (quantity * exec_entry + quantity * exec_exit) * settings.commission_rate
    gross_pnl = (exec_exit - exec_entry) * quantity * Decimal(direction)
    # Keep the wallet at 8dp so the reported identity
    # ending_cash = starting_cash + sum(net_pnl) is exact, not approximate.
    net_pnl = (gross_pnl - commission).quantize(MONEY_QUANT)

    if direction > 0:
        required_cash = quantity * exec_entry + quantity * exec_entry * settings.commission_rate
    else:
        required_cash = settings.notional_per_trade
    if cash < required_cash:
        return None, "insufficient_cash"

    liquidated = False
    # A short can lose more than its collateral only if the price more than
    # doubles inside one bar. Clamp the loss at the posted collateral and flag
    # it instead of letting the wallet go negative.
    if direction < 0 and net_pnl < -settings.notional_per_trade:
        net_pnl = -settings.notional_per_trade
        liquidated = True

    if direction > 0:
        slippage_cost = quantity * (exec_entry - mid_entry) + quantity * (mid_exit - exec_exit)
    else:
        slippage_cost = quantity * (mid_entry - exec_entry) + quantity * (exec_exit - mid_exit)

    cash_after = cash + net_pnl
    record = TradeRecord(
        trade_index=trade_index,
        segment_interval_minutes=segment.interval_minutes,
        signal_open_time=signal_bar.open_time,
        entry_open_time=entry_bar.open_time,
        exit_open_time=exit_bar.open_time,
        side="long" if direction > 0 else "short",
        direction=direction,
        quantity=quantity,
        signal_bar_close=signal_bar.close,
        mid_entry=mid_entry,
        mid_exit=mid_exit,
        exec_entry=exec_entry,
        exec_exit=exec_exit,
        gross_pnl=gross_pnl,
        commission=commission,
        slippage_cost=slippage_cost,
        net_pnl=net_pnl,
        cash_after=cash_after.quantize(MONEY_QUANT),
        liquidated=liquidated,
    )
    return record, None


def _trade_payload(record: TradeRecord) -> dict:
    return {
        "trade_index": record.trade_index,
        "segment_interval": INTERVAL_LABELS.get(record.segment_interval_minutes),
        "signal_open_time": _iso(record.signal_open_time),
        "entry_open_time": _iso(record.entry_open_time),
        "exit_open_time": _iso(record.exit_open_time),
        "side": record.side,
        "quantity": format(record.quantity, "f"),
        "signal_bar_close": _money(record.signal_bar_close),
        "mid_entry": _money(record.mid_entry),
        "mid_exit": _money(record.mid_exit),
        "exec_entry": _money(record.exec_entry),
        "exec_exit": _money(record.exec_exit),
        "gross_pnl": _money(record.gross_pnl),
        "commission": _money(record.commission),
        "slippage_cost": _money(record.slippage_cost),
        "net_pnl": _money(record.net_pnl),
        "cash_after": _money(record.cash_after),
        "liquidated": record.liquidated,
    }


def _ledger_hash(strategy_id: str, records: Sequence[TradeRecord]) -> str:
    return _fingerprint(
        f"{strategy_id}|{record.trade_index}|{record.side}|{record.quantity}|"
        f"{record.exec_entry}|{record.exec_exit}|{record.net_pnl}|{record.cash_after}"
        for record in records
    )


def run_strategy(
    dataset: StrategyDataset,
    spec: StrategySpec,
    settings: LabSettings,
    *,
    include_trades: bool = False,
) -> dict:
    cash = settings.starting_cash
    records: List[TradeRecord] = []
    halted_reason: Optional[str] = None
    skipped_bars = 0

    for segment in dataset.segments:
        first_signal = segment.start_index + max(spec.warmup_bars, MAX_WARMUP_BARS)
        last_signal = segment.end_index - 2
        if first_signal > last_signal:
            continue
        index = first_signal
        while index <= last_signal and len(records) < settings.trades_per_strategy:
            direction = spec.signal(dataset.bars, index)
            if direction == 0:
                skipped_bars += 1
                index += 1
                continue
            record, refusal = _simulate_trade(
                trade_index=len(records) + 1,
                segment=segment,
                bars=dataset.bars,
                signal_index=index,
                direction=direction,
                settings=settings,
                cash=cash,
            )
            if refusal == "insufficient_cash":
                halted_reason = refusal
                break
            if refusal is not None:
                # A bar that cannot be priced is skipped, not fabricated.
                skipped_bars += 1
                index += 1
                continue
            if record is None:
                skipped_bars += 1
                index += 1
                continue
            records.append(record)
            cash = record.cash_after
            index += 2
        if halted_reason is not None:
            break

    if len(records) < settings.trades_per_strategy and halted_reason is None:
        halted_reason = "dataset_exhausted"

    gross_pnl = sum((record.gross_pnl for record in records), Decimal(0))
    fees = sum((record.commission for record in records), Decimal(0))
    slippage = sum((record.slippage_cost for record in records), Decimal(0))
    net_pnl = sum((record.net_pnl for record in records), Decimal(0))
    ending_cash = cash

    peak = settings.starting_cash
    max_drawdown = Decimal(0)
    for record in records:
        equity = record.cash_after
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    wins = sum(1 for record in records if record.net_pnl > 0)
    losses = sum(1 for record in records if record.net_pnl < 0)
    flats = len(records) - wins - losses
    executed = len(records)
    win_rate = (Decimal(wins) / Decimal(executed)) if executed else None
    long_count = sum(1 for record in records if record.direction > 0)
    short_count = executed - long_count

    accounting_ok = (settings.starting_cash + net_pnl).quantize(MONEY_QUANT) == ending_cash.quantize(
        MONEY_QUANT
    )

    payload = {
        "strategy_id": spec.strategy_id,
        "label": spec.label,
        "family": spec.family,
        "thesis": spec.thesis,
        "rule": spec.rule,
        "parameters": spec.parameters,
        "trades_requested": settings.trades_per_strategy,
        "trades_executed": executed,
        "completed": executed == settings.trades_per_strategy,
        "halted_reason": None if executed == settings.trades_per_strategy else halted_reason,
        "starting_cash": _money(settings.starting_cash),
        "ending_cash": _money(ending_cash),
        "net_change": _money(net_pnl),
        "gross_pnl": _money(gross_pnl),
        "total_fees": _money(fees),
        "total_slippage_cost": _money(slippage),
        "win_count": wins,
        "loss_count": losses,
        "flat_count": flats,
        "win_rate": _rate(win_rate) if win_rate is not None else None,
        "max_drawdown_pct": _rate(max_drawdown * Decimal(100)),
        "largest_win": _money(max((r.net_pnl for r in records), default=Decimal(0))),
        "largest_loss": _money(min((r.net_pnl for r in records), default=Decimal(0))),
        "direction_counts": {"long": long_count, "short": short_count},
        "skipped_signal_bars": skipped_bars,
        "liquidations": sum(1 for record in records if record.liquidated),
        "ledger_sha256": _ledger_hash(spec.strategy_id, records),
        "accounting": {
            "ending_equals_start_plus_net": accounting_ok,
            "identity": "ending_cash = starting_cash + sum(net_pnl)",
            "trades_are_closed": True,
            "open_positions_at_end": 0,
        },
        "first_trade": _trade_payload(records[0]) if records else None,
        "last_trade": _trade_payload(records[-1]) if records else None,
    }
    if include_trades:
        payload["trades"] = [_trade_payload(record) for record in records]
    return payload


def _evaluation_indices(dataset: StrategyDataset) -> List[int]:
    indices: List[int] = []
    for segment in dataset.segments:
        first_signal = segment.start_index + MAX_WARMUP_BARS
        last_signal = segment.end_index - 2
        if first_signal <= last_signal:
            indices.extend(range(first_signal, last_signal + 1))
    return indices


def _agreement_matrix(
    dataset: StrategyDataset,
    strategies: Sequence[StrategySpec],
) -> List[dict]:
    indices = _evaluation_indices(dataset)

    per_strategy: Dict[str, List[int]] = {}
    for spec in strategies:
        per_strategy[spec.strategy_id] = [spec.signal(dataset.bars, index) for index in indices]

    pairs: List[dict] = []
    for left in range(len(strategies)):
        for right in range(left + 1, len(strategies)):
            a = per_strategy[strategies[left].strategy_id]
            b = per_strategy[strategies[right].strategy_id]
            both = sum(1 for x, y in zip(a, b) if x != 0 and y != 0)
            same = sum(1 for x, y in zip(a, b) if x != 0 and x == y)
            identical = all(x == y for x, y in zip(a, b))
            pairs.append(
                {
                    "left": strategies[left].strategy_id,
                    "right": strategies[right].strategy_id,
                    "both_sided_bars": both,
                    "same_side_bars": same,
                    "agreement_rate": _rate(Decimal(same) / Decimal(both)) if both else None,
                    "identical_signal_series": identical,
                }
            )
    return pairs


def run_lab(
    dataset: StrategyDataset,
    *,
    settings: LabSettings,
    strategies: Optional[Sequence[StrategySpec]] = None,
    include_trades: bool = False,
) -> dict:
    selected = tuple(strategies or STRATEGIES)
    if not selected:
        raise StrategyLabError("no_strategies", "At least one strategy must be selected.")

    if settings.trades_per_strategy < 1:
        raise StrategyLabError("invalid_trade_count", "trades_per_strategy must be at least 1.")
    if settings.trades_per_strategy > MAX_TRADES_PER_STRATEGY:
        raise StrategyLabError(
            "invalid_trade_count",
            f"trades_per_strategy must be at most {MAX_TRADES_PER_STRATEGY}.",
        )
    if settings.starting_cash <= 0:
        raise StrategyLabError("invalid_balance", "starting_cash must be positive.")
    if settings.notional_per_trade <= 0:
        raise StrategyLabError("invalid_notional", "notional_per_trade must be positive.")
    if settings.starting_cash < settings.notional_per_trade:
        raise StrategyLabError(
            "invalid_notional",
            "notional_per_trade cannot exceed starting_cash.",
        )

    if len(dataset.bars) < MAX_WARMUP_BARS + 2:
        raise StrategyLabError(
            "insufficient_dataset",
            (
                f"The assembled market is {len(dataset.bars)} bars; at least "
                f"{MAX_WARMUP_BARS + 2} are required for one trade. No synthetic bars "
                "were substituted."
            ),
            bars_available=len(dataset.bars),
            bars_required=MAX_WARMUP_BARS + 2,
        )

    results = [
        run_strategy(dataset, spec, settings, include_trades=include_trades)
        for spec in selected
    ]

    total_requested = settings.trades_per_strategy * len(selected)
    total_executed = sum(result["trades_executed"] for result in results)

    return {
        "lane": LANE,
        "lab_version": LAB_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instrument_id": dataset.instrument_id,
        "provider_id": dataset.provider_id,
        "data_nature": dataset.data_nature,
        "settings": {
            "trades_per_strategy": settings.trades_per_strategy,
            "starting_cash_per_strategy": _money(settings.starting_cash),
            "notional_per_trade": _money(settings.notional_per_trade),
            "commission_rate_per_side": _rate(settings.commission_rate),
            "slippage_rate_per_side": _rate(settings.slippage_rate),
            "holding_bars": HOLDING_BARS,
            "entry": "next bar open after the signal bar closes",
            "exit": "the following bar open",
            "sides": "long and short",
            "currency": "USD",
        },
        "dataset": _dataset_payload(dataset),
        "strategies": results,
        "agreement": {
            "evaluated_bars": len(_evaluation_indices(dataset)),
            "pairs": _agreement_matrix(dataset, selected),
            "note": (
                "Agreement is measured on every eligible bar before wallet sequencing. "
                "Separate wallets prevent P&L leakage; they cannot make two market "
                "rules independent of the same prices."
            ),
        },
        "isolation": {
            "separate_wallets": True,
            "shared_market_bars": True,
            "shared_cash": False,
            "cross_wallet_transfers": 0,
            "per_strategy_ledgers": True,
            "deterministic": True,
            "note": (
                "Each strategy starts with its own balance and only that wallet's "
                "trades change it. The market bars are shared because there is one "
                "venue feed; signal rules and ledgers are not."
            ),
        },
        "totals": {
            "strategies": len(selected),
            "trades_requested": total_requested,
            "trades_executed": total_executed,
            "all_strategies_completed": all(result["completed"] for result in results),
        },
        "disclosures": [
            "Simulated research only: no broker order, no payment, no real money.",
            "Prices are completed Kraken public candles; the forming candle is excluded.",
            "The dataset stitches non-overlapping 4h/1h/5m/1m windows because the venue "
            "serves only ~720 recent bars per interval. One-bar holds therefore cover "
            "different wall-clock durations across segments.",
            "Commission (0.1%) and slippage (0.1%) per side are declared lab assumptions, "
            "not the venue's actual fee schedule.",
            "No profitability claim. Costs are applied on both sides and usually dominate "
            "a one-bar holding period.",
            "Market-data redistribution is NOT_CLEARED; this lane is for internal "
            "research only.",
        ],
        "disclosures_zh": [
            "此為模擬研究：不接券商、不付款、不動用真實資金。",
            "價格來自 Kraken 已完成之公開 K 線，未完成 K 線不會進入策略。",
            "由於交易所每個週期只提供最近約 720 根 K 線，資料集由 4h／1h／5m／1m "
            "不重疊時段拼接；因此「持有 1 根」在不同時段代表不同實際時間長度。",
            "每邊 0.1% 手續費與 0.1% 滑價為本實驗假設，不是交易所實際費率。",
            "不保證獲利；單根持有下，成本通常主導結果。",
            "行情資料對外重新分發尚未取得授權，本車道僅供內部研究。",
        ],
    }


def _dataset_payload(dataset: StrategyDataset) -> dict:
    return {
        "instrument_id": dataset.instrument_id,
        "provider_id": dataset.provider_id,
        "data_nature": dataset.data_nature,
        "fetched_at": _iso(dataset.fetched_at),
        "fingerprint": dataset.fingerprint,
        "total_bars": len(dataset.bars),
        "first_open_time": _iso(dataset.first_open_time),
        "last_close_time": _iso(dataset.last_close_time),
        "segments": [
            {
                "interval": INTERVAL_LABELS.get(segment.interval_minutes),
                "interval_minutes": segment.interval_minutes,
                "bar_count": len(segment.bars),
                "first_open_time": _iso(segment.first_open_time),
                "last_close_time": _iso(segment.last_close_time),
                "fingerprint": segment.fingerprint,
                "integrity_ok": segment.integrity_ok,
                "gap_count": segment.gap_count,
                "missing_intervals": segment.missing_intervals,
            }
            for segment in dataset.segments
        ],
        "non_overlapping": True,
        "overlap_rule": (
            "A coarser bar is kept only if it closes at or before the first bar of "
            "the next finer window; no timestamp is re-used."
        ),
        "forming_candles_used": False,
        "synthetic_bars_used": False,
    }


def build_plan() -> dict:
    """Static description of the lab. Performs no venue read."""
    return {
        "lane": LANE,
        "lab_version": LAB_VERSION,
        "purpose": (
            "Run five distinct signal families for exactly 1,000 one-bar round trips "
            "each on real completed bars, in separate wallets, and report the funds "
            "remaining in each wallet."
        ),
        "endpoint": {"plan": "/api/v1/strategy-lab/plan", "run": "/api/v1/strategy-lab/run"},
        "settings": {
            "trades_per_strategy_default": DEFAULT_TRADES_PER_STRATEGY,
            "trades_per_strategy_max": MAX_TRADES_PER_STRATEGY,
            "starting_cash_per_strategy": _money(DEFAULT_STARTING_CASH),
            "notional_per_trade": _money(DEFAULT_NOTIONAL_PER_TRADE),
            "commission_rate_per_side": _rate(DEFAULT_COMMISSION_RATE),
            "slippage_rate_per_side": _rate(DEFAULT_SLIPPAGE_RATE),
            "holding_bars": HOLDING_BARS,
            "currency": "USD",
        },
        "data_plan": {
            "venue": "Kraken public REST OHLC",
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "intervals": list(DEFAULT_INTERVALS),
            "per_request_bar_cap": 720,
            "assembly": (
                "Read the newest ~720 completed bars at 4h, 1h, 5m and 1m. Keep the 1m "
                "window whole; keep each coarser window only up to the first bar of the "
                "next finer window. Concatenate oldest to newest."
            ),
            "expected_bars_observed": "about 2,400-2,500 at the time of writing",
            "minimum_for_one_trade": MAX_WARMUP_BARS + 2,
            "no_synthetic_substitution": True,
            "forming_candle_policy": "excluded",
        },
        "strategies": [spec.plan_payload() for spec in STRATEGIES],
        "isolation": {
            "separate_wallets": True,
            "separate_ledgers": True,
            "shared_market_bars": True,
            "shared_cash": False,
            "cross_wallet_transfers": 0,
            "why_market_bars_are_shared": (
                "There is one venue feed. Isolation is about cash, positions and P&L, "
                "not about inventing a different price per strategy."
            ),
            "signal_agreement_reported": True,
        },
        "cost_model": {
            "commission": "0.1% of executed notional per side",
            "slippage": "0.1% worse than the bar open per side",
            "round_trip_approximation": "about 0.4% of notional before market P&L",
            "assumption_note": "Declared lab assumptions, not the venue's actual fee schedule.",
        },
        "limitations": [
            "One-bar holds are a sampling design, not a recommendation.",
            "The dataset mixes intervals; wall-clock holding time is not uniform.",
            "No stop-loss, take-profit, leverage or partial fills are modelled.",
            "The lab does not optimise parameters or claim that any rule is profitable.",
            "It is not connected to the paper portfolio, the broker lane or payments.",
            "Market-data redistribution is NOT_CLEARED; internal research only.",
        ],
        "limitations_zh": [
            "單根持有是取樣設計，不是投資建議。",
            "資料集混合不同週期，實際持倉時間長度不一致。",
            "不模擬停損、停利、槓桿或部分成交。",
            "不進行參數最佳化，也不宣稱任何策略可獲利。",
            "與模擬投組、券商車道及付款流程完全隔離。",
            "行情資料對外重新分發尚未取得授權，僅供內部研究。",
        ],
    }


__all__ = [
    "DEFAULT_COMMISSION_RATE",
    "DEFAULT_INTERVALS",
    "DEFAULT_NOTIONAL_PER_TRADE",
    "DEFAULT_STARTING_CASH",
    "DEFAULT_TRADES_PER_STRATEGY",
    "HOLDING_BARS",
    "LAB_VERSION",
    "LANE",
    "MAX_TRADES_PER_STRATEGY",
    "STRATEGIES",
    "STRATEGY_BY_ID",
    "DatasetSegment",
    "LabBar",
    "LabSettings",
    "StrategyDataset",
    "StrategyLabError",
    "StrategySpec",
    "assemble_dataset",
    "build_plan",
    "run_lab",
    "run_strategy",
    "select_strategies",
]
