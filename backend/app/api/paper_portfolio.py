from datetime import datetime, date, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.paper_portfolio import (
    PaperPortfolio,
    PortfolioStatus,
    PaperPosition,
    PositionStatus,
    PaperOrder,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskCheckStatus,
    PaperFill,
    DecisionRecord,
    PaperDecisionActorType,
    PaperDecisionType,
    ConfidenceLevel,
    OutcomeStatus,
    OutcomeEvaluation,
    HypothesisStatus,
)
from app.auth.jwt import get_current_user
from app.api.schemas import (
    PaperPortfolioCreate,
    PaperPortfolioResponse,
    PaperPositionResponse,
    PaperOrderCreate,
    PaperOrderResponse,
    PaperFillResponse,
    DecisionRecordCreate,
    DecisionRecordResponse,
    OutcomeEvaluationCreate,
    OutcomeEvaluationResponse,
    OverrideRequestCreate,
    RiskCheckConfig,
)
from app.data.demo_data import get_demo_ohlcv, get_trade_dates, get_date_range
from app.services import emit_event

router = APIRouter(prefix="/paper-portfolio", tags=["paper-portfolio"])

DEFAULT_RISK_CONFIG = RiskCheckConfig()


def _get_portfolio_with_ownership(
    portfolio_id: UUID, db: Session, current_user: User
) -> PaperPortfolio:
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return portfolio


def _check_risk_limits(
    portfolio: PaperPortfolio,
    order: PaperOrderCreate,
    db: Session,
    config: RiskCheckConfig = DEFAULT_RISK_CONFIG,
) -> tuple[bool, Optional[str]]:
    if portfolio.status == PortfolioStatus.paused:
        return False, "Portfolio is paused"

    if order.asset_id not in config.allowed_assets:
        return False, f"Asset {order.asset_id} not in allowed list"

    if order.side == "SELL":
        open_positions = [
            p for p in portfolio.positions if p.status == PositionStatus.open and p.asset_id == order.asset_id
        ]
        total_held = sum(p.quantity for p in open_positions)
        if order.quantity > total_held:
            return False, f"Cannot sell {order.quantity} — only hold {total_held}"

    today_str = date.today().isoformat()
    today_orders = [
        o for o in portfolio.orders
        if o.created_at.date().isoformat() == today_str and o.status != OrderStatus.rejected
    ]
    if len(today_orders) >= config.max_daily_orders:
        return False, f"Daily order limit reached ({config.max_daily_orders})"

    if order.side == "BUY":
        date_range = get_date_range()
        ohlcv_data = get_demo_ohlcv([order.asset_id], date_range["min"], date_range["max"])
        if order.asset_id in ohlcv_data and not ohlcv_data[order.asset_id].empty:
            latest_price = ohlcv_data[order.asset_id].iloc[-1]["close"]
            order_value = order.quantity * latest_price
            portfolio_value = portfolio.current_virtual_cash + sum(
                p.quantity * latest_price
                for p in portfolio.positions
                if p.status == PositionStatus.open
            )
            if portfolio_value > 0 and (order_value / portfolio_value) > config.max_position_pct:
                return False, f"Position would exceed {config.max_position_pct*100}% of portfolio"

    return True, None


