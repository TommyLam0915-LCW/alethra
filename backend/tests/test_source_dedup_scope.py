"""Test source dedup scope: run-scoped, not table-wide.

Verifies that:
1. Same paper run cannot duplicate the same source trade (prevents double-counting)
2. Different paper runs can each use the same source trade (correct behavior)

This tests the fix in migration 006 that changed UNIQUE(source_trade_id) to
UNIQUE(paper_run_id, source_trade_id) for decision_records and
UNIQUE(portfolio_id, source_trade_id) for paper_positions.
"""

import pytest
from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine as db_engine, SessionLocal
from app.services.paper_trading import PaperTradingEngine


@pytest.fixture
def seed_source_trade():
    """Seed a strategy_study_run with one trade to use as source."""
    db = SessionLocal()
    try:
        user_id = str(uuid4())
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, TRUE, FALSE)
            """),
            {"id": user_id, "email": "source_user@test.com", "password": "hashed"}
        )
        
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
            {"id": study_run_id, "user_id": user_id, "now": now}
        )
        
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
                    'Profit', 'Profit', :now
                )
            """),
            {"id": trade_id, "run_id": study_run_id, "entry_time": entry_time, "exit_time": exit_time, "now": now}
        )
        
        db.commit()
        yield user_id, study_run_id, trade_id
    finally:
        db = SessionLocal()
        try:
            # Cleanup in FK-safe order
            db.execute(text("DELETE FROM decision_records WHERE owner_id = :id"), {"id": user_id})
            db.execute(text("DELETE FROM paper_fills WHERE paper_order_id IN (SELECT id FROM paper_orders WHERE portfolio_id IN (SELECT id FROM paper_portfolios WHERE owner_id = :id))"), {"id": user_id})
            db.execute(text("DELETE FROM paper_orders WHERE portfolio_id IN (SELECT id FROM paper_portfolios WHERE owner_id = :id)"), {"id": user_id})
            db.execute(text("DELETE FROM paper_positions WHERE portfolio_id IN (SELECT id FROM paper_portfolios WHERE owner_id = :id)"), {"id": user_id})
            db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id IN (SELECT id FROM paper_trading_runs WHERE portfolio_id IN (SELECT id FROM paper_portfolios WHERE owner_id = :id))"), {"id": user_id})
            db.execute(text("DELETE FROM paper_trading_runs WHERE portfolio_id IN (SELECT id FROM paper_portfolios WHERE owner_id = :id)"), {"id": user_id})
            db.execute(text("DELETE FROM paper_portfolios WHERE owner_id = :id"), {"id": user_id})
            db.execute(text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"), {"run_id": study_run_id})
            db.execute(text("DELETE FROM strategy_study_runs WHERE id = :id"), {"id": study_run_id})
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            db.commit()
        finally:
            db.close()


class TestSourceDedupScope:
    """Verify source dedup is run-scoped, not table-wide."""
    
    def test_same_run_cannot_duplicate_source_trade(self, seed_source_trade):
        """Same paper run cannot create two decision_records with same source_trade_id."""
        user_id, study_run_id, source_trade_id = seed_source_trade
        
        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=Decimal("100000.00"),
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]
        
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            decision_id_1 = str(uuid4())
            decision_id_2 = str(uuid4())
            
            # Insert first decision_record with source_trade_id
            db.execute(
                text("""
                    INSERT INTO decision_records (
                        id, owner_id, paper_portfolio_id, actor_type, decision_type,
                        decision_time, source_trade_id, source_run_id, outcome_status
                    ) VALUES (
                        :id, :owner_id, :portfolio_id, 'system', 'entry',
                        :now, :source_trade_id, :source_run_id, 'pending'
                    )
                """),
                {
                    "id": decision_id_1,
                    "owner_id": user_id,
                    "portfolio_id": portfolio_id,
                    "now": now,
                    "source_trade_id": source_trade_id,
                    "source_run_id": study_run_id,
                }
            )
            db.commit()
            
            # Attempt to insert second decision_record with same source_trade_id and source_run_id
            # Should fail with unique constraint violation
            with pytest.raises(IntegrityError) as exc_info:
                db.execute(
                    text("""
                        INSERT INTO decision_records (
                            id, owner_id, paper_portfolio_id, actor_type, decision_type,
                            decision_time, source_trade_id, source_run_id, outcome_status
                        ) VALUES (
                            :id, :owner_id, :portfolio_id, 'system', 'entry',
                            :now, :source_trade_id, :source_run_id, 'pending'
                        )
                    """),
                    {
                        "id": decision_id_2,
                        "owner_id": user_id,
                        "portfolio_id": portfolio_id,
                        "now": now,
                        "source_trade_id": source_trade_id,
                        "source_run_id": study_run_id,
                    }
                )
                db.commit()
            
            # Verify it's the expected constraint violation
            assert "uq_decision_records_source_per_portfolio" in str(exc_info.value)
        finally:
            # Cleanup - rollback first to clear transaction state, then use fresh session
            try:
                db.rollback()
            except:
                pass
            db.close()
            db = SessionLocal()
            try:
                db.execute(text("DELETE FROM decision_records WHERE id = :id"), {"id": decision_id_1})
                db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id = :id"), {"id": run_id})
                db.execute(text("DELETE FROM paper_trading_runs WHERE id = :id"), {"id": run_id})
                db.execute(text("DELETE FROM paper_portfolios WHERE id = :id"), {"id": portfolio_id})
                db.commit()
            finally:
                db.close()
    
    def test_different_runs_can_use_same_source_trade(self, seed_source_trade):
        """Different paper runs can each use the same source_trade_id."""
        user_id, study_run_id, source_trade_id = seed_source_trade
        
        engine = PaperTradingEngine(database_url=str(db_engine.url))
        
        # Create two separate portfolios/runs for the same user
        result_1 = engine.create_portfolio(
            user_id=user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=Decimal("100000.00"),
        )
        portfolio_id_1 = result_1["portfolio_id"]
        run_id_1 = result_1["run_id"]
        
        result_2 = engine.create_portfolio(
            user_id=user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=Decimal("100000.00"),
        )
        portfolio_id_2 = result_2["portfolio_id"]
        run_id_2 = result_2["run_id"]
        
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            decision_id_1 = str(uuid4())
            decision_id_2 = str(uuid4())
            
            # Insert decision_record for run 1 with source_trade_id
            db.execute(
                text("""
                    INSERT INTO decision_records (
                        id, owner_id, paper_portfolio_id, actor_type, decision_type,
                        decision_time, source_trade_id, source_run_id, outcome_status
                    ) VALUES (
                        :id, :owner_id, :portfolio_id, 'system', 'entry',
                        :now, :source_trade_id, :source_run_id, 'pending'
                    )
                """),
                {
                    "id": decision_id_1,
                    "owner_id": user_id,
                    "portfolio_id": portfolio_id_1,
                    "now": now,
                    "source_trade_id": source_trade_id,
                    "source_run_id": study_run_id,
                }
            )
            db.commit()
            
            # Insert decision_record for run 2 with SAME source_trade_id
            # Should succeed because different run (different portfolio)
            db.execute(
                text("""
                    INSERT INTO decision_records (
                        id, owner_id, paper_portfolio_id, actor_type, decision_type,
                        decision_time, source_trade_id, source_run_id, outcome_status
                    ) VALUES (
                        :id, :owner_id, :portfolio_id, 'system', 'entry',
                        :now, :source_trade_id, :source_run_id, 'pending'
                    )
                """),
                {
                    "id": decision_id_2,
                    "owner_id": user_id,
                    "portfolio_id": portfolio_id_2,
                    "now": now,
                    "source_trade_id": source_trade_id,
                    "source_run_id": study_run_id,
                }
            )
            db.commit()
            
            # Verify both decision_records exist
            count = db.execute(
                text("SELECT COUNT(*) FROM decision_records WHERE source_trade_id = :id"),
                {"id": source_trade_id}
            ).scalar()
            assert count == 2, f"Should have 2 decision_records with same source_trade_id, got {count}"
        finally:
            # Cleanup - use fresh session to avoid transaction state issues
            try:
                db.rollback()
            except:
                pass
            db.close()
            db = SessionLocal()
            try:
                db.execute(text("DELETE FROM decision_records WHERE id IN (:id1, :id2)"), {"id1": decision_id_1, "id2": decision_id_2})
                db.execute(text("DELETE FROM paper_trading_daily_metrics WHERE run_id IN (:id1, :id2)"), {"id1": run_id_1, "id2": run_id_2})
                db.execute(text("DELETE FROM paper_trading_runs WHERE id IN (:id1, :id2)"), {"id1": run_id_1, "id2": run_id_2})
                db.execute(text("DELETE FROM paper_portfolios WHERE id IN (:id1, :id2)"), {"id1": portfolio_id_1, "id2": portfolio_id_2})
                db.commit()
            finally:
                db.close()
