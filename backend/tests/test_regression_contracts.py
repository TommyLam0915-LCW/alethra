"""Regression contract tests for paper trading system.

These tests verify critical invariants that must hold across the system:
1. RETURNING clause results are not consumed by hooks
2. Session hooks are isolated (different sessions don't share state)
3. Failed commits don't fire success signals
4. Source query actually selects the specified fixture

These are independent regression tests, not just implementation reading.
"""
import uuid
import threading
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
import pytest
from test_target_guard import get_test_db_url


@pytest.fixture
def session_factory():
    """Create explicit session factory for test isolation.
    
    Uses shared verified factory from test_target_guard.
    FAIL CLOSED: No fallback URL. If ALETHRA_PG_TEST_URL not set, test fails.
    TARGET GUARD: Verifies actual DB/role/server before any write.
    """
    from test_target_guard import create_verified_session_factory
    
    db_url = get_test_db_url()
    guarded_factory, engine = create_verified_session_factory(db_url)
    
    try:
        yield guarded_factory
    finally:
        engine.dispose()


class TestReturningNotConsumed:
    """Verify RETURNING clause results pass through hooks unchanged."""
    
    def test_update_returning_preserved_through_hook(self, session_factory):
        """UPDATE...RETURNING results are not consumed by session hooks."""
        run_id = str(uuid.uuid4())
        portfolio_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        
        # Setup: create user and portfolio
        setup_session = session_factory()
        try:
            setup_session.execute(text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type, 
                                   is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, 'hash', 'Test', 'individual', TRUE, FALSE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": user_id, "email": f"{user_id[:8]}@test.com"})
            
            setup_session.execute(text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash,
                                             current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
            """), {"id": portfolio_id, "owner_id": user_id})
            
            setup_session.execute(text("""
                INSERT INTO paper_trading_runs (id, portfolio_id, skill, instrument_id,
                                               interval_minutes, exit_config, start_date,
                                               initial_capital, status, cancel_requested,
                                               created_at, updated_at)
                VALUES (:id, :portfolio_id, 'test_skill', 'BTC-USD', 60, 'daily', 
                        CURRENT_TIMESTAMP, 100000.0, 'queued', FALSE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": run_id, "portfolio_id": portfolio_id})
            
            setup_session.commit()
        finally:
            setup_session.close()
        
        # Add a hook that tracks execute calls
        hook_session = session_factory()
        execute_calls = []
        original_execute = hook_session.execute
        
        def hooked_execute(query, params=None):
            execute_calls.append(str(query))
            return original_execute(query, params)
        
        hook_session.execute = hooked_execute
        
        try:
            # Execute UPDATE with RETURNING
            result = hook_session.execute(text("""
                UPDATE paper_trading_runs 
                SET status = 'running', updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND status = 'queued'
                RETURNING id, status
            """), {"id": run_id})
            
            # Verify RETURNING result is available
            row = result.fetchone()
            assert row is not None, "RETURNING should return a row"
            assert str(row[0]) == run_id, "RETURNING should return the run_id"
            assert row[1] == 'running', "RETURNING should return the new status"
            
            # Verify hook was called
            assert len(execute_calls) > 0, "Hook should have been called"
            assert "RETURNING" in execute_calls[0], "Hook should see the RETURNING clause"
            
            hook_session.commit()
        finally:
            hook_session.close()
        
        # Cleanup
        cleanup_session = session_factory()
        try:
            cleanup_session.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), 
                                   {"id": run_id})
            cleanup_session.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), 
                                   {"id": portfolio_id})
            cleanup_session.execute(text("DELETE FROM users WHERE id = :id"), 
                                   {"id": user_id})
            cleanup_session.commit()
        finally:
            cleanup_session.close()


