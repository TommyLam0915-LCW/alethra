"""Platform cancel flow — end-to-end tests.

The owner-facing cancel of a SUBMITTED intent must reach the broker through
the execution agent and come back as a platform 'cancelled' status (with any
fills that landed first recorded) — not silently mark a live broker order as
'rejected' while it keeps executing.

These tests drive the real agent control loop against the real platform HTTP
API (TestClient) with a FakeBrokerTransport — fully offline and re-runnable:
create → authorize → agent submits → owner cancels → agent cancels at broker
→ platform records the cancellation.
"""
import time
from datetime import timedelta
from uuid import uuid4

import pytest

from app.models.execution import OrderIntent, IntentStatus
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.broker_transport import (
    BrokerFill,
    BrokerOrderStatus,
    FakeBrokerTransport,
)
from app.execution_agent.config import AgentConfig
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.alethra_client import FillReport, OrderIntentDTO
from app.lib.timeutil import utcnow

from tests.conftest import TestSession


def _create_user_and_portfolio(client, auth_headers):
    resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": 100000.0,
        "currency": "USD",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_and_authorize_intent(client, auth_headers, portfolio_id, **overrides):
    payload = {
        "portfolio_id": portfolio_id,
        "symbol": "US:AAPL",
        "side": "BUY",
        "quantity": 10,
        "order_type": "MARKET",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
    assert resp.status_code == 201
    intent = resp.json()
    intent_id = intent["intent_id"]

    resp = client.post(
        f"/api/v1/execution/intents/{intent_id}/authorize",
        headers=auth_headers,
        json={"authorization_token": intent["authorization_token"]},
    )
    assert resp.status_code == 200
    return resp.json()


class TestClientAlethraClient:
    """Same interface as AlethraAPIClient, routed through the FastAPI
    TestClient — the real HTTP contract, no network."""

    def __init__(self, testclient, headers):
        self.client = testclient
        self.headers = headers

    def poll_order_intents(self):
        resp = self.client.get("/api/v1/execution-agent/order-intents", headers=self.headers)
        resp.raise_for_status()
        return [OrderIntentDTO(**item) for item in resp.json()]

    def get_control_status(self):
        resp = self.client.get("/api/v1/execution-agent/control-status", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def get_claimed_broker_refs(self):
        resp = self.client.get("/api/v1/execution-agent/claimed-broker-refs", headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("broker_refs", [])

    def report_order_submitted(self, intent_id, broker_ref, submitted_at=None):
        payload = {"broker_ref": broker_ref}
        if submitted_at:
            payload["submitted_at"] = submitted_at.isoformat()
        resp = self.client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/submitted",
            json=payload, headers=self.headers,
        )
        resp.raise_for_status()

    def report_fill(self, fill: FillReport):
        resp = self.client.post(
            f"/api/v1/execution-agent/order-intents/{fill.intent_id}/fills",
            json={
                "quantity": fill.quantity,
                "price": fill.price,
                "fill_time": fill.fill_time.isoformat(),
                "broker_ref": fill.broker_ref,
                "commission": fill.commission,
            },
            headers=self.headers,
        )
        resp.raise_for_status()

    def report_order_rejected(self, intent_id, reason):
        resp = self.client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/rejected",
            json={"reason": reason}, headers=self.headers,
        )
        resp.raise_for_status()

    def report_order_cancelled(self, intent_id, reason):
        resp = self.client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/cancelled",
            json={"reason": reason}, headers=self.headers,
        )
        resp.raise_for_status()

    def report_order_uncertain(self, intent_id, reason):
        resp = self.client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/uncertain",
            json={"reason": reason}, headers=self.headers,
        )
        resp.raise_for_status()

    def report_agent_heartbeat(self, connected, ib_gateway_host=None, ib_gateway_port=None):
        resp = self.client.post(
            "/api/v1/execution-agent/heartbeat",
            json={"connected": connected, "timestamp": utcnow().isoformat()},
            headers=self.headers,
        )
        resp.raise_for_status()


def _make_agent(testclient, headers, transport):
    config = AgentConfig(
        alethra_api_key="test-key",
        alethra_api_url="http://testclient",
        poll_interval_seconds=1,
        max_reconnect_attempts=3,
        reconnect_interval_seconds=0,
    )
    alethra_client = TestClientAlethraClient(testclient, headers)
    agent = ExecutionAgent(config, IBKRClient(transport), alethra_client)
    agent.connect()
    return agent


@pytest.fixture(autouse=True)
def _no_monitor_sleep(monkeypatch):
    """The fill monitor polls up to 60 times with 1s sleeps; drop the sleeps
    so a submitted-unfilled order exits the monitor quickly in tests."""
    monkeypatch.setattr("app.execution_agent.agent.time.sleep", lambda s: None)


