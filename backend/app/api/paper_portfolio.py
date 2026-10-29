from datetime import datetime, timezone
import csv
import io
import uuid
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
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
from app.auth.api_keys import check_api_key_scope
from app.auth.rate_limit import api_key_rate_limit_dependency
from app.auth.safety import safety_control
from app.config import get_settings
from app.models.kill_switch import KillSwitchScope
from app.models.simulation import SimulationSession
from app.api.schemas import (
    PaperPortfolioCreate,
    PaperPortfolioResponse,
    PaperPositionResponse,
    PaperOrderCreate,
    PaperOrderResponse,
    PaperOrderLifecycleResponse,
    PaperFillResponse,
    PaperValuationBasis,
    DecisionRecordCreate,
    DecisionRecordResponse,
    OutcomeEvaluationCreate,
    OutcomeEvaluationResponse,
    OverrideRequestCreate,
)
from app.data.demo_data import get_demo_ohlcv, get_date_range
from app.services import emit_event
from app.services.paper_execution import PaperExecutionError, book_fill
from app.services.risk_policy import (
    RiskInputs,
    count_entries_on_utc_date,
    evaluate_risk,
    value_open_positions,
)
from app.services.order_lifecycle import (
    LifecycleError,
    SETTLEMENT_POLICY_LABEL,
    cancel_order,
    execution_view,
    settle_date_after,
)

_settings = get_settings()

router = APIRouter(
    prefix="/paper-portfolio",
    tags=["paper-portfolio"],
    dependencies=[
        Depends(
            api_key_rate_limit_dependency(
                max_requests=_settings.api_key_rate_limit_requests,
                window_seconds=_settings.api_key_rate_limit_window_seconds,
            )
        )
    ],
)

# Shared by the fill path and the valuation path. Both must use the same rate,
# otherwise realised/unrealized P&L cannot reconcile with the cash movement.
DEFAULT_COMMISSION_RATE = 0.001
DEFAULT_SLIPPAGE_RATE = 0.001


def _get_portfolio_with_ownership(
    portfolio_id: UUID, db: Session, current_user: User
) -> PaperPortfolio:
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return portfolio


def _lock_portfolio(
    portfolio_id: UUID, db: Session, current_user: User
) -> PaperPortfolio:
    """Re-read the portfolio under a row lock so cash/position updates serialize.

    ``populate_existing()`` is required because SQLAlchemy would otherwise hand
    back the already-loaded instance without refreshing it under the lock.
    """
    portfolio = (
        db.query(PaperPortfolio)
        .populate_existing()
        .filter(PaperPortfolio.id == portfolio_id)
        .with_for_update()
        .first()
    )
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return portfolio


def _matches_request(existing: PaperOrder, payload: PaperOrderCreate) -> bool:
    return (
        existing.asset_id == payload.asset_id
        and existing.side == OrderSide(payload.side)
        and float(existing.quantity) == float(payload.quantity)
        and existing.order_type == OrderType(payload.order_type)
    )


def _latest_available_bar(asset_id: str) -> Optional[dict]:
    """Return the newest bar actually present for an asset, or None.

    The date comes from the bar itself, never from the wall clock, so a fill can
    never be stamped with a session that has no data behind it.
    """
    date_range = get_date_range()
    ohlcv_data = get_demo_ohlcv([asset_id], date_range["min"], date_range["max"])
    ohlcv = ohlcv_data.get(asset_id)
    if ohlcv is None or ohlcv.empty:
        return None
    row = ohlcv.iloc[-1]
    return {
        "date": row["date"].strftime("%Y-%m-%d"),
        "open": float(row["open"]),
        "close": float(row["close"]),
    }


def _mark_prices(asset_ids: Iterable[str]) -> Dict[str, dict]:
    """Newest bar per asset; an asset with no data is simply absent."""
    marks: Dict[str, dict] = {}
    for asset_id in {a for a in asset_ids if a}:
        bar = _latest_available_bar(asset_id)
        if bar is not None:
            marks[asset_id] = bar
    return marks


