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


# ib_insync OrderStatus strings → BrokerOrderStatus
_IBKR_STATUS_MAP = {
    "PendingSubmit": BrokerOrderStatus.PENDING,
    "PendingCancel": BrokerOrderStatus.PENDING,
    "PreSubmitted": BrokerOrderStatus.SUBMITTED,
    "Submitted": BrokerOrderStatus.SUBMITTED,
    "Filled": BrokerOrderStatus.FILLED,
    "Cancelled": BrokerOrderStatus.CANCELLED,
    "ApiCancelled": BrokerOrderStatus.CANCELLED,
    "Inactive": BrokerOrderStatus.REJECTED,
}


def normalize_instrument_id(instrument_id: str) -> str:
    """Reduce accepted instrument formats to the broker-side symbol.

    ``US:AAPL``/``AAPL`` → ``AAPL``; ``0700.HK``/``HK:0700`` → ``700``.
    IBKR contract symbols lose the platform prefix and HKEX leading zeros,
    so recovered broker orders must be compared against intents through
    this same mapping (e.g. in lost-acknowledgement matching).
    """
    symbol = (instrument_id or "").strip().upper()
    if symbol.endswith(".HK"):
        return symbol[: -len(".HK")].lstrip("0") or "0"
    if ":" in symbol:
        prefix, _, sym = symbol.partition(":")
        if prefix == "HK":
            return sym.lstrip("0") or "0"
        return sym
    return symbol


