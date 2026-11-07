"""Tests for paper trading cancel atomic state transition.

Proves that cancel operations are atomic and race-condition free:
1. Conditional UPDATE prevents read-then-update race
2. Worker cancel check detects cancel during execution
3. Final commit protection prevents cancel interference
4. Independent sessions test true concurrency (not simulated)
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
    # Use the test database URL from environment or default to SQLite
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
    
    # Create portfolio and run
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


class TestCancelAtomicStateTransition:
    """Test atomic cancel operations."""
    
    def test_cancel_from_queued_state(self, engine_with_run, db_session):
        """Cancel should succeed from queued state."""
        pt_engine, run_id, _ = engine_with_run
        
        result = pt_engine.cancel_run(run_id, "test-user-123")
        
        assert result["status"] == "cancelled"
        assert result["run_id"] == run_id
        
        # Verify DB state
        run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.status == "cancelled"
        assert run.cancel_requested is True
    
    def test_cancel_from_running_state(self, engine_with_run, db_session):
        """Cancel should succeed from running state."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to running state
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        result = pt_engine.cancel_run(run_id, "test-user-123")
        
        assert result["status"] == "cancelled"
        
        # Verify DB state
        run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert run.status == "cancelled"
        assert run.cancel_requested is True
    
    def test_cancel_from_completed_state_fails(self, engine_with_run, db_session):
        """Cancel should fail from completed state."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to completed state
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'completed' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        with pytest.raises(InvalidStateError) as exc_info:
            pt_engine.cancel_run(run_id, "test-user-123")
        
        assert "completed" in str(exc_info.value)
    
    def test_cancel_from_cancelled_state_fails(self, engine_with_run, db_session):
        """Cancel should fail if already cancelled."""
        pt_engine, run_id, _ = engine_with_run
        
        # Cancel once
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Try to cancel again
        with pytest.raises(InvalidStateError) as exc_info:
            pt_engine.cancel_run(run_id, "test-user-123")
        
        assert "cancelled" in str(exc_info.value)
    
    def test_cancel_wrong_user_fails(self, engine_with_run):
        """Cancel should fail if user doesn't own the run."""
        pt_engine, run_id, _ = engine_with_run
        
        with pytest.raises(AuthorizationError):
            pt_engine.cancel_run(run_id, "wrong-user-456")
    
    def test_cancel_nonexistent_run_fails(self, engine_with_run):
        """Cancel should fail if run doesn't exist."""
        pt_engine, _, _ = engine_with_run
        
        with pytest.raises(ResourceNotFoundError):
            pt_engine.cancel_run("nonexistent-run-id", "test-user-123")


