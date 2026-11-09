"""Service layer for execution agent API endpoints.

These functions are called by the agent-facing API routes. They handle
the state transitions that the execution agent reports back to the platform.
"""
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.execution import (
    OrderIntent,
    IntentStatus,
    FillSource,
    FillRecord,
)
from app.lib.timeutil import utcnow


def broker_fill_key(broker_ref: str, fill_time: datetime, quantity: int) -> str:
    """Deterministic idempotency key for a broker fill report."""
    return f"{broker_ref}:{fill_time.isoformat()}:{quantity}"


def list_agent_intents(
    db: Session,
    user_id: uuid.UUID,
) -> List[OrderIntent]:
    """Return intents the agent must act on.

    Includes:
    - authorized intents that have not expired (awaiting submission)
    - submitted intents regardless of expiry (their orders may still fill at
      the broker; the agent must resume fill monitoring after a restart)

    Each intent carries its broker_ref when submitted, so a restarted agent
    can re-adopt the mapped broker order instead of resubmitting.
    """
    now = utcnow()
    return (
        db.query(OrderIntent)
        .filter(
            OrderIntent.user_id == user_id,
            OrderIntent.status.in_([IntentStatus.authorized, IntentStatus.submitted]),
            or_(
                OrderIntent.status == IntentStatus.submitted,
                OrderIntent.expires_at > now,
            ),
        )
        .order_by(OrderIntent.created_at.asc())
        .all()
    )


def list_claimed_broker_refs(db: Session, user_id: uuid.UUID) -> List[str]:
    """Return every broker_ref already mapped to one of the user's intents.

    Covers all statuses (including filled/cancelled/rejected), so a restarted
    agent can exclude orders that belong to other intents when matching
    unclaimed broker orders for lost-acknowledgement recovery.
    """
    rows = (
        db.query(OrderIntent.broker_ref)
        .filter(
            OrderIntent.user_id == user_id,
            OrderIntent.broker_ref.isnot(None),
        )
        .all()
    )
    return [row[0] for row in rows]


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
    intent.updated_at = utcnow()
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

    Idempotent: a fill re-reported with the same (broker_ref, fill_time,
    quantity) — e.g. after an agent restart re-polls the broker — returns the
    existing FillRecord instead of creating a duplicate, so filled quantity is
    never double-counted. Creates a FillRecord with broker source. If total
    filled quantity reaches the order quantity, transitions intent to 'filled'.
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

    key = broker_fill_key(broker_ref, fill_time, quantity)
    existing = (
        db.query(FillRecord)
        .filter(
            FillRecord.order_intent_id == intent.id,
            FillRecord.broker_fill_key == key,
        )
        .first()
    )
    if existing:
        return existing

    if intent.status not in (IntentStatus.submitted, IntentStatus.filled):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot report fill",
        )

    if intent.broker_ref and broker_ref != intent.broker_ref:
        raise HTTPException(
            status_code=400,
            detail=f"broker_ref mismatch: intent has '{intent.broker_ref}', fill reports '{broker_ref}'",
        )

    from sqlalchemy import func
    current_filled = (
        db.query(func.coalesce(func.sum(FillRecord.quantity), 0))
        .filter(FillRecord.order_intent_id == intent.id)
        .scalar()
    )
    if current_filled + quantity > intent.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Overfill: {current_filled} already filled, reporting {quantity} would exceed {intent.quantity}",
        )

    fill_record = FillRecord(
        order_intent_id=intent.id,
        quantity=quantity,
        price=price,
        fill_source=FillSource.broker,
        broker_ref=broker_ref,
        broker_fill_key=key,
        broker_confirmed_at=fill_time,
    )
    db.add(fill_record)
    db.flush()

    total_filled = current_filled + quantity

    if total_filled >= intent.quantity:
        intent.status = IntentStatus.filled
        intent.broker_confirmed_at = utcnow()

    intent.updated_at = utcnow()
    db.commit()
    db.refresh(fill_record)
    return fill_record


def agent_report_uncertain(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
    reason: str,
) -> OrderIntent:
    """Agent reports ambiguous broker state for an intent.

    Used when the agent cannot determine whether an order was submitted
    (e.g. multiple unclaimed matching broker orders after a restart). The
    intent is frozen in 'uncertain' for manual resolution — never resubmitted
    and never silently dropped.
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

    if intent.status not in (IntentStatus.authorized, IntentStatus.submitted):
        raise HTTPException(
            status_code=409,
            detail=f"Intent is '{intent.status.value}', cannot mark uncertain",
        )

    intent.status = IntentStatus.uncertain
    intent.rejection_reason = reason
    intent.updated_at = utcnow()
    db.commit()
    db.refresh(intent)
    return intent


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
    intent.updated_at = utcnow()
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
    intent.updated_at = utcnow()
    db.commit()
    db.refresh(intent)
    return intent
