"""Round-trip attribution: realised P&L, R-multiples, risk plan and provenance.

Transactions are per-fill records. Win rate, profit factor and realised R are
properties of *closed* trades, so they are derived by matching fills FIFO by
share quantity rather than read off individual rows.

Cost allocation: a buy's cost basis includes its commission, a sell's proceeds
are net of its commission, both split pro-rata across shares. Slippage is
already inside the execution price, so it lands in the basis/proceeds too and
cannot be counted twice.

Undefined is never zero. When a template declares no stop, planned risk stays
None and realised R stays None — a trade entered without a risk unit is not a
zero-risk trade.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.engine import exit_rules

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

FORWARD = "long"

TIME_OF_DAY_UNAVAILABLE = {
    "available": False,
    "value": None,
    "reason": (
        "Only daily (1d) bars are served by the connected data layer, so there is no "
        "intra-session timing to break down. Requires an intraday-capable provider, "
        "which is not connected."
    ),
    "requires": "intraday_bars",
}


def _round(value: float) -> float:
    # Engine prices are numpy scalars; round() would keep them numpy-typed and
    # leak np.float64/np.bool_ into the JSON run payload.
    return float(round(value, 4))


def _risk_plan_from_spec(strategy_spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the declared risk plan. An absent block means undeclared, not zero."""
    # Delegated for the same reason as _price_levels: the plan the journal reports
    # must be the plan the engine executed, read from one place.
    return exit_rules.plan_from_spec(strategy_spec)


def _price_levels(risk_plan: Dict[str, Any], entry_price: float) -> Tuple[Optional[float], Optional[float]]:
    # Delegated to the execution module on purpose: the levels reported in the
    # journal must be the levels the engine actually trades against. Two
    # independent definitions would let one drift from the other.
    return exit_rules.resolve_levels(risk_plan, entry_price)


def annotate_and_attribute(
    transactions: List[Dict[str, Any]],
    *,
    strategy_spec: Optional[Dict[str, Any]] = None,
    config_hash: Optional[str] = None,
    actor: str = "strategy",
) -> Dict[str, Any]:
    """Stamp provenance and the risk plan on every fill, then FIFO-match the lots.

    ``strategy_spec=None`` runs the attribution pass only, leaving existing
    risk fields on the fills untouched. Exits inherit the plan of the position
    they close, because the plan is set when risk is taken, not when it is
    released.
    """
    risk_plan = _risk_plan_from_spec(strategy_spec)
    declare_plan = strategy_spec is not None

    open_lots: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []

    entry_stop: Optional[float] = None
    entry_target: Optional[float] = None
    entry_planned_risk: Optional[float] = None
    entry_planned_r: Optional[float] = None

    for txn in sorted(transactions, key=lambda t: (t["execution_date"], t.get("signal_date") or "")):
        quantity = txn["quantity"]
        entry_price = txn["execution_price_before_costs"]

        if declare_plan:
            txn["actor"] = actor
            txn["strategy_config_hash"] = config_hash

        if txn["side"] == "BUY":
            if declare_plan:
                stop, target = _price_levels(risk_plan, entry_price)
                risk_per_share = entry_price - stop if stop is not None else None
                entry_planned_risk = _round(risk_per_share * quantity) if risk_per_share is not None else None
                entry_target = target
                entry_stop = stop
                entry_planned_r = (
                    _round((target - entry_price) / (entry_price - stop))
                    if stop is not None and target is not None and entry_price > stop
                    else None
                )
                txn["stop_price"] = entry_stop
                txn["target_price"] = entry_target
                txn["planned_risk"] = entry_planned_risk
                txn["planned_r"] = entry_planned_r
            else:
                entry_planned_risk = txn.get("planned_risk")
                entry_stop = txn.get("stop_price")
                entry_target = txn.get("target_price")
                entry_planned_r = txn.get("planned_r")

            cost_per_share = (txn["gross_amount"] + txn["commission_amount"]) / quantity if quantity else 0.0
            open_lots.append({
                "entry_date": txn["execution_date"],
                "symbol": txn["symbol"],
                "shares": quantity,
                "cost_per_share": cost_per_share,
                "risk_per_share": (entry_planned_risk / quantity) if entry_planned_risk else None,
            })
            txn["realized_pnl"] = None
            txn["realized_r"] = None
            continue

        if declare_plan:
            txn["stop_price"] = entry_stop
            txn["target_price"] = entry_target
            txn["planned_risk"] = entry_planned_risk
            txn["planned_r"] = entry_planned_r

        proceeds_per_share = (txn["gross_amount"] - txn["commission_amount"]) / quantity if quantity else 0.0
        realized_on_this_exit = 0.0
        remaining = quantity

        while remaining > 0 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot["shares"])
            realized = _round((proceeds_per_share - lot["cost_per_share"]) * matched)
            realized_on_this_exit += realized
            risk_per_share = lot["risk_per_share"]
            closed.append({
                "direction": FORWARD,
                "symbol": txn["symbol"],
                "entry_date": lot["entry_date"],
                "exit_date": txn["execution_date"],
                "shares": matched,
                "cost_per_share": _round(lot["cost_per_share"]),
                "proceeds_per_share": _round(proceeds_per_share),
                "realized_pnl": realized,
                "planned_risk": _round(risk_per_share * matched) if risk_per_share else None,
                "realized_r": _round(realized / (risk_per_share * matched)) if risk_per_share else None,
            })
            lot["shares"] -= matched
            remaining -= matched
            if lot["shares"] == 0:
                open_lots.pop(0)

        if remaining > 0:
            closed.append({
                "direction": FORWARD,
                "symbol": txn["symbol"],
                "entry_date": None,
                "exit_date": txn["execution_date"],
                "shares": remaining,
                "cost_per_share": None,
                "proceeds_per_share": _round(proceeds_per_share),
                "realized_pnl": None,
                "planned_risk": None,
                "realized_r": None,
                "note": "Sold shares with no matching buy lot; cost basis unknown, so P&L is not assumed to be zero.",
            })

        txn["realized_pnl"] = _round(realized_on_this_exit)
        planned_risk = txn.get("planned_risk")
        txn["realized_r"] = _round(realized_on_this_exit / planned_risk) if planned_risk else None

    return _summarize(closed, open_lots)


