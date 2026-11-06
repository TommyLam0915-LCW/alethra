"""Retry and failure validation tests.

Verifies:
1. After successful replay, retry attempts have no side effects
2. Failed replay can be retried and produces complete ledger
3. Transaction rollback on failure prevents partial ledger
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import text

from app.database import SessionLocal, engine as db_engine
from app.services.paper_trading import PaperTradingEngine


@pytest.fixture
def test_user_id():
    """Create a test user and return ID."""
    db = SessionLocal()
    try:
        user_id = str(uuid4())
        db.execute(
            text("""
                INSERT INTO users (id, email, password_hash, created_at, updated_at, is_active, is_admin)
                VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, true, false)
            """),
            {"id": user_id, "email": f"retry_{user_id[:8]}@test.local", "password": "test_hash"}
        )
        db.commit()
        yield user_id
    finally:
        # FK-safe cleanup
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
            text("DELETE FROM decision_records WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_positions WHERE portfolio_id IN (
                    SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_trading_daily_metrics WHERE run_id IN (
                    SELECT r.id FROM paper_trading_runs r
                    JOIN paper_portfolios p ON r.portfolio_id = p.id
                    WHERE p.owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("""
                DELETE FROM paper_trading_runs WHERE portfolio_id IN (
                    SELECT id FROM paper_portfolios WHERE owner_id = :user_id
                )
            """),
            {"user_id": user_id},
        )
        db.execute(
            text("DELETE FROM paper_portfolios WHERE owner_id = :user_id"),
            {"user_id": user_id},
        )
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        db.commit()
        db.close()


@pytest.fixture
def seed_retry_trades(test_user_id):
    """Seed trades for retry testing."""
    db = SessionLocal()
    try:
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
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 2, :now
                )
            """),
            {"id": study_run_id, "user_id": test_user_id, "now": now},
        )

        # Seed 2 trades
        for i in range(2):
            trade_id = str(uuid4())
            entry_time = now - timedelta(days=5 - i * 2)
            exit_time = entry_time + timedelta(hours=1)
            entry_price = 100.0
            exit_price = 110.0 if i == 0 else 90.0

            db.execute(
                text("""
                    INSERT INTO strategy_study_trades (
                        id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                        direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                        entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                        total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                    ) VALUES (
                        :id, :run_id, :idx, 'test_rule', 'Test Rule', 'test_exit', 'Test Exit',
                        'long', 'signal', :outcome, :entry_time, :entry_time, :exit_time,
                        :entry_price, :exit_price, 100.0, :gross, 0.0, 0.0, 0.0, :net, 100000.0, 1,
                        'Trade', 'Trade', :now
                    )
                """),
                {
                    "id": trade_id,
                    "run_id": study_run_id,
                    "idx": i,
                    "outcome": "win" if i == 0 else "loss",
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross": Decimal(str(100 * (exit_price - entry_price))),
                    "net": Decimal(str(100 * (exit_price - entry_price))),
                    "now": now,
                },
            )

        db.commit()
        yield study_run_id
    finally:
        db.execute(
            text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"),
            {"run_id": study_run_id},
        )
        db.execute(
            text("DELETE FROM strategy_study_runs WHERE id = :id"),
            {"id": study_run_id},
        )
        db.commit()
        db.close()


