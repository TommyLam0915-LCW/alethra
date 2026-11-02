"""A submitted paper order is an intent the market has not answered yet.

The account used to book a fill inside the request that created the order: cash,
position and a journal row all appeared at submit time, priced off whatever the
fixture happened to call "latest". Three things followed from that — a pending
order read as a holding, a decision made after a session closed got crammed into
the bar the user was already watching, and a retry or restart could book the same
intent a second time.

The lifecycle this module owns:

    交易提交 → pending／等待 → 市場開市／可執行 → 真實執行 → fills／settlement
             → portfolio cash／position → journal／CSV

* ``PaperOrder.data_cutoff_at`` is the last market date the user had *observed*
  when they submitted. An order is due at the first stored bar strictly after
  it, so the decision clock and the execution clock are separate quantities and
  an order never fills inside the bar the user was watching. A post-close
  decision therefore waits for the next available window instead of squeezing
  into the current bar.
* Pending is not executed. Submitting books no cash, no position and no fill
  row; the journal still shows the order, as an ``open`` record.
* The executor is idempotent because only a ``pending`` order can be booked, and
  all lifecycle state lives in rows — a restart resumes from the database and
  replaying the same bar cannot move cash twice.
* An order that cannot be honoured is said, not dropped. Insufficient cash at
  execution rejects the order with its reason; a dataset that runs out cancels
  the pending orders as expired; an asset with no quote on the window stays
  pending and is reported as blocked rather than filled at an invented price.
* Prices and costs come from :mod:`app.engine.fill_math` through
  :func:`app.services.paper_execution.book_fill` — the same convention the
  backtest, the replay and rule-triggered trades use, so one trade cannot be
  priced two ways.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.engine.fill_math import BUY, SELL, slipped_price
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderStatus,
    OutcomeStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
    RiskCheckStatus,
)
from app.models.simulation import SimulationSession
from app.services.paper_execution import PaperExecutionError, book_fill

SETTLEMENT_POLICY_LABEL = "T+1 (next stored trading session of the bound dataset)"

# Reasons a pending order is not executable yet, shown to the user verbatim.
BLOCKER_NO_LINKED_SESSION = "no_market_clock"
BLOCKER_NO_QUOTE = "no_quote_on_window"
BLOCKER_PORTFOLIO_PAUSED = "portfolio_paused"
BLOCKER_PORTFOLIO_RETIRED = "portfolio_retired"
BLOCKER_PORTFOLIO_COMPLETED = "portfolio_completed"


class LifecycleError(Exception):
    """A lifecycle request that cannot be honoured. Carries a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _stamp(now: Optional[datetime]) -> datetime:
    """Naive UTC, matching every other timestamp column in this schema."""
    return now or datetime.utcnow()


def resolve_session_portfolio(
    db: Session, session: SimulationSession
) -> PaperPortfolio:
    """The one account a session's trades settle into.

    A session with no linked portfolio has no account of record, and a session
    with two could settle into either one. Both are refused by name instead of
    picking the first row, because a silently chosen account is a wrong balance
    the user cannot see.
    """
    portfolios = (
        db.query(PaperPortfolio)
        .filter(PaperPortfolio.simulation_session_id == session.id)
        .all()
    )
    if not portfolios:
        raise LifecycleError(
            "no_linked_portfolio",
            f"Replay session {session.id} has no linked paper portfolio. A pending order needs a "
            "ledger to settle against, so create and bind an account before submitting one.",
        )
    if len(portfolios) > 1:
        raise LifecycleError(
            "ambiguous_portfolio_binding",
            f"Replay session {session.id} is bound to {len(portfolios)} paper portfolios "
            f"({', '.join(sorted(str(p.id) for p in portfolios))}). The system will not choose one "
            "to settle against; keep a single binding.",
        )
    return portfolios[0]


