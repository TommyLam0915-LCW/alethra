"""Test service owner verification prevents cross-user operations.

This test verifies that user B cannot operate on user A's paper trading run.
Security requirement: service must verify portfolio.owner_id == user_id.
"""

import pytest
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from app.database import engine as db_engine, SessionLocal
from app.services.paper_trading import PaperTradingEngine


@pytest.fixture
def test_users():
    """Create two test users (A and B)."""
    db = SessionLocal()
    try:
        user_a_id = str(uuid4())
        user_b_id = str(uuid4())
        
        # Create user A
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, TRUE, FALSE)
            """),
            {
                "id": user_a_id,
                "email": "user_a@test.com",
                "password": "hashed_password_a",
            }
        )
        
        # Create user B
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, TRUE, FALSE)
            """),
            {
                "id": user_b_id,
                "email": "user_b@test.com",
                "password": "hashed_password_b",
            }
        )
        
        db.commit()
        yield user_a_id, user_b_id
    finally:
        # Cleanup
        db.execute(text("DELETE FROM users WHERE id IN (:id_a, :id_b)"), {"id_a": user_a_id, "id_b": user_b_id})
        db.commit()
        db.close()


@pytest.fixture
def seed_strategy_study_data(test_users):
    """Seed strategy_study_runs and strategy_study_trades for user A."""
    user_a_id, _ = test_users
    db = SessionLocal()
    try:
        # Create strategy study run for user A
        study_run_id = str(uuid4())
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
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 1, :now
                )
            """),
            {
                "id": study_run_id,
                "user_id": user_a_id,
                "now": now,
            }
        )

        # Seed one trade for user A
        trade_id = str(uuid4())
        entry_time = now - timedelta(days=5)
        exit_time = now - timedelta(days=5) + timedelta(hours=1)
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
                    100.0, 110.0, 100.0, 1000.0, 0.0, 0.0, 0.0, 1000.0, 101000.0, 1,
                    'Profit from price increase', 'Profit from price increase', :now
                )
            """),
            {
                "id": trade_id,
                "run_id": study_run_id,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "now": now,
            }
        )

        db.commit()
        yield study_run_id, trade_id
    finally:
        db.execute(text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"), {"run_id": study_run_id})
        db.execute(text("DELETE FROM strategy_study_runs WHERE id = :id"), {"id": study_run_id})
        db.commit()
        db.close()


class TestOwnerVerification:
    """Verify service enforces owner_id checks."""

    def test_user_b_cannot_execute_user_a_run(self, test_users, seed_strategy_study_data):
        """User B attempts to execute user A's run - must be rejected with no side effects."""
        user_a_id, user_b_id = test_users
        study_run_id, _ = seed_strategy_study_data
        
        # Create portfolio and run for user A
        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=user_a_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=Decimal("100000.00"),
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]
        
        # Verify run is in 'queued' status
        db = SessionLocal()
        try:
            run_before = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run_before.status == "queued"
            
            # Get portfolio cash before attempt
            portfolio_before = db.execute(
                text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :id"),
                {"id": portfolio_id}
            ).fetchone()
            cash_before = Decimal(str(portfolio_before.current_virtual_cash))
        finally:
            db.close()
        
        # User B attempts to execute user A's run - should be rejected
        with pytest.raises(ValueError) as exc_info:
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=user_b_id,  # Wrong user!
                days_back=30,
                position_size_pct=0.10,
            )
        
        # Verify error message mentions authorization
        assert "not authorized" in str(exc_info.value).lower()
        
        # Verify NO side effects: status unchanged, cash unchanged, no records created
        db = SessionLocal()
        try:
            # Status should still be 'queued'
            run_after = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id}
            ).fetchone()
            assert run_after.status == "queued", "Status should not change on unauthorized attempt"
            
            # Cash should be unchanged
            portfolio_after = db.execute(
                text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :id"),
                {"id": portfolio_id}
            ).fetchone()
            cash_after = Decimal(str(portfolio_after.current_virtual_cash))
            assert cash_after == cash_before, "Cash should not change on unauthorized attempt"
            
            # No orders should be created
            order_count = db.execute(
                text("SELECT COUNT(*) FROM paper_orders WHERE portfolio_id = :id"),
                {"id": portfolio_id}
            ).scalar()
            assert order_count == 0, "No orders should be created on unauthorized attempt"
            
            # No fills should be created
            fill_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM paper_fills f
                    JOIN paper_orders o ON f.paper_order_id = o.id
                    WHERE o.portfolio_id = :id
                """),
                {"id": portfolio_id}
            ).scalar()
            assert fill_count == 0, "No fills should be created on unauthorized attempt"
        finally:
            db.close()
        
        # Cleanup
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id = :id"), {"id": run_id})
            db.execute(text("DELETE FROM paper_fills WHERE paper_order_id IN (SELECT id FROM paper_orders WHERE portfolio_id = :id)"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_orders WHERE portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_positions WHERE portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM decision_records WHERE paper_portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
            db.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
            db.commit()
        finally:
            db.close()

    def test_user_a_can_execute_own_run(self, test_users, seed_strategy_study_data):
        """User A executes their own run - should succeed."""
        user_a_id, _ = test_users
        study_run_id, _ = seed_strategy_study_data
        
        # Create portfolio and run for user A
        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=user_a_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=Decimal("100000.00"),
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]
        
        # User A executes their own run - should succeed
        replay_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=user_a_id,  # Correct user!
            days_back=30,
            position_size_pct=0.10,
        )
        
        # Verify execution succeeded
        assert replay_result["status"] == "completed"
        assert replay_result["total_trades"] >= 0
        
        # Cleanup
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id = :id"), {"id": run_id})
            db.execute(text("DELETE FROM paper_fills WHERE paper_order_id IN (SELECT id FROM paper_orders WHERE portfolio_id = :id)"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_orders WHERE portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_positions WHERE portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM decision_records WHERE paper_portfolio_id = :id"), {"id": portfolio_id})
            db.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
            db.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
            db.commit()
        finally:
            db.close()
