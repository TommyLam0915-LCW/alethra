"""Intraday opening-range-breakout engine (5-minute bars), long-only.

This module is additive: it does not change any daily strategy, and nothing in
the daily backtest or forward simulator reaches it. A strategy spec selects it
only by declaring ``timeframe`` other than ``"1d"``; specs without a timeframe
keep hashing and running exactly as before.

One config, two consumers. ``parse_intraday_config`` is the single place the
declared ``signal_parameters`` / ``risk_parameters`` are read, and both the batch
backtest (``run_opening_range_backtest``) and the incremental replay
(``OpeningRangeForwardReplay``) drive the *same* ``OpeningRangeSession`` state
machine. So a stop or a volume filter the spec declares cannot be honoured by
one path and ignored by the other: there is only one path.

Execution conventions are shared with the daily engine on purpose:
* a signal is read from a *completed* bar's close and fills at the *next* bar's
  open (the next allowed event), never on the bar that produced it;
* protective levels are resolved once against the price actually paid
  (``exit_rules.resolve_levels``) and evaluated from the bar *after* the entry
  bar, so the entry bar's own range is never used as exit evidence;
* stop/target fills reuse ``exit_rules.evaluate_bar``, which resolves a
  same-bar stop-and-target touch as stop-first and fills a gap through the stop
  at the worse open rather than at a price nobody could trade at;
* every exit applies one cost convention (``_book_exit`` from strategies.py);
* an open position at the last bar of its session is force-closed at that bar's
  close (reason ``session_end``) so no position is held across days unless the
  spec explicitly sets ``allow_cross_day``.

Synthetic-fixture note: this engine is validated against a clearly-labelled
synthetic 5-minute fixture in tests. Passing tests here demonstrate the
mechanics are correct; they are *not* evidence that the rule is profitable on
real markets.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.engine import exit_rules
from app.engine.fill_math import (
    BUY,
    SELL,
    quantity_precision_for,
    round_quantity,
    size_position,
    slipped_price,
)
from app.engine.strategies import generate_transaction

# Exit vocabulary for the intraday-only reasons. Stop/target/time reuse the
# tokens already defined in exit_rules so a journal shows one vocabulary.
SESSION_END = "session_end"

INTRADAY_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h"})

# Distinct provenance for decisions produced by the per-bar intraday replay, so a
# journal can tell an intraday fill from a daily one without reading the spec.
INTRADAY_ENGINE_VERSION = "intraday-1.0.0"


@dataclass
class IntradayConfig:
    """The declared intraday rule, parsed once and shared by both engines."""

    timeframe: str = "5m"
    opening_range_bars: int = 3
    volume_window_bars: int = 5
    volume_min_ratio: Optional[float] = 1.0
    risk_per_trade_pct: Optional[float] = None
    allow_cross_day: bool = False
    stop_pct: Optional[float] = None
    target_pct: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_parameters: Dict[str, Any] = field(default_factory=dict)


def parse_intraday_config(strategy_spec: Dict[str, Any]) -> IntradayConfig:
    """Read signal/risk parameters into an IntradayConfig, validating as we go.

    Both the backtester and the forward replay call this and nothing else, so an
    engine can never trade a different rule than the one the spec declared.
    """
    timeframe = str(strategy_spec.get("timeframe", "1d"))
    if timeframe not in INTRADAY_TIMEFRAMES:
        raise ValueError(
            f"timeframe {timeframe!r} is not an intraday timeframe; "
            f"expected one of {sorted(INTRADAY_TIMEFRAMES)}"
        )

    sig = strategy_spec.get("signal_parameters", {}) or {}
    risk = strategy_spec.get("risk_parameters", {}) or {}

    opening_range_bars = sig.get("opening_range_bars", 3)
    if not isinstance(opening_range_bars, int) or opening_range_bars < 1 or opening_range_bars > 48:
        raise ValueError("opening_range_bars must be an integer 1-48")

    volume_window_bars = sig.get("volume_window_bars", 5)
    if not isinstance(volume_window_bars, int) or volume_window_bars < 1 or volume_window_bars > 240:
        raise ValueError("volume_window_bars must be an integer 1-240")

    volume_min_ratio = sig.get("volume_min_ratio", 1.0)
    if volume_min_ratio is not None:
        if not isinstance(volume_min_ratio, (int, float)) or volume_min_ratio <= 0:
            raise ValueError("volume_min_ratio must be a positive number or null")

    risk_per_trade_pct = sig.get("risk_per_trade_pct")
    if risk_per_trade_pct is not None:
        if not isinstance(risk_per_trade_pct, (int, float)) or not (0 < risk_per_trade_pct <= 1):
            raise ValueError("risk_per_trade_pct must be in (0, 1] or null")

    allow_cross_day = bool(sig.get("allow_cross_day", False))

    return IntradayConfig(
        timeframe=timeframe,
        opening_range_bars=opening_range_bars,
        volume_window_bars=volume_window_bars,
        volume_min_ratio=None if volume_min_ratio is None else float(volume_min_ratio),
        risk_per_trade_pct=None if risk_per_trade_pct is None else float(risk_per_trade_pct),
        allow_cross_day=allow_cross_day,
        stop_pct=risk.get("stop_pct"),
        target_pct=risk.get("target_pct"),
        stop_price=risk.get("stop_price"),
        target_price=risk.get("target_price"),
        risk_parameters=risk,
    )


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def session_date(self):
        return self.timestamp.date()


def _to_bar(row: Any) -> _Bar:
    ts = row["timestamp"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    elif isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return _Bar(
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume", 0) or 0),
    )


class OpeningRangeSession:
    """Bar-by-bar state machine for one long-only opening-range breakout.

    Feed bars in chronological order via ``on_bar``; it returns zero or more
    transaction dicts. All decision logic lives here so the batch backtest and
    the incremental replay are literally the same code reading the same config.
    """

    def __init__(
        self,
        *,
        symbol: str,
        config: IntradayConfig,
        initial_cash: float,
        commission_rate: float,
        slippage_rate: float,
    ):
        self.symbol = symbol
        self.config = config
        self.quantity_precision = quantity_precision_for(symbol)
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.risk_plan = exit_rules.plan_from_spec({"risk_parameters": config.risk_parameters})

        self.cash = float(initial_cash)
        self.shares = 0
        self.entry_price: Optional[float] = None
        self.entry_ts: Optional[datetime] = None
        self.entry_bar_index: Optional[int] = None
        self.stop_level: Optional[float] = None
        self.target_level: Optional[float] = None

        self._bar_index = 0
        self._session_date = None
        self._session_bar_ordinal = 0
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._or_complete = False
        self._volume_history: List[float] = []
        self._pending_entry: Optional[Dict[str, Any]] = None
        self._pending_session_close: Optional[_Bar] = None

        self.warnings: List[str] = []

    # -- helpers ----------------------------------------------------------
    def _warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _is_session_last_bar(self, bar: _Bar, next_bar: Optional[_Bar]) -> bool:
        """A bar ends its session when the next bar is a different date or absent."""
        if next_bar is None:
            return True
        return next_bar.session_date != bar.session_date

    def _reset_session(self, session_date) -> None:
        self._session_date = session_date
        self._session_bar_ordinal = 0
        self._or_high = None
        self._or_low = None
        self._or_complete = False
        self._volume_history = []

    def adopt_fill(
        self,
        *,
        side: str,
        quantity: float,
        price: float,
        net_cash_change: float,
        at: Optional[datetime],
    ) -> None:
        """Take in a trade the account already booked, outside the strategy's signals.

        The forward replay rebuilds itself bar by bar on every advance, so a
        manual fill that is not re-adopted during that rebuild would vanish from
        the replay's book the next time the user advances while still sitting in
        the account. Both books must show the same trades on the same bars.
        """
        quantity = float(quantity)
        if side == "BUY":
            held = float(self.shares)
            if held > 0 and self.entry_price is not None:
                self.entry_price = round(
                    (float(self.entry_price) * held + float(price) * quantity)
                    / (held + quantity),
                    4,
                )
            else:
                self.entry_price = float(price)
                self.entry_ts = at
            self.shares = self._quantity(held + quantity)
        elif side == "SELL":
            remaining = self._quantity(float(self.shares) - quantity)
            self.shares = remaining if remaining > 0 else self._zero_quantity()
            if self.shares == 0:
                self.entry_price = None
                self.entry_ts = None
        else:
            raise ValueError(f"Unknown side: {side!r}")
        self.cash = round(float(self.cash) + float(net_cash_change), 4)

    # -- main entry point -------------------------------------------------
    def on_bar(self, bar: _Bar, next_bar: Optional[_Bar]) -> List[Dict[str, Any]]:
        """Process one bar; return any transactions booked on it."""
        txns: List[Dict[str, Any]] = []

        # Detect a new session (first bar, or the calendar date rolled over).
        if self._session_date is None or bar.session_date != self._session_date:
            self._reset_session(bar.session_date)

        self._session_bar_ordinal += 1
        current_index = self._bar_index
        self._bar_index += 1

        # 1. A pending entry (signalled on the previous bar's close) fills at
        #    this bar's open — the next allowed event after the signal.
        if self._pending_entry is not None:
            txns.extend(self._fill_pending_entry(bar))

        # 2. If we are holding, evaluate exits against this bar's range. Levels
        #    only apply from the bar after the entry bar.
        if self.shares > 0 and self.entry_bar_index is not None and current_index > self.entry_bar_index:
            exit_txn = self._evaluate_exit(bar)
            if exit_txn is not None:
                txns.append(exit_txn)

        # 3. Session-end forced close: flatten at this bar's close unless the
        #    spec explicitly allows carrying overnight.
        if self.shares > 0 and self._is_session_last_bar(bar, next_bar) and not self.config.allow_cross_day:
            txns.append(self._force_close_session_end(bar))

        # 4. Build the opening range, then look for a breakout signal on this
        #    bar's close (only when flat and the opening range is complete and
        #    this is not the session's last tradable bar for a fresh entry).
        if self.shares == 0 and self._pending_entry is None:
            self._update_opening_range(bar)
            if self._or_complete and not self._is_session_last_bar(bar, next_bar):
                signal = self._detect_breakout(bar)
                if signal is not None:
                    self._pending_entry = signal

        # 5. Volume history is updated *after* signal detection so the filter
        #    for a signal bar uses only strictly-prior bars (no future data).
        self._volume_history.append(bar.volume)

        return txns

    # -- opening range ----------------------------------------------------
    def _update_opening_range(self, bar: _Bar) -> None:
        if self._or_complete:
            return
        if self._session_bar_ordinal <= self.config.opening_range_bars:
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)
            if self._session_bar_ordinal == self.config.opening_range_bars:
                self._or_complete = True

    def _detect_breakout(self, bar: _Bar) -> Optional[Dict[str, Any]]:
        """Long-only: a completed-bar close above the opening-range high, with
        the past-only volume filter satisfied, arms an entry for the next bar."""
        if self._or_high is None:
            return None
        if bar.close <= self._or_high:
            return None

        # Past-only volume filter. The history currently holds bars strictly
        # before this signal bar (this bar is appended after detection).
        if self.config.volume_min_ratio is not None:
            window = self._volume_history[-self.config.volume_window_bars:]
            if len(window) < self.config.volume_window_bars:
                return None
            baseline = sum(window) / len(window)
            if baseline <= 0 or bar.volume < self.config.volume_min_ratio * baseline:
                return None

        return {
            "signal_timestamp": bar.timestamp.isoformat(),
            "signal_close": bar.close,
            "or_high": self._or_high,
            "rationale": (
                f"Opening-range breakout: close {bar.close:.4f} above OR high "
                f"{self._or_high:.4f} on {self.config.timeframe} bar"
            ),
        }

    # -- entry ------------------------------------------------------------
    def _quantity(self, value: float) -> float:
        """Round a held quantity to the instrument's tradable precision."""
        return round_quantity(value, self.quantity_precision)

    def _zero_quantity(self):
        return 0.0 if self.quantity_precision else 0

    def _size_shares(self, exec_price: float) -> float:
        """Fixed-fractional risk sizing when a stop is declared and resolvable,
        otherwise the maximum affordable quantity at the instrument's precision."""
        stop, _ = exit_rules.resolve_levels(self.risk_plan, exec_price)
        if self.config.risk_per_trade_pct is not None and stop is not None and exec_price > stop:
            risk_cash = self.cash * self.config.risk_per_trade_pct
            per_share_risk = exec_price - stop
            sized = self._quantity(risk_cash / per_share_risk)
            max_affordable = size_position(
                self.cash, exec_price, self.commission_rate, self.quantity_precision
            )
            return max(0, min(sized, max_affordable))
        return size_position(
            self.cash, exec_price, self.commission_rate, self.quantity_precision
        )

    def _fill_pending_entry(self, bar: _Bar) -> List[Dict[str, Any]]:
        order = self._pending_entry
        self._pending_entry = None

        exec_price = slipped_price(bar.open, BUY, self.slippage_rate)
        quantity = self._size_shares(exec_price)
        if quantity <= 0:
            self._warn_once(
                f"Blocked opening-range entry on {bar.timestamp.isoformat()}: "
                "insufficient cash for 1 share at the sized risk"
            )
            return []

        txn = generate_transaction(
            signal_date=order["signal_timestamp"],
            execution_date=bar.timestamp.isoformat(),
            symbol=self.symbol,
            side=BUY,
            quantity=quantity,
            observed_price=bar.open,
            execution_price=exec_price,
            slippage_rate=self.slippage_rate,
            commission_rate=self.commission_rate,
            cash_before=self.cash,
            shares_after=quantity,
            rationale=order["rationale"],
        )
        self.cash = txn["cash_after"]
        self.shares = quantity
        self.entry_price = exec_price
        self.entry_ts = bar.timestamp
        self.entry_bar_index = self._bar_index - 1  # this bar's index

        self.stop_level, self.target_level = exit_rules.resolve_levels(self.risk_plan, exec_price)
        for message in exit_rules.unusable_warnings(self.risk_plan, self.stop_level, self.target_level):
            self._warn_once(message)

        return [txn]

    # -- exits ------------------------------------------------------------
    def _evaluate_exit(self, bar: _Bar) -> Optional[Dict[str, Any]]:
        decision = exit_rules.evaluate_bar(
            {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close},
            self.stop_level,
            self.target_level,
        )
        if decision is None:
            return None
        return self._book(bar, decision.observed_price, decision.reason, decision.note)

    def _force_close_session_end(self, bar: _Bar) -> Dict[str, Any]:
        note = (
            f"Session-end forced close at {bar.timestamp.isoformat()} close — "
            "no overnight carry declared"
        )
        return self._book(bar, bar.close, SESSION_END, note)

    def _book(self, bar: _Bar, reference_price: float, reason: str, note: str) -> Dict[str, Any]:
        """Sell the whole position with the one shared cost convention: the booked
        price is the reference price less slippage, commission sits on the gross at
        that price, and cash moves by exactly the net the transaction reports."""
        quantity = self.shares
        sell_price = slipped_price(reference_price, SELL, self.slippage_rate)

        txn = generate_transaction(
            signal_date=self.entry_ts.isoformat() if self.entry_ts else None,
            execution_date=bar.timestamp.isoformat(),
            symbol=self.symbol,
            side=SELL,
            quantity=quantity,
            observed_price=reference_price,
            execution_price=sell_price,
            slippage_rate=self.slippage_rate,
            commission_rate=self.commission_rate,
            cash_before=self.cash,
            shares_after=0,
            rationale=note,
            exit_reason=reason,
        )
        self.cash = txn["cash_after"]
        self._clear_position_state()
        return txn

    def _clear_position_state(self) -> None:
        self.shares = 0
        self.entry_price = None
        self.entry_ts = None
        self.entry_bar_index = None
        self.stop_level = None
        self.target_level = None