def _unrealized_pnl(
    position: PaperPosition,
    marks: Dict[str, dict],
    commission_rate: float = DEFAULT_COMMISSION_RATE,
) -> Optional[float]:
    """Mark-to-market P&L for an open position, or None when it cannot be marked.

    A missing mark price is reported as None, never as 0.0: rendering an
    unavailable valuation as zero states a number the system does not have.

    The entry commission already paid on these shares is deducted, because cash
    was debited for it at the open. Leaving it out would overstate the position
    and break the identity realised + unrealized == movement in cash plus
    holdings.
    """
    if position.status != PositionStatus.open:
        return None
    bar = marks.get(position.asset_id)
    if bar is None:
        return None
    average_entry = float(position.average_entry_price)
    quantity = float(position.quantity)
    gross = (bar["close"] - average_entry) * quantity
    return round(gross - average_entry * quantity * commission_rate, 4)


def _portfolio_response(portfolio: PaperPortfolio) -> PaperPortfolioResponse:
    """Serialise a portfolio with its unrealized P&L marked from real bar data."""
    open_positions = [p for p in portfolio.positions if p.status == PositionStatus.open]
    marks = _mark_prices(p.asset_id for p in open_positions)
    values = [_unrealized_pnl(p, marks) for p in open_positions]
    unpriced = sorted({
        p.asset_id for p, value in zip(open_positions, values) if value is None
    })
    total_unrealized = None if unpriced else round(sum(v for v in values if v is not None), 4)
    as_of = sorted({bar["date"] for bar in marks.values()})

    note = (
        "估值使用內建合成 demo 收盤價，非即時行情；已實現與未實現損益均已扣除佣金，"
        "滑價已含在成交價內；無可用價格的標的不計入未實現損益。"
        if open_positions
        else "目前沒有持有部位，未實現損益為 0。"
    )
    if unpriced:
        note = f"以下標的沒有可用價格，未實現損益無法計算：{', '.join(unpriced)}。" + note

    return PaperPortfolioResponse.model_validate(portfolio).model_copy(
        update={
            "total_unrealized_pnl": total_unrealized,
            "valuation": PaperValuationBasis(
                mode="synthetic_fixture",
                mode_label_zh="合成數據",
                provider="Demo (Synthetic)",
                data_as_of=(as_of[-1] if as_of else None),
                unpriced_assets=unpriced,
                note=note,
            ),
        }
    )


def _evaluate_direct_order(
    db: Session,
    portfolio: PaperPortfolio,
    payload: PaperOrderCreate,
    bar: Optional[dict],
):
    """Ask the shared admission policy before this lane creates anything.

    The direct lane prices off the newest stored bar, which is also the bar it
    fills at, so the position cap is valued on the price the trade is about to
    pay. Open positions the fixture cannot mark are passed through as unpriced —
    never valued at zero — so a binding cap fails closed instead of passing.

    The budget is scoped to the UTC submission date: this lane is not a replay,
    so it has no decision bar to belong to.
    """
    open_positions = [
        p for p in portfolio.positions if p.status == PositionStatus.open
    ]
    marks = _mark_prices(p.asset_id for p in open_positions)
    open_value, unpriced = value_open_positions(
        db,
        portfolio,
        price_by_asset={asset: mark["close"] for asset, mark in marks.items()},
    )
    held = sum(
        float(p.quantity or 0)
        for p in open_positions
        if p.asset_id == payload.asset_id
    )
    decision_day = datetime.now(timezone.utc).date()
    return evaluate_risk(
        RiskInputs(
            lane="direct",
            side=payload.side,
            asset_id=payload.asset_id,
            quantity=float(payload.quantity),
            portfolio_status=portfolio.status.value,
            portfolio_cash=float(portfolio.current_virtual_cash),
            held_quantity=held,
            price=float(bar["close"]) if bar is not None else None,
            open_positions_value=None if unpriced else open_value,
            unpriced_assets=unpriced,
            entries_on_date=count_entries_on_utc_date(db, portfolio, day=decision_day),
            decision_date=decision_day.isoformat(),
        )
    )


class _FillRefused(HTTPException):
    """A fill rejected before it could be booked, carrying its reason code."""

    def __init__(self, detail: str, code: str):
        super().__init__(status_code=400, detail=detail)
        self.code = code


