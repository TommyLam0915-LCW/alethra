"""Main execution agent loop.

The agent bridges the Alethra platform and a broker (IBKR in production,
FakeBrokerTransport in tests). It:

1. Polls the platform for authorized and in-flight order intents
2. Submits orders to the broker via a pluggable transport
3. Reports submissions, fills, rejections, and cancellations back
4. Handles reconnection, partial fills, and intent expiry

Safety invariants:
- Never resubmits an order if the submission acknowledgement was lost:
  before submitting, the agent matches unclaimed recent broker orders
  against the intent and adopts an exact match instead of resubmitting
- Ambiguous broker state (multiple matching unclaimed orders) is reported
  as 'uncertain' for manual resolution — never guessed, never resubmitted
- Fill reports are idempotent end to end: the platform deduplicates by
  (broker_ref, fill_time, quantity), so re-reports after a restart
  never double-count filled quantity
- Restart reconciliation re-adopts the platform's durable broker_ref for
  submitted intents and resumes fill monitoring
- Tracks partial fills and reports each individually
- Skips expired intents
- Respects the platform execution kill switch: while active, no new
  submissions (in-flight monitoring continues); control-status fetch
  failures fail closed
"""
import logging
import os
import time
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from .config import AgentConfig, load_config_from_env
from .ibkr_client import IBKRClient
from .alethra_client import AlethraAPIClient, OrderIntentDTO, FillReport
from .broker_transport import (
    BrokerTransport,
    BrokerOrderStatus,
    BrokerOrderState,
    normalize_instrument_id,
)
from app.lib.timeutil import utcnow


logger = logging.getLogger(__name__)

