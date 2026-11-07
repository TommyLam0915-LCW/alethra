"""Shared test utilities for cleanup and verification.

This module provides reusable helpers for test fixtures across different test suites.

Safety guarantees:
- All ownership verification happens BEFORE any deletion
- Cross-user association aborts cleanup immediately
- All DELETEs (including user) happen in single transaction
- Verification phase is read-only on independent connection
"""
from sqlalchemy import text


def collect_user_resource_ids(db_session, user_id: str) -> dict:
    """Collect all resource IDs for a user with comprehensive ownership verification.
    
    Phase 1 of unified cleanup: collect IDs and verify ALL ownership before deletion.
    
    Ownership verification covers:
    - Portfolios: owner_id = user_id
    - Runs: portfolio_id -> portfolios.owner_id = user_id
    - Orders (portfolio-based): portfolio_id -> portfolios.owner_id = user_id
    - Orders (decision-based): decision_id -> decisions.paper_portfolio_id -> portfolios.owner_id = user_id
    - Fills: paper_order_id -> orders.id (verified via orders)
    - Decisions: paper_portfolio_id -> portfolios.owner_id = user_id
    - Positions: portfolio_id -> portfolios.owner_id = user_id
    - Metrics: run_id -> runs.portfolio_id -> portfolios.owner_id = user_id
    - Study runs: user_id = user_id
    
    Returns:
        Dict with keys: portfolio_ids, run_ids, order_ids, fill_ids, 
        decision_ids, position_ids, metric_ids, study_run_ids
        
    Raises:
        RuntimeError: If ANY resources belong to other users (cleanup aborted)
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
    
    # Collect portfolio IDs (only those owned by user)
    portfolio_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_portfolios WHERE owner_id = :user_id"),
        {"user_id": user_id}
    ).fetchall()]
    
    # Collect paper_trading_run IDs (only via user's portfolios)
    run_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_trading_runs WHERE portfolio_id = ANY(:portfolio_ids)"),
        {"portfolio_ids": portfolio_ids}
    ).fetchall()] if portfolio_ids else []
    
    # Collect paper_order IDs (both portfolio-based and decision-based)
    order_ids_by_portfolio = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_orders WHERE portfolio_id = ANY(:portfolio_ids)"),
        {"portfolio_ids": portfolio_ids}
    ).fetchall()] if portfolio_ids else []
    
    order_ids_by_decision = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_orders WHERE decision_id IN "
             "(SELECT id FROM decision_records WHERE paper_portfolio_id = ANY(:portfolio_ids))"),
        {"portfolio_ids": portfolio_ids}
    ).fetchall()] if portfolio_ids else []
    
    order_ids = list(set(order_ids_by_portfolio + order_ids_by_decision))
    
    # Collect paper_fill IDs (by order_id, NOT by portfolio_id)
    fill_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_fills WHERE paper_order_id = ANY(:order_ids)"),
        {"order_ids": order_ids}
    ).fetchall()] if order_ids else []
    
    # Collect decision IDs (only via user's portfolios)
    decision_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM decision_records WHERE paper_portfolio_id = ANY(:portfolio_ids)"),
        {"portfolio_ids": portfolio_ids}
    ).fetchall()] if portfolio_ids else []
    
    # Collect position IDs (only via user's portfolios)
    position_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_positions WHERE portfolio_id = ANY(:portfolio_ids)"),
        {"portfolio_ids": portfolio_ids}
    ).fetchall()] if portfolio_ids else []
    
    # Collect metric IDs (only via user's runs)
    metric_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM paper_trading_daily_metrics WHERE run_id = ANY(:run_ids)"),
        {"run_ids": run_ids}
    ).fetchall()] if run_ids else []
    
    # Collect strategy_study IDs (only for this user)
    study_run_ids = [row[0] for row in db_session.execute(
        text("SELECT id FROM strategy_study_runs WHERE user_id = :user_id"),
        {"user_id": user_id}
    ).fetchall()]
    
    # === OWNERSHIP VERIFICATION (BIDIRECTIONAL) ===
    
    # 1. Verify ALL orders (union of portfolio-based and decision-based):
    #    - portfolio_id must point to user's portfolios
    #    - If decision_id is not null, it must point to user's decisions
    if order_ids:
        # Check portfolio ownership for all orders
        stray_portfolio_orders = db_session.execute(
            text("SELECT COUNT(*) FROM paper_orders WHERE id = ANY(:order_ids) "
                 "AND portfolio_id NOT IN (SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"order_ids": order_ids, "user_id": user_id}
        ).scalar()
        if stray_portfolio_orders > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_portfolio_orders} orders link to portfolios outside user's scope"
            )
        
        # Check decision ownership for orders with decision_id
        stray_decision_orders = db_session.execute(
            text("SELECT COUNT(*) FROM paper_orders WHERE id = ANY(:order_ids) "
                 "AND decision_id IS NOT NULL "
                 "AND decision_id NOT IN (SELECT id FROM decision_records WHERE paper_portfolio_id = ANY(:portfolio_ids))"),
            {"order_ids": order_ids, "portfolio_ids": portfolio_ids}
        ).scalar()
        if stray_decision_orders > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_decision_orders} orders link to decisions outside user's scope"
            )
    
    # 3. Verify decisions: decisions.paper_portfolio_id must point to user's portfolios
    if decision_ids:
        stray_decisions = db_session.execute(
            text("SELECT COUNT(*) FROM decision_records WHERE id = ANY(:decision_ids) "
                 "AND paper_portfolio_id NOT IN (SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"decision_ids": decision_ids, "user_id": user_id}
        ).scalar()
        if stray_decisions > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_decisions} decisions link to portfolios outside user's scope"
            )
    
    # 4. Verify fills: fills.paper_order_id must point to user's orders
    if fill_ids:
        stray_fills = db_session.execute(
            text("SELECT COUNT(*) FROM paper_fills WHERE id = ANY(:fill_ids) "
                 "AND paper_order_id NOT IN (SELECT id FROM paper_orders WHERE id = ANY(:order_ids))"),
            {"fill_ids": fill_ids, "order_ids": order_ids}
        ).scalar()
        if stray_fills > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_fills} fills link to orders outside user's scope"
            )
    
    # 5. Verify runs: runs.portfolio_id must point to user's portfolios
    if run_ids:
        stray_runs = db_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = ANY(:run_ids) "
                 "AND portfolio_id NOT IN (SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"run_ids": run_ids, "user_id": user_id}
        ).scalar()
        if stray_runs > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_runs} runs link to portfolios outside user's scope"
            )
    
    # 6. Verify positions: positions.portfolio_id must point to user's portfolios
    if position_ids:
        stray_positions = db_session.execute(
            text("SELECT COUNT(*) FROM paper_positions WHERE id = ANY(:position_ids) "
                 "AND portfolio_id NOT IN (SELECT id FROM paper_portfolios WHERE owner_id = :user_id)"),
            {"position_ids": position_ids, "user_id": user_id}
        ).scalar()
        if stray_positions > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_positions} positions link to portfolios outside user's scope"
            )
    
    # 7. Verify metrics: metrics.run_id must point to user's runs
    if metric_ids:
        stray_metrics = db_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id = ANY(:metric_ids) "
                 "AND run_id NOT IN (SELECT id FROM paper_trading_runs WHERE id = ANY(:run_ids))"),
            {"metric_ids": metric_ids, "run_ids": run_ids}
        ).scalar()
        if stray_metrics > 0:
            raise RuntimeError(
                f"Cleanup aborted: {stray_metrics} metrics link to runs outside user's scope"
            )
    
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


def delete_user_resources_in_transaction(db_session, user_id: str, resource_ids: dict):
    """Delete all user resources and user itself in a SINGLE transaction.
    
    Phase 2 of unified cleanup: all DELETEs in one transaction.
    Caller must commit.
    
    FK-safe deletion order:
    1. fills (depends on orders)
    2. orders (depends on portfolios, decisions)
    3. decisions (depends on portfolios)
    4. metrics (depends on runs)
    5. positions (depends on portfolios)
    6. runs (depends on portfolios)
    7. portfolios (depends on user)
    8. strategy_study_trades (depends on study_runs)
    9. strategy_study_runs (depends on user)
    10. user (last)
    
    Args:
        db_session: Database session (transaction not yet committed)
        user_id: User ID to delete
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
    
    # 1. Delete fills
    if fill_ids:
        db_session.execute(
            text("DELETE FROM paper_fills WHERE id = ANY(:fill_ids)"),
            {"fill_ids": fill_ids}
        )
    
    # 2. Delete orders
    if order_ids:
        db_session.execute(
            text("DELETE FROM paper_orders WHERE id = ANY(:order_ids)"),
            {"order_ids": order_ids}
        )
    
    # 3. Delete decisions
    if decision_ids:
        db_session.execute(
            text("DELETE FROM decision_records WHERE id = ANY(:decision_ids)"),
            {"decision_ids": decision_ids}
        )
    
    # 4. Delete metrics
    if metric_ids:
        db_session.execute(
            text("DELETE FROM paper_trading_daily_metrics WHERE id = ANY(:metric_ids)"),
            {"metric_ids": metric_ids}
        )
    
    # 5. Delete positions
    if position_ids:
        db_session.execute(
            text("DELETE FROM paper_positions WHERE id = ANY(:position_ids)"),
            {"position_ids": position_ids}
        )
    
    # 6. Delete runs
    if run_ids:
        db_session.execute(
            text("DELETE FROM paper_trading_runs WHERE id = ANY(:run_ids)"),
            {"run_ids": run_ids}
        )
    
    # 7. Delete portfolios
    if portfolio_ids:
        db_session.execute(
            text("DELETE FROM paper_portfolios WHERE id = ANY(:portfolio_ids)"),
            {"portfolio_ids": portfolio_ids}
        )
    
    # 8. Delete strategy study trades
    if study_run_ids:
        db_session.execute(
            text("DELETE FROM strategy_study_trades WHERE run_id = ANY(:study_run_ids)"),
            {"study_run_ids": study_run_ids}
        )
        
        # 9. Delete strategy study runs
        db_session.execute(
            text("DELETE FROM strategy_study_runs WHERE id = ANY(:study_run_ids)"),
            {"study_run_ids": study_run_ids}
        )
    
    # 10. Delete user LAST
    db_session.execute(
        text("DELETE FROM users WHERE id = :user_id"),
        {"user_id": user_id}
    )


