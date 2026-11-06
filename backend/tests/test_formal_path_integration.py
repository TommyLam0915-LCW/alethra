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
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from app.services.paper_trading import PaperTradingEngine


INSTRUMENT = "XBT/USD"
INTERVAL = 1  # 1-minute bars
ANCHOR = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def formal_path_setup():
    """Create complete formal path test data: user, study run, trades, portfolio."""
    db = SessionLocal()
    user_id = uuid.uuid4()
    study_run_id = uuid.uuid4()
    portfolio_id = uuid.uuid4()
    run_id = uuid.uuid4()
    
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
        study_run = StrategyStudyRun(
            id=study_run_id,
            user_id=user_id,
            lane="formal_path_test",
            research_version="test-v1",
            instrument_id=INSTRUMENT,
            interval_minutes=INTERVAL,
            provider_id="kraken",
            data_nature="live",
            bar_count=100,
            first_open_time=ANCHOR,
            last_open_time=ANCHOR + timedelta(minutes=100),
            fetched_at=ANCHOR,
            dataset_integrity_ok=True,
            trade_count=2,  # We'll create 2 trades
            settings_json={"test": True},
            walk_forward_json={"folds": [1, 2, 3]},
            bars_json=[],  # Empty bars for test
        )
        db.add(study_run)
        db.flush()

        # Create 2 synthetic trades with known PnL
        # Trade 1: Long entry at $50,000, exit at $50,500 → $500 profit
        trade1 = StrategyStudyTrade(
            run_id=study_run_id,
            trade_index=0,
            fold=1,
            rule_id="range_fade",
            rule_label_zh="區間 fade",
            exit_id="default",
            exit_label_zh="預設",
            direction="long",
            exit_reason="signal_flip",
            outcome="win",
            signal_time=ANCHOR + timedelta(minutes=10),
            entry_time=ANCHOR + timedelta(minutes=10),
            exit_time=ANCHOR + timedelta(minutes=20),
            entry_price=Decimal("50000"),
            exit_price=Decimal("50500"),
            quantity=Decimal("0.1"),
            gross_pnl=Decimal("50"),
            commission=Decimal("0"),
            slippage_cost=Decimal("0"),
            total_cost=Decimal("0"),
            net_pnl=Decimal("50"),
            cash_after=Decimal("100050"),
            bars_held=10,
            pnl_reason_zh="測試獲利",
            pnl_reason_en="test profit",
        )
        db.add(trade1)

        # Trade 2: Long entry at $50,200, exit at $49,800 → -$40 loss
        trade2 = StrategyStudyTrade(
            run_id=study_run_id,
            trade_index=1,
            fold=1,
            rule_id="range_fade",
            rule_label_zh="區間 fade",
            exit_id="default",
            exit_label_zh="預設",
            direction="long",
            exit_reason="stop_loss",
            outcome="loss",
            signal_time=ANCHOR + timedelta(minutes=30),
            entry_time=ANCHOR + timedelta(minutes=30),
            exit_time=ANCHOR + timedelta(minutes=40),
            entry_price=Decimal("50200"),
            exit_price=Decimal("49800"),
            quantity=Decimal("0.1"),
            gross_pnl=Decimal("-40"),
            commission=Decimal("0"),
            slippage_cost=Decimal("0"),
            total_cost=Decimal("0"),
            net_pnl=Decimal("-40"),
            cash_after=Decimal("100010"),
            bars_held=10,
            pnl_reason_zh="測試虧損",
            pnl_reason_en="test loss",
        )
        db.add(trade2)

        # Create paper portfolio
        db.execute(
            text("""
            INSERT INTO paper_portfolios (
                id, owner_id, currency, initial_virtual_cash, current_virtual_cash,
                status, created_at, updated_at
            ) VALUES (
                :id, :owner_id, 'USD', 100000.0, 100000.0, 'active',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """),
            {"id": portfolio_id, "owner_id": user_id},
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
            "user_id": user_id,
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
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.commit()
        db.close()


class TestFormalPathIntegration:
    """Test formal path: execute_historical_replay with real DB records."""

    def test_entry_and_exit_orders_both_created(self, formal_path_setup):
        """Verify that both entry AND exit orders are created in DB."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=str(setup["run_id"]),
            user_id=str(setup["user_id"]),
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
            run_id=str(setup["run_id"]),
            user_id=str(setup["user_id"]),
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
            run_id=str(setup["run_id"]),
            user_id=str(setup["user_id"]),
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
            run_id=str(setup["run_id"]),
            user_id=str(setup["user_id"]),
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

            # Expected: 100000 + 50 (trade1 profit) - 40 (trade2 loss) = 100010
            expected_final = initial_capital + Decimal("50") - Decimal("40")

            assert abs(final_capital - expected_final) < Decimal("0.01"), (
                f"Cash flow mismatch: expected {expected_final}, got {final_capital}"
            )
        finally:
            db.close()

    def test_daily_pnl_matches_trades(self, formal_path_setup):
        """Verify that daily PnL summary matches individual trade PnL."""
        setup = formal_path_setup
        engine = PaperTradingEngine(database_url=None)

        result = engine.execute_historical_replay(
            run_id=str(setup["run_id"]),
            user_id=str(setup["user_id"]),
            days_back=30,
        )

        # Query DB for daily metrics
        db = SessionLocal()
        try:
            metrics = db.execute(
                text("SELECT * FROM paper_trading_daily_metrics WHERE run_id = :rid"),
                {"rid": setup["run_id"]},
            ).fetchall()

            # Sum daily PnL
            total_daily_pnl = sum(Decimal(str(m._mapping["daily_pnl"])) for m in metrics)

            # Expected total: 50 - 40 = 10
            expected_total = Decimal("10")

            assert abs(total_daily_pnl - expected_total) < Decimal("0.01"), (
                f"Daily PnL mismatch: expected {expected_total}, got {total_daily_pnl}"
            )
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
