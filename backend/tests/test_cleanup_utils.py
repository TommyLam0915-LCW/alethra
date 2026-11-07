"""Shared test utilities for cleanup and verification.

This module provides reusable helpers for test fixtures across different test suites.
"""
from sqlalchemy import text


def collect_user_resource_ids(db_session, user_id: str) -> dict:
    """Collect all resource IDs for a user with ownership verification.
    
    Phase 1 of unified cleanup: collect IDs before deletion.
    
    Returns:
        Dict with keys: portfolio_ids, run_ids, order_ids, fill_ids, 
        decision_ids, position_ids, metric_ids, study_run_ids
        
    Raises:
        RuntimeError: If any resources belong to other users
    """
    # Verify user exists
    user_check = db_session.execute(
        text("SELECT id FROM users WHERE id = :user_id"),
        {"user_id": user_id}
    ).fetchone()
    if not user_check:
        return {
            "portfolio_ids": [],
            "run_ids": [],
            "order_ids": [],
            "fill_ids": [],
            "decision_ids": [],
            "position_ids": [],
            "metric_ids": [],
            "study_run_ids": [],
        }
    
    # Collect portfolio IDs
    portfolio_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_portfolios WHERE owner_id = :user_id"),
        {"user_id": user_id}
    ).fetchall()]
    
    # Verify all portfolios belong to this user
    if portfolio_ids:
        placeholders = ','.join([':p' + str(i) for i in range(len(portfolio_ids))])
        stray = db_session.execute(
            text(f"SELECT COUNT(*) FROM paper_portfolios WHERE id IN ({placeholders}) AND owner_id != :user_id"),
            {f'p{i}': pid for i, pid in enumerate(portfolio_ids)} | {"user_id": user_id}
        ).scalar()
        if stray > 0:
            raise RuntimeError(f"Cleanup aborted: {stray} portfolios found belonging to other users")
    
    # Collect paper_trading_run IDs
    run_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_trading_runs WHERE portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    # Collect paper_order IDs (both portfolio-based and decision-based)
    order_ids_by_portfolio = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_orders WHERE portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    order_ids_by_decision = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_orders WHERE decision_id IN "
             "(SELECT id FROM decision_records WHERE paper_portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id))"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    order_ids = list(set(order_ids_by_portfolio + order_ids_by_decision))
    
    # Collect paper_fill IDs (by order_id, NOT by portfolio_id)
    fill_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_fills WHERE paper_order_id IN "
             "(SELECT id FROM paper_orders WHERE id = ANY(:order_ids))"),
        {"order_ids": order_ids}
    ).fetchall()] if order_ids else []
    
    # Collect decision IDs
    decision_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM decision_records WHERE paper_portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    # Collect position IDs
    position_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_positions WHERE portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    # Collect metric IDs
    metric_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_trading_daily_metrics WHERE run_id IN "
             "(SELECT id FROM paper_trading_runs WHERE portfolio_id IN "
             "(SELECT id FROM paper_portfolios WHERE owner_id = :user_id))"),
        {"user_id": user_id}
    ).fetchall()] if portfolio_ids else []
    
    # Collect strategy_study IDs
    study_run_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM strategy_study_runs WHERE user_id = :user_id"),
        {"user_id": user_id}
    ).fetchall()]
    
    return {
        "portfolio_ids": portfolio_ids,
        "run_ids": run_ids,
        "order_ids": order_ids,
        "fill_ids": fill_ids,
        "decision_ids": decision_ids,
        "position_ids": position_ids,
        "metric_ids": metric_ids,
        "study_run_ids": study_run_ids,
    }


