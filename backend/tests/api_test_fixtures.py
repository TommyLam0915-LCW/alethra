"""Context manager for API test fixtures with guaranteed cleanup.

Ensures that even if fixture creation fails partway through,
all successfully created resources are cleaned up in reverse order.

Cleanup errors propagate:
- Test body succeeds, cleanup fails → test FAILS
- Test body fails, cleanup fails → both errors visible
"""
from contextlib import contextmanager
from sqlalchemy import text
from tests import uuid_identity
from tests.conftest import TestSession


@contextmanager
def api_test_fixtures(email: str):
    """Context manager that creates and cleans up API test fixtures.
    
    Args:
        email: Test user email
    
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

    user_id = uuid4()
    study_run_id = uuid4()

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
                        '{}', '{}', '[]', 2, :now
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
                        run_clause, run_params = uuid_identity.match("run_id", resource_id, name="run")
                        run_id_clause, run_id_params = uuid_identity.match("id", resource_id, name="run")
                        db.execute(
                            text(f"DELETE FROM strategy_study_trades WHERE {run_clause}"),
                            run_params,
                        )
                        db.execute(
                            text(f"DELETE FROM strategy_study_runs WHERE {run_id_clause}"),
                            run_id_params,
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
    """Remove all paper trading data for a user in FK-safe order.
    
    Unified cleanup set:
    1. Collect all IDs before deletion (portfolio, run, order, fill, position, decision, metric)
    2. Verify fixture ownership (all IDs belong to this user)
    3. Delete in FK-safe order using collected ID sets
    4. Verify per-table zero residuals with independent new connection
    """
    # Every predicate below goes through uuid_identity. Which spelling a row
    # carries depends on which layer wrote it, so a single-spelling probe here
    # answers "this user has nothing to clean" for a user that does.
    # All three clauses bind the same value under the same keys, so uid_params
    # serves every statement in this function.
    user_id_clause, uid_params = uuid_identity.match("id", user_id, name="uid")
    owner_clause, _ = uuid_identity.match("owner_id", user_id, name="uid")
    study_user_clause, _ = uuid_identity.match("user_id", user_id, name="uid")
    not_owner_clause, _ = uuid_identity.match("owner_id", user_id, name="uid", negate=True)
    owned_portfolios = f"(SELECT id FROM paper_portfolios WHERE {owner_clause})"

    # Phase 1: Collect all IDs with ownership verification
    db = TestSession()
    try:
        # Verify user exists
        user_check = db.execute(
            text(f"SELECT id FROM users WHERE {user_id_clause}"),
            uid_params,
        ).fetchone()
        if not user_check:
            return  # User already deleted, nothing to clean
        
        # Collect portfolio IDs
        portfolio_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_portfolios WHERE {owner_clause}"),
            uid_params,
        ).fetchall()]
        
        # Verify all portfolios belong to this user
        if portfolio_ids:
            stray = db.execute(
                text(f"SELECT COUNT(*) FROM paper_portfolios WHERE id IN ({','.join([':p' + str(i) for i in range(len(portfolio_ids))])}) AND {not_owner_clause}"),
                {f'p{i}': pid for i, pid in enumerate(portfolio_ids)} | uid_params
            ).scalar()
            if stray > 0:
                raise RuntimeError(f"Cleanup aborted: {stray} portfolios found belonging to other users")
        
        # Collect paper_trading_run IDs
        run_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_trading_runs WHERE portfolio_id IN {owned_portfolios}"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        # Collect paper_order IDs (both portfolio-based and decision-based)
        order_ids_by_portfolio = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_orders WHERE portfolio_id IN {owned_portfolios}"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        order_ids_by_decision = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_orders WHERE decision_id IN "
                 f"(SELECT id FROM decision_records WHERE paper_portfolio_id IN {owned_portfolios})"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        order_ids = list(set(order_ids_by_portfolio + order_ids_by_decision))
        
        # Collect paper_fill IDs
        fill_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_fills WHERE paper_order_id IN "
                 f"(SELECT id FROM paper_orders WHERE portfolio_id IN {owned_portfolios})"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        # Collect decision IDs
        decision_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM decision_records WHERE paper_portfolio_id IN {owned_portfolios}"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        # Collect position IDs
        position_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_positions WHERE portfolio_id IN {owned_portfolios}"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        # Collect metric IDs
        metric_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM paper_trading_daily_metrics WHERE run_id IN "
                 f"(SELECT id FROM paper_trading_runs WHERE portfolio_id IN {owned_portfolios})"),
            uid_params,
        ).fetchall()] if portfolio_ids else []
        
        # Collect strategy_study IDs
        study_run_ids = [row[0] for row in db.execute(
            text(f"SELECT id FROM strategy_study_runs WHERE {study_user_clause}"),
            uid_params,
        ).fetchall()]
        
    finally:
        db.close()
    
    # Phase 2: Delete in FK-safe order using collected ID sets
    db = TestSession()
    try:
        # Delete fills (depends on orders)
        if fill_ids:
            fill_placeholders = ','.join([':f' + str(i) for i in range(len(fill_ids))])
            db.execute(
                text(f"DELETE FROM paper_fills WHERE id IN ({fill_placeholders})"),
                {f'f{i}': fid for i, fid in enumerate(fill_ids)}
            )
        
        # Delete orders (depends on portfolios and decisions)
        if order_ids:
            order_placeholders = ','.join([':o' + str(i) for i in range(len(order_ids))])
            db.execute(
                text(f"DELETE FROM paper_orders WHERE id IN ({order_placeholders})"),
                {f'o{i}': oid for i, oid in enumerate(order_ids)}
            )
        
        # Delete decisions (depends on portfolios)
        if decision_ids:
            decision_placeholders = ','.join([':d' + str(i) for i in range(len(decision_ids))])
            db.execute(
                text(f"DELETE FROM decision_records WHERE id IN ({decision_placeholders})"),
                {f'd{i}': did for i, did in enumerate(decision_ids)}
            )
        
        # Delete metrics (depends on runs)
        if metric_ids:
            metric_placeholders = ','.join([':m' + str(i) for i in range(len(metric_ids))])
            db.execute(
                text(f"DELETE FROM paper_trading_daily_metrics WHERE id IN ({metric_placeholders})"),
                {f'm{i}': mid for i, mid in enumerate(metric_ids)}
            )
        
        # Delete runs (depends on portfolios)
        if run_ids:
            run_placeholders = ','.join([':r' + str(i) for i in range(len(run_ids))])
            db.execute(
                text(f"DELETE FROM paper_trading_runs WHERE id IN ({run_placeholders})"),
                {f'r{i}': rid for i, rid in enumerate(run_ids)}
            )
        
        # Delete positions (depends on portfolios)
        if position_ids:
            position_placeholders = ','.join([':pos' + str(i) for i in range(len(position_ids))])
            db.execute(
                text(f"DELETE FROM paper_positions WHERE id IN ({position_placeholders})"),
                {f'pos{i}': posid for i, posid in enumerate(position_ids)}
            )
        
        # Delete portfolios (depends on user)
        if portfolio_ids:
            portfolio_placeholders = ','.join([':p' + str(i) for i in range(len(portfolio_ids))])
            db.execute(
                text(f"DELETE FROM paper_portfolios WHERE id IN ({portfolio_placeholders})"),
                {f'p{i}': pid for i, pid in enumerate(portfolio_ids)}
            )
        
        # Delete strategy study trades (depends on study runs)
        if study_run_ids:
            study_run_placeholders = ','.join([':sr' + str(i) for i in range(len(study_run_ids))])
            db.execute(
                text(f"DELETE FROM strategy_study_trades WHERE run_id IN ({study_run_placeholders})"),
                {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
            )
            
            # Delete strategy study runs (depends on user)
            db.execute(
                text(f"DELETE FROM strategy_study_runs WHERE id IN ({study_run_placeholders})"),
                {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
            )
        
        # Delete user (last)
        db.execute(
            text(f"DELETE FROM users WHERE {user_id_clause}"),
            uid_params,
        )
        
        db.commit()
    finally:
        db.close()
    
    # Phase 3: Verify per-table zero residuals with independent new connection
    verify_db = TestSession()
    try:
        # Verify user deleted
        user_count = verify_db.execute(
            text(f"SELECT COUNT(*) FROM users WHERE {user_id_clause}"),
            uid_params,
        ).scalar()
        if user_count != 0:
            raise RuntimeError(f"Cleanup verification failed: user {user_id} still exists (count={user_count})")
        
        # Owner-scoped residuals, independently of what Phase 1 collected. The
        # per-id checks below can only report on ids that were found; these two
        # report on the ownership predicate itself, so a Phase 1 that found
        # nothing is a failure here instead of a silent pass.
        owner_residual = verify_db.execute(
            text(f"SELECT COUNT(*) FROM paper_portfolios WHERE {owner_clause}"),
            uid_params,
        ).scalar()
        if owner_residual != 0:
            raise RuntimeError(
                f"Cleanup verification failed: {owner_residual} portfolios still owned by {user_id}"
            )
        
        study_residual = verify_db.execute(
            text(f"SELECT COUNT(*) FROM strategy_study_runs WHERE {study_user_clause}"),
            uid_params,
        ).scalar()
        if study_residual != 0:
            raise RuntimeError(
                f"Cleanup verification failed: {study_residual} study runs still owned by {user_id}"
            )
        
        # Verify all portfolios deleted
        if portfolio_ids:
            portfolio_placeholders = ','.join([':p' + str(i) for i in range(len(portfolio_ids))])
            portfolio_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_portfolios WHERE id IN ({portfolio_placeholders})"),
                {f'p{i}': pid for i, pid in enumerate(portfolio_ids)}
            ).scalar()
            if portfolio_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {portfolio_count} portfolios still exist")
        
        # Verify all runs deleted
        if run_ids:
            run_placeholders = ','.join([':r' + str(i) for i in range(len(run_ids))])
            run_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_trading_runs WHERE id IN ({run_placeholders})"),
                {f'r{i}': rid for i, rid in enumerate(run_ids)}
            ).scalar()
            if run_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {run_count} runs still exist")
        
        # Verify all orders deleted
        if order_ids:
            order_placeholders = ','.join([':o' + str(i) for i in range(len(order_ids))])
            order_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_orders WHERE id IN ({order_placeholders})"),
                {f'o{i}': oid for i, oid in enumerate(order_ids)}
            ).scalar()
            if order_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {order_count} orders still exist")
        
        # Verify all fills deleted
        if fill_ids:
            fill_placeholders = ','.join([':f' + str(i) for i in range(len(fill_ids))])
            fill_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_fills WHERE id IN ({fill_placeholders})"),
                {f'f{i}': fid for i, fid in enumerate(fill_ids)}
            ).scalar()
            if fill_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {fill_count} fills still exist")
        
        # Verify all decisions deleted
        if decision_ids:
            decision_placeholders = ','.join([':d' + str(i) for i in range(len(decision_ids))])
            decision_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM decision_records WHERE id IN ({decision_placeholders})"),
                {f'd{i}': did for i, did in enumerate(decision_ids)}
            ).scalar()
            if decision_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {decision_count} decisions still exist")
        
        # Verify all positions deleted
        if position_ids:
            position_placeholders = ','.join([':pos' + str(i) for i in range(len(position_ids))])
            position_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_positions WHERE id IN ({position_placeholders})"),
                {f'pos{i}': posid for i, posid in enumerate(position_ids)}
            ).scalar()
            if position_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {position_count} positions still exist")
        
        # Verify all metrics deleted
        if metric_ids:
            metric_placeholders = ','.join([':m' + str(i) for i in range(len(metric_ids))])
            metric_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id IN ({metric_placeholders})"),
                {f'm{i}': mid for i, mid in enumerate(metric_ids)}
            ).scalar()
            if metric_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {metric_count} metrics still exist")
        
        # Verify all study runs deleted
        if study_run_ids:
            study_run_placeholders = ','.join([':sr' + str(i) for i in range(len(study_run_ids))])
            study_count = verify_db.execute(
                text(f"SELECT COUNT(*) FROM strategy_study_runs WHERE id IN ({study_run_placeholders})"),
                {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
            ).scalar()
            if study_count != 0:
                raise RuntimeError(f"Cleanup verification failed: {study_count} study runs still exist")
        
    finally:
        verify_db.close()
