"""Execution agent API — agent-facing endpoints.

These endpoints are called by the user-side execution agent (not the frontend).
The agent authenticates via Bearer token (JWT or API key).
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.auth.jwt import get_current_user
from app.models.execution import OrderIntent, IntentStatus
from app.services.execution_agent_api import (
    list_authorized_intents,
    agent_report_submitted,
    agent_report_fill,
    agent_report_rejected,
    agent_report_cancelled,
)
from app.lib.timeutil import utcnow


router = APIRouter(prefix="/execution-agent", tags=["execution-agent"])


class OrderIntentAgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intent_id: str
    user_id: uuid.UUID
    portfolio_id: uuid.UUID
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    status: str
    broker_ref: Optional[str] = None
    rejection_reason: Optional[str] = None
    expires_at: datetime
    created_at: datetime


class SubmissionReport(BaseModel):
    broker_ref: str
    submitted_at: Optional[str] = None


class FillReport(BaseModel):
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fill_time: str
    broker_ref: str
    commission: float = 0.0


class RejectionReport(BaseModel):
    reason: str


class CancellationReport(BaseModel):
    reason: str


class HeartbeatReport(BaseModel):
    connected: bool
    ib_gateway_host: Optional[str] = None
    ib_gateway_port: Optional[int] = None
    timestamp: Optional[str] = None


@router.get("/order-intents", response_model=List[OrderIntentAgentResponse])
def api_agent_poll_intents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Poll for authorized order intents awaiting submission."""
    return list_authorized_intents(db, user_id=current_user.id)


@router.post("/order-intents/{intent_id}/submitted", response_model=OrderIntentAgentResponse)
def api_agent_report_submitted(
    intent_id: str,
    payload: SubmissionReport,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Agent reports that an order was submitted to the broker."""
    submitted_at = None
    if payload.submitted_at:
        submitted_at = datetime.fromisoformat(payload.submitted_at)
    return agent_report_submitted(
        db,
        intent_id=intent_id,
        user_id=current_user.id,
        broker_ref=payload.broker_ref,
        submitted_at=submitted_at,
    )


@router.post("/order-intents/{intent_id}/fills", status_code=status.HTTP_201_CREATED)
def api_agent_report_fill(
    intent_id: str,
    payload: FillReport,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Agent reports a fill event from the broker."""
    fill_time = datetime.fromisoformat(payload.fill_time)
    fill_record = agent_report_fill(
        db,
        intent_id=intent_id,
        user_id=current_user.id,
        quantity=payload.quantity,
        price=payload.price,
        fill_time=fill_time,
        broker_ref=payload.broker_ref,
        commission=payload.commission,
    )
    return {"fill_id": str(fill_record.id), "status": "recorded"}


@router.post("/order-intents/{intent_id}/rejected", response_model=OrderIntentAgentResponse)
def api_agent_report_rejected(
    intent_id: str,
    payload: RejectionReport,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Agent reports that an order was rejected by the broker."""
    return agent_report_rejected(
        db,
        intent_id=intent_id,
        user_id=current_user.id,
        reason=payload.reason,
    )


@router.post("/order-intents/{intent_id}/cancelled", response_model=OrderIntentAgentResponse)
def api_agent_report_cancelled(
    intent_id: str,
    payload: CancellationReport,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Agent reports that an order was cancelled."""
    return agent_report_cancelled(
        db,
        intent_id=intent_id,
        user_id=current_user.id,
        reason=payload.reason,
    )


@router.post("/heartbeat")
def api_agent_heartbeat(
    payload: HeartbeatReport,
    current_user: User = Depends(get_current_user),
):
    """Agent heartbeat — records that the agent is alive."""
    return {
        "status": "ok",
        "user_id": str(current_user.id),
        "received_at": utcnow().isoformat(),
    }
