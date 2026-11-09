import pytest
import json
import hashlib
import hmac
from uuid import uuid4

from app.models.billing import Plan, Subscription, PaymentRecord, WebhookEventRecord, SubscriptionStatus
from app.models.pilot import PilotEntitlement
from app.models.user import User
from app.services.billing import (
    create_subscription,
    activate_subscription,
    cancel_subscription,
    record_payment,
    process_webhook,
    get_payment_adapter,
)
from app.services.feature_switches import (
    FeatureSwitch,
    is_enabled,
    set_switch,
    get_all_switches,
    get_operational_status,
)
from app.services.correlation import generate_correlation_id
from app.providers.payment_adapter import TestPaymentAdapter
from tests.conftest import TestSession


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def user(db):
    u = User(id=uuid4(), email=f"test-{uuid4().hex[:8]}@example.com", display_name="Test", password_hash="x")
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def plan(db):
    p = Plan(name=f"Test Plan {uuid4().hex[:8]}", monthly_price=999, currency="USD")
    db.add(p)
    db.flush()
    return p


class TestPaymentAdapterUnit:
    def test_create_intent(self):
        adapter = get_payment_adapter()
        intent = adapter.create_payment_intent(1000, "USD", "cust_1")
        assert intent.amount == 1000
        assert intent.status == "created"
        assert intent.id.startswith("test_pi_")

    def test_confirm_payment(self):
        adapter = get_payment_adapter()
        intent = adapter.create_payment_intent(500, "USD", "cust_1")
        confirmed = adapter.confirm_payment(intent.id)
        assert confirmed.status == "succeeded"

    def test_confirm_nonexistent_raises(self):
        adapter = get_payment_adapter()
        with pytest.raises(ValueError):
            adapter.confirm_payment("nonexistent")

    def test_webhook_signature(self):
        from app.providers.payment_adapter import TestPaymentAdapter as TestAdapter
        adapter = TestAdapter(webhook_secret="my-secret")
        payload = '{"type":"payment.completed"}'
        sig = hmac.new(b"my-secret", payload.encode(), hashlib.sha256).hexdigest()
        assert adapter.verify_webhook_signature(payload, sig) is True
        assert adapter.verify_webhook_signature(payload, "wrong") is False

    def test_parse_webhook_event(self):
        adapter = get_payment_adapter()
        payload = json.dumps({"type": "payment.completed", "data": {"amount": 100}})
        event = adapter.parse_webhook_event(payload)
        assert event.event_type == "payment.completed"
        assert event.data["amount"] == 100

    def test_test_mode(self):
        adapter = get_payment_adapter()
        assert adapter.is_test_mode is True
        assert adapter.adapter_id == "test"


class TestBillingService:
    def test_create_subscription(self, db, user, plan):
        sub = create_subscription(db, user.id, plan.id)
        assert sub.status == SubscriptionStatus.trialing
        assert sub.user_id == user.id

    def test_create_subscription_idempotent(self, db, user, plan):
        sub1 = create_subscription(db, user.id, plan.id)
        sub2 = create_subscription(db, user.id, plan.id)
        assert sub1.id == sub2.id

    def test_activate_subscription(self, db, user, plan):
        sub = create_subscription(db, user.id, plan.id)
        activated = activate_subscription(db, sub.id, "pi_test_123")
        assert activated.status == SubscriptionStatus.active

    def test_activate_subscription_creates_entitlement(self, db, user, plan):
        """Activating a subscription must atomically create/update PilotEntitlement."""
        sub = create_subscription(db, user.id, plan.id)
        activate_subscription(db, sub.id, "pi_test_456")

        ent = db.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user.id,
        ).first()
        assert ent is not None, "PilotEntitlement should be created on activation"
        assert ent.pilot_access is True
        # plan fixture: monthly_price=999 → "starter" tier
        assert ent.research_run_limit == 50
        assert ent.ai_usage_limit == 20
        assert ent.export_limit == 10
        assert ent.forward_simulation_limit == 10

    def test_activate_subscription_updates_existing_entitlement(self, db, user, plan):
        """Re-activating must update limits rather than creating a duplicate row."""
        # Pre-create an entitlement with different limits
        old_ent = PilotEntitlement(
            user_id=user.id,
            pilot_access=False,
            research_run_limit=1,
            ai_usage_limit=1,
            export_limit=1,
            forward_simulation_limit=1,
        )
        db.add(old_ent)
        db.commit()

        sub = create_subscription(db, user.id, plan.id)
        activate_subscription(db, sub.id, "pi_test_789")

        # Should still be exactly one entitlement for this user
        count = db.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user.id,
        ).count()
        assert count == 1

        ent = db.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user.id,
        ).first()
        assert ent.pilot_access is True
        assert ent.research_run_limit == 50  # starter tier from plan price

    def test_cancel_subscription(self, db, user, plan):
        sub = create_subscription(db, user.id, plan.id)
        cancelled = cancel_subscription(db, sub.id)
        assert cancelled.status == SubscriptionStatus.cancelled
        assert cancelled.cancelled_at is not None

    def test_record_payment(self, db, user):
        record = record_payment(db, user.id, None, 999, "USD", "succeeded")
        assert record.amount == 999
        assert record.test_mode is True

    def test_process_webhook(self, db):
        result = process_webhook(db, "evt_1", "payment.completed", {"amount": 100})
        assert result is True

    def test_process_webhook_duplicate(self, db):
        process_webhook(db, "evt_dup", "payment.completed", {})
        result = process_webhook(db, "evt_dup", "payment.completed", {})
        assert result is False


