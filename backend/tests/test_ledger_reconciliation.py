"""Independent ledger reconciliation test.

Verifies that the paper trading engine's DB writes are internally consistent
and match hand-calculated expected values. This is the owner's item #4 from
the minimum delivery scope: "Independent ledger reconciliation: read back
from DB fills/portfolio, verify against hand-calculated fixed case."

Tests:
1. Cash reconciliation: beginning cash + sum(fills.net_cash_change) = ending cash
2. Position closure: all positions closed after full replay
3. PnL accuracy: DB total_pnl matches hand calculation
4. Fill completeness: every trade has entry + exit fills
5. Cross-user isolation: one user's fills don't affect another's portfolio
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal, engine as db_engine
from app.services.paper_trading import PaperTradingEngine


@pytest.fixture
def test_user_id():
    """Create a test user and return ID."""
    db = SessionLocal()
    try:
        user_id = str(uuid4())
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, true, false)
            """),
            {"id": user_id, "email": f"recon_{user_id[:8]}@test.local", "password": "test_hash_placeholder"}
        )
        db.commit()
        yield user_id
    finally:
        # Clean up in FK-safe order: fills → orders → decisions → positions → runs → portfolios → user
        db.execute(
            text("""
                DELETE FROM paper_fills WHERE paper_order_id IN (
                    SELECT o.id FROM paper_orders o
                    JOIN paper_portfolios p ON o.portfolio_id = p.id
                    WHERE p.owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_orders WHERE portfolio_id IN (
                    SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM decision_records WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_positions WHERE portfolio_id IN (
                    SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_trading_daily_metrics WHERE run_id IN (
                    SELECT r.id FROM paper_trading_runs r
                    JOIN paper_portfolios p ON r.portfolio_id = p.id
                    WHERE p.owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_trading_runs WHERE portfolio_id IN (
                    SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"), {"user_id": user_id})
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
        db.close()


@pytest.fixture
def seed_strategy_study_data(test_user_id):
    """Seed strategy_study_runs and strategy_study_trades with KNOWN data.

    Creates 2 trades with hand-calculable PnL:
    - Trade 1: Buy 100 units @ $100, sell @ $110 → PnL = +$1,000
    - Trade 2: Buy 101 units @ $100, sell @ $90 → PnL = -$1,010
    Total expected PnL = -$10

    The engine will recalculate quantities based on position_size_pct, so we
    seed with approximate quantities and let the engine compute exact values.
    """
    db = SessionLocal()
    try:
        # Create strategy study run
        study_run_id = str(uuid4())
        now = datetime.now(timezone.utc)
        db.execute(
            text("""
                INSERT INTO strategy_study_runs (
                    id, user_id, lane, research_version, instrument_id, interval_minutes,
                    provider_id, data_nature, bar_count, first_open_time, last_open_time,
                    settings_json, walk_forward_json, bars_json, trade_count, created_at
                ) VALUES (
                    :id, :user_id, 'strategy_research_simulated', 'v1.0', 'TEST/USD', 60,
                    'test_provider', 'simulated', 100, :now, :now,
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 2, :now
                )
            """),
            {
                "id": study_run_id,
                "user_id": test_user_id,
                "now": now,
            }
        )

        # Seed trade 1: entry @ $100, exit @ $110 (profitable)
        trade1_id = str(uuid4())
        entry_time_1 = now - timedelta(days=5)
        exit_time_1 = now - timedelta(days=5) + timedelta(hours=1)
        db.execute(
            text("""
                INSERT INTO strategy_study_trades (
                    id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                    direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                    entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                    total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                ) VALUES (
                    :id, :run_id, 0, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                    'long', 'signal', 'win', :entry_time, :entry_time, :exit_time,
                    100.0, 110.0, 100.0, 1000.0, 0.0, 0.0, 0.0, 1000.0, 101000.0, 1,
                    'Profit from price increase', 'Profit from price increase', :now
                )
            """),
            {
                "id": trade1_id,
                "run_id": study_run_id,
                "entry_time": entry_time_1,
                "exit_time": exit_time_1,
                "now": now,
            }
        )

        # Seed trade 2: entry @ $100, exit @ $90 (loss)
        trade2_id = str(uuid4())
        entry_time_2 = now - timedelta(days=3)
        exit_time_2 = now - timedelta(days=3) + timedelta(hours=1)
        db.execute(
            text("""
                INSERT INTO strategy_study_trades (
                    id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                    direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                    entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                    total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                ) VALUES (
                    :id, :run_id, 1, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                    'long', 'signal', 'loss', :entry_time, :entry_time, :exit_time,
                    100.0, 90.0, 100.0, -1000.0, 0.0, 0.0, 0.0, -1000.0, 100000.0, 1,
                    'Loss from price decrease', 'Loss from price decrease', :now
                )
            """),
            {
                "id": trade2_id,
                "run_id": study_run_id,
                "entry_time": entry_time_2,
                "exit_time": exit_time_2,
                "now": now,
            }
        )

        db.commit()
        yield study_run_id, trade1_id, trade2_id
    finally:
        db.execute(text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"), {"run_id": study_run_id})
        db.execute(text("DELETE FROM strategy_study_runs WHERE id = :id"), {"id": study_run_id})
        db.commit()
        db.close()


class TestLedgerReconciliation:
    """Independent reconciliation: verify DB state matches hand calculations."""

    def test_cash_reconciliation_from_fills(self, test_user_id, seed_strategy_study_data):
        """Verify: beginning cash + sum(fills.net_cash_change) = ending cash.

        This reads from DB fills, not from in-memory ledger state.
        """
        study_run_id, _, _ = seed_strategy_study_data
        initial_capital = Decimal("100000.00")
        position_size_pct = 0.10

        # Create portfolio and run
        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Execute replay
        replay_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
            position_size_pct=position_size_pct,
        )

        # Read back from DB: sum all fills.net_cash_change for this portfolio
        db = SessionLocal()
        try:
            fills = db.execute(
                text("""
                    SELECT f.net_cash_change, f.quantity, f.observed_price, f.simulated_fill_reason
                    FROM paper_fills f
                    JOIN paper_orders o ON f.paper_order_id = o.id
                    WHERE o.portfolio_id = :portfolio_id
                    ORDER BY f.execution_date, f.simulated_fill_reason
                """),
                {"portfolio_id": portfolio_id}
            ).fetchall()

            # Calculate sum of net cash flows from fills
            total_net_flows = Decimal("0")
            entry_flows = []
            exit_flows = []
            for fill in fills:
                net_change = Decimal(str(fill.net_cash_change))
                total_net_flows += net_change
                if fill.simulated_fill_reason == "signal":
                    entry_flows.append(net_change)
                elif fill.simulated_fill_reason == "exit_signal":
                    exit_flows.append(net_change)

            # Read portfolio's ending cash
            portfolio = db.execute(
                text("SELECT current_virtual_cash, initial_virtual_cash FROM paper_portfolios WHERE id = :id"),
                {"id": portfolio_id}
            ).fetchone()

            ending_cash = Decimal(str(portfolio.current_virtual_cash))
            beginning_cash = Decimal(str(portfolio.initial_virtual_cash))

            # Verify reconciliation: beginning + net flows = ending
            expected_ending = beginning_cash + total_net_flows
            reconciliation_diff = abs(ending_cash - expected_ending)

            assert reconciliation_diff < Decimal("0.01"), (
                f"Cash reconciliation failed: ending_cash={ending_cash}, "
                f"expected={expected_ending}, diff={reconciliation_diff}, "
                f"entry_flows={entry_flows}, exit_flows={exit_flows}"
            )

            # Verify we have both entry and exit fills
            assert len(entry_flows) > 0, "No entry fills found"
            assert len(exit_flows) > 0, "No exit fills found"

        finally:
            db.close()

    def test_all_positions_closed_after_replay(self, test_user_id, seed_strategy_study_data):
        """Verify: all positions are closed after full replay completes."""
        study_run_id, _, _ = seed_strategy_study_data
        initial_capital = Decimal("100000.00")

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Execute replay
        engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        # Read back from DB: check all positions are closed
        db = SessionLocal()
        try:
            open_positions = db.execute(
                text("""
                    SELECT id, status, quantity, realized_pnl
                    FROM paper_positions
                    WHERE portfolio_id = :portfolio_id AND status = 'open'
                """),
                {"portfolio_id": portfolio_id}
            ).fetchall()

            assert len(open_positions) == 0, (
                f"Found {len(open_positions)} open positions after replay: {open_positions}"
            )

            # Verify all positions have realized_pnl set
            closed_positions = db.execute(
                text("""
                    SELECT id, status, realized_pnl
                    FROM paper_positions
                    WHERE portfolio_id = :portfolio_id
                """),
                {"portfolio_id": portfolio_id}
            ).fetchall()

            for pos in closed_positions:
                assert pos.status == "closed", f"Position {pos.id} not closed"
                assert pos.realized_pnl is not None, f"Position {pos.id} has no realized_pnl"

        finally:
            db.close()

    def test_pnl_matches_hand_calculation(self, test_user_id, seed_strategy_study_data):
        """Verify: DB total_pnl matches hand-calculated expected value.

        With position_size_pct=0.10 and initial_capital=100000:
        - Trade 1: quantity = (100000 * 0.10) / 100 = 100 units
                   PnL = 100 * (110 - 100) = +1000
                   After: capital = 101000
        - Trade 2: quantity = (101000 * 0.10) / 100 = 101 units
                   PnL = 101 * (90 - 100) = -1010
                   After: capital = 101000 - 1010 = 99990
        Total PnL = 1000 - 1010 = -10
        """
        study_run_id, _, _ = seed_strategy_study_data
        initial_capital = Decimal("100000.00")
        position_size_pct = 0.10

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Execute replay
        replay_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
            position_size_pct=position_size_pct,
        )

        # Hand-calculate expected PnL
        # Trade 1: buy 100 units @ 100, sell @ 110
        capital_after_trade_1 = initial_capital
        quantity_1 = (capital_after_trade_1 * Decimal(str(position_size_pct))) / Decimal("100")
        pnl_1 = quantity_1 * (Decimal("110") - Decimal("100"))
        capital_after_trade_1 = capital_after_trade_1 + pnl_1

        # Trade 2: buy units @ 100, sell @ 90
        quantity_2 = (capital_after_trade_1 * Decimal(str(position_size_pct))) / Decimal("100")
        pnl_2 = quantity_2 * (Decimal("90") - Decimal("100"))
        expected_total_pnl = pnl_1 + pnl_2

        # Read DB total_pnl
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT total_pnl, final_capital FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()

            db_total_pnl = Decimal(str(run.total_pnl))
            db_final_capital = Decimal(str(run.final_capital))

            # Verify PnL matches (allow small floating point tolerance)
            pnl_diff = abs(db_total_pnl - expected_total_pnl)
            assert pnl_diff < Decimal("0.01"), (
                f"PnL mismatch: DB={db_total_pnl}, expected={expected_total_pnl}, diff={pnl_diff}"
            )

            # Verify final capital
            expected_final_capital = initial_capital + expected_total_pnl
            capital_diff = abs(db_final_capital - expected_final_capital)
            assert capital_diff < Decimal("0.01"), (
                f"Final capital mismatch: DB={db_final_capital}, expected={expected_final_capital}, diff={capital_diff}"
            )

        finally:
            db.close()

    def test_fill_completeness_every_trade_has_entry_and_exit(
        self, test_user_id, seed_strategy_study_data
    ):
        """Verify: every seeded trade produces both entry and exit fills."""
        study_run_id, trade1_id, trade2_id = seed_strategy_study_data
        initial_capital = Decimal("100000.00")

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Execute replay
        engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        # Read back from DB: count fills by type
        db = SessionLocal()
        try:
            fill_counts = db.execute(
                text("""
                    SELECT simulated_fill_reason, COUNT(*) as count
                    FROM paper_fills f
                    JOIN paper_orders o ON f.paper_order_id = o.id
                    WHERE o.portfolio_id = :portfolio_id
                    GROUP BY simulated_fill_reason
                """),
                {"portfolio_id": portfolio_id}
            ).fetchall()

            fill_dict = {row.simulated_fill_reason: row.count for row in fill_counts}

            # Should have 2 entry fills (signal) and 2 exit fills (exit_signal)
            assert fill_dict.get("signal", 0) == 2, (
                f"Expected 2 entry fills, got {fill_dict.get('signal', 0)}"
            )
            assert fill_dict.get("exit_signal", 0) == 2, (
                f"Expected 2 exit fills, got {fill_dict.get('exit_signal', 0)}"
            )

        finally:
            db.close()

    def test_cross_user_isolation(self, test_user_id, seed_strategy_study_data):
        """Verify: one user's fills don't affect another user's portfolio.

        Create two users, seed trades for user 1 only, verify user 2's
        portfolio is unaffected.
        """
        study_run_id, _, _ = seed_strategy_study_data
        initial_capital = Decimal("100000.00")

        # Create second user
        db = SessionLocal()
        try:
            user2_id = str(uuid4())
            db.execute(
                text("""
                    INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                    VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, true, false)
                """),
                {"id": user2_id, "email": f"user2_{user2_id[:8]}@test.local", "password": "test_hash"}
            )
            db.commit()
        finally:
            db.close()

        try:
            # Create portfolio for user 1 (has seeded trades)
            engine = PaperTradingEngine(database_url=str(db_engine.url))
            result1 = engine.create_portfolio(
                user_id=test_user_id,
                skill="test_rule",
                instrument_id="TEST/USD",
                interval_minutes=60,
                exit_config="default",
                initial_capital=initial_capital,
            )
            portfolio1_id = result1["portfolio_id"]
            run1_id = result1["run_id"]

            # Create portfolio for user 2 (no seeded trades)
            result2 = engine.create_portfolio(
                user_id=user2_id,
                skill="test_rule",
                instrument_id="TEST/USD",
                interval_minutes=60,
                exit_config="default",
                initial_capital=initial_capital,
            )
            portfolio2_id = result2["portfolio_id"]
            run2_id = result2["run_id"]

            # Execute replay for user 1 (should find trades)
            engine.execute_historical_replay(
                run_id=run1_id,
                user_id=test_user_id,
                days_back=30,
            )

            # Execute replay for user 2 (should find NO trades)
            result2_replay = engine.execute_historical_replay(
                run_id=run2_id,
                user_id=user2_id,
                days_back=30,
            )

            # Verify user 2's portfolio is unchanged
            db = SessionLocal()
            try:
                portfolio2 = db.execute(
                    text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :id"),
                    {"id": portfolio2_id}
                ).fetchone()

                # User 2 should have 0 trades and unchanged capital
                assert result2_replay["total_trades"] == 0, (
                    f"User 2 should have 0 trades, got {result2_replay['total_trades']}"
                )
                assert Decimal(str(portfolio2.current_virtual_cash)) == initial_capital, (
                    f"User 2's capital should be unchanged: {portfolio2.current_virtual_cash} != {initial_capital}"
                )

                # Verify user 1's portfolio HAS changed
                portfolio1 = db.execute(
                    text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :id"),
                    {"id": portfolio1_id}
                ).fetchone()
                assert Decimal(str(portfolio1.current_virtual_cash)) != initial_capital, (
                    "User 1's capital should have changed after replay"
                )

            finally:
                db.close()

        finally:
            # Clean up user 2 in FK-safe order
            db = SessionLocal()
            try:
                db.execute(
                    text("""
                        DELETE FROM paper_fills WHERE paper_order_id IN (
                            SELECT o.id FROM paper_orders o
                            JOIN paper_portfolios p ON o.portfolio_id = p.id
                            WHERE p.owner_id = :user_id
                        )
                    """),
                    {"user_id": user2_id},
                )
                db.execute(
                    text("""
                        DELETE FROM paper_orders WHERE portfolio_id IN (
                            SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                        )
                    """),
                    {"user_id": user2_id},
                )
                db.execute(
                    text("DELETE FROM decision_records WHERE owner_id = :user_id"),
                    {"user_id": user2_id},
                )
                db.execute(
                    text("""
                        DELETE FROM paper_positions WHERE portfolio_id IN (
                            SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                        )
                    """),
                    {"user_id": user2_id},
                )
                db.execute(
                    text("""
                        DELETE FROM paper_trading_daily_metrics WHERE run_id IN (
                            SELECT r.id FROM paper_trading_runs r
                            JOIN paper_portfolios p ON r.portfolio_id = p.id
                            WHERE p.owner_id = :user_id
                        )
                    """),
                    {"user_id": user2_id},
                )
                db.execute(
                    text("""
                        DELETE FROM paper_trading_runs WHERE portfolio_id IN (
                            SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                        )
                    """),
                    {"user_id": user2_id},
                )
                db.execute(text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"), {"user_id": user2_id})
                db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user2_id})
                db.commit()
            finally:
                db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
