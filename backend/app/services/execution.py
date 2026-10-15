import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.execution import (
    OrderIntent,
    IntentStatus,
    IntentSide,
    IntentOrderType,
    FillSource,
    FillRecord,
    FillReconciliation,
)
from app.models.kill_switch import KillSwitchScope
from app.models.paper_portfolio import PaperPortfolio
from app.services.kill_switch import is_kill_switch_active


INTENT_TTL_MINUTES = 5


def create_order_intent(
    db: Session,
    user_id: uuid.UUID,
    portfolio_id: uuid.UUID,
    symbol: str,
    side: str,
    quantity: int,
    order_type: str = "MARKET",
    limit_price: Optional[float] = None,
    idempotency_key: Optional[str] = None,
) -> OrderIntent:
    """Create a new order intent with a unique intent_id and authorization token.

    If *idempotency_key* is supplied and already exists for this user, the
    existing intent is returned instead of creating a duplicate (idempotency).
    """
    # Verify portfolio ownership
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this portfolio")

    # Idempotency: if a key was supplied and already used by this user, return existing
    if idempotency_key:
        existing = db.query(OrderIntent).filter(
            OrderIntent.idempotency_key == idempotency_key,
            OrderIntent.user_id == user_id,
        ).first()
        if existing:
            return existing

    now = datetime.utcnow()
    intent = OrderIntent(
        user_id=user_id,
        portfolio_id=portfolio_id,
        intent_id=str(uuid.uuid4()),
        symbol=symbol,
        side=IntentSide(side),
        quantity=quantity,
        order_type=IntentOrderType(order_type),
        limit_price=limit_price,
        status=IntentStatus.pending,
        authorization_token=secrets.token_urlsafe(32),
        expires_at=now + timedelta(minutes=INTENT_TTL_MINUTES),
        idempotency_key=idempotency_key,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def authorize_intent(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
) -> OrderIntent:
    """Authorize an intent. Uses row-level locking (with_for_update) to prevent
    concurrent read-check-write races."""
    # Row-level lock: with_for_update() issues SELECT ... FOR UPDATE
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).with_for_update().first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if intent.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if intent.status != IntentStatus.pending:
        raise HTTPException(status_code=400, detail=f"Intent is '{intent.status.value}', cannot authorize")
    if datetime.utcnow() > intent.expires_at:
        raise HTTPException(status_code=400, detail="Intent has expired")

    intent.status = IntentStatus.authorized
    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)
    return intent


def submit_intent(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
) -> OrderIntent:
    """Submit an authorized intent. Uses row-level locking to prevent
    concurrent read-check-write races.
    """
    if is_kill_switch_active(db, KillSwitchScope.execution):
        raise HTTPException(status_code=503, detail="Execution halted: kill switch is active")

    # Row-level lock: with_for_update() prevents concurrent status transitions
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).with_for_update().first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if intent.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if intent.status != IntentStatus.authorized:
        raise HTTPException(status_code=400, detail=f"Intent is '{intent.status.value}', must be 'authorized' to submit")
    if datetime.utcnow() > intent.expires_at:
        raise HTTPException(status_code=400, detail="Intent has expired")

    intent.status = IntentStatus.submitted
    intent.broker_ref = f"OFFLINE-{uuid.uuid4().hex[:12]}"
    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)

    # In offline mode, immediately simulate fill
    simulate_offline_fill(db, intent.intent_id)

    db.refresh(intent)
    return intent


def simulate_offline_fill(
    db: Session,
    intent_id: str,
) -> OrderIntent:
    """Simulate an offline fill for sandbox/research mode.

    Creates a FillRecord flagged as `simulated` so reconciliation can
    distinguish it from broker-confirmed fills.
    """
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if intent.status != IntentStatus.submitted:
        raise HTTPException(status_code=400, detail=f"Intent is '{intent.status.value}', must be 'submitted'")

    now = datetime.utcnow()
    intent.status = IntentStatus.filled
    intent.fill_source = FillSource.simulated
    intent.updated_at = now

    # Create a FillRecord to track this simulated fill
    fill_record = FillRecord(
        order_intent_id=intent.id,
        quantity=intent.quantity,
        fill_source=FillSource.simulated,
        broker_ref=intent.broker_ref,
    )
    db.add(fill_record)
    db.commit()
    db.refresh(intent)
    return intent


def cancel_intent(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
) -> OrderIntent:
    """Cancel an intent. If pending/authorized -> cancelled; if submitted -> rejected."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).with_for_update().first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if intent.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if intent.status in (IntentStatus.pending, IntentStatus.authorized):
        intent.status = IntentStatus.cancelled
    elif intent.status == IntentStatus.submitted:
        intent.status = IntentStatus.rejected
        intent.rejection_reason = "Cancelled after submission"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel intent in '{intent.status.value}' status",
        )

    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)
    return intent


def reconcile_order(
    db: Session,
    intent_id: str,
) -> FillReconciliation:
    """Create a FillReconciliation record by summing FillRecords against expected quantity.

    Reconciliation now compares the sum of individual FillRecord quantities
    (tracked per-fill) against the order's expected quantity, rather than
    merely checking whether status == filled.
    """
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    # Sum actual fills from FillRecord table
    filled_qty = (
        db.query(func.coalesce(func.sum(FillRecord.quantity), 0))
        .filter(FillRecord.order_intent_id == intent.id)
        .scalar()
    )
    expected_qty = intent.quantity
    discrepancy = filled_qty != expected_qty

    reconciliation = FillReconciliation(
        order_intent_id=intent.id,
        expected_quantity=expected_qty,
        filled_quantity=int(filled_qty),
        discrepancy=discrepancy,
    )
    db.add(reconciliation)
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def get_fill_records(
    db: Session,
    intent_id: str,
) -> list:
    """Return all FillRecords for a given intent."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    return (
        db.query(FillRecord)
        .filter(FillRecord.order_intent_id == intent.id)
        .order_by(FillRecord.created_at.asc())
        .all()
    )