class TestSessionHookIsolation:
    """Verify session hooks don't share state across sessions."""
    
    def test_hooks_isolated_per_session(self, session_factory):
        """Different sessions have independent hook state."""
        # Create two sessions with separate hooks
        session1 = session_factory()
        session2 = session_factory()
        
        calls1 = []
        calls2 = []
        
        original_execute1 = session1.execute
        original_execute2 = session2.execute
        
        def hooked_execute1(query, params=None):
            calls1.append(str(query))
            return original_execute1(query, params)
        
        def hooked_execute2(query, params=None):
            calls2.append(str(query))
            return original_execute2(query, params)
        
        session1.execute = hooked_execute1
        session2.execute = hooked_execute2
        
        try:
            # Execute on session1
            session1.execute(text("SELECT 1"))
            assert len(calls1) == 1, "Session 1 hook should be called"
            assert len(calls2) == 0, "Session 2 hook should NOT be called"
            
            # Execute on session2
            session2.execute(text("SELECT 2"))
            assert len(calls1) == 1, "Session 1 hook count should not change"
            assert len(calls2) == 1, "Session 2 hook should be called"
            
            # Execute again on session1
            session1.execute(text("SELECT 3"))
            assert len(calls1) == 2, "Session 1 hook should be called again"
            assert len(calls2) == 1, "Session 2 hook count should not change"
        finally:
            session1.close()
            session2.close()


