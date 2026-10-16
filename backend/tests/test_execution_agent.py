"""Offline validation tests for the execution agent.

Tests the agent's control flow using fake transports — no real broker or
platform connection required. Covers:

- Happy path: immediate fill
- Duplicate intent (idempotency)
- Partial fills
- Rejected orders
- Cancelled orders
- Broker disconnect and reconnect
- Expired intent
- Unresolved instrument metadata
- Kill switch
- User/agent isolation (processed intent tracking)
- Restart after ack loss (re-processing same intents)
- Config safety (live mode guard)
"""

import pytest
import uuid
from datetime import datetime, timedelta

from app.execution_agent.config import AgentConfig
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.transport import (
    FakeBrokerTransport,
    FakeBrokerConfig,
    RejectionMode,
)
from app.execution_agent.platform_transport import (
    FakePlatformTransport,
    FakePlatformConfig,
    IntentDTO,
)


def _make_config(**overrides) -> AgentConfig:
    defaults = dict(
        mode="paper",
        alethra_api_key="test-key",
        alethra_api_url="https://test.alethra.com",
        poll_interval_seconds=1,
        reconnect_interval_seconds=0,
        max_reconnect_attempts=3,
    )
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_intent(
    instrument_id="US:AAPL",
    side="BUY",
    quantity=100,
    order_type="MARKET",
    limit_price=None,
    expires_at=None,
    idempotency_key=None,
) -> IntentDTO:
    return IntentDTO(
        intent_id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        portfolio_id=str(uuid.uuid4()),
        instrument_id=instrument_id,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        authorization_token="test-token",
        expires_at=expires_at or datetime.utcnow() + timedelta(minutes=5),
        idempotency_key=idempotency_key,
    )


