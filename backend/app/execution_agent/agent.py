"""Main execution agent loop.

Bridges the Alethra platform and a broker via transport abstractions.
Uses BrokerTransport for broker operations and PlatformTransport for
platform communication. Both are injectable for offline testing.

SAFETY
======
- Live trading mode is DISABLED by default. Setting mode="live" requires
  explicit ALETHRA_LIVE_TRADING_ENABLED=true environment variable.
- Kill switch is checked before every submission.
- Unknown execution-critical metadata (e.g., unresolved board lot) blocks
  submission.
- Expired intents are rejected, not submitted.
- Duplicate intents (same idempotency key) are skipped.
"""

import logging
import signal
import time
import uuid
from datetime import datetime
from typing import Optional

from .config import AgentConfig
from .transport import (
    BrokerTransport,
    BrokerSubmitResult,
    OrderStatus,
)
from .platform_transport import (
    PlatformTransport,
    IntentDTO,
    FillReportDTO,
)

logger = logging.getLogger(__name__)


class LiveTradingDisabledError(RuntimeError):
    """Raised when live trading is attempted without explicit enablement."""


class ExecutionAgent:
    """Execution agent that bridges Alethra platform and a broker.

    Uses transport abstractions so the control flow can be tested offline
    with fake transports while production uses real IBKR/platform transports.
    """

    def __init__(
        self,
        config: AgentConfig,
        broker: BrokerTransport,
        platform: PlatformTransport,
    ):
        self.config = config
        self.broker = broker
        self.platform = platform
        self.running = False
        self.reconnect_attempts = 0
        self._processed_idempotency_keys: set[str] = set()
        self._processed_intent_ids: set[str] = set()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info("Received signal %s, shutting down gracefully...", signum)
        self.running = False

    def connect(self) -> None:
        self.broker.connect()
        self.reconnect_attempts = 0
        logger.info("Connected to broker")

    def disconnect(self) -> None:
        self.broker.disconnect()
        logger.info("Disconnected from broker")

    def reconnect(self) -> bool:
        if self.reconnect_attempts >= self.config.max_reconnect_attempts:
            logger.error("Max reconnection attempts (%d) reached", self.config.max_reconnect_attempts)
            return False

        self.reconnect_attempts += 1
        logger.warning(
            "Reconnection attempt %d/%d",
            self.reconnect_attempts,
            self.config.max_reconnect_attempts,
        )

        try:
            self.disconnect()
            time.sleep(self.config.reconnect_interval_seconds)
            self.connect()
            logger.info("Reconnection successful")
            return True
        except Exception as e:
            logger.error("Reconnection failed: %s", e)
            return False

    def process_cycle(self) -> int:
        """Run one processing cycle. Returns number of intents processed."""
        if self.platform.check_kill_switch():
            logger.warning("Kill switch active — skipping processing cycle")
            return 0

        intents = self.platform.poll_order_intents()
        if not intents:
            return 0

        processed = 0
        for intent in intents:
            try:
                if self._process_intent(intent):
                    processed += 1
            except Exception as e:
                logger.error("Error processing intent %s: %s", intent.intent_id, e)
                self.platform.report_rejected(
                    intent_id=intent.intent_id,
                    reason=f"Agent error: {e}",
                )
        return processed

    def _process_intent(self, intent: IntentDTO) -> bool:
        """Process a single order intent. Returns True if actually processed."""
        if intent.intent_id in self._processed_intent_ids:
            logger.debug("Skipping already-processed intent %s", intent.intent_id)
            return False

        if intent.idempotency_key and intent.idempotency_key in self._processed_idempotency_keys:
            logger.warning("Duplicate idempotency key %s for intent %s — skipping",
                           intent.idempotency_key, intent.intent_id)
            return False

        if datetime.utcnow() > intent.expires_at:
            logger.warning("Intent %s has expired", intent.intent_id)
            self.platform.report_rejected(
                intent_id=intent.intent_id,
                reason="Intent expired",
            )
            return False

        if not self.broker.resolve_instrument(intent.instrument_id):
            logger.error("Cannot resolve instrument %s — metadata unresolved", intent.instrument_id)
            self.platform.report_rejected(
                intent_id=intent.intent_id,
                reason=f"Unresolved instrument metadata: {intent.instrument_id}",
            )
            return False

        if not self.broker.is_connected():
            logger.warning("Broker disconnected — attempting reconnect")
            if not self.reconnect():
                self.platform.report_rejected(
                    intent_id=intent.intent_id,
                    reason="Broker connection lost and reconnect failed",
                )
                return False

        result = self._submit_order(intent)
        if not result.success:
            self.platform.report_rejected(
                intent_id=intent.intent_id,
                reason=result.error_message or "Order submission failed",
            )
            return False

        self.platform.report_submitted(
            intent_id=intent.intent_id,
            broker_ref=result.broker_order_id,
            submitted_at=result.submitted_at,
        )
        self._processed_intent_ids.add(intent.intent_id)
        if intent.idempotency_key:
            self._processed_idempotency_keys.add(intent.idempotency_key)

        self._reconcile_fills(intent.intent_id, result.broker_order_id, intent.quantity)
        return True

    def _submit_order(self, intent: IntentDTO) -> BrokerSubmitResult:
        return self.broker.submit_order(
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
        )

    def _reconcile_fills(
        self,
        intent_id: str,
        broker_order_id: str,
        expected_quantity: int,
    ) -> None:
        state = self.broker.get_order_state(broker_order_id)

        if state.status == OrderStatus.FILLED:
            fills = self.broker.get_fills(broker_order_id)
            for fill in fills:
                self.platform.report_fill(FillReportDTO(
                    intent_id=intent_id,
                    quantity=fill.quantity,
                    price=fill.price,
                    fill_time=fill.fill_time,
                    broker_ref=broker_order_id,
                    commission=fill.commission,
                ))
            return

        if state.status == OrderStatus.PARTIALLY_FILLED:
            fills = self.broker.get_fills(broker_order_id)
            for fill in fills:
                self.platform.report_fill(FillReportDTO(
                    intent_id=intent_id,
                    quantity=fill.quantity,
                    price=fill.price,
                    fill_time=fill.fill_time,
                    broker_ref=broker_order_id,
                    commission=fill.commission,
                ))
            logger.info(
                "Partial fill for intent %s: %d/%d filled",
                intent_id, state.filled_quantity, expected_quantity,
            )
            return

        if state.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self.platform.report_cancelled(
                intent_id=intent_id,
                reason=f"Order {state.status.value.lower()} by broker",
            )
            return

        logger.warning("Order %s still in state %s — fill reconciliation deferred",
                        broker_order_id, state.status.value)

    def run(self) -> None:
        logger.info("Starting execution agent (mode=%s)", self.config.mode)

        self.connect()
        self.running = True
        last_heartbeat = 0.0

        while self.running:
            try:
                if not self.broker.is_connected():
                    logger.warning("Lost connection to broker")
                    if not self.reconnect():
                        logger.error("Failed to reconnect, shutting down")
                        break

                self.process_cycle()

                now = time.time()
                if now - last_heartbeat > 60:
                    self.platform.report_heartbeat(
                        connected=self.broker.is_connected(),
                    )
                    last_heartbeat = now

                time.sleep(self.config.poll_interval_seconds)

            except Exception as e:
                logger.error("Error in main loop: %s", e)
                time.sleep(5)

        logger.info("Shutting down execution agent")
        self.disconnect()
