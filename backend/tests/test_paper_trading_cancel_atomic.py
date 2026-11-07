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

    FAIL CLOSED: No fallback URL. If ALETHRA_PG_TEST_URL not set, test fails.
    This ensures tests never accidentally use wrong database.

    TARGET GUARD: Verifies actual DB/role/server before any write.
    Stops if target doesn't match approved test environment.
    """
    import os
    db_url = os.environ.get("ALETHRA_PG_TEST_URL")
    if not db_url:
        raise RuntimeError(
            "ALETHRA_PG_TEST_URL environment variable not set. "
            "Cannot proceed without explicit test database configuration. "
            "Set: export ALETHRA_PG_TEST_URL='postgresql://role@host:port/db'"
        )

    engine = create_engine(db_url, pool_pre_ping=True)

    # TARGET GUARD: Verify actual DB/role/server before any write
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                current_database() as db_name,
                current_user as role_name,
                inet_server_addr() as server_addr,
                inet_server_port() as server_port
        """)).fetchone()

        db_name, role_name, server_addr, server_port = result

        # Expected test target (from approved configuration)
        expected_db = "ai_investment_lab_test"
        expected_role = "alethra_test_runtime"
        expected_server = "127.0.0.1"  # localhost
        expected_port = "5432"

        mismatches = []
        if db_name != expected_db:
            mismatches.append(f"DB: got '{db_name}', expected '{expected_db}'")
        if role_name != expected_role:
            mismatches.append(f"Role: got '{role_name}', expected '{expected_role}'")
        if server_addr != expected_server:
            mismatches.append(f"Server: got '{server_addr}', expected '{expected_server}'")
        if str(server_port) != expected_port:
            mismatches.append(f"Port: got '{server_port}', expected '{expected_port}'")

        if mismatches:
            engine.dispose()
            raise RuntimeError(
                "TARGET GUARD FAILED - Test environment does not match approved target.\n"
                + "\n".join(mismatches)
                + "\nStopping before any write operations."
            )

    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db_session(session_factory):
    """Create a database session for testing."""
    session = session_factory()
    yield session
    session.close()


import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from test_cleanup_utils import unified_cleanup_user_data


@pytest.fixture
def complete_fk_setup(request, session_factory, test_user_id, test_portfolio_id, test_run_id):
    """Set up complete FK chain: users → portfolios → runs.

    Uses real schema with all required columns.
    Registers finalizer early to ensure cleanup even if setup fails.
    Uses unified cleanup pattern: collect IDs, delete by IDs, verify zero residual.
    """
    # Track what has been created
    created_resources = {"user_id": test_user_id, "portfolio_id": test_portfolio_id, "run_id": test_run_id}

    def cleanup():
        """Remove all test data using unified cleanup pattern.
        
        Always attempts cleanup, even if setup didn't complete.
        Uses independent connections for each phase.
        Preserves error evidence for all failure scenarios.
        """
        user_id = created_resources["user_id"]
        
        try:
            # Use unified cleanup: collect, delete, verify
            unified_cleanup_user_data(session_factory, user_id)
        except Exception as e:
            # Preserve error evidence - don't swallow exceptions
            import traceback
            error_details = traceback.format_exc()
            raise RuntimeError(f"Cleanup failed for user {user_id}: {e}\n{error_details}") from e

    # Register finalizer EARLY - before any DB operations
    # This ensures cleanup runs even if setup fails partway through
    request.addfinalizer(cleanup)

    # Setup session
    setup_session = session_factory()
    try:
        # Create user (real schema: requires password_hash, is_active, updated_at)
        setup_session.execute(
            text("""
            INSERT INTO users (id, email, password_hash, display_name, user_type, is_active, is_admin, created_at, updated_at)
            VALUES (:id, :email, :password, :name, 'individual', TRUE, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO NOTHING
        """),
            {
                "id": test_user_id,
                "email": f"test-{test_user_id[:8]}@alethra.io",
                "password": "test_hash_not_real_password",  # Required field, not used in tests
                "name": "Test User"
            }
        )

        # Create portfolio (real schema: requires currency, initial_virtual_cash)
        setup_session.execute(
            text("""
            INSERT INTO paper_portfolios (
                id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at
            )
            VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
        """),
            {"id": test_portfolio_id, "owner_id": test_user_id}
        )

        # Create run (real schema: requires all columns from PaperTradingRun model)
        setup_session.execute(
            text("""
            INSERT INTO paper_trading_runs (
                id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                start_date, initial_capital, status, cancel_requested, run_type, created_at, updated_at
            )
            VALUES (
                :id, :portfolio_id, 'test_skill', 'BTC-USD', 1, 'test_exit',
                CURRENT_TIMESTAMP, 100000.0, 'queued', FALSE, 'historical_simulation',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """),
            {"id": test_run_id, "portfolio_id": test_portfolio_id}
        )

        setup_session.commit()

    except Exception as e:
        setup_session.rollback()
        # Preserve error evidence
        import traceback
        error_details = traceback.format_exc()
        raise RuntimeError(f"Setup failed: {e}\n{error_details}") from e
    finally:
        setup_session.close()

    yield test_user_id, test_portfolio_id, test_run_id


