"""Alethra platform API client for the execution agent.

Makes HTTP calls to the server-side execution-agent API endpoints.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import requests


@dataclass
class OrderIntentDTO:
    intent_id: str
    user_id: str
    portfolio_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    status: str
    expires_at: str
    created_at: str


@dataclass
class FillReport:
    intent_id: str
    quantity: int
    price: float
    fill_time: datetime
    broker_ref: str
    commission: float = 0.0


class AlethraAPIClient:
    """HTTP client for the Alethra platform execution-agent API."""

    def __init__(self, api_url: str, api_token: str, timeout: int = 30):
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "X-Agent-Version": "0.2.0",
        }

    def poll_order_intents(self) -> List[OrderIntentDTO]:
        resp = requests.get(
            f"{self.api_url}/v1/execution-agent/order-intents",
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [OrderIntentDTO(**item) for item in resp.json()]

    def report_order_submitted(
        self,
        intent_id: str,
        broker_ref: str,
        submitted_at: Optional[datetime] = None,
    ) -> None:
        payload = {"broker_ref": broker_ref}
        if submitted_at:
            payload["submitted_at"] = submitted_at.isoformat()
        resp = requests.post(
            f"{self.api_url}/v1/execution-agent/order-intents/{intent_id}/submitted",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def report_fill(self, fill: FillReport) -> None:
        resp = requests.post(
            f"{self.api_url}/v1/execution-agent/order-intents/{fill.intent_id}/fills",
            json={
                "quantity": fill.quantity,
                "price": fill.price,
                "fill_time": fill.fill_time.isoformat(),
                "broker_ref": fill.broker_ref,
                "commission": fill.commission,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def report_order_rejected(self, intent_id: str, reason: str) -> None:
        resp = requests.post(
            f"{self.api_url}/v1/execution-agent/order-intents/{intent_id}/rejected",
            json={"reason": reason},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def report_order_cancelled(self, intent_id: str, reason: str) -> None:
        resp = requests.post(
            f"{self.api_url}/v1/execution-agent/order-intents/{intent_id}/cancelled",
            json={"reason": reason},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def report_agent_heartbeat(
        self,
        connected: bool,
        ib_gateway_host: Optional[str] = None,
        ib_gateway_port: Optional[int] = None,
    ) -> None:
        resp = requests.post(
            f"{self.api_url}/v1/execution-agent/heartbeat",
            json={
                "connected": connected,
                "ib_gateway_host": ib_gateway_host,
                "ib_gateway_port": ib_gateway_port,
                "timestamp": datetime.utcnow().isoformat(),
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