class TestFeatureSwitches:
    def test_demo_data_enabled_by_default(self):
        assert is_enabled(FeatureSwitch.DEMO_DATA) is True

    def test_live_broker_disabled_by_default(self):
        assert is_enabled(FeatureSwitch.LIVE_BROKER) is False

    def test_live_payments_disabled_by_default(self):
        assert is_enabled(FeatureSwitch.LIVE_PAYMENTS) is False

    def test_set_non_live_switch(self):
        original = is_enabled(FeatureSwitch.SANDBOX_BROKER)
        set_switch(FeatureSwitch.SANDBOX_BROKER, not original)
        assert is_enabled(FeatureSwitch.SANDBOX_BROKER) == (not original)
        set_switch(FeatureSwitch.SANDBOX_BROKER, original)

    def test_cannot_enable_live_switch_programmatically(self):
        with pytest.raises(ValueError):
            set_switch(FeatureSwitch.LIVE_BROKER, True)

    def test_operational_status(self):
        status = get_operational_status()
        assert "feature_switches" in status
        assert status["mode"] == "test"
        assert status["data_mode"] == "demo"
        assert status["broker_mode"] == "paper-only"
        assert status["ai_mode"] == "mock"


class TestCorrelationId:
    def test_generate_correlation_id(self):
        cid = generate_correlation_id()
        assert cid.startswith("corr-")
        assert len(cid) == 21

    def test_unique_ids(self):
        ids = {generate_correlation_id() for _ in range(100)}
        assert len(ids) == 100


class TestWebhookSignature:
    """Webhook endpoint must reject unsigned or tampered payloads."""

    def _build_payload(self):
        return json.dumps({
            "id": f"evt_{uuid4().hex[:12]}",
            "type": "payment.completed",
            "data": {"amount": 100},
        })

    def _sign(self, payload: str, secret: str = "test-webhook-secret") -> str:
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def test_missing_signature_returns_400(self, client):
        """Omitting the signature header entirely → 400 (checked explicitly
        since the endpoint accepts both Stripe-Signature and X-Webhook-Signature)."""
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=self._build_payload(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_empty_signature_returns_400(self, client):
        """Sending an empty signature value → 400."""
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=self._build_payload(),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "",
            },
        )
        assert resp.status_code == 400
        assert "signature" in resp.json()["detail"].lower()

    def test_invalid_signature_returns_400(self, client):
        """Wrong signature → 400."""
        payload = self._build_payload()
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "deadbeef",
            },
        )
        assert resp.status_code == 400

    def test_valid_signature_accepted(self, client):
        """Correctly signed payload → 200."""
        payload = self._build_payload()
        sig = self._sign(payload)
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sig,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["received"] is True
