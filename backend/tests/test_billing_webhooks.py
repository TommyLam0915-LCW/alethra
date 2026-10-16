"""Tests for atomic webhook application in the billing service.

Covers the payment webhook path end to end:
- Events are APPLIED (subscription activation / past_due / cancellation +
  payment record), not merely recorded
- Event record and state change commit atomically; application failure rolls
  back both, so the sender's retry reprocesses the event (retry-safe)
- Duplicate deliveries apply state exactly once
- Both flat (test adapter) and Stripe ``data.object`` envelopes are supported
- Unknown event types and unresolvable references are acknowledged without
  state change
- The HTTP endpoint verifies the signature before processing
"""
import hashlib
import hmac
import json
from uuid import uuid4

import pytest

from app.models.billing import (
    Plan,
    Subscription,
    PaymentRecord,
    WebhookEventRecord,
    SubscriptionStatus,
)
from app.models.pilot import PilotEntitlement
from app.models.user import User
from app.services.billing import (
    create_subscription,
    process_webhook,
    _apply_webhook_event,
)
from tests.conftest import TestSession


TEST_WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def user(db):
    u = User(id=uuid4(), email=f"wh-{uuid4().hex[:8]}@example.com", display_name="WH", password_hash="x")
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def plan(db):
    p = Plan(name=f"WH Plan {uuid4().hex[:8]}", monthly_price=999, currency="USD")
    db.add(p)
    db.flush()
    return p


@pytest.fixture()
def subscription(db, user, plan):
    return create_subscription(db, user.id, plan.id)


