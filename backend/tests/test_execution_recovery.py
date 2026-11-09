"""Tests for execution agent restart recovery and durable dedup.

Covers the safety invariants that must survive a process restart:
- Poll returns submitted intents with their durable broker_ref
- Restart reconciliation re-adopts the real broker_ref (not the symbol)
- Lost-acknowledgement recovery adopts a matching unclaimed broker order
  instead of resubmitting
- Ambiguous broker state is reported 'uncertain' — never guessed
- Fill reports are idempotent on the platform (no double-count after restart)
- Execution kill switch blocks new submissions (fail-closed)
- Paper/live trading mode separation in agent config

All tests use FakeBrokerTransport — no IBKR connection required.
"""
from datetime import timedelta

import pytest
from unittest.mock import MagicMock

from tests.conftest import TestSession
from app.models.user import User
from app.models.execution import OrderIntent, FillRecord, IntentStatus
from app.models.kill_switch import KillSwitchScope
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from app.services.execution_agent_api import (
    agent_report_submitted,
    agent_report_fill,
    agent_report_uncertain,
)
from app.execution_agent.broker_transport import (
    FakeBrokerTransport,
    BrokerOrderStatus,
)
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.config import AgentConfig, load_config_from_env
from app.execution_agent.alethra_client import OrderIntentDTO
from app.lib.timeutil import utcnow


def _make_intent(
    intent_id="rec-intent-001",
    symbol="US:AAPL",
    side="BUY",
    quantity=10,
    order_type="MARKET",
    limit_price=None,
    status="authorized",
    broker_ref=None,
):
    return OrderIntentDTO(
        intent_id=intent_id,
        user_id="user-1",
        portfolio_id="portfolio-1",
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        status=status,
        expires_at=(utcnow() + timedelta(minutes=5)).isoformat(),
        created_at=utcnow().isoformat(),
        broker_ref=broker_ref,
    )


def _make_agent(fill_mode="immediate", fill_price=150.0, transport=None):
    config = AgentConfig(
        alethra_api_key="test-key",
        alethra_api_url="http://localhost:8000",
        poll_interval_seconds=1,
        max_reconnect_attempts=3,
        reconnect_interval_seconds=0,
    )
    if transport is None:
        transport = FakeBrokerTransport(fill_mode=fill_mode, fill_price=fill_price)
    ibkr_client = IBKRClient(transport)

    alethra_client = MagicMock()
    alethra_client.poll_order_intents.return_value = []
    alethra_client.get_control_status.return_value = {"kill_switch_active": False}
    alethra_client.get_claimed_broker_refs.return_value = []

    agent = ExecutionAgent(config, ibkr_client, alethra_client)
    agent.connect()
    return agent, transport, alethra_client


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
# 1. Transport: get_recent_orders
# ──────────────────────────────────────────────

class TestGetRecentOrders:

    def test_returns_all_orders_since(self):
        transport = FakeBrokerTransport(fill_mode="delayed", seed=0)
        transport._rng.random = lambda: 0.99
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)
        transport.submit_order("US:MSFT", "SELL", 5)

        recent = transport.get_recent_orders(since=utcnow() - timedelta(minutes=1))
        assert len(recent) == 2

    def test_empty_when_since_after_all_orders(self):
        transport = FakeBrokerTransport(fill_mode="immediate")
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)

        assert transport.get_recent_orders(since=utcnow() + timedelta(minutes=1)) == []

    def test_includes_terminal_status_orders(self):
        transport = FakeBrokerTransport(fill_mode="reject")
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)

        recent = transport.get_recent_orders(since=utcnow() - timedelta(minutes=1))
        assert len(recent) == 1
        assert recent[0].status == BrokerOrderStatus.REJECTED


# ──────────────────────────────────────────────
# 2. Platform poll contract
# ──────────────────────────────────────────────

