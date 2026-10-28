"""One fill convention for manual paper orders and rule-triggered execution.

A manual order and a rule-triggered order differ in *which price* they fill at —
a manual order prices off the latest available bar, a rule exit prices off the
level the shared exit policy evidenced — but they must not differ in how a price
becomes a cash movement, a position change and a fill row. If they did, the
portfolio could not reconcile against its own journal, and the same trade would
book differently depending on who placed it.

So the callers supply observed and execution prices, ``app.engine.fill_math`` is
the only place that decides what those prices cost, and ``book_fill`` is the only
place that applies the answer to cash, positions and the fill ledger.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.engine.fill_math import fill_economics
from app.models.paper_portfolio import (
    DecisionRecord,
    OutcomeStatus,
    OrderSide,
    OrderStatus,
    PaperDecisionActorType,
    PaperDecisionType,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PortfolioStatus,
    PositionStatus,
    RiskCheckStatus,
)


class PaperExecutionError(Exception):
    """A fill that must not be booked. Carries the reason for the journal."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _open_position(portfolio: PaperPortfolio, asset_id: str) -> Optional[PaperPosition]:
    return next(
        (
            p
            for p in portfolio.positions
            if p.asset_id == asset_id and p.status == PositionStatus.open
        ),
        None,
    )


def book_fill(
    db: Session,
    *,
    order: PaperOrder,
    portfolio: PaperPortfolio,
    execution_date: str,
    observed_price: float,
    execution_price: float,
    quantity: float,
    commission_rate: float,
    fill_reason: str,
    bar_timestamp: Optional[datetime] = None,
) -> PaperFill:
    """Apply one fill to cash, position and the fill ledger, and mark it filled.

    ``execution_price`` already carries slippage; commission is charged on the
    gross at that price. Neither cost is applied twice.

    Raises ``PaperExecutionError`` rather than mutating anything when the fill
    cannot be honoured, so a rejected trade leaves no half-applied cash or
    position behind.
    """
    economics = fill_economics(
        order.side.value, quantity, observed_price, execution_price, commission_rate
    )
    gross = economics.gross_amount
    commission = economics.commission_amount
    net_cash_change = economics.net_cash_change

    if order.side == OrderSide.BUY:
        if float(portfolio.current_virtual_cash) + net_cash_change < 0:
            raise PaperExecutionError("Insufficient cash for order")

        portfolio.current_virtual_cash = round(
            float(portfolio.current_virtual_cash) + net_cash_change, 4
        )

        open_pos = _open_position(portfolio, order.asset_id)
        if open_pos:
            total_qty = float(open_pos.quantity) + quantity
            open_pos.average_entry_price = round(
                (float(open_pos.average_entry_price) * float(open_pos.quantity)
                 + execution_price * quantity)
                / total_qty,
                4,
            )
            open_pos.quantity = total_qty
        else:
            db.add(
                PaperPosition(
                    portfolio_id=portfolio.id,
                    asset_id=order.asset_id,
                    quantity=quantity,
                    average_entry_price=execution_price,
                    status=PositionStatus.open,
                )
            )
    else:
        open_pos = _open_position(portfolio, order.asset_id)
        held = float(open_pos.quantity) if open_pos else 0.0
        if quantity > held:
            # Selling shares the portfolio does not hold would credit cash that
            # came from nothing. The order-creation risk check rejects this too;
            # this guard is what stops any other caller from booking it.
            raise PaperExecutionError(
                f"Cannot sell {quantity} — only hold {held}"
            )

        portfolio.current_virtual_cash = round(
            float(portfolio.current_virtual_cash) + net_cash_change, 4
        )

        average_entry = float(open_pos.average_entry_price)
        # Cash was debited for commission on the entry leg and is debited again
        # here, so realised P&L must carry both or it cannot reconcile with the
        # cash movement. Pro-rating the entry leg onto the weighted average
        # price is exact: commission is a constant fraction of price x quantity,
        # so it averages the same way the price does.
        entry_commission = average_entry * quantity * commission_rate
        net_pnl = (execution_price - average_entry) * quantity - commission - entry_commission
        open_pos.realized_pnl = round(float(open_pos.realized_pnl or 0) + round(net_pnl, 4), 4)

        if quantity >= held:
            open_pos.status = PositionStatus.closed
            open_pos.closed_at = _utcnow()
            open_pos.quantity = 0.0
        else:
            open_pos.quantity = round(held - quantity, 8)

    fill = PaperFill(
        paper_order_id=order.id,
        execution_date=execution_date,
        observed_price=round(observed_price, 4),
        execution_price_before_costs=execution_price,
        slippage_amount=economics.slippage_amount,
        commission_amount=commission,
        quantity=quantity,
        gross_amount=gross,
        net_cash_change=net_cash_change,
        simulated_fill_reason=fill_reason,
        bar_timestamp=bar_timestamp,
    )
    db.add(fill)

    order.status = OrderStatus.filled
    return fill


