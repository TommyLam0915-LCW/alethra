"""Service layer for execution agent API endpoints.

These functions are called by the agent-facing API routes. They handle
the state transitions that the execution agent reports back to the platform.
"""
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.execution import (
    OrderIntent,
    IntentStatus,
    FillSource,
    FillRecord,
)


def list_authorized_intents(
    db: Session,
    user_id: uuid.UUID,
) -> List[OrderIntent]:
    """Return all authorized (not yet submitted) intents for a user.

    Only returns intents that have not expired.
    """
    now = datetime.utcnow()
    return (
        db.query(OrderIntent)
        .filter(
            OrderIntent.user_id == user_id,
            OrderIntent.status == IntentStatus.authorized,
            OrderIntent.expires_at > now,
        )
        .order_by(OrderIntent.created_at.asc())
        .all()
    )


def agent_report_submitted(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
    broker_ref: str,
    submitted_at: Optional[datetime] = None,
) -> OrderIntent:
    """Agent reports that an order has been submitted to the broker.

    Transitions intent from 'authorized' to 'submitted'.
    Uses row-level locking to prevent duplicate submissions.
    """
    intent = (
        db.query(OrderIntent)
        .filter(
            OrderIntent.intent_id == intent_id,
            OrderIntent.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    if intent.status == IntentStatus.submitted and intent.broker_ref == broker_ref:
        return intent

    if intent.status not in (IntentStatus.authorized,):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot submit",
        )

    intent.status = IntentStatus.submitted
    intent.broker_ref = broker_ref
    intent.fill_source = FillSource.broker
    intent.updated_at = datetime.utcnow()
    if submitted_at:
        intent.broker_confirmed_at = submitted_at

    db.commit()
    db.refresh(intent)
    return intent


def agent_report_fill(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
    quantity: int,
    price: float,
    fill_time: datetime,
    broker_ref: str,
    commission: float = 0.0,
) -> FillRecord:
    """Agent reports a fill event from the broker.

    Creates a FillRecord with broker source. If total filled quantity
    reaches the order quantity, transitions intent to 'filled'.
    """
    intent = (
        db.query(OrderIntent)
        .filter(
            OrderIntent.intent_id == intent_id,
            OrderIntent.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    if intent.status not in (IntentStatus.submitted, IntentStatus.filled):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot report fill",
        )

    fill_record = FillRecord(
        order_intent_id=intent.id,
        quantity=quantity,
        price=price,
        fill_source=FillSource.broker,
        broker_ref=broker_ref,
        broker_confirmed_at=fill_time,
    )
    db.add(fill_record)
    db.flush()

    from sqlalchemy import func
    total_filled = (
        db.query(func.coalesce(func.sum(FillRecord.quantity), 0))
        .filter(FillRecord.order_intent_id == intent.id)
        .scalar()
    )

    if total_filled >= intent.quantity:
        intent.status = IntentStatus.filled
        intent.broker_confirmed_at = datetime.utcnow()

    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(fill_record)
    return fill_record


def agent_report_rejected(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
    reason: str,
) -> OrderIntent:
    """Agent reports that an order was rejected by the broker."""
    intent = (
        db.query(OrderIntent)
        .filter(
            OrderIntent.intent_id == intent_id,
            OrderIntent.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    if intent.status not in (IntentStatus.authorized, IntentStatus.submitted):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot reject",
        )

    intent.status = IntentStatus.rejected
    intent.rejection_reason = reason
    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)
    return intent


def agent_report_cancelled(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
    reason: str,
) -> OrderIntent:
    """Agent reports that an order was cancelled."""
    intent = (
        db.query(OrderIntent)
        .filter(
            OrderIntent.intent_id == intent_id,
            OrderIntent.user_id == user_id,
        )
        .with_for_update()
        .first()
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    if intent.status not in (IntentStatus.submitted,):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot cancel",
        )

    intent.status = IntentStatus.cancelled
    intent.rejection_reason = reason
    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)
    return intent
