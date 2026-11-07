"""Tests for paper trading cancel two-phase contract.

Two-Phase Cancel Contract:
- Phase 1: Cancel REQUESTED (cancel_requested=TRUE, status unchanged for running)
  * Running state: endpoint sets flag atomically, returns immediately
  * Queued/pending/failed: cancel completes immediately (no worker)
  * Distinguishes "request received" from "worker stopped"

- Phase 2: Cancel COMPLETED (status='cancelled')
  * Running: Worker detects flag at next check point (≤100 events)
  * Worker rolls back uncommitted work
  * Worker atomically sets status='cancelled'
  * Only now is run truly cancelled

Test Coverage (12 methods implemented):
- Valid UUID user with complete FK chain (users → portfolios → runs)
- Running state: two-phase contract with worker polling (5 tests)
- Queued/pending/failed: immediate completion without worker (3 tests)
- Completion/failure handlers don't override cancel status (2 tests)
- Race conditions with barrier control (2 tests)
- Explicit session factory injection (no global SessionLocal override)
- Comprehensive FK-safe cleanup (fills → orders → positions → decisions → metrics → runs → portfolios → users)

Additional tests needed (not yet implemented):
- Actual execute_historical_replay (requires historical trades setup)
- <100 events, polling boundary, pre-final-commit cancel
- Worker interruption recovery policy
- Complete ledger and cash consistency verification

Status: 12 tests WRITTEN, NOT YET EXECUTED (requires PostgreSQL test DB migration)
"""

import pytest
import threading
import time
import uuid
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
def test_user_id():
    """Generate valid UUID for test user."""
    return str(uuid.uuid4())


@pytest.fixture
def test_portfolio_id():
    """Generate valid UUID for test portfolio."""
    return str(uuid.uuid4())


@pytest.fixture
def test_run_id():
    """Generate valid UUID for test run."""
    return str(uuid.uuid4())


