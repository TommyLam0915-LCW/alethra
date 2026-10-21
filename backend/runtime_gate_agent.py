#!/usr/bin/env python3
"""Runtime gate agent: separate process with real HTTP calls.

This agent is spawned as a separate process by runtime_gate.py.
It uses:
- Real HTTP calls to the platform API (not TestClient)
- FakeBrokerTransport for offline broker simulation
- FillDeliveryJournal for durable tracking
- Environment variables for configuration
"""
import os
import sys
import time
import logging
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("runtime-gate-agent")

# Import after logging setup
from app.execution_agent.config import AgentConfig
from app.execution_agent.agent import ExecutionAgent
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.broker_transport import FakeBrokerTransport
from app.execution_agent.alethra_client import AlethraAPIClient
from app.execution_agent.fill_journal import FillDeliveryJournal
from app.lib.timeutil import utcnow


class FailingAlethraClient(AlethraAPIClient):
    """Wrapper that fails the first fill upload if SIMULATE_UPLOAD_FAILURE is set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fill_upload_count = 0
        self._should_fail = os.environ.get("SIMULATE_UPLOAD_FAILURE") == "1"

    def report_fill(self, fill):
        if self._should_fail:
            self._fill_upload_count += 1
            logger.warning(f"SIMULATED FAILURE: Failing fill upload (count={self._fill_upload_count})")
            raise ConnectionError("Simulated upload failure")

        return super().report_fill(fill)


def main():
    logger.info("Starting runtime gate agent")

    # Load configuration from environment
    api_url = os.environ.get("ALETHRA_API_URL", "http://127.0.0.1:18765")
    api_key = os.environ.get("ALETHRA_API_KEY", "test-key")
    client_id = int(os.environ.get("IB_CLIENT_ID", "1"))
    journal_path = os.environ.get("FILL_DELIVERY_JOURNAL_PATH")
    poll_interval = int(os.environ.get("POLL_INTERVAL", "1"))

    if not journal_path:
        logger.error("FILL_DELIVERY_JOURNAL_PATH not set")
        sys.exit(1)

    logger.info(f"Configuration: api_url={api_url}, client_id={client_id}, journal={journal_path}")

    # Create config
    config = AgentConfig(
        alethra_api_key=api_key,
        alethra_api_url=api_url,
        ib_client_id=client_id,
        poll_interval_seconds=poll_interval,
        trading_mode="paper",
        allow_live=False,
    )

    # Create broker transport (fake, offline) with durable submission log
    submission_log = os.environ.get("BROKER_SUBMISSION_LOG")
    transport = FakeBrokerTransport(
        fill_mode="immediate",
        fill_price=150.0,
        submission_log_path=submission_log,
    )
    transport.connect()

    # Create IBKR client wrapper
    ibkr_client = IBKRClient(transport)

    # Create Alethra client (with optional failure simulation)
    if os.environ.get("SIMULATE_UPLOAD_FAILURE") == "1":
        logger.info("Upload failure simulation ENABLED")
        alethra_client = FailingAlethraClient(
            api_url=api_url,
            api_token=api_key,
        )
    else:
        logger.info("Upload failure simulation DISABLED")
        alethra_client = AlethraAPIClient(
            api_url=api_url,
            api_token=api_key,
        )

    # Create execution agent
    agent = ExecutionAgent(config, ibkr_client, alethra_client)

    # Override journal path
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
            # Check connection
            if not ibkr_client.is_connected():
                logger.warning("Lost connection to broker")
                break

            # Check kill switch
            kill_switch_active = agent._check_kill_switch()

            # Process intents
            agent.process_order_intents(kill_switch_active=kill_switch_active)

            # Sleep
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