def _simulate_fill(
    order: PaperOrder,
    portfolio: PaperPortfolio,
    bar: dict,
    db: Session,
    commission_rate: float = DEFAULT_COMMISSION_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
) -> PaperFill:
    """Fill a manual order at the bar's open, less or plus slippage.

    Only the pricing lives here. The cash, position and fill-row bookkeeping is
    shared with rule-triggered execution so one trade cannot be booked two ways.
    """
    today_open = float(bar["open"])
    quantity = float(order.quantity)

    if order.side == OrderSide.BUY:
        observed_price = today_open
        exec_price = round(today_open * (1 + slippage_rate), 4)
        fill_reason = "simulated_buy"
    else:
        observed_price = today_open
        exec_price = round(today_open * (1 - slippage_rate), 4)
        fill_reason = "simulated_sell"

    try:
        return book_fill(
            db,
            order=order,
            portfolio=portfolio,
            execution_date=bar["date"],
            observed_price=observed_price,
            execution_price=exec_price,
            quantity=quantity,
            commission_rate=commission_rate,
            fill_reason=fill_reason,
        )
    except PaperExecutionError as exc:
        raise _FillRefused(exc.message, exc.code)


@router.post("", response_model=PaperPortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    payload: PaperPortfolioCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    if payload.simulation_session_id is not None:
        # The link is what lets a replay's rules trade this account, so a session
        # that is missing or belongs to someone else must be refused here rather
        # than stored as a link that silently never executes.
        linked_session = db.query(SimulationSession).filter(
            SimulationSession.id == payload.simulation_session_id,
            SimulationSession.owner_id == current_user.id,
        ).first()
        if linked_session is None:
            raise HTTPException(
                status_code=404, detail="Simulation session not found for this user"
            )

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
    return _portfolio_response(portfolio)


@router.get("", response_model=List[PaperPortfolioResponse])
def list_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolios = db.query(PaperPortfolio).filter(PaperPortfolio.owner_id == current_user.id).all()
    return [_portfolio_response(p) for p in portfolios]


@router.get("/{portfolio_id}", response_model=PaperPortfolioResponse)
def get_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _portfolio_response(_get_portfolio_with_ownership(portfolio_id, db, current_user))


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
    positions = query.all()
    marks = _mark_prices(p.asset_id for p in positions)
    return [
        PaperPositionResponse.model_validate(p).model_copy(
            update={"unrealized_pnl": _unrealized_pnl(p, marks)}
        )
        for p in positions
    ]


@router.post("/{portfolio_id}/orders", response_model=PaperOrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    portfolio_id: UUID,
    payload: PaperOrderCreate,
    request: Request,
    route_response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    _safety=Depends(safety_control(KillSwitchScope.execution, "execution")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    # The simulator prices every fill off the market bar and accepts no client
    # limit price, so a limit order can only ever be a market order in disguise.
    if payload.order_type != "market":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported order_type '{payload.order_type}': the paper executor takes no limit "
                "price and would fill it at the bar open. Submit order_type='market'."
            ),
        )

    scopes = getattr(request.state, "api_key_scopes", None)
    actor_type = "api_key" if scopes is not None else "human"

    # Serialize cash/position changes for this portfolio before evaluating limits.
    portfolio = _lock_portfolio(portfolio_id, db, current_user)

    if payload.idempotency_key:
        existing = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.portfolio_id == portfolio.id,
                PaperOrder.idempotency_key == payload.idempotency_key,
            )
            .first()
        )
        if existing:
            if not _matches_request(existing, payload):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "idempotency_key_conflict",
                        "idempotency_key": payload.idempotency_key,
                        "existing_order_id": str(existing.id),
                        "message": "idempotency_key was already used with a different order",
                    },
                )
            route_response.status_code = status.HTTP_200_OK
            return existing

    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(status_code=400, detail=f"Cannot create order for '{portfolio.status.value}' portfolio")

    # The bar is read before the policy so the cap is valued on the price this
    # order is about to pay, and a cap that cannot be priced is refused rather
    # than labelled passed. A missing bar is still answered by the price-data
    # error below when no binding cap needed it.
    bar = _latest_available_bar(payload.asset_id)
    risk = _evaluate_direct_order(db, portfolio, payload, bar)
    if not risk.allowed:
        rejected = PaperOrder(
            portfolio_id=portfolio.id,
            asset_id=payload.asset_id,
            side=OrderSide(payload.side),
            quantity=payload.quantity,
            order_type=OrderType(payload.order_type),
            status=OrderStatus.rejected,
            rationale=payload.rationale,
            risk_check_status=RiskCheckStatus.failed,
            idempotency_key=payload.idempotency_key,
            actor_type=actor_type,
            data_cutoff_at=bar["date"] if bar is not None else None,
        )
        db.add(rejected)
        db.flush()
        emit_event(
            db,
            entity_type="paper_portfolio", entity_id=portfolio.id,
            event_type="order_rejected", actor_id=current_user.id, actor_type=actor_type,
            data_cutoff_at=bar["date"] if bar is not None else None,
            payload={
                # Identity of the rejected record itself, so the refusal is
                # traceable without guessing which order it belonged to.
                "order_id": str(rejected.id),
                "decision_id": None,
                "idempotency_key": payload.idempotency_key,
                "rejection_code": risk.code,
                "asset_id": payload.asset_id,
                "side": payload.side,
                "quantity": payload.quantity,
                "actor_type": actor_type,
                "risk_check": risk.as_dict(),
            },
        )
        db.commit()
        db.refresh(rejected)
        raise HTTPException(
            status_code=400,
            detail=f"Risk check failed [{risk.code}]: {risk.message}",
        )

    if bar is None:
        raise HTTPException(
            status_code=400,
            detail=f"No price data available for {payload.asset_id} (data mode: demo fixture)",
        )

    decision = DecisionRecord(
        owner_id=current_user.id,
        paper_portfolio_id=portfolio.id,
        actor_type=(
            PaperDecisionActorType.human if actor_type == "human" else PaperDecisionActorType.system
        ),
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
        idempotency_key=payload.idempotency_key,
        actor_type=actor_type,
        data_cutoff_at=bar["date"],
    )
    db.add(order)
    db.flush()

    try:
        _simulate_fill(order, portfolio, bar, db)
    except _FillRefused as refusal:
        # The fill could not be paid for, so the order/decision rows are rolled
        # back with the cash movement. The refusal still has to be durable and
        # attributable, so it is journaled as its own committed event under an
        # operation identity rather than disappearing with the rollback.
        operation_id = str(uuid.uuid4())
        portfolio_id_snapshot = str(portfolio.id)
        bar_date = bar["date"]
        db.rollback()
        emit_event(
            db,
            entity_type="paper_portfolio", entity_id=portfolio_id_snapshot,
            event_type="order_rejected_before_persist",
            actor_id=current_user.id, actor_type=actor_type,
            data_cutoff_at=bar_date,
            payload={
                "operation_id": operation_id,
                "order_id": None,
                "decision_id": None,
                "asset_id": payload.asset_id,
                "side": payload.side,
                "quantity": payload.quantity,
                "reason": refusal.detail,
                "reason_code": refusal.code,
                "actor_type": actor_type,
                "execution_date": bar_date,
                "idempotency_key": payload.idempotency_key,
            },
        )
        db.commit()
        raise HTTPException(status_code=refusal.status_code, detail=refusal.detail)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="order_created", actor_id=current_user.id, actor_type=actor_type,
        data_cutoff_at=bar["date"],
        payload={
            "order_id": str(order.id),
            "asset_id": payload.asset_id,
            "side": payload.side,
            "quantity": payload.quantity,
            "actor_type": actor_type,
            "execution_date": bar["date"],
            "idempotency_key": payload.idempotency_key,
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


def _order_lifecycle_view(db: Session, portfolio: PaperPortfolio, order: PaperOrder) -> Dict[str, Any]:
    """Where this order stands, read against the dataset its session is bound to.

    A portfolio bound to a replay has a real market clock, so the view names the
    window the order waits for. A portfolio without one has no clock to read, and
    the view says so rather than guessing a date.
    """
    session = None
    if portfolio.simulation_session_id:
        session = (
            db.query(SimulationSession)
            .filter(SimulationSession.id == portfolio.simulation_session_id)
            .first()
        )
    if session is not None and session.experiment_version is not None:
        spec = session.experiment_version.strategy_spec or {}
        instrument_id = (spec.get("asset_ids") or [order.asset_id])[0]
        return execution_view(
            db,
            order=order,
            portfolio=portfolio,
            instrument_id=instrument_id,
            provider_id=session.provider_id,
            start_date=spec.get("start_date") or order.data_cutoff_at or "",
            end_date=spec.get("end_date") or "9999-12-31",
            market_clock_date=session.current_snapshot_date,
        )
    return execution_view(
        db,
        order=order,
        portfolio=portfolio,
        instrument_id=order.asset_id,
        provider_id=None,
        start_date=order.data_cutoff_at or "",
        end_date="9999-12-31",
    )


@router.post(
    "/{portfolio_id}/orders/{order_id}/cancel",
    response_model=PaperOrderLifecycleResponse,
)
def cancel_order_route(
    portfolio_id: UUID,
    order_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Withdraw a pending order before the market reaches it.

    Only ``pending`` can be cancelled: an executed order already moved cash and
    settled, so reversing it means a counter-order, not erasing a trade. The
    execution kill switch deliberately does not gate this route — cancelling
    removes exposure, which is the opposite of what a kill switch stops.
    """
    portfolio = _lock_portfolio(portfolio_id, db, current_user)
    order = db.query(PaperOrder).filter(
        PaperOrder.id == order_id, PaperOrder.portfolio_id == portfolio.id
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        cancel_order(
            db,
            portfolio=portfolio,
            order=order,
            actor_id=current_user.id,
            reason="user_cancelled",
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    db.commit()
    db.refresh(order)
    return PaperOrderLifecycleResponse(
        order=PaperOrderResponse.model_validate(order),
        execution=_order_lifecycle_view(db, portfolio, order),
    )


@router.post("/{portfolio_id}/decisions", response_model=DecisionRecordResponse, status_code=status.HTTP_201_CREATED)
def create_decision(
    portfolio_id: UUID,
    payload: DecisionRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    if payload.decision_type in ("entry", "exit"):
        if not payload.rationale:
            raise HTTPException(status_code=400, detail="Rationale required for entry/exit decisions")
        if not payload.expected_outcome:
            raise HTTPException(status_code=400, detail="Expected outcome required for entry/exit decisions")

    decision = DecisionRecord(
        owner_id=current_user.id,
        paper_portfolio_id=portfolio.id,
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
    # Rows recorded before paper_portfolio_id existed cannot be attributed; they
    # stay visible rather than silently vanishing from the journal.
    return db.query(DecisionRecord).filter(
        DecisionRecord.owner_id == current_user.id,
        or_(
            DecisionRecord.paper_portfolio_id == portfolio.id,
            DecisionRecord.paper_portfolio_id.is_(None),
        ),
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
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    decision = db.query(DecisionRecord).filter(
        DecisionRecord.id == decision_id,
        DecisionRecord.owner_id == current_user.id,
        or_(
            DecisionRecord.paper_portfolio_id == portfolio.id,
            DecisionRecord.paper_portfolio_id.is_(None),
        ),
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
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    decision = DecisionRecord(
        owner_id=current_user.id,
        paper_portfolio_id=portfolio.id,
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
    _scope_check=Depends(check_api_key_scope("write")),
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
    return _portfolio_response(portfolio)


@router.post("/{portfolio_id}/resume", response_model=PaperPortfolioResponse)
def resume_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
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
    return _portfolio_response(portfolio)


@router.post("/{portfolio_id}/retire", response_model=PaperPortfolioResponse)
def retire_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{portfolio.status.value}' portfolio")

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
    return _portfolio_response(portfolio)


# ── Trade-journal export (CSV + JSON) ──────────────────────────────────
#
# One row per executed fill, plus one row per order that never filled (so
# pending/cancelled/rejected orders stay visible in the journal instead of
# silently dropping). Column order is the contract the UI download and the
# CSV parser tests both read.

JOURNAL_COLUMNS = [
    "record_type",
    "record_classification",
    "portfolio_id",
    "currency",
    "order_id",
    "fill_id",
    "decision_id",
    "asset_id",
    "side",
    "order_type",
    "order_status",
    "actor_type",
    "decision_type",
    "outcome_status",
    "data_cutoff_at",
    "execution_date",
    "bar_timestamp",
    "settlement_date",
    "settlement_policy",
    "order_created_at_utc",
    "observed_price",
    "execution_price_before_costs",
    "slippage_amount",
    "commission_amount",
    "quantity",
    "gross_amount",
    "net_cash_change",
    "simulated_fill_reason",
    "rationale",
]

_JOURNAL_NUMERIC_COLUMNS = frozenset({
    "observed_price",
    "execution_price_before_costs",
    "slippage_amount",
    "commission_amount",
    "quantity",
    "gross_amount",
    "net_cash_change",
})

# Only a fill can answer these. An order that never filled carries them as null,
# so every export column exists in every JSON row — the same shape the CSV writes.
_FILL_ONLY_COLUMNS = [
    "fill_id",
    "execution_date",
    "bar_timestamp",
    "settlement_date",
    "observed_price",
    "execution_price_before_costs",
    "slippage_amount",
    "commission_amount",
    "quantity",
    "gross_amount",
    "net_cash_change",
    "simulated_fill_reason",
]

JOURNAL_COLUMN_SEMANTICS = {
    "record_type": "'fill' = an executed fill; 'order' = an order that never filled (pending/cancelled/rejected).",
    "record_classification": "'closed' when the order reached a terminal status (filled/cancelled/rejected); 'open' while the order is still pending.",
    "currency": "ISO-4217 code that applies to every monetary column in the row (the portfolio currency).",
    "data_cutoff_at": "Calendar date YYYY-MM-DD, no timezone: the replay/pricing cut-off, not an instant.",
    "execution_date": "Calendar date YYYY-MM-DD, no timezone: the simulated trade date of the fill.",
    "bar_timestamp": "ISO-8601 naive exchange-local instant of the intraday bar the fill executed on (e.g. 2024-03-04T09:50:00). Empty for daily fills, which carry no intraday bar time.",
    "settlement_date": "Calendar date YYYY-MM-DD, no timezone: the settlement session of the fill, read from the bound dataset (its next stored bar after the execution date). Empty = the dataset has no later bar, so settlement is unknown rather than zero.",
    "settlement_policy": "How settlement_date was derived, stated per row so an export can be read without the application.",
    "order_created_at_utc": "UTC timestamp, ISO-8601, naive (no offset suffix): the instant the order was created.",
    "observed_price": "Decimal in `currency`, reference price before slippage. Empty = null, never 0.",
    "execution_price_before_costs": "Decimal in `currency`, fill price before commission. Empty = null.",
    "slippage_amount": "Decimal in `currency`, absolute slippage cost. Empty = null.",
    "commission_amount": "Decimal in `currency`. Empty = null.",
    "quantity": "Decimal shares (may be fractional). Empty = null.",
    "gross_amount": "Decimal in `currency`, quantity * execution price before commission. Empty = null.",
    "net_cash_change": "Decimal in `currency`, signed cash movement (negative on BUY, positive on SELL). Empty = null.",
    "simulated_fill_reason": "Controlled vocabulary (e.g. rule_entry, rule_stop). Empty = null.",
    "rationale": "Free text; may contain commas, quotes, newlines and non-ASCII. Spreadsheet-formula guarded.",
    "_null": "An empty cell always means null; a real zero is written as 0.",
    "_encoding": "UTF-8, no BOM. RFC-4180 CRLF line endings, minimal quoting.",
    "_formula_guard": "Any text cell beginning with = + - @ TAB or CR is prefixed with a single quote so spreadsheets treat it as text, not a formula.",
}

# A cell starting with any of these could be read as a formula by Excel/Sheets.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _guard_formula(text: str) -> str:
    """Neutralise spreadsheet CSV-formula injection (OWASP).

    Only text cells pass through here; numeric columns are written as numbers.
    A leading single quote makes a spreadsheet show the literal text.
    """
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def _journal_cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if column in _JOURNAL_NUMERIC_COLUMNS:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    text = value.value if hasattr(value, "value") else str(value)
    return _guard_formula(text)


def _portfolio_dataset_provider(db: Session, portfolio: PaperPortfolio) -> Optional[str]:
    """Which provider's bars the bound session reads, when there is a bound session.

    Settlement is looked up in the same dataset a fill executed on. A portfolio
    with no bound session has no dataset to read, so settlement is unknown rather
    than guessed from an arbitrary source.
    """
    if not portfolio.simulation_session_id:
        return None
    session = (
        db.query(SimulationSession)
        .filter(SimulationSession.id == portfolio.simulation_session_id)
        .first()
    )
    return session.provider_id if session is not None else None


def _journal_rows(
    db: Session,
    portfolio: PaperPortfolio,
    orders: List[PaperOrder],
    decision_map: Dict[UUID, DecisionRecord],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    provider_id = _portfolio_dataset_provider(db, portfolio)
    for order in orders:
        decision = decision_map.get(order.decision_id) if order.decision_id else None
        classification = "open" if order.status == OrderStatus.pending else "closed"
        base = {
            "record_classification": classification,
            "portfolio_id": str(portfolio.id),
            "currency": portfolio.currency,
            "order_id": str(order.id),
            "decision_id": str(order.decision_id) if order.decision_id else None,
            "asset_id": order.asset_id,
            "side": order.side,
            "order_type": order.order_type,
            "order_status": order.status,
            "actor_type": order.actor_type,
            "decision_type": decision.decision_type if decision else None,
            "outcome_status": decision.outcome_status if decision else None,
            "data_cutoff_at": order.data_cutoff_at,
            "order_created_at_utc": order.created_at.isoformat() if order.created_at else None,
            "rationale": order.rationale,
            "settlement_policy": SETTLEMENT_POLICY_LABEL,
        }
        fills = list(order.fills)
        if fills:
            for fill in fills:
                row = dict(base)
                # Settlement comes from the dataset the order belongs to. When it
                # has no later bar, settlement is unknown and stays null rather
                # than becoming a guessed calendar date.
                row["settlement_date"] = settle_date_after(
                    db,
                    instrument_id=order.asset_id,
                    provider_id=provider_id,
                    execution_date=fill.execution_date,
                )
                row.update({
                    "record_type": "fill",
                    "fill_id": str(fill.id),
                    "execution_date": fill.execution_date,
                    "bar_timestamp": fill.bar_timestamp.isoformat() if fill.bar_timestamp else None,
                    "observed_price": fill.observed_price,
                    "execution_price_before_costs": fill.execution_price_before_costs,
                    "slippage_amount": fill.slippage_amount,
                    "commission_amount": fill.commission_amount,
                    "quantity": fill.quantity,
                    "gross_amount": fill.gross_amount,
                    "net_cash_change": fill.net_cash_change,
                    "simulated_fill_reason": fill.simulated_fill_reason,
                })
                rows.append(row)
        else:
            row = dict(base)
            row["record_type"] = "order"
            for column in _FILL_ONLY_COLUMNS:
                row[column] = None
            rows.append(row)
    return rows


def _journal_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(JOURNAL_COLUMNS)
    for row in rows:
        writer.writerow([_journal_cell(col, row.get(col)) for col in JOURNAL_COLUMNS])
    return buf.getvalue()


@router.get("/{portfolio_id}/journal")
def export_trade_journal(
    portfolio_id: UUID,
    format: str = "csv",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Owner-authorized trade-journal export. There is no anonymous download.

    ``format=csv`` returns a text/csv attachment (the deliverable); ``format=json``
    returns the same rows plus explicit column semantics (the JSON package). Both
    are kept — CSV never replaces JSON.
    """
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'")

    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)

    orders = (
        db.query(PaperOrder)
        .filter(PaperOrder.portfolio_id == portfolio.id)
        .order_by(PaperOrder.created_at)
        .all()
    )
    decisions = (
        db.query(DecisionRecord)
        .filter(
            DecisionRecord.owner_id == current_user.id,
            or_(
                DecisionRecord.paper_portfolio_id == portfolio.id,
                DecisionRecord.paper_portfolio_id.is_(None),
            ),
        )
        .all()
    )
    decision_map = {d.id: d for d in decisions}
    rows = _journal_rows(db, portfolio, orders, decision_map)

    if format == "json":
        return {
            "portfolio_id": str(portfolio.id),
            "currency": portfolio.currency,
            "columns": JOURNAL_COLUMNS,
            "column_semantics": JOURNAL_COLUMN_SEMANTICS,
            "row_count": len(rows),
            "records": rows,
        }

    csv_text = _journal_csv(rows)
    filename = f"trade-journal-{portfolio.id}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
