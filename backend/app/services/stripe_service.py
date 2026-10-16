"""Stripe billing service — high-level Stripe operations.

This service provides Stripe-specific operations beyond the basic payment adapter.
It handles customer management, checkout sessions, portal sessions, and webhook processing.

Requires:
- STRIPE_SECRET_KEY environment variable
- STRIPE_WEBHOOK_SECRET environment variable
- STRIPE_PRICE_ID_FREE, STRIPE_PRICE_ID_PRO, STRIPE_PRICE_ID_ENTERPRISE (optional)
"""
import os
import uuid
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.billing import Plan, Subscription, SubscriptionStatus, WebhookEventRecord
from app.models.user import User
from app.providers.stripe_adapter import StripePaymentAdapter
from app.providers.payment_adapter import TestPaymentAdapter
from app.lib.timeutil import utcnow


class StripeService:
    """High-level Stripe operations for billing management."""

    def __init__(self):
        self._adapter: Optional[StripePaymentAdapter] = None
        self._test_mode = False

    def _get_adapter(self) -> StripePaymentAdapter:
        """Get or create Stripe adapter."""
        if self._adapter is None:
            secret_key = os.environ.get("STRIPE_SECRET_KEY")
            webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

            if secret_key and webhook_secret:
                try:
                    self._adapter = StripePaymentAdapter(
                        secret_key=secret_key,
                        webhook_secret=webhook_secret,
                    )
                    self._test_mode = self._adapter.is_test_mode
                except (ValueError, ImportError):
                    # Fall back to test mode
                    self._adapter = None
                    self._test_mode = True
            else:
                self._test_mode = True

        return self._adapter

    @property
    def is_test_mode(self) -> bool:
        """Check if running in test mode."""
        self._get_adapter()
        return self._test_mode

    def create_customer(self, user: User) -> str:
        """Create a Stripe customer for the user.

        Returns customer ID (or test customer ID in test mode).
        """
        adapter = self._get_adapter()

        if adapter is None:
            # Test mode - return mock customer ID
            return f"test_cus_{user.id.hex[:16]}"

        # Check if user already has a Stripe customer ID
        # This would be stored in a user metadata table in production
        customer_id = adapter.create_customer(
            email=user.email,
            name=user.display_name,
        )
        return customer_id

    def create_checkout_session(
        self,
        user: User,
        plan: Plan,
        success_url: str,
        cancel_url: str,
    ) -> Dict[str, Any]:
        """Create a Stripe Checkout session for subscription.

        Returns session URL and ID.
        """
        adapter = self._get_adapter()

        # Get or create customer
        customer_id = self.create_customer(user)

        # Get price ID from plan features or environment
        price_id = self._get_price_id_for_plan(plan)

        if adapter is None:
            # Test mode - return mock session
            return {
                "session_id": f"test_cs_{uuid.uuid4().hex[:16]}",
                "session_url": f"https://checkout.stripe.com/test/{customer_id}",
                "customer_id": customer_id,
                "test_mode": True,
            }

        # Create real checkout session
        session_url = adapter.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        return {
            "session_id": f"cs_{uuid.uuid4().hex[:16]}",  # In production, get from Stripe
            "session_url": session_url,
            "customer_id": customer_id,
            "test_mode": self.is_test_mode,
        }

    def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> Dict[str, Any]:
        """Create a Stripe Customer Portal session.

        Returns portal URL.
        """
        adapter = self._get_adapter()

        if adapter is None:
            # Test mode - return mock portal URL
            return {
                "portal_url": f"https://billing.stripe.com/test/{customer_id}",
                "test_mode": True,
            }

        # Create portal session
        import stripe
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )

        return {
            "portal_url": session.url,
            "test_mode": self.is_test_mode,
        }

    def _get_price_id_for_plan(self, plan: Plan) -> str:
        """Get Stripe price ID for a plan.

        Looks up from plan.features['stripe_price_id'] or environment variables.
        """
        # Check plan features first
        features = plan.features or {}
        if "stripe_price_id" in features:
            return features["stripe_price_id"]

        # Fall back to environment variables
        env_map = {
            "free": "STRIPE_PRICE_ID_FREE",
            "starter": "STRIPE_PRICE_ID_STARTER",
            "pro": "STRIPE_PRICE_ID_PRO",
            "enterprise": "STRIPE_PRICE_ID_ENTERPRISE",
        }

        # Determine tier from price
        price = plan.monthly_price or 0
        if price == 0:
            tier = "free"
        elif price < 5000:
            tier = "starter"
        else:
            tier = "pro"

        env_var = env_map.get(tier)
        if env_var:
            price_id = os.environ.get(env_var)
            if price_id:
                return price_id

        raise ValueError(f"No Stripe price ID configured for plan: {plan.name}")

    def process_stripe_webhook(
        self,
        db: Session,
        event_type: str,
        event_data: Dict[str, Any],
        event_id: str,
    ) -> bool:
        """Process a Stripe webhook event.

        Returns True if processed, False if duplicate.
        """
        # Check for duplicate
        existing = db.query(WebhookEventRecord).filter(
            WebhookEventRecord.event_id == event_id
        ).first()
        if existing:
            return False

        # Process based on event type
        if event_type == "checkout.session.completed":
            self._handle_checkout_completed(db, event_data)
        elif event_type == "customer.subscription.updated":
            self._handle_subscription_updated(db, event_data)
        elif event_type == "customer.subscription.deleted":
            self._handle_subscription_deleted(db, event_data)
        elif event_type == "invoice.payment_succeeded":
            self._handle_payment_succeeded(db, event_data)
        elif event_type == "invoice.payment_failed":
            self._handle_payment_failed(db, event_data)

        # Record event
        record = WebhookEventRecord(
            event_id=event_id,
            event_type=event_type,
            payload=event_data,
            processed=True,
            processed_at=utcnow(),
        )
        db.add(record)
        db.commit()

        return True

    def _handle_checkout_completed(self, db: Session, data: Dict[str, Any]):
        """Handle checkout.session.completed event."""
        # Extract subscription and customer info
        subscription_id = data.get("subscription")
        customer_id = data.get("customer")
        metadata = data.get("metadata", {})

        # Find or create subscription
        # In production, you'd look up by metadata or customer_id
        # For now, we'll just record the event

    def _handle_subscription_updated(self, db: Session, data: Dict[str, Any]):
        """Handle customer.subscription.updated event."""
        stripe_sub_id = data.get("id")
        status = data.get("status")
        current_period_end = data.get("current_period_end")

        # Update subscription in database
        # This would require mapping Stripe subscription ID to our subscription

    def _handle_subscription_deleted(self, db: Session, data: Dict[str, Any]):
        """Handle customer.subscription.deleted event."""
        stripe_sub_id = data.get("id")

        # Mark subscription as cancelled

    def _handle_payment_succeeded(self, db: Session, data: Dict[str, Any]):
        """Handle invoice.payment_succeeded event."""
        # Record successful payment

    def _handle_payment_failed(self, db: Session, data: Dict[str, Any]):
        """Handle invoice.payment_failed event."""
        # Mark subscription as past_due


# Global instance
_stripe_service: Optional[StripeService] = None


def get_stripe_service() -> StripeService:
    """Get global Stripe service instance."""
    global _stripe_service
    if _stripe_service is None:
        _stripe_service = StripeService()
    return _stripe_service
