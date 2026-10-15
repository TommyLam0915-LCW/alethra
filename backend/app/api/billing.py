from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Header
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
)
from app.services.feature_switches import get_operational_status

router = APIRouter(tags=["billing"])


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
    x_webhook_signature: str = Header(...),
    db: Session = Depends(get_db),
):
    if not x_webhook_signature:
        raise HTTPException(status_code=400, detail="Missing webhook signature")

    payload = (await request.body()).decode()
    adapter = get_payment_adapter()

    if not adapter.verify_webhook_signature(payload, x_webhook_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = adapter.parse_webhook_event(payload)

    is_duplicate = not process_webhook(db, event.id, event.event_type, event.data)
    db.commit()

    return {
        "received": True,
        "event_id": event.id,
        "duplicate": is_duplicate,
        "test_mode": True,
    }


@router.get("/operational-status")
def operational_status():
    return get_operational_status()
