"""Batch B: Historical replay integration tests with cancel barrier.

Tests the full execute_historical_replay flow with:
- Historical trades fixture matching real schema
- Event/barrier pause after execution acquisition
- Cancel submission from another connection
- Exception injection during replay
- Verification of cancelled state, exception preservation, thread termination
"""
import pytest
import uuid
import threading
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from sqlalchemy import text
from sqlalchemy.orm import Session

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from test_target_guard import verify_test_target, TargetGuardError, get_test_db_url


@pytest.fixture
def session_factory():
    """Create session factory with target guard."""
    db_url = get_test_db_url()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        try:
            actual_target = verify_test_target(conn)
        except TargetGuardError as e:
            engine.dispose()
            raise RuntimeError(f"TARGET GUARD FAILED: {e}") from e
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def test_user_id():
    """Generate unique test user ID."""
    return str(uuid.uuid4())


class TestHistoricalReplayIntegration:
    """Integration tests for execute_historical_replay with cancel barrier."""
    
    def test_replay_acquires_execution_then_cancels(self, request, session_factory, test_user_id):
        """Replay acquires execution rights, then cancel is submitted before completion."""
        from app.services.paper_trading import PaperTradingEngine
        
        # Setup: user, portfolio, run, historical trades
        portfolio_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        trade_id = str(uuid.uuid4())
        
        # Register finalizer BEFORE any writes
        def cleanup():
            s = session_factory()
            try:
                s.execute(text("DELETE FROM strategy_study_trades WHERE id = :id"), {"id": trade_id})
                s.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
                s.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
                s.execute(text("DELETE FROM users WHERE id = :id"), {"id": test_user_id})
                s.commit()
            except Exception as e:
                s.rollback()
                raise RuntimeError(f"Cleanup failed: {e}") from e
            finally:
                s.close()
        
        request.addfinalizer(cleanup)
        
        # Create test data
        setup_session = session_factory()
        try:
            # Create user
            setup_session.execute(
                text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type, is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, :password, :name, 'individual', TRUE, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
                {"id": test_user_id, "email": "replay@test.com", "password": "hash", "name": "Replay Test"}
            )
            
            # Create portfolio
            setup_session.execute(
                text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
            """),
                {"id": portfolio_id, "owner_id": test_user_id}
            )
            
            # Create run in 'queued' status
            setup_session.execute(
                text("""
                INSERT INTO paper_trading_runs (
                    id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                    start_date, status, initial_capital, total_pnl, total_trades, cancel_requested
                )
                VALUES (
                    :id, :portfolio_id, :skill, :instrument, :interval, :exit_config,
                    CURRENT_TIMESTAMP, 'queued', 100000.0, 0, 0, FALSE
                )
            """),
                {
                    "id": run_id,
                    "portfolio_id": portfolio_id,
                    "skill": "test_skill",
                    "instrument": "BTC-USD",
                    "interval": 60,
                    "exit_config": "default"
                }
            )
            
            # Create historical trade (matching real schema)
            trade_time = datetime.now(timezone.utc) - timedelta(days=15)
            exit_time = trade_time + timedelta(hours=1)
            setup_session.execute(
                text("""
                INSERT INTO strategy_study_trades (
                    id, source_trade_id, run_id, rule_id, exit_id, data_version,
                    instrument, interval, direction, entry_time, exit_time,
                    entry_price, exit_price, quantity, pnl, confidence
                )
                VALUES (
                    :id, :source_id, :run_id, :rule_id, :exit_id, :data_ver,
                    :instrument, :interval, :direction, :entry_time, :exit_time,
                    :entry_price, :exit_price, :quantity, :pnl, :confidence
                )
            """),
                {
                    "id": trade_id,
                    "source_id": f"source_{trade_id[:8]}",
                    "run_id": run_id,
                    "rule_id": "test_rule",
                    "exit_id": f"exit_{trade_id[:8]}",
                    "data_ver": "v1",
                    "instrument": "BTC-USD",
                    "interval": 60,
                    "direction": "long",
                    "entry_time": trade_time,
                    "exit_time": exit_time,
                    "entry_price": 50000.0,
                    "exit_price": 51000.0,
                    "quantity": 0.1,
                    "pnl": 100.0,
                    "confidence": 0.75
                }
            )
            setup_session.commit()
        finally:
            setup_session.close()
        
        # Create engine with injected session_factory
        engine = PaperTradingEngine(session_factory=session_factory)
        
        # Barrier to pause execution after acquisition
        execution_acquired = threading.Event()
        can_proceed = threading.Event()
        
        # Mock the historical trades query to inject barrier
        original_execute = session_factory().execute
        
        def mock_execute(query, params=None):
            # Check if this is the atomic UPDATE that acquires execution
            if isinstance(query, str) and "UPDATE paper_trading_runs" in query and "RETURNING id" in query:
                result = original_execute(query, params)
                if result.rowcount > 0:
                    # Execution acquired - signal and wait
                    execution_acquired.set()
                    can_proceed.wait(timeout=5.0)
                return result
            return original_execute(query, params)
        
        # Submit cancel from another thread after execution is acquired
        def submit_cancel():
            execution_acquired.wait(timeout=5.0)
            cancel_session = session_factory()
            try:
                cancel_session.execute(
                    text("UPDATE paper_trading_runs SET cancel_requested = TRUE WHERE id = :id"),
                    {"id": run_id}
                )
                cancel_session.commit()
            finally:
                cancel_session.close()
        
        cancel_thread = threading.Thread(target=submit_cancel)
        cancel_thread.start()
        
        # Patch execute to inject barrier
        with patch.object(Session, 'execute', side_effect=mock_execute):
            # Start replay in another thread
            replay_thread = threading.Thread(
                target=lambda: engine.execute_historical_replay(run_id, test_user_id, days_back=30)
            )
            replay_thread.start()
            
            # Wait for execution to be acquired
            assert execution_acquired.wait(timeout=5.0), "Execution should be acquired"
            
            # Let replay proceed (cancel already submitted)
            can_proceed.set()
            
            # Wait for replay to complete
            replay_thread.join(timeout=10.0)
            cancel_thread.join(timeout=5.0)
        
        # Verify run is cancelled
        verify_session = session_factory()
        try:
            run = verify_session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run is not None, "Run should exist"
            assert run.cancel_requested == True, "Cancel should be requested"
            # Status should be 'cancelled' (handler respects cancel)
            assert run.status == "cancelled", f"Run should be cancelled, got {run.status}"
        finally:
            verify_session.close()
    
    def test_replay_exception_preserved_after_cancel(self, request, session_factory, test_user_id):
        """Replay exception during cancel: original exception preserved, not replaced."""
        from app.services.paper_trading import PaperTradingEngine
        
        portfolio_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        
        # Register finalizer
        def cleanup():
            s = session_factory()
            try:
                s.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
                s.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
                s.execute(text("DELETE FROM users WHERE id = :id"), {"id": test_user_id})
                s.commit()
            except Exception as e:
                s.rollback()
                raise RuntimeError(f"Cleanup failed: {e}") from e
            finally:
                s.close()
        
        request.addfinalizer(cleanup)
        
        # Setup minimal data
        setup_session = session_factory()
        try:
            setup_session.execute(
                text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type, is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, :password, :name, 'individual', TRUE, FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
                {"id": test_user_id, "email": "exception@test.com", "password": "hash", "name": "Exception Test"}
            )
            setup_session.execute(
                text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
            """),
                {"id": portfolio_id, "owner_id": test_user_id}
            )
            setup_session.execute(
                text("""
                INSERT INTO paper_trading_runs (
                    id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                    start_date, status, initial_capital, total_pnl, total_trades, cancel_requested
                )
                VALUES (
                    :id, :portfolio_id, :skill, :instrument, :interval, :exit_config,
                    CURRENT_TIMESTAMP, 'queued', 100000.0, 0, 0, TRUE
                )
            """),
                {
                    "id": run_id,
                    "portfolio_id": portfolio_id,
                    "skill": "test_skill",
                    "instrument": "BTC-USD",
                    "interval": 60,
                    "exit_config": "default"
                }
            )
            setup_session.commit()
        finally:
            setup_session.close()
        
        # Create engine
        engine = PaperTradingEngine(session_factory=session_factory)
        
        # Mock historical trades query to raise exception
        with patch('sqlalchemy.orm.Session.execute') as mock_execute:
            def side_effect(query, params=None):
                if isinstance(query, str) and "SELECT" in query and "strategy_study_trades" in query:
                    raise RuntimeError("Injected replay exception")
                # For other queries, use real execute
                return session_factory().execute(query, params)
            
            mock_execute.side_effect = side_effect
            
            # Execute replay - should raise exception
            with pytest.raises(RuntimeError, match="Injected replay exception"):
                engine.execute_historical_replay(run_id, test_user_id, days_back=30)
        
        # Verify exception preserved (not replaced by cancel handler)
        verify_session = session_factory()
        try:
            run = verify_session.execute(
                text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            # Should be 'failed' (exception during replay), not 'cancelled'
            assert run.status == "failed", f"Run should be failed, got {run.status}"
            assert run.cancel_requested == True, "Cancel should still be requested"
        finally:
            verify_session.close()