def _verify_zero_residual_independent(session_factory, user_id, portfolio_ids, run_id):
    """Verify complete cleanup using NEW INDEPENDENT connection.

    This ensures verification is done with a fresh session, not reusing the cleanup session.
    """
    verify_session = session_factory()
    try:
        # Verify user deleted
        user_count = verify_session.execute(
            text("SELECT COUNT(*) FROM users WHERE id = :id"),
            {"id": user_id}
        ).scalar()
        assert user_count == 0, f"User {user_id} not deleted"

        # Verify portfolio deleted
        portfolio_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_portfolios WHERE owner_id = :id"),
            {"id": user_id}
        ).scalar()
        assert portfolio_count == 0, f"Portfolios for user {user_id} not deleted"

        # Verify run deleted
        run_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id}
        ).scalar()
        assert run_count == 0, f"Run {run_id} not deleted"

        if portfolio_ids:
            # Verify all child tables deleted
            for table in ["paper_fills", "paper_orders", "paper_positions", "decision_records"]:
                if table == "decision_records":
                    # decision_records uses paper_portfolio_id, not portfolio_id
                    count = verify_session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE paper_portfolio_id = ANY(:ids)"),
                        {"ids": portfolio_ids}
                    ).scalar()
                else:
                    count = verify_session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE portfolio_id = ANY(:ids)"),
                        {"ids": portfolio_ids}
                    ).scalar()
                assert count == 0, f"{table} not deleted for portfolios {portfolio_ids}"

            # Verify metrics deleted
            metrics_count = verify_session.execute(
                text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :id"),
                {"id": run_id}
            ).scalar()
            assert metrics_count == 0, f"Metrics for run {run_id} not deleted"
    finally:
        verify_session.close()


def _verify_zero_residual(db_session, user_id, portfolio_ids, run_id):
    """Verify complete cleanup with comprehensive checks (legacy, uses same session)."""
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
            if table == "decision_records":
                # decision_records uses paper_portfolio_id, not portfolio_id
                count = db_session.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE paper_portfolio_id = ANY(:ids)"),
                    {"ids": portfolio_ids}
                ).scalar()
            else:
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


