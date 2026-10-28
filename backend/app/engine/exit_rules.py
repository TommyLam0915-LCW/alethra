"""One exit policy, shared by the backtester and the forward simulator.

Only daily OHLC bars are available, so the intra-bar price path is unknowable.
A bar whose low touched the stop and whose high touched the target carries no
evidence of which happened first. Rather than guess optimistically, every rule
here resolves so that an exit never fills better than the level declared:

* stop — fills at ``min(bar_open, stop)``. A gap down through the stop fills at
  the worse open, because that is the first price actually available. Filling at
  the stop itself would invent a price nobody could have traded at.
* target — fills at the target level exactly. A gap up beyond the target does
  not earn the better open; a limit order resting at the target fills at it.
* both touched on the same bar — the stop is assumed to fill first. This is a
  conservative assumption, disclosed rather than inferred from the data.
* levels are evaluated from the bar after the entry execution bar. The entry
  bar's own range is not used: the entry filled at its open, and the rest of
  that bar is unknowable too.
* a time/holding-period exit fills at that session's close, and only if no stop
  or target triggered on or before that session.
* end of data with a position still open force-closes at the last close.
* no declared plan means no stop and no target, so a strategy that never
  declared one behaves exactly as it did before this module existed.

Prices returned here are *before* slippage and commission; the caller applies
its own cost convention so that entry and exit legs stay symmetric.
"""

from typing import Any, Dict, List, NamedTuple, Optional

STOP = "stop"
TARGET = "target"
TIME = "time"
END_OF_DATA = "end_of_data"
# The strategy's own rule closed the position (e.g. an SMA cross-down). Kept in
# this module so every exit a journal can show comes from one vocabulary.
SIGNAL = "signal"


class ExitDecision(NamedTuple):
    reason: str
    # Price the exit is evidenced at, before slippage and commission.
    observed_price: float
    # The declared level that fired, or None for a clock/end-of-data exit.
    level: Optional[float]
    note: str


def _round(value: float) -> float:
    return round(float(value), 4)


PLAN_KEYS = ("stop_price", "stop_pct", "target_price", "target_pct")


def plan_from_spec(strategy_spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The declared risk plan. An absent block means undeclared, not zero."""
    params = (strategy_spec or {}).get("risk_parameters") or {}
    return {key: params.get(key) for key in PLAN_KEYS}


def unusable_warnings(
    risk_plan: Optional[Dict[str, Any]],
    stop: Optional[float],
    target: Optional[float],
) -> List[str]:
    """Say out loud when a declared level was not simulated.

    Dropping a stop because it sits at or above the entry is correct, but doing
    it silently would report a protected position that was never protected.
    """
    params = risk_plan or {}
    warnings: List[str] = []
    if (params.get("stop_price") is not None or params.get("stop_pct") is not None) and stop is None:
        warnings.append(
            "Declared stop was at or above the entry price, so it defines no risk unit "
            "for a long position and was not simulated."
        )
    if (params.get("target_price") is not None or params.get("target_pct") is not None) and target is None:
        warnings.append(
            "Declared target was at or below the entry price, so it would have fired on "
            "the entry bar and was not simulated."
        )
    return warnings


def resolve_levels(
    risk_plan: Optional[Dict[str, Any]], entry_price: float
) -> tuple[Optional[float], Optional[float]]:
    """Turn a declared plan into absolute stop/target prices for one entry.

    An absent block means undeclared, not zero: a position entered without a
    risk unit has no stop, and that is reported as None rather than as a stop at
    the entry price.
    """
    params = risk_plan or {}

    stop = params.get("stop_price")
    if stop is None and params.get("stop_pct") is not None:
        stop = entry_price * (1.0 - float(params["stop_pct"]))
    if stop is not None:
        stop = _round(stop)
        if stop >= entry_price:
            # A long stop at or above the entry defines no risk unit.
            stop = None

    target = params.get("target_price")
    if target is None and params.get("target_pct") is not None:
        target = entry_price * (1.0 + float(params["target_pct"]))
    if target is not None:
        target = _round(target)
        if target <= entry_price:
            # A long target at or below the entry would fire on the entry bar.
            target = None

    return stop, target


def evaluate_bar(
    bar: Dict[str, Any],
    stop: Optional[float],
    target: Optional[float],
    *,
    time_exit: bool = False,
) -> Optional[ExitDecision]:
    """Return the exit this bar forces on an open long position, else None."""
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])

    if stop is not None and low <= stop:
        fill = min(open_, stop)
        if fill < stop:
            note = (
                f"Stop {stop} gapped through: filled at the session open {fill:.4f}, "
                "worse than the declared level"
            )
        else:
            note = f"Stop {stop} triggered intra-bar"
        return ExitDecision(STOP, _round(fill), stop, note)

    if target is not None and high >= target:
        note = f"Target {target} triggered intra-bar"
        if open_ > target:
            note = (
                f"Target {target} gapped above: filled at the target, not at the "
                f"better open {open_:.4f}"
            )
        return ExitDecision(TARGET, _round(target), target, note)

    if time_exit:
        return ExitDecision(TIME, _round(close), None, "Scheduled holding-period exit at the session close")

    return None
