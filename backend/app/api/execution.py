import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.auth.jwt import get_current_user
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
    fill_source: str
    broker_ref: Optional[str]
    broker_confirmed_at: Optional[datetime]
    created_at: datetime


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
):
    return cancel_intent(db, intent_id=intent_id, user_id=current_user.id)


@router.get("/intents", response_model=List[OrderIntentResponse])
def api_list_intents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(OrderIntent)
        .filter(OrderIntent.user_id == current_user.id)
        .order_by(OrderIntent.created_at.desc())
        .all()
    )


@router.get("/intents/{intent_id}", response_model=OrderIntentResponse)
def api_get_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
):
    intent = _get_intent_or_404(db, intent_id)
    if intent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return get_fill_records(db, intent_id=intent_id)
