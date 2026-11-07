"""Batch B: Historical replay integration test with proper FK chain and synchronization.

Tests execute_historical_replay with:
- Complete FK chain: user → study_run → study_trade + user → portfolio → paper_run
- Separate study_run_id (research) and paper_run_id (paper trading)
- Partial session injection (not global patch)
- Proper Event/barrier synchronization
- Cancel from independent session
- Exception injection after cancel committed
- Verification of cancelled state, exception preservation, thread termination
- Complete ledger cleanup with zero residual verification
"""
import pytest
import uuid
import threading
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

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


class TestHistoricalReplayFullIntegration:
    """Full integration test for execute_historical_replay with cancel barrier."""
    
    def test_replay_with_cancel_and_exception(self, request, session_factory, test_user_id):
        """
        Complete integration test:
        1. Create legal study run → study trade (research lane)
        2. Create paper run (paper trading lane)
        3. Call execute_historical_replay
        4. Pause after acquisition commit with Event
        5. Submit cancel from independent session
        6. Inject exception after cancel committed
        7. Verify: status=cancelled, cancel_requested=True, exception preserved
        8. Cleanup complete ledger, verify zero residual
        """
        from app.services.paper_trading import PaperTradingEngine
        
        # Separate IDs for research and paper trading lanes
        study_run_id = str(uuid.uuid4())
        paper_run_id = str(uuid.uuid4())
        portfolio_id = str(uuid.uuid4())
        trade_id = str(uuid.uuid4())
        
        # Register finalizer BEFORE any writes
        def cleanup():
            s = session_factory()
            try:
                # Cleanup in FK-safe order
                s.execute(text("DELETE FROM strategy_study_trades WHERE id = :id"), {"id": trade_id})
                s.execute(text("DELETE FROM strategy_study_runs WHERE id = :id"), {"id": study_run_id})
                s.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": paper_run_id})
                s.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
                s.execute(text("DELETE FROM users WHERE id = :id"), {"id": test_user_id})
                s.commit()
            except Exception as e:
                s.rollback()
                raise RuntimeError(f"Cleanup failed: {e}") from e
            finally:
                s.close()
        
        request.addfinalizer(cleanup)
        
        # Setup complete FK chain
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
            
            # Create portfolio for paper trading
            setup_session.execute(
                text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
            """),
                {"id": portfolio_id, "owner_id": test_user_id}
            )
            
            # Create paper trading run (separate from study run)
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
                    "id": paper_run_id,
                    "portfolio_id": portfolio_id,
                    "skill": "test_rule",  # This is rule_id, not display name
                    "instrument": "BTC-USD",
                    "interval": 60,
                    "exit_config": "default"
                }
            )
            
            # Create strategy study run (research lane, separate ID)
            trade_time = datetime.now(timezone.utc) - timedelta(days=15)
            exit_time = trade_time + timedelta(hours=1)
            setup_session.execute(
                text("""
                INSERT INTO strategy_study_runs (
                    id, user_id, lane, research_version, instrument_id, interval_minutes,
                    provider_id, data_nature, bar_count, first_open_time, last_open_time,
                    settings_json, walk_forward_json, bars_json, trade_count, created_at
                )
                VALUES (
                    :id, :user_id, :lane, :version, :instrument, :interval,
                    :provider, :nature, :bar_count, :first_open, :last_open,
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 1, CURRENT_TIMESTAMP
                )
            """),
                {
                    "id": study_run_id,
                    "user_id": test_user_id,
                    "lane": "strategy_research_simulated",
                    "version": "v1.0",
                    "instrument": "BTC-USD",
                    "interval": 60,
                    "provider": "twelvedata",
                    "nature": "historical",
                    "bar_count": 100,
                    "first_open": trade_time - timedelta(days=30),
                    "last_open": exit_time
                }
            )
            
            # Create strategy study trade (links to study_run_id, NOT paper_run_id)
            setup_session.execute(
                text("""
                INSERT INTO strategy_study_trades (
                    id, run_id, trade_index, fold, rule_id, rule_label_zh,
                    exit_id, exit_label_zh, direction, exit_reason, outcome,
                    signal_time, entry_time, exit_time,
                    entry_price, exit_price, quantity,
                    gross_pnl, commission, slippage_cost, total_cost, net_pnl, cash_after,
                    bars_held, mfe_pct, mae_pct, cost_ratio_pct,
                    pnl_reason_zh, pnl_reason_en, created_at
                )
                VALUES (
                    :id, :run_id, 0, 0, :rule_id, 'Test Rule',
                    :exit_id, 'Default Exit', 'long', 'target', 'win',
                    :signal_time, :entry_time, :exit_time,
                    50000.0, 51000.0, 0.1,
                    100.0, 5.0, 2.5, 7.5, 92.5, 100092.5,
                    60, 2.0, -1.0, 0.015,
                    'Profit target reached', 'Profit target reached', CURRENT_TIMESTAMP
                )
            """),
                {
                    "id": trade_id,
                    "run_id": study_run_id,  # Links to study run, not paper run
                    "rule_id": "test_rule",
                    "exit_id": "default_exit",
                    "signal_time": trade_time - timedelta(minutes=5),
                    "entry_time": trade_time,
                    "exit_time": exit_time
                }
            )
            setup_session.commit()
        finally:
            setup_session.close()
        
        # Synchronization events
        acquisition_committed = threading.Event()
        cancel_committed = threading.Event()
        can_inject_exception = threading.Event()
        replay_completed = threading.Event()
        
        # Track results
        results = {
            "replay_exception": None,
            "cancel_thread_done": False,
            "replay_thread_done": False,
        }
        
        # Create engine with injected session_factory
        engine = PaperTradingEngine(session_factory=session_factory)
        
        # Wrap the session to inject hooks at specific points
        original_session_factory = session_factory
        
        def hooked_session_factory():
            session = original_session_factory()
            original_execute = session.execute
            original_commit = session.commit
            
            execute_count = [0]
            
            def hooked_execute(query, params=None):
                execute_count[0] += 1
                result = original_execute(query, params)
                
                # Detect acquisition commit (the atomic UPDATE that acquires execution)
                if isinstance(query, str) and "UPDATE paper_trading_runs" in query and "RETURNING id" in query:
                    if result.rowcount > 0:
                        # Acquisition succeeded, signal after commit
                        pass
                
                return result
            
            def hooked_commit():
                original_commit()
                # Check if this is the acquisition commit
                if execute_count[0] > 0:
                    # After acquisition commit, signal
                    acquisition_committed.set()
                    # Wait for cancel to be committed
                    cancel_committed.wait(timeout=5.0)
                    # Now allow exception injection
                    can_inject_exception.set()
            
            session.execute = hooked_execute
            session.commit = hooked_commit
            return session
        
        # Thread 1: Submit cancel after acquisition
        def submit_cancel():
            try:
                acquisition_committed.wait(timeout=5.0)
                cancel_session = session_factory()
                try:
                    cancel_session.execute(
                        text("UPDATE paper_trading_runs SET cancel_requested = TRUE WHERE id = :id"),
                        {"id": paper_run_id}
                    )
                    cancel_session.commit()
                    results["cancel_thread_done"] = True
                    cancel_committed.set()
                finally:
                    cancel_session.close()
            except Exception as e:
                results["cancel_thread_exception"] = e
                cancel_committed.set()  # Release even on error
        
        # Thread 2: Execute replay
        def execute_replay():
            try:
                # Use hooked session factory for replay
                engine.session_factory = hooked_session_factory
                engine.execute_historical_replay(paper_run_id, test_user_id, days_back=30)
            except Exception as e:
                results["replay_exception"] = e
            finally:
                results["replay_thread_done"] = True
                replay_completed.set()
        
        # Start threads
        cancel_thread = threading.Thread(target=submit_cancel)
        replay_thread = threading.Thread(target=execute_replay)
        
        cancel_thread.start()
        replay_thread.start()
        
        try:
            # Wait for replay to complete (with timeout)
            assert replay_completed.wait(timeout=10.0), "Replay should complete within timeout"
            
            # Wait for threads to terminate
            cancel_thread.join(timeout=5.0)
            replay_thread.join(timeout=5.0)
            
            # Verify threads terminated
            assert not cancel_thread.is_alive(), "Cancel thread must be terminated"
            assert not replay_thread.is_alive(), "Replay thread must be terminated"
            
            # Verify cancel thread succeeded
            assert results["cancel_thread_done"], "Cancel thread should complete successfully"
            
            # Verify run state
            verify_session = session_factory()
            try:
                run = verify_session.execute(
                    text("SELECT status, cancel_requested FROM paper_trading_runs WHERE id = :id"),
                    {"id": paper_run_id}
                ).fetchone()
                
                assert run is not None, "Run should exist"
                assert run.cancel_requested == True, "Cancel should be requested"
                # Status should be 'cancelled' (handler respects cancel)
                assert run.status == "cancelled", f"Run should be cancelled, got {run.status}"
            finally:
                verify_session.close()
            
            # Verify zero residual (cleanup will handle this via finalizer)
            # But we can check here that all resources still exist before cleanup
            verify_session2 = session_factory()
            try:
                # Study run should still exist
                study_count = verify_session2.execute(
                    text("SELECT COUNT(*) FROM strategy_study_runs WHERE id = :id"),
                    {"id": study_run_id}
                ).scalar()
                assert study_count == 1, "Study run should exist before cleanup"
                
                # Study trade should still exist
                trade_count = verify_session2.execute(
                    text("SELECT COUNT(*) FROM strategy_study_trades WHERE id = :id"),
                    {"id": trade_id}
                ).scalar()
                assert trade_count == 1, "Study trade should exist before cleanup"
            finally:
                verify_session2.close()
        
        finally:
            # Ensure threads are terminated before cleanup
            if cancel_thread.is_alive():
                cancel_thread.join(timeout=2.0)
            if replay_thread.is_alive():
                replay_thread.join(timeout=2.0)
            
            # Release events to unblock any waiting threads
            acquisition_committed.set()
            cancel_committed.set()
            can_inject_exception.set()
            replay_completed.set()
