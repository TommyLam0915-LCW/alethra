"""Tests for the billing system.

Covers:
- Plan listing
- Subscription lifecycle (create, activate, cancel)
- Payment intent creation
- Webhook processing (signature verification, duplicate detection)
- Entitlement linking on subscription activation
- Payment adapter (test mode)
- Stripe adapter initialization guards
- Operational status endpoint
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tests.conftest import TestSession
from app.models.user import User
from app.models.billing import (
    Plan,
    Subscription,
    SubscriptionStatus,
    PaymentRecord,
    WebhookEventRecord,
)
from app.models.pilot import PilotEntitlement
from app.services.billing import (
    create_subscription,
    activate_subscription,
    cancel_subscription,
    record_payment,
    process_webhook,
    get_payment_adapter,
    set_payment_adapter,
    _limits_for_plan,
)
from app.providers.payment_adapter import TestPaymentAdapter as MockPaymentAdapter, PaymentIntent


# ── Helpers ──

def _get_db() -> Session:
    return TestSession()


def _create_plan(db, name="Pro", price=9900, features=None):
    plan = Plan(
        name=name,
        description=f"{name} plan",
        monthly_price=price,
        currency="USD",
        features=features or {},
        is_active=True,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _create_user_via_api(client):
    email = f"billing-{uuid.uuid4().hex[:8]}@test.io"
    resp = client.post("/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "display_name": "Billing Test",
        "user_type": "individual",
    })
    assert resp.status_code == 201
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": "testpass123",
    })
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────
# 1. Plan listing
# ──────────────────────────────────────────────

class TestPlanListing:

    def test_list_plans_empty(self, client):
        resp = client.get("/api/v1/plans")
        assert resp.status_code == 200
        assert resp.json()["plans"] == []
        assert "test_mode" not in resp.json() or resp.json().get("disclaimer") is not None

    def test_list_plans_with_data(self, client):
        db = _get_db()
        _create_plan(db, "Free", 0)
        _create_plan(db, "Pro", 9900)

        resp = client.get("/api/v1/plans")
        assert resp.status_code == 200
        plans = resp.json()["plans"]
        assert len(plans) == 2
        names = {p["name"] for p in plans}
        assert "Free" in names
        assert "Pro" in names

    def test_list_plans_excludes_inactive(self, client):
        db = _get_db()
        _create_plan(db, "Active", 5000)
        inactive = Plan(name="Inactive", monthly_price=5000, is_active=False)
        db.add(inactive)
        db.commit()

        resp = client.get("/api/v1/plans")
        plans = resp.json()["plans"]
        assert len(plans) == 1
        assert plans[0]["name"] == "Active"


# ──────────────────────────────────────────────
# 2. Subscription lifecycle
# ──────────────────────────────────────────────

class TestSubscriptionLifecycle:

    def test_create_subscription(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()

        sub = create_subscription(db, user.id, plan.id)
        assert sub.status == SubscriptionStatus.trialing
        assert sub.plan_id == plan.id
        assert sub.user_id == user.id

    def test_create_subscription_duplicate_returns_existing(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()

        sub1 = create_subscription(db, user.id, plan.id)
        sub2 = create_subscription(db, user.id, plan.id)
        assert sub1.id == sub2.id

    def test_activate_subscription(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()

        sub = create_subscription(db, user.id, plan.id)
        activated = activate_subscription(db, sub.id, payment_intent_id="pi_test_123")

        assert activated.status == SubscriptionStatus.active
        assert activated.payment_provider_id == "pi_test_123"

    def test_activate_creates_entitlement(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()

        sub = create_subscription(db, user.id, plan.id)
        activate_subscription(db, sub.id)

        entitlement = db.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user.id
        ).first()
        assert entitlement is not None
        assert entitlement.pilot_access is True

    def test_cancel_subscription(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()

        sub = create_subscription(db, user.id, plan.id)
        cancelled = cancel_subscription(db, sub.id)

        assert cancelled.status == SubscriptionStatus.cancelled
        assert cancelled.cancelled_at is not None

    def test_get_my_subscription_none(self, client):
        auth = _create_user_via_api(client)
        resp = client.get("/api/v1/subscriptions/me", headers=auth)
        assert resp.status_code == 200
        assert resp.json()["subscription"] is None

    def test_get_my_subscription_exists(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()
        create_subscription(db, user.id, plan.id)

        resp = client.get("/api/v1/subscriptions/me", headers=auth)
        assert resp.status_code == 200
        sub = resp.json()["subscription"]
        assert sub is not None
        assert sub["status"] == "trialing"

    def test_cancel_via_api(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        user = db.query(User).filter(User.email.contains("billing-")).first()
        sub = create_subscription(db, user.id, plan.id)

        resp = client.post(
            f"/api/v1/subscriptions/{sub.id}/cancel",
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


# ──────────────────────────────────────────────
# 3. Payment intent
# ──────────────────────────────────────────────

class TestPaymentIntent:

    def test_create_payment_intent(self, client):
        auth = _create_user_via_api(client)
        resp = client.post(
            "/api/v1/payments/create-intent?amount=9900&currency=usd",
            headers=auth,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount"] == 9900
        assert data["currency"] == "usd"
        assert data["test_mode"] is True
        assert data["intent_id"].startswith("test_pi_")


# ──────────────────────────────────────────────
# 4. Webhook processing
# ──────────────────────────────────────────────

class TestWebhookProcessing:

    def test_process_webhook_new_event(self, client):
        db = _get_db()
        result = process_webhook(db, "evt_001", "payment_intent.succeeded", {"amount": 9900})
        assert result is True

        record = db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt_001"
        ).first()
        assert record is not None
        assert record.processed is True

    def test_process_webhook_duplicate(self, client):
        db = _get_db()
        process_webhook(db, "evt_002", "payment_intent.succeeded", {})
        result = process_webhook(db, "evt_002", "payment_intent.succeeded", {})
        assert result is False

    def test_webhook_endpoint_requires_signature(self, client):
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=b'{"type": "test"}',
        )
        assert resp.status_code == 422

    def test_webhook_endpoint_invalid_signature(self, client):
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=b'{"type": "test"}',
            headers={"X-Webhook-Signature": "invalid-sig"},
        )
        assert resp.status_code == 400

    def test_webhook_endpoint_valid_signature(self, client):
        adapter = get_payment_adapter()
        payload = json.dumps({"type": "payment_intent.succeeded", "data": {}})
        signature = hmac.new(
            adapter._webhook_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload.encode(),
            headers={"X-Webhook-Signature": signature},
        )
        assert resp.status_code == 200
        assert resp.json()["received"] is True
        assert resp.json()["test_mode"] is True


# ──────────────────────────────────────────────
# 5. Payment record
# ──────────────────────────────────────────────

class TestPaymentRecord:

    def test_record_payment(self, client):
        auth = _create_user_via_api(client)
        db = _get_db()
        user = db.query(User).filter(User.email.contains("billing-")).first()

        record = record_payment(
            db,
            user_id=user.id,
            subscription_id=None,
            amount=9900,
            currency="USD",
            status="succeeded",
            payment_intent_id="pi_test_abc",
        )
        assert record.amount == 9900
        assert record.provider == "test"
        assert record.test_mode is True


# ──────────────────────────────────────────────
# 6. Plan limits
# ──────────────────────────────────────────────

class TestPlanLimits:

    def test_free_plan_limits(self):
        db = _get_db()
        plan = _create_plan(db, "Free", 0)
        limits = _limits_for_plan(plan)
        assert limits["research_run_limit"] > 0
        assert limits["support_tier"] == "standard"

    def test_starter_plan_limits(self):
        db = _get_db()
        plan = _create_plan(db, "Starter", 2900)
        limits = _limits_for_plan(plan)
        assert limits["research_run_limit"] == 50
        assert limits["ai_usage_limit"] == 20

    def test_pro_plan_limits(self):
        db = _get_db()
        plan = _create_plan(db, "Pro", 9900)
        limits = _limits_for_plan(plan)
        assert limits["research_run_limit"] == 200
        assert limits["support_tier"] == "priority"

    def test_explicit_features_override(self):
        db = _get_db()
        plan = _create_plan(
            db, "Custom", 5000,
            features={"research_run_limit": 999, "support_tier": "vip"},
        )
        limits = _limits_for_plan(plan)
        assert limits["research_run_limit"] == 999
        assert limits["support_tier"] == "vip"


# ──────────────────────────────────────────────
# 7. Payment adapter
# ──────────────────────────────────────────────

class TestPaymentAdapter:

    def test_default_adapter_is_test(self):
        set_payment_adapter(None)
        adapter = get_payment_adapter()
        assert adapter.is_test_mode is True
        assert adapter.adapter_id == "test"

    def test_test_adapter_create_intent(self):
        adapter = MockPaymentAdapter()
        intent = adapter.create_payment_intent(9900, "usd", "user-123")
        assert intent.amount == 9900
        assert intent.status == "created"
        assert intent.id.startswith("test_pi_")

    def test_test_adapter_confirm(self):
        adapter = MockPaymentAdapter()
        intent = adapter.create_payment_intent(9900, "usd", "user-123")
        confirmed = adapter.confirm_payment(intent.id)
        assert confirmed.status == "succeeded"

    def test_test_adapter_webhook_signature(self):
        adapter = MockPaymentAdapter(webhook_secret="my-secret")
        payload = '{"type": "test"}'
        sig = hmac.new(
            b"my-secret", payload.encode(), hashlib.sha256
        ).hexdigest()
        assert adapter.verify_webhook_signature(payload, sig) is True
        assert adapter.verify_webhook_signature(payload, "wrong") is False


# ──────────────────────────────────────────────
# 8. Stripe adapter guards
# ──────────────────────────────────────────────

class TestStripeAdapterGuards:

    def test_stripe_adapter_requires_secret_key(self):
        from app.providers.stripe_adapter import StripePaymentAdapter
        with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
            StripePaymentAdapter(secret_key=None, webhook_secret="whsec_test")

    def test_stripe_adapter_requires_webhook_secret(self):
        from app.providers.stripe_adapter import StripePaymentAdapter
        with pytest.raises(ValueError, match="STRIPE_WEBHOOK_SECRET"):
            StripePaymentAdapter(secret_key="sk_test_xxx", webhook_secret=None)

    def test_fallback_to_test_when_no_stripe_env(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        set_payment_adapter(None)
        adapter = get_payment_adapter()
        assert adapter.adapter_id == "test"


# ──────────────────────────────────────────────
# 9. Operational status
# ──────────────────────────────────────────────

class TestOperationalStatus:

    def test_operational_status_endpoint(self, client):
        resp = client.get("/api/v1/operational-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "billing" in data or "payments" in data or isinstance(data, dict)