class TestHappyPath:
    def test_immediate_fill(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate", fill_price=150.0)

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 1
        assert intent.intent_id in platform_cfg.submitted_intents
        assert intent.intent_id in platform_cfg.fills_reported
        fills = platform_cfg.fills_reported[intent.intent_id]
        assert len(fills) == 1
        assert fills[0].quantity == 100
        assert fills[0].price == 150.0

    def test_multiple_intents_in_one_cycle(self):
        intents = [_make_intent() for _ in range(3)]
        platform_cfg = FakePlatformConfig(intents=intents)
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 3
        for intent in intents:
            assert intent.intent_id in platform_cfg.submitted_intents
            assert intent.intent_id in platform_cfg.fills_reported


class TestDuplicateIntent:
    def test_duplicate_idempotency_key_skipped(self):
        key = "idem-key-123"
        intent1 = _make_intent(idempotency_key=key)
        intent2 = _make_intent(idempotency_key=key)
        platform_cfg = FakePlatformConfig(intents=[intent1, intent2])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 1
        assert intent1.intent_id in platform_cfg.submitted_intents
        assert intent2.intent_id not in platform_cfg.submitted_intents

    def test_already_processed_intent_id_skipped(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()

        agent.process_cycle()
        assert intent.intent_id in platform_cfg.submitted_intents

        platform_cfg.intents = [intent]
        platform_cfg._poll_count = 0
        agent.process_cycle()

        assert len(platform_cfg.submitted_intents) == 1


class TestPartialFills:
    def test_partial_fill_reported(self):
        intent = _make_intent(quantity=100)
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(
            fill_mode="partial",
            partial_fill_quantity=40,
            fill_price=150.0,
        )

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.process_cycle()

        assert intent.intent_id in platform_cfg.submitted_intents
        fills = platform_cfg.fills_reported[intent.intent_id]
        assert len(fills) == 1
        assert fills[0].quantity == 40
        assert fills[0].price == 150.0


class TestRejectedCancelled:
    def test_rejected_order_reported(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(
            rejection_mode=RejectionMode.IMMEDIATE,
            rejection_reason="Insufficient margin",
        )

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.process_cycle()

        assert intent.intent_id in platform_cfg.rejected_intents
        assert "Insufficient margin" in platform_cfg.rejected_intents[intent.intent_id]
        assert intent.intent_id not in platform_cfg.submitted_intents

    def test_cancelled_after_submit(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker = FakeBrokerTransport(FakeBrokerConfig(fill_mode="never"))
        broker.connect()

        agent = ExecutionAgent(
            config=_make_config(),
            broker=broker,
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.process_cycle()

        assert intent.intent_id in platform_cfg.submitted_intents

        order_id = platform_cfg.submitted_intents[intent.intent_id]["broker_ref"]
        broker.cancel_order(order_id)

        state = broker.get_order_state(order_id)
        assert state.status.value == "Cancelled"


class TestDisconnect:
    def test_disconnect_during_submit(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker = FakeBrokerTransport(FakeBrokerConfig(fill_mode="immediate"))
        broker.connect()
        broker.disconnect()

        agent = ExecutionAgent(
            config=_make_config(max_reconnect_attempts=0),
            broker=broker,
            platform=FakePlatformTransport(platform_cfg),
        )

        agent.process_cycle()

        assert intent.intent_id in platform_cfg.rejected_intents
        assert "reconnect failed" in platform_cfg.rejected_intents[intent.intent_id].lower()

    def test_reconnect_succeeds(self):
        broker = FakeBrokerTransport(FakeBrokerConfig(fill_mode="immediate"))
        broker.connect()
        broker.disconnect()
        assert not broker.is_connected()

        broker.simulate_reconnect()
        assert broker.is_connected()


class TestExpiredIntent:
    def test_expired_intent_rejected(self):
        intent = _make_intent(expires_at=datetime.utcnow() - timedelta(minutes=1))
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.process_cycle()

        assert intent.intent_id in platform_cfg.rejected_intents
        assert "expired" in platform_cfg.rejected_intents[intent.intent_id].lower()
        assert intent.intent_id not in platform_cfg.submitted_intents


class TestUnresolvedMetadata:
    def test_unresolved_instrument_rejected(self):
        intent = _make_intent(instrument_id="HK:9999")
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(
            fill_mode="immediate",
            reject_instrument_ids={"HK:9999"},
        )

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.process_cycle()

        assert intent.intent_id in platform_cfg.rejected_intents
        assert "unresolved" in platform_cfg.rejected_intents[intent.intent_id].lower()


class TestKillSwitch:
    def test_kill_switch_blocks_processing(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(
            intents=[intent],
            kill_switch_active=True,
        )
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 0
        assert intent.intent_id not in platform_cfg.submitted_intents
        assert intent.intent_id not in platform_cfg.rejected_intents

    def test_kill_switch_inactive_allows_processing(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(
            intents=[intent],
            kill_switch_active=False,
        )
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 1
        assert intent.intent_id in platform_cfg.submitted_intents


class TestUserAgentIsolation:
    def test_different_users_intents_processed_independently(self):
        user_a_intent = _make_intent()
        user_b_intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[user_a_intent, user_b_intent])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        processed = agent.process_cycle()

        assert processed == 2
        assert user_a_intent.intent_id in platform_cfg.submitted_intents
        assert user_b_intent.intent_id in platform_cfg.submitted_intents

    def test_agent_tracks_processed_intents_across_cycles(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(fill_mode="immediate")

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()

        agent.process_cycle()
        assert intent.intent_id in platform_cfg.submitted_intents

        platform_cfg._poll_count = 0
        platform_cfg.intents = [intent]
        agent.process_cycle()

        assert len(platform_cfg.submitted_intents) == 1


class TestRestartAfterAckLoss:
    def test_restart_reprocesses_unacked_intents(self):
        intent = _make_intent()

        platform_cfg_1 = FakePlatformConfig(intents=[intent])
        broker_1 = FakeBrokerTransport(FakeBrokerConfig(fill_mode="immediate"))
        broker_1.connect()

        agent_1 = ExecutionAgent(
            config=_make_config(),
            broker=broker_1,
            platform=FakePlatformTransport(platform_cfg_1),
        )
        agent_1.process_cycle()
        assert intent.intent_id in platform_cfg_1.submitted_intents

        platform_cfg_2 = FakePlatformConfig(intents=[intent])
        broker_2 = FakeBrokerTransport(FakeBrokerConfig(fill_mode="immediate"))
        broker_2.connect()

        agent_2 = ExecutionAgent(
            config=_make_config(),
            broker=broker_2,
            platform=FakePlatformTransport(platform_cfg_2),
        )
        agent_2.process_cycle()

        assert intent.intent_id in platform_cfg_2.submitted_intents


class TestConfigSafety:
    def test_paper_mode_validates(self):
        config = _make_config(mode="paper")
        config.validate()

    def test_live_mode_without_flag_raises(self):
        import os
        os.environ.pop("ALETHRA_LIVE_TRADING_ENABLED", None)
        config = _make_config(mode="live")
        with pytest.raises(ValueError, match="ALETHRA_LIVE_TRADING_ENABLED"):
            config.validate()

    def test_invalid_mode_raises(self):
        config = _make_config(mode="invalid")
        with pytest.raises(ValueError, match="mode must be"):
            config.validate()

    def test_missing_api_key_raises(self):
        config = _make_config(alethra_api_key=None)
        with pytest.raises(ValueError, match="alethra_api_key"):
            config.validate()


class TestCommissionTracking:
    def test_commission_reported_in_fill(self):
        intent = _make_intent()
        platform_cfg = FakePlatformConfig(intents=[intent])
        broker_cfg = FakeBrokerConfig(
            fill_mode="immediate",
            commission_per_fill=2.50,
        )

        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(broker_cfg),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.process_cycle()

        fills = platform_cfg.fills_reported[intent.intent_id]
        assert fills[0].commission == 2.50


class TestHeartbeat:
    def test_heartbeat_tracked(self):
        platform_cfg = FakePlatformConfig()
        agent = ExecutionAgent(
            config=_make_config(),
            broker=FakeBrokerTransport(FakeBrokerConfig()),
            platform=FakePlatformTransport(platform_cfg),
        )
        agent.broker.connect()
        agent.platform.report_heartbeat(connected=True)

        assert len(platform_cfg.heartbeat_log) == 1
        assert platform_cfg.heartbeat_log[0]["connected"] is True
