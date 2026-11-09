"""IBKR client — broker-facing interface for the execution agent.

Wraps a ``BrokerTransport`` (pluggable) to provide order submission and
fill tracking. In production, uses ``IBKRBrokerTransport`` (requires ib_insync).
In tests, uses ``FakeBrokerTransport``.
"""
from datetime import datetime
from typing import List, Optional

from .broker_transport import (
    BrokerTransport,
    BrokerOrderResult,
    BrokerFill,
    BrokerOrderStatus,
    BrokerOrderState,
)


class IBKRClient:
    """IBKR client wrapper. Delegates to a BrokerTransport for actual I/O."""

    def __init__(self, transport: BrokerTransport):
        self._transport = transport

    def connect(self) -> None:
        self._transport.connect()

    def disconnect(self) -> None:
        self._transport.disconnect()

    def is_connected(self) -> bool:
        return self._transport.is_connected()

    def submit_market_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
    ) -> BrokerOrderResult:
        return self._transport.submit_order(
            instrument_id=instrument_id,
            side=side,
            quantity=quantity,
            order_type="MARKET",
        )

    def submit_limit_order(
        self,
        instrument_id: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> BrokerOrderResult:
        return self._transport.submit_order(
            instrument_id=instrument_id,
            side=side,
            quantity=quantity,
            order_type="LIMIT",
            limit_price=limit_price,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        return self._transport.cancel_order(broker_order_id)

    def get_order_status(self, broker_order_id: str) -> str:
        return self._transport.get_order_status(broker_order_id).value

    def get_fills(self, broker_order_id: str) -> List[BrokerFill]:
        return self._transport.get_fills(broker_order_id)

    def get_recent_orders(self, since: datetime) -> List[BrokerOrderState]:
        return self._transport.get_recent_orders(since)