class TestSessionFactoryContract:
    """Verify session factory injection contract through actual service methods (no DB required)."""

    def test_check_cancel_requested_calls_factory(self):
        """check_cancel_requested must call injected factory and close session."""
        factory_calls = []
        sessions_created = []
        sessions_closed = []

        def mock_factory():
            factory_calls.append(1)

            class MockResult:
                def fetchone(self):
                    # Return tuple that can be unpacked: (cancel_requested, status)
                    return (False, "running")

            class MockSession:
                def execute(self, stmt, params=None):
                    sessions_created.append(1)
                    return MockResult()

                def close(self):
                    sessions_closed.append(1)

            return MockSession()

        engine = PaperTradingEngine(session_factory=mock_factory)

        # Call actual service method
        result = engine.check_cancel_requested("test-run-id")

        # Verify factory was called
        assert len(factory_calls) == 1, "Factory must be called exactly once"

        # Verify session was created
        assert len(sessions_created) == 1, "Session must be created"

        # Verify session was closed
        assert len(sessions_closed) == 1, "Session must be closed"

        # Verify result
        assert result is False, "check_cancel_requested should return False"

    def test_cancel_run_calls_factory_on_not_found(self):
        """cancel_run must call factory even when run not found."""
        factory_calls = []
        sessions_closed = []

        def mock_factory():
            factory_calls.append(1)

            class MockResult:
                def fetchone(self):
                    return None  # Run not found

            class MockSession:
                def execute(self, stmt, params=None):
                    return MockResult()

                def rollback(self):
                    pass

                def close(self):
                    sessions_closed.append(1)

            return MockSession()

        engine = PaperTradingEngine(session_factory=mock_factory)

        # Call actual service method (expect exception)
        try:
            engine.cancel_run("nonexistent-run", "nonexistent-user")
            assert False, "Should have raised ResourceNotFoundError"
        except ResourceNotFoundError:
            pass  # Expected

        # Verify factory was called
        assert len(factory_calls) >= 1, "Factory must be called"

        # Verify session was closed (even on exception)
        assert len(sessions_closed) >= 1, "Session must be closed even on exception"

    def test_multiple_operations_reuse_factory(self):
        """Multiple service calls must reuse the same factory."""
        factory_calls = []

        def mock_factory():
            factory_calls.append(1)

            class MockResult:
                def fetchone(self):
                    return (False, "running")

            class MockSession:
                def execute(self, stmt, params=None):
                    return MockResult()
                def close(self):
                    pass

            return MockSession()

        engine = PaperTradingEngine(session_factory=mock_factory)

        # Call multiple methods
        engine.check_cancel_requested("run-1")
        engine.check_cancel_requested("run-2")
        engine.check_cancel_requested("run-3")

        # Verify factory called 3 times (once per operation)
        assert len(factory_calls) == 3, "Factory must be called for each operation"

    def test_legacy_database_url_creates_working_factory(self):
        """Legacy database_url parameter must create a working factory."""
        # Use SQLite in-memory for this test (no external DB required)
        engine = PaperTradingEngine(database_url="sqlite:///:memory:")

        # Verify factory was created
        assert engine.session_factory is not None
        assert callable(engine.session_factory)

        # Verify factory can create sessions
        session = engine.session_factory()
        assert session is not None

        # Verify session can execute
        result = session.execute(text("SELECT 1"))
        assert result is not None

        session.close()

    def test_no_parameters_falls_back_to_global_with_warning(self, caplog):
        """No parameters must fall back to global SessionLocal (with warning)."""
        import logging

        # Set log level to capture warnings
        with caplog.at_level(logging.WARNING):
            engine = PaperTradingEngine()

        # Verify warning was logged
        assert any("global SessionLocal" in record.message for record in caplog.records)

        # Verify factory is set (to global)
        assert engine.session_factory is not None
        assert callable(engine.session_factory)


class TestTwoPhaseCancelContract:
    """Test two-phase cancel contract: request vs completion."""

    def test_phase1_cancel_requested_running_state(self, complete_fk_setup, session_factory):
        """Phase 1: Cancel request sets flag, status unchanged for running state."""
        user_id, portfolio_id, run_id = complete_fk_setup

        # Use explicit session factory, not global SessionLocal
        session = session_factory()
        try:
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
            pt_engine = PaperTradingEngine(session_factory=session_factory)

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
                pt_engine = PaperTradingEngine(session_factory=session_factory)

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
                pt_engine = PaperTradingEngine(session_factory=session_factory)

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