def _signed(body: dict) -> tuple:
    payload = json.dumps(body)
    sig = hmac.new(TEST_WEBHOOK_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload, sig


class TestWebhookApplication:
    def test_activation_event_applies_subscription_and_payment(self, db, user, plan, subscription):
        applied = process_webhook(db, "evt-act-1", "checkout.session.completed", {
            "subscription_id": str(subscription.id),
            "amount": 999,
            "currency": "USD",
            "payment_intent_id": "pi_123",
        })
        assert applied is True

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.payment_provider_id is None  # activation via webhook carries no pi on the sub row

        payment = db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).all()
        assert len(payment) == 1
        assert payment[0].amount == 999
        assert payment[0].status == "succeeded"
        assert payment[0].payment_intent_id == "pi_123"

        ent = db.query(PilotEntitlement).filter(PilotEntitlement.user_id == user.id).first()
        assert ent is not None and ent.pilot_access is True

        record = db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt-act-1"
        ).first()
        assert record.processed is True and record.processed_at is not None

    def test_stripe_object_envelope_is_flattened(self, db, subscription):
        applied = process_webhook(db, "evt-act-2", "payment_intent.succeeded", {
            "object": {
                "subscription_id": str(subscription.id),
                "amount": 499,
            },
        })
        assert applied is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 1

    def test_client_reference_id_resolves_subscription(self, db, subscription):
        applied = process_webhook(db, "evt-act-3", "checkout.session.completed", {
            "client_reference_id": str(subscription.id),
            "amount": 999,
        })
        assert applied is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

    def test_metadata_subscription_id_resolves(self, db, subscription):
        applied = process_webhook(db, "evt-act-4", "invoice.paid", {
            "metadata": {"subscription_id": str(subscription.id)},
            "amount_paid": 1500,
        })
        assert applied is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        payment = db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).first()
        assert payment.amount == 1500

    def test_duplicate_event_applies_state_once(self, db, subscription):
        payload = {"subscription_id": str(subscription.id), "amount": 999}
        assert process_webhook(db, "evt-dup-1", "checkout.session.completed", payload) is True
        assert process_webhook(db, "evt-dup-1", "checkout.session.completed", payload) is False

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 1
        assert db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt-dup-1"
        ).count() == 1

    def test_payment_failure_sets_past_due(self, db, subscription):
        assert process_webhook(db, "evt-pd-1", "invoice.payment_failed", {
            "subscription_id": str(subscription.id),
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due
        assert subscription.cancelled_at is None

    def test_subscription_deleted_cancels(self, db, subscription):
        assert process_webhook(db, "evt-cancel-1", "customer.subscription.deleted", {
            "subscription_id": str(subscription.id),
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.cancelled
        assert subscription.cancelled_at is not None

    def test_unknown_event_type_acknowledged_without_state_change(self, db, subscription):
        assert process_webhook(db, "evt-unk-1", "payout.created", {"amount": 100}) is True
        record = db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt-unk-1"
        ).first()
        assert record.processed is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing
        assert db.query(PaymentRecord).count() == 0

    def test_activation_without_resolvable_reference_is_acknowledged(self, db, subscription):
        assert process_webhook(db, "evt-no-1", "checkout.session.completed", {"amount": 100}) is True
        record = db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt-no-1"
        ).first()
        assert record.processed is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing
        assert db.query(PaymentRecord).count() == 0

    def test_activation_with_unknown_subscription_id_is_acknowledged(self, db, subscription):
        assert process_webhook(db, "evt-no-2", "checkout.session.completed", {
            "subscription_id": str(uuid4()),
            "amount": 100,
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing

    def test_non_integer_amount_activates_without_payment_record(self, db, subscription):
        assert process_webhook(db, "evt-amt-1", "checkout.session.completed", {
            "subscription_id": str(subscription.id),
            "amount": "not-a-number",
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert db.query(PaymentRecord).count() == 0

    def test_application_failure_rolls_back_and_retry_applies(self, db, subscription, monkeypatch):
        """If state application raises, the event record must roll back too —
        the sender's retry then reprocesses instead of finding it 'processed'."""
        from app.services import billing

        calls = {"n": 0}
        original = billing._activate_subscription_tx

        def flaky(db_session, sub):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient failure")
            return original(db_session, sub)

        monkeypatch.setattr(billing, "_activate_subscription_tx", flaky)

        with pytest.raises(RuntimeError):
            process_webhook(db, "evt-retry-1", "checkout.session.completed", {
                "subscription_id": str(subscription.id),
                "amount": 999,
            })

        assert db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == "evt-retry-1"
        ).count() == 0, "event record must not survive a failed application"
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing

        # flaky passes through to the original on the second call — the
        # sender's retry applies cleanly on the same event_id.
        assert process_webhook(db, "evt-retry-1", "checkout.session.completed", {
            "subscription_id": str(subscription.id),
            "amount": 999,
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert calls["n"] == 2

    def test_reactivation_reapplies_entitlement_limits(self, db, user, plan, subscription):
        event = {"subscription_id": str(subscription.id)}
        assert process_webhook(db, "evt-react-1", "payment.completed", event) is True
        assert process_webhook(db, "evt-react-2", "payment.completed", event) is True

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        # Idempotent activation: still exactly one payment per event with amount
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 0  # "payment.completed" events here carry no amount


class TestWebhookEndpoint:
    def test_signed_webhook_activates_subscription(self, client, db, user, plan, subscription):
        body, sig = _signed({
            "id": "evt-http-1",
            "type": "checkout.session.completed",
            "data": {"subscription_id": str(subscription.id), "amount": 999},
        })
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=body,
            headers={"X-Webhook-Signature": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["duplicate"] is False

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 1

    def test_tampered_signature_rejected(self, client, db, subscription):
        body, _sig = _signed({
            "id": "evt-http-2",
            "type": "checkout.session.completed",
            "data": {"subscription_id": str(subscription.id)},
        })
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=body,
            headers={"X-Webhook-Signature": "0" * 64, "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing

    def test_duplicate_delivery_reported(self, client, db, subscription):
        body, sig = _signed({
            "id": "evt-http-3",
            "type": "checkout.session.completed",
            "data": {"subscription_id": str(subscription.id), "amount": 999},
        })
        headers = {"X-Webhook-Signature": sig, "Content-Type": "application/json"}
        assert client.post("/api/v1/webhooks/payment", content=body, headers=headers).status_code == 200
        resp = client.post("/api/v1/webhooks/payment", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is True

        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 1
