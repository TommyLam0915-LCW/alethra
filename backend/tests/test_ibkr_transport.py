"""Unit tests for IBKRBrokerTransport adapter logic (offline).

The real transport's gateway connection requires TWS/IB Gateway (verified
externally). These tests inject a fake ``ib`` object with ib_insync-shaped
responses to verify the platform's own adapter code: contract mapping,
order construction, status mapping, fill extraction, cancellation guards,
and recent-order recovery — no network.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.execution_agent.broker_transport import (
    BrokerOrderStatus,
    IBKRBrokerTransport,
)
from app.lib.timeutil import utcnow


class FakeIb:
    """Minimal ib_insync.IB stand-in."""

    def __init__(self, connected=True):
        self._connected = connected
        self.placed = []
        self.cancelled = []
        self.trades_list = []

    def connect(self, host, port, clientId=None, timeout=None):
        self.last_connect = (host, port, clientId, timeout)
        return self._connected

    def disconnect(self):
        self._connected = False

    def isConnected(self):
        return self._connected

    def placeOrder(self, contract, order):
        order.orderId = len(self.placed) + 1
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(
                status="Submitted", filled=0, totalQuantity=order.totalQuantity,
            ),
            fills=[],
            log=[SimpleNamespace(time=utcnow())],
        )
        self.placed.append((contract, order, trade))
        self.trades_list.append(trade)
        return trade

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def trades(self):
        return list(self.trades_list)


def _fill(shares, price, when, commission=1.0):
    return SimpleNamespace(
        execution=SimpleNamespace(shares=shares, avgPrice=price, time=when),
        commissionReport=SimpleNamespace(commission=commission),
    )


@pytest.fixture()
def ib():
    return FakeIb()


@pytest.fixture()
def transport(ib):
    t = IBKRBrokerTransport(host="127.0.0.1", port=7497, client_id=7, ib=ib)
    return t


class TestConnection:
    def test_not_connected_without_ib(self):
        t = IBKRBrokerTransport()
        assert t.is_connected() is False

    def test_connect_success(self, transport, ib):
        transport.connect()
        assert ib.last_connect == ("127.0.0.1", 7497, 7, 10.0)
        assert transport.is_connected() is True

    def test_connect_failure_raises(self, transport, ib):
        ib._connected = False
        with pytest.raises(ConnectionError):
            transport.connect()

    def test_disconnect(self, transport, ib):
        transport.connect()
        transport.disconnect()
        assert ib._connected is False
        assert transport.is_connected() is False


class TestSubmitOrder:
    def test_requires_connection(self, transport, ib):
        ib._connected = False
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success is False
        assert "Not connected" in result.error_message

    def test_market_order_maps_to_smart_usd(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success is True
        assert result.broker_order_id == "1"

        contract, order, _trade = ib.placed[0]
        assert contract.symbol == "AAPL"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"
        assert order.action == "BUY"
        assert order.totalQuantity == 10
        assert order.orderType == "MKT"

    def test_bare_symbol(self, transport, ib):
        transport.connect()
        assert transport.submit_order("MSFT", "SELL", 5).success is True
        contract = ib.placed[0][0]
        assert contract.symbol == "MSFT"

    def test_prefixed_hk_symbol_maps_to_hkex(self, transport, ib):
        transport.connect()
        assert transport.submit_order("HK:0700", "BUY", 100).success is True
        contract = ib.placed[0][0]
        assert contract.symbol == "700"
        assert contract.exchange == "HKEX"
        assert contract.currency == "HKD"

    def test_dot_hk_symbol_maps_to_hkex(self, transport, ib):
        transport.connect()
        assert transport.submit_order("0700.HK", "BUY", 100).success is True
        contract = ib.placed[0][0]
        assert contract.symbol == "700"
        assert contract.exchange == "HKEX"
        assert contract.currency == "HKD"

    def test_limit_order(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10, "LIMIT", 150.0)
        assert result.success is True
        order = ib.placed[0][1]
        assert order.orderType == "LMT"
        assert order.lmtPrice == 150.0

    def test_limit_without_price_fails(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10, "LIMIT", None)
        assert result.success is False
        assert "limit_price" in result.error_message

    def test_unsupported_order_type_fails(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10, "STOP", 99.0)
        assert result.success is False
        assert "Unsupported order type" in result.error_message

    def test_broker_exception_returns_failure(self, transport, ib):
        transport.connect()
        ib.placeOrder = lambda contract, order: (_ for _ in ()).throw(RuntimeError("gateway busy"))
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert result.success is False
        assert "gateway busy" in result.error_message


class TestStatusMapping:
    def test_all_status_strings_map(self, ib):
        transport = IBKRBrokerTransport(ib=ib)
        cases = {
            "PendingSubmit": BrokerOrderStatus.PENDING,
            "PendingCancel": BrokerOrderStatus.PENDING,
            "PreSubmitted": BrokerOrderStatus.SUBMITTED,
            "Submitted": BrokerOrderStatus.SUBMITTED,
            "Filled": BrokerOrderStatus.FILLED,
            "Cancelled": BrokerOrderStatus.CANCELLED,
            "ApiCancelled": BrokerOrderStatus.CANCELLED,
            "Inactive": BrokerOrderStatus.REJECTED,
        }
        for status_str, expected in cases.items():
            got = transport._map_status(SimpleNamespace(status=status_str, filled=0, totalQuantity=10))
            assert got == expected, status_str

    def test_partial_fill_detected(self, ib):
        transport = IBKRBrokerTransport(ib=ib)
        status = SimpleNamespace(status="Submitted", filled=4, totalQuantity=10)
        assert transport._map_status(status) == BrokerOrderStatus.PARTIALLY_FILLED

    def test_unknown_status_defaults_to_submitted(self, ib):
        transport = IBKRBrokerTransport(ib=ib)
        got = transport._map_status(SimpleNamespace(status="Weird", filled=0, totalQuantity=10))
        assert got == BrokerOrderStatus.SUBMITTED

    def test_get_order_status(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert transport.get_order_status(result.broker_order_id) == BrokerOrderStatus.SUBMITTED

    def test_get_order_status_unknown_order_raises(self, transport, ib):
        with pytest.raises(ValueError):
            transport.get_order_status("nope")


class TestFills:
    def test_fill_extraction(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        trade = ib.placed[0][2]
        when = utcnow()
        trade.fills.append(_fill(10, 151.5, when, commission=2.5))

        fills = transport.get_fills(result.broker_order_id)
        assert len(fills) == 1
        assert fills[0].quantity == 10
        assert fills[0].price == 151.5
        assert fills[0].commission == 2.5
        assert fills[0].fill_time == when

    def test_fills_for_unknown_order_empty(self, transport, ib):
        assert transport.get_fills("nope") == []


class TestCancelOrder:
    def test_cancel_open_order(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        assert transport.cancel_order(result.broker_order_id) is True
        assert len(ib.cancelled) == 1

    def test_cancel_filled_order_returns_false(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        ib.placed[0][2].orderStatus.status = "Filled"
        assert transport.cancel_order(result.broker_order_id) is False
        assert ib.cancelled == []

    def test_cancel_unknown_order_returns_false(self, transport, ib):
        assert transport.cancel_order("nope") is False


class TestRecentOrders:
    def test_recent_orders_from_trades(self, transport, ib):
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)
        transport.submit_order("HK:0700", "SELL", 100, "LIMIT", 350.0)

        orders = transport.get_recent_orders(utcnow() - timedelta(minutes=1))
        assert len(orders) == 2
        by_symbol = {o.instrument_id: o for o in orders}
        assert set(by_symbol) == {"AAPL", "700"}
        aapl = by_symbol["AAPL"]
        assert aapl.side == "BUY"
        assert aapl.quantity == 10
        assert aapl.order_type == "MKT" or aapl.order_type == "MARKET"
        hkc = by_symbol["700"]
        assert hkc.order_type == "LMT"
        assert hkc.limit_price == 350.0

    def test_old_orders_excluded(self, transport, ib):
        transport.connect()
        transport.submit_order("US:AAPL", "BUY", 10)
        # Backdate the only log entry.
        ib.trades_list[0].log[0].time = utcnow() - timedelta(hours=2)
        orders = transport.get_recent_orders(utcnow() - timedelta(minutes=30))
        assert orders == []

    def test_recent_orders_includes_terminal_orders(self, transport, ib):
        transport.connect()
        result = transport.submit_order("US:AAPL", "BUY", 10)
        ib.placed[0][2].orderStatus.status = "Filled"
        orders = transport.get_recent_orders(utcnow() - timedelta(minutes=1))
        assert len(orders) == 1
        assert orders[0].status == BrokerOrderStatus.FILLED
