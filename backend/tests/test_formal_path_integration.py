"""Formal path integration test for paper trading.

Verifies that execute_historical_replay:
1. Reads from strategy_study_trades (formal path, not parallel engine)
2. Creates correct paper_orders, paper_fills, paper_positions
3. Updates paper_portfolios.current_virtual_cash correctly
4. Handles both entry AND exit orders/fills (double-entry bookkeeping)
5. Cash flow reconciles: final_capital = initial + sum(exit proceeds) - sum(entry costs)
"""

import pytest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from app.database import SessionLocal
from app.models.user import User
from app.services.paper_trading import PaperTradingEngine


INSTRUMENT = "XBT/USD"
INTERVAL = 1  # 1-minute bars
# The replay window is relative (days_back=30 counted from the service call), so
# the fixture's trades must be relative too; a frozen calendar anchor silently
# aged out of the window and made the seed invisible.
ANCHOR = datetime.now(timezone.utc) - timedelta(days=5)


@pytest.fixture
def formal_path_setup():
    """Create complete formal path test data: user, study run, trades, portfolio."""
    db = SessionLocal()
    user_id = uuid.uuid4()
    # Ids that the service reads through raw text() binds are seeded in the same
    # textual form the API hands over (str(UUID) with dashes); SQLAlchemy's UUID
    # column type writes dashless hex on SQLite, so an ORM-seeded row and a
    # dashed bind never match there.
    study_run_id = str(uuid.uuid4())
    portfolio_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    
    try:
        # Create user
        user = User(
            id=user_id,
            email=f"formal-{user_id.hex[:10]}@test.local",
            password_hash="test_hash",
            display_name="Formal Path Test",
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        db.flush()

        # Create strategy study run
        db.execute(
            text("""
            INSERT INTO strategy_study_runs (
                id, user_id, lane, research_version, instrument_id, interval_minutes,
                provider_id, data_nature, bar_count, first_open_time, last_open_time,
                fetched_at, settings_json, walk_forward_json, bars_json, trade_count,
                created_at
            ) VALUES (
                :id, :user_id, 'formal_path_test', 'test-v1', :instrument, :interval,
                'kraken', 'live', 100, :first, :last, :last,
                '{}', '{"folds": [1, 2, 3]}', '[]', 2, CURRENT_TIMESTAMP
            )
        """),
            {
                "id": study_run_id,
                "user_id": str(user_id),
                "instrument": INSTRUMENT,
                "interval": INTERVAL,
                "first": ANCHOR,
                "last": ANCHOR + timedelta(minutes=100),
            },
        )

        # Create 2 synthetic trades with known prices
        # Trade 1: Long entry at $50,000, exit at $50,500 → +1% price move
        db.execute(
            text("""
            INSERT INTO strategy_study_trades (
                id, run_id, trade_index, fold, rule_id, rule_label_zh, exit_id,
                exit_label_zh, direction, exit_reason, outcome, signal_time,
                entry_time, exit_time, entry_price, exit_price, quantity,
                gross_pnl, commission, slippage_cost, total_cost, net_pnl,
                cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
            ) VALUES (
                :id, :run_id, 0, 1, 'range_fade', '區間 fade', 'default', '預設',
                'long', 'signal_flip', 'win', :entry_time, :entry_time, :exit_time,
                50000, 50500, 0.1, 50, 0, 0, 0, 50, 100050, 10,
                '測試獲利', 'test profit', CURRENT_TIMESTAMP
            )
        """),
            {
                "id": str(uuid.uuid4()),
                "run_id": study_run_id,
                "entry_time": ANCHOR + timedelta(minutes=10),
                "exit_time": ANCHOR + timedelta(minutes=20),
            },
        )

        # Trade 2: Long entry at $50,200, exit at $49,800 → -0.8% price move
        db.execute(
            text("""
            INSERT INTO strategy_study_trades (
                id, run_id, trade_index, fold, rule_id, rule_label_zh, exit_id,
                exit_label_zh, direction, exit_reason, outcome, signal_time,
                entry_time, exit_time, entry_price, exit_price, quantity,
                gross_pnl, commission, slippage_cost, total_cost, net_pnl,
                cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
            ) VALUES (
                :id, :run_id, 1, 1, 'range_fade', '區間 fade', 'default', '預設',
                'long', 'stop_loss', 'loss', :entry_time, :entry_time, :exit_time,
                50200, 49800, 0.1, -40, 0, 0, 0, -40, 100010, 10,
                '測試虧損', 'test loss', CURRENT_TIMESTAMP
            )
        """),
            {
                "id": str(uuid.uuid4()),
                "run_id": study_run_id,
                "entry_time": ANCHOR + timedelta(minutes=30),
                "exit_time": ANCHOR + timedelta(minutes=40),
            },
        )

        # Create paper portfolio
        db.execute(
            text("""
            INSERT INTO paper_portfolios (
                id, owner_id, currency, initial_virtual_cash, current_virtual_cash,
                status, created_at
            ) VALUES (
                :id, :owner_id, 'USD', 100000.0, 100000.0, 'active',
                CURRENT_TIMESTAMP
            )
        """),
            {"id": portfolio_id, "owner_id": str(user_id)},
        )

        # Create paper trading run
        db.execute(
            text("""
            INSERT INTO paper_trading_runs (
                id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                start_date, status, initial_capital, total_pnl, total_trades,
                created_at, updated_at
            ) VALUES (
                :id, :portfolio_id, 'range_fade', :instrument, :interval, 'default',
                CURRENT_TIMESTAMP, 'queued', 100000.0, 0, 0,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """),
            {
                "id": run_id,
                "portfolio_id": portfolio_id,
                "instrument": INSTRUMENT,
                "interval": INTERVAL,
            },
        )

        db.commit()

        yield {
            "user_id": str(user_id),
            "study_run_id": study_run_id,
            "portfolio_id": portfolio_id,
            "run_id": run_id,
            "initial_capital": Decimal("100000"),
        }

    finally:
        # Cleanup
        db.execute(text("DELETE FROM paper_fills WHERE paper_order_id IN (SELECT id FROM paper_orders WHERE portfolio_id = :pid)"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_orders WHERE portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_positions WHERE portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM paper_trading_runs WHERE id = :rid"), {"rid": run_id})
        db.execute(text("DELETE FROM paper_portfolios WHERE id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM decision_records WHERE paper_portfolio_id = :pid"), {"pid": portfolio_id})
        db.execute(text("DELETE FROM strategy_study_trades WHERE run_id = :sid"), {"sid": study_run_id})
        db.execute(text("DELETE FROM strategy_study_runs WHERE id = :sid"), {"sid": study_run_id})
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id.hex})
        db.commit()
        db.close()


class TestFormalPathIntegration:
    """Test formal path: execute_historical_replay with real DB records."""

    def test_entry_and_exit_orders_both_created(self, formal_path_setup):
        """Verify that both entry AND exit orders are created in DB."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=setup["run_id"],
            user_id=setup["user_id"],
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # Query DB for orders
        db = SessionLocal()
        try:
            orders = db.execute(
                text("SELECT * FROM paper_orders WHERE portfolio_id = :pid"),
                {"pid": setup["portfolio_id"]},
            ).fetchall()

            entry_orders = [o for o in orders if o._mapping["side"] == "BUY"]
            exit_orders = [o for o in orders if o._mapping["side"] == "SELL"]

            # Should have 2 entry orders and 2 exit orders (one per trade)
            assert len(entry_orders) == 2, f"Expected 2 entry orders, got {len(entry_orders)}"
            assert len(exit_orders) == 2, f"Expected 2 exit orders, got {len(exit_orders)}"

            # All orders should be filled
            for order in orders:
                assert order._mapping["status"] == "filled", f"Order {order._mapping['id']} not filled"
        finally:
            db.close()

    def test_entry_and_exit_fills_both_created(self, formal_path_setup):
        """Verify that both entry AND exit fills are created in DB."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=setup["run_id"],
            user_id=setup["user_id"],
            days_back=30,
        )

        # Query DB for fills
        db = SessionLocal()
        try:
            fills = db.execute(
                text("""
                SELECT f.* FROM paper_fills f
                JOIN paper_orders o ON f.paper_order_id = o.id
                WHERE o.portfolio_id = :pid
            """),
                {"pid": setup["portfolio_id"]},
            ).fetchall()

            # Should have 4 fills: 2 entry + 2 exit
            assert len(fills) == 4, f"Expected 4 fills, got {len(fills)}"

            # Verify fills have required fields
            for fill in fills:
                assert fill._mapping["quantity"] > 0, "Fill quantity must be positive"
                assert fill._mapping["gross_amount"] != 0, "Gross amount must be non-zero"
                assert "net_cash_change" in fill._mapping.keys(), "Fill must have net_cash_change"
        finally:
            db.close()

    def test_positions_created_and_closed(self, formal_path_setup):
        """Verify that positions are created and properly closed."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=setup["run_id"],
            user_id=setup["user_id"],
            days_back=30,
        )

        # Query DB for positions
        db = SessionLocal()
        try:
            positions = db.execute(
                text("SELECT * FROM paper_positions WHERE portfolio_id = :pid"),
                {"pid": setup["portfolio_id"]},
            ).fetchall()

            # Should have 2 positions (one per trade)
            assert len(positions) == 2, f"Expected 2 positions, got {len(positions)}"

            # All positions should be closed
            for pos in positions:
                assert pos._mapping["status"] == "closed", f"Position {pos._mapping['id']} not closed"
                assert pos._mapping["closed_at"] is not None, "Closed position must have closed_at"
                assert pos._mapping["realized_pnl"] is not None, "Closed position must have realized_pnl"
        finally:
            db.close()

    def test_cash_flow_reconciles(self, formal_path_setup):
        """Verify that final capital = initial + sum(exit proceeds) - sum(entry costs)."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=setup["run_id"],
            user_id=setup["user_id"],
            days_back=30,
        )

        # Query DB for final capital
        db = SessionLocal()
        try:
            portfolio = db.execute(
                text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :pid"),
                {"pid": setup["portfolio_id"]},
            ).fetchone()

            final_capital = Decimal(str(portfolio._mapping["current_virtual_cash"]))
            initial_capital = setup["initial_capital"]

            fills = db.execute(
                text("""
                SELECT f.net_cash_change FROM paper_fills f
                JOIN paper_orders o ON f.paper_order_id = o.id
                WHERE o.portfolio_id = :pid
            """),
                {"pid": setup["portfolio_id"]},
            ).fetchall()
            assert len(fills) == 4, f"Expected 2 entry + 2 exit fills, got {len(fills)}"

            # The replay sizes each leg from equity × position_size_pct and charges
            # its own commission and slippage, so the source trade's P&L columns are
            # inputs, not the expected ledger result. Closure is checked against the
            # fill postings themselves: entry rows are negative, exit rows positive.
            net_flows = sum(Decimal(str(f._mapping["net_cash_change"])) for f in fills)
            expected_final = initial_capital + net_flows

            assert abs(final_capital - expected_final) < Decimal("0.01"), (
                f"Cash flow mismatch: expected {expected_final}, got {final_capital}"
            )
            # Anchored outside the ledger: each leg risks 10% of the equity then
            # open, at the seeded prices, less 0.1% commission and 0.05% slippage
            # on both sides — 100000 → 100069.85 → 99960.21171812749003984063745.
            # Without this, a product change that drops or duplicates a cost still
            # reconciles with itself.
            assert abs(final_capital - Decimal("99960.21171812749003984063745")) < Decimal("0.01"), (
                f"Final capital {final_capital} does not match the hand-computed round trip"
            )
            assert abs(Decimal(str(result["final_capital"])) - final_capital) < Decimal("0.01"), (
                f"Returned capital {result['final_capital']} diverges from ledger {final_capital}"
            )
        finally:
            db.close()

    def test_daily_pnl_matches_trades(self, formal_path_setup):
        """Verify that daily PnL summary matches individual trade PnL."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=setup["run_id"],
            user_id=setup["user_id"],
            days_back=30,
        )

        # Query DB for daily metrics
        db = SessionLocal()
        try:
            metrics = db.execute(
                text("SELECT * FROM paper_trading_daily_metrics WHERE run_id = :rid"),
                {"rid": setup["run_id"]},
            ).fetchall()
            assert len(metrics) > 0, "Replay must write daily metrics"

            # Sum daily PnL
            total_daily_pnl = sum(Decimal(str(m._mapping["daily_pnl"])) for m in metrics)

            # Three representations are written by separate statements: the daily
            # summary, the fill postings, and each closed position's realized P&L.
            fills = db.execute(
                text("""
                SELECT f.net_cash_change FROM paper_fills f
                JOIN paper_orders o ON f.paper_order_id = o.id
                WHERE o.portfolio_id = :pid
            """),
                {"pid": setup["portfolio_id"]},
            ).fetchall()
            net_flows = sum(Decimal(str(f._mapping["net_cash_change"])) for f in fills)

            positions = db.execute(
                text("SELECT realized_pnl FROM paper_positions WHERE portfolio_id = :pid"),
                {"pid": setup["portfolio_id"]},
            ).fetchall()
            realized_total = sum(
                Decimal(str(p._mapping["realized_pnl"])) for p in positions
            )

            assert abs(total_daily_pnl - net_flows) < Decimal("0.01"), (
                f"Daily PnL {total_daily_pnl} does not reconcile to fill flows {net_flows}"
            )
            assert abs(total_daily_pnl - realized_total) < Decimal("0.01"), (
                f"Daily PnL {total_daily_pnl} does not reconcile to realized {realized_total}"
            )
            assert abs(Decimal(str(result["total_pnl"])) - total_daily_pnl) < Decimal("0.01"), (
                f"Returned PnL {result['total_pnl']} diverges from daily summary {total_daily_pnl}"
            )
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
