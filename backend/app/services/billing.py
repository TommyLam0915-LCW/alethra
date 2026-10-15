from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billing import Plan, Subscription, PaymentRecord, WebhookEventRecord, SubscriptionStatus
from app.models.pilot import PilotEntitlement, SupportTier
from app.providers.payment_adapter import PaymentAdapter, TestPaymentAdapter


from app.auth.entitlements import FREE_PLAN_ENTITLEMENT


# Default entitlement limits keyed by rough price tier.
# Plans may override via their ``features`` JSONB (keys matching
# PilotEntitlement limit columns).
# The "free" tier reuses FREE_PLAN_ENTITLEMENT as the single source of truth.
_DEFAULT_LIMITS: Dict[str, Dict[str, Any]] = {
    "free": {
        **FREE_PLAN_ENTITLEMENT,
        "support_tier": SupportTier.standard,
    },
    "starter": {
        "research_run_limit": 50,
        "ai_usage_limit": 20,
        "export_limit": 10,
        "forward_simulation_limit": 10,
        "support_tier": SupportTier.standard,
    },
    "pro": {
        "research_run_limit": 200,
        "ai_usage_limit": 100,
        "export_limit": 50,
        "forward_simulation_limit": 50,
        "support_tier": SupportTier.priority,
    },
}


def _limits_for_plan(plan: Plan) -> Dict[str, Any]:
    """Return entitlement limits for *plan*.

    If the plan's ``features`` JSONB contains explicit limit keys they are used
    directly.  Otherwise the monthly price selects a default tier:
      * 0 → free
      * < 5000 → starter
      * >= 5000 → pro
    """
    features = plan.features or {}
    explicit_keys = {
        "research_run_limit", "ai_usage_limit", "export_limit",
        "forward_simulation_limit", "support_tier",
    }
    explicit = {k: features[k] for k in explicit_keys if k in features}
    if explicit:
        return explicit

    price = plan.monthly_price or 0
    if price == 0:
        tier = "free"
    elif price < 5000:
        tier = "starter"
    else:
        tier = "pro"
    return dict(_DEFAULT_LIMITS[tier])


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
    db.commit()
    return sub


def activate_subscription(
    db: Session,
    subscription_id: UUID,
    payment_intent_id: Optional[str] = None,
) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"Subscription not found: {subscription_id}")

    plan = db.query(Plan).filter(Plan.id == sub.plan_id).first()
    if not plan:
        raise ValueError(f"Plan not found: {sub.plan_id}")

    sub.status = SubscriptionStatus.active
    sub.payment_provider_id = payment_intent_id
    sub.current_period_start = datetime.utcnow()

    # --- Atomic entitlement link -------------------------------------------
    limits = _limits_for_plan(plan)
    entitlement = db.query(PilotEntitlement).filter(
        PilotEntitlement.user_id == sub.user_id,
    ).first()
    if entitlement is None:
        entitlement = PilotEntitlement(
            user_id=sub.user_id,
            pilot_access=True,
            **limits,
        )
        db.add(entitlement)
    else:
        entitlement.pilot_access = True
        for key, value in limits.items():
            setattr(entitlement, key, value)

    db.flush()
    db.commit()
    return sub


def cancel_subscription(db: Session, subscription_id: UUID) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"Subscription not found: {subscription_id}")

    sub.status = SubscriptionStatus.cancelled
    sub.cancelled_at = datetime.utcnow()
    db.flush()
    db.commit()
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
    db.commit()
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
    db.commit()
    return True
