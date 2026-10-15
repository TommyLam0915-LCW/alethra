import pytest
import json
import hashlib
import hmac
from uuid import uuid4

from app.models.billing import Plan, Subscription, PaymentRecord, WebhookEventRecord, SubscriptionStatus
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
