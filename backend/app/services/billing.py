from datetime import datetime
from typing import Optional, Dict, Any
import uuid
from uuid import UUID

from sqlalchemy.exc import IntegrityError
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

    _activate_subscription_tx(db, sub)
    sub.payment_provider_id = payment_intent_id

    db.flush()
    db.commit()
    return sub


def _activate_subscription_tx(db: Session, sub: Subscription) -> Subscription:
    """Activate *sub* and upsert its PilotEntitlement. No commit — runs inside
    the caller's transaction so the state change lands atomically with the
    webhook event record."""
    plan = db.query(Plan).filter(Plan.id == sub.plan_id).first()
    if not plan:
        raise ValueError(f"Plan not found: {sub.plan_id}")

    sub.status = SubscriptionStatus.active
    sub.current_period_start = utcnow()

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
    return sub


def cancel_subscription(db: Session, subscription_id: UUID) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise ValueError(f"Subscription not found: {subscription_id}")

    _cancel_subscription_tx(db, sub)
    db.flush()
    db.commit()
    return sub


def _cancel_subscription_tx(db: Session, sub: Subscription) -> None:
    """Cancel *sub*. No commit — runs inside the caller's transaction."""
    sub.status = SubscriptionStatus.cancelled
    sub.cancelled_at = utcnow()


def record_payment(
    db: Session,
    user_id: UUID,
    subscription_id: Optional[UUID],
    amount: int,
    currency: str,
    status: str,
    payment_intent_id: Optional[str] = None,
) -> PaymentRecord:
    record = _record_payment_tx(
        db, user_id=user_id, subscription_id=subscription_id,
        amount=amount, currency=currency, status=status,
        payment_intent_id=payment_intent_id,
    )
    db.commit()
    return record


def _record_payment_tx(
    db: Session,
    user_id: UUID,
    subscription_id: Optional[UUID],
    amount: int,
    currency: str,
    status: str,
    payment_intent_id: Optional[str] = None,
) -> PaymentRecord:
    if payment_intent_id:
        existing = db.query(PaymentRecord).filter(
            PaymentRecord.payment_intent_id == payment_intent_id,
        ).first()
        if existing:
            return existing

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


# Event types that activate a subscription (idempotent: activating an active
# subscription re-applies entitlement limits).
_ACTIVATION_EVENT_TYPES = {
    "checkout.session.completed",
    "payment_intent.succeeded",
    "invoice.paid",
    "payment.completed",
}
# Payment failure puts the subscription past_due (not cancelled — the sender
# may retry the charge).
_PAST_DUE_EVENT_TYPES = {"invoice.payment_failed", "payment.failed"}
_CANCELLATION_EVENT_TYPES = {"customer.subscription.deleted", "subscription.cancelled"}


def _flatten_event_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a Stripe-style envelope (``data.object``) into a flat dict.

    Both envelopes are accepted: the test adapter sends flat ``data`` dicts,
    Stripe sends ``{"object": {...}}``. Object fields win on conflicts.
    """
    d = dict(payload or {})
    obj = payload.get("object") if isinstance(payload, dict) else None
    if isinstance(obj, dict):
        d.update(obj)
    return d


def _resolve_subscription(db: Session, data: Dict[str, Any]) -> Optional[Subscription]:
    """Find the subscription an event refers to.

    Accepted references: ``subscription_id``, ``client_reference_id``
    (Stripe checkout convention), or ``metadata.subscription_id``.
    Returns None when the event carries no resolvable reference or one that
    does not match an existing subscription.
    """
    candidates = [data.get("subscription_id"), data.get("client_reference_id")]
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        candidates.append(metadata.get("subscription_id"))

    for value in candidates:
        if not value:
            continue
        try:
            sid = uuid.UUID(str(value))
        except ValueError:
            continue
        sub = db.query(Subscription).filter(Subscription.id == sid).first()
        if sub:
            return sub
    return None


def _apply_webhook_event(db: Session, event_type: str, payload: Dict[str, Any]) -> None:
    """Apply the state change for a webhook event. No commit — the caller
    commits the event record and the state change together.

    Events with no resolvable subscription reference are acknowledged without
    state change (the sender must not retry them forever); unknown event types
    are likewise acknowledged. Only unexpected errors propagate, which rolls
    back the whole transaction so the sender's retry reprocesses the event.
    """
    data = _flatten_event_data(payload)

    if event_type in _ACTIVATION_EVENT_TYPES:
        sub = _resolve_subscription(db, data)
        if sub is None:
            return
        if sub.status == SubscriptionStatus.cancelled:
            return
        _activate_subscription_tx(db, sub)
        amount = data.get("amount") if data.get("amount") is not None else data.get("amount_paid")
        if isinstance(amount, int) and not isinstance(amount, bool):
            payment_intent_id = (
                data.get("payment_intent")
                or data.get("payment_intent_id")
                or data.get("intent_id")
            )
            _record_payment_tx(
                db,
                user_id=sub.user_id,
                subscription_id=sub.id,
                amount=amount,
                currency=data.get("currency") or "USD",
                status="succeeded",
                payment_intent_id=payment_intent_id,
            )
    elif event_type in _PAST_DUE_EVENT_TYPES:
        sub = _resolve_subscription(db, data)
        if sub is not None:
            sub.status = SubscriptionStatus.past_due
    elif event_type in _CANCELLATION_EVENT_TYPES:
        sub = _resolve_subscription(db, data)
        if sub is not None:
            _cancel_subscription_tx(db, sub)


def process_webhook(db: Session, event_id: str, event_type: str, payload: Dict[str, Any]) -> bool:
    """Record and apply a payment webhook event atomically.

    Returns True if the event was applied now, False if it was already
    recorded (duplicate delivery). The event record and the state change are
    committed together: if application raises, the whole transaction rolls
    back — including the event record — so the sender's retry reprocesses the
    event instead of finding it silently marked processed (retry-safe).
    """
    event_created = payload.get("created")
    if event_created:
        try:
            from datetime import datetime, timezone
            event_time = datetime.fromtimestamp(int(event_created), tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - event_time).days
            if age_days > 7:
                logger.warning("Processing stale webhook event %s (%d days old): %s", event_id, age_days, event_type)
        except (ValueError, TypeError, OSError):
            pass
    existing = db.query(WebhookEventRecord).filter(
        WebhookEventRecord.event_id == event_id
    ).first()
    if existing:
        return False

    record = WebhookEventRecord(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        processed=False,
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent delivery of the same event_id won the unique race.
        db.rollback()
        return False

    try:
        _apply_webhook_event(db, event_type, payload)
    except Exception:
        # Application failed: roll back the event record and any partial
        # state change so the sender's retry reprocesses the event.
        db.rollback()
        raise

    record.processed = True
    record.processed_at = utcnow()
    db.commit()
    return True