class IBKRBrokerTransport(BrokerTransport):
    """Real IBKR transport via ib_insync (IB Gateway / TWS).

    Requires a reachable IB Gateway or TWS with the API enabled. ``ib_insync``
    is imported lazily so environments without it can still import this module
    (e.g. tests that use FakeBrokerTransport).

    An already-connected ``ib`` instance can be injected for offline unit
    testing of the adapter logic (contract building, status mapping, fill
    extraction) — the socket-level connection to a real gateway is verified
    externally only.

    Contract mapping: ``US:<SYM>`` / bare ``SYM`` → SMART(USD);
    ``<CODE>.HK`` → HKEX(HKD). Orders are submitted in the instrument's
    currency as inferred here.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        ib=None,
        connect_timeout: float = 10.0,
    ):
        self._host = host
        self._port = port
        self._client_id = client_id
        self._connect_timeout = connect_timeout
        # Injectable ib_insync.IB instance (tests); created lazily otherwise.
        self._ib = ib
        self._trades: Dict[str, object] = {}

    # -- connection ---------------------------------------------------------

    def _get_ib(self):
        if self._ib is None:
            from ib_insync import IB
            self._ib = IB()
        return self._ib

    def connect(self) -> None:
        ib = self._get_ib()
        ib.connect(self._host, self._port, clientId=self._client_id, timeout=self._connect_timeout)
        if not ib.isConnected():
            raise ConnectionError(
                f"Could not connect to IB Gateway at {self._host}:{self._port}"
            )

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    # -- order handling -----------------------------------------------------

    def _make_contract(self, instrument_id: str):
        from ib_insync import Stock

        symbol = instrument_id.strip().upper()
        if symbol.endswith(".HK"):
            code = symbol[: -len(".HK")].lstrip("0") or "0"
            return Stock(code, "HKEX", "HKD")
        if ":" in symbol:
            # e.g. "US:AAPL" — platform's prefixed form
            prefix, _, sym = symbol.partition(":")
            if prefix == "HK":
                code = sym.lstrip("0") or "0"
                return Stock(code, "HKEX", "HKD")
            symbol = sym
        return Stock(symbol, "SMART", "USD")

    @staticmethod
    def _map_status(order_status) -> BrokerOrderStatus:
        status_str = getattr(order_status, "status", "") or ""
        mapped = _IBKR_STATUS_MAP.get(status_str)
        if mapped is not None:
            if mapped is BrokerOrderStatus.SUBMITTED:
                filled = getattr(order_status, "filled", 0) or 0
                total = getattr(order_status, "totalQuantity", 0) or 0
                if total and 0 < filled < total:
                    return BrokerOrderStatus.PARTIALLY_FILLED
            return mapped
        return BrokerOrderStatus.SUBMITTED

    def submit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        if not self.is_connected():
            return BrokerOrderResult(
                success=False,
                error_message="Not connected to IB Gateway",
            )

        try:
            from ib_insync import MarketOrder, LimitOrder

            ib = self._get_ib()
            contract = self._make_contract(instrument_id)
            if order_type == "LIMIT":
                if limit_price is None:
                    return BrokerOrderResult(
                        success=False,
                        error_message="LIMIT order requires limit_price",
                    )
            elif order_type != "MARKET":
                return BrokerOrderResult(
                    success=False,
                    error_message=f"Unsupported order type: {order_type}",
                )

            ib_order = (
                LimitOrder(side.upper(), quantity, limit_price)
                if order_type == "LIMIT"
                else MarketOrder(side.upper(), quantity)
            )
            trade = ib.placeOrder(contract, ib_order)
            broker_order_id = str(ib_order.orderId)
            self._trades[broker_order_id] = trade
            return BrokerOrderResult(
                success=True,
                broker_order_id=broker_order_id,
                submitted_at=utcnow(),
            )
        except Exception as e:
            return BrokerOrderResult(success=False, error_message=str(e))

    def _get_trade(self, broker_order_id: str):
        trade = self._trades.get(broker_order_id)
        if trade is not None:
            return trade
        ib = self._get_ib()
        for t in ib.trades():
            if str(t.order.orderId) == str(broker_order_id):
                self._trades[broker_order_id] = t
                return t
        return None

    def cancel_order(self, broker_order_id: str) -> bool:
        trade = self._get_trade(broker_order_id)
        if trade is None:
            return False
        status = self._map_status(trade.orderStatus)
        if status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
            return False
        self._get_ib().cancelOrder(trade.order)
        return True

    def get_order_status(self, broker_order_id: str) -> BrokerOrderStatus:
        trade = self._get_trade(broker_order_id)
        if trade is None:
            raise ValueError(f"Unknown order: {broker_order_id}")
        return self._map_status(trade.orderStatus)

    def get_fills(self, broker_order_id: str) -> List[BrokerFill]:
        trade = self._get_trade(broker_order_id)
        if trade is None:
            return []
        fills = []
        for fill in trade.fills:
            exec_details = getattr(fill, "execution", None)
            if exec_details is None:
                continue
            commission = 0.0
            report = getattr(fill, "commissionReport", None)
            if report is not None and getattr(report, "commission", None) is not None:
                commission = float(report.commission)
            fills.append(BrokerFill(
                broker_order_id=broker_order_id,
                quantity=int(exec_details.shares),
                price=float(exec_details.avgPrice),
                fill_time=exec_details.time,
                commission=commission,
            ))
        return fills

    def get_recent_orders(self, since: datetime) -> List[BrokerOrderState]:
        ib = self._get_ib()
        states = []
        for trade in ib.trades():
            created_at = None
            log = getattr(trade, "log", None) or []
            for entry in log:
                entry_time = getattr(entry, "time", None)
                if entry_time is not None:
                    created_at = entry_time
                    break
            if created_at is None:
                created_at = utcnow()
            if created_at < since:
                continue
            order_status = trade.orderStatus
            fills = []
            for fill in trade.fills:
                exec_details = getattr(fill, "execution", None)
                if exec_details is None:
                    continue
                fills.append(BrokerFill(
                    broker_order_id=str(trade.order.orderId),
                    quantity=int(exec_details.shares),
                    price=float(exec_details.avgPrice),
                    fill_time=exec_details.time,
                ))
            states.append(BrokerOrderState(
                order_id=str(trade.order.orderId),
                instrument_id=trade.contract.symbol,
                side=(trade.order.action or "").upper(),
                quantity=int(order_status.totalQuantity or trade.order.totalQuantity or 0),
                order_type=(trade.order.orderType or "MARKET").upper(),
                limit_price=getattr(trade.order, "lmtPrice", None),
                status=self._map_status(order_status),
                filled_quantity=int(order_status.filled or 0),
                fills=fills,
                created_at=created_at,
            ))
        return states
