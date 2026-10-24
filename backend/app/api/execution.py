import uuid
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session


def _normalize_to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize datetime to naive UTC for database comparison.

    - Timezone-aware: convert to UTC, strip tzinfo
    - Naive: treat as UTC, return as-is
    - None: return None

    This matches the project convention where database stores naive UTC datetimes.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

from app.database import get_db
from app.models.user import User
from app.auth.jwt import get_current_user
from app.auth.api_keys import check_api_key_scope
from app.auth.safety import safety_control
from app.models.kill_switch import KillSwitchScope
from app.services.execution import (
    create_order_intent,
    authorize_intent,
    submit_intent,
    cancel_intent,
    reconcile_order,
    get_fill_records,
)
from app.models.execution import (
    OrderIntent,
    FillReconciliation,
    FillRecord,
    IntentStatus,
)


# ── Pydantic schemas ──


class OrderIntentCreate(BaseModel):
    portfolio_id: uuid.UUID
    symbol: str
    side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: int = Field(..., gt=0)
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT)$")
    limit_price: Optional[float] = None
    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Client-supplied key to prevent duplicate order creation. "
                    "If a request with the same key already exists, the existing intent is returned.",
    )


class OrderIntentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    portfolio_id: uuid.UUID
    intent_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    status: str
    authorization_token: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    rejection_reason: Optional[str]
    broker_ref: Optional[str]
    cancel_requested: bool = False
    idempotency_key: Optional[str]
    fill_source: Optional[str]
    broker_confirmed_at: Optional[datetime]


class AuthorizeRequest(BaseModel):
    authorization_token: str


class ReconcileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_intent_id: uuid.UUID
    expected_quantity: int
    filled_quantity: int
    discrepancy: bool
    reconciled_at: datetime


class FillRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_intent_id: uuid.UUID
    quantity: int
    price: Optional[float]
    commission: Optional[float] = Field(
        None,
        description="Agent-reported commission. NULL means unknown/not reported, distinct from 0."
    )
    fill_source: str
    broker_ref: Optional[str]
    broker_confirmed_at: Optional[datetime]
    created_at: datetime


class OrderIntentWithFillsResponse(OrderIntentResponse):
    """Intent response with embedded fill records for execution history."""
    fills: List[FillRecordResponse] = []
    total_filled_quantity: int = Field(
        0,
        description="Sum of all fill quantities for this intent."
    )


# ── Router ──


router = APIRouter(prefix="/execution", tags=["execution"])


def _get_intent_or_404(db: Session, intent_id: str) -> OrderIntent:
    intent = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    return intent


