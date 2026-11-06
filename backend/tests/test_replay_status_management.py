"""Test replay status management and concurrency protection.

Verifies:
1. New run (queued) can be replayed successfully
2. Completed run replay is rejected with no status change
3. Running run concurrent request is rejected with no status change
4. Atomic conditional UPDATE prevents race conditions
5. Only task with execution rights can modify status
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
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 0)
            """),
            {"id": user_id, "email": f"test_{user_id[:8]}@test.local", "password": "test_hash_placeholder"}
        )
        db.commit()
        yield user_id
    finally:
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
        db.close()


@pytest.fixture
def test_portfolio_and_run(test_user_id):
    """Create test portfolio and run, return (portfolio_id, run_id)."""
    db = SessionLocal()
    try:
        portfolio_id = str(uuid4())
        run_id = str(uuid4())

        db.execute(
            text("""
            INSERT INTO paper_portfolios (
                id, owner_id, currency, initial_virtual_cash, current_virtual_cash,
                status, created_at
            ) VALUES (
                :id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP
            )
        """),
            {"id": portfolio_id, "owner_id": test_user_id}
        )

        db.execute(
            text("""
            INSERT INTO paper_trading_runs (
                id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                start_date, status, initial_capital, total_pnl, total_trades
            ) VALUES (
                :id, :portfolio_id, 'range_fade', 'XBT/USD', 1, 'default',
                CURRENT_TIMESTAMP, 'queued', 100000.0, 0, 0
            )
        """),
            {"id": run_id, "portfolio_id": portfolio_id}
        )

        db.commit()
        yield portfolio_id, run_id
    finally:
        db.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
        db.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
        db.commit()
        db.close()


class TestReplayStatusManagement:
    """Test replay status transitions and concurrency protection."""

    def test_queued_run_can_be_replayed(self, test_user_id, test_portfolio_and_run):
        """Verify that a newly created run (queued) can be replayed successfully."""
        portfolio_id, run_id = test_portfolio_and_run
        trading_engine = PaperTradingEngine(database_url=str(db_engine.url))

        # Should succeed without raising
        result = trading_engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        assert result["status"] == "completed"
        assert result["run_id"] == run_id

        # Verify DB status is now 'completed'
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "completed"
        finally:
            db.close()

    def test_completed_run_rejected_no_side_effects(self, test_user_id, test_portfolio_and_run):
        """Verify that replaying a completed run is rejected with no status change."""
        portfolio_id, run_id = test_portfolio_and_run
        trading_engine = PaperTradingEngine(database_url=str(db_engine.url))

        # First replay succeeds
        trading_engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        # Second replay should be rejected
        with pytest.raises(ValueError, match="already completed"):
            trading_engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # Verify status is still 'completed' (not changed to 'failed')
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "completed"
        finally:
            db.close()

    def test_running_run_rejected_no_side_effects(self, test_user_id, test_portfolio_and_run):
        """Verify that concurrent replay of running run is rejected with no status change."""
        portfolio_id, run_id = test_portfolio_and_run

        # Manually set status to 'running' to simulate concurrent execution
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
                {"id": run_id}
            )
            db.commit()
        finally:
            db.close()

        engine = PaperTradingEngine(database_url=str(db_engine.url))

        # Should be rejected
        with pytest.raises(ValueError, match="cannot be executed"):
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # Verify status is still 'running' (not changed to 'failed')
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "running"
        finally:
            db.close()

    def test_concurrent_replay_only_one_succeeds(self, test_user_id, test_portfolio_and_run):
        """Verify that two concurrent replay attempts: only one acquires execution rights."""
        portfolio_id, run_id = test_portfolio_and_run
        trading_engine = PaperTradingEngine(database_url=str(db_engine.url))

        results = {"success": 0, "rejected": 0}
        lock = threading.Lock()

        def attempt_replay():
            try:
                trading_engine.execute_historical_replay(
                    run_id=run_id,
                    user_id=test_user_id,
                    days_back=30,
                )
                with lock:
                    results["success"] += 1
            except ValueError:
                with lock:
                    results["rejected"] += 1

        # Launch two concurrent threads
        thread1 = threading.Thread(target=attempt_replay)
        thread2 = threading.Thread(target=attempt_replay)

        thread1.start()
        time.sleep(0.01)  # Small delay to increase race probability
        thread2.start()

        thread1.join()
        thread2.join()

        # Exactly one should succeed, one should be rejected
        assert results["success"] == 1, f"Expected 1 success, got {results['success']}"
        assert results["rejected"] == 1, f"Expected 1 rejection, got {results['rejected']}"

        # Verify final status is 'completed' (not 'failed' or 'running')
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "completed"
        finally:
            db.close()

    def test_failed_run_can_be_retried(self, test_user_id, test_portfolio_and_run):
        """Verify that a failed run can be retried."""
        portfolio_id, run_id = test_portfolio_and_run

        # Manually set status to 'failed'
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET status = 'failed' WHERE id = :id"),
                {"id": run_id}
            )
            db.commit()
        finally:
            db.close()

        engine = PaperTradingEngine(database_url=str(db_engine.url))

        # Should succeed
        result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        assert result["status"] == "completed"

        # Verify DB status is now 'completed'
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "completed"
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