def verify_zero_residual_readonly(verify_session, user_id: str, resource_ids: dict):
    """Verify per-table zero residuals using collected IDs on independent connection.
    
    Phase 3 of unified cleanup: READ-ONLY verification with new connection.
    NO DELETEs happen here. This is purely verification.
    
    Aggregates ALL residual errors before raising, so caller sees complete picture.
    
    Args:
        verify_session: Independent database session for verification (read-only)
        user_id: User ID to verify
        resource_ids: Dict from collect_user_resource_ids()
        
    Raises:
        RuntimeError: If any resources still exist (aggregated message)
    """
    portfolio_ids = resource_ids["portfolio_ids"]
    run_ids = resource_ids["run_ids"]
    order_ids = resource_ids["order_ids"]
    fill_ids = resource_ids["fill_ids"]
    decision_ids = resource_ids["decision_ids"]
    position_ids = resource_ids["position_ids"]
    metric_ids = resource_ids["metric_ids"]
    study_run_ids = resource_ids["study_run_ids"]
    
    residuals = []
    
    # Verify user deleted
    user_count = verify_session.execute(
        text("SELECT COUNT(*) FROM users WHERE id = :user_id"),
        {"user_id": user_id}
    ).scalar()
    if user_count != 0:
        residuals.append(f"user still exists")
    
    # Verify all portfolios deleted
    if portfolio_ids:
        portfolio_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_portfolios WHERE id = ANY(:portfolio_ids)"),
            {"portfolio_ids": portfolio_ids}
        ).scalar()
        if portfolio_count != 0:
            residuals.append(f"{portfolio_count} portfolios still exist")
    
    # Verify all runs deleted
    if run_ids:
        run_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = ANY(:run_ids)"),
            {"run_ids": run_ids}
        ).scalar()
        if run_count != 0:
            residuals.append(f"{run_count} runs still exist")
    
    # Verify all orders deleted
    if order_ids:
        order_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_orders WHERE id = ANY(:order_ids)"),
            {"order_ids": order_ids}
        ).scalar()
        if order_count != 0:
            residuals.append(f"{order_count} orders still exist")
    
    # Verify all fills deleted
    if fill_ids:
        fill_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_fills WHERE id = ANY(:fill_ids)"),
            {"fill_ids": fill_ids}
        ).scalar()
        if fill_count != 0:
            residuals.append(f"{fill_count} fills still exist")
    
    # Verify all decisions deleted
    if decision_ids:
        decision_count = verify_session.execute(
            text("SELECT COUNT(*) FROM decision_records WHERE id = ANY(:decision_ids)"),
            {"decision_ids": decision_ids}
        ).scalar()
        if decision_count != 0:
            residuals.append(f"{decision_count} decisions still exist")
    
    # Verify all positions deleted
    if position_ids:
        position_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_positions WHERE id = ANY(:position_ids)"),
            {"position_ids": position_ids}
        ).scalar()
        if position_count != 0:
            residuals.append(f"{position_count} positions still exist")
    
    # Verify all metrics deleted
    if metric_ids:
        metric_count = verify_session.execute(
            text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id = ANY(:metric_ids)"),
            {"metric_ids": metric_ids}
        ).scalar()
        if metric_count != 0:
            residuals.append(f"{metric_count} metrics still exist")
    
    # Verify all study runs deleted
    if study_run_ids:
        study_count = verify_session.execute(
            text("SELECT COUNT(*) FROM strategy_study_runs WHERE id = ANY(:study_run_ids)"),
            {"study_run_ids": study_run_ids}
        ).scalar()
        if study_count != 0:
            residuals.append(f"{study_count} study runs still exist")
    
    # Raise aggregated error if any residuals found
    if residuals:
        raise RuntimeError(
            f"Cleanup verification failed: {'; '.join(residuals)}"
        )