def _snapshot_db_state(portfolio_id: str, run_id: str) -> dict:
    """Capture current DB state for comparison."""
    db = SessionLocal()
    try:
        run = db.execute(
            text("SELECT status, total_pnl, total_trades, final_capital FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id},
        ).fetchone()

        portfolio = db.execute(
            text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :id"),
            {"id": portfolio_id},
        ).fetchone()

        fill_count = db.execute(
            text("""
                SELECT COUNT(*) FROM paper_fills f
                JOIN paper_orders o ON f.paper_order_id = o.id
                WHERE o.portfolio_id = :portfolio_id
            """),
            {"portfolio_id": portfolio_id},
        ).scalar()

        position_count = db.execute(
            text("SELECT COUNT(*) FROM paper_positions WHERE portfolio_id = :portfolio_id"),
            {"portfolio_id": portfolio_id},
        ).scalar()

        metric_count = db.execute(
            text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :run_id"),
            {"run_id": run_id},
        ).scalar()

        return {
            "status": run.status,
            "total_pnl": float(run.total_pnl) if run.total_pnl else 0,
            "total_trades": run.total_trades,
            "final_capital": float(run.final_capital) if run.final_capital else 0,
            "current_cash": float(portfolio.current_virtual_cash),
            "fill_count": fill_count,
            "position_count": position_count,
            "metric_count": metric_count,
        }
    finally:
        db.close()


class TestRetryAndFailure:
    """Retry and failure validation tests."""

    def test_retry_after_success_has_no_side_effects(self, test_user_id, seed_retry_trades):
        """After successful replay, retry attempts are rejected with no state change."""
        study_run_id = seed_retry_trades
        initial_capital = Decimal("100000.00")

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # First replay: should succeed
        first_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )
        assert first_result["status"] == "completed"

        # Snapshot DB state after first replay
        snapshot_after_first = _snapshot_db_state(portfolio_id, run_id)

        # Second replay: should be rejected
        with pytest.raises(ValueError, match="already completed"):
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # Third replay: should also be rejected
        with pytest.raises(ValueError, match="already completed"):
            engine.execute_historical_replay(
                run_id=run_id,
                user_id=test_user_id,
                days_back=30,
            )

        # Snapshot DB state after retries
        snapshot_after_retries = _snapshot_db_state(portfolio_id, run_id)

        # Verify no changes
        assert snapshot_after_first == snapshot_after_retries, (
            f"DB state changed after retries: {snapshot_after_first} != {snapshot_after_retries}"
        )

    def test_failed_replay_can_be_retried(self, test_user_id, seed_retry_trades):
        """A failed replay can be retried and produces complete ledger."""
        study_run_id = seed_retry_trades
        initial_capital = Decimal("100000.00")

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Manually set status to 'failed' to simulate a previous failure
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET status = 'failed' WHERE id = :id"),
                {"id": run_id},
            )
            db.commit()
        finally:
            db.close()

        # Retry should succeed
        retry_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        assert retry_result["status"] == "completed"
        assert retry_result["total_trades"] > 0

        # Verify DB state is complete
        db_state = _snapshot_db_state(portfolio_id, run_id)
        assert db_state["status"] == "completed"
        assert db_state["fill_count"] > 0
        assert db_state["position_count"] > 0
        assert db_state["metric_count"] > 0

    def test_injected_failure_causes_rollback(self, test_user_id, seed_retry_trades):
        """Inject a failure during replay and verify rollback prevents partial ledger."""
        study_run_id = seed_retry_trades
        initial_capital = Decimal("100000.00")

        engine = PaperTradingEngine(database_url=str(db_engine.url))
        result = engine.create_portfolio(
            user_id=test_user_id,
            skill="test_rule",
            instrument_id="TEST/USD",
            interval_minutes=60,
            exit_config="default",
            initial_capital=initial_capital,
        )
        portfolio_id = result["portfolio_id"]
        run_id = result["run_id"]

        # Inject a failure by mocking db.execute to raise an error
        original_execute = None
        failure_injected = False
        
        def failing_execute(*args, **kwargs):
            # Let the first few calls succeed (status UPDATE, trade SELECT)
            # Then fail on a later call (e.g., when writing fills)
            nonlocal failure_injected
            failing_execute.call_count = getattr(failing_execute, 'call_count', 0) + 1
            
            # Only fail once, on the 6th call (after atomic UPDATE succeeds)
            if failing_execute.call_count == 6 and not failure_injected:
                failure_injected = True
                raise RuntimeError("Injected failure for testing")
            
            return original_execute(*args, **kwargs)

        # Patch SessionLocal to inject failure
        with patch('app.services.paper_trading.SessionLocal') as mock_session_local:
            mock_db = SessionLocal()
            original_execute = mock_db.execute
            mock_db.execute = failing_execute
            mock_session_local.return_value = mock_db

            # Attempt replay: should fail
            with pytest.raises(RuntimeError, match="Injected failure"):
                engine.execute_historical_replay(
                    run_id=run_id,
                    user_id=test_user_id,
                    days_back=30,
                )

        # Verify run status is 'failed'
        db = SessionLocal()
        try:
            run = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :id"),
                {"id": run_id},
            ).fetchone()
            assert run.status == "failed", f"Run should be 'failed', got {run.status}"

            # Verify no partial fills were committed
            fill_count = db.execute(
                text("""
                    SELECT COUNT(*) FROM paper_fills f
                    JOIN paper_orders o ON f.paper_order_id = o.id
                    WHERE o.portfolio_id = :portfolio_id
                """),
                {"portfolio_id": portfolio_id},
            ).scalar()
            assert fill_count == 0, f"Should have 0 fills after rollback, got {fill_count}"

        finally:
            db.close()

        # Retry should now succeed and produce complete ledger
        retry_result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=test_user_id,
            days_back=30,
        )

        assert retry_result["status"] == "completed"
        assert retry_result["total_trades"] > 0

        # Verify complete ledger
        db_state = _snapshot_db_state(portfolio_id, run_id)
        assert db_state["status"] == "completed"
        assert db_state["fill_count"] > 0
        assert db_state["position_count"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