def _prepare_bars(bars: pd.DataFrame) -> List[_Bar]:
    """Sort by timestamp and materialise into _Bar objects."""
    if bars is None or len(bars) == 0:
        return []
    df = bars.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return [_to_bar(row) for _, row in df.iterrows()]


def run_opening_range_backtest(
    *,
    symbol: str,
    bars: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
    config: IntradayConfig,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Batch-run the opening-range rule over all bars. Uses the same
    ``OpeningRangeSession`` the forward replay drives."""
    prepared = _prepare_bars(bars)
    engine = OpeningRangeSession(
        symbol=symbol,
        config=config,
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )

    transactions: List[Dict[str, Any]] = []
    for i, bar in enumerate(prepared):
        next_bar = prepared[i + 1] if i + 1 < len(prepared) else None
        transactions.extend(engine.on_bar(bar, next_bar))

    return transactions, engine.warnings


class OpeningRangeForwardReplay:
    """Incremental replay over the *same* state machine and *same* config.

    ``push_bar`` feeds one bar at a time (as a forward session would receive
    them) and returns the transactions booked on it. ``lookahead_next`` must be
    supplied so the session-end rule can tell whether the current bar is the
    last of its session; a real forward feed knows this from the session
    calendar. This proves the config the backtest consumed is the config the
    replay consumes: there is no second implementation to drift.
    """

    def __init__(
        self,
        *,
        symbol: str,
        config: IntradayConfig,
        initial_cash: float,
        commission_rate: float,
        slippage_rate: float,
    ):
        self._engine = OpeningRangeSession(
            symbol=symbol,
            config=config,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
        )

    @property
    def warnings(self) -> List[str]:
        return self._engine.warnings

    @property
    def cash(self) -> float:
        return self._engine.cash

    @property
    def shares(self) -> int:
        return self._engine.shares

    @property
    def entry_price(self) -> Optional[float]:
        return self._engine.entry_price

    @property
    def entry_ts(self) -> Optional[datetime]:
        return self._engine.entry_ts

    def adopt_fill(
        self,
        *,
        side: str,
        quantity: float,
        price: float,
        net_cash_change: float,
        at: Optional[datetime],
    ) -> None:
        """See :meth:`OpeningRangeSession.adopt_fill`."""
        self._engine.adopt_fill(
            side=side,
            quantity=quantity,
            price=price,
            net_cash_change=net_cash_change,
            at=at,
        )

    def push_bar(self, bar_row: Any, next_bar_row: Optional[Any]) -> List[Dict[str, Any]]:
        bar = _to_bar(bar_row)
        next_bar = _to_bar(next_bar_row) if next_bar_row is not None else None
        return self._engine.on_bar(bar, next_bar)

    def run(self, bars: pd.DataFrame) -> List[Dict[str, Any]]:
        """Convenience: replay a whole frame bar-by-bar through push_bar."""
        prepared = _prepare_bars(bars)
        txns: List[Dict[str, Any]] = []
        for i, bar in enumerate(prepared):
            nxt = prepared[i + 1] if i + 1 < len(prepared) else None
            nxt_row = None
            if nxt is not None:
                nxt_row = {
                    "timestamp": nxt.timestamp,
                    "open": nxt.open,
                    "high": nxt.high,
                    "low": nxt.low,
                    "close": nxt.close,
                    "volume": nxt.volume,
                }
            bar_row = {
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            txns.extend(self.push_bar(bar_row, nxt_row))
        return txns