def _simulate_fill(
    order: PaperOrder,
    portfolio: PaperPortfolio,
    execution_date: str,
    db: Session,
    commission_rate: float = 0.001,
    slippage_rate: float = 0.001,
) -> PaperFill:
    date_range = get_date_range()
    ohlcv_data = get_demo_ohlcv([order.asset_id], date_range["min"], execution_date)
    if order.asset_id not in ohlcv_data or ohlcv_data[order.asset_id].empty:
        raise HTTPException(status_code=400, detail=f"No price data for {order.asset_id}")

    ohlcv = ohlcv_data[order.asset_id]
    today_open = ohlcv.iloc[-1]["open"]

    if order.side == OrderSide.BUY:
        observed_price = today_open
        exec_price = round(today_open * (1 + slippage_rate), 4)
        gross = round(order.quantity * exec_price, 4)
        commission = round(gross * commission_rate, 4)
        net_cash_change = -(gross + commission)

        if portfolio.current_virtual_cash + net_cash_change < 0:
            raise HTTPException(status_code=400, detail="Insufficient cash for order")

        portfolio.current_virtual_cash = round(portfolio.current_virtual_cash + net_cash_change, 4)

        open_pos = next(
            (p for p in portfolio.positions if p.asset_id == order.asset_id and p.status == PositionStatus.open),
            None,
        )
        if open_pos:
            total_qty = open_pos.quantity + order.quantity
            open_pos.average_entry_price = round(
                (open_pos.average_entry_price * open_pos.quantity + exec_price * order.quantity) / total_qty, 4
            )
            open_pos.quantity = total_qty
        else:
            new_pos = PaperPosition(
                portfolio_id=portfolio.id,
                asset_id=order.asset_id,
                quantity=order.quantity,
                average_entry_price=exec_price,
                status=PositionStatus.open,
            )
            db.add(new_pos)

        fill_reason = "simulated_buy"
    else:
        observed_price = today_open
        exec_price = round(today_open * (1 - slippage_rate), 4)
        gross = round(order.quantity * exec_price, 4)
        commission = round(gross * commission_rate, 4)
        net_cash_change = gross - commission
        portfolio.current_virtual_cash = round(portfolio.current_virtual_cash + net_cash_change, 4)

        open_pos = next(
            (p for p in portfolio.positions if p.asset_id == order.asset_id and p.status == PositionStatus.open),
            None,
        )
        if open_pos:
            if order.quantity >= open_pos.quantity:
                pnl = round((exec_price - open_pos.average_entry_price) * open_pos.quantity, 4)
                open_pos.realized_pnl = pnl
                open_pos.status = PositionStatus.closed
                open_pos.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                open_pos.quantity = 0
            else:
                open_pos.quantity -= order.quantity

        fill_reason = "simulated_sell"

    slippage_amount = round(abs(exec_price - observed_price) * order.quantity, 4)

    fill = PaperFill(
        paper_order_id=order.id,
        execution_date=execution_date,
        observed_price=round(observed_price, 4),
        execution_price_before_costs=exec_price,
        slippage_amount=slippage_amount,
        commission_amount=commission,
        quantity=order.quantity,
        gross_amount=gross,
        net_cash_change=net_cash_change,
        simulated_fill_reason=fill_reason,
    )
    db.add(fill)

    order.status = OrderStatus.filled
    return fill


@router.post("", response_model=PaperPortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PaperPortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = PaperPortfolio(
        owner_id=current_user.id,
        simulation_session_id=payload.simulation_session_id,
        currency=payload.currency,
        initial_virtual_cash=payload.initial_virtual_cash,
        current_virtual_cash=payload.initial_virtual_cash,
        status=PortfolioStatus.active,
    )
    db.add(portfolio)
    db.flush()

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_created", actor_id=current_user.id,
        payload={"initial_virtual_cash": payload.initial_virtual_cash},
    )

    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.get("", response_model=List[PaperPortfolioResponse])
def list_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(PaperPortfolio).filter(PaperPortfolio.owner_id == current_user.id).all()


@router.get("/{portfolio_id}", response_model=PaperPortfolioResponse)
def get_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_portfolio_with_ownership(portfolio_id, db, current_user)


