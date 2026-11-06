"""Ledger reconciliation tests for paper trading.

Verifies that:
1. Entry and exit orders/fills are both created
2. Fills → positions → cash → daily report → summary reconcile
3. No orphaned orders or fills
4. Exposure limits are enforced
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal
from app.services.paper_trading import PaperTradingEngine


def create_synthetic_trade_events():
    """Create synthetic trade events for testing without historical data.

    Returns a list of trade events that simulate a complete trading session:
    - Entry order at time T0
    - Exit order at time T1
    - Known prices, quantities, and expected PnL
    """
    base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

    return [
        {
            "idx": 0,
            "event_type": "entry",
            "event_time": base_time,
            "instrument_id": "XBT/USD",
            "rule_id": "range_fade",
            "exit_config": "default",
            "quantity": 0.1,
            "price": 50000.0,
        },
        {
            "idx": 0,
            "event_type": "exit",
            "event_time": base_time + timedelta(hours=1),
            "instrument_id": "XBT/USD",
            "rule_id": "range_fade",
            "exit_config": "default",
            "quantity": 0.1,
            "price": 50500.0,  # $500 profit
        },
    ]


class TestLedgerReconciliation:
    """Test that ledger entries reconcile correctly."""

    def test_entry_and_exit_orders_both_created(self):
        """Verify that both entry AND exit orders/fills are created for each trade."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        # Verify both entry and exit orders exist
        entry_orders = [o for o in result["orders"] if o["side"] == "BUY"]
        exit_orders = [o for o in result["orders"] if o["side"] == "SELL"]

        assert len(entry_orders) >= 1, "Must have at least one entry order"
        assert len(exit_orders) >= 1, "Must have at least one exit order"

        # Verify both entry and exit fills exist
        entry_fills = [f for f in result["fills"] if f["quantity"] > 0]
        exit_fills = [f for f in result["fills"] if f["quantity"] < 0]

        assert len(entry_fills) >= 1, "Must have at least one entry fill"
        assert len(exit_fills) >= 1, "Must have at least one exit fill"

    def test_fill_quantity_matches_position_quantity(self):
        """Verify that fill quantities sum to position quantities."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        # For each closed position, entry fill qty + exit fill qty should net to 0
        for pos in result["positions"]:
            if pos["status"] == "closed":
                # Find corresponding fills
                pos_fills = [f for f in result["fills"] if f["position_id"] == pos["id"]]
                total_fill_qty = sum(f["quantity"] for f in pos_fills)

                assert abs(total_fill_qty) < 1e-8, (
                    f"Position {pos['id']} fills don't net to zero: {total_fill_qty}"
                )

    def test_cash_changes_match_fill_net_amounts(self):
        """Verify that cash changes match the net amounts from fills."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        # Calculate expected cash change from fills
        expected_cash_change = sum(f["net_cash_change"] for f in result["fills"])
        actual_cash_change = result["final_capital"] - result["initial_capital"]

        assert abs(expected_cash_change - actual_cash_change) < 0.01, (
            f"Cash change mismatch: expected {expected_cash_change}, "
            f"got {actual_cash_change}"
        )

    def test_daily_pnl_summarizes_exit_fills(self):
        """Verify that daily PnL matches sum of exit fill realized PnL."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        # Sum realized PnL from exit fills
        exit_fills = [f for f in result["fills"] if f["quantity"] < 0]
        total_realized_pnl = sum(f.get("realized_pnl", 0.0) for f in exit_fills)

        # Sum daily PnL
        daily_total = sum(day["pnl"] for day in result["daily_pnl"].values())

        assert abs(total_realized_pnl - daily_total) < 0.01, (
            f"Daily PnL mismatch: exit fills {total_realized_pnl}, "
            f"daily summary {daily_total}"
        )

    def test_no_orphaned_orders_without_fills(self):
        """Verify that every order has at least one fill (or is cancelled)."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        for order in result["orders"]:
            if order["status"] != "cancelled":
                order_fills = [f for f in result["fills"] if f["order_id"] == order["id"]]
                assert len(order_fills) >= 1, (
                    f"Order {order['id']} has no fills and is not cancelled"
                )

    def test_no_orphaned_fills_without_orders(self):
        """Verify that every fill references a valid order."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(events)

        order_ids = {o["id"] for o in result["orders"]}

        for fill in result["fills"]:
            assert fill["order_id"] in order_ids, (
                f"Fill {fill['id']} references non-existent order {fill['order_id']}"
            )


class TestExposureLimits:
    """Test that exposure limits are enforced."""

    def test_position_size_limited_by_available_capital(self):
        """Verify that position size cannot exceed available capital * position_size_pct."""
        engine = PaperTradingEngine(database_url=None)

        # Try to enter a position larger than 10% of capital
        events = [
            {
                "idx": 0,
                "event_type": "entry",
                "event_time": datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                "instrument_id": "XBT/USD",
                "rule_id": "range_fade",
                "exit_config": "default",
                "quantity": 1.0,  # 1 BTC at $50k = $50k, way more than 10% of $10k
                "price": 50000.0,
            }
        ]

        result = engine.execute_synthetic_replay(
            events,
            initial_capital=10000.0,
            max_positions=5,
            position_size_pct=0.10,
        )

        # Should be blocked by exposure limit
        blocked_events = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked_events) >= 1, "Large position should be blocked by exposure limit"

    def test_total_exposure_limited(self):
        """Verify that total exposure across all positions is limited."""
        engine = PaperTradingEngine(database_url=None)

        # Try to open 3 positions (exceeds max_positions)
        base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            {
                "idx": i,
                "event_type": "entry",
                "event_time": base_time + timedelta(minutes=i),
                "instrument_id": f"ASSET{i}",
                "rule_id": "range_fade",
                "exit_config": "default",
                "quantity": 0.5,
                "price": 50000.0,
            }
            for i in range(3)
        ]

        result = engine.execute_synthetic_replay(
            events,
            initial_capital=100000.0,
            max_positions=2,
            position_size_pct=0.60,
        )

        # Third position should be blocked
        blocked_events = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked_events) >= 1, "Third position should be blocked"

    def test_negative_capital_rejected(self):
        """Verify that entries are rejected when capital would go negative."""
        engine = PaperTradingEngine(database_url=None)

        # First entry uses most capital
        base_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            {
                "idx": 0,
                "event_type": "entry",
                "event_time": base_time,
                "instrument_id": "XBT/USD",
                "rule_id": "range_fade",
                "exit_config": "default",
                "quantity": 0.002,  # $100 at $50k
                "price": 50000.0,
            },
            {
                "idx": 1,
                "event_type": "entry",
                "event_time": base_time + timedelta(minutes=1),
                "instrument_id": "ETH/USD",
                "rule_id": "range_fade",
                "exit_config": "default",
                "quantity": 1.0,  # $3000 at $3k, more than remaining capital
                "price": 3000.0,
            },
        ]

        result = engine.execute_synthetic_replay(
            events,
            initial_capital=1000.0,
            max_positions=5,
            position_size_pct=0.10,
        )

        # Second entry should be blocked due to insufficient capital
        blocked_events = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked_events) >= 1, "Second entry should be blocked"


class TestCostTracking:
    """Test that costs (commission, slippage) are tracked separately."""

    def test_commission_tracked_separately_from_gross(self):
        """Verify that commission is tracked as a separate field."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(
            events,
            commission_rate=0.001,  # 0.1% commission
        )

        # Verify commission field exists in fills
        for fill in result["fills"]:
            assert "commission_amount" in fill, "Fill must track commission separately"
            assert fill["commission_amount"] >= 0, "Commission cannot be negative"

    def test_slippage_tracked_separately_from_gross(self):
        """Verify that slippage is tracked as a separate field."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(
            events,
            slippage_rate=0.0005,  # 0.05% slippage
        )

        # Verify slippage field exists in fills
        for fill in result["fills"]:
            assert "slippage_amount" in fill, "Fill must track slippage separately"
            assert fill["slippage_amount"] >= 0, "Slippage cannot be negative"

    def test_net_pnl_equals_gross_minus_costs(self):
        """Verify that net PnL = gross PnL - commission - slippage."""
        engine = PaperTradingEngine(database_url=None)

        events = create_synthetic_trade_events()
        result = engine.execute_synthetic_replay(
            events,
            commission_rate=0.001,
            slippage_rate=0.0005,
        )

        # For each fill, verify the cash flow calculation
        for fill in result["fills"]:
            gross = fill["gross_amount"]
            commission = fill["commission_amount"]
            slippage = fill["slippage_amount"]
            net = fill["net_cash_change"]

            # Entry fills (positive quantity): cash outflow = -(gross + costs)
            # Exit fills (negative quantity): cash inflow = +(gross - costs)
            if fill["quantity"] > 0:  # Entry
                expected_net = -(gross + commission + slippage)
            else:  # Exit
                expected_net = gross - commission - slippage

            assert abs(net - expected_net) < 0.01, (
                f"Fill {fill['id']}: net {net} != expected {expected_net} "
                f"(gross {gross}, commission {commission}, slippage {slippage})"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
