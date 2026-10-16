"""Stripe payment adapter — real Stripe SDK integration.

Requires:
- STRIPE_SECRET_KEY environment variable
- STRIPE_WEBHOOK_SECRET environment variable

This adapter is NOT activated unless both environment variables are set.
The default adapter is TestPaymentAdapter (test mode).
"""
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any

from app.providers.payment_adapter import (
    PaymentAdapter,
    PaymentIntent,
    WebhookEvent,
)


class StripePaymentAdapter(PaymentAdapter):
    """Stripe payment adapter using the official Stripe SDK.

    This adapter makes real API calls to Stripe. It is only instantiated
    when STRIPE_SECRET_KEY is configured.
    """

    def __init__(
        self,
        secret_key: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ):
        self._secret_key = secret_key or os.environ.get("STRIPE_SECRET_KEY")
        self._webhook_secret = webhook_secret or os.environ.get("STRIPE_WEBHOOK_SECRET")

        if not self._secret_key:
            raise ValueError(
                "STRIPE_SECRET_KEY is required for StripePaymentAdapter. "
                "Use TestPaymentAdapter for test mode."
            )
        if not self._webhook_secret:
            raise ValueError(
                "STRIPE_WEBHOOK_SECRET is required for webhook verification."
            )

        import stripe
        self._stripe = stripe
        self._stripe.api_key = self._secret_key

    @property
    def adapter_id(self) -> str:
        return "stripe"

    @property
    def is_test_mode(self) -> bool:
        return self._secret_key.startswith("sk_test_")

    def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentIntent:
        intent = self._stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            customer=customer_id if customer_id.startswith("cus_") else None,
            metadata={**(metadata or {}), "alethra_user_id": customer_id},
        )
        return PaymentIntent(
            id=intent.id,
            amount=intent.amount,
            currency=intent.currency,
            status=intent.status,
            customer_id=customer_id,
            metadata=dict(intent.metadata) if intent.metadata else {},
            created_at=datetime.fromtimestamp(intent.created).isoformat(),
        )

    def confirm_payment(self, intent_id: str) -> PaymentIntent:
        intent = self._stripe.PaymentIntent.retrieve(intent_id)
        return PaymentIntent(
            id=intent.id,
            amount=intent.amount,
            currency=intent.currency,
            status=intent.status,
            customer_id=intent.metadata.get("alethra_user_id", ""),
            metadata=dict(intent.metadata) if intent.metadata else {},
            created_at=datetime.fromtimestamp(intent.created).isoformat(),
        )

    def verify_webhook_signature(self, payload: str, signature: str):
        """Verify webhook signature and return the verified Event object.

        Returns the Stripe Event on success, None on failure.
        """
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
            return event
        except (self._stripe.error.SignatureVerificationError, ValueError):
            return None

    def parse_webhook_event(self, payload: str) -> WebhookEvent:
        data = json.loads(payload)
        return WebhookEvent(
            id=data.get("id", ""),
            event_type=data.get("type", "unknown"),
            data=data.get("data", {}).get("object", {}),
            created_at=datetime.fromtimestamp(data.get("created", 0)).isoformat(),
            signature="",
        )

    def create_customer(self, email: str, name: Optional[str] = None) -> str:
        customer = self._stripe.Customer.create(email=email, name=name)
        return customer.id

    def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        session = self._stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return session.url