def unified_cleanup_user_data(session_factory, user_id: str):
    """Complete unified cleanup: collect, delete in single transaction, verify read-only.
    
    This is the main entry point for cleanup operations.
    
    Safety guarantees:
    1. Phase 1 (collect): Collect all IDs with comprehensive ownership verification
    2. Phase 2 (delete): All DELETEs including user in single transaction
    3. Phase 3 (verify): Read-only verification on independent connection
    
    Args:
        session_factory: Callable that creates new database sessions
        user_id: User ID to clean up
        
    Raises:
        RuntimeError: If ownership verification fails or residual data found
    """
    # Phase 1: Collect all IDs with ownership verification
    collect_session = session_factory()
    try:
        resource_ids = collect_user_resource_ids(collect_session, user_id)
    finally:
        collect_session.close()
    
    # If user doesn't exist, nothing to clean
    if not resource_ids["portfolio_ids"] and not resource_ids["study_run_ids"]:
        # Check if user exists at all
        check_session = session_factory()
        try:
            user_exists = check_session.execute(
                text("SELECT COUNT(*) FROM users WHERE id = :user_id"),
                {"user_id": user_id}
            ).scalar() > 0
        finally:
            check_session.close()
        
        if not user_exists:
            return  # User already deleted, nothing to clean
    
    # Phase 2: Delete ALL in single transaction (including user)
    delete_session = session_factory()
    try:
        delete_user_resources_in_transaction(delete_session, user_id, resource_ids)
        delete_session.commit()
    except Exception:
        delete_session.rollback()
        raise
    finally:
        delete_session.close()
    
    # Phase 3: Verify zero residuals with independent read-only connection
    verify_session = session_factory()
    try:
        verify_zero_residual_readonly(verify_session, user_id, resource_ids)
    finally:
        verify_session.close()
