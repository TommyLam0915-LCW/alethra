"""Stripe billing service — high-level Stripe operations.

This service provides Stripe-specific operations beyond the basic payment adapter.
It handles customer management, checkout sessions, and portal sessions.
Webhook events are applied through the atomic, retry-safe
``app.services.billing.process_webhook`` path.

Requires:
- STRIPE_SECRET_KEY environment variable
- STRIPE_WEBHOOK_SECRET environment variable
- STRIPE_PRICE_ID_FREE, STRIPE_PRICE_ID_PRO, STRIPE_PRICE_ID_ENTERPRISE (optional)
"""
import os
import uuid
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.billing import Plan
from app.models.user import User
from app.providers.stripe_adapter import StripePaymentAdapter


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

    def create_customer(self, user: User, db: Optional[Session] = None) -> str:
        """Get or create a Stripe customer for the user.

        Reuses the stored ``user.stripe_customer_id`` when present so repeated
        checkout/portal calls do not create duplicate Stripe customers.
        """
        if user.stripe_customer_id:
            return user.stripe_customer_id

        adapter = self._get_adapter()

        if adapter is None:
            # Test mode - return mock customer ID
            customer_id = f"test_cus_{user.id.hex[:16]}"
        else:
            customer_id = adapter.create_customer(
                email=user.email,
                name=user.display_name,
            )

        if db is not None:
            user.stripe_customer_id = customer_id
            db.commit()
        return customer_id

    def create_checkout_session(
        self,
        user: User,
        plan: Plan,
        success_url: str,
        cancel_url: str,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """Create a Stripe Checkout session for subscription.

        Creates (or reuses) the local trialing subscription first and embeds
        its id as ``client_reference_id`` / ``metadata.subscription_id`` so the
        ``checkout.session.completed`` webhook can resolve and activate it.

        Returns session URL and ID.
        """
        adapter = self._get_adapter()

        local_sub = None
        if db is not None:
            from app.services.billing import create_subscription
            local_sub = create_subscription(db, user.id, plan.id)

        # Get or create customer
        customer_id = self.create_customer(user, db=db)

        # Get price ID from plan features or environment
        price_id = self._get_price_id_for_plan(plan)

        client_reference_id = str(local_sub.id) if local_sub is not None else None
        metadata = (
            {
                "subscription_id": str(local_sub.id),
                "user_id": str(user.id),
                "plan_id": str(plan.id),
            }
            if local_sub is not None
            else {}
        )

        if adapter is None:
            # Test mode - return mock session
            return {
                "session_id": f"test_cs_{uuid.uuid4().hex[:16]}",
                "session_url": f"https://checkout.stripe.com/test/{customer_id}",
                "customer_id": customer_id,
                "test_mode": True,
                "client_reference_id": client_reference_id,
                "metadata": metadata,
            }

        # Create real checkout session
        session_url = adapter.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata or None,
            client_reference_id=client_reference_id,
        )

        return {
            "session_id": f"cs_{uuid.uuid4().hex[:16]}",  # In production, get from Stripe
            "session_url": session_url,
            "customer_id": customer_id,
            "test_mode": self.is_test_mode,
            "client_reference_id": client_reference_id,
            "metadata": metadata,
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


# Global instance
_stripe_service: Optional[StripeService] = None


def get_stripe_service() -> StripeService:
    """Get global Stripe service instance."""
    global _stripe_service
    if _stripe_service is None:
        _stripe_service = StripeService()
    return _stripe_service