# Clock-skew tolerance when matching broker orders to an intent's creation time.
_SUBMISSION_MATCH_SKEW_SECONDS = 60


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
        # Broker order ids already mapped to an intent (this process or
        # re-adopted from the platform). Excluded from lost-ack matching so a
        # second identical intent never adopts another intent's order.
        self._claimed_broker_refs: Set[str] = set()

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
        """After a restart, re-adopt the platform's durable intent→broker mapping.

        The poll returns submitted intents with their broker_ref, so the agent
        resumes fill monitoring for in-flight orders without resubmitting.
        Authorized intents are handled by _process_single_intent, which checks
        the broker for lost-acknowledgement orders before any submission.
        """
        try:
            intents = self.alethra_client.poll_order_intents()
        except Exception as e:
            logger.warning(f"Could not poll intents on startup: {e}")
            return

        for intent in intents:
            if intent.status != "submitted":
                continue
            if intent.broker_ref:
                self._submitted_intents[intent.intent_id] = intent.broker_ref
                self._claimed_broker_refs.add(intent.broker_ref)
                logger.info(
                    f"Reconciling submitted intent {intent.intent_id} "
                    f"(broker_ref={intent.broker_ref})"
                )
            else:
                logger.warning(
                    f"Intent {intent.intent_id} is submitted but has no broker_ref; "
                    f"will be reported uncertain"
                )

        logger.info(f"Reconciliation complete: {len(self._submitted_intents)} tracked intents")

    def process_order_intents(self, kill_switch_active: bool = False) -> None:
        """Poll for and process order intents awaiting submission or monitoring."""
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
                self._process_single_intent(intent, kill_switch_active=kill_switch_active)
            except Exception as e:
                logger.error(f"Error processing intent {intent.intent_id}: {e}")
                try:
                    self.alethra_client.report_order_rejected(
                        intent_id=intent.intent_id,
                        reason=f"Agent error: {e}",
                    )
                except Exception as report_err:
                    logger.error(f"Failed to report rejection: {report_err}")

    def _check_kill_switch(self) -> bool:
        """Fetch the execution kill switch state. Fails closed: if the control
        status cannot be read, treat the kill switch as active so no new orders
        are submitted while the platform is unreachable."""
        try:
            status = self.alethra_client.get_control_status()
            return bool(status.get("kill_switch_active"))
        except Exception as e:
            logger.warning(
                f"Could not fetch control status ({e}); "
                f"assuming kill switch ACTIVE (fail-closed)"
            )
            return True

    def _find_unclaimed_broker_orders(self, intent: OrderIntentDTO) -> List[BrokerOrderState]:
        """Return unclaimed recent broker orders matching the intent exactly.

        Used for lost-acknowledgement recovery. Matching is on instrument,
        side, quantity, order type, limit price, and creation time (with skew
        tolerance). Claimed orders — mapped by this process, re-adopted at
        startup, or mapped to any platform intent (all statuses) — are
        excluded. Fails closed: if platform claims cannot be read, no
        matching is attempted (caller skips submission and retries).
        """
        try:
            platform_claimed = set(self.alethra_client.get_claimed_broker_refs())
        except Exception as e:
            raise RuntimeError(f"Cannot read platform broker-ref claims: {e}") from e
        claimed = self._claimed_broker_refs | platform_claimed

        since = (
            datetime.fromisoformat(intent.created_at)
            - timedelta(seconds=_SUBMISSION_MATCH_SKEW_SECONDS)
        )
        candidates = []
        for order in self.ibkr_client.get_recent_orders(since=since):
            if order.order_id in claimed:
                continue
            if order.status == BrokerOrderStatus.REJECTED:
                # A rejected order never reached the market — it must never be
                # adopted as a lost submission.
                continue
            if normalize_instrument_id(order.instrument_id) != normalize_instrument_id(intent.symbol):
                continue
            if order.side.upper() != intent.side.upper():
                continue
            if order.quantity != intent.quantity:
                continue
            if order.order_type != intent.order_type:
                continue
            if (order.limit_price if order.limit_price is not None else None) != (
                intent.limit_price if intent.limit_price is not None else None
            ):
                continue
            candidates.append(order)
        return candidates

    def _process_single_intent(
        self, intent: OrderIntentDTO, kill_switch_active: bool = False
    ) -> None:
        """Process a single order intent with duplicate prevention."""
        logger.info(
            f"Processing intent {intent.intent_id}: "
            f"{intent.side} {intent.quantity} {intent.symbol}"
        )

        if intent.intent_id in self._completed_intents:
            logger.debug(f"Intent {intent.intent_id} already completed, skipping")
            return

        if intent.intent_id in self._submitted_intents:
            logger.info(
                f"Intent {intent.intent_id} already submitted "
                f"(broker_ref={self._submitted_intents[intent.intent_id]}), skipping"
            )
            if intent.cancel_requested:
                self._cancel_submitted_order(intent)
                return
            self._monitor_fills_for_known_order(
                intent.intent_id,
                self._submitted_intents[intent.intent_id],
            )
            return

        if intent.status == "submitted":
            # The platform durably mapped this intent to a broker order but
            # this agent process has no in-memory state for it (restart, or
            # another agent process reported it). Re-adopt the mapping.
            if intent.broker_ref:
                logger.info(
                    f"Re-adopting submitted intent {intent.intent_id} "
                    f"(broker_ref={intent.broker_ref})"
                )
                self._submitted_intents[intent.intent_id] = intent.broker_ref
                self._claimed_broker_refs.add(intent.broker_ref)
                if intent.cancel_requested:
                    self._cancel_submitted_order(intent)
                    return
                self._monitor_fills_for_known_order(intent.intent_id, intent.broker_ref)
            else:
                logger.warning(
                    f"Intent {intent.intent_id} is submitted without a broker_ref; "
                    f"marking uncertain for manual resolution"
                )
                self.alethra_client.report_order_uncertain(
                    intent_id=intent.intent_id,
                    reason="Submitted intent has no broker_ref on the platform",
                )
                self._completed_intents.add(intent.intent_id)
            return

        # From here the intent is authorized (not yet submitted).

        if utcnow() > datetime.fromisoformat(intent.expires_at):
            logger.warning(f"Intent {intent.intent_id} has expired")
            self.alethra_client.report_order_rejected(
                intent_id=intent.intent_id,
                reason="Intent expired",
            )
            return

        if kill_switch_active:
            logger.warning(
                f"Execution kill switch active; not submitting {intent.intent_id}"
            )
            return

        # Lost-acknowledgement recovery: if a matching unclaimed broker order
        # exists, the submission acknowledgement was lost and the order is
        # already live — adopt it instead of resubmitting.
        try:
            candidates = self._find_unclaimed_broker_orders(intent)
        except Exception as e:
            logger.warning(
                f"Cannot verify broker state for {intent.intent_id} ({e}); "
                f"skipping submission, will retry next poll"
            )
            return

        if len(candidates) > 1:
            logger.error(
                f"Ambiguous broker state for {intent.intent_id}: "
                f"{len(candidates)} matching unclaimed orders; "
                f"marking uncertain for manual resolution"
            )
            self.alethra_client.report_order_uncertain(
                intent_id=intent.intent_id,
                reason=(
                    f"Ambiguous broker state: {len(candidates)} matching "
                    f"unclaimed broker orders"
                ),
            )
            self._completed_intents.add(intent.intent_id)
            return

        if len(candidates) == 1:
            broker_ref = candidates[0].order_id
            logger.warning(
                f"Lost acknowledgement recovered for {intent.intent_id}: "
                f"adopting broker order {broker_ref} without resubmission"
            )
            self._submitted_intents[intent.intent_id] = broker_ref
            self._claimed_broker_refs.add(broker_ref)
            try:
                self.alethra_client.report_order_submitted(
                    intent_id=intent.intent_id,
                    broker_ref=broker_ref,
                    submitted_at=candidates[0].created_at,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to report recovered submission for {intent.intent_id}: {e}. "
                    f"Will reconcile on next poll."
                )
            self._monitor_fills_for_known_order(intent.intent_id, broker_ref)
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
        self._claimed_broker_refs.add(broker_ref)

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

    def _cancel_submitted_order(self, intent: OrderIntentDTO) -> None:
        """Cancel a submitted order because the owner requested it.

        Requests the cancellation at the broker, then continues monitoring the
        order: the monitor reports any fills that landed first and, once the
        broker confirms the cancellation, reports it to the platform. If the
        order fully filled before the cancel landed, the fills are recorded
        and the intent becomes 'filled' — never silently dropped.
        """
        broker_ref = self._submitted_intents.get(intent.intent_id) or intent.broker_ref
        if not broker_ref:
            logger.warning(
                f"Cancel requested for {intent.intent_id} but no broker_ref; "
                f"marking uncertain for manual resolution"
            )
            self.alethra_client.report_order_uncertain(
                intent_id=intent.intent_id,
                reason="Cancel requested for a submitted intent with no broker_ref",
            )
            self._completed_intents.add(intent.intent_id)
            return

        logger.info(
            f"Cancel requested for intent {intent.intent_id} "
            f"(broker_ref={broker_ref}); cancelling at broker"
        )
        try:
            accepted = self.ibkr_client.cancel_order(broker_ref)
        except Exception as e:
            # Retry on the next poll — do not report cancelled without broker
            # confirmation, and do not report uncertain (the order state is
            # known, only the cancel attempt failed).
            logger.error(f"Cancel attempt failed for {intent.intent_id}: {e}")
            return

        if not accepted:
            logger.info(
                f"Cancel for {intent.intent_id} ({broker_ref}) not accepted — "
                f"order likely already filled or cancelled; continuing monitoring"
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
                    retries = 0
                    while retries < 3:
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
                            break
                        except Exception as e:
                            retries += 1
                            if retries >= 3:
                                logger.error(f"Failed to report fill after 3 attempts: {e}")
                                self._reported_fills.add(fill_key)
                            else:
                                time.sleep(1)

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

                kill_switch_active = self._check_kill_switch()
                self.process_order_intents(kill_switch_active=kill_switch_active)

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

    transport_name = os.getenv("ALETHRA_BROKER_TRANSPORT", "ibkr").strip().lower()
    if transport_name == "fake":
        # Explicit override for local development/tests — never used by default.
        logger.warning("Using FakeBrokerTransport (ALETHRA_BROKER_TRANSPORT=fake)")
        from .broker_transport import FakeBrokerTransport
        transport = FakeBrokerTransport()
    elif transport_name == "ibkr":
        from .broker_transport import IBKRBrokerTransport
        transport = IBKRBrokerTransport(
            host=config.ib_gateway_host,
            port=config.ib_gateway_port,
            client_id=config.ib_client_id,
        )
    else:
        logger.error(f"Unknown ALETHRA_BROKER_TRANSPORT: {transport_name!r}")
        sys.exit(1)
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