@router.get("/{portfolio_id}/positions", response_model=List[PaperPositionResponse])
def list_positions(
    portfolio_id: UUID,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    query = db.query(PaperPosition).filter(PaperPosition.portfolio_id == portfolio.id)
    if status_filter:
        query = query.filter(PaperPosition.status == status_filter)
    return query.all()


@router.post("/{portfolio_id}/orders", response_model=PaperOrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    portfolio_id: UUID,
    payload: PaperOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(status_code=400, detail=f"Cannot create order for '{portfolio.status}' portfolio")

    passed, reason = _check_risk_limits(portfolio, payload, db)
    if not passed:
        order = PaperOrder(
            portfolio_id=portfolio.id,
            asset_id=payload.asset_id,
            side=OrderSide(payload.side),
            quantity=payload.quantity,
            order_type=OrderType(payload.order_type),
            status=OrderStatus.rejected,
            rationale=payload.rationale,
            risk_check_status=RiskCheckStatus.failed,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        raise HTTPException(status_code=400, detail=f"Risk check failed: {reason}")

    decision = DecisionRecord(
        owner_id=current_user.id,
        actor_type=PaperDecisionActorType.human,
        decision_type=PaperDecisionType.entry if payload.side == "BUY" else PaperDecisionType.exit,
        rationale=payload.rationale,
        expected_outcome=payload.expected_outcome,
        expected_timeframe_days=payload.expected_timeframe_days,
        self_reported_confidence=ConfidenceLevel(payload.self_reported_confidence or "not_provided"),
        falsification_condition=payload.falsification_condition,
        outcome_status=OutcomeStatus.pending,
    )
    db.add(decision)
    db.flush()

    order = PaperOrder(
        portfolio_id=portfolio.id,
        decision_id=decision.id,
        asset_id=payload.asset_id,
        side=OrderSide(payload.side),
        quantity=payload.quantity,
        order_type=OrderType(payload.order_type),
        status=OrderStatus.pending,
        rationale=payload.rationale,
        risk_check_status=RiskCheckStatus.passed,
    )
    db.add(order)
    db.flush()

    trade_dates = get_trade_dates()
    today_str = date.today().isoformat()
    future_dates = [d for d in trade_dates if d >= today_str]
    exec_date = future_dates[0] if future_dates else today_str

    _simulate_fill(order, portfolio, exec_date, db)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="order_created", actor_id=current_user.id,
        payload={
            "order_id": str(order.id),
            "asset_id": payload.asset_id,
            "side": payload.side,
            "quantity": payload.quantity,
        },
    )

    db.commit()
    db.refresh(order)
    return order


@router.get("/{portfolio_id}/orders", response_model=List[PaperOrderResponse])
def list_orders(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    return portfolio.orders


@router.get("/{portfolio_id}/orders/{order_id}/fills", response_model=List[PaperFillResponse])
def list_fills(
    portfolio_id: UUID,
    order_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    order = db.query(PaperOrder).filter(
        PaperOrder.id == order_id, PaperOrder.portfolio_id == portfolio.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order.fills


@router.post("/{portfolio_id}/decisions", response_model=DecisionRecordResponse, status_code=status.HTTP_201_CREATED)
def create_decision(
    portfolio_id: UUID,
    payload: DecisionRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    if payload.decision_type in ("entry", "exit"):
        if not payload.rationale:
            raise HTTPException(status_code=400, detail="Rationale required for entry/exit decisions")
        if not payload.expected_outcome:
            raise HTTPException(status_code=400, detail="Expected outcome required for entry/exit decisions")

    decision = DecisionRecord(
        owner_id=current_user.id,
        simulation_session_id=payload.simulation_session_id,
        actor_type=PaperDecisionActorType(payload.actor_type),
        decision_type=PaperDecisionType(payload.decision_type),
        linked_experiment_version_id=payload.linked_experiment_version_id,
        rationale=payload.rationale,
        expected_outcome=payload.expected_outcome,
        expected_timeframe_days=payload.expected_timeframe_days,
        self_reported_confidence=ConfidenceLevel(payload.self_reported_confidence or "not_provided"),
        falsification_condition=payload.falsification_condition,
        risk_flags=payload.risk_flags,
        outcome_status=OutcomeStatus.pending,
    )
    db.add(decision)
    db.flush()

    emit_event(
        db,
        entity_type="decision_record", entity_id=decision.id,
        event_type="decision_recorded", actor_id=current_user.id,
        payload={
            "decision_type": payload.decision_type,
            "actor_type": payload.actor_type,
        },
    )

    db.commit()
    db.refresh(decision)
    return decision


@router.get("/{portfolio_id}/decisions", response_model=List[DecisionRecordResponse])
def list_decisions(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    return db.query(DecisionRecord).filter(
        DecisionRecord.owner_id == current_user.id
    ).order_by(DecisionRecord.decision_time.desc()).all()


@router.post(
    "/{portfolio_id}/decisions/{decision_id}/outcome",
    response_model=OutcomeEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_outcome_evaluation(
    portfolio_id: UUID,
    decision_id: UUID,
    payload: OutcomeEvaluationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    decision = db.query(DecisionRecord).filter(
        DecisionRecord.id == decision_id, DecisionRecord.owner_id == current_user.id
    ).first()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    if decision.outcome_status == OutcomeStatus.evaluated:
        raise HTTPException(status_code=400, detail="Decision already evaluated")

    evaluation = OutcomeEvaluation(
        decision_id=decision.id,
        actual_outcome_summary=payload.actual_outcome_summary,
        net_simulated_return_pct=payload.net_simulated_return_pct,
        max_drawdown_pct=payload.max_drawdown_pct,
        hypothesis_status=HypothesisStatus(payload.hypothesis_status) if payload.hypothesis_status else None,
        evaluation_method=payload.evaluation_method,
        user_reflection=payload.user_reflection,
        data_quality_flags=payload.data_quality_flags,
    )
    db.add(evaluation)

    decision.outcome_status = OutcomeStatus.evaluated

    emit_event(
        db,
        entity_type="decision_record", entity_id=decision.id,
        event_type="outcome_evaluated", actor_id=current_user.id,
        payload={"hypothesis_status": payload.hypothesis_status},
    )

    db.commit()
    db.refresh(evaluation)
    return evaluation


@router.post(
    "/{portfolio_id}/override-requests",
    response_model=DecisionRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_override_request(
    portfolio_id: UUID,
    payload: OverrideRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    decision = DecisionRecord(
        owner_id=current_user.id,
        actor_type=PaperDecisionActorType.human,
        decision_type=PaperDecisionType.override_request,
        rationale=f"Original: {payload.original_rationale}\nOverride reason: {payload.override_reason}",
        expected_outcome=payload.expected_outcome,
        expected_timeframe_days=payload.expected_timeframe_days,
        self_reported_confidence=ConfidenceLevel(payload.self_reported_confidence or "not_provided"),
        falsification_condition=payload.falsification_condition,
        risk_flags={"is_override": True, "outside_locked_strategy": True},
        outcome_status=OutcomeStatus.pending,
    )
    db.add(decision)
    db.flush()

    emit_event(
        db,
        entity_type="decision_record", entity_id=decision.id,
        event_type="override_requested", actor_id=current_user.id,
        payload={"outside_locked_strategy": True},
        rules_locked=True,
    )

    db.commit()
    db.refresh(decision)
    return decision


@router.post("/{portfolio_id}/pause", response_model=PaperPortfolioResponse)
def pause_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status != PortfolioStatus.active:
        raise HTTPException(status_code=400, detail="Can only pause an active portfolio")

    portfolio.status = PortfolioStatus.paused
    portfolio.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_paused", actor_id=current_user.id,
    )

    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.post("/{portfolio_id}/resume", response_model=PaperPortfolioResponse)
def resume_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status != PortfolioStatus.paused:
        raise HTTPException(status_code=400, detail="Can only resume a paused portfolio")

    portfolio.status = PortfolioStatus.active
    portfolio.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_resumed", actor_id=current_user.id,
    )

    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.post("/{portfolio_id}/retire", response_model=PaperPortfolioResponse)
def retire_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{portfolio.status}' portfolio")

    portfolio.status = PortfolioStatus.retired
    portfolio.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    portfolio.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_retired", actor_id=current_user.id,
        payload={"final_cash": portfolio.current_virtual_cash},
    )

    db.commit()
    db.refresh(portfolio)
    return portfolio