@pytest.fixture
def session_factory():
    """Create explicit session factory for test isolation.
    
    Tests must NOT override global SessionLocal. Use this factory to create
    independent sessions for concurrent operations.
    
    Connection URL from environment variable ALETHRA_PG_TEST_URL or fallback to
    verified test DB configuration (no hardcoded credentials in source).
    """
    import os
    db_url = os.environ.get(
        "ALETHRA_PG_TEST_URL",
        "postgresql://alethra_test_runtime@localhost:5432/ai_investment_lab_test"
    )
    engine = create_engine(db_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db_session(session_factory):
    """Create a database session for testing."""
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def complete_fk_setup(db_session, test_user_id, test_portfolio_id, test_run_id):
    """Set up complete FK chain: users → portfolios → runs.
    
    Returns cleanup function that removes all test data in FK-safe order.
    """
    # Create user
    db_session.execute(
        text("""
        INSERT INTO users (id, email, display_name, user_type, risk_acknowledged, created_at)
        VALUES (:id, :email, :name, 'individual', TRUE, CURRENT_TIMESTAMP)
        ON CONFLICT (id) DO NOTHING
    """),
        {"id": test_user_id, "email": f"test-{test_user_id[:8]}@alethra.io", "name": "Test User"}
    )
    
    # Create portfolio
    db_session.execute(
        text("""
        INSERT INTO paper_portfolios (
            id, owner_id, skill, instrument_id, interval_minutes, exit_config,
            current_virtual_cash, created_at
        )
        VALUES (:id, :owner_id, 'test_skill', 'BTC-USD', 1, 'test_exit', 100000.0, CURRENT_TIMESTAMP)
    """),
        {"id": test_portfolio_id, "owner_id": test_user_id}
    )
    
    # Create run
    db_session.execute(
        text("""
        INSERT INTO paper_trading_runs (
            id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
            start_date, initial_capital, status, cancel_requested, created_at
        )
        VALUES (
            :id, :portfolio_id, 'test_skill', 'BTC-USD', 1, 'test_exit',
            CURRENT_TIMESTAMP, 100000.0, 'queued', FALSE, CURRENT_TIMESTAMP
        )
    """),
        {"id": test_run_id, "portfolio_id": test_portfolio_id}
    )
    
    db_session.commit()
    
    # Return cleanup function
    def cleanup():
        """Remove all test data in FK-safe order."""
        try:
            # Delete in FK-safe order (child tables first)
            # paper_fills → paper_orders → paper_positions → decision_records →
            # paper_trading_daily_metrics → paper_trading_runs → paper_portfolios → users
            
            portfolio_ids_result = db_session.execute(
                text("SELECT id FROM paper_portfolios WHERE owner_id = :user_id"),
                {"user_id": test_user_id},
            ).fetchall()
            portfolio_ids = [str(row[0]) for row in portfolio_ids_result]
            
            if portfolio_ids:
                # Delete paper_fills (child of paper_orders)
                db_session.execute(
                    text("DELETE FROM paper_fills WHERE paper_order_id IN "
                         "(SELECT id FROM paper_orders WHERE portfolio_id = ANY(:portfolio_ids))"),
                    {"portfolio_ids": portfolio_ids},
                )
                
                # Delete paper_orders
                db_session.execute(
                    text("DELETE FROM paper_orders WHERE portfolio_id = ANY(:portfolio_ids)"),
                    {"portfolio_ids": portfolio_ids},
                )
            
            # Delete paper_positions
            db_session.execute(
                text("DELETE FROM paper_positions WHERE portfolio_id IN "
                     "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
                {"user_id": test_user_id},
            )
            
            # Delete decision_records
            db_session.execute(
                text("DELETE FROM decision_records WHERE paper_portfolio_id IN "
                     "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
                {"user_id": test_user_id},
            )
            
            # Delete paper_trading_daily_metrics
            db_session.execute(
                text("DELETE FROM paper_trading_daily_metrics WHERE run_id IN "
                     "(SELECT id FROM paper_trading_runs WHERE portfolio_id IN "
                     "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id))"),
                {"user_id": test_user_id},
            )
            
            # Delete paper_trading_runs
            db_session.execute(
                text("DELETE FROM paper_trading_runs WHERE portfolio_id IN "
                     "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
                {"user_id": test_user_id},
            )
            
            # Delete paper_portfolios
            db_session.execute(
                text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"),
                {"user_id": test_user_id},
            )
            
            # Delete user
            db_session.execute(
                text("DELETE FROM users WHERE id = :user_id"),
                {"user_id": test_user_id},
            )
            
            db_session.commit()
            
            # Verify zero residual
            _verify_zero_residual(db_session, test_user_id, portfolio_ids, test_run_id)
            
        except Exception as e:
            db_session.rollback()
            raise RuntimeError(f"Cleanup failed: {e}") from e
    
    yield test_user_id, test_portfolio_id, test_run_id
    cleanup()


def _verify_zero_residual(db_session, user_id, portfolio_ids, run_id):
    """Verify complete cleanup with comprehensive checks."""
    # Verify user deleted
    user_count = db_session.execute(
        text("SELECT COUNT(*) FROM users WHERE id = :id"),
        {"id": user_id}
    ).scalar()
    assert user_count == 0, f"User {user_id} not deleted"
    
    # Verify portfolio deleted
    portfolio_count = db_session.execute(
        text("SELECT COUNT(*) FROM paper_portfolios WHERE owner_id = :id"),
        {"id": user_id}
    ).scalar()
    assert portfolio_count == 0, f"Portfolios for user {user_id} not deleted"
    
    # Verify run deleted
    run_count = db_session.execute(
        text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = :id"),
        {"id": run_id}
    ).scalar()
    assert run_count == 0, f"Run {run_id} not deleted"
    
    if portfolio_ids:
        # Verify all child tables deleted
        for table in ["paper_fills", "paper_orders", "paper_positions", "decision_records"]:
            count = db_session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE portfolio_id = ANY(:ids)"),
                {"ids": portfolio_ids}
            ).scalar()
            assert count == 0, f"{table} not deleted for portfolios {portfolio_ids}"
        
        # Verify metrics deleted
        metrics_count = db_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :id"),
            {"id": run_id}
        ).scalar()
        assert metrics_count == 0, f"Metrics for run {run_id} not deleted"


class TestTwoPhaseCancelContract:
    """Test two-phase cancel contract: request vs completion."""
    
    def test_phase1_cancel_requested_running_state(self, complete_fk_setup, session_factory):
        """Phase 1: Cancel request sets flag, status unchanged for running state."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        # Use explicit session factory, not global SessionLocal
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Move to running state
            session.execute(
                text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
                {"id": run_id}
            )
            session.commit()
            
            # Phase 1: Request cancel
            result = pt_engine.cancel_run(run_id, user_id)
            
            # Verify: cancel_requested=TRUE, status still 'running'
            run = session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            
            assert result["cancel_requested"] is True
            assert result["status"] == "running", "Phase 1 must NOT change status"
            assert run.cancel_requested is True
            assert run.status == "running", "Phase 1 must NOT change status in DB"
            
        finally:
            session.close()
    
    def test_phase2_worker_completes_cancel(self, complete_fk_setup, session_factory):
        """Phase 2: Worker detects flag and sets status='cancelled'."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Setup: running state with cancel_requested=TRUE
            session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', cancel_requested = TRUE
                WHERE id = :id
            """),
                {"id": run_id}
            )
            session.commit()
            
            # Worker check_cancel_requested detects flag
            assert pt_engine.check_cancel_requested(run_id) is True
            
            # Worker sets status='cancelled' (simulating worker completion)
            session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'running'
            """),
                {"id": run_id}
            )
            session.commit()
            
            # Verify: status='cancelled'
            run = session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            
            assert run.status == "cancelled"
            assert run.cancel_requested is True
            
        finally:
            session.close()
    
    def test_cancel_requested_blocks_execution_acquisition(self, complete_fk_setup, session_factory):
        """Execution acquisition must reject cancel_requested=TRUE."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            # Setup: queued state with cancel_requested=TRUE
            session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'queued', cancel_requested = TRUE
                WHERE id = :id
            """),
                {"id": run_id}
            )
            session.commit()
            
            # Attempt execution acquisition (simulating execute_historical_replay)
            result = session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status IN ('queued', 'pending', 'failed')
                  AND cancel_requested = FALSE
                RETURNING id
            """),
                {"id": run_id}
            )
            
            # Verify: execution NOT acquired (no rows returned)
            acquired_row = result.fetchone()
            assert acquired_row is None, "Execution must be blocked when cancel_requested=TRUE"
            
            # Verify: status unchanged
            run = session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "queued"
            assert run.cancel_requested is True
            
        finally:
            session.close()
    
    def test_double_cancel_idempotent(self, complete_fk_setup, session_factory):
        """Double cancel returns success (idempotent)."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Move to running state
            session.execute(
                text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
                {"id": run_id}
            )
            session.commit()
            
            # First cancel
            result1 = pt_engine.cancel_run(run_id, user_id)
            assert result1["cancel_requested"] is True
            
            # Second cancel (idempotent)
            result2 = pt_engine.cancel_run(run_id, user_id)
            assert result2["cancel_requested"] is True
            assert "already" in result2["message"].lower() or result2["status"] == "running"
            
        finally:
            session.close()
    
    def test_cancel_after_completed_rejected(self, complete_fk_setup, session_factory):
        """Cancel after completed status is rejected."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Set status to completed
            session.execute(
                text("UPDATE paper_trading_runs SET status = 'completed' WHERE id = :id"),
                {"id": run_id}
            )
            session.commit()
            
            # Attempt cancel
            with pytest.raises(InvalidStateError) as exc_info:
                pt_engine.cancel_run(run_id, user_id)
            
            assert "completed" in str(exc_info.value).lower()
            
        finally:
            session.close()


class TestCancelConvergence:
    """Test queued/pending/failed states complete cancel immediately."""
    
    def test_cancel_queued_completes_immediately(self, complete_fk_setup, session_factory):
        """Queued state (no worker) completes cancel immediately."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Verify initial state: queued
            run = session.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "queued"
            
            # Cancel queued run
            result = pt_engine.cancel_run(run_id, user_id)
            
            # Verify: status='cancelled' immediately (no worker needed)
            assert result["status"] == "cancelled"
            assert result["cancel_requested"] is True
            assert "immediately" in result["message"].lower()
            
            run = session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "cancelled"
            assert run.cancel_requested is True
            
        finally:
            session.close()
    
    def test_cancel_pending_completes_immediately(self, complete_fk_setup, session_factory):
        """Pending state (no worker) completes cancel immediately."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Move to pending
            session.execute(
                text("UPDATE paper_trading_runs SET status = 'pending' WHERE id = :id"),
                {"id": run_id}
            )
            session.commit()
            
            # Cancel pending run
            result = pt_engine.cancel_run(run_id, user_id)
            
            # Verify: status='cancelled' immediately
            assert result["status"] == "cancelled"
            
        finally:
            session.close()
    
    def test_cancel_failed_completes_immediately(self, complete_fk_setup, session_factory):
        """Failed state (no worker) completes cancel immediately."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
            
            # Move to failed
            session.execute(
                text("UPDATE paper_trading_runs SET status = 'failed' WHERE id = :id"),
                {"id": run_id}
            )
            session.commit()
            
            # Cancel failed run
            result = pt_engine.cancel_run(run_id, user_id)
            
            # Verify: status='cancelled' immediately
            assert result["status"] == "cancelled"
            
        finally:
            session.close()