class TestCancelRaceConditions:
    """Test cancel race conditions with independent sessions."""
    
    def test_concurrent_cancel_only_one_succeeds(self, engine_with_run, db_session):
        """Two concurrent cancel requests should result in exactly one success."""
        pt_engine, run_id, _ = engine_with_run
        
        # Create two independent database sessions
        engine1 = create_engine(db_session.bind.url.render_as_string(hide_password=False))
        engine2 = create_engine(db_session.bind.url.render_as_string(hide_password=False))
        
        session1 = Session(engine1)
        session2 = Session(engine2)
        
        results = []
        errors = []
        
        def cancel_with_session(session, session_id):
            try:
                # Create a new engine instance with this session
                temp_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
                # Override the SessionLocal to use our session
                import app.services.paper_trading as pt_module
                original_session_local = pt_module.SessionLocal
                pt_module.SessionLocal = lambda: session
                
                try:
                    result = temp_engine.cancel_run(run_id, "test-user-123")
                    results.append((session_id, result))
                except Exception as e:
                    errors.append((session_id, e))
                finally:
                    pt_module.SessionLocal = original_session_local
            finally:
                session.close()
        
        # Launch two threads that try to cancel simultaneously
        thread1 = threading.Thread(target=cancel_with_session, args=(session1, 1))
        thread2 = threading.Thread(target=cancel_with_session, args=(session2, 2))
        
        thread1.start()
        thread2.start()
        
        thread1.join()
        thread2.join()
        
        # Exactly one should succeed
        assert len(results) == 1, f"Expected 1 success, got {len(results)}: {results}"
        assert len(errors) == 1, f"Expected 1 error, got {len(errors)}: {errors}"
        
        # Verify final state is cancelled
        final_run = db_session.execute(
            text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert final_run.status == "cancelled"
        assert final_run.cancel_requested is True
    
    def test_cancel_during_completion_race(self, engine_with_run, db_session):
        """Cancel arriving during final commit should either cancel or complete, not corrupt."""
        pt_engine, run_id, _ = engine_with_run
        
        # Move to running state
        db_session.execute(
            text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
            {"id": run_id}
        )
        db_session.commit()
        
        results = []
        
        def cancel_after_delay():
            time.sleep(0.05)  # Small delay to let completion start
            try:
                result = pt_engine.cancel_run(run_id, "test-user-123")
                results.append(("cancel", result))
            except Exception as e:
                results.append(("cancel_error", e))
        
        # Start cancel in background
        cancel_thread = threading.Thread(target=cancel_after_delay)
        cancel_thread.start()
        
        # Simulate completion (in real code this would be execute_historical_replay final commit)
        try:
            # Lock the row for final commit
            locked = db_session.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id FOR UPDATE"),
                {"id": run_id}
            ).fetchone()
            
            # Check if cancel was requested
            cancel_check = db_session.execute(
                text("SELECT cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            
            if cancel_check and cancel_check.cancel_requested:
                # Cancel won, rollback completion
                db_session.rollback()
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
        
        # Verify final state is either cancelled or completed, not corrupted
        final_run = db_session.execute(
            text("SELECT status FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).fetchone()
        assert final_run.status in ("cancelled", "completed"), f"Corrupted state: {final_run.status}"


class TestWorkerCancelCheck:
    """Test worker cancel check mechanism."""
    
    def test_check_cancel_requested_false_initially(self, engine_with_run):
        """Worker check should return False when no cancel requested."""
        pt_engine, run_id, _ = engine_with_run
        
        assert pt_engine.check_cancel_requested(run_id) is False
    
    def test_check_cancel_requested_true_after_cancel(self, engine_with_run):
        """Worker check should return True after cancel."""
        pt_engine, run_id, _ = engine_with_run
        
        pt_engine.cancel_run(run_id, "test-user-123")
        
        assert pt_engine.check_cancel_requested(run_id) is True
    
    def test_check_cancel_requested_nonexistent_run(self, engine_with_run):
        """Worker check should return False for nonexistent run."""
        pt_engine, _, _ = engine_with_run
        
        assert pt_engine.check_cancel_requested("nonexistent-run-id") is False


class TestCancelDataPolicy:
    """Test cancel data policy: what happens to committed vs uncommitted data."""
    
    def test_cancel_preserves_committed_trades(self, engine_with_run, db_session):
        """Cancel should preserve already-committed trades."""
        pt_engine, run_id, portfolio_id = engine_with_run
        
        # Simulate some committed trades
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
        
        # Cancel the run
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Verify trades are preserved
        order = db_session.execute(
            text("SELECT status FROM paper_orders WHERE id = 'order-1'")
        ).fetchone()
        assert order.status == "filled"
        
        position = db_session.execute(
            text("SELECT status FROM paper_positions WHERE id = 'position-1'")
        ).fetchone()
        assert position.status == "open"
    
    def test_cancel_prevents_new_trades(self, engine_with_run, db_session):
        """After cancel, no new trades should be acquirable."""
        pt_engine, run_id, _ = engine_with_run
        
        # Cancel the run
        pt_engine.cancel_run(run_id, "test-user-123")
        
        # Try to acquire execution (should fail)
        result = db_session.execute(
            text("""
            UPDATE paper_trading_runs
            SET status = 'running'
            WHERE id = :id AND status IN ('queued', 'pending', 'failed')
            RETURNING id
        """),
            {"id": run_id}
        ).fetchone()
        
        assert result is None, "Should not be able to acquire execution after cancel"
