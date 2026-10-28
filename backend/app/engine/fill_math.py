"""One fill arithmetic, shared by every path that turns a price into money.

The product computes the same fill in four places: the batch backtester's
strategy templates, the per-bar forward replay, the paper ledger, and the manual
order path. Each of them used to round in its own order — one rounded the slipped
price to four places and booked cash from it, another reported the rounded price
but moved cash built from the unrounded one, a third charged commission and
slippage additively off the unslipped cost. The consequence is that a user who
backtested a locked spec and then replayed it saw two different books for one
idea, and a journal row could not reproduce its own cash movement.

This module is the only place that decides what a fill costs:

* ``slipped_price`` — the price actually booked. The observed bar price moved
  *against* the trader (a buy pays more, a sell receives less), then rounded to
  the price precision the journal reports. Rounding here rather than at the
  caller is the point: the reported price must be the price the money used.
* ``fill_economics`` — gross, commission, slippage and net cash change, all
  derived from that same rounded price. Commission is charged on the gross at the
  slipped price, never on the unslipped cost, and slippage is never charged
  twice.
* ``size_position`` — how many whole shares the cash affords at that price,
  commission included, so a sizing decision cannot differ from the fill decision.

Nothing here commits a transaction or touches a database; callers own the
bookkeeping, and every caller must own its rollback.
"""

from typing import NamedTuple

PRICE_PLACES = 4

BUY = "BUY"
SELL = "SELL"


class FillEconomics(NamedTuple):
    gross_amount: float
    commission_amount: float
    slippage_amount: float
    net_cash_change: float


def _money(value: float) -> float:
    """Plain float at money precision — numpy scalars abort writes on PostgreSQL."""
    return round(float(value), PRICE_PLACES)


def slipped_price(observed_price: float, side: str, slippage_rate: float) -> float:
    """The booked fill price: the observed price, moved against the trader."""
    observed = float(observed_price)
    rate = float(slippage_rate)
    if side == BUY:
        return _money(observed * (1 + rate))
    if side == SELL:
        return _money(observed * (1 - rate))
    raise ValueError(f"Unknown side: {side!r}")


def fill_economics(
    side: str,
    quantity: float,
    observed_price: float,
    execution_price: float,
    commission_rate: float,
) -> FillEconomics:
    """Gross, commission, slippage and net cash for one fill at one price.

    ``execution_price`` already carries slippage, so the slippage figure is the
    difference between it and what the user observed — never an extra charge on
    top of the price.
    """
    quantity = float(quantity)
    execution_price = float(execution_price)
    rate = float(commission_rate)

    gross = _money(quantity * execution_price)
    commission = _money(gross * rate)
    slippage = _money(abs(execution_price - float(observed_price)) * quantity)

    if side == BUY:
        net = _money(-(gross + commission))
    elif side == SELL:
        net = _money(gross - commission)
    else:
        raise ValueError(f"Unknown side: {side!r}")

    return FillEconomics(
        gross_amount=gross,
        commission_amount=commission,
        slippage_amount=slippage,
        net_cash_change=net,
    )


def size_position(cash: float, execution_price: float, commission_rate: float) -> int:
    """Whole shares ``cash`` affords at ``execution_price``, commission included."""
    affordable = float(cash) / (float(execution_price) * (1 + float(commission_rate)))
    return int(affordable)