def next_execution_window(
    db: Session,
    *,
    instrument_id: str,
    provider_id: Optional[str],
    start_date: str,
    end_date: str,
    after_date: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The first bar of the bound dataset that is strictly after ``after_date``.

    Reads the same rows the chart plots and the replay cursor walks, so a paper
    order cannot be priced off a different series than the one the user decided
    against. ``after_date`` None means the user has not observed a bar yet, and
    the dataset's first bar is the window.
    """
    query = db.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == instrument_id,
        MarketDataBar.bar_date >= start_date,
        MarketDataBar.bar_date <= end_date,
    )
    if provider_id:
        query = query.filter(MarketDataBar.provider_id == provider_id)
    if after_date:
        query = query.filter(MarketDataBar.bar_date > date.fromisoformat(after_date))

    bar = query.order_by(MarketDataBar.bar_date).first()
    if bar is None:
        return None
    return {
        "date": bar.bar_date.isoformat(),
        "open": float(bar.open),
        "bar_timestamp": bar.bar_timestamp,
        "provider_id": bar.provider_id,
    }


def settle_date_after(db: Session, *, instrument_id: str, provider_id: Optional[str], execution_date: str) -> Optional[str]:
    """The settlement session for a fill, from the dataset — never invented.

    US equities settle one trading session after the trade date, and the only
    trading calendar this product has is the bound dataset. When the dataset has
    no later bar, settlement is unknowable and this returns None rather than a
    guessed calendar date.
    """
    window = next_execution_window(
        db,
        instrument_id=instrument_id,
        provider_id=provider_id,
        start_date=execution_date,
        end_date="9999-12-31",
        after_date=execution_date,
    )
    return window["date"] if window else None


def submit_pending(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    decision: DecisionRecord,
    order: PaperOrder,
    now: Optional[datetime] = None,
    data_cutoff_at: Optional[str] = None,
) -> Tuple[DecisionRecord, PaperOrder]:
    """Persist a submission as an intent: one decision row, one pending order.

    Nothing here touches cash, positions or fills — that is the whole point of
    the call. The order is forced to ``pending`` so a submit path cannot smuggle
    an executed state in.
    """
    stamp = _stamp(now)
    decision.decision_time = stamp
    order.status = OrderStatus.pending
    order.created_at = stamp
    if data_cutoff_at is not None:
        order.data_cutoff_at = data_cutoff_at

    db.add(decision)
    db.flush()
    if order.decision_id is None:
        order.decision_id = decision.id
    db.add(order)
    db.flush()

    from app.services import emit_event

    emit_event(
        db,
        entity_type="paper_portfolio",
        entity_id=portfolio.id,
        event_type="paper_order_submitted",
        actor_id=decision.owner_id,
        actor_type=order.actor_type,
        data_cutoff_at=order.data_cutoff_at,
        payload={
            "order_id": str(order.id),
            "decision_id": str(decision.id),
            "asset_id": order.asset_id,
            "side": order.side.value,
            "quantity": float(order.quantity),
            "status": order.status.value,
            "decided_on_cutoff": order.data_cutoff_at,
            "idempotency_key": order.idempotency_key,
            "booked_at_submit": False,
        },
    )
    return decision, order


def find_existing_by_key(
    db: Session, *, portfolio: PaperPortfolio, idempotency_key: Optional[str]
) -> Optional[PaperOrder]:
    """The order a repeated submission is replaying, if any."""
    if not idempotency_key:
        return None
    return (
        db.query(PaperOrder)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.idempotency_key == idempotency_key,
        )
        .first()
    )


def _is_due(order: PaperOrder, execution_date: str) -> bool:
    """First window strictly after what the user had seen when they submitted."""
    cutoff = order.data_cutoff_at
    if cutoff is None:
        return True
    return execution_date > cutoff


def execute_due_orders(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    instrument_id: str,
    execution_date: str,
    observed_open: float,
    commission_rate: float,
    slippage_rate: float,
    provider_id: Optional[str] = None,
    bar_timestamp: Optional[datetime] = None,
    actor_id: Any = None,
    now: Optional[datetime] = None,
    exclude_order_ids: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    """Fill the pending orders this bar is the first available window for.

    Called by the market clock — the replay advancing onto a bar — never by the
    submitting request. Each booked order reports its own settlement date, taken
    from the dataset rather than a calendar assumption.

    A paused portfolio still exits and still refuses to open: taking on new risk
    while paused is the thing pausing exists to stop.
    """
    stamp = _stamp(now)
    # The market clock, not the submitting request, gets here — so two advancing
    # sessions (or a retry racing the original) can both hold a balance read
    # before either booked. Re-read the account under a row lock so the second
    # one waits, then sees the cash the first one actually left behind.
    portfolio = (
        db.query(PaperPortfolio)
        .populate_existing()
        .filter(PaperPortfolio.id == portfolio.id)
        .with_for_update()
        .first()
    )
    query = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.status == OrderStatus.pending,
            PaperOrder.asset_id == instrument_id,
        )
        .order_by(PaperOrder.created_at, PaperOrder.id)
    )
    orders = query.all()
    if exclude_order_ids:
        excluded = {str(x) for x in exclude_order_ids}
        orders = [o for o in orders if str(o.id) not in excluded]

    results: List[Dict[str, Any]] = []
    for order in orders:
        if not _is_due(order, execution_date):
            continue

        if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
            # Retirement is terminal, so an intent queued before it is not a trade
            # the closed account may still make — in either direction.
            results.append(
                {
                    "order_id": str(order.id),
                    "status": OrderStatus.pending.value,
                    "executed": False,
                    "blocker": (
                        BLOCKER_PORTFOLIO_RETIRED
                        if portfolio.status == PortfolioStatus.retired
                        else BLOCKER_PORTFOLIO_COMPLETED
                    ),
                    "message": (
                        f"The account is {portfolio.status.value}, so this order stays pending "
                        "and is not executed."
                    ),
                }
            )
            continue

        if portfolio.status == PortfolioStatus.paused and order.side == OrderSide.BUY:
            # Left pending and said out loud: pausing must not convert an open
            # intent into a position, and must not lose the intent either.
            results.append(
                {
                    "order_id": str(order.id),
                    "status": OrderStatus.pending.value,
                    "executed": False,
                    "blocker": BLOCKER_PORTFOLIO_PAUSED,
                    "message": "The account is paused, so this buy order stays pending and is not executed.",
                }
            )
            continue

        side = order.side.value
        observed_price = float(observed_open)
        execution_price = slipped_price(observed_price, side, slippage_rate)

        try:
            fill = book_fill(
                db,
                order=order,
                portfolio=portfolio,
                execution_date=execution_date,
                observed_price=observed_price,
                execution_price=execution_price,
                quantity=float(order.quantity),
                commission_rate=commission_rate,
                fill_reason=f"manual_{side.lower()}",
                bar_timestamp=bar_timestamp,
            )
        except PaperExecutionError as exc:
            # The order cannot be honoured at this window. Reject it with the
            # reason so the journal shows a closed record instead of a trade
            # that never happened or a silently vanishing order.
            order.status = OrderStatus.rejected
            order.risk_check_status = RiskCheckStatus.failed
            _stamp_decision_outcome(db, order, stamp)
            results.append(
                {
                    "order_id": str(order.id),
                    "status": OrderStatus.rejected.value,
                    "executed": False,
                    "blocked_reason": exc.message,
                }
            )
            continue

        settlement_date = settle_date_after(
            db,
            instrument_id=instrument_id,
            provider_id=provider_id,
            execution_date=execution_date,
        )
        _stamp_decision_outcome(db, order, stamp)

        from app.services import emit_event

        emit_event(
            db,
            entity_type="paper_portfolio",
            entity_id=portfolio.id,
            event_type="paper_order_executed",
            actor_id=actor_id,
            actor_type="system",
            data_cutoff_at=execution_date,
            payload={
                "order_id": str(order.id),
                "fill_id": str(fill.id),
                "asset_id": order.asset_id,
                "side": side,
                "quantity": float(order.quantity),
                "observed_price": round(observed_price, 4),
                "execution_price": execution_price,
                "commission_amount": fill.commission_amount,
                "slippage_amount": fill.slippage_amount,
                "net_cash_change": float(fill.net_cash_change),
                "order_created_at_utc": order.created_at.isoformat() if order.created_at else None,
                "executed_at_utc": stamp.isoformat(),
                "execution_date": execution_date,
                "bar_timestamp": bar_timestamp.isoformat() if bar_timestamp else None,
                "settlement_date": settlement_date,
                "settlement_policy": SETTLEMENT_POLICY_LABEL,
                "cash_after": float(portfolio.current_virtual_cash),
            },
        )

        results.append(
            {
                "order_id": str(order.id),
                "fill_id": str(fill.id),
                "status": OrderStatus.filled.value,
                "executed": True,
                "side": side,
                "quantity": float(order.quantity),
                "observed_price": round(observed_price, 4),
                "execution_price": execution_price,
                "commission_amount": fill.commission_amount,
                "slippage_amount": fill.slippage_amount,
                "net_cash_change": float(fill.net_cash_change),
                "execution_date": execution_date,
                "settlement_date": settlement_date,
                "cash_after": float(portfolio.current_virtual_cash),
            }
        )

    return results


def _stamp_decision_outcome(db: Session, order: PaperOrder, stamp: datetime) -> None:
    """Record the lifecycle instant on the decision that produced the order."""
    if order.decision_id is None:
        return
    decision = (
        db.query(DecisionRecord).filter(DecisionRecord.id == order.decision_id).first()
    )
    if decision is None:
        return
    decision.risk_flags = {
        **(decision.risk_flags or {}),
        "order_status": order.status.value,
        "executed_or_closed_at_utc": stamp.isoformat(),
    }
    db.flush()


def cancel_order(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    order: PaperOrder,
    actor_id: Any,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
    actor_type: str = "unknown",
) -> PaperOrder:
    """Withdraw an order that has not been executed.

    Only a ``pending`` order can be cancelled. Cancelling an executed order would
    erase a trade the account already settled, so it is refused rather than
    quietly reversed.
    """
    if order.status != OrderStatus.pending:
        raise LifecycleError(
            "not_cancellable",
            f"The order is '{order.status.value}'; only unfilled pending orders can be cancelled. "
            "Close an executed position with an opposite order instead.",
        )

    stamp = _stamp(now)
    order.status = OrderStatus.cancelled
    _stamp_decision_outcome(db, order, stamp)
    if order.decision_id:
        decision = (
            db.query(DecisionRecord)
            .filter(DecisionRecord.id == order.decision_id)
            .first()
        )
        if decision is not None:
            decision.risk_flags = {
                **(decision.risk_flags or {}),
                "cancel_reason": reason,
            }

    from app.services import emit_event

    emit_event(
        db,
        entity_type="paper_portfolio",
        entity_id=portfolio.id,
        event_type="paper_order_cancelled",
        actor_id=actor_id,
        actor_type=actor_type,
        data_cutoff_at=order.data_cutoff_at,
        payload={
            "order_id": str(order.id),
            "asset_id": order.asset_id,
            "side": order.side.value,
            "quantity": float(order.quantity),
            "reason": reason,
            "cancelled_at_utc": stamp.isoformat(),
            "cash_moved": False,
            "fill_created": False,
        },
    )
    db.flush()
    return order


def expire_pending_orders(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    actor_id: Any,
    reason: str,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Close out orders the market clock will never reach.

    When a bound dataset runs out — or an account is retired — a pending order
    has no window left to execute in. ``OrderStatus`` has no ``expired`` value,
    so the order is cancelled and the *expiry* is recorded where the journal can
    read it: the decision's outcome status, which already distinguishes
    ``expired``. The two are reported apart rather than blurring an unfilled
    order into a user-cancelled one.
    """
    stamp = _stamp(now)
    pending = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.status == OrderStatus.pending,
        )
        .order_by(PaperOrder.created_at, PaperOrder.id)
        .all()
    )

    expired: List[Dict[str, Any]] = []
    for order in pending:
        order.status = OrderStatus.cancelled
        if order.decision_id:
            decision = (
                db.query(DecisionRecord)
                .filter(DecisionRecord.id == order.decision_id)
                .first()
            )
            if decision is not None:
                decision.outcome_status = OutcomeStatus.expired
                decision.risk_flags = {
                    **(decision.risk_flags or {}),
                    "expired_reason": reason,
                    "expired_at_utc": stamp.isoformat(),
                }
        expired.append(
            {
                "order_id": str(order.id),
                "status": OrderStatus.cancelled.value,
                "expired": True,
                "reason": reason,
                "asset_id": order.asset_id,
                "side": order.side.value,
                "quantity": float(order.quantity),
            }
        )

    if expired:
        from app.services import emit_event

        emit_event(
            db,
            entity_type="paper_portfolio",
            entity_id=portfolio.id,
            event_type="paper_orders_expired",
            actor_id=actor_id,
            actor_type="system",
            payload={
                "reason": reason,
                "expired_at_utc": stamp.isoformat(),
                "orders": [e["order_id"] for e in expired],
                "cash_moved": False,
                "fill_created": False,
            },
        )
    db.flush()
    return expired