def attribute_round_trips(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """FIFO round-trip attribution over fills whose plan fields are already set."""
    return annotate_and_attribute(transactions)


def _summarize(closed: List[Dict[str, Any]], open_lots: List[Dict[str, Any]]) -> Dict[str, Any]:
    priced = [c for c in closed if c["realized_pnl"] is not None]
    wins = [c for c in priced if c["realized_pnl"] > 0]
    losses = [c for c in priced if c["realized_pnl"] < 0]
    flats = [c for c in priced if c["realized_pnl"] == 0]
    with_r = [c for c in priced if c["realized_r"] is not None]

    return {
        "closed_trades": closed,
        "closed_trade_count": len(priced),
        "unmatched_sell_count": len(closed) - len(priced),
        "win_count": len(wins),
        "loss_count": len(losses),
        "flat_count": len(flats),
        "gross_profit": _round(sum(c["realized_pnl"] for c in wins)),
        "gross_loss": _round(abs(sum(c["realized_pnl"] for c in losses))),
        "net_realized_pnl": _round(sum(c["realized_pnl"] for c in priced)),
        "trades_with_planned_risk": len(with_r),
        "mean_realized_r": _round(sum(c["realized_r"] for c in with_r) / len(with_r)) if with_r else None,
        "open_shares": sum(lot["shares"] for lot in open_lots),
    }


def day_of_week_breakdown(attribution: Dict[str, Any], trading_dates: List[str]) -> Dict[str, Any]:
    """Realised P&L by exchange-local weekday, attributed to the exit session.

    Bar dates are already exchange-local calendar dates, so no timezone
    conversion happens here; stating that keeps the breakdown from being read
    as a user-local pattern.
    """
    sessions_by_day = {idx: 0 for idx in range(7)}
    for raw in trading_dates:
        parsed = _parse_date(raw)
        if parsed is not None:
            sessions_by_day[parsed.weekday()] += 1

    rows = {
        idx: {
            "weekday": WEEKDAY_NAMES[idx],
            "weekday_index": idx,
            "sessions": sessions_by_day[idx],
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl": 0.0,
        }
        for idx in range(7) if sessions_by_day[idx] > 0
    }

    unattributed = 0
    for trade in attribution["closed_trades"]:
        parsed = _parse_date(trade["exit_date"]) if trade["realized_pnl"] is not None else None
        if parsed is None or parsed.weekday() not in rows:
            unattributed += 1
            continue
        row = rows[parsed.weekday()]
        row["closed_trades"] += 1
        row["realized_pnl"] = _round(row["realized_pnl"] + trade["realized_pnl"])
        if trade["realized_pnl"] > 0:
            row["wins"] += 1
        elif trade["realized_pnl"] < 0:
            row["losses"] += 1

    for row in rows.values():
        row["realized_pnl"] = _round(row["realized_pnl"])
        row["mean_realized_pnl"] = _round(row["realized_pnl"] / row["closed_trades"]) if row["closed_trades"] else None
        row["win_rate_pct"] = _round(row["wins"] / row["closed_trades"] * 100) if row["closed_trades"] else None

    return {
        "available": True,
        "date_semantics": "exchange_local_trading_day",
        "attribution_rule": "realized P&L booked on the exit session",
        "rows": sorted(rows.values(), key=lambda r: r["weekday_index"]),
        "unattributed_trades": unattributed,
        "caveat": (
            "Small sample: a weekday mean over a handful of closed trades is not a "
            "seasonal effect. Descriptive only."
        ),
    }


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except ValueError:
        return None
