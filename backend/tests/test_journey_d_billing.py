"""Journey D: Billing and subscription lifecycle.

Tests the complete journey from checkout through entitlement:
1. View available plans
2. Create checkout session
3. Process payment webhook
4. Activate subscription
5. Create entitlement
6. Enforce quota limits
7. Track usage

Uses test mode - no live Stripe credentials required.
"""
import hashlib
import hmac
import json
import uuid
from uuid import uuid4

import pytest

from app.database import SessionLocal
from app.models.billing import Plan, Subscription, SubscriptionStatus
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter
from app.models.user import User
from app.services.billing import activate_subscription, create_subscription


def _register_and_login(client, email, password="testpass123", display_name="Test"):
    """Register a new user and return Bearer auth headers."""
    client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "display_name": display_name,
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": password,
    })
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestJourneyDBillingLifecycle:
    """Journey D: Billing (checkout → webhook → subscription → entitlement)."""

    def test_plan_to_entitlement(self, client):
        """Complete journey from plan selection through entitlement creation."""
        # Step 1: Register and login
        headers = _register_and_login(client, "journey-d@test.com")

        # Step 2: Create a test plan (database doesn't have seeded plans)
        db = SessionLocal()
        try:
            plan = Plan(
                name="Test Pro Plan",
                description="Test plan for journey D",
                monthly_price=2999,
                currency="USD",
                features={"max_models": 10, "max_inference_per_day": 1000},
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id
        finally:
            db.close()

        # Step 3: View available plans
        plans_resp = client.get("/api/v1/plans")
        assert plans_resp.status_code == 200
        plans = plans_resp.json()["plans"]
        assert len(plans) > 0
        assert any(p["id"] == str(plan_id) for p in plans)

        # Get user_id for later assertions
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d@test.com").first()
            assert user is not None
            user_id = user.id
        finally:
            db.close()

        # Step 4: Create subscription
        sub_resp = client.post(
            f"/api/v1/subscriptions?plan_id={plan_id}",
            headers=headers,
        )
        assert sub_resp.status_code == 200
        sub_data = sub_resp.json()
        sub_id = sub_data["id"]
        assert sub_data["status"] == "trialing"
        assert sub_data["test_mode"] is True

        # Step 5: Verify subscription via GET
        my_sub_resp = client.get("/api/v1/subscriptions/me", headers=headers)
        assert my_sub_resp.status_code == 200
        assert my_sub_resp.json()["subscription"]["id"] == sub_id

        # Step 6: Simulate payment webhook
        webhook_secret = "test-webhook-secret"
        payload = json.dumps({
            "id": f"evt_{uuid4().hex[:12]}",
            "type": "payment.completed",
            "data": {"subscription_id": sub_id, "amount": 2999},
        })
        sig = hmac.new(webhook_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        webhook_resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sig,
            },
        )
        assert webhook_resp.status_code == 200
        assert webhook_resp.json()["received"] is True

        # Step 7: Activate subscription (simulating webhook processing)
        db = SessionLocal()
        try:
            activated = activate_subscription(db, uuid.UUID(sub_id), "pi_journey_d_test")
            assert activated.status == SubscriptionStatus.active

            # Verify entitlement was created
            entitlement = db.query(PilotEntitlement).filter(
                PilotEntitlement.user_id == user_id,
            ).first()
            assert entitlement is not None
            assert entitlement.pilot_access is True
            # monthly_price=2999 < 5000 → "starter" tier
            assert entitlement.research_run_limit == 50
            assert entitlement.ai_usage_limit == 20
        finally:
            db.close()

        # Step 8: Verify entitlement via API
        ent_resp = client.get("/api/v1/pilot/entitlements/me", headers=headers)
        assert ent_resp.status_code == 200
        entitlement_data = ent_resp.json()
        assert entitlement_data["pilot_access"] is True
        assert entitlement_data["research_run_limit"] == 50
        assert entitlement_data["ai_usage_limit"] == 20

    def test_quota_enforcement_with_entitlement(self, client):
        """Verify quota limits are enforced based on entitlement."""
        headers = _register_and_login(client, "journey-d-quota@test.com")

        # Create plan and subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d-quota@test.com").first()
            user_id = user.id

            plan = Plan(
                name=f"Quota Test Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id

            # Create and activate subscription
            sub = create_subscription(db, user_id, plan_id)
            activate_subscription(db, sub.id, "pi_quota_test")
            db.commit()
        finally:
            db.close()

        # Verify quota endpoint returns correct limits
        quota_resp = client.get("/api/v1/usage/quota?quota_type=ai_usage", headers=headers)
        assert quota_resp.status_code == 200
        quota_data = quota_resp.json()
        assert quota_data["quota_type"] == "ai_usage"
        assert quota_data["limit"] == 20  # starter tier
        assert quota_data["used"] == 0

    def test_usage_tracking(self, client):
        """Verify usage is tracked correctly."""
        headers = _register_and_login(client, "journey-d-usage@test.com")

        # Create plan and subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d-usage@test.com").first()
            user_id = user.id

            plan = Plan(
                name=f"Usage Test Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id

            sub = create_subscription(db, user_id, plan_id)
            activate_subscription(db, sub.id, "pi_usage_test")
            db.commit()
        finally:
            db.close()

        # Record usage
        usage_resp = client.post("/api/v1/usage/record?endpoint=/api/v1/ml/predict&model=test-model&tokens_used=100&cost=10", headers=headers)
        assert usage_resp.status_code == 200

        # Verify usage was recorded
        usage_data = usage_resp.json()
        assert usage_data["endpoint"] == "/api/v1/ml/predict"
        assert usage_data["tokens_used"] == 100

    def test_subscription_cancellation(self, client):
        """Verify subscription can be cancelled."""
        headers = _register_and_login(client, "journey-d-cancel@test.com")

        # Create plan and subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d-cancel@test.com").first()
            user_id = user.id

            plan = Plan(
                name=f"Cancel Test Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id

            sub = create_subscription(db, user_id, plan_id)
            activate_subscription(db, sub.id, "pi_cancel_test")
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        # Cancel subscription
        cancel_resp = client.post(
            f"/api/v1/subscriptions/{sub_id}/cancel",
            headers=headers,
        )
        assert cancel_resp.status_code == 200

        # Verify subscription status changed
        my_sub_resp = client.get("/api/v1/subscriptions/me", headers=headers)
        assert my_sub_resp.status_code == 200
        sub_data = my_sub_resp.json()["subscription"]
        assert sub_data["status"] in ["cancelled", "canceled"]

    def test_webhook_signature_verification(self, client):
        """Verify webhook rejects invalid signatures."""
        headers = _register_and_login(client, "journey-d-webhook@test.com")

        # Create subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d-webhook@test.com").first()
            user_id = user.id

            plan = Plan(
                name=f"Webhook Test Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id

            sub = create_subscription(db, user_id, plan_id)
            db.commit()
            sub_id = sub.id
        finally:
            db.close()

        # Send webhook with invalid signature
        payload = json.dumps({
            "id": f"evt_{uuid4().hex[:12]}",
            "type": "payment.completed",
            "data": {"subscription_id": sub_id, "amount": 999},
        })
        invalid_sig = "invalid-signature"

        webhook_resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": invalid_sig,
            },
        )
        # Should reject invalid signature
        assert webhook_resp.status_code in [400, 401, 403]

    def test_test_mode_explicit(self, client):
        """Verify test mode is explicitly shown in responses."""
        headers = _register_and_login(client, "journey-d-testmode@test.com")

        # Create plan and subscription
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "journey-d-testmode@test.com").first()
            user_id = user.id

            plan = Plan(
                name=f"Test Mode Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id

            sub = create_subscription(db, user_id, plan_id)
            db.commit()
        finally:
            db.close()

        # Verify test_mode is shown
        my_sub_resp = client.get("/api/v1/subscriptions/me", headers=headers)
        assert my_sub_resp.status_code == 200
        sub_data = my_sub_resp.json()["subscription"]
        assert "test_mode" in sub_data
        assert sub_data["test_mode"] is True

        # Verify operational status shows test mode
        ops_resp = client.get("/api/v1/operational-status")
        assert ops_resp.status_code == 200
        ops_data = ops_resp.json()
        assert "test_mode" in ops_data or "payment_mode" in ops_data