def apply_rule_decision(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    session_id: Any,
    step_number: int,
    strategy_version_id: Any,
    result: Any,
    commission_rate: float,
    bar_timestamp: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Book one simulator step into the paper portfolio, or explain why not.

    Returns a summary dict for the audit payload, or None when the step produced
    no trade to book (no action, warm-up, or a blocked signal).

    Idempotent by (session, step): the key is derived from the replay position
    rather than supplied by a client, so re-advancing or retrying the same step
    cannot book a second fill for it.
    """
    detail = result.decision_detail or {}
    decision_type = getattr(result, "decision_type", None)
    decision_type = getattr(decision_type, "value", decision_type)

    if decision_type not in ("simulated_entry", "simulated_exit", "forced_exit"):
        return None

    side = detail.get("side")
    if side not in ("BUY", "SELL"):
        return None

    symbol = detail.get("symbol")
    quantity = detail.get("quantity")
    observed_price = detail.get("observed_price")
    execution_price = detail.get("execution_price")
    snapshot_date = getattr(result, "snapshot_date", None)
    rationale = detail.get("rationale") or ""
    exit_reason = detail.get("exit_reason")

    if not all(
        value is not None
        for value in (symbol, quantity, observed_price, execution_price, snapshot_date)
    ):
        # A trade the simulator did not fully price must not be guessed at.
        return _record_blocked(
            db,
            portfolio=portfolio,
            strategy_version_id=strategy_version_id,
            session_id=session_id,
            step_number=step_number,
            snapshot_date=snapshot_date,
            side=side,
            quantity=quantity,
            rationale=rationale,
            reason="decision_detail_incomplete",
        )

    is_entry = side == "BUY"

    # A paused portfolio takes no new positions. Exits still run: closing risk is
    # the one thing pausing should not prevent. A retired or completed portfolio
    # trades nothing at all.
    if portfolio.status == PortfolioStatus.paused and is_entry:
        return _record_blocked(
            db,
            portfolio=portfolio,
            strategy_version_id=strategy_version_id,
            session_id=session_id,
            step_number=step_number,
            snapshot_date=snapshot_date,
            side=side,
            quantity=quantity,
            rationale=rationale,
            reason="portfolio_paused",
        )
    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        return _record_blocked(
            db,
            portfolio=portfolio,
            strategy_version_id=strategy_version_id,
            session_id=session_id,
            step_number=step_number,
            snapshot_date=snapshot_date,
            side=side,
            quantity=quantity,
            rationale=rationale,
            reason=f"portfolio_{portfolio.status.value}",
        )

    idempotency_key = f"sim-{session_id}-step-{step_number}"
    existing = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing is not None:
        return {
            "order_id": str(existing.id),
            "status": getattr(existing.status, "value", existing.status),
            "duplicate": True,
        }

    decision = DecisionRecord(
        owner_id=portfolio.owner_id,
        paper_portfolio_id=portfolio.id,
        simulation_session_id=session_id,
        actor_type=PaperDecisionActorType.system,
        decision_type=PaperDecisionType.entry if is_entry else PaperDecisionType.exit,
        decision_time=_utcnow(),
        data_cutoff_at=snapshot_date,
        linked_experiment_version_id=strategy_version_id,
        rationale=rationale,
        outcome_status=OutcomeStatus.pending,
        risk_flags={
            "auto_execution": True,
            "rules_locked": True,
            "exit_reason": exit_reason,
        },
    )
    db.add(decision)
    db.flush()

    order = PaperOrder(
        portfolio_id=portfolio.id,
        decision_id=decision.id,
        asset_id=symbol,
        side=OrderSide(side),
        quantity=float(quantity),
        status=OrderStatus.pending,
        model_or_strategy_version_id=strategy_version_id,
        data_cutoff_at=snapshot_date,
        rationale=rationale,
        risk_check_status=RiskCheckStatus.passed,
        idempotency_key=idempotency_key,
        actor_type="system",
    )
    db.add(order)
    db.flush()

    try:
        fill = book_fill(
            db,
            order=order,
            portfolio=portfolio,
            execution_date=snapshot_date,
            observed_price=float(observed_price),
            execution_price=float(execution_price),
            quantity=float(quantity),
            commission_rate=commission_rate,
            fill_reason=f"rule_{exit_reason}" if exit_reason else "rule_entry",
            bar_timestamp=bar_timestamp,
        )
    except PaperExecutionError as exc:
        # The trade is refused, not partially applied: the order is marked
        # rejected and the journal records why, so a failed rule never leaves a
        # half-updated cash or position behind.
        order.status = OrderStatus.rejected
        order.risk_check_status = RiskCheckStatus.failed
        decision.risk_flags = {
            **(decision.risk_flags or {}),
            "blocked_reason": exc.message,
        }
        db.flush()
        return {
            "order_id": str(order.id),
            "status": OrderStatus.rejected.value,
            "blocked_reason": exc.message,
        }

    db.flush()
    return {
        "order_id": str(order.id),
        "fill_id": str(fill.id),
        "status": OrderStatus.filled.value,
        "side": side,
        "quantity": float(quantity),
        "execution_price": float(execution_price),
        "net_cash_change": float(fill.net_cash_change),
        "exit_reason": exit_reason,
        "cash_after": float(portfolio.current_virtual_cash),
    }


def _record_blocked(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    strategy_version_id: Any,
    session_id: Any,
    step_number: int,
    snapshot_date: Optional[str],
    side: str,
    quantity: Any,
    rationale: str,
    reason: str,
) -> Dict[str, Any]:
    """Journal a rule that fired but was not executed.

    A silently dropped signal would make the portfolio's journal disagree with
    the replay that produced it, so the refusal is recorded with its reason.
    """
    decision = DecisionRecord(
        owner_id=portfolio.owner_id,
        paper_portfolio_id=portfolio.id,
        simulation_session_id=session_id,
        actor_type=PaperDecisionActorType.system,
        decision_type=PaperDecisionType.entry if side == "BUY" else PaperDecisionType.exit,
        decision_time=_utcnow(),
        data_cutoff_at=snapshot_date,
        linked_experiment_version_id=strategy_version_id,
        rationale=rationale,
        outcome_status=OutcomeStatus.pending,
        risk_flags={
            "auto_execution": True,
            "rules_locked": True,
            "blocked_reason": reason,
            "quantity": quantity,
        },
    )
    db.add(decision)
    db.flush()
    return {"status": "not_executed", "blocked_reason": reason}
