from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billing import Plan, Subscription, PaymentRecord, WebhookEventRecord, SubscriptionStatus
from app.providers.payment_adapter import PaymentAdapter, TestPaymentAdapter


_payment_adapter: Optional[PaymentAdapter] = None


def get_payment_adapter() -> PaymentAdapter:
    global _payment_adapter
    if _payment_adapter is None:
        _payment_adapter = TestPaymentAdapter()
    return _payment_adapter


def set_payment_adapter(adapter: PaymentAdapter) -> None:
    global _payment_adapter
    _payment_adapter = adapter


def create_subscription(
    db: Session,
    user_id: UUID,
    plan_id: UUID,
) -> Subscription:
    existing = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if existing:
        return existing

    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise ValueError(f"Plan not found: {plan_id}")

    sub = Subscription(
        user_id=user_id,
        plan_id=plan_id,
        status=SubscriptionStatus.trialing,
        current_period_start=datetime.utcnow(),
    )
    db.add(sub)
    db.flush()
    return sub


def activate_subscription(
    db: Session,
    subscription_id: UUID,
    payment_intent_id: Optional[str] = None,
) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"Subscription not found: {subscription_id}")

    sub.status = SubscriptionStatus.active
    sub.payment_provider_id = payment_intent_id
    sub.current_period_start = datetime.utcnow()
    db.flush()
    return sub


def cancel_subscription(db: Session, subscription_id: UUID) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"Subscription not found: {subscription_id}")

    sub.status = SubscriptionStatus.cancelled
    sub.cancelled_at = datetime.utcnow()
    db.flush()
    return sub


def record_payment(
    db: Session,
    user_id: UUID,
    subscription_id: Optional[UUID],
    amount: int,
    currency: str,
    status: str,
    payment_intent_id: Optional[str] = None,
) -> PaymentRecord:
    adapter = get_payment_adapter()
    record = PaymentRecord(
        user_id=user_id,
        subscription_id=subscription_id,
        amount=amount,
        currency=currency,
        status=status,
        payment_intent_id=payment_intent_id,
        provider=adapter.adapter_id,
        test_mode=adapter.is_test_mode,
    )
    db.add(record)
    db.flush()
    return record


def process_webhook(db: Session, event_id: str, event_type: str, payload: Dict[str, Any]) -> bool:
    existing = db.query(WebhookEventRecord).filter(
        WebhookEventRecord.event_id == event_id
    ).first()
    if existing:
        return False

    record = WebhookEventRecord(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        processed=True,
        processed_at=datetime.utcnow(),
    )
    db.add(record)
    db.flush()
    return True
