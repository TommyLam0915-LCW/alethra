from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
import hashlib
import hmac
import json
import uuid
from app.lib.timeutil import utcnow


@dataclass(frozen=True)
class PaymentIntent:
    id: str
    amount: int
    currency: str
    status: str
    customer_id: str
    metadata: Dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class WebhookEvent:
    id: str
    event_type: str
    data: Dict[str, Any]
    created_at: str
    signature: str


class PaymentAdapter(ABC):
    @property
    @abstractmethod
    def adapter_id(self) -> str:
        pass

    @property
    @abstractmethod
    def is_test_mode(self) -> bool:
        pass

    @abstractmethod
    def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentIntent:
        pass

    @abstractmethod
    def confirm_payment(self, intent_id: str) -> PaymentIntent:
        pass

    @abstractmethod
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        pass

    @abstractmethod
    def parse_webhook_event(self, payload: str) -> WebhookEvent:
        pass


class TestPaymentAdapter(PaymentAdapter):
    def __init__(self, webhook_secret: str = "test-webhook-secret"):
        self._intents: Dict[str, PaymentIntent] = {}
        self._webhook_secret = webhook_secret

    @property
    def adapter_id(self) -> str:
        return "test"

    @property
    def is_test_mode(self) -> bool:
        return True

    def create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentIntent:
        intent = PaymentIntent(
            id=f"test_pi_{uuid.uuid4().hex[:16]}",
            amount=amount,
            currency=currency,
            status="created",
            customer_id=customer_id,
            metadata=metadata or {},
            created_at=utcnow().isoformat(),
        )
        self._intents[intent.id] = intent
        return intent

    def confirm_payment(self, intent_id: str) -> PaymentIntent:
        intent = self._intents.get(intent_id)
        if intent is None:
            raise ValueError(f"Payment intent not found: {intent_id}")
        confirmed = PaymentIntent(
            id=intent.id,
            amount=intent.amount,
            currency=intent.currency,
            status="succeeded",
            customer_id=intent.customer_id,
            metadata=intent.metadata,
            created_at=intent.created_at,
        )
        self._intents[intent_id] = confirmed
        return confirmed

    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        expected = hmac.new(
            self._webhook_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook_event(self, payload: str) -> WebhookEvent:
        data = json.loads(payload)
        return WebhookEvent(
            id=data.get("id", f"evt_{uuid.uuid4().hex[:12]}"),
            event_type=data.get("type", "unknown"),
            data=data.get("data", {}),
            created_at=data.get("created_at", utcnow().isoformat()),
            signature=data.get("signature", ""),
        )
