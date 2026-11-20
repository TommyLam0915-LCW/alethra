"""Test replay status management and concurrency protection.

Verifies:
1. New run (queued) can be replayed successfully
2. Completed run replay is rejected with no status change
3. Running run concurrent request is rejected with no status change
4. Atomic conditional UPDATE prevents race conditions
5. Only task with execution rights can modify status
6. A stop request filed while queued blocks acquisition

Rejections arrive as ``InvalidStateError``, a ``PaperTradingError`` — not a
``ValueError``.  The API layer turns it into 409 (app/api/paper_trading.py:198,
:245, :295, :337), and these tests assert the service-level type because that
is what a caller of the service sees.  The assertions previously caught
``ValueError`` with messages ("already completed", "cannot be executed") that
the service stopped emitting when the exception hierarchy was introduced; a
rejection then simply escaped as an unexpected error, and in the concurrency
node it escaped the worker thread, so the rejection was counted as zero.
"""

import pytest
import threading
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal, engine as db_engine
from app.services.paper_trading import PaperTradingEngine
from app.services.paper_trading_exceptions import InvalidStateError


def _run_snapshot(run_id: str, portfolio_id: str) -> dict:
    """Everything a rejected replay could have changed, read back in one go."""
    db = SessionLocal()
    try:
        run = db.execute(
            text("""
                SELECT status, total_pnl, total_trades, final_capital, cancel_requested
                FROM paper_trading_runs WHERE id = :id
            """),
            {"id": run_id},
        ).fetchone()
        counts = {
            "run": tuple(run),
            "orders": db.execute(
                text("SELECT COUNT(*) FROM paper_orders WHERE portfolio_id = :pid"),
                {"pid": portfolio_id},
            ).scalar(),
            "positions": db.execute(
                text("SELECT COUNT(*) FROM paper_positions WHERE portfolio_id = :pid"),
                {"pid": portfolio_id},
            ).scalar(),
            "decisions": db.execute(
                text("SELECT COUNT(*) FROM decision_records WHERE paper_portfolio_id = :pid"),
                {"pid": portfolio_id},
            ).scalar(),
            "daily_metrics": db.execute(
                text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :rid"),
                {"rid": run_id},
            ).scalar(),
        }
        db.rollback()
        return counts
    finally:
        db.close()


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

        after_first = _run_snapshot(run_id, portfolio_id)

        # Second replay should be rejected
        with pytest.raises(InvalidStateError, match="is in state 'completed'"):
            trading_engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # The rejection must not have touched the run or the ledger.
        assert _run_snapshot(run_id, portfolio_id) == after_first, (
            "rejected replay changed run status, totals or ledger rows"
        )

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

        # 'running' is not in the acquisition allow-list, so the conditional UPDATE
        # claims nothing and the replay is refused before it can write a row.
        before = _run_snapshot(run_id, portfolio_id)

        with pytest.raises(InvalidStateError, match=r"is in state 'running'"):
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # Verify status is still 'running' (not changed to 'failed') and no ledger rows appeared
        assert _run_snapshot(run_id, portfolio_id) == before, (
            "rejected replay changed run status, totals or ledger rows"
        )

    def test_concurrent_replay_only_one_succeeds(self, test_user_id, test_portfolio_and_run):
        """Two replays released together: exactly one acquires execution rights.

        A barrier replaces the old "start, sleep 10ms, start" stagger, which let
        the first thread finish the whole replay before the second had read the
        run.  Serialized threads prove nothing about the conditional UPDATE: the
        loser is refused by the completed-state short-circuit regardless of the
        acquisition predicate, so the node passed even with that predicate
        deleted.  Both threads now clear the barrier before touching the DB, so
        the loser must be stopped by ``status IN (...)`` in the UPDATE itself.
        """
        portfolio_id, run_id = test_portfolio_and_run
        trading_engine = PaperTradingEngine(database_url=str(db_engine.url))

        results = {"success": 0, "rejected": 0, "other": []}
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt_replay():
            try:
                barrier.wait(timeout=10)
                trading_engine.execute_historical_replay(
                    run_id=run_id,
                    user_id=test_user_id,
                    days_back=30,
                )
                with lock:
                    results["success"] += 1
            except InvalidStateError:
                with lock:
                    results["rejected"] += 1
            except threading.BrokenBarrierError:
                with lock:
                    results["other"].append("BrokenBarrierError: peer never reached the gate")
            except Exception as exc:  # noqa: BLE001 - recorded, never counted as a rejection
                with lock:
                    results["other"].append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=attempt_replay) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results["other"] == [], f"Unexpected replay failures: {results['other']}"
        assert results["success"] == 1, f"Expected 1 success, got {results['success']}"
        assert results["rejected"] == 1, f"Expected 1 rejection, got {results['rejected']}"

        # Verify final status is 'completed' (not 'failed' or 'running')
        assert _run_snapshot(run_id, portfolio_id)["run"][0] == "completed"

    def test_cancel_requested_blocks_replay_acquisition(self, test_user_id, test_portfolio_and_run):
        """A stop request already filed must bar acquisition — via the service.

        The node that used to claim this contract (test_paper_trading_cancel_atomic.py:538)
        re-types the acquisition UPDATE instead of calling the service, so it stays
        green whatever the product does.  Here the refusal has to come out of
        execute_historical_replay itself.
        """
        portfolio_id, run_id = test_portfolio_and_run

        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET cancel_requested = TRUE WHERE id = :id"),
                {"id": run_id},
            )
            db.commit()
        finally:
            db.close()

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        before = _run_snapshot(run_id, portfolio_id)
        # Raw text() SELECT gives SQLite's 1 where psycopg2 gives True, so compare truthiness.
        assert before["run"][0] == "queued" and bool(before["run"][4])

        with pytest.raises(InvalidStateError, match=r"is in state 'queued'"):
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        after = _run_snapshot(run_id, portfolio_id)
        assert after == before, "cancelled-while-queued replay must not run, write or restate"

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
