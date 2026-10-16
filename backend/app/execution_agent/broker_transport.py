"""Broker transport abstraction for the execution agent.

Provides a pluggable transport layer so the agent control flow can be tested
without a live IBKR connection.

- ``BrokerTransport`` — abstract interface
- ``FakeBrokerTransport`` — deterministic in-memory transport for tests
- ``IBKRBrokerTransport`` — real IBKR transport (requires ib_insync)
"""
import random
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from app.lib.timeutil import utcnow


class BrokerOrderStatus(str, Enum):
    PENDING = "PendingSubmit"
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    REJECTED = "Rejected"
    PARTIALLY_FILLED = "PartiallyFilled"


@dataclass
class BrokerOrderResult:
    success: bool
    broker_order_id: Optional[str] = None
    error_message: Optional[str] = None
    submitted_at: Optional[datetime] = None


@dataclass
class BrokerFill:
    broker_order_id: str
    quantity: int
    price: float
    fill_time: datetime
    commission: float = 0.0


@dataclass
class BrokerOrderState:
    order_id: str
    instrument_id: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[float]
    status: BrokerOrderStatus
    filled_quantity: int = 0
    fills: List[BrokerFill] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)


class BrokerTransport(ABC):
    """Abstract broker transport interface."""

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def submit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        ...

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> BrokerOrderStatus:
        ...

    @abstractmethod
    def get_fills(self, broker_order_id: str) -> List[BrokerFill]:
        ...

    @abstractmethod
    def get_recent_orders(self, since: datetime) -> List[BrokerOrderState]:
        """Return all orders (any status) created at or after `since`.

        Used for lost-acknowledgement recovery: after a crash between order
        submission and platform reporting, the agent matches recent broker
        orders against authorized intents instead of resubmitting.
        """
        ...


class FakeBrokerTransport(BrokerTransport):
    """Deterministic in-memory broker transport for testing.

    Configurable behaviors:
    - fill_mode: "immediate" | "delayed" | "partial" | "reject"
    - fill_price_offset: deviation from a reference price (default 0)
    - partial_fill_pct: fraction filled per poll in "partial" mode
    - reject_after_submit: if True, order is submitted then rejected on next status check
    """

    def __init__(
        self,
        fill_mode: str = "immediate",
        fill_price: float = 100.0,
        partial_fill_pct: float = 0.5,
        reject_message: str = "Order rejected by exchange",
        seed: Optional[int] = None,
    ):
        self.fill_mode = fill_mode
        self.fill_price = fill_price
        self.partial_fill_pct = partial_fill_pct
        self.reject_message = reject_message
        self._connected = False
        self._orders: Dict[str, BrokerOrderState] = {}
        self._order_counter = 0
        self._rng = random.Random(seed)

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def submit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        if not self._connected:
            return BrokerOrderResult(
                success=False,
                error_message="Not connected to broker",
            )

        self._order_counter += 1
        order_id = f"FAKE-{self._order_counter:06d}"
        now = utcnow()

        if self.fill_mode == "reject":
            order = BrokerOrderState(
                order_id=order_id,
                instrument_id=instrument_id,
                side=side,
                quantity=quantity,
                order_type=order_type,
                limit_price=limit_price,
                status=BrokerOrderStatus.REJECTED,
            )
            self._orders[order_id] = order
            return BrokerOrderResult(
                success=False,
                broker_order_id=order_id,
                error_message=self.reject_message,
                submitted_at=now,
            )

        order = BrokerOrderState(
            order_id=order_id,
            instrument_id=instrument_id,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            status=BrokerOrderStatus.SUBMITTED,
        )
        self._orders[order_id] = order

        if self.fill_mode == "immediate":
            fill = BrokerFill(
                broker_order_id=order_id,
                quantity=quantity,
                price=self.fill_price,
                fill_time=now,
                commission=self._compute_commission(quantity, self.fill_price),
            )
            order.fills.append(fill)
            order.filled_quantity = quantity
            order.status = BrokerOrderStatus.FILLED

        return BrokerOrderResult(
            success=True,
            broker_order_id=order_id,
            submitted_at=now,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        order = self._orders.get(broker_order_id)
        if not order:
            return False
        if order.status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
            return False
        order.status = BrokerOrderStatus.CANCELLED
        return True

    def get_order_status(self, broker_order_id: str) -> BrokerOrderStatus:
        order = self._orders.get(broker_order_id)
        if not order:
            raise ValueError(f"Unknown order: {broker_order_id}")

        if self.fill_mode == "delayed" and order.status == BrokerOrderStatus.SUBMITTED:
            if self._rng.random() < 0.5:
                fill = BrokerFill(
                    broker_order_id=broker_order_id,
                    quantity=order.quantity,
                    price=self.fill_price,
                    fill_time=utcnow(),
                    commission=self._compute_commission(order.quantity, self.fill_price),
                )
                order.fills.append(fill)
                order.filled_quantity = order.quantity
                order.status = BrokerOrderStatus.FILLED

        if self.fill_mode == "partial" and order.status in (
            BrokerOrderStatus.SUBMITTED, BrokerOrderStatus.PARTIALLY_FILLED
        ):
            remaining = order.quantity - order.filled_quantity
            if remaining > 0:
                partial_qty = max(1, int(remaining * self.partial_fill_pct))
                partial_qty = min(partial_qty, remaining)
                fill = BrokerFill(
                    broker_order_id=broker_order_id,
                    quantity=partial_qty,
                    price=self.fill_price,
                    fill_time=utcnow(),
                    commission=self._compute_commission(partial_qty, self.fill_price),
                )
                order.fills.append(fill)
                order.filled_quantity += partial_qty
                if order.filled_quantity >= order.quantity:
                    order.status = BrokerOrderStatus.FILLED
                else:
                    order.status = BrokerOrderStatus.PARTIALLY_FILLED

        return order.status

    def get_fills(self, broker_order_id: str) -> List[BrokerFill]:
        order = self._orders.get(broker_order_id)
        if not order:
            return []
        return list(order.fills)

    def get_recent_orders(self, since: datetime) -> List[BrokerOrderState]:
        return [
            order for order in self._orders.values()
            if order.created_at >= since
        ]

    def _compute_commission(self, quantity: int, price: float) -> float:
        return max(1.0, quantity * price * 0.0001)

    def get_order(self, broker_order_id: str) -> Optional[BrokerOrderState]:
        return self._orders.get(broker_order_id)