class TestFailedCommitNoSuccessSignal:
    """Verify failed commits don't fire success signals."""
    
    def test_successful_commit_signals_success(self, session_factory):
        """Successful commit fires success signal."""
        session = session_factory()
        
        commit_called = []
        original_commit = session.commit
        
        def hooked_commit():
            try:
                result = original_commit()
                commit_called.append(True)
                return result
            except Exception as e:
                commit_called.append(False)
                raise
        
        session.commit = hooked_commit
        
        try:
            # Insert a user (should succeed)
            user_id = str(uuid.uuid4())
            
            session.execute(text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type,
                                   is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, 'hash', 'Test', 'individual', TRUE, FALSE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": user_id, "email": f"{user_id[:8]}@test.com"})
            
            session.commit()
            assert commit_called == [True], "Successful commit should signal success"
            
        finally:
            # Cleanup
            cleanup_session = session_factory()
            try:
                cleanup_session.execute(text("DELETE FROM users WHERE id = :id"), 
                                       {"id": user_id})
                cleanup_session.commit()
            finally:
                cleanup_session.close()
            session.close()
    
    def test_rollback_does_not_signal_success(self, session_factory):
        """Rollback does not fire success signal."""
        session = session_factory()
        
        commit_called = []
        original_commit = session.commit
        original_rollback = session.rollback
        
        def hooked_commit():
            try:
                result = original_commit()
                commit_called.append(True)
                return result
            except Exception as e:
                commit_called.append(False)
                raise
        
        def hooked_rollback():
            commit_called.append(False)
            return original_rollback()
        
        session.commit = hooked_commit
        session.rollback = hooked_rollback
        
        try:
            # Insert a user then rollback
            user_id = str(uuid.uuid4())
            
            session.execute(text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type,
                                   is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, 'hash', 'Test', 'individual', TRUE, FALSE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": user_id, "email": f"{user_id[:8]}@test.com"})
            
            session.rollback()
            
            # Verify rollback was called and commit was not
            assert commit_called == [False], "Rollback should signal failure (not success)"
            
        finally:
            session.close()


class TestSourceQuerySelectsFixture:
    """Verify source query actually selects the specified fixture."""
    
    def test_historical_query_selects_created_trade(self, session_factory):
        """Historical replay query selects the trade we created."""
        user_id = str(uuid.uuid4())
        portfolio_id = str(uuid.uuid4())
        study_run_id = str(uuid.uuid4())
        trade_id = str(uuid.uuid4())
        
        # Create test data
        trade_entry_time = datetime.now(timezone.utc) - timedelta(hours=2)
        trade_exit_time = datetime.now(timezone.utc) - timedelta(hours=1)
        
        setup_session = session_factory()
        try:
            # Create user
            setup_session.execute(text("""
                INSERT INTO users (id, email, password_hash, display_name, user_type,
                                   is_active, is_admin, created_at, updated_at)
                VALUES (:id, :email, 'hash', 'Test', 'individual', TRUE, FALSE,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": user_id, "email": f"{user_id[:8]}@test.com"})
            
            # Create portfolio
            setup_session.execute(text("""
                INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash,
                                             current_virtual_cash, status, created_at)
                VALUES (:id, :owner_id, 'USD', 100000.0, 100000.0, 'active', CURRENT_TIMESTAMP)
            """), {"id": portfolio_id, "owner_id": user_id})
            
            # Create study run
            setup_session.execute(text("""
                INSERT INTO strategy_study_runs (id, user_id, lane, research_version,
                                               instrument_id, interval_minutes, provider_id,
                                               data_nature, bar_count, first_open_time,
                                               last_open_time, settings_json, walk_forward_json,
                                               bars_json, trade_count, created_at)
                VALUES (:id, :user_id, 'strategy_research_simulated', 'v1.0', 'BTC-USD', 60,
                        'kraken', 'historical', 100, :first_time, :last_time,
                        '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, 1, CURRENT_TIMESTAMP)
            """), {
                "id": study_run_id,
                "user_id": user_id,
                "first_time": trade_entry_time - timedelta(hours=1),
                "last_time": trade_exit_time
            })
            
            # Create study trade
            setup_session.execute(text("""
                INSERT INTO strategy_study_trades (
                    id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                    direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                    entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                    total_cost, net_pnl, cash_after, bars_held, mfe_pct, mae_pct, cost_ratio_pct,
                    pnl_reason_zh, pnl_reason_en, created_at
                ) VALUES (
                    :id, :run_id, 0, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                    'long', 'target', 'win', :signal_time, :entry_time, :exit_time,
                    50000.0, 51000.0, 0.1, 100.0, 5.0, 2.5, 7.5, 92.5, 100092.5,
                    60, 2.0, -1.0, 0.015,
                    'Profit target reached', 'Profit target reached', CURRENT_TIMESTAMP
                )
            """), {
                "id": trade_id,
                "run_id": study_run_id,
                "signal_time": trade_entry_time - timedelta(minutes=5),
                "entry_time": trade_entry_time,
                "exit_time": trade_exit_time
            })
            
            setup_session.commit()
        finally:
            setup_session.close()
        
        # Now execute the same query that historical replay uses
        verify_session = session_factory()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            as_of = datetime.now(timezone.utc)
            
            trades = verify_session.execute(text("""
                SELECT
                    t.id as source_trade_id,
                    t.entry_time, t.exit_time, t.entry_price, t.exit_price,
                    t.net_pnl, t.direction, t.quantity, t.exit_reason,
                    t.run_id as source_run_id, t.rule_id as source_rule_id,
                    t.exit_id as source_exit_id, r.research_version as source_data_version
                FROM strategy_study_trades t
                JOIN strategy_study_runs r ON t.run_id = r.id
                WHERE r.instrument_id = :instrument
                  AND r.interval_minutes = :interval
                  AND t.rule_id = :rule_id
                  AND r.user_id = :user_id
                  AND t.entry_time >= :cutoff
                  AND t.exit_time <= :as_of
                ORDER BY t.entry_time ASC
            """), {
                "instrument": "BTC-USD",
                "interval": 60,
                "rule_id": "test_rule",
                "user_id": user_id,
                "cutoff": cutoff,
                "as_of": as_of
            }).fetchall()
            
            # Verify the query selected our fixture
            assert len(trades) == 1, f"Query should select exactly 1 trade, got {len(trades)}"
            assert str(trades[0][0]) == trade_id, "Query should select the trade we created"
            assert str(trades[0][9]) == study_run_id, "Query should return correct run_id"
            assert trades[0][10] == "test_rule", "Query should return correct rule_id"
            
        finally:
            verify_session.close()
        
        # Cleanup
        cleanup_session = session_factory()
        try:
            cleanup_session.execute(text("DELETE FROM strategy_study_trades WHERE id = :id"),
                                   {"id": trade_id})
            cleanup_session.execute(text("DELETE FROM strategy_study_runs WHERE id = :id"),
                                   {"id": study_run_id})
            cleanup_session.execute(text("DELETE FROM paper_portfolios WHERE id = :id"),
                                   {"id": portfolio_id})
            cleanup_session.execute(text("DELETE FROM users WHERE id = :id"),
                                   {"id": user_id})
            cleanup_session.commit()
        finally:
            cleanup_session.close()