class TestPollContract:

    def test_dto_parses_full_api_response(self):
        """The poll response includes broker_ref/rejection_reason keys; the
        agent DTO must accept them (OrderIntentDTO(**item) is how the client
        parses each poll item)."""
        item = {
            "intent_id": "i-1",
            "user_id": "00000000-0000-0000-0000-000000000001",
            "portfolio_id": "00000000-0000-0000-0000-000000000002",
            "symbol": "US:AAPL",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
            "limit_price": None,
            "status": "submitted",
            "broker_ref": "FAKE-000001",
            "rejection_reason": None,
            "expires_at": utcnow().isoformat(),
            "created_at": utcnow().isoformat(),
        }
        dto = OrderIntentDTO(**item)
        assert dto.broker_ref == "FAKE-000001"
        assert dto.status == "submitted"

    def test_poll_includes_submitted_intent_with_broker_ref(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "IBKR-777"},
        )
        assert resp.status_code == 200

        resp = client.get(
            "/api/v1/execution-agent/order-intents", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "submitted"
        assert data[0]["broker_ref"] == "IBKR-777"

    def test_poll_keeps_submitted_intent_past_expiry(self, client, auth_headers):
        """Submitted orders may fill after the authorization window expires;
        the agent must keep monitoring them."""
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "IBKR-778"},
        )

        db = TestSession()
        db_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first()
        db_intent.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()

        resp = client.get(
            "/api/v1/execution-agent/order-intents", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "submitted"


# ──────────────────────────────────────────────
# 3. Fill report idempotency (server-side)
# ──────────────────────────────────────────────

class TestFillReportIdempotency:

    def test_same_fill_reported_twice_returns_same_record(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = TestSession()
        user = db.query(User).first()
        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-100")

        fill_time = utcnow()
        first = agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=10, price=150.0, fill_time=fill_time,
            broker_ref="IBKR-100",
        )
        second = agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=10, price=150.0, fill_time=fill_time,
            broker_ref="IBKR-100",
        )
        assert first.id == second.id
        assert db.query(FillRecord).filter(
            FillRecord.order_intent_id == first.order_intent_id
        ).count() == 1

    def test_distinct_fills_are_both_recorded(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id, quantity=10)

        db = TestSession()
        user = db.query(User).first()
        agent_report_submitted(db, intent["intent_id"], user.id, "IBKR-101")

        agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=5, price=150.0, fill_time=utcnow(),
            broker_ref="IBKR-101",
        )
        agent_report_fill(
            db, intent["intent_id"], user.id,
            quantity=5, price=150.5, fill_time=utcnow() + timedelta(seconds=1),
            broker_ref="IBKR-101",
        )

        db_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent["intent_id"]
        ).first()
        assert db_intent.status == IntentStatus.filled
        assert db.query(FillRecord).filter(
            FillRecord.order_intent_id == db_intent.id
        ).count() == 2

    def test_restarted_agent_rereport_does_not_double_count(self, client, auth_headers):
        """End-to-end: partial fill reported, agent restarts, re-reports the
        same fill via the HTTP endpoint — quantity must not double-count."""
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id, quantity=10)
        intent_id = intent["intent_id"]

        client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/submitted",
            headers=auth_headers,
            json={"broker_ref": "FAKE-000009"},
        )
        fill_payload = {
            "quantity": 5,
            "price": 150.0,
            "fill_time": utcnow().isoformat(),
            "broker_ref": "FAKE-000009",
            "commission": 1.0,
        }
        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/fills",
            headers=auth_headers,
            json=fill_payload,
        )
        assert resp.status_code == 201

        # "Restart": identical re-report (same broker_ref, fill_time, quantity)
        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/fills",
            headers=auth_headers,
            json=fill_payload,
        )
        assert resp.status_code == 201

        db = TestSession()
        db_intent = db.query(OrderIntent).filter(
            OrderIntent.intent_id == intent_id
        ).first()
        assert db_intent.status == IntentStatus.submitted  # 5/10 — not 10/10
        assert db.query(FillRecord).filter(
            FillRecord.order_intent_id == db_intent.id
        ).count() == 1

    def test_submitted_report_conflict_on_different_ref(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "IBKR-A"},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/submitted",
            headers=auth_headers,
            json={"broker_ref": "IBKR-B"},
        )
        assert resp.status_code == 409


# ──────────────────────────────────────────────
# 4. Uncertain status (ambiguous broker state)
# ──────────────────────────────────────────────

