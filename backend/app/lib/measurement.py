"""One rule for a number that was never measured.

Three kinds of number reach the wire from this backend and they must not share one
coercion:

* a **stored measure** -- a Numeric column some writer either filled in or left NULL.
  NULL means "not measured"; a stored ``0.00`` means "measured, and the answer was
  zero". The two are different claims and cannot be rendered the same way.
* a **ratio** -- win rate, Sharpe, profit factor, average. When its denominator has
  no observations the value is *undefined*, which is not the same claim as ``0``.
* an **additive sum** -- ``SUM(net_pnl)`` over no trades is the empty sum, and the
  empty sum really is ``0``.

``float(x) if x else 0`` collapses all three at once: it shows an unmeasured
``win_rate`` as 0% ("every trade lost") and an unmeasured ``max_drawdown_pct`` as
0.0% ("never drew down", the flattering end of the range). ``float(x) if x else
None`` collapses them the other way and turns a measured ``mfe_pct`` of 0.0000 --
a trade that never went into profit -- into "not measured". Both spellings are
replaced by the four names below, so the direction of the error is stated at the
call site and a scanner can demand a verdict for any new truthiness coercion.

The response models that carry these values have to be able to express ``None``;
a field declared as plain ``float`` cannot, and FastAPI would reject the honest
answer. See ``app/api/paper_trading.py`` and ``app/api/journal.py``.
"""
from __future__ import annotations

from typing import Any, Optional

__all__ = ["measured", "summed", "ratio", "percent_ratio"]


def measured(value: Any) -> Optional[float]:
    """A stored measure: NULL stays None, a measured zero stays 0.0."""
    if value is None:
        return None
    return float(value)


def summed(value: Any) -> float:
    """An additive sum: NULL is the empty sum, which is 0.0.

    Only sound for a value *defined* as a sum over a set of rows. Applying it to a
    ratio invents a number where the math has no answer, which is the defect this
    module exists to stop.
    """
    if value is None:
        return 0.0
    return float(value)


def ratio(numerator: Any, denominator: Any) -> Optional[float]:
    """A proportion: an empty denominator is undefined, so it yields None, not 0.0."""
    if denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def percent_ratio(numerator: Any, denominator: Any) -> Optional[float]:
    """A proportion in percent, e.g. 62.5 for 5 wins out of 8 trades.

    None when there are no observations: a run that never traded has no win rate,
    it does not have a win rate of zero.
    """
    value = ratio(numerator, denominator)
    if value is None:
        return None
    return value * 100.0
