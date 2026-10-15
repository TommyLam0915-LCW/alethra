import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.execution import (
    OrderIntent,
    IntentStatus,
    IntentSide,
    IntentOrderType,
    FillReconciliation,
)
from app.models.paper_portfolio import PaperPortfolio


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
) -> OrderIntent:
    """Create a new order intent with a unique intent_id (idempotency key) and authorization token."""
    # Verify portfolio ownership
    portfolio = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this portfolio")

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
    """Authorize an intent. Validates ownership, expiry, and not already authorized."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
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
) -> OrderIntent:
    """Submit an authorized intent. In offline mode, immediately simulates fill."""
    from app.services.kill_switch import kill_switch

    if kill_switch.is_active():
        raise HTTPException(status_code=503, detail="Execution halted: kill switch is active")

    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
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
    """Simulate an offline fill for sandbox/research mode."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if intent.status != IntentStatus.submitted:
        raise HTTPException(status_code=400, detail=f"Intent is '{intent.status.value}', must be 'submitted'")

    intent.status = IntentStatus.filled
    intent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(intent)
    return intent


def cancel_intent(
    db: Session,
    intent_id: str,
    user_id: uuid.UUID,
) -> OrderIntent:
    """Cancel an intent. If pending/authorized → cancelled; if submitted → rejected."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
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
    """Create a FillReconciliation record comparing expected vs filled quantity."""
    intent = db.query(OrderIntent).filter(
        OrderIntent.intent_id == intent_id,
    ).first()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")

    filled_qty = intent.quantity if intent.status == IntentStatus.filled else 0
    expected_qty = intent.quantity
    discrepancy = filled_qty != expected_qty

    reconciliation = FillReconciliation(
        order_intent_id=intent.id,
        expected_quantity=expected_qty,
        filled_quantity=filled_qty,
        discrepancy=discrepancy,
    )
    db.add(reconciliation)
    db.commit()
    db.refresh(reconciliation)
    return reconciliation
