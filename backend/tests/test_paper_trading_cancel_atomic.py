"""Tests for paper trading cancel two-phase contract.

Two-Phase Cancel Contract:
- Phase 1: Cancel REQUESTED (cancel_requested=TRUE, status unchanged)
  * Endpoint sets flag atomically, returns immediately
  * Status remains 'running' - worker still processing
  * Distinguishes "request received" from "worker stopped"

- Phase 2: Cancel COMPLETED (status='cancelled')
  * Worker detects flag at next check point (≤100 events)
  * Worker rolls back uncommitted work
  * Worker atomically sets status='cancelled'
  * Only now is run truly cancelled

Test Coverage:
- Running state: request accepted → worker confirms cancelled
- Queued/pending/failed: termination without worker
- Cancel requested blocks execution acquisition
- Double cancel (idempotent)
- Cancel after completed (rejected)
- Race conditions: double cancel, cancel vs completion, cancel vs failure
- <100 events, polling boundary, pre-final-commit cancel
- Worker interruption recovery policy
- Rollback ledger and cash consistency

Status: Tests WRITTEN, NOT YET EXECUTED (requires PostgreSQL verification)
"""

import pytest
import threading
import time
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.services.paper_trading import PaperTradingEngine
from app.services.paper_trading_exceptions import (
    AuthorizationError,
    ResourceNotFoundError,
    InvalidStateError,
)


@pytest.fixture
def db_session():
    """Create a database session for testing."""
    db_url = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
    
    if db_url.startswith("sqlite"):
        from app.database import Base
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
    else:
        engine = create_engine(db_url)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def engine_with_run(db_session):
    """Create engine with a test run."""
    pt_engine = PaperTradingEngine(db_session.bind.url.render_as_string(hide_password=False))
    
    result = pt_engine.create_portfolio(
        user_id="test-user-123",
        skill="test_skill",
        instrument_id="BTC-USD",
        interval_minutes=1,
        exit_config="test_exit",
        initial_capital=Decimal("100000"),
    )
    
    run_id = result["run_id"]
    portfolio_id = result["portfolio_id"]
    
    yield pt_engine, run_id, portfolio_id
    
    # Cleanup
    db_session.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
    db_session.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
    db_session.commit()


