from datetime import datetime
from typing import Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billing import Plan, Subscription, PaymentRecord, WebhookEventRecord, SubscriptionStatus
from app.models.pilot import PilotEntitlement, SupportTier
from app.providers.payment_adapter import PaymentAdapter, TestPaymentAdapter


from app.auth.entitlements import FREE_PLAN_ENTITLEMENT
from app.lib.timeutil import utcnow


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
        import os
        if os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_WEBHOOK_SECRET"):
            try:
                from app.providers.stripe_adapter import StripePaymentAdapter
                _payment_adapter = StripePaymentAdapter()
            except (ValueError, ImportError):
                _payment_adapter = TestPaymentAdapter()
        else:
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
        current_period_start=utcnow(),
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
    sub.current_period_start = utcnow()

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
    sub.cancelled_at = utcnow()
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
        processed_at=utcnow(),
    )
    db.add(record)
    db.flush()
    db.commit()
    return True


def record_api_call(
    db: Session,
    user_id: UUID,
    endpoint: str,
    model: Optional[str] = None,
    tokens_used: int = 0,
    cost: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> "UsageRecord":
    """Record an API call for usage tracking."""
    from app.models.billing import UsageRecord

    # Get user's subscription
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()

    record = UsageRecord(
        user_id=user_id,
        subscription_id=sub.id if sub else None,
        endpoint=endpoint,
        model=model,
        tokens_used=tokens_used,
        cost=cost,
        metadata_json=metadata,
    )
    db.add(record)
    db.flush()
    db.commit()
    return record


def check_quota(
    db: Session,
    user_id: UUID,
    quota_type: str,
    period: str = "monthly",
) -> Dict[str, Any]:
    """Check if user has remaining quota.

    Returns dict with 'allowed', 'used', 'limit', 'remaining'.
    """
    from app.models.quota import QuotaCounter
    from app.models.pilot import PilotEntitlement

    # Get entitlement limits
    entitlement = db.query(PilotEntitlement).filter(
        PilotEntitlement.user_id == user_id,
    ).first()

    if entitlement is None:
        # No entitlement - use free plan limits
        limit = FREE_PLAN_ENTITLEMENT.get(quota_type, 0)
    else:
        limit = getattr(entitlement, quota_type, None)
        if limit is None:
            return {"allowed": True, "used": 0, "limit": None, "remaining": None}

    # Get current usage
    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == period,
    ).first()

    used = counter.used if counter else 0
    remaining = max(0, limit - used)

    return {
        "allowed": used < limit,
        "used": used,
        "limit": limit,
        "remaining": remaining,
    }


def increment_quota(
    db: Session,
    user_id: UUID,
    quota_type: str,
    amount: int = 1,
    period: str = "monthly",
) -> int:
    """Increment quota usage counter.

    Returns new used amount.
    """
    from app.models.quota import QuotaCounter

    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == period,
    ).first()

    if counter is None:
        counter = QuotaCounter(
            user_id=user_id,
            quota_type=quota_type,
            period=period,
            used=amount,
        )
        db.add(counter)
    else:
        counter.used += amount

    db.flush()
    db.commit()
    return counter.used


def enforce_quota(
    db: Session,
    user_id: UUID,
    quota_type: str,
    period: str = "monthly",
) -> bool:
    """Check and increment quota atomically.

    Uses SELECT FOR UPDATE to prevent concurrent requests from exceeding
    the quota limit (TOCTOU race). Returns True if allowed.
    Raises HTTPException(429) if quota exceeded.
    """
    from fastapi import HTTPException
    from app.models.quota import QuotaCounter

    entitlement = db.query(PilotEntitlement).filter(
        PilotEntitlement.user_id == user_id,
    ).first()

    if entitlement is None:
        limit = FREE_PLAN_ENTITLEMENT.get(quota_type, 0)
    else:
        limit = getattr(entitlement, quota_type, None)
        if limit is None:
            counter = db.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == quota_type,
                QuotaCounter.period == period,
            ).first()
            if counter is None:
                counter = QuotaCounter(user_id=user_id, quota_type=quota_type, period=period, used=1)
                db.add(counter)
            else:
                counter.used += 1
            db.flush()
            db.commit()
            return True

    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == period,
    ).with_for_update().first()

    used = counter.used if counter else 0

    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "quota_type": quota_type,
                "used": used,
                "limit": limit,
            },
        )

    if counter is None:
        counter = QuotaCounter(user_id=user_id, quota_type=quota_type, period=period, used=1)
        db.add(counter)
    else:
        counter.used += 1

    db.flush()
    db.commit()
    return True


def get_usage_summary(
    db: Session,
    user_id: UUID,
    days: int = 30,
) -> Dict[str, Any]:
    """Get usage summary for the user."""
    from app.models.billing import UsageRecord
    from datetime import timedelta

    start_date = utcnow() - timedelta(days=days)

    usage_records = db.query(UsageRecord).filter(
        UsageRecord.user_id == user_id,
        UsageRecord.created_at >= start_date,
    ).all()

    total_calls = len(usage_records)
    total_tokens = sum(r.tokens_used for r in usage_records)
    total_cost = sum(r.cost for r in usage_records)

    # Group by endpoint
    by_endpoint = {}
    for record in usage_records:
        if record.endpoint not in by_endpoint:
            by_endpoint[record.endpoint] = {"calls": 0, "tokens": 0, "cost": 0}
        by_endpoint[record.endpoint]["calls"] += 1
        by_endpoint[record.endpoint]["tokens"] += record.tokens_used
        by_endpoint[record.endpoint]["cost"] += record.cost

    return {
        "period_days": days,
        "total_api_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost_cents": total_cost,
        "by_endpoint": by_endpoint,
    }