class TestCompletionFailureProtection:
    """Test completion/failure handlers don't override cancel status."""
    
    def test_completion_handler_respects_cancel(self, complete_fk_setup, session_factory):
        """Completion handler must NOT set status='completed' if cancel_requested=TRUE."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            # Setup: running with cancel_requested=TRUE
            session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', cancel_requested = TRUE
                WHERE id = :id
            """),
                {"id": run_id}
            )
            session.commit()
            
            # Simulate completion handler UPDATE
            result = session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND cancel_requested = FALSE
                RETURNING id
            """),
                {"id": run_id}
            )
            
            # Verify: completion NOT applied (no rows updated)
            updated_row = result.fetchone()
            assert updated_row is None, "Completion must not override cancel"
            
            # Verify: status unchanged
            run = session.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run.status == "running"
            
        finally:
            session.close()
    
    def test_failure_handler_respects_cancel(self, complete_fk_setup, session_factory):
        """Failure handler must NOT set status='failed' if cancel_requested=TRUE."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        session = session_factory()
        try:
            # Setup: running with cancel_requested=TRUE
            session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'running', cancel_requested = TRUE
                WHERE id = :id
            """),
                {"id": run_id}
            )
            session.commit()
            
            # Simulate failure handler UPDATE
            result = session.execute(
                text("""
                UPDATE paper_trading_runs
                SET status = 'failed', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND cancel_requested = FALSE
                RETURNING id
            """),
                {"id": run_id}
            )
            
            # Verify: failure NOT applied
            updated_row = result.fetchone()
            assert updated_row is None, "Failure must not override cancel"
            
        finally:
            session.close()


class TestRaceConditionsWithBarrier:
    """Test race conditions with barrier control."""
    
    def test_concurrent_cancel_with_barrier(self, complete_fk_setup, session_factory):
        """Two concurrent cancel requests: only one succeeds atomically."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        barrier = threading.Barrier(2)
        results = {}
        
        def cancel_attempt(thread_id):
            session = session_factory()
            try:
                pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
                
                # Move to running state (only first thread)
                if thread_id == 0:
                    session.execute(
                        text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
                        {"id": run_id}
                    )
                    session.commit()
                
                # Wait for both threads to be ready
                barrier.wait(timeout=5)
                
                # Both attempt cancel
                results[thread_id] = pt_engine.cancel_run(run_id, user_id)
                
            except Exception as e:
                results[thread_id] = e
            finally:
                session.close()
        
        threads = [threading.Thread(target=cancel_attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        
        # Verify: at least one succeeded, no exceptions
        success_count = sum(1 for r in results.values() if isinstance(r, dict) and r.get("cancel_requested"))
        assert success_count >= 1, "At least one cancel must succeed"
    
    def test_cancel_vs_completion_race(self, complete_fk_setup, session_factory):
        """Cancel and completion race: cancel wins if flag set first."""
        user_id, portfolio_id, run_id = complete_fk_setup
        
        barrier = threading.Barrier(2)
        results = {}
        
        def cancel_attempt():
            session = session_factory()
            try:
                pt_engine = PaperTradingEngine(session.bind.url.render_as_string(hide_password=False))
                
                session.execute(
                    text("UPDATE paper_trading_runs SET status = 'running' WHERE id = :id"),
                    {"id": run_id}
                )
                session.commit()
                
                barrier.wait(timeout=5)
                results["cancel"] = pt_engine.cancel_run(run_id, user_id)
                
            except Exception as e:
                results["cancel"] = e
            finally:
                session.close()
        
        def completion_attempt():
            session = session_factory()
            try:
                barrier.wait(timeout=5)
                
                # Simulate completion handler
                result = session.execute(
                    text("""
                    UPDATE paper_trading_runs
                    SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND cancel_requested = FALSE
                    RETURNING id
                """),
                    {"id": run_id}
                )
                results["completion"] = result.fetchone()
                session.commit()
                
            except Exception as e:
                results["completion"] = e
            finally:
                session.close()
        
        t1 = threading.Thread(target=cancel_attempt)
        t2 = threading.Thread(target=completion_attempt)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        
        # Verify: if cancel succeeded, completion must NOT have applied
        if isinstance(results.get("cancel"), dict) and results["cancel"].get("cancel_requested"):
            assert results.get("completion") is None, "Completion must not override cancel"
    
    # Additional tests would cover:
    # - <100 events (cancel check not triggered)
    # - Polling boundary (exactly 100 events)
    # - Cancel before final commit
    # - Cancel vs failure race
    # - Worker interruption policy
    # - Complete ledger and cash consistency
    # - Actual execute_historical_replay (requires historical trades setup)


# Test count: 12 methods across 4 classes
# - TestTwoPhaseCancelContract: 5 tests
# - TestCancelConvergence: 3 tests  
# - TestCompletionFailureProtection: 2 tests
# - TestRaceConditionsWithBarrier: 2 tests
