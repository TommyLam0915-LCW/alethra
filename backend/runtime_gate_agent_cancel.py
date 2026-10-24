#!/usr/bin/env python3
"""Agent for cancellation-recovery scenario."""
import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("cancel-agent")

from app.execution_agent.config import AgentConfig
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.broker_transport import FakeBrokerTransport
from app.execution_agent.alethra_client import AlethraAPIClient
from app.execution_agent.fill_journal import FillDeliveryJournal
from app.lib.timeutil import utcnow


class FailingCancelReportClient(AlethraAPIClient):
    """Client that fails cancellation report if FAIL_CANCEL_REPORT is set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._should_fail = os.environ.get("FAIL_CANCEL_REPORT") == "1"

    def report_order_cancelled(self, intent_id, reason):
        if self._should_fail:
            logger.warning(f"SIMULATED FAILURE: Failing cancellation report for {intent_id}")
            raise ConnectionError("Platform unreachable")
        return super().report_order_cancelled(intent_id, reason)


def main():
    logger.info("Starting cancellation-recovery agent")

    api_url = os.environ.get("ALETHRA_API_URL", "http://127.0.0.1:18767")
    api_key = os.environ.get("ALETHRA_API_KEY", "test-key")
    client_id = int(os.environ.get("IB_CLIENT_ID", "1"))
    journal_path = os.environ.get("FILL_DELIVERY_JOURNAL_PATH")
    submission_log = os.environ.get("BROKER_SUBMISSION_LOG")
    state_path = os.environ.get("BROKER_STATE_PATH")
    poll_interval = int(os.environ.get("POLL_INTERVAL", "1"))

    if not journal_path:
        logger.error("FILL_DELIVERY_JOURNAL_PATH not set")
        sys.exit(1)

    logger.info(f"Configuration: api_url={api_url}, client_id={client_id}")

    config = AgentConfig(
        alethra_api_key=api_key,
        alethra_api_url=api_url,
        ib_client_id=client_id,
        poll_interval_seconds=poll_interval,
        trading_mode="paper",
        allow_live=False,
    )

    transport = FakeBrokerTransport(
        fill_mode="pending",  # Order stays submitted, doesn't fill
        fill_price=150.0,
        submission_log_path=submission_log,
        state_path=state_path,
    )
    transport.connect()

    ibkr_client = IBKRClient(transport)

    # Use failing client if configured
    if os.environ.get("FAIL_CANCEL_REPORT") == "1":
        logger.info("Cancel-report failure simulation ENABLED")
        alethra_client = FailingCancelReportClient(
            api_url=api_url,
            api_token=api_key,
        )
    else:
        logger.info("Cancel-report failure simulation DISABLED")
        alethra_client = AlethraAPIClient(
            api_url=api_url,
            api_token=api_key,
        )

    agent = ExecutionAgent(config, ibkr_client, alethra_client)

    agent.fill_delivery_journal = FillDeliveryJournal(
        journal_path=journal_path,
        agent_id=str(client_id)
    )

    logger.info("Agent initialized, connecting...")
    agent.connect()

    logger.info("Running reconciliation...")
    agent.reconcile_on_startup()

    logger.info("Replaying pending fills...")
    agent._replay_pending_fill_deliveries()

    logger.info("Entering main loop...")
    agent.running = True

    try:
        while agent.running:
            if not ibkr_client.is_connected():
                logger.warning("Lost connection to broker")
                break

            kill_switch_active = agent._check_kill_switch()
            agent.process_order_intents(kill_switch_active=kill_switch_active)
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
        agent.running = False

    finally:
        logger.info("Disconnecting...")
        agent.disconnect()
        logger.info("Agent stopped")


if __name__ == "__main__":
    main()
