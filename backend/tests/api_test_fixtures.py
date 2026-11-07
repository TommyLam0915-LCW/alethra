"""Context manager for API test fixtures with guaranteed cleanup.

Ensures that even if fixture creation fails partway through,
all successfully created resources are cleaned up in reverse order.

Cleanup errors propagate:
- Test body succeeds, cleanup fails → test FAILS
- Test body fails, cleanup fails → both errors visible
"""
from contextlib import contextmanager
from sqlalchemy import text
from tests.conftest import TestSession


@contextmanager
def api_test_fixtures(email: str):
    """Context manager that creates and cleans up API test fixtures.
    
    Creates in order:
    1. User
    2. Strategy study run
    3. Strategy study trades
    
    Cleans up in reverse order (even if creation fails partway):
    1. Delete trades
    2. Delete run
    3. Delete user (+ all paper trading data)
    
    Usage:
        with api_test_fixtures("test@example.com") as (auth_headers, user_id, run_id):
            # Use fixtures
            # Even if exception occurs here, cleanup will run
        # After exit, all fixtures are cleaned up
    """
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User
    from datetime import datetime, timezone, timedelta
    from uuid import uuid4, UUID
    
    # Pre-generate all IDs before any database operations
    user_id = uuid4()  # UUID object, not string
    study_run_id = uuid4()  # UUID object, not string
    
    # Register cleanup responsibilities BEFORE any writes
    # This ensures cleanup runs even if commit/refresh/token fails
    created_resources = []
    test_body_exception = None
    
    try:
        # Step 1: Create user
        # Register BEFORE commit so cleanup runs even if commit fails
        created_resources.append(("user", user_id))
        
        db = TestSession()
        try:
            user = User(
                id=user_id,  # Use pre-generated UUID object
                email=email,
                password_hash=get_password_hash("testpass123"),
                display_name=email.split("@")[0],
                is_admin=False,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            token = create_access_token(data={"sub": str(user_id)})  # JWT needs string
            auth_headers = {"Authorization": f"Bearer {token}"}
        finally:
            db.close()
        
        # Step 2: Create strategy study run
        created_resources.append(("run", study_run_id))
        
        db = TestSession()
        try:
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
                        '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 2, :now
                    )
                """),
                {"id": str(study_run_id), "user_id": str(user_id), "now": now},
            )
            db.commit()
        finally:
            db.close()
        
        # Step 3: Create strategy study trades
        created_resources.append(("trades", study_run_id))
        
        db = TestSession()
        try:
            now = datetime.now(timezone.utc)
            
            # Trade 1: Buy @ $100, sell @ $110 → profitable
            trade1_id = str(uuid4())
            entry_time_1 = now - timedelta(days=5)
            exit_time_1 = now - timedelta(days=5) + timedelta(hours=1)
            db.execute(
                text("""
                    INSERT INTO strategy_study_trades (
                        id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                        direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                        entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                        total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                    ) VALUES (
                        :id, :run_id, 0, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                        'long', 'signal', 'win', :entry_time, :entry_time, :exit_time,
                        100.0, 110.0, 100.0, 1000.0, 0.10, 0.05, 0.15, 999.85, 100999.85, 1,
                        'Profit', 'Profit', :now
                    )
                """),
                {
                    "id": trade1_id, "run_id": str(study_run_id),
                    "entry_time": entry_time_1, "exit_time": exit_time_1, "now": now,
                },
            )
            
            # Trade 2: Buy @ $100, sell @ $90 → loss
            trade2_id = str(uuid4())
            entry_time_2 = now - timedelta(days=3)
            exit_time_2 = now - timedelta(days=3) + timedelta(hours=1)
            db.execute(
                text("""
                    INSERT INTO strategy_study_trades (
                        id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                        direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                        entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                        total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                    ) VALUES (
                        :id, :run_id, 1, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                        'long', 'signal', 'loss', :entry_time, :entry_time, :exit_time,
                        100.0, 90.0, 100.0, -1000.0, 0.10, 0.05, 0.15, -1000.15, 99999.70, 1,
                        'Loss', 'Loss', :now
                    )
                """),
                {
                    "id": trade2_id, "run_id": str(study_run_id),
                    "entry_time": entry_time_2, "exit_time": exit_time_2, "now": now,
                },
            )
            
            db.commit()
        finally:
            db.close()
        
        # Yield control to test (return string IDs for backward compatibility)
        yield auth_headers, str(user_id), str(study_run_id)
        
    except Exception as e:
        # Capture test body exception to propagate later
        test_body_exception = e
    
    finally:
        # Cleanup in reverse order
        cleanup_errors = []
        for resource_type, resource_id in reversed(created_resources):
            try:
                if resource_type == "trades":
                    # Trades are deleted with the run
                    pass
                elif resource_type == "run":
                    db = TestSession()
                    try:
                        db.execute(
                            text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"),
                            {"run_id": str(resource_id)},
                        )
                        db.execute(
                            text("DELETE FROM strategy_study_runs WHERE id = :id"),
                            {"id": str(resource_id)},
                        )
                        db.commit()
                    finally:
                        db.close()
                elif resource_type == "user":
                    _cleanup_user_data(str(resource_id))
            except Exception as e:
                # Collect cleanup errors but continue cleanup
                cleanup_errors.append((resource_type, resource_id, e))
                import sys
                print(f"ERROR: Cleanup failed for {resource_type} {resource_id}: {e}", file=sys.stderr)
        
        # Propagate errors
        if cleanup_errors:
            if test_body_exception:
                # Both test body and cleanup failed - use ExceptionGroup to preserve both
                all_exceptions = [test_body_exception] + [e for _, _, e in cleanup_errors]
                raise ExceptionGroup(
                    f"Test body failed AND {len(cleanup_errors)} cleanup(s) failed",
                    all_exceptions
                )
            else:
                # Test body succeeded but cleanup failed - fail the test
                error_summary = "; ".join(f"{t} {i}: {e}" for t, i, e in cleanup_errors)
                if len(cleanup_errors) == 1:
                    raise RuntimeError(f"Cleanup failed: {error_summary}") from cleanup_errors[0][2]
                else:
                    # Multiple cleanup failures - use ExceptionGroup
                    all_exceptions = [e for _, _, e in cleanup_errors]
                    raise ExceptionGroup(
                        f"{len(cleanup_errors)} cleanups failed: {error_summary}",
                        all_exceptions
                    )
        
        # If no cleanup errors but test body failed, propagate test body exception
        if test_body_exception:
            raise test_body_exception


def _cleanup_user_data(user_id: str):
    """Remove all paper trading data for a user in FK-safe order."""
    db = TestSession()
    try:
        # Delete in FK-safe order
        # paper_fills → paper_orders → decision_records → paper_trading_daily_metrics → paper_trading_runs → paper_positions → paper_portfolios → users
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
            text("DELETE FROM decision_records WHERE paper_portfolio_id IN "
                 "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM paper_trading_daily_metrics WHERE run_id IN "
                 "(SELECT id FROM paper_trading_runs WHERE portfolio_id IN "
                 "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id))"),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM paper_trading_runs WHERE portfolio_id IN "
                 "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM paper_positions WHERE portfolio_id IN "
                 "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        db.commit()
        
        # Verify cleanup succeeded
        result = db.execute(
            text("SELECT COUNT(*) FROM users WHERE id = :user_id"),
            {"user_id": user_id},
        )
        count = result.scalar()
        if count != 0:
            raise RuntimeError(f"Cleanup verification failed: user {user_id} still exists (count={count})")
    finally:
        db.close()