class TestUncertainStatus:

    def test_agent_report_uncertain(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = TestSession()
        user = db.query(User).first()
        result = agent_report_uncertain(
            db, intent["intent_id"], user.id, "Ambiguous broker state"
        )
        assert result.status == IntentStatus.uncertain
        assert result.rejection_reason == "Ambiguous broker state"

    def test_uncertain_endpoint(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent['intent_id']}/uncertain",
            headers=auth_headers,
            json={"reason": "2 matching unclaimed broker orders"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "uncertain"

    def test_uncertain_intent_removed_from_poll(self, client, auth_headers):
        portfolio_id = _create_user_and_portfolio(client, auth_headers)
        intent = _create_and_authorize_intent(client, auth_headers, portfolio_id)

        db = TestSession()
        user = db.query(User).first()
        agent_report_uncertain(db, intent["intent_id"], user.id, "ambiguous")

        resp = client.get(
            "/api/v1/execution-agent/order-intents", headers=auth_headers
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 0


# ──────────────────────────────────────────────
# 5. Restart reconciliation & lost-ack recovery
# ──────────────────────────────────────────────

class TestRestartRecovery:

    def test_reconcile_adopts_platform_broker_ref(self):
        """After restart, a submitted intent's broker_ref comes from the
        platform — never the symbol."""
        agent, transport, alethra = _make_agent()
        submitted = _make_intent(status="submitted", broker_ref="IBKR-555")
        alethra.poll_order_intents.return_value = [submitted]

        agent.reconcile_on_startup()

        assert agent._submitted_intents["rec-intent-001"] == "IBKR-555"
        assert "IBKR-555" in agent._claimed_broker_refs

    def test_reconciled_intent_monitors_without_resubmitting(self):
        agent, transport, alethra = _make_agent(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        submitted = _make_intent(
            status="submitted", broker_ref=result.broker_order_id
        )
        alethra.poll_order_intents.return_value = [submitted]

        agent.reconcile_on_startup()
        agent.process_order_intents()

        assert transport._order_counter == 1  # no resubmission
        assert alethra.report_fill.call_count >= 1
        assert alethra.report_order_submitted.call_count == 0

    def test_lost_ack_recovered_without_resubmission(self):
        """Crash between submit_order and report_order_submitted: the platform
        still shows 'authorized'. A restarted agent must adopt the live broker
        order instead of submitting a duplicate."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)

        # "Agent instance 1": submits, then the platform report is lost.
        agent1, _, alethra1 = _make_agent(transport=transport)
        alethra1.report_order_submitted.side_effect = Exception("network lost")
        intent = _make_intent()
        agent1._process_single_intent(intent)
        assert transport._order_counter == 1

        # "Agent instance 2" (restart): fresh in-memory state, same broker.
        agent2, _, alethra2 = _make_agent(transport=transport)
        agent2._process_single_intent(intent)

        assert transport._order_counter == 1  # duplicate order NOT created
        assert alethra2.report_order_submitted.call_count == 1
        assert alethra2.report_order_submitted.call_args.kwargs["broker_ref"] == (
            transport.get_recent_orders(utcnow() - timedelta(minutes=1))[0].order_id
        )
        assert alethra2.report_fill.call_count >= 1

    def test_lost_ack_matching_normalizes_instrument_format(self):
        """The real IBKR transport reconstructs instrument_id from the contract
        symbol ('0700.HK' → '700', 'US:AAPL' → 'AAPL'). Lost-ack matching must
        compare normalized forms, or a live HK order is never adopted and the
        agent resubmits a duplicate."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        # Order as IBKR would have created it: contract symbol "700".
        transport.submit_order("700", "BUY", 100)

        agent, _, alethra = _make_agent(transport=transport)
        intent = _make_intent(symbol="0700.HK", quantity=100)

        agent._process_single_intent(intent)

        assert transport._order_counter == 1  # adopted, not duplicated
        alethra.report_order_uncertain.assert_not_called()
        alethra.report_order_submitted.assert_called_once()
        assert alethra.report_fill.call_count >= 1

    def test_ambiguous_state_reported_uncertain_never_resubmitted(self):
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        # Two identical unclaimed broker orders (e.g. two crashed submissions).
        transport.submit_order("US:AAPL", "BUY", 10)
        transport.submit_order("US:AAPL", "BUY", 10)

        agent, _, alethra = _make_agent(transport=transport)
        intent = _make_intent()
        agent._process_single_intent(intent)

        assert transport._order_counter == 2  # no third order
        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_uncertain.assert_called_once()
        assert "mbiguous" in alethra.report_order_uncertain.call_args.kwargs["reason"]
        assert "rec-intent-001" in agent._completed_intents

    def test_no_broker_match_submits_normally(self):
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        agent, _, alethra = _make_agent(transport=transport)
        intent = _make_intent()

        agent._process_single_intent(intent)

        assert transport._order_counter == 1
        alethra.report_order_submitted.assert_called_once()
        alethra.report_order_uncertain.assert_not_called()

    def test_platform_claimed_refs_excluded_from_matching(self):
        """Broker orders mapped to other intents (any status, e.g. filled)
        are excluded via the platform's claimed refs — a fresh agent must not
        adopt them for a new intent."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)  # another intent's order

        agent, _, alethra = _make_agent(transport=transport)
        alethra.get_claimed_broker_refs.return_value = [result.broker_order_id]
        intent = _make_intent()

        agent._process_single_intent(intent)

        assert transport._order_counter == 2  # submitted fresh, did not adopt
        alethra.report_order_submitted.assert_called_once()
        alethra.report_order_uncertain.assert_not_called()

    def test_claims_fetch_failure_fails_closed(self):
        """If platform claims cannot be read, the agent must not guess — skip
        submission (no new order, no adoption, no rejection)."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)  # would match if claims were []

        agent, _, alethra = _make_agent(transport=transport)
        alethra.get_claimed_broker_refs.side_effect = Exception("platform error")
        intent = _make_intent()

        agent._process_single_intent(intent)

        assert transport._order_counter == 1  # no resubmission
        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_rejected.assert_not_called()
        alethra.report_order_uncertain.assert_not_called()

    def test_identical_second_intent_does_not_adopt_first_order(self):
        """Two identical authorized intents in the same process: the first
        submits and claims its broker order; the second must submit fresh
        rather than adopt the first's order."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        agent, _, alethra = _make_agent(transport=transport)

        intent_a = _make_intent(intent_id="rec-intent-A")
        intent_b = _make_intent(intent_id="rec-intent-B")
        agent._process_single_intent(intent_a)
        agent._process_single_intent(intent_b)

        assert transport._order_counter == 2
        refs = {c.kwargs["broker_ref"] for c in alethra.report_order_submitted.call_args_list}
        assert len(refs) == 2

    def test_rejected_order_never_adopted(self):
        """A REJECTED broker order was never live — reprocessing the intent
        must not adopt it as a lost submission."""
        transport = FakeBrokerTransport(fill_mode="reject")
        agent, _, alethra = _make_agent(transport=transport)
        intent = _make_intent()

        agent._process_single_intent(intent)
        alethra.report_order_rejected.assert_called_once()
        assert alethra.report_order_submitted.call_count == 0

    def test_broker_error_during_match_skips_submission(self):
        """If broker state cannot be verified, the agent must not submit
        blindly — skip and retry next poll."""
        transport = FakeBrokerTransport(fill_mode="immediate")
        agent, _, alethra = _make_agent(transport=transport)
        agent.ibkr_client.get_recent_orders = MagicMock(
            side_effect=Exception("transport down")
        )
        intent = _make_intent()

        agent._process_single_intent(intent)

        assert transport._order_counter == 0
        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_rejected.assert_not_called()
        alethra.report_order_uncertain.assert_not_called()

    def test_submitted_intent_without_broker_ref_reported_uncertain(self):
        agent, transport, alethra = _make_agent()
        submitted = _make_intent(status="submitted", broker_ref=None)
        alethra.poll_order_intents.return_value = [submitted]

        agent.process_order_intents()

        alethra.report_order_uncertain.assert_called_once()
        alethra.report_order_submitted.assert_not_called()
        assert transport._order_counter == 0


# ──────────────────────────────────────────────
# 6. Kill switch integration
# ──────────────────────────────────────────────

class TestKillSwitch:

    def test_control_status_endpoint(self, client, auth_headers):
        resp = client.get(
            "/api/v1/execution-agent/control-status", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["kill_switch_active"] is False

        db = TestSession()
        activate_kill_switch(db, KillSwitchScope.execution, "test-runner", "halt")
        resp = client.get(
            "/api/v1/execution-agent/control-status", headers=auth_headers
        )
        assert resp.json()["kill_switch_active"] is True

        deactivate_kill_switch(db, KillSwitchScope.execution)
        resp = client.get(
            "/api/v1/execution-agent/control-status", headers=auth_headers
        )
        assert resp.json()["kill_switch_active"] is False

    def test_active_kill_switch_blocks_new_submissions(self, client, auth_headers):
        db = TestSession()
        activate_kill_switch(db, KillSwitchScope.execution, "test-runner")

        transport = FakeBrokerTransport(fill_mode="immediate")
        agent, _, alethra = _make_agent(transport=transport)
        intent = _make_intent()

        agent._process_single_intent(intent, kill_switch_active=True)

        assert transport._order_counter == 0
        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_rejected.assert_not_called()

    def test_kill_switch_does_not_block_fill_monitoring(self):
        """In-flight orders keep being monitored while the kill switch is on."""
        transport = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        agent, _, alethra = _make_agent(transport=transport)
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        agent._submitted_intents["rec-intent-001"] = result.broker_order_id

        submitted = _make_intent(
            status="submitted", broker_ref=result.broker_order_id
        )
        alethra.poll_order_intents.return_value = [submitted]
        agent.process_order_intents(kill_switch_active=True)

        assert alethra.report_fill.call_count >= 1

    def test_check_kill_switch_fails_closed(self):
        agent, _, alethra = _make_agent()
        alethra.get_control_status.side_effect = Exception("platform unreachable")

        assert agent._check_kill_switch() is True

    def test_run_loop_passes_kill_switch_to_processing(self):
        agent, transport, alethra = _make_agent(fill_mode="immediate")
        alethra.get_control_status.return_value = {"kill_switch_active": True}
        alethra.poll_order_intents.return_value = [_make_intent()]

        agent.process_order_intents(kill_switch_active=agent._check_kill_switch())

        assert transport._order_counter == 0


# ──────────────────────────────────────────────
# 7. Config: paper/live separation
# ──────────────────────────────────────────────

class TestTradingModeConfig:

    def test_paper_mode_default_valid(self):
        config = AgentConfig(alethra_api_key="k", alethra_api_url="http://x")
        config.validate()
        assert config.trading_mode == "paper"

    def test_live_mode_requires_allow_live(self):
        config = AgentConfig(
            alethra_api_key="k", alethra_api_url="http://x", trading_mode="live"
        )
        with pytest.raises(ValueError, match="allow_live"):
            config.validate()

    def test_live_mode_with_allow_live_valid(self):
        config = AgentConfig(
            alethra_api_key="k",
            alethra_api_url="http://x",
            trading_mode="live",
            allow_live=True,
        )
        config.validate()

    def test_invalid_trading_mode_rejected(self):
        config = AgentConfig(
            alethra_api_key="k", alethra_api_url="http://x", trading_mode="yolo"
        )
        with pytest.raises(ValueError, match="trading_mode"):
            config.validate()

    def test_env_live_without_allow_live_rejected(self, monkeypatch):
        monkeypatch.setenv("ALETHRA_API_KEY", "k")
        monkeypatch.setenv("ALETHRA_API_URL", "http://x")
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.delenv("ALLOW_LIVE_TRADING", raising=False)
        with pytest.raises(ValueError, match="allow_live"):
            load_config_from_env()

    def test_env_live_with_allow_live_accepted(self, monkeypatch):
        monkeypatch.setenv("ALETHRA_API_KEY", "k")
        monkeypatch.setenv("ALETHRA_API_URL", "http://x")
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
        config = load_config_from_env()
        assert config.trading_mode == "live"
        assert config.allow_live is True

    def test_env_defaults_to_paper(self, monkeypatch):
        monkeypatch.setenv("ALETHRA_API_KEY", "k")
        monkeypatch.setenv("ALETHRA_API_URL", "http://x")
        monkeypatch.delenv("TRADING_MODE", raising=False)
        config = load_config_from_env()
        assert config.trading_mode == "paper"
        assert config.allow_live is False