class TestPlatformCancelFlow:
    def test_owner_cancel_of_submitted_intent_cancels_at_broker(self, client, auth_headers, monkeypatch):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        transport = FakeBrokerTransport(fill_mode="delayed", seed=0)
        # Rig the delayed-mode rng so the order stays unfilled until cancelled.
        monkeypatch.setattr(transport._rng, "random", lambda: 0.9)

        agent = _make_agent(client, auth_headers, transport)

        # Agent poll #1: submits the order (no immediate fill).
        agent.process_order_intents(kill_switch_active=False)
        db = TestSession()
        try:
            row = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            assert row.status == IntentStatus.submitted
            assert row.broker_ref == "FAKE-000001"
            assert row.cancel_requested is False
        finally:
            db.close()

        # Owner cancels the submitted intent.
        resp = client.post(f"/api/v1/execution/intents/{intent_id}/cancel", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "submitted"
        assert body["cancel_requested"] is True

        # Agent poll #2: sees cancel_requested, cancels at the broker, and the
        # monitor reports the broker cancellation back to the platform.
        agent.process_order_intents(kill_switch_active=False)

        db = TestSession()
        try:
            row = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            assert row.status == IntentStatus.cancelled
            assert row.broker_ref == "FAKE-000001"
            assert transport.get_order_status("FAKE-000001") == BrokerOrderStatus.CANCELLED
            from app.models.execution import FillRecord
            assert db.query(FillRecord).filter(FillRecord.order_intent_id == row.id).count() == 0
        finally:
            db.close()

    def test_fill_before_cancel_wins_and_records_fill(self, client, auth_headers, monkeypatch):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        transport = FakeBrokerTransport(fill_mode="delayed", seed=0)
        monkeypatch.setattr(transport._rng, "random", lambda: 0.9)

        agent = _make_agent(client, auth_headers, transport)
        agent.process_order_intents(kill_switch_active=False)

        # The order fills at the broker before the owner's cancel arrives.
        order = transport.get_order("FAKE-000001")
        order.status = BrokerOrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.fills.append(BrokerFill(
            broker_order_id="FAKE-000001",
            quantity=order.quantity,
            price=150.0,
            fill_time=utcnow(),
            commission=1.0,
        ))

        resp = client.post(f"/api/v1/execution/intents/{intent_id}/cancel", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["cancel_requested"] is True

        # Agent poll #2: cancel not accepted (already filled); the fill is
        # reported and the intent becomes 'filled' — never silently dropped.
        agent.process_order_intents(kill_switch_active=False)

        db = TestSession()
        try:
            row = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            assert row.status == IntentStatus.filled
            from app.models.execution import FillRecord
            fills = db.query(FillRecord).filter(FillRecord.order_intent_id == row.id).all()
            assert len(fills) == 1
            assert fills[0].quantity == 10
        finally:
            db.close()

        # Cancelling a filled intent is rejected.
        resp = client.post(f"/api/v1/execution/intents/{intent_id}/cancel", headers=auth_headers)
        assert resp.status_code == 400

    def test_cancel_of_authorized_intent_is_direct(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution/intents/{intent['intent_id']}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # The agent no longer sees the intent.
        resp = client.get("/api/v1/execution-agent/order-intents", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_cancel_requires_ownership(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        other_email = f"other-{uuid4().hex[:8]}@example.com"
        client.post("/api/auth/register", json={
            "email": other_email,
            "password": "otherpass123",
            "display_name": "Other",
            "user_type": "individual",
        })
        login = client.post("/api/auth/login", json={
            "email": other_email,
            "password": "otherpass123",
        })
        assert login.status_code == 200
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        resp = client.post(
            f"/api/v1/execution/intents/{intent['intent_id']}/cancel",
            headers=other_headers,
        )
        assert resp.status_code in (403, 404)


class TestCancelRequestedContract:
    """Unit-level guards for the cancel_intent service change."""

    def test_submitted_intent_is_not_rejected_by_cancel(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        # Agent reports the submission.
        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001"},
        )
        assert resp.status_code == 200

        resp = client.post(f"/api/v1/execution/intents/{intent_id}/cancel", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Regression: the intent must NOT become 'rejected' while the broker
        # order is live — the agent has to confirm the cancellation first.
        assert body["status"] == "submitted"
        assert body["cancel_requested"] is True

    def test_poll_exposes_cancel_requested_flag(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001"},
        )
        client.post(f"/api/v1/execution/intents/{intent_id}/cancel", headers=auth_headers)

        resp = client.get("/api/v1/execution-agent/order-intents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["cancel_requested"] is True

    def test_cancel_retry_after_failed_cancel_attempt(self, monkeypatch):
        """If the broker cancel attempt raises, the agent must not report
        cancelled or mark the intent completed — the next poll retries."""
        from unittest.mock import MagicMock

        transport = FakeBrokerTransport(fill_mode="delayed", seed=0)
        monkeypatch.setattr(transport._rng, "random", lambda: 0.9)
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success

        config = AgentConfig(alethra_api_key="k", alethra_api_url="http://x")
        alethra = MagicMock()
        alethra.get_control_status.return_value = {"kill_switch_active": False}
        agent = ExecutionAgent(config, IBKRClient(transport), alethra)

        intent = OrderIntentDTO(
            intent_id="cxl-intent-001", user_id="u", portfolio_id="p",
            symbol="US:AAPL", side="BUY", quantity=10, order_type="MARKET",
            limit_price=None, status="submitted",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
            broker_ref=result.broker_order_id,
            cancel_requested=True,
        )
        agent._submitted_intents[intent.intent_id] = result.broker_order_id

        # First attempt raises at the transport.
        calls = {"n": 0}
        original_cancel = transport.cancel_order

        def flaky_cancel(broker_order_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("socket closed")
            return original_cancel(broker_order_id)

        agent.ibkr_client = IBKRClient(type("T", (), {
            "cancel_order": staticmethod(flaky_cancel),
            "get_order_status": transport.get_order_status,
            "get_fills": transport.get_fills,
        })())

        agent._cancel_submitted_order(intent)
        alethra.report_order_cancelled.assert_not_called()
        assert intent.intent_id not in agent._completed_intents

        # Second attempt goes through and reports the cancellation.
        agent._cancel_submitted_order(intent)
        alethra.report_order_cancelled.assert_called_once()
        assert intent.intent_id in agent._completed_intents