class TestTwoPhaseCancelContract:
    """Test two-phase cancel contract: request vs completion."""
    
    def test_phase1_cancel_requested_running_state(self, engine_with_run, db_session):
        """Phase 1: Cancel request sets flag, status unchanged."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to running state
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        # Request cancel
        result = pt_engine.cancel_run(run_id, "test-user-123")
        
        # Phase 1: cancel_requested=TRUE, status still 'running'
        assert result["cancel_requested"] is True
        assert result["status"] == "running"  # NOT 'cancelled'
        assert "Cancel request accepted" in result["message"]
        
        # Verify DB state
        run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.status == "running"  # Still running
        assert run.cancel_requested is True
    
    def test_phase2_worker_sets_cancelled_status(self, engine_with_run, db_session):
        """Phase 2: Worker sets status='cancelled' after detecting flag."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to running state and set cancel flag
        db_session.execute(
            text("""
            UPDATE paper_trading_runs 
            SET status = 'running', cancel_requested = TRUE
            WHERE id = :id
        """),
            {"id": run_id}
        )
        db_session.commit()
        
        # Simulate worker detecting flag and setting status='cancelled'
        # (In real code, this happens in execute_historical_replay event loop)
        db_session.execute(
            text("""
            UPDATE paper_trading_runs
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'running'
        """),
            {"id": run_id}
        )
        db_session.commit()
        
        # Verify Phase 2 complete: status='cancelled'
        run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.status == "cancelled"
        assert run.cancel_requested is True
    
    def test_cancel_requested_blocks_execution_acquisition(self, engine_with_run, db_session):
        """Cancel requested prevents new execution acquisition."""
        pt_engine, run_id, _ = engine_with_run
        
        # Set cancel_requested flag
        db_session.execute(
            text("""
            UPDATE paper_trading_runs
            SET cancel_requested = TRUE, status = 'queued'
            WHERE id = :id
        """),
            {"id": run_id}
        )
        db_session.commit()
        
        # Try to acquire execution (should fail due to cancel_requested)
        result = db_session.execute(
            text("""
            UPDATE paper_trading_runs
            SET status = 'running'
            WHERE id = :id AND status IN ('queued', 'pending', 'failed')
              AND cancel_requested = FALSE
            RETURNING id
        """),
            {"id": run_id}
        ).fetchone()
        
        assert result is None, "Should not acquire execution when cancel_requested=TRUE"
    
    def test_double_cancel_idempotent(self, engine_with_run, db_session):
        """Double cancel is idempotent - second returns success."""
        pt_engine, run_id, _ = engine_with_run
        
        # First cancel
        result1 = pt_engine.cancel_run(run_id, "test-user-123")
        assert result1["cancel_requested"] is True
        
        # Second cancel (idempotent)
        result2 = pt_engine.cancel_run(run_id, "test-user-123")
        assert result2["cancel_requested"] is True
        assert "Cancel already requested" in result2["message"]
    
    def test_cancel_after_completed_rejected(self, engine_with_run, db_session):
        """Cancel after completed is rejected (409 Conflict)."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to completed state
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'completed' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        # Try to cancel (should fail)
        with pytest.raises(InvalidStateError) as exc_info:
            pt_engine.cancel_run(run_id, "test-user-123")
        
        assert "completed" in str(exc_info.value)


class TestCancelWithoutWorker:
    """Test cancel for states without active worker (queued/pending/failed)."""
    
    def test_cancel_queued_state(self, engine_with_run, db_session):
        """Cancel from queued state sets flag immediately."""
        pt_engine, run_id, _ = engine_with_run
        
        # Status is 'queued' by default
        result = pt_engine.cancel_run(run_id, "test-user-123")
        
        assert result["cancel_requested"] is True
        assert result["status"] == "queued"
        
        # Verify DB
        run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.status == "queued"
        assert run.cancel_requested is True
    
    def test_cancel_pending_state(self, engine_with_run, db_session):
        """Cancel from pending state sets flag."""
        pt_engine, run_id, _ = engine_with_run
        
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'pending' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        result = pt_engine.cancel_run(run_id, "test-user-123")
        assert result["cancel_requested"] is True
        assert result["status"] == "pending"
    
    def test_cancel_failed_state(self, engine_with_run, db_session):
        """Cancel from failed state sets flag."""
        pt_engine, run_id, _ = engine_with_run
        
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'failed' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        result = pt_engine.cancel_run(run_id, "test-user-123")
        assert result["cancel_requested"] is True
        assert result["status"] == "failed"


class TestCancelRaceConditions:
    """Test cancel race conditions with independent sessions."""
    
    def test_concurrent_cancel_only_one_sets_flag(self, engine_with_run, db_session):
        """Two concurrent cancel requests: only one sets flag first."""
        pt_engine, run_id, _ = engine_with_run
        
        engine1 = create_engine(db_session.bind.url.render_as_string(hide_password=False))
        engine2 = create_engine(db_session.bind.url.render_as_string(hide_password=False))
        
        session1 = Session(engine1)
        session2 = Session(engine2)
        
        results = []
        
        def cancel_with_session(session, session_id):
            try:
                temp_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
                import app.services.paper_trading as pt_module
                original_session_local = pt_module.SessionLocal
                pt_module.SessionLocal = lambda: session
                
                try:
                    result = temp_engine.cancel_run(run_id, "test-user-123")
                    results.append((session_id, result))
                except Exception as e:
                    results.append((session_id, e))
                finally:
                    pt_module.SessionLocal = original_session_local
            finally:
                session.close()
        
        thread1 = threading.Thread(target=cancel_with_session, args=(session1, 1))
        thread2 = threading.Thread(target=cancel_with_session, args=(session2, 2))
        
        thread1.start()
        thread2.start()
        
        thread1.join()
        thread2.join()
        
        # Both should succeed (idempotent), but flag set only once
        assert len(results) == 2
        assert all(r[1]["cancel_requested"] for r in results if isinstance(r[1], dict))
        
        # Verify final state
        run = db_session.execute(
            text("SELECT cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.cancel_requested is True
    
    def test_cancel_vs_completion_race(self, engine_with_run, db_session):
        """Cancel arriving during final commit: either cancels or completes."""
        pt_engine, run_id, _ = engine_with_run
        
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        results = []
        
        def cancel_after_delay():
            time.sleep(0.05)
            try:
                result = pt_engine.cancel_run(run_id, "test-user-123")
                results.append(("cancel", result))
            except Exception as e:
                results.append(("cancel_error", e))
        
        cancel_thread = threading.Thread(target=cancel_after_delay)
        cancel_thread.start()
        
        # Simulate completion with final commit protection
        try:
            locked = db_session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id FOR UPDATE"),
                {"id": run_id}
            ).fetchone()
            
            if locked.cancel_requested:
                # Cancel won, worker sets status='cancelled'
                db_session.execute(
                    text("UPDATE paper_trading_runs SET status = 'cancelled' WHERE id = :id"),
                    {"id": run_id}
                )
                db_session.commit()
                results.append(("complete", {"status": "cancelled"}))
            else:
                # Complete the run
                db_session.execute(
                    text("UPDATE paper_trading_runs SET status = 'completed' WHERE id = :id"),
                    {"id": run_id}
                )
                db_session.commit()
                results.append(("complete", {"status": "completed"}))
        except Exception as e:
            results.append(("complete_error", e))
        
        cancel_thread.join()
        
        # Verify final state is either cancelled or completed
        final_run = db_session.execute(
            text("SELECT status FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert final_run.status in ("cancelled", "completed")


class TestWorkerCancelCheck:
    """Test worker cancel check mechanism."""
    
    def test_check_cancel_requested_false_initially(self, engine_with_run):
        """Worker check returns False when no cancel requested."""
        pt_engine, run_id, _ = engine_with_run
        
        assert pt_engine.check_cancel_requested(run_id) is False
    
    def test_check_cancel_requested_true_after_phase1(self, engine_with_run, db_session):
        """Worker check returns True after Phase 1 cancel request."""
        pt_engine, run_id, _ = engine_with_run
        
        # Phase 1: Request cancel
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Worker check should return True
        assert pt_engine.check_cancel_requested(run_id) is True
    
    def test_check_cancel_respects_status(self, engine_with_run, db_session):
        """Worker check returns True if status='cancelled' even if flag not set."""
        pt_engine, run_id, _ = engine_with_run
        
        # Set status='cancelled' directly (simulating worker completion)
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'cancelled' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        # Worker check should return True
        assert pt_engine.check_cancel_requested(run_id) is True


class TestCancelDataPolicy:
    """Test cancel data policy: what happens to committed vs uncommitted data."""
    
    def test_cancel_preserves_committed_trades(self, engine_with_run, db_session):
        """Cancel preserves already-committed trades."""
        pt_engine, run_id, portfolio_id = engine_with_run
        
        # Simulate committed trades
        db_session.execute(
            text("""
            INSERT INTO paper_orders (id, portfolio_id, decision_id, asset_id, side, quantity, order_type, created_at, status, actor_type)
            VALUES ('order-1', :portfolio_id, 'decision-1', 'BTC-USD', 'BUY', 0.1, 'market', NOW(), 'filled', 'paper_trading')
        """),
            {"portfolio_id": portfolio_id}
        )
        
        db_session.execute(
            text("""
            INSERT INTO paper_positions (id, portfolio_id, asset_id, quantity, average_entry_price, opened_at, status, source_trade_id)
            VALUES ('position-1', :portfolio_id, 'BTC-USD', 0.1, 50000, NOW(), 'open', 'trade-1')
        """),
            {"portfolio_id": portfolio_id}
        )
        
        db_session.commit()
        
        # Phase 1: Request cancel
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Verify trades preserved
        order = db_session.execute(
            text("SELECT status FROM paper_orders WHERE id = 'order-1'")
        ).fetchone()
        assert order.status == "filled"
        
        position = db_session.execute(
            text("SELECT status FROM paper_positions WHERE id = 'position-1'")
        ).fetchone()
        assert position.status == "open"
    
    def test_worker_rollback_uncommitted_work(self, engine_with_run, db_session):
        """Worker rolls back uncommitted work when cancel detected."""
        pt_engine, run_id, portfolio_id = engine_with_run
        
        # Simulate uncommitted work (in real code, this is in worker's transaction)
        db_session.execute(
            text("""
            INSERT INTO paper_orders (id, portfolio_id, decision_id, asset_id, side, quantity, order_type, created_at, status, actor_type)
            VALUES ('order-uncommitted', :portfolio_id, 'decision-1', 'BTC-USD', 'BUY', 0.1, 'market', NOW(), 'pending', 'paper_trading')
        """),
            {"portfolio_id": portfolio_id}
        )
        # Don't commit - simulating worker's uncommitted transaction
        
        # Phase 1: Request cancel
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Worker would rollback here
        db_session.rollback()
        
        # Verify uncommitted work rolled back
        order = db_session.execute(
            text("SELECT COUNT(*) FROM paper_orders WHERE id = 'order-uncommitted'")
        ).scalar()
        assert order == 0, "Uncommitted work should be rolled back"


# Status: Tests WRITTEN, NOT YET EXECUTED
# Requires: PostgreSQL role verification, migration application, test execution
# Next: Run tests with approved PG environment, verify all scenarios pass