def delete_user_resources_by_ids(db_session, resource_ids: dict):
    """Delete user resources in FK-safe order using collected ID sets.
    
    Phase 2 of unified cleanup: delete using collected IDs.
    
    Args:
        db_session: Database session
        resource_ids: Dict from collect_user_resource_ids()
    """
    portfolio_ids = resource_ids["portfolio_ids"]
    run_ids = resource_ids["run_ids"]
    order_ids = resource_ids["order_ids"]
    fill_ids = resource_ids["fill_ids"]
    decision_ids = resource_ids["decision_ids"]
    position_ids = resource_ids["position_ids"]
    metric_ids = resource_ids["metric_ids"]
    study_run_ids = resource_ids["study_run_ids"]
    
    # Delete fills (depends on orders)
    if fill_ids:
        placeholders = ','.join([':f' + str(i) for i in range(len(fill_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_fills WHERE id IN ({placeholders})"),
            {f'f{i}': fid for i, fid in enumerate(fill_ids)}
        )
    
    # Delete orders (depends on portfolios and decisions)
    if order_ids:
        placeholders = ','.join([':o' + str(i) for i in range(len(order_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_orders WHERE id IN ({placeholders})"),
            {f'o{i}': oid for i, oid in enumerate(order_ids)}
        )
    
    # Delete decisions (depends on portfolios)
    if decision_ids:
        placeholders = ','.join([':d' + str(i) for i in range(len(decision_ids))])
        db_session.execute(
            text(f"DELETE FROM decision_records WHERE id IN ({placeholders})"),
            {f'd{i}': did for i, did in enumerate(decision_ids)}
        )
    
    # Delete metrics (depends on runs)
    if metric_ids:
        placeholders = ','.join([':m' + str(i) for i in range(len(metric_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_trading_daily_metrics WHERE id IN ({placeholders})"),
            {f'm{i}': mid for i, mid in enumerate(metric_ids)}
        )
    
    # Delete runs (depends on portfolios)
    if run_ids:
        placeholders = ','.join([':r' + str(i) for i in range(len(run_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_trading_runs WHERE id IN ({placeholders})"),
            {f'r{i}': rid for i, rid in enumerate(run_ids)}
        )
    
    # Delete positions (depends on portfolios)
    if position_ids:
        placeholders = ','.join([':pos' + str(i) for i in range(len(position_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_positions WHERE id IN ({placeholders})"),
            {f'pos{i}': posid for i, posid in enumerate(position_ids)}
        )
    
    # Delete portfolios (depends on user)
    if portfolio_ids:
        placeholders = ','.join([':p' + str(i) for i in range(len(portfolio_ids))])
        db_session.execute(
            text(f"DELETE FROM paper_portfolios WHERE id IN ({placeholders})"),
            {f'p{i}': pid for i, pid in enumerate(portfolio_ids)}
        )
    
    # Delete strategy study trades (depends on study runs)
    if study_run_ids:
        placeholders = ','.join([':sr' + str(i) for i in range(len(study_run_ids))])
        db_session.execute(
            text(f"DELETE FROM strategy_study_trades WHERE run_id IN ({placeholders})"),
            {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
        )
        
        # Delete strategy study runs (depends on user)
        db_session.execute(
            text(f"DELETE FROM strategy_study_runs WHERE id IN ({placeholders})"),
            {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
        )


def verify_zero_residual_by_ids(verify_session, user_id: str, resource_ids: dict):
    """Verify per-table zero residuals using collected IDs on independent connection.
    
    Phase 3 of unified cleanup: verify deletion with new connection.
    
    Args:
        verify_session: Independent database session for verification
        user_id: User ID to verify
        resource_ids: Dict from collect_user_resource_ids()
        
    Raises:
        RuntimeError: If any resources still exist
    """
    portfolio_ids = resource_ids["portfolio_ids"]
    run_ids = resource_ids["run_ids"]
    order_ids = resource_ids["order_ids"]
    fill_ids = resource_ids["fill_ids"]
    decision_ids = resource_ids["decision_ids"]
    position_ids = resource_ids["position_ids"]
    metric_ids = resource_ids["metric_ids"]
    study_run_ids = resource_ids["study_run_ids"]
    
    # Verify user deleted
    user_count = verify_session.execute(
        text("SELECT COUNT(*) FROM users WHERE id = :user_id"),
        {"user_id": user_id}
    ).scalar()
    if user_count != 0:
        raise RuntimeError(f"Cleanup verification failed: user {user_id} still exists (count={user_count})")
    
    # Verify all portfolios deleted
    if portfolio_ids:
        placeholders = ','.join([':p' + str(i) for i in range(len(portfolio_ids))])
        portfolio_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_portfolios WHERE id IN ({placeholders})"),
            {f'p{i}': pid for i, pid in enumerate(portfolio_ids)}
        ).scalar()
        if portfolio_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {portfolio_count} portfolios still exist")
    
    # Verify all runs deleted
    if run_ids:
        placeholders = ','.join([':r' + str(i) for i in range(len(run_ids))])
        run_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_trading_runs WHERE id IN ({placeholders})"),
            {f'r{i}': rid for i, rid in enumerate(run_ids)}
        ).scalar()
        if run_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {run_count} runs still exist")
    
    # Verify all orders deleted
    if order_ids:
        placeholders = ','.join([':o' + str(i) for i in range(len(order_ids))])
        order_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_orders WHERE id IN ({placeholders})"),
            {f'o{i}': oid for i, oid in enumerate(order_ids)}
        ).scalar()
        if order_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {order_count} orders still exist")
    
    # Verify all fills deleted
    if fill_ids:
        placeholders = ','.join([':f' + str(i) for i in range(len(fill_ids))])
        fill_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_fills WHERE id IN ({placeholders})"),
            {f'f{i}': fid for i, fid in enumerate(fill_ids)}
        ).scalar()
        if fill_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {fill_count} fills still exist")
    
    # Verify all decisions deleted
    if decision_ids:
        placeholders = ','.join([':d' + str(i) for i in range(len(decision_ids))])
        decision_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM decision_records WHERE id IN ({placeholders})"),
            {f'd{i}': did for i, did in enumerate(decision_ids)}
        ).scalar()
        if decision_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {decision_count} decisions still exist")
    
    # Verify all positions deleted
    if position_ids:
        placeholders = ','.join([':pos' + str(i) for i in range(len(position_ids))])
        position_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_positions WHERE id IN ({placeholders})"),
            {f'pos{i}': posid for i, posid in enumerate(position_ids)}
        ).scalar()
        if position_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {position_count} positions still exist")
    
    # Verify all metrics deleted
    if metric_ids:
        placeholders = ','.join([':m' + str(i) for i in range(len(metric_ids))])
        metric_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id IN ({placeholders})"),
            {f'm{i}': mid for i, mid in enumerate(metric_ids)}
        ).scalar()
        if metric_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {metric_count} metrics still exist")
    
    # Verify all study runs deleted
    if study_run_ids:
        placeholders = ','.join([':sr' + str(i) for i in range(len(study_run_ids))])
        study_count = verify_session.execute(
            text(f"SELECT COUNT(*) FROM strategy_study_runs WHERE id IN ({placeholders})"),
            {f'sr{i}': srid for i, srid in enumerate(study_run_ids)}
        ).scalar()
        if study_count != 0:
            raise RuntimeError(f"Cleanup verification failed: {study_count} study runs still exist")


def unified_cleanup_user_data(session_factory, user_id: str):
    """Complete unified cleanup: collect, delete, verify.
    
    This is the main entry point for cleanup operations.
    
    Args:
        session_factory: Callable that creates new database sessions
        user_id: User ID to clean up
    """
    # Phase 1: Collect all IDs with ownership verification
    collect_session = session_factory()
    try:
        resource_ids = collect_user_resource_ids(collect_session, user_id)
    finally:
        collect_session.close()
    
    # Phase 2: Delete in FK-safe order using collected ID sets
    delete_session = session_factory()
    try:
        delete_user_resources_by_ids(delete_session, resource_ids)
        delete_session.commit()
    except Exception:
        delete_session.rollback()
        raise
    finally:
        delete_session.close()
    
    # Phase 3: Verify per-table zero residuals with independent new connection
    verify_session = session_factory()
    try:
        # Also delete user in verify phase (after all resources deleted)
        verify_session.execute(
            text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        verify_session.commit()
        
        verify_zero_residual_by_ids(verify_session, user_id, resource_ids)
    except Exception:
        verify_session.rollback()
        raise
    finally:
        verify_session.close()
