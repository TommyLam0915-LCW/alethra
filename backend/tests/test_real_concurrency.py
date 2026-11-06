"""Real concurrency test with barrier synchronization.

Verifies that two independent threads competing for the same run:
- Exactly one acquires execution rights
- Only one set of orders/fills/positions is created
- Rejected request doesn't change state
- No uncaught thread exceptions

Uses threading.Barrier to synchronize thread start, increasing the probability
of true race condition at the atomic UPDATE statement.
"""

import pytest
import threading
import time
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
            {"id": user_id, "email": f"concurrent_{user_id[:8]}@test.local", "password": "test_hash"}
        )
        db.commit()
        yield user_id
    finally:
        # FK-safe cleanup
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
        db.execute(
            text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
        db.close()


@pytest.fixture
def seed_concurrent_trades(test_user_id):
    """Seed multiple trades to increase replay duration and race probability."""
    db = SessionLocal()
    try:
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
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 5, :now
                )
            """),
            {"id": study_run_id, "user_id": test_user_id, "now": now},
        )

        # Seed 5 trades to increase replay duration
        for i in range(5):
            trade_id = str(uuid4())
            entry_time = now - timedelta(days=10 - i)
            exit_time = entry_time + timedelta(hours=1)
            entry_price = 100.0 + i * 2
            exit_price = entry_price + (10 if i % 2 == 0 else -5)

            db.execute(
                text("""
                    INSERT INTO strategy_study_trades (
                        id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                        direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                        entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                        total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                    ) VALUES (
                        :id, :run_id, :idx, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                        'long', 'signal', :outcome, :entry_time, :entry_time, :exit_time,
                        :entry_price, :exit_price, 100.0, :gross, 0.0, 0.0, 0.0, :net, 100000.0, 1,
                        'Trade', 'Trade', :now
                    )
                """),
                {
                    "id": trade_id,
                    "run_id": study_run_id,
                    "idx": i,
                    "outcome": "win" if i % 2 == 0 else "loss",
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross": Decimal(str(100 * (exit_price - entry_price))),
                    "net": Decimal(str(100 * (exit_price - entry_price))),
                    "now": now,
                },
            )

        db.commit()
        yield study_run_id
    finally:
        db.execute(
            text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"),
            {"run_id": study_run_id},
        )
        db.execute(
            text("DELETE FROM strategy_study_runs WHERE id = :id"),
            {"id": study_run_id},
        )
        db.commit()
        db.close()


class TestRealConcurrency:
    """Real concurrency test with barrier synchronization."""

    def test_barrier_synchronized_concurrent_replay(self, test_user_id, seed_concurrent_trades):
        """Two threads with barrier synchronization compete for same run.

        Verifies:
        - Exactly one succeeds, one is rejected
        - Only one set of orders/fills/positions in DB
        - Rejected thread doesn't change state
        - No uncaught thread exceptions
        """
        study_run_id = seed_concurrent_trades
        initial_capital = Decimal("100000.00")

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

        # Barrier to synchronize thread start
        barrier = threading.Barrier(2)
        
        # Track results and exceptions
        results = {"success": 0, "rejected": 0}
        exceptions = []
        lock = threading.Lock()

        def attempt_replay(thread_id):
            try:
                # Wait for both threads to be ready
                barrier.wait(timeout=5)
                
                # Both threads start simultaneously
                thread_engine = PaperTradingEngine(database_url=str(db_engine.url))
                thread_engine.execute_historical_replay(
                    run_id=run_id,
                    user_id=test_user_id,
                    days_back=30,
                    position_size_pct=0.10,
                )
                
                with lock:
                    results["success"] += 1
                    
            except ValueError as e:
                # Expected rejection
                with lock:
                    results["rejected"] += 1
            except Exception as e:
                # Unexpected exception
                with lock:
                    exceptions.append((thread_id, str(e)))

        # Launch two threads
        thread1 = threading.Thread(target=attempt_replay, args=(1,))
        thread2 = threading.Thread(target=attempt_replay, args=(2,))

        thread1.start()
        thread2.start()

        thread1.join(timeout=30)
        thread2.join(timeout=30)

        # Verify no uncaught exceptions
        assert len(exceptions) == 0, f"Unexpected exceptions: {exceptions}"

        # Verify exactly one succeeded, one rejected
        assert results["success"] == 1, f"Expected 1 success, got {results['success']}"
        assert results["rejected"] == 1, f"Expected 1 rejection, got {results['rejected']}"

        # Verify DB state: only one set of fills/positions
        db = SessionLocal()
        try:
            # Check run status is 'completed'
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()
            assert run.status == "completed", f"Run status should be 'completed', got {run.status}"

            # Count fills (should be from one replay only)
            fill_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM paper_fills f
                    JOIN paper_orders o ON f.paper_order_id = o.id
                    WHERE o.portfolio_id = :portfolio_id
                """),
                {"portfolio_id": portfolio_id},
            ).scalar()
            
            # With 5 trades, we expect some fills (entry + exit for each executed trade)
            # Some trades may be blocked by exposure limits, so fill_count < 10 is OK
            # The key is: if both threads had succeeded, we'd have roughly 2x the fills
            assert fill_count > 0, "Should have some fills from one replay"
            assert fill_count <= 10, f"Should have at most 10 fills (one replay), got {fill_count}"

            # Count positions (should be from one replay only)
            position_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM paper_positions
                    WHERE portfolio_id = :portfolio_id
                """),
                {"portfolio_id": portfolio_id},
            ).scalar()
            assert position_count > 0, "Should have some positions"
            assert position_count <= 5, f"Should have at most 5 positions (one replay), got {position_count}"

            # All positions should be closed
            open_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM paper_positions
                    WHERE portfolio_id = :portfolio_id AND status = 'open'
                """),
                {"portfolio_id": portfolio_id},
            ).scalar()
            assert open_count == 0, f"Expected 0 open positions, got {open_count}"

        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
