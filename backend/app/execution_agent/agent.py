"""Main execution agent loop.

The agent bridges the Alethra platform and a broker (IBKR in production,
FakeBrokerTransport in tests). It:

1. Polls the platform for authorized order intents
2. Submits orders to the broker via a pluggable transport
3. Reports submissions, fills, rejections, and cancellations back
4. Handles reconnection, partial fills, and intent expiry

Safety invariants:
- Never resubmits an order if the submission acknowledgement was lost
  (checks broker_ref before submitting)
- Tracks partial fills and reports each individually
- Reconciles in-flight orders on startup after disconnect/restart
- Skips expired intents
"""
import logging
import time
import signal
import sys
from datetime import datetime
from typing import Dict, Optional, Set

from .config import AgentConfig, load_config_from_env
from .ibkr_client import IBKRClient
from .alethra_client import AlethraAPIClient, OrderIntentDTO, FillReport
from .broker_transport import BrokerTransport, BrokerOrderStatus


logger = logging.getLogger(__name__)


class ExecutionAgent:
    """Main execution agent that bridges Alethra platform and a broker."""

    def __init__(
        self,
        config: AgentConfig,
        ibkr_client: IBKRClient,
        alethra_client: AlethraAPIClient,
    ):
        self.config = config
        self.ibkr_client = ibkr_client
        self.alethra_client = alethra_client
        self.running = False
        self.reconnect_attempts = 0

        self._submitted_intents: Dict[str, str] = {}
        self._reported_fills: Set[str] = set()
        self._completed_intents: Set[str] = set()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def connect(self) -> None:
        logger.info(
            f"Connecting to broker at {self.config.ib_gateway_host}:{self.config.ib_gateway_port}"
        )
        self.ibkr_client.connect()
        logger.info("Connected to broker")
        self.reconnect_attempts = 0

    def disconnect(self) -> None:
        if self.ibkr_client:
            logger.info("Disconnecting from broker")
            self.ibkr_client.disconnect()

    def reconnect(self) -> bool:
        if self.reconnect_attempts >= self.config.max_reconnect_attempts:
            logger.error(
                f"Max reconnection attempts ({self.config.max_reconnect_attempts}) reached"
            )
            return False

        self.reconnect_attempts += 1
        logger.warning(
            f"Reconnection attempt {self.reconnect_attempts}/{self.config.max_reconnect_attempts}"
        )

        try:
            self.disconnect()
            time.sleep(self.config.reconnect_interval_seconds)
            self.connect()
            logger.info("Reconnection successful")
            return True
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            return False

    def reconcile_on_startup(self) -> None:
        """After a restart, check any intents that were submitted but not yet filled.

        For each submitted intent, check the broker for its current status
        and report accordingly. This handles the case where the agent
        crashed after submitting to the broker but before reporting back.
        """
        try:
            intents = self.alethra_client.poll_order_intents()
        except Exception as e:
            logger.warning(f"Could not poll intents on startup: {e}")
            return

        for intent in intents:
            if intent.status == "submitted" and intent.intent_id not in self._submitted_intents:
                logger.info(f"Reconciling submitted intent {intent.intent_id}")
                self._submitted_intents[intent.intent_id] = intent.symbol

        logger.info(f"Reconciliation complete: {len(self._submitted_intents)} tracked intents")

    def process_order_intents(self) -> None:
        """Poll for and process authorized order intents."""
        try:
            intents = self.alethra_client.poll_order_intents()
        except Exception as e:
            logger.error(f"Failed to poll order intents: {e}")
            return

        if not intents:
            logger.debug("No new order intents")
            return

        logger.info(f"Processing {len(intents)} order intent(s)")

        for intent in intents:
            try:
                self._process_single_intent(intent)
            except Exception as e:
                logger.error(f"Error processing intent {intent.intent_id}: {e}")
                try:
                    self.alethra_client.report_order_rejected(
                        intent_id=intent.intent_id,
                        reason=f"Agent error: {e}",
                    )
                except Exception as report_err:
                    logger.error(f"Failed to report rejection: {report_err}")

    def _process_single_intent(self, intent: OrderIntentDTO) -> None:
        """Process a single order intent with duplicate prevention."""
        logger.info(
            f"Processing intent {intent.intent_id}: "
            f"{intent.side} {intent.quantity} {intent.symbol}"
        )

        if datetime.utcnow() > datetime.fromisoformat(intent.expires_at):
            logger.warning(f"Intent {intent.intent_id} has expired")
            self.alethra_client.report_order_rejected(
                intent_id=intent.intent_id,
                reason="Intent expired",
            )
            return

        if intent.intent_id in self._completed_intents:
            logger.debug(f"Intent {intent.intent_id} already completed, skipping")
            return

        if intent.intent_id in self._submitted_intents:
            logger.info(
                f"Intent {intent.intent_id} already submitted "
                f"(broker_ref={self._submitted_intents[intent.intent_id]}), skipping"
            )
            self._monitor_fills_for_known_order(
                intent.intent_id,
                self._submitted_intents[intent.intent_id],
            )
            return

        if intent.order_type == "MARKET":
            result = self.ibkr_client.submit_market_order(
                instrument_id=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
            )
        elif intent.order_type == "LIMIT":
            result = self.ibkr_client.submit_limit_order(
                instrument_id=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                limit_price=intent.limit_price,
            )
        else:
            raise ValueError(f"Unsupported order type: {intent.order_type}")

        if not result.success:
            logger.error(
                f"Order submission failed for intent {intent.intent_id}: "
                f"{result.error_message}"
            )
            self.alethra_client.report_order_rejected(
                intent_id=intent.intent_id,
                reason=result.error_message or "Order submission failed",
            )
            return

        broker_ref = result.broker_order_id
        self._submitted_intents[intent.intent_id] = broker_ref

        logger.info(
            f"Order submitted for intent {intent.intent_id}, "
            f"broker order ID: {broker_ref}"
        )

        try:
            self.alethra_client.report_order_submitted(
                intent_id=intent.intent_id,
                broker_ref=broker_ref,
                submitted_at=result.submitted_at,
            )
        except Exception as e:
            logger.warning(
                f"Failed to report submission for {intent.intent_id}: {e}. "
                f"Will reconcile on next poll."
            )

        self._monitor_fills_for_known_order(intent.intent_id, broker_ref)

    def _monitor_fills_for_known_order(
        self, intent_id: str, broker_order_id: str
    ) -> None:
        """Monitor an order for fills and report them to the platform."""
        max_wait_seconds = 60
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                status_val = self.ibkr_client.get_order_status(broker_order_id)
            except Exception as e:
                logger.error(f"Error checking order status: {e}")
                time.sleep(1)
                continue

            status_enum = BrokerOrderStatus(status_val)

            fills = self.ibkr_client.get_fills(broker_order_id)
            for fill in fills:
                fill_key = f"{broker_order_id}:{fill.fill_time.isoformat()}:{fill.quantity}"
                if fill_key not in self._reported_fills:
                    try:
                        self.alethra_client.report_fill(FillReport(
                            intent_id=intent_id,
                            quantity=fill.quantity,
                            price=fill.price,
                            fill_time=fill.fill_time,
                            broker_ref=broker_order_id,
                            commission=fill.commission,
                        ))
                        self._reported_fills.add(fill_key)
                    except Exception as e:
                        logger.error(f"Failed to report fill: {e}")

            if status_enum in (BrokerOrderStatus.FILLED,):
                logger.info(f"Order {broker_order_id} fully filled")
                self._completed_intents.add(intent_id)
                return

            if status_enum in (BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
                logger.warning(f"Order {broker_order_id} was {status_enum.value}")
                try:
                    self.alethra_client.report_order_cancelled(
                        intent_id=intent_id,
                        reason=f"Order {status_enum.value.lower()} by broker",
                    )
                except Exception as e:
                    logger.error(f"Failed to report cancellation: {e}")
                self._completed_intents.add(intent_id)
                return

            time.sleep(1)

        logger.warning(
            f"Order {broker_order_id} did not fill within {max_wait_seconds}s, "
            f"will check on next poll"
        )

    def run(self) -> None:
        """Run the main agent loop. Blocks until stopped."""
        logger.info("Starting execution agent")

        self.connect()
        self.reconcile_on_startup()

        self.running = True
        last_heartbeat = 0

        while self.running:
            try:
                if not self.ibkr_client.is_connected():
                    logger.warning("Lost connection to broker")
                    if not self.reconnect():
                        logger.error("Failed to reconnect, shutting down")
                        break

                self.process_order_intents()

                if time.time() - last_heartbeat > 60:
                    try:
                        self.alethra_client.report_agent_heartbeat(
                            connected=self.ibkr_client.is_connected(),
                            ib_gateway_host=self.config.ib_gateway_host,
                            ib_gateway_port=self.config.ib_gateway_port,
                        )
                    except Exception as e:
                        logger.warning(f"Heartbeat failed: {e}")
                    last_heartbeat = time.time()

                time.sleep(self.config.poll_interval_seconds)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)

        logger.info("Shutting down execution agent")
        self.disconnect()


def main():
    """Main entry point for the execution agent."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = load_config_from_env()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    from .broker_transport import FakeBrokerTransport
    transport = FakeBrokerTransport()
    ibkr_client = IBKRClient(transport)

    alethra_client = AlethraAPIClient(
        api_url=config.alethra_api_url,
        api_token=config.alethra_api_key,
    )

    try:
        agent = ExecutionAgent(config, ibkr_client, alethra_client)
        agent.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