@router.post("/intents", response_model=OrderIntentResponse, status_code=status.HTTP_201_CREATED)
def api_create_intent(
    payload: OrderIntentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    intent = create_order_intent(
        db,
        user_id=current_user.id,
        portfolio_id=payload.portfolio_id,
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        limit_price=payload.limit_price,
        idempotency_key=payload.idempotency_key,
    )
    return intent


@router.post("/intents/{intent_id}/authorize", response_model=OrderIntentResponse)
def api_authorize_intent(
    intent_id: str,
    payload: AuthorizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    intent = _get_intent_or_404(db, intent_id)
    # Ownership check at route level for consistency with other routes
    if intent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if intent.authorization_token != payload.authorization_token:
        raise HTTPException(status_code=403, detail="Invalid authorization token")
    return authorize_intent(db, intent_id=intent_id, user_id=current_user.id)


@router.post("/intents/{intent_id}/submit", response_model=OrderIntentResponse)
def api_submit_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    _: None = Depends(safety_control(KillSwitchScope.execution, "execution")),
):
    """Submit an order intent to the broker.
    
    Safety controls:
    - Kill switch: Blocks new submissions if execution/global switch is active
    - Rate limit: 50 execution intents per hour per user/API key
    
    Note: Kill switch blocks NEW submissions only. Already-submitted orders
    are not cancelled by the kill switch.
    """
    return submit_intent(db, intent_id=intent_id, user_id=current_user.id)


@router.post("/intents/{intent_id}/cancel", response_model=OrderIntentResponse)
def api_cancel_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    return cancel_intent(db, intent_id=intent_id, user_id=current_user.id)


@router.get("/intents", response_model=List[OrderIntentResponse])
def api_list_intents(
    portfolio_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = Query(
        None,
        description="Filter intents created at or after this time. "
                    "Timezone-aware datetimes are converted to UTC. "
                    "Naive datetimes are treated as UTC. "
                    "Filters intent creation time, not fill execution time."
    ),
    end_date: Optional[datetime] = Query(
        None,
        description="Filter intents created at or before this time (inclusive). "
                    "Timezone-aware datetimes are converted to UTC. "
                    "Naive datetimes are treated as UTC. "
                    "Filters intent creation time, not fill execution time."
    ),
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    """List order intents for the current user with optional filters.

    Filters:
    - portfolio_id: Only intents for this portfolio
    - status: Only intents with this status
    - start_date/end_date: Only intents created in this range (inclusive on both boundaries).
      Timezone-aware datetimes are converted to UTC. Naive datetimes are treated as UTC.
      These filter intent creation time, not fill execution time.

    Pagination:
    - limit: Max results (optional, max 1000). If not specified, returns all results.
    - offset: Skip this many results

    Results are ordered by created_at DESC, then intent_id DESC for stable pagination.
    """
    start_utc = _normalize_to_naive_utc(start_date)
    end_utc = _normalize_to_naive_utc(end_date)

    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise HTTPException(
            status_code=400,
            detail="start_date must not be later than end_date"
        )

    query = db.query(OrderIntent).filter(OrderIntent.user_id == current_user.id)

    if portfolio_id is not None:
        query = query.filter(OrderIntent.portfolio_id == portfolio_id)

    if status is not None:
        try:
            status_enum = IntentStatus(status)
            query = query.filter(OrderIntent.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if start_utc is not None:
        query = query.filter(OrderIntent.created_at >= start_utc)

    if end_utc is not None:
        query = query.filter(OrderIntent.created_at <= end_utc)

    query = query.order_by(
        OrderIntent.created_at.desc(),
        OrderIntent.intent_id.desc(),
    ).offset(offset)

    if limit is not None:
        query = query.limit(limit)

    return query.all()


@router.get("/history", response_model=List[OrderIntentWithFillsResponse])
def api_execution_history(
    portfolio_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = Query(
        None,
        description="Filter intents created at or after this time. "
                    "Timezone-aware datetimes are converted to UTC. "
                    "Naive datetimes are treated as UTC. "
                    "Filters intent creation time, not fill execution time."
    ),
    end_date: Optional[datetime] = Query(
        None,
        description="Filter intents created at or before this time (inclusive). "
                    "Timezone-aware datetimes are converted to UTC. "
                    "Naive datetimes are treated as UTC. "
                    "Filters intent creation time, not fill execution time."
    ),
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    """Execution history: intents with embedded fill records.

    Returns agent-reported execution records. Fills are NOT verified against
    broker statements; they represent what the execution agent reported.

    Filters and pagination work the same as GET /intents:
    - start_date/end_date: Inclusive range, timezone-aware converted to UTC, naive treated as UTC.
      These filter intent creation time, not fill execution time.
    - Results ordered by created_at DESC, then intent_id DESC for stable pagination.

    Each intent includes:
    - fills: List of all FillRecords for this intent
    - total_filled_quantity: Sum of fill quantities

    Commission semantics:
    - commission: NULL means unknown/not reported by agent (distinct from 0.0)
    - commission: 0.0 means explicitly reported as zero
    """
    if limit > 1000:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 1000")

    start_utc = _normalize_to_naive_utc(start_date)
    end_utc = _normalize_to_naive_utc(end_date)

    if start_utc is not None and end_utc is not None and start_utc > end_utc:
        raise HTTPException(
            status_code=400,
            detail="start_date must not be later than end_date"
        )

    query = db.query(OrderIntent).filter(OrderIntent.user_id == current_user.id)

    if portfolio_id is not None:
        query = query.filter(OrderIntent.portfolio_id == portfolio_id)

    if status is not None:
        try:
            status_enum = IntentStatus(status)
            query = query.filter(OrderIntent.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    if start_utc is not None:
        query = query.filter(OrderIntent.created_at >= start_utc)

    if end_utc is not None:
        query = query.filter(OrderIntent.created_at <= end_utc)

    intents = query.order_by(
        OrderIntent.created_at.desc(),
        OrderIntent.intent_id.desc(),
    ).offset(offset).limit(limit).all()

    result = []
    for intent in intents:
        fills = get_fill_records(db, intent_id=intent.intent_id)
        total_filled = sum(f.quantity for f in fills)

        intent_dict = {
            "id": intent.id,
            "user_id": intent.user_id,
            "portfolio_id": intent.portfolio_id,
            "intent_id": intent.intent_id,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "quantity": intent.quantity,
            "order_type": intent.order_type.value,
            "limit_price": intent.limit_price,
            "status": intent.status.value,
            "authorization_token": intent.authorization_token,
            "expires_at": intent.expires_at,
            "created_at": intent.created_at,
            "updated_at": intent.updated_at,
            "rejection_reason": intent.rejection_reason,
            "broker_ref": intent.broker_ref,
            "cancel_requested": intent.cancel_requested,
            "idempotency_key": intent.idempotency_key,
            "fill_source": intent.fill_source.value if intent.fill_source else None,
            "broker_confirmed_at": intent.broker_confirmed_at,
            "fills": fills,
            "total_filled_quantity": total_filled,
        }
        result.append(intent_dict)

    return result


@router.get("/intents/{intent_id}", response_model=OrderIntentResponse)
def api_get_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    intent = _get_intent_or_404(db, intent_id)
    if intent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return intent


@router.post(
    "/intents/{intent_id}/reconcile",
    response_model=ReconcileResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_reconcile_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    intent = _get_intent_or_404(db, intent_id)
    if intent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return reconcile_order(db, intent_id=intent_id)


@router.get(
    "/intents/{intent_id}/fills",
    response_model=List[FillRecordResponse],
)
def api_list_fill_records(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    intent = _get_intent_or_404(db, intent_id)
    if intent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return get_fill_records(db, intent_id=intent_id)
