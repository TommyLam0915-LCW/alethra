from datetime import date, datetime, timezone
import csv
import io
import uuid
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.database import get_db
from app.lib.uuid_spelling import dashed_text, stored_as
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
    decision_actor_type,
)
from app.models.paper_trading_run import PaperTradingRun
from app.auth.jwt import get_current_user
from app.auth.api_keys import check_api_key_scope
from app.auth.rate_limit import api_key_rate_limit_dependency
from app.auth.safety import safety_control
from app.auth.provenance import request_actor_provenance
from app.config import get_settings
from app.models.kill_switch import KillSwitchScope
from app.models.billing import UsageRecord
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession
from app.models.experiment_version import ExperimentVersion
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
from app.services.decision_lineage import validate_linked_inference
from app.services.strategy_versions import validate_strategy_version
from app.services.paper_execution import PaperExecutionError, book_fill
from app.services.skill_units import SkillUnitLimitReached, assert_slot_available
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
    if portfolio is None:
        # An account written by this product's own creation paths —
        # POST /paper-trading/create and the live daemon insert with raw SQL and
        # keep ``str(uuid4())``, the dashed 36-character form — is not found by
        # the filter above on the lane where UUID columns are text, because an
        # ORM identity key renders 32 characters with no dashes. Asking for the
        # dashed spelling loads the same row, and PostgreSQL resolves either
        # spelling through the indexed key above, so this only adds a second
        # query for a row the first one already could not name.
        portfolio = (
            db.query(PaperPortfolio)
            .filter(dashed_text(PaperPortfolio.id, portfolio_id))
            .first()
        )
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

    Deliberately a single indexed identity comparison, with no dashed retry:
    both callers go on to *write* this row, and on the lane where UUID columns
    are text an instance loaded by the dashed spelling cannot be written at all
    -- the UPDATE statement SQLAlchemy emits carries the 32-character identity
    key and matches nothing, raising ``StaleDataError``. Loading such a row a
    second way would therefore turn this ``Portfolio not found`` into a 500 for
    the same request.

    That gap no longer describes an account this product makes: the raw-SQL
    writers now bind the column's own UUID type (``app/lib/uuid_bind.py``), so a
    create-lane account is stored in the spelling this read already uses and the
    owner's manual order on it is measurable on the lane -- see
    ``tests/test_paper_create_engine_consistency.py``, P6. What is still true is
    the case this function refuses: a row already stored dashed predates that
    normalisation, answers 404 here rather than 500, and whether any deployed
    database holds one has not been measured, because the protected databases
    were not touched.
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
    as_of = row["date"].strftime("%Y-%m-%d")
    return {
        "date": as_of,
        "as_of": as_of,
        "source": "demo_synthetic_fixture",
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


def _bound_session(db: Session, portfolio: PaperPortfolio) -> Optional[SimulationSession]:
    if portfolio.simulation_session_id is None:
        return None
    return (
        db.query(SimulationSession)
        .filter(SimulationSession.id == portfolio.simulation_session_id)
        .first()
    )


def require_session_for_owner(db: Session, owner_id: UUID, session_id: UUID) -> None:
    """Refuse a replay session this owner may not name, before anything is written.

    ``simulation_sessions.id`` is a real foreign key on both ``paper_portfolios``
    and ``decision_records``, so an id nobody checked turns the insert into a
    database error on PostgreSQL. Stated once because two doors — opening an
    account and recording a decision — accept the same column, and a rule only
    one of them holds lets the same id be a link here and a 500 there.
    """
    named = (
        db.query(SimulationSession.id)
        .filter(
            SimulationSession.id == session_id,
            SimulationSession.owner_id == owner_id,
        )
        .first()
    )
    if named is None:
        raise HTTPException(
            status_code=404, detail="Simulation session not found for this user"
        )


def _session_cursor(session: SimulationSession):
    """The point in replay time a bound account may be valued at, or None."""
    if session.timeframe not in (None, "", "1d"):
        if session.current_bar_timestamp is None:
            return None
        return ("timestamp", session.current_bar_timestamp)
    if not session.current_snapshot_date:
        return None
    try:
        return ("date", date.fromisoformat(session.current_snapshot_date[:10]))
    except ValueError:
        return None


def _session_bound_marks(
    db: Session, portfolio: PaperPortfolio, asset_ids: Iterable[str]
) -> Dict[str, dict]:
    """Mark a bound account from the bars its session has already replayed.

    The cursor is the guarantee: marks are read at or before the point the
    replay reached, so a valuation can never see a future bar. A missing bar
    leaves the asset unpriced rather than falling back to the demo fixture — a
    bound account must not be valued from data its session never replayed.
    """
    session = _bound_session(db, portfolio)
    if session is None:
        return {}
    cursor = _session_cursor(session)
    if cursor is None:
        return {}

    kind, value = cursor
    marks: Dict[str, dict] = {}
    for asset_id in {a for a in asset_ids if a}:
        query = db.query(MarketDataBar).filter(MarketDataBar.instrument_id == asset_id)
        if session.provider_id:
            query = query.filter(MarketDataBar.provider_id == session.provider_id)
        if kind == "timestamp":
            query = query.filter(MarketDataBar.bar_timestamp <= value)
        else:
            query = query.filter(MarketDataBar.bar_date <= value)
        bar = query.order_by(MarketDataBar.bar_timestamp.desc()).first()
        if bar is None:
            continue
        marks[asset_id] = {
            "close": float(bar.close),
            "date": bar.bar_date.isoformat(),
            "as_of": bar.bar_timestamp.isoformat(),
            "source": "stored_market_data_bar",
        }
    return marks


def _entry_commission_per_share(
    db: Session, portfolio: PaperPortfolio, position: PaperPosition
) -> Optional[float]:
    """The entry commission a fill actually recorded for these shares.

    The session lane books at its locked spec's rate, which is often 0.0, while
    the direct lane uses the default. Reading the rate back from the fill keeps
    the mark tied to the money that actually left the account instead of
    assuming one rate for every portfolio.
    """
    row = (
        db.query(PaperFill.commission_amount, PaperFill.quantity)
        .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.asset_id == position.asset_id,
            PaperOrder.side == OrderSide.BUY,
        )
        .order_by(PaperOrder.created_at.desc(), PaperFill.bar_timestamp.desc())
        .first()
    )
    if row is None or not float(row.quantity):
        return None
    return float(row.commission_amount) / float(row.quantity)


