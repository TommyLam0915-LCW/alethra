#!/usr/bin/env python3
"""Agent for acknowledgement-loss scenario.

This agent can be configured to crash after successfully uploading a fill,
simulating the case where the acknowledgement is lost before the journal
entry is removed.
"""
import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("ack-loss-agent")

from app.execution_agent.config import AgentConfig
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.broker_transport import FakeBrokerTransport
from app.execution_agent.alethra_client import AlethraAPIClient
from app.execution_agent.fill_journal import FillDeliveryJournal
from app.lib.timeutil import utcnow


class CrashAfterUploadClient(AlethraAPIClient):
    """Client that crashes after successful upload if CRASH_AFTER_UPLOAD is set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._should_crash = os.environ.get("CRASH_AFTER_UPLOAD") == "1"
        self._upload_count = 0

    def report_fill(self, fill):
        result = super().report_fill(fill)
        if self._should_crash:
            self._upload_count += 1
            logger.warning(f"SIMULATED CRASH: Upload succeeded (count={self._upload_count}), crashing now")
            # Crash immediately without removing from journal
            sys.exit(42)  # Special exit code to indicate simulated crash
        return result


def main():
    logger.info("Starting acknowledgement-loss agent")

    api_url = os.environ.get("ALETHRA_API_URL", "http://127.0.0.1:18766")
    api_key = os.environ.get("ALETHRA_API_KEY", "test-key")
    client_id = int(os.environ.get("IB_CLIENT_ID", "1"))
    journal_path = os.environ.get("FILL_DELIVERY_JOURNAL_PATH")
    submission_log = os.environ.get("BROKER_SUBMISSION_LOG")
    poll_interval = int(os.environ.get("POLL_INTERVAL", "1"))

    if not journal_path:
        logger.error("FILL_DELIVERY_JOURNAL_PATH not set")
        sys.exit(1)

    logger.info(f"Configuration: api_url={api_url}, client_id={client_id}, journal={journal_path}")

    config = AgentConfig(
        alethra_api_key=api_key,
        alethra_api_url=api_url,
        ib_client_id=client_id,
        poll_interval_seconds=poll_interval,
        trading_mode="paper",
        allow_live=False,
    )

    transport = FakeBrokerTransport(
        fill_mode="immediate",
        fill_price=150.0,
        submission_log_path=submission_log,
    )
    transport.connect()

    ibkr_client = IBKRClient(transport)

    # Use crash client if configured
    if os.environ.get("CRASH_AFTER_UPLOAD") == "1":
        logger.info("Crash-after-upload simulation ENABLED")
        alethra_client = CrashAfterUploadClient(
            api_url=api_url,
            api_token=api_key,
        )
    else:
        logger.info("Crash-after-upload simulation DISABLED")
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
