"""Broker transport abstraction for the execution agent.

Defines the interface between the agent's control flow and the actual broker.
This allows offline testing with a fake transport while the real IBKR
transport remains BLOCKED on external credentials.
"""

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol


class OrderStatus(str, enum.Enum):
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    PARTIALLY_FILLED = "PartiallyFilled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PENDING = "Pending"


@dataclass(frozen=True)
class BrokerSubmitResult:
    success: bool
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: Optional[datetime] = None


@dataclass(frozen=True)
class BrokerFill:
    broker_order_id: str
    quantity: int
    price: float
    fill_time: datetime
    commission: float = 0.0


@dataclass
class BrokerOrderState:
    status: OrderStatus
    filled_quantity: int = 0
    remaining_quantity: int = 0
    fills: list[BrokerFill] = field(default_factory=list)


class BrokerTransport(Protocol):
    """Interface for broker operations.

    The agent uses this protocol to interact with the broker. Tests use
    FakeBrokerTransport; production uses IBKRTransport (BLOCKED).
    """

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def resolve_instrument(self, instrument_id: str) -> bool:
        """Check if instrument can be resolved. Returns False if unresolved."""
        ...

    def submit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float] = None,
    ) -> BrokerSubmitResult: ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def get_order_state(self, broker_order_id: str) -> BrokerOrderState: ...

    def get_fills(self, broker_order_id: str) -> list[BrokerFill]: ...


class RejectionMode(str, enum.Enum):
    IMMEDIATE = "immediate"
    AFTER_SUBMIT = "after_submit"


@dataclass
class FakeBrokerConfig:
    """Configuration for FakeBrokerTransport behavior."""
    reject_instrument_ids: set[str] = field(default_factory=set)
    rejection_mode: Optional[RejectionMode] = None
    rejection_reason: str = "Order rejected by fake broker"
    fill_mode: str = "immediate"  # "immediate", "partial", "delayed", "never"
    partial_fill_quantity: Optional[int] = None
    commission_per_fill: float = 1.0
    simulate_disconnect_after_n_orders: Optional[int] = None
    fill_price: float = 100.0


class FakeBrokerTransport:
    """Fake broker transport for offline testing.

    Configurable behavior:
    - Immediate fill, partial fill, no fill
    - Rejection (before or after submit)
    - Disconnect simulation
    - Instrument resolution failure
    """

    def __init__(self, config: Optional[FakeBrokerConfig] = None):
        self._config = config or FakeBrokerConfig()
        self._connected = False
        self._orders: dict[str, BrokerOrderState] = {}
        self._order_counter = 0
        self._orders_submitted = 0
        self._cancelled_orders: set[str] = set()

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def resolve_instrument(self, instrument_id: str) -> bool:
        if instrument_id in self._config.reject_instrument_ids:
            return False
        return True

    def submit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        order_type: str,
        limit_price: Optional[float] = None,
    ) -> BrokerSubmitResult:
        if not self._connected:
            return BrokerSubmitResult(
                success=False,
                error_message="Not connected to broker",
            )

        if not self.resolve_instrument(instrument_id):
            return BrokerSubmitResult(
                success=False,
                error_message=f"Cannot resolve instrument: {instrument_id}",
            )

        if self._config.rejection_mode == RejectionMode.IMMEDIATE:
            return BrokerSubmitResult(
                success=False,
                error_message=self._config.rejection_reason,
            )

        self._order_counter += 1
        order_id = f"FAKE-{self._order_counter:06d}"
        now = datetime.utcnow()

        self._orders[order_id] = BrokerOrderState(
            status=OrderStatus.SUBMITTED,
            filled_quantity=0,
            remaining_quantity=quantity,
        )

        self._orders_submitted += 1

        if (
            self._config.simulate_disconnect_after_n_orders is not None
            and self._orders_submitted >= self._config.simulate_disconnect_after_n_orders
        ):
            self._connected = False

        if self._config.fill_mode == "immediate":
            fill = BrokerFill(
                broker_order_id=order_id,
                quantity=quantity,
                price=self._config.fill_price,
                fill_time=now,
                commission=self._config.commission_per_fill,
            )
            self._orders[order_id] = BrokerOrderState(
                status=OrderStatus.FILLED,
                filled_quantity=quantity,
                remaining_quantity=0,
                fills=[fill],
            )
        elif self._config.fill_mode == "partial":
            partial_qty = self._config.partial_fill_quantity or max(1, quantity // 2)
            fill = BrokerFill(
                broker_order_id=order_id,
                quantity=partial_qty,
                price=self._config.fill_price,
                fill_time=now,
                commission=self._config.commission_per_fill,
            )
            self._orders[order_id] = BrokerOrderState(
                status=OrderStatus.PARTIALLY_FILLED,
                filled_quantity=partial_qty,
                remaining_quantity=quantity - partial_qty,
                fills=[fill],
            )
        elif self._config.fill_mode == "never":
            pass  # stays SUBMITTED

        return BrokerSubmitResult(
            success=True,
            broker_order_id=order_id,
            submitted_at=now,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        if broker_order_id in self._orders:
            state = self._orders[broker_order_id]
            if state.status in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
                state.status = OrderStatus.CANCELLED
                self._cancelled_orders.add(broker_order_id)
                return True
        return False

    def get_order_state(self, broker_order_id: str) -> BrokerOrderState:
        if broker_order_id in self._orders:
            return self._orders[broker_order_id]
        return BrokerOrderState(
            status=OrderStatus.REJECTED,
            filled_quantity=0,
            remaining_quantity=0,
        )

    def get_fills(self, broker_order_id: str) -> list[BrokerFill]:
        if broker_order_id in self._orders:
            return list(self._orders[broker_order_id].fills)
        return []

    def simulate_fill_completion(self, broker_order_id: str) -> None:
        """Test helper: complete a partial fill."""
        if broker_order_id in self._orders:
            state = self._orders[broker_order_id]
            if state.status == OrderStatus.PARTIALLY_FILLED:
                remaining = state.remaining_quantity
                fill = BrokerFill(
                    broker_order_id=broker_order_id,
                    quantity=remaining,
                    price=self._config.fill_price,
                    fill_time=datetime.utcnow(),
                    commission=self._config.commission_per_fill,
                )
                state.fills.append(fill)
                state.filled_quantity += remaining
                state.remaining_quantity = 0
                state.status = OrderStatus.FILLED

    def simulate_reconnect(self) -> None:
        """Test helper: reconnect after disconnect."""
        self._connected = True