def _unrealized_pnl(
    position: PaperPosition,
    marks: Dict[str, dict],
    commission_rate: float = DEFAULT_COMMISSION_RATE,
    entry_commission_per_share: Optional[float] = None,
) -> Optional[float]:
    """Mark-to-market P&L for an open position, or None when it cannot be marked.

    A missing mark price is reported as None, never as 0.0: rendering an
    unavailable valuation as zero states a number the system does not have.

    The entry commission already paid on these shares is deducted, because cash
    was debited for it at the open. Leaving it out would overstate the position
    and break the identity realised + unrealized == movement in cash plus
    holdings. ``entry_commission_per_share`` wins when the ledger has a fill to
    read it from; the rate is only a fallback.
    """
    if position.status != PositionStatus.open:
        return None
    bar = marks.get(position.asset_id)
    if bar is None:
        return None
    average_entry = float(position.average_entry_price)
    quantity = float(position.quantity)
    gross = (bar["close"] - average_entry) * quantity
    per_share = (
        entry_commission_per_share
        if entry_commission_per_share is not None
        else average_entry * commission_rate
    )
    return round(gross - per_share * quantity, 4)


def _portfolio_response(
    portfolio: PaperPortfolio, db: Optional[Session] = None
) -> PaperPortfolioResponse:
    """Serialise a portfolio with its unrealized P&L marked from real bar data.

    A portfolio bound to a replay session is valued from that session's stored
    bars at its own cursor; an unbound one from the demo fixture. The two lanes
    report different ``mode``/``price_source``/``data_as_of`` values on purpose,
    because a bound account silently marked from the demo fixture is exactly the
    mismatch this field set exists to prevent.
    """
    open_positions = [p for p in portfolio.positions if p.status == PositionStatus.open]
    session = _bound_session(db, portfolio) if db is not None else None
    session_valuation = session is not None

    if session_valuation:
        marks = _session_bound_marks(db, portfolio, (p.asset_id for p in open_positions))
    else:
        marks = _mark_prices(p.asset_id for p in open_positions)

    commissions = {
        p.id: _entry_commission_per_share(db, portfolio, p)
        for p in open_positions
    } if db is not None else {}
    values = [
        _unrealized_pnl(
            p,
            marks,
            entry_commission_per_share=commissions.get(p.id),
        )
        for p in open_positions
    ]
    unpriced = sorted({
        p.asset_id for p, value in zip(open_positions, values) if value is None
    })
    total_unrealized = None if unpriced else round(sum(v for v in values if v is not None), 4)
    as_of = sorted({bar.get("as_of") or bar["date"] for bar in marks.values()})

    if session_valuation:
        provider = session.provider_id or "stored_market_data"
        cursor = _session_cursor(session)
        note = (
            "估值使用此回放 session 已儲存行情 bar，並以其游標為上限；"
            "未實現損益已扣除成交紀錄中的入場佣金，無可用 bar 的標的不計入。"
            if cursor is not None
            else "此回放 session 尚未推進到任何 bar，暫無可用估值資料；未實現損益為 null，不代表 0。"
        )
        if unpriced:
            note = f"以下標的在回放游標前沒有已儲存行情，未實現損益無法計算：{', '.join(unpriced)}。" + note
        valuation = PaperValuationBasis(
            mode="session_replay",
            mode_label_zh="策略回放（已存行情）" if cursor is not None else "策略回放（未開始）",
            provider=provider,
            price_source="stored_market_data_bar",
            data_as_of=(as_of[-1] if as_of else None),
            unpriced_assets=unpriced,
            unrealized_pnl_basis="net_of_entry_commission",
            note=note,
        )
    else:
        note = (
            "估值使用內建合成 demo 收盤價，非即時行情；已實現與未實現損益均已扣除佣金，"
            "滑價已含在成交價內；無可用價格的標的不計入未實現損益。"
            if open_positions
            else "目前沒有持有部位，未實現損益為 0。"
        )
        if unpriced:
            note = f"以下標的沒有可用價格，未實現損益無法計算：{', '.join(unpriced)}。" + note
        valuation = PaperValuationBasis(
            mode="synthetic_fixture",
            mode_label_zh="合成數據",
            provider="Demo (Synthetic)",
            price_source="demo_synthetic_fixture",
            data_as_of=(as_of[-1] if as_of else None),
            unpriced_assets=unpriced,
            unrealized_pnl_basis="net_of_entry_commission",
            note=note,
        )

    return PaperPortfolioResponse.model_validate(portfolio).model_copy(
        update={
            "total_unrealized_pnl": total_unrealized,
            "valuation": valuation,
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


@router.post(
    "",
    response_model=PaperPortfolioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a skill unit (paper portfolio)",
    description=(
        "A skill unit is the isolated account: it carries its own virtual cash "
        "and, through `portfolio_id`, its positions, orders, fills and "
        "decisions. `PAPER_SKILL_UNIT_MAX_PER_USER` caps how many one main "
        "account may hold open at once and the cap is enforced here, not in the "
        "UI: the 11th create returns 409 `skill_unit_limit_reached` and names "
        "the retire entrypoint that releases a slot. Retired and completed "
        "accounts do not hold a slot; paused ones do."
    ),
)
def create_portfolio(
    payload: PaperPortfolioCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    if payload.simulation_session_id is not None:
        # The link is what lets a replay's rules trade this account, so a session
        # that is missing or belongs to someone else must be refused here rather
        # than stored as a link that silently never executes.
        require_session_for_owner(
            db, current_user.id, payload.simulation_session_id
        )

    try:
        assert_slot_available(db, current_user.id)
    except SkillUnitLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail())

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
        actor_type=request_actor_provenance(request),
        payload={"initial_virtual_cash": payload.initial_virtual_cash},
    )

    db.commit()
    db.refresh(portfolio)
    return _portfolio_response(portfolio, db)


@router.get("", response_model=List[PaperPortfolioResponse])
def list_portfolios(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolios = (
        db.query(PaperPortfolio)
        .filter(stored_as(PaperPortfolio.owner_id, current_user.id))
        .all()
    )
    return [_portfolio_response(p, db) for p in portfolios]


@router.get("/{portfolio_id}", response_model=PaperPortfolioResponse)
def get_portfolio(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _portfolio_response(
        _get_portfolio_with_ownership(portfolio_id, db, current_user), db
    )


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
    open_positions = [p for p in positions if p.status == PositionStatus.open]
    # Same lane selection as the portfolio view: a bound account is marked at
    # its replay cursor, so a position and its portfolio cannot disagree.
    if _bound_session(db, portfolio) is not None:
        marks = _session_bound_marks(db, portfolio, (p.asset_id for p in open_positions))
    else:
        marks = _mark_prices(p.asset_id for p in open_positions)

    responses: List[PaperPositionResponse] = []
    for position in positions:
        if position.status != PositionStatus.open:
            responses.append(PaperPositionResponse.model_validate(position))
            continue
        value = _unrealized_pnl(
            position,
            marks,
            entry_commission_per_share=_entry_commission_per_share(db, portfolio, position),
        )
        mark = marks.get(position.asset_id)
        responses.append(
            PaperPositionResponse.model_validate(position).model_copy(
                update={
                    "unrealized_pnl": value,
                    "mark_price": mark["close"] if mark else None,
                    "mark_as_of": (mark.get("as_of") or mark["date"]) if mark else None,
                    "mark_source": mark["source"] if mark else None,
                    "unrealized_pnl_basis": (
                        "net_of_entry_commission" if value is not None else None
                    ),
                }
            )
        )
    return responses


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

    actor_type = request_actor_provenance(request)

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
        actor_type=decision_actor_type(actor_type),
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
    request: Request,
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
            actor_type=request_actor_provenance(request),
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    db.commit()
    db.refresh(order)
    return PaperOrderLifecycleResponse(
        order=PaperOrderResponse.model_validate(order),
        execution=_order_lifecycle_view(db, portfolio, order),
    )


def _require_usable_and_bound_version(
    db: Session, *, portfolio: PaperPortfolio, version_id: UUID, owner_id: UUID
) -> None:
    """Refuse a version this decision may not record, whether claimed or derived.

    The version row has neither an owner nor a status: ownership is the parent
    experiment's, usability is the freeze stamp. Then the account itself gets a
    say, because a decision recorded against a strategy the account does not run
    is a journal entry about research nobody here is trading.

    This is the one statement of that rule. The desk reaches it both when the
    request names a version and when the inference's own metadata does — a
    version arrived at by reading someone else's row cannot be a version exempt
    from the checks, or omitting the field would become the way to skip them.
    """
    validate_strategy_version(db, version_id=version_id, owner_id=owner_id)

    # Consistency with what this account is actually running: if any run
    # for this portfolio names a strategy version, it must be the one
    # claimed. The indexed identity key answers on PostgreSQL, so it stays
    # first; on the lane where UUID columns are text it sees only rows an
    # ORM write inserted (32 characters, no dashes), while this product's
    # own creation paths insert the dashed 36-character form with raw SQL.
    # Both forms are therefore consulted, and the comparison is made on
    # parsed UUIDs so the spelling a row happens to be stored in cannot
    # decide whether the account's binding was contradicted.
    from sqlalchemy import text as sql_text

    portfolio_run = db.query(PaperTradingRun).filter(
        PaperTradingRun.portfolio_id == portfolio.id,
        PaperTradingRun.strategy_version_id.isnot(None),
    ).first()
    bound_version = (
        portfolio_run.strategy_version_id
        if portfolio_run is not None
        else db.execute(
            sql_text(
                "SELECT strategy_version_id FROM paper_trading_runs "
                "WHERE replace(CAST(portfolio_id AS TEXT), '-', '') "
                "= :portfolio_hex AND strategy_version_id IS NOT NULL "
                "LIMIT 1"
            ),
            {"portfolio_hex": str(portfolio.id).replace("-", "")},
        ).scalar()
    )
    if bound_version is not None and uuid.UUID(str(bound_version)) != version_id:
        run_version = str(uuid.UUID(str(bound_version)))
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "version_mismatch",
                "run_version": run_version,
                "decision_version": str(version_id),
                "message": "Decision's linked version does not match the portfolio run's strategy version.",
            },
        )


@router.post("/{portfolio_id}/decisions", response_model=DecisionRecordResponse, status_code=status.HTTP_201_CREATED)
def create_decision(
    portfolio_id: UUID,
    payload: DecisionRecordCreate,
    request: Request,
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

    # The verified credential channel decides the actor. The body's actor_type is
    # accepted for client compatibility but only recorded as a declaration, so a
    # caller cannot label an agent-written row as a human decision.
    actor_type = request_actor_provenance(request)

    # The version this decision records, and the checks it has to survive. Either
    # the request names one, or the inference it names does: a decision that
    # points at a model execution cannot leave the strategy that execution ran on
    # unstated when the execution itself says, because the journal would then show
    # an origin with a version beside a decision with none and let a reader assume
    # they were different. What is derived is held to exactly what is claimed.
    claimed_version = payload.linked_experiment_version_id
    if claimed_version is None and payload.linked_inference_id is not None:
        claimed_version = validate_linked_inference(
            db,
            owner_id=current_user.id,
            inference_id=payload.linked_inference_id,
            portfolio_id=portfolio.id,
        )

    if claimed_version is not None:
        _require_usable_and_bound_version(
            db, portfolio=portfolio, version_id=claimed_version, owner_id=current_user.id
        )

    # Validate linked_inference_id if provided — must exist, belong to same owner, and contain model-execution metadata
    if payload.linked_inference_id is not None:
        validate_linked_inference(
            db,
            owner_id=current_user.id,
            inference_id=payload.linked_inference_id,
            portfolio_id=portfolio.id,
            # Whatever version this decision records — claimed or derived — the
            # inference has to come from a model trained from that same version.
            # Checked after the version's own ownership/freeze/run checks above, so
            # the two sides of the lineage are never allowed to name different
            # research.
            strategy_version_id=claimed_version,
        )

    if payload.simulation_session_id is not None:
        require_session_for_owner(db, current_user.id, payload.simulation_session_id)

    decision = DecisionRecord(
        owner_id=current_user.id,
        paper_portfolio_id=portfolio.id,
        simulation_session_id=payload.simulation_session_id,
        actor_type=decision_actor_type(actor_type),
        decision_type=PaperDecisionType(payload.decision_type),
        linked_experiment_version_id=claimed_version,
        linked_inference_id=payload.linked_inference_id,
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
        actor_type=actor_type,
        payload={
            "decision_type": payload.decision_type,
            "actor_type": actor_type,
            "client_declared_actor_type": payload.actor_type,
            # Which of the two a recorded version was: only the audit trail can
            # still say so once the row exists, and "the account was trading this"
            # is a different claim from "the user said this account was trading it".
            "version_source": (
                "claimed"
                if payload.linked_experiment_version_id is not None
                else "inference_origin"
                if claimed_version is not None
                else None
            ),
        },
    )

    db.commit()
    db.refresh(decision)
    return decision


def _decisions_in_scope(db: Session, portfolio: PaperPortfolio, owner_id: UUID):
    """The one decision scope both decision surfaces read.

    Rows recorded before ``paper_portfolio_id`` existed cannot be attributed;
    they stay visible rather than silently vanishing from the journal. Keeping
    the filter in one place is what makes the JSON read and the CSV export able
    to be the same set of decisions under different renderings.
    """
    return db.query(DecisionRecord).filter(
        DecisionRecord.owner_id == owner_id,
        or_(
            DecisionRecord.paper_portfolio_id == portfolio.id,
            DecisionRecord.paper_portfolio_id.is_(None),
        ),
    ).order_by(DecisionRecord.decision_time.desc())


@router.get("/{portfolio_id}/decisions", response_model=List[DecisionRecordResponse])
def list_decisions(
    portfolio_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    return _decisions_in_scope(db, portfolio, current_user.id).all()


@router.post(
    "/{portfolio_id}/decisions/{decision_id}/outcome",
    response_model=OutcomeEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_outcome_evaluation(
    portfolio_id: UUID,
    decision_id: UUID,
    payload: OutcomeEvaluationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    actor_type = request_actor_provenance(request)
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
        actor_type=actor_type,
        payload={
            "hypothesis_status": payload.hypothesis_status,
            "actor_type": actor_type,
        },
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    actor_type = request_actor_provenance(request)

    decision = DecisionRecord(
        owner_id=current_user.id,
        paper_portfolio_id=portfolio.id,
        actor_type=decision_actor_type(actor_type),
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
        actor_type=actor_type,
        payload={"outside_locked_strategy": True, "actor_type": actor_type},
        rules_locked=True,
    )

    db.commit()
    db.refresh(decision)
    return decision


@router.post(
    "/{portfolio_id}/pause",
    response_model=PaperPortfolioResponse,
    summary="Pause a portfolio's entries",
    description=(
        "Only an `active` portfolio can be paused; anything else returns 400. A "
        "paused account opens no new exposure: the direct lane refuses a buy with "
        "`portfolio_paused`, and a queued buy is held pending by the executor "
        "instead of filling. Exits are unaffected, and the pause is reversible "
        "with `/resume`."
    ),
)
def pause_portfolio(
    portfolio_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status != PortfolioStatus.active:
        raise HTTPException(status_code=400, detail="Can only pause an active portfolio")

    portfolio.status = PortfolioStatus.paused

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_paused", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
    )

    db.commit()
    db.refresh(portfolio)
    return _portfolio_response(portfolio, db)


@router.post(
    "/{portfolio_id}/resume",
    response_model=PaperPortfolioResponse,
    summary="Resume a paused portfolio",
    description=(
        "Only a `paused` portfolio can be resumed; anything else returns 400. The "
        "account returns to `active` and a buy held pending by the pause becomes "
        "eligible at the next execution window."
    ),
)
def resume_portfolio(
    portfolio_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status != PortfolioStatus.paused:
        raise HTTPException(status_code=400, detail="Can only resume a paused portfolio")

    portfolio.status = PortfolioStatus.active

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_resumed", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
    )

    db.commit()
    db.refresh(portfolio)
    return _portfolio_response(portfolio, db)


@router.post(
    "/{portfolio_id}/retire",
    response_model=PaperPortfolioResponse,
    summary="Retire a portfolio (terminal)",
    description=(
        "Mark the portfolio `retired`, stamp `closed_at`, and emit a "
        "`portfolio_retired` event carrying `final_cash`. Retirement is terminal: "
        "it does not liquidate open positions and does not cancel pending orders. "
        "A pending order is never executed afterwards — the executor reports it "
        "with the `portfolio_retired` blocker and it stays in the journal until "
        "cancelled. New orders are refused, and retiring a portfolio that is "
        "already `retired` or `completed` returns 400."
    ),
)
def retire_portfolio(
    portfolio_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    if portfolio.status in (PortfolioStatus.retired, PortfolioStatus.completed):
        raise HTTPException(status_code=400, detail=f"Cannot retire a '{portfolio.status.value}' portfolio")

    portfolio.status = PortfolioStatus.retired
    portfolio.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        entity_type="paper_portfolio", entity_id=portfolio.id,
        event_type="portfolio_retired", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"final_cash": portfolio.current_virtual_cash},
    )

    db.commit()
    db.refresh(portfolio)
    return _portfolio_response(portfolio, db)


# ── Trade-journal export (CSV + JSON) ──────────────────────────────────
#
# One row per executed fill, plus one row per order that never filled (so
# pending/cancelled/rejected orders stay visible in the journal instead of
# silently dropping). Column order is the contract the UI download and the
# CSV parser tests both read.

JOURNAL_COLUMNS = [
    "record_type",
    "record_classification",
    "order_state",
    "position_status",
    "portfolio_id",
    "currency",
    "order_id",
    "fill_id",
    "decision_id",
    "linked_experiment_version_id",
    "linked_inference_id",
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
    "order_state": "'pending' while the order can still execute; 'terminal' once it is filled, cancelled or rejected. This describes the order, not the position.",
    "position_status": "'open' or 'closed' for the portfolio's position in this row's asset at export time; empty when no position row exists. A terminal order can still leave its position open, and a closed position does not rewrite the order's history.",
    "currency": "ISO-4217 code that applies to every monetary column in the row (the portfolio currency).",
    "linked_experiment_version_id": "Decision lineage: the research version this decision was recorded under, exactly as the create call stored it and the decisions read returns. Empty = the decision carried no version, or no decision row is attached to this order.",
    "linked_inference_id": "Decision lineage: the usage_records.id of the model execution behind the decision. Creating a decision refuses an inference that is not this owner's, not a personal-model prediction, not generated for this skill account, or — when the decision names a strategy version — not produced by a model trained from that version, so a value here was verified at write time. The link carries no schema foreign key, so nothing at the database keeps that billing row or takes it away with the decision: GET /{portfolio_id}/decision-history reports whether each id still reads back. No job in this build deletes usage_records — measured 2026-11-18, the only DELETE against the table is a seed script's own cleanup — so a row that has gone went out of band, not on a schedule. Empty = a human/rule decision with no model execution.",
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
    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.portfolio_id == portfolio.id)
        .all()
    )
    position_status: Dict[str, str] = {}
    for position in positions:
        status = getattr(position.status, "value", position.status)
        # Prefer a live position: a later BUY can create a new open row while
        # an older closed row for the same asset is kept as history.
        if position_status.get(position.asset_id) != PositionStatus.open.value:
            position_status[position.asset_id] = status
    for order in orders:
        decision = decision_map.get(order.decision_id) if order.decision_id else None
        classification = "open" if order.status == OrderStatus.pending else "closed"
        base = {
            "record_classification": classification,
            "order_state": "pending" if order.status == OrderStatus.pending else "terminal",
            "position_status": position_status.get(order.asset_id),
            "portfolio_id": str(portfolio.id),
            "currency": portfolio.currency,
            "order_id": str(order.id),
            "decision_id": str(order.decision_id) if order.decision_id else None,
            # The same two identities the create call accepted and the decisions
            # read returns, so one exported row can be traced back to the version
            # and the inference without the application.
            "linked_experiment_version_id": (
                str(decision.linked_experiment_version_id)
                if decision and decision.linked_experiment_version_id
                else None
            ),
            "linked_inference_id": (
                str(decision.linked_inference_id)
                if decision and decision.linked_inference_id
                else None
            ),
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


def _journal_csv(rows: List[Dict[str, Any]], columns: List[str] = JOURNAL_COLUMNS) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_journal_cell(col, row.get(col)) for col in columns])
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
    decisions = _decisions_in_scope(db, portfolio, current_user.id).all()
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


# ── Decision-history export (CSV + JSON) ───────────────────────────────
#
# A second file with its own contract, not extra rows inside the trade journal.
# The journal answers "what did this account trade" and is built from orders, so
# by construction it cannot show a decision that never carried an order. This one
# answers "what did this account decide, and on what source", over exactly the
# decision rows GET /{portfolio_id}/decisions returns.

DECISION_HISTORY_COLUMNS = [
    "record_type",
    "decision_id",
    "portfolio_id",
    "decision_scope",
    "decision_type",
    "actor_type",
    "decision_time",
    "data_cutoff_at",
    "outcome_status",
    "linked_experiment_version_id",
    "linked_inference_id",
    "inference_source_state",
    "inference_model_version_id",
    "inference_strategy_version_id",
    "inference_artifact_sha256",
    "inference_as_of_date",
    "inference_skill_unit_id",
    "simulation_session_id",
    "has_order",
    "order_count",
    "order_ids",
    "fill_count",
    "source_run_id",
    "source_trade_id",
    "source_rule_id",
    "rationale",
    "expected_outcome",
    "self_reported_confidence",
    "expected_timeframe_days",
]

# Copied off the inference usage row at export time, so a reader can tell these
# apart from the two linked_* columns, which are what the create call stored —
# and, for a row written before the two were compared, whether they agree.
_INFERENCE_METADATA_COLUMNS = {
    "inference_model_version_id": "model_version_id",
    "inference_strategy_version_id": "strategy_version_id",
    "inference_artifact_sha256": "artifact_sha256",
    "inference_as_of_date": "as_of_date",
    "inference_skill_unit_id": "skill_unit_id",
}

DECISION_HISTORY_COLUMN_SEMANTICS = {
    "record_type": "Always 'decision': one row per recorded decision. This file has no fill and no order rows, so it cannot be read as a trade ledger and does not replace the trade journal.",
    "decision_id": "decision_records.id, in canonical dashed text regardless of how the row is stored.",
    "portfolio_id": "The skill account this file was asked for (paper_portfolios.id).",
    "decision_scope": "'this_portfolio' = the decision row names this account. 'unattributed' = recorded before the account link existed, so it cannot be attributed and stays visible instead of disappearing. Same inclusion rule as GET /{id}/decisions and the trade journal.",
    "decision_type": "Controlled vocabulary (entry, exit, no_action, pause, retire, override_request).",
    "actor_type": "The authentication channel that wrote the row, decided server-side from the credential: 'human' for a password session. A client's declared actor in the request body is not what lands here.",
    "decision_time": "ISO-8601 naive UTC instant the decision was recorded. Rows are newest first, the order the decisions read returns.",
    "outcome_status": "Controlled vocabulary of the outcome-evaluation state (pending until an evaluation is recorded).",
    "linked_experiment_version_id": "The research version this decision was recorded under, exactly as the decisions read returns it. It is the version the create call named, or — when the call named none and named an inference that does — the version that inference recorded, after the same checks a named version is held to (this account owns it, it is frozen, it does not contradict the version bound to the account's run). Empty means neither side named one: a row written before either source could, or a decision with no lineage to reconcile. An empty cell here is not a verified absence and never means the version was confirmed — for a decision that names an inference, read inference_strategy_version_id below, which is what the origin itself says.",
    "linked_inference_id": "The usage_records.id this decision names as its origin, exactly as stored. Validated at write time (app/services/decision_lineage.py) against the version column above: a decision that names both is refused unless the inference came from a model trained from that version. There is deliberately no schema foreign key behind it, so read inference_source_state.",
    "inference_source_state": "'none' = no inference was named (a human or rule decision). 'present' = the usage row is still readable. 'missing' = the decision names an inference whose usage row cannot be read now. usage_records is the billing ledger and nothing in the schema links the two, so a lineage id can outlive the row it points at; this column says so rather than leaving an id that reads back nothing. This build runs no job that ages billing rows out, so 'missing' is reachable today only through a deletion made outside the application — the state is a guard, not a described lifecycle.",
    "inference_model_version_id": "The model version the inference itself recorded, read from the usage row now. Empty unless inference_source_state is 'present'.",
    "inference_strategy_version_id": "The strategy version the inference's own model was trained from, read from the usage row now — the origin's statement, not the decision's. A decision written by a door that compares the two cannot disagree with linked_experiment_version_id above; a row written before that comparison existed can, and this column is how the difference stays visible instead of being read as agreement.",
    "inference_artifact_sha256": "The artifact digest the inference ran on, read from the usage row now: the pinned build behind this decision, not the currently selected one.",
    "inference_as_of_date": "YYYY-MM-DD data cut-off the inference was computed at, read from the usage row now.",
    "inference_skill_unit_id": "The skill account the inference was generated for, read from the usage row now. Creating the decision refuses a mismatch, so equality with portfolio_id is the expected case; a difference is exported rather than hidden.",
    "simulation_session_id": "The session the decision was recorded against, when the create call named one.",
    "has_order": "true when at least one order names this decision through paper_orders.decision_id (a schema foreign key), false when the decision carried no order.",
    "order_count": "How many orders name this decision. 0 = a decision with no order, which is precisely the row the trade journal cannot carry.",
    "order_ids": "decision_records ← paper_orders.decision_id, ';' joined in order-creation time. Empty when order_count is 0.",
    "fill_count": "How many simulated fills those orders produced in total. Empty price/quantity facts stay in the trade journal, which is fill-grained.",
    "source_run_id": "Set by the execution lane when a run produced the decision; empty for a decision the owner typed.",
    "source_trade_id": "Set by the execution lane to identify the traded item the decision came from; empty for a typed decision. Note the per-account uniqueness on (paper_portfolio_id, source_trade_id, decision_type) does not constrain rows whose source_trade_id is NULL.",
    "source_rule_id": "Which rule fired, when a rule produced the decision.",
    "rationale": "Free text; may contain commas, quotes, newlines and non-ASCII. Spreadsheet-formula guarded.",
    "expected_outcome": "What the owner expected to happen, in their own words, as recorded.",
    "self_reported_confidence": "Controlled vocabulary (low, medium, high, not_provided).",
    "expected_timeframe_days": "Integer days the owner expected the outcome within. Empty = not provided.",
}

DECISION_HISTORY_COLUMN_SEMANTICS.update(
    {key: JOURNAL_COLUMN_SEMANTICS[key] for key in
     ("data_cutoff_at", "_null", "_encoding", "_formula_guard")}
)


def _decision_history_rows(
    db: Session,
    portfolio: PaperPortfolio,
    decisions: List[DecisionRecord],
) -> List[Dict[str, Any]]:
    """One row per decision, with its orders counted and its source resolved."""
    inference_ids = [
        d.linked_inference_id for d in decisions if d.linked_inference_id is not None
    ]
    usage_by_id: Dict[Any, UsageRecord] = {}
    if inference_ids:
        # Typed IN on a UUID-as-UUID column: the bind uses the column's own type,
        # so this matches whether the lane stores the id as hex or as text.
        # Bounded by owner as well as by id. The create door already refused an
        # inference that is not this owner's, so under writes made through the API
        # the two bounds agree; this one is what stops a lineage id that arrived
        # any other way — a backfill, a direct write, a row whose owner changed —
        # from having another account's billing metadata rendered into this
        # owner's history. It reads back as ``missing``, which is the state the
        # contract already reserves for a source this caller may not see.
        usage_by_id = {
            usage.id: usage
            for usage in db.query(UsageRecord)
            .filter(
                UsageRecord.id.in_(inference_ids),
                UsageRecord.user_id == portfolio.owner_id,
            )
            .all()
        }

    decision_ids = [d.id for d in decisions]
    orders_by_decision: Dict[Any, List[PaperOrder]] = {}
    if decision_ids:
        for order in (
            db.query(PaperOrder)
            .filter(PaperOrder.decision_id.in_(decision_ids))
            .order_by(PaperOrder.created_at)
            .all()
        ):
            orders_by_decision.setdefault(order.decision_id, []).append(order)

    rows: List[Dict[str, Any]] = []
    for decision in decisions:
        orders = orders_by_decision.get(decision.id, [])
        usage = (
            usage_by_id.get(decision.linked_inference_id)
            if decision.linked_inference_id is not None
            else None
        )
        if decision.linked_inference_id is None:
            source_state = "none"
        elif usage is not None:
            source_state = "present"
        else:
            source_state = "missing"
        metadata = usage.metadata_json if (usage is not None and isinstance(usage.metadata_json, dict)) else {}

        row = {
            "record_type": "decision",
            "decision_id": str(decision.id),
            "portfolio_id": str(portfolio.id),
            "decision_scope": (
                "this_portfolio"
                if decision.paper_portfolio_id == portfolio.id
                else "unattributed"
            ),
            "decision_type": decision.decision_type,
            "actor_type": decision.actor_type,
            "decision_time": (
                decision.decision_time.isoformat() if decision.decision_time else None
            ),
            "data_cutoff_at": decision.data_cutoff_at,
            "outcome_status": decision.outcome_status,
            "linked_experiment_version_id": (
                str(decision.linked_experiment_version_id)
                if decision.linked_experiment_version_id
                else None
            ),
            "linked_inference_id": (
                str(decision.linked_inference_id)
                if decision.linked_inference_id
                else None
            ),
            "inference_source_state": source_state,
            "simulation_session_id": (
                str(decision.simulation_session_id)
                if decision.simulation_session_id
                else None
            ),
            "has_order": bool(orders),
            "order_count": len(orders),
            "order_ids": ";".join(str(order.id) for order in orders) or None,
            "fill_count": sum(len(list(order.fills)) for order in orders),
            "source_run_id": (
                str(decision.source_run_id) if decision.source_run_id else None
            ),
            "source_trade_id": (
                str(decision.source_trade_id) if decision.source_trade_id else None
            ),
            "source_rule_id": decision.source_rule_id,
            "rationale": decision.rationale,
            "expected_outcome": decision.expected_outcome,
            "self_reported_confidence": decision.self_reported_confidence,
            "expected_timeframe_days": decision.expected_timeframe_days,
        }
        for column, metadata_key in _INFERENCE_METADATA_COLUMNS.items():
            value = metadata.get(metadata_key)
            row[column] = value if isinstance(value, (str, int, float)) else None
        rows.append(row)
    return rows


@router.get("/{portfolio_id}/decision-history")
def export_decision_history(
    portfolio_id: UUID,
    format: str = "csv",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Owner-authorized export of every recorded decision, order or no order.

    ``format=csv`` returns the attachment; ``format=json`` returns the same rows
    plus ``column_semantics``. The row set is exactly the one
    ``GET /{portfolio_id}/decisions`` returns, in the same order.

    This is deliberately a separate file from ``GET /{portfolio_id}/journal``:
    that one is built from this account's orders and answers what was traded.
    Adding decision rows to it would change what its rows mean.
    """
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'")

    portfolio = _get_portfolio_with_ownership(portfolio_id, db, current_user)
    decisions = _decisions_in_scope(db, portfolio, current_user.id).all()
    rows = _decision_history_rows(db, portfolio, decisions)

    if format == "json":
        return {
            "portfolio_id": str(portfolio.id),
            "currency": portfolio.currency,
            "scope": "decisions",
            "columns": DECISION_HISTORY_COLUMNS,
            "column_semantics": DECISION_HISTORY_COLUMN_SEMANTICS,
            "row_count": len(rows),
            "records": rows,
        }

    csv_text = _journal_csv(rows, DECISION_HISTORY_COLUMNS)
    filename = f"decision-history-{portfolio.id}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