def execution_view(
    db: Session,
    *,
    order: PaperOrder,
    portfolio: PaperPortfolio,
    instrument_id: str,
    provider_id: Optional[str],
    start_date: str,
    end_date: str,
    market_clock_date: Optional[str] = None,
) -> Dict[str, Any]:
    """What the user is told about one order's place in the lifecycle.

    ``pending`` must never look like a holding, so the view names the window the
    order is waiting for — or says there is none — instead of leaving the user to
    infer it from a missing position.

    ``market_clock_date`` is how far the bound replay has actually walked. A
    pending order left behind by that clock (paused account, a bar with no quote)
    is not waiting for a bar the market has already traded past; it waits for the
    next one.
    """
    filled_quantity = round(
        sum(float(fill.quantity) for fill in order.fills), 8
    )
    view: Dict[str, Any] = {
        "state": order.status.value,
        "decided_on_cutoff": order.data_cutoff_at,
        "order_created_at_utc": order.created_at.isoformat() if order.created_at else None,
        "filled_quantity": filled_quantity,
        "requested_quantity": float(order.quantity),
        "fill_count": len(order.fills),
        "settlement_policy": SETTLEMENT_POLICY_LABEL,
        "blockers": [],
    }
    if order.status != OrderStatus.pending:
        fills = [fill for fill in order.fills]
        if fills:
            last = fills[-1]
            view["executed"] = {
                "execution_date": last.execution_date,
                "bar_timestamp": last.bar_timestamp.isoformat() if last.bar_timestamp else None,
                "settlement_date": settle_date_after(
                    db,
                    instrument_id=instrument_id,
                    provider_id=provider_id,
                    execution_date=last.execution_date,
                ),
            }
        else:
            view["not_executed_reason"] = order.risk_check_status.value if order.risk_check_status else None
        return view

    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        view["blockers"].append(
            BLOCKER_PORTFOLIO_RETIRED
            if portfolio.status == PortfolioStatus.retired
            else BLOCKER_PORTFOLIO_COMPLETED
        )
        view["waiting"] = {
            "reason": (
                f"The account is {portfolio.status.value}. Retirement is terminal, so a pending "
                "order is not executed and stays in the journal until it is cancelled."
            )
        }
        return view

    after_date = order.data_cutoff_at
    if market_clock_date and (after_date is None or market_clock_date > after_date):
        after_date = market_clock_date

    window = next_execution_window(
        db,
        instrument_id=instrument_id,
        provider_id=provider_id,
        start_date=start_date,
        end_date=end_date,
        after_date=after_date,
    )
    if window is None:
        view["blockers"].append(BLOCKER_NO_QUOTE)
        view["waiting"] = {
            "reason": "The bound dataset has no bar after the decision basis, so this order cannot "
            "fill and is settled as expired when the session is reviewed, not silently dropped."
        }
    else:
        view["next_window"] = {
            "date": window["date"],
            "open": window["open"],
            "bar_timestamp": window["bar_timestamp"].isoformat() if window["bar_timestamp"] else None,
        }
        if portfolio.status == PortfolioStatus.paused and order.side == OrderSide.BUY:
            view["blockers"].append(BLOCKER_PORTFOLIO_PAUSED)
    return view
