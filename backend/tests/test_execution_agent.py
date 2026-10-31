"""Tests for the execution agent system.

Covers:
- Broker transport abstraction (FakeBrokerTransport)
- IBKR client wrapper
- Server-side agent API endpoints
- Agent control loop (duplicate prevention, partial fills, expiry, reconciliation)
- Service layer (agent_report_submitted, agent_report_fill, etc.)

All tests use FakeBrokerTransport — no IBKR connection required.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import TestSession
from app.models.user import User
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.execution import (
    OrderIntent,
    IntentStatus,
    FillSource,
    FillRecord,
)
from app.services.execution import create_order_intent, authorize_intent
from app.services.execution_agent_api import (
    list_agent_intents,
    agent_report_submitted,
    agent_report_fill,
    agent_report_rejected,
    agent_report_cancelled,
)
from app.execution_agent.broker_transport import (
    FakeBrokerTransport,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerTransport,
)
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.config import AgentConfig
from app.lib.timeutil import utcnow


# ── Helpers ──

def _get_db() -> Session:
    return TestSession()


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


# ──────────────────────────────────────────────
# 1. FakeBrokerTransport
# ──────────────────────────────────────────────

class TestFakeBrokerTransport:

    def test_connect_disconnect(self):
        transport = FakeBrokerTransport()
        assert not transport.is_connected()
        transport.connect()
        assert transport.is_connected()
        transport.disconnect()
        assert not transport.is_connected()

    def test_submit_order_not_connected(self):
        transport = FakeBrokerTransport()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert not result.success
        assert "Not connected" in result.error_message

    def test_immediate_fill(self):
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success
        assert result.broker_order_id is not None

        status = transport.get_order_status(result.broker_order_id)
        assert status == BrokerOrderStatus.FILLED

        fills = transport.get_fills(result.broker_order_id)
        assert len(fills) == 1
        assert fills[0].quantity == 10
        assert fills[0].price == 150.0

    def test_reject_mode(self):
        transport = FakeBrokerTransport(fill_mode="reject", reject_message="No liquidity")
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert not result.success
        assert result.error_message == "No liquidity"
        assert result.broker_order_id is not None

        status = transport.get_order_status(result.broker_order_id)
        assert status == BrokerOrderStatus.REJECTED

    def test_cancel_order(self):
        transport = FakeBrokerTransport(fill_mode="delayed", seed=0)
        transport._rng.random = lambda: 0.99
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success

        cancelled = transport.cancel_order(result.broker_order_id)
        assert cancelled

        status = transport.get_order_status(result.broker_order_id)
        assert status == BrokerOrderStatus.CANCELLED

    def test_cancel_filled_order_fails(self):
        transport = FakeBrokerTransport(fill_mode="immediate")
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success

        cancelled = transport.cancel_order(result.broker_order_id)
        assert not cancelled

    def test_partial_fill_mode(self):
        transport = FakeBrokerTransport(
            fill_mode="partial", fill_price=100.0, partial_fill_pct=0.5
        )
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success

        status = transport.get_order_status(result.broker_order_id)
        assert status == BrokerOrderStatus.PARTIALLY_FILLED

        fills = transport.get_fills(result.broker_order_id)
        assert len(fills) == 1
        assert fills[0].quantity == 5

        for _ in range(10):
            status = transport.get_order_status(result.broker_order_id)
            if status == BrokerOrderStatus.FILLED:
                break

        assert status == BrokerOrderStatus.FILLED

        fills = transport.get_fills(result.broker_order_id)
        total_qty = sum(f.quantity for f in fills)
        assert total_qty == 10


# ──────────────────────────────────────────────
# 2. IBKR Client wrapper
# ──────────────────────────────────────────────

class TestIBKRClient:

    def test_delegates_to_transport(self):
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=200.0)
        client = IBKRClient(transport)
        client.connect()
        assert client.is_connected()

        result = client.submit_market_order("US:AAPL", "BUY", 5)
        assert result.success
        assert result.broker_order_id is not None

        status = client.get_order_status(result.broker_order_id)
        assert status == "Filled"

        fills = client.get_fills(result.broker_order_id)
        assert len(fills) == 1
        assert fills[0].price == 200.0

    def test_limit_order(self):
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        client = IBKRClient(transport)
        client.connect()

        result = client.submit_limit_order("US:AAPL", "SELL", 10, limit_price=155.0)
        assert result.success

    def test_cancel(self):
        transport = FakeBrokerTransport(fill_mode="delayed", seed=42)
        transport._rng.random = lambda: 0.99
        client = IBKRClient(transport)
        client.connect()

        result = client.submit_market_order("US:AAPL", "BUY", 10)
        assert result.success
        assert client.cancel_order(result.broker_order_id)


# ──────────────────────────────────────────────
# 3. Server-side agent API service layer
# ──────────────────────────────────────────────

class TestAgentAPIService:

    def test_list_agent_intents(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()
        intents = list_agent_intents(db, user_id=user.id)
        assert len(intents) == 1
        assert intents[0].intent_id == intent["intent_id"]

    def test_list_authorized_intents_excludes_expired(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)

        payload = {
            "portfolio_id": portfolio_id,
            "symbol": "US:AAPL",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
        }
        resp = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
        intent_data = resp.json()
        intent_id = intent_data["intent_id"]

        db = _get_db()
        db_intent = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
        db_intent.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": intent_data["authorization_token"]},
        )
        assert resp.status_code == 400

        user = db.query(User).first()
        intents = list_agent_intents(db, user_id=user.id)
        assert len(intents) == 0

    def test_agent_report_submitted(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()

        result = agent_report_submitted(
            db,
            intent_id=intent["intent_id"],
            user_id=user.id,
            broker_ref="IBKR-12345",
            submitted_at=utcnow(),
        )
        assert result.status == IntentStatus.submitted
        assert result.broker_ref == "IBKR-12345"
        assert result.fill_source == FillSource.broker

    def test_agent_report_submitted_idempotent(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()

        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-12345")
        result = agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-12345")
        assert result.status == IntentStatus.submitted

    def test_agent_report_fill(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()

        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-12345")

        fill = agent_report_fill(
            db,
            intent_id=intent["intent_id"],
            user_id=user.id,
            quantity=10,
            price=150.0,
            fill_time=utcnow(),
            broker_ref="IBKR-12345",
            commission=1.50,
        )
        assert fill.quantity == 10
        assert fill.price == 150.0
        assert fill.fill_source == FillSource.broker

        db.refresh(db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first())
        updated_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first()
        assert updated_intent.status == IntentStatus.filled

    def test_agent_report_partial_fills(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id, quantity=10)

        db = _get_db()
        user = db.query(User).first()

        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-12345")

        agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=5, price=150.0, fill_time=utcnow(),
            broker_ref="IBKR-12345",
        )

        db_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first()
        assert db_intent.status == IntentStatus.submitted

        agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=5, price=150.5, fill_time=utcnow(),
            broker_ref="IBKR-12345",
        )

        db_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first()
        assert db_intent.status == IntentStatus.filled

    def test_agent_report_rejected(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()

        result = agent_report_rejected(
            db, intent["intent_id"], user.id, "Insufficient margin"
        )
        assert result.status == IntentStatus.rejected
        assert result.rejection_reason == "Insufficient margin"

    def test_agent_report_cancelled(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = _get_db()
        user = db.query(User).first()

        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-12345")

        result = agent_report_cancelled(
            db, intent["intent_id"], user.id, "User requested cancellation"
        )
        assert result.status == IntentStatus.cancelled


# ──────────────────────────────────────────────
# 4. Server-side agent API HTTP endpoints
# ──────────────────────────────────────────────

class TestAgentAPIEndpoints:

    def test_poll_authorized_intents(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.get("/api/v1/execution-agent/order-intents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "authorized"

    def test_report_submitted(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001", "submitted_at": utcnow().isoformat()},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert resp.json()["broker_ref"] == "FAKE-000001"

    def test_report_fill(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001"},
        )

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/fills",
            headers=auth_headers,
            json={
                "quantity": 10,
                "price": 150.0,
                "fill_time": utcnow().isoformat(),
                "broker_ref": "FAKE-000001",
                "commission": 1.50,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "recorded"

    def test_report_rejected(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/rejected",
            headers=auth_headers,
            json={"reason": "No liquidity"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_report_cancelled(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001"},
        )

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/cancelled",
            headers=auth_headers,
            json={"reason": "User cancelled"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_heartbeat(self, client, auth_headers):
        resp = client.post(
            "/api/v1/execution-agent/heartbeat",
            headers=auth_headers,
            json={
                "connected": True,
                "ib_gateway_host": "localhost",
                "ib_gateway_port": 7497,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_unauthenticated(self, client):
        resp = client.get("/api/v1/execution-agent/order-intents")
        assert resp.status_code == 401


# ──────────────────────────────────────────────
# 5. Agent control loop
# ──────────────────────────────────────────────

class TestExecutionAgent:

    def _make_agent(self, fill_mode="immediate", fill_price=150.0):
        config = AgentConfig(
            alethra_api_key="test-key",
            alethra_api_url="http://localhost:8000",
            poll_interval_seconds=1,
            max_reconnect_attempts=3,
            reconnect_interval_seconds=0,
        )
        transport = FakeBrokerTransport(fill_mode=fill_mode, fill_price=fill_price)
        ibkr_client = IBKRClient(transport)

        alethra_client = MagicMock()
        alethra_client.poll_order_intents.return_value = []
        alethra_client.report_order_submitted.return_value = None
        alethra_client.report_fill.return_value = None
        alethra_client.report_order_rejected.return_value = None
        alethra_client.report_order_cancelled.return_value = None
        alethra_client.report_agent_heartbeat.return_value = None

        agent = ExecutionAgent(config, ibkr_client, alethra_client)
        return agent, transport, alethra_client

    def test_process_single_intent_immediate_fill(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent(fill_mode="immediate", fill_price=150.0)
        agent.connect()

        intent = OrderIntentDTO(
            intent_id="test-intent-001",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)

        alethra_client.report_order_submitted.assert_called_once()
        call_args = alethra_client.report_order_submitted.call_args
        assert call_args.kwargs["intent_id"] == "test-intent-001"
        assert call_args.kwargs["broker_ref"] is not None

        assert alethra_client.report_fill.call_count >= 1

    def test_duplicate_prevention(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent()
        agent.connect()

        intent = OrderIntentDTO(
            intent_id="test-intent-002",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)
        first_submit_count = alethra_client.report_order_submitted.call_count
        assert first_submit_count == 1

        agent._process_single_intent(intent)
        assert alethra_client.report_order_submitted.call_count == first_submit_count

    def test_expired_intent_rejected(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent()
        agent.connect()

        intent = OrderIntentDTO(
            intent_id="test-intent-003",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() - timedelta(minutes=1)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)

        alethra_client.report_order_submitted.assert_not_called()
        alethra_client.report_order_rejected.assert_called_once()
        assert "expired" in alethra_client.report_order_rejected.call_args.kwargs["reason"].lower()

    def test_reject_mode(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent(fill_mode="reject")
        agent.connect()

        intent = OrderIntentDTO(
            intent_id="test-intent-004",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)

        alethra_client.report_order_submitted.assert_not_called()
        alethra_client.report_order_rejected.assert_called_once()

    def test_limit_order(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent()
        agent.connect()

        intent = OrderIntentDTO(
            intent_id="test-intent-005",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="SELL",
            quantity=5,
            order_type="LIMIT",
            limit_price=160.0,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)
        alethra_client.report_order_submitted.assert_called_once()

    def test_connect_disconnect(self):
        agent, transport, _ = self._make_agent()
        agent.connect()
        assert agent.ibkr_client.is_connected()
        agent.disconnect()
        assert not agent.ibkr_client.is_connected()

    def test_reconnect(self):
        agent, transport, _ = self._make_agent()
        agent.connect()
        agent.disconnect()
        assert agent.reconnect()
        assert agent.ibkr_client.is_connected()

    def test_reconnect_max_attempts(self):
        agent, transport, _ = self._make_agent()
        agent.config.max_reconnect_attempts = 1
        agent.reconnect_attempts = 1
        assert not agent.reconnect()

    def test_submission_report_failure_does_not_resubmit(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        agent, transport, alethra_client = self._make_agent()
        agent.connect()

        alethra_client.report_order_submitted.side_effect = Exception("Network error")

        intent = OrderIntentDTO(
            intent_id="test-intent-006",
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

        agent._process_single_intent(intent)

        assert "test-intent-006" in agent._submitted_intents or "test-intent-006" in agent._completed_intents

        alethra_client.report_order_submitted.side_effect = None
        agent._process_single_intent(intent)

        assert alethra_client.report_order_submitted.call_count == 1


# ──────────────────────────────────────────────
# 6. Agent log safety — the broker error chain
# ──────────────────────────────────────────────

# Synthetic secret material. It never leaves this test: the transport and the
# exception below are in-memory stand-ins for a broker client whose error text
# embeds a gateway configuration path, a token and a database DSN.
_SENSITIVE_ERROR = (
    "connect failed at /Users/owner/ibgateway/conf.yaml "
    "api_token=sk-live-abc123 "
    "postgresql+psycopg2://postgres:hunter2@db:5432/alethra"
)
_SECRET_FRAGMENTS = (
    "/Users/owner",
    "conf.yaml",
    "sk-live-abc123",
    "hunter2",
    "postgresql+psycopg2",
)


def _assert_no_secrets(text: str, surface: str) -> None:
    for fragment in _SECRET_FRAGMENTS:
        assert fragment not in text, f"{surface} leaked {fragment!r}: {text}"


class _RejectingTransport(FakeBrokerTransport):
    """Fake broker whose client reports a rejection message verbatim."""

    def __init__(self, error_message: str):
        super().__init__()
        self._error_message = error_message

    def submit_order(self, *args, **kwargs):
        return BrokerOrderResult(success=False, error_message=self._error_message)


class _ExplodingTransport(FakeBrokerTransport):
    """Fake broker whose client raises the same raw text as an exception."""

    def __init__(self, error: BaseException):
        super().__init__()
        self._error = error

    def submit_order(self, *args, **kwargs):
        raise self._error


class _DbAgentClient:
    """Platform client backed by the real services and the test database.

    ``AlethraAPIClient`` speaks HTTP, and the agent routes call exactly these
    service functions, so this adapter exercises the same persistence and
    scrub path without opening a socket or touching a real platform.
    """

    def __init__(self, db, user_id, rows):
        self._db = db
        self._user_id = user_id
        self._rows = rows

    def poll_order_intents(self):
        from app.execution_agent.alethra_client import OrderIntentDTO

        return [
            OrderIntentDTO(
                intent_id=row.intent_id,
                user_id=str(row.user_id),
                portfolio_id=str(row.portfolio_id),
                symbol=row.symbol,
                side=row.side,
                quantity=row.quantity,
                order_type=row.order_type,
                limit_price=row.limit_price,
                status=row.status.value,
                expires_at=row.expires_at.isoformat(),
                created_at=row.created_at.isoformat(),
                broker_ref=row.broker_ref,
                rejection_reason=row.rejection_reason,
                cancel_requested=bool(row.cancel_requested),
                # The platform poll response carries no execution scope. The
                # harness grants the fake-transport scope explicitly so the
                # broker call path (the subject of this test) is reachable;
                # the scope gate itself is covered by the containment tests.
                execution_scope="fake-transport",
            )
            for row in self._rows
        ]

    def get_claimed_broker_refs(self):
        return []

    def report_order_rejected(self, intent_id, reason):
        return agent_report_rejected(self._db, intent_id, self._user_id, reason)


class TestAgentLogSafety:
    """The agent's own log is written before any downstream filter runs.

    These tests pin that the broker error chain — transport error message,
    agent log/stderr, agent-facing API response and owner response — carries no
    secret value, while the line stays diagnosable (error type, controlled
    code, intent id and the log timestamp).
    """

    def _agent(self, transport):
        config = AgentConfig(
            alethra_api_key="test-key",
            alethra_api_url="http://localhost:8000",
            poll_interval_seconds=1,
            max_reconnect_attempts=3,
            reconnect_interval_seconds=0,
        )
        alethra_client = MagicMock()
        alethra_client.poll_order_intents.return_value = []
        alethra_client.get_claimed_broker_refs.return_value = []
        alethra_client.report_order_rejected.return_value = None
        agent = ExecutionAgent(config, IBKRClient(transport), alethra_client)
        return agent, alethra_client

    def _intent(self, intent_id):
        from app.execution_agent.alethra_client import OrderIntentDTO

        return OrderIntentDTO(
            intent_id=intent_id,
            user_id="user-1",
            portfolio_id="portfolio-1",
            symbol="US:AAPL",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            limit_price=None,
            status="authorized",
            execution_scope="fake-transport",
            expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
            created_at=utcnow().isoformat(),
        )

    def _capture_agent_log(self):
        """Attach the same stderr handler shape ``main()`` configures."""
        import io
        import logging

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        target = logging.getLogger("app.execution_agent.agent")
        target.addHandler(handler)
        return target, handler, stream

    def test_rejected_result_is_masked_in_log_and_reported_reason(self):
        target, handler, stream = self._capture_agent_log()
        try:
            agent, alethra_client = self._agent(_RejectingTransport(_SENSITIVE_ERROR))
            agent.connect()
            agent._process_single_intent(self._intent("safety-reject-1"))
        finally:
            target.removeHandler(handler)

        emitted = stream.getvalue()
        # Diagnosability kept: error type is implied by the controlled code,
        # the intent id correlates the line, the formatter adds the timestamp.
        assert "safety-reject-1" in emitted
        assert "BROKER_REJECTED" in emitted
        assert "<path>" in emitted
        assert "<redacted-url>" in emitted
        _assert_no_secrets(emitted, "agent log")

        alethra_client.report_order_rejected.assert_called_once()
        reason = alethra_client.report_order_rejected.call_args.kwargs["reason"]
        assert "<path>" in reason
        assert "<redacted-url>" in reason
        _assert_no_secrets(reason, "reported reason")

    def test_processing_exception_keeps_type_and_intent_id_without_secrets(self):
        target, handler, stream = self._capture_agent_log()
        try:
            agent, alethra_client = self._agent(
                _ExplodingTransport(RuntimeError(_SENSITIVE_ERROR))
            )
            agent.connect()
            alethra_client.poll_order_intents.return_value = [
                self._intent("safety-raise-1")
            ]
            agent.process_order_intents()
        finally:
            target.removeHandler(handler)

        emitted = stream.getvalue()
        assert "safety-raise-1" in emitted
        assert "RuntimeError" in emitted
        assert "<path>" in emitted
        assert "<redacted-url>" in emitted
        _assert_no_secrets(emitted, "agent log")

        alethra_client.report_order_rejected.assert_called_once()
        reason = alethra_client.report_order_rejected.call_args.kwargs["reason"]
        # Owner-facing wording is unchanged: prefix plus the masked message,
        # no exception type exposed.
        assert reason.startswith("Agent error: ")
        assert "RuntimeError" not in reason
        _assert_no_secrets(reason, "reported reason")

    def test_owner_and_agent_api_responses_stay_clean_end_to_end(
        self, client, auth_headers
    ):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        db = _get_db()
        user = db.query(User).first()
        row = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
        assert row is not None

        config = AgentConfig(
            alethra_api_key="test-key",
            alethra_api_url="http://localhost:8000",
            poll_interval_seconds=1,
            max_reconnect_attempts=3,
            reconnect_interval_seconds=0,
        )
        agent = ExecutionAgent(
            config,
            IBKRClient(_ExplodingTransport(RuntimeError(_SENSITIVE_ERROR))),
            _DbAgentClient(db, user.id, [row]),
        )

        target, handler, stream = self._capture_agent_log()
        try:
            agent.connect()
            agent.process_order_intents()
        finally:
            target.removeHandler(handler)

        _assert_no_secrets(stream.getvalue(), "agent log")

        owner_view = client.get(
            f"/api/v1/execution/intents/{intent_id}", headers=auth_headers
        )
        assert owner_view.status_code == 200, owner_view.text
        assert owner_view.json()["status"] == "rejected"
        assert "<path>" in owner_view.json()["rejection_reason"]
        assert "<redacted-url>" in owner_view.json()["rejection_reason"]
        _assert_no_secrets(owner_view.text, "owner response")

        # The agent-facing HTTP response for the same raw text.
        second = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        agent_view = client.post(
            f"/api/v1/execution-agent/order-intents/{second['intent_id']}/rejected",
            headers=auth_headers,
            json={"reason": _SENSITIVE_ERROR},
        )
        assert agent_view.status_code == 200, agent_view.text
        _assert_no_secrets(agent_view.text, "agent API response")

    def test_rejected_is_terminal_and_a_second_report_is_refused(
        self, client, auth_headers
    ):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = intent["intent_id"]

        first = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/rejected",
            headers=auth_headers,
            json={"reason": "Insufficient margin"},
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "rejected"

        second = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/rejected",
            headers=auth_headers,
            json={"reason": _SENSITIVE_ERROR},
        )
        assert second.status_code == 409, second.text

        owner_view = client.get(
            f"/api/v1/execution/intents/{intent_id}", headers=auth_headers
        )
        assert owner_view.status_code == 200
        assert owner_view.json()["status"] == "rejected"
        assert owner_view.json()["rejection_reason"] == "Insufficient margin"

    def test_uncertain_and_cancelled_transitions_are_unchanged(
        self, client, auth_headers
    ):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)

        uncertain = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{uncertain['intent_id']}/uncertain",
            headers=auth_headers,
            json={"reason": "Ambiguous broker state: 2 matching orders"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "uncertain"
        again = client.post(
            f"/api/v1/execution-agent/order-intents/{uncertain['intent_id']}/uncertain",
            headers=auth_headers,
            json={"reason": "Ambiguous broker state: 2 matching orders"},
        )
        assert again.status_code == 409, again.text

        cancelled = _create_and_authorize_intent(client, auth_headers, portfolio_id)
        submitted = client.post(
            f"/api/v1/execution-agent/order-intents/{cancelled['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000001"},
        )
        assert submitted.status_code == 200, submitted.text
        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{cancelled['intent_id']}/cancelled",
            headers=auth_headers,
            json={"reason": "Order cancelled by broker"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        again = client.post(
            f"/api/v1/execution-agent/order-intents/{cancelled['intent_id']}/cancelled",
            headers=auth_headers,
            json={"reason": "Order cancelled by broker"},
        )
        assert again.status_code == 409, again.text
