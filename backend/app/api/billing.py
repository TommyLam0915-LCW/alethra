from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.billing import Plan, Subscription, PaymentRecord
from app.auth.jwt import get_current_user
from app.services.billing import (
    get_payment_adapter,
    create_subscription,
    activate_subscription,
    cancel_subscription,
    record_payment,
    process_webhook,
    record_api_call,
    check_quota,
    get_usage_summary,
)
from app.services.stripe_service import get_stripe_service
from app.services.feature_switches import get_operational_status
from app.config import get_settings
from urllib.parse import urlparse

router = APIRouter(tags=["billing"])


def _validate_redirect_url(url: str, param_name: str) -> None:
    """Validate that a redirect URL belongs to an allowed origin."""
    allowed_origins = get_settings().cors_origins_list
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in allowed_origins:
        raise HTTPException(
            status_code=400,
            detail=f"{param_name} must be an allowed origin ({', '.join(allowed_origins)})",
        )


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    plans = db.query(Plan).filter(Plan.is_active == True).all()
    return {
        "plans": [
            {
                "id": str(p.id),
                "name": p.name,
                "description": p.description,
                "monthly_price": p.monthly_price,
                "currency": p.currency,
                "features": p.features,
            }
            for p in plans
        ],
        "disclaimer": "Test mode — no real payments are processed.",
        "disclaimer_zh": "測試模式 — 不會處理真實付款。",
    }


@router.post("/subscriptions")
def create_sub(
    plan_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = create_subscription(db, current_user.id, plan_id)
    return {
        "id": str(sub.id),
        "plan_id": str(sub.plan_id),
        "status": sub.status,
        "test_mode": True,
    }


@router.get("/subscriptions/me")
def get_my_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = db.query(Subscription).filter(Subscription.user_id == current_user.id).first()
    if not sub:
        return {"subscription": None, "test_mode": True}
    return {
        "subscription": {
            "id": str(sub.id),
            "plan_id": str(sub.plan_id),
            "status": sub.status,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        },
        "test_mode": True,
    }


@router.post("/subscriptions/{subscription_id}/cancel")
def cancel_sub(
    subscription_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = db.query(Subscription).filter(
        Subscription.id == subscription_id,
        Subscription.user_id == current_user.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    sub = cancel_subscription(db, subscription_id)
    return {"id": str(sub.id), "status": sub.status}


@router.post("/payments/create-intent")
def create_payment_intent(
    amount: int,
    currency: str = "USD",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    adapter = get_payment_adapter()
    if not adapter.is_test_mode:
        raise HTTPException(status_code=403, detail="Live payments are not authorized.")
    intent = adapter.create_payment_intent(amount, currency, str(current_user.id))
    return {
        "intent_id": intent.id,
        "amount": intent.amount,
        "currency": intent.currency,
        "status": intent.status,
        "test_mode": True,
    }


@router.post("/webhooks/payment")
async def handle_payment_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    # Real Stripe deliveries carry the signature in the Stripe-Signature
    # header; the test adapter contract uses X-Webhook-Signature.
    signature = (
        request.headers.get("stripe-signature")
        or request.headers.get("x-webhook-signature")
    )
    if not signature:
        raise HTTPException(status_code=400, detail="Missing webhook signature")

    raw_body = await request.body()
    payload_text = raw_body.decode("utf-8", errors="replace")
    adapter = get_payment_adapter()

    verified_event = adapter.verify_webhook_signature(payload_text, signature)
    if not verified_event:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if hasattr(verified_event, "type"):
        # Verified provider Event object (e.g. Stripe SDK): the object payload
        # (data.object) is what process_webhook applies.
        event_id = verified_event.id
        event_type = verified_event.type
        event_payload = verified_event.data.get("object", {}) if hasattr(verified_event.data, "get") else {}
    else:
        event = adapter.parse_webhook_event(payload_text)
        event_id = event.id
        event_type = event.event_type
        event_payload = event.data

    # process_webhook commits the event record and its state change atomically.
    is_duplicate = not process_webhook(db, event_id, event_type, event_payload)

    return {
        "received": True,
        "event_id": event_id,
        "duplicate": is_duplicate,
        "test_mode": True,
    }


@router.get("/operational-status")
def operational_status():
    return get_operational_status()


@router.post("/checkout")
def create_checkout(
    plan_id: UUID,
    success_url: str = Query(..., description="URL to redirect after successful checkout"),
    cancel_url: str = Query(..., description="URL to redirect if checkout is cancelled"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Checkout session for subscription."""
    _validate_redirect_url(success_url, "success_url")
    _validate_redirect_url(cancel_url, "cancel_url")

    stripe_service = get_stripe_service()

    # Get plan
    plan = db.query(Plan).filter(Plan.id == plan_id, Plan.is_active == True).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Create checkout session
    try:
        session = stripe_service.create_checkout_session(
            user=current_user,
            plan=plan,
            success_url=success_url,
            cancel_url=cancel_url,
            db=db,
        )
        return {
            "session_id": session["session_id"],
            "session_url": session["session_url"],
            "customer_id": session["customer_id"],
            "test_mode": session["test_mode"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portal")
def create_portal(
    return_url: str = Query(..., description="URL to return to after portal"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a Stripe Customer Portal session."""
    _validate_redirect_url(return_url, "return_url")

    stripe_service = get_stripe_service()

    # Get customer ID (reuses the stored per-user customer id when present)
    customer_id = stripe_service.create_customer(current_user, db=db)

    # Create portal session
    portal = stripe_service.create_portal_session(
        customer_id=customer_id,
        return_url=return_url,
    )

    return {
        "portal_url": portal["portal_url"],
        "test_mode": portal["test_mode"],
    }


@router.get("/subscription")
def get_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current subscription details."""
    sub = db.query(Subscription).filter(Subscription.user_id == current_user.id).first()
    if not sub:
        return {"subscription": None, "test_mode": True}

    # Get plan details
    plan = db.query(Plan).filter(Plan.id == sub.plan_id).first()

    return {
        "subscription": {
            "id": str(sub.id),
            "plan_id": str(sub.plan_id),
            "plan_name": plan.name if plan else None,
            "status": sub.status,
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
        },
        "test_mode": True,
    }


@router.get("/usage/summary")
def get_usage(
    days: int = Query(30, ge=1, le=365, description="Number of days to summarize"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get usage summary for the current user."""
    summary = get_usage_summary(db, current_user.id, days)
    return {
        "usage": summary,
        "test_mode": True,
    }


@router.get("/usage/quota")
def get_quota_status(
    quota_type: str = Query(..., description="Quota type to check (e.g., research_run_limit, ai_usage_limit)"),
    period: str = Query("monthly", description="Period (monthly, lifetime)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check quota status for the current user."""
    quota_status = check_quota(db, current_user.id, quota_type, period)
    return {
        "quota_type": quota_type,
        "period": period,
        **quota_status,
        "test_mode": True,
    }


@router.post("/usage/record")
def record_usage(
    endpoint: str,
    model: str = None,
    tokens_used: int = 0,
    cost: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record an API call for usage tracking."""
    record = record_api_call(
        db=db,
        user_id=current_user.id,
        endpoint=endpoint,
        model=model,
        tokens_used=tokens_used,
        cost=cost,
    )
    return {
        "id": str(record.id),
        "endpoint": record.endpoint,
        "tokens_used": record.tokens_used,
        "cost": record.cost,
        "created_at": record.created_at.isoformat(),
        "test_mode": True,
    }
