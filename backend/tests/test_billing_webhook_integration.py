"""Integration tests for the billing webhook path.

Covers the payment webhook path end to end:
- Events are APPLIED (subscription activation / past_due / cancellation +
  payment record + entitlement), not merely recorded
- Event record and state change commit atomically; application failure rolls
  back both, so the sender's retry reprocesses the event (retry-safe)
- Duplicate deliveries apply state exactly once (retry-safe usage)
- Both flat (test adapter) and Stripe ``data.object`` envelopes are supported
- checkout/subscription/customer mapping: client_reference_id,
  metadata.subscription_id, and stored ``stripe_subscription_id`` all resolve;
  activation persists the Stripe subscription and customer ids
- The HTTP endpoint verifies the signature over the raw body before
  processing, including the real Stripe SDK signature scheme
"""
import hashlib
import hmac
import json
import time
import uuid
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


def _stripe_signed_header(payload: str, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header the same way stripe.Webhook does."""
    t = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{t}.{payload}"
    v1 = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


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
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).count() == 0  # "payment.completed" events here carry no amount

    def test_stale_event_logs_warning_but_is_processed(self, db, subscription, caplog):
        stale_created = int(time.time()) - 30 * 24 * 3600
        applied = process_webhook(db, "evt-stale-1", "checkout.session.completed", {
            "subscription_id": str(subscription.id),
            "amount": 999,
            "created": stale_created,
        })
        assert applied is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active

        warnings = [r for r in caplog.records if "stale webhook event" in r.getMessage()]
        assert len(warnings) == 1
        assert "evt-stale-1" in warnings[0].getMessage()


class TestSubscriptionCustomerMapping:
    def test_activation_persists_stripe_subscription_and_customer_ids(self, db, user, plan, subscription):
        assert process_webhook(db, "evt-map-1", "checkout.session.completed", {
            "client_reference_id": str(subscription.id),
            "subscription": "sub_stripe_abc",
            "customer": "cus_stripe_abc",
            "amount_total": 999,
        }) is True

        db.refresh(subscription)
        assert subscription.stripe_subscription_id == "sub_stripe_abc"
        db.refresh(user)
        assert user.stripe_customer_id == "cus_stripe_abc"

    def test_stripe_subscription_id_resolves_later_events(self, db, user, plan, subscription):
        assert process_webhook(db, "evt-map-2", "checkout.session.completed", {
            "client_reference_id": str(subscription.id),
            "subscription": "sub_stripe_xyz",
        }) is True

        # invoice.payment_failed references only the Stripe subscription id
        assert process_webhook(db, "evt-map-3", "invoice.payment_failed", {
            "object": {"id": "in_123", "subscription": "sub_stripe_xyz"},
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.past_due

        # customer.subscription.deleted references only the Stripe subscription id
        assert process_webhook(db, "evt-map-4", "customer.subscription.deleted", {
            "object": {"id": "sub_stripe_xyz"},
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.cancelled

    def test_unmapped_stripe_subscription_id_is_acknowledged(self, db, subscription):
        assert process_webhook(db, "evt-map-5", "invoice.payment_failed", {
            "object": {"id": "in_456", "subscription": "sub_unknown"},
        }) is True
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing


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

    def test_missing_signature_rejected(self, client):
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=b'{"type": "test"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "signature" in resp.json()["detail"].lower()


class TestStripeSdkSignatureContract:
    """Exercises the real Stripe signature scheme through the HTTP endpoint.

    StripePaymentAdapter.verify_webhook_signature delegates to
    stripe.Webhook.construct_event (local HMAC verification, no network).
    """

    WHSEC = "whsec_integration_test"

    def _make_adapter(self):
        from app.providers.stripe_adapter import StripePaymentAdapter
        return StripePaymentAdapter(secret_key="sk_test_integration", webhook_secret=self.WHSEC)

    def _envelope(self, subscription) -> dict:
        return {
            "id": "evt_stripe_1",
            "object": "event",
            "api_version": "2024-06-20",
            "created": int(time.time()),
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "object": "checkout.session",
                    "id": "cs_test_1",
                    "client_reference_id": str(subscription.id),
                    "metadata": {"subscription_id": str(subscription.id)},
                    "subscription": "sub_stripe_123",
                    "customer": "cus_stripe_123",
                    "amount_total": 999,
                    "currency": "usd",
                },
            },
        }

    def test_stripe_signed_envelope_activates_subscription(self, client, db, user, plan, subscription, monkeypatch):
        monkeypatch.setattr("app.services.billing._payment_adapter", self._make_adapter())
        payload = json.dumps(self._envelope(subscription))
        header = _stripe_signed_header(payload, self.WHSEC)

        resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["duplicate"] is False

        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.active
        assert subscription.stripe_subscription_id == "sub_stripe_123"
        db.refresh(user)
        assert user.stripe_customer_id == "cus_stripe_123"

        payment = db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == subscription.id
        ).first()
        assert payment is not None
        assert payment.amount == 999  # amount_total mapped from the checkout session

    def test_stripe_tampered_signature_rejected(self, client, db, subscription, monkeypatch):
        monkeypatch.setattr("app.services.billing._payment_adapter", self._make_adapter())
        payload = json.dumps(self._envelope(subscription))
        header = _stripe_signed_header(payload, self.WHSEC)

        # Same header, different body → signature must not verify.
        tampered = json.dumps({**self._envelope(subscription), "id": "evt_evil"})
        resp = client.post(
            "/api/v1/webhooks/payment",
            content=tampered,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing

    def test_stripe_wrong_secret_rejected(self, client, db, subscription, monkeypatch):
        monkeypatch.setattr("app.services.billing._payment_adapter", self._make_adapter())
        payload = json.dumps(self._envelope(subscription))
        header = _stripe_signed_header(payload, "whsec_wrong_secret")

        resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        db.refresh(subscription)
        assert subscription.status == SubscriptionStatus.trialing


class TestCheckoutSessionMapping:
    def test_checkout_creates_local_subscription_with_mapping(self, client, db, user):
        from app.services.stripe_service import get_stripe_service

        plan = Plan(
            name=f"WH Plan {uuid4().hex[:8]}",
            monthly_price=999,
            currency="USD",
            features={"stripe_price_id": "price_test_mapping"},
        )
        db.add(plan)
        db.flush()

        service = get_stripe_service()
        session = service.create_checkout_session(
            user=user,
            plan=plan,
            success_url="http://localhost:3000/success",
            cancel_url="http://localhost:3000/cancel",
            db=db,
        )

        assert session["client_reference_id"] is not None
        assert session["metadata"]["subscription_id"] == session["client_reference_id"]
        assert session["metadata"]["user_id"] == str(user.id)
        assert session["metadata"]["plan_id"] == str(plan.id)

        sub = db.query(Subscription).filter(
            Subscription.id == uuid.UUID(session["client_reference_id"])
        ).first()
        assert sub is not None
        assert sub.status == SubscriptionStatus.trialing
        assert str(sub.plan_id) == str(plan.id)

        # checkout.session.completed for that session activates the mapped sub
        assert process_webhook(db, "evt-co-1", "checkout.session.completed", {
            "object": {
                "id": session["session_id"],
                "client_reference_id": session["client_reference_id"],
                "metadata": session["metadata"],
                "amount_total": plan.monthly_price,
                "currency": "usd",
            },
        }) is True
        db.refresh(sub)
        assert sub.status == SubscriptionStatus.active
        assert db.query(PaymentRecord).filter(
            PaymentRecord.subscription_id == sub.id
        ).count() == 1

    def test_checkout_reuses_existing_subscription(self, client, db, user, plan, subscription):
        from app.services.stripe_service import get_stripe_service

        plan.features = {"stripe_price_id": "price_test_mapping"}
        db.flush()

        service = get_stripe_service()
        session = service.create_checkout_session(
            user=user,
            plan=plan,
            success_url="http://localhost:3000/success",
            cancel_url="http://localhost:3000/cancel",
            db=db,
        )
        assert session["client_reference_id"] == str(subscription.id)
        assert db.query(Subscription).filter(Subscription.user_id == user.id).count() == 1

    def test_create_customer_reuses_stored_customer_id(self, db, user):
        from app.services.stripe_service import get_stripe_service

        service = get_stripe_service()
        first = service.create_customer(user, db=db)
        assert first == user.stripe_customer_id
        second = service.create_customer(user, db=db)
        assert second == first

    def test_create_customer_test_mode_persists(self, db, user):
        from app.services.stripe_service import get_stripe_service

        service = get_stripe_service()
        customer_id = service.create_customer(user, db=db)
        assert customer_id.startswith("test_cus_")
        db.refresh(user)
        assert user.stripe_customer_id == customer_id
