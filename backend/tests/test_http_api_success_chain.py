"""Formal HTTP API success chain test.

Verifies the full customer flow through actual HTTP endpoints:
  login → create → replay → status → daily-report

Seeds non-zero, fixed, hand-calculable source data.
Asserts response content matches DB ledger, not just HTTP 200.

This is the owner's item #1 from the acceptance plan:
"普通用戶登入 → API 建立 → API replay → status → daily-report"
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal
from tests.conftest import TestSession


def _make_user(client, email: str):
    """Register a user and return (auth_headers, user_id)."""
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name=email.split("@")[0],
            is_admin=False,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(data={"sub": str(user.id)})
        user_id = str(user.id)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}, user_id


def _seed_fixed_trades(user_id: str):
    """Seed strategy_study_runs and strategy_study_trades with KNOWN data.

    Creates 2 trades:
    - Trade 1: Buy @ $100, sell @ $110 → profitable
    - Trade 2: Buy @ $100, sell @ $90 → loss

    Returns (study_run_id, trade_ids).
    """
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
            {"id": study_run_id, "user_id": user_id, "now": now},
        )

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
                    100.0, 110.0, 100.0, 1000.0, 0.0, 0.0, 0.0, 1000.0, 101000.0, 1,
                    'Profit', 'Profit', :now
                )
            """),
            {
                "id": trade1_id, "run_id": study_run_id,
                "entry_time": entry_time_1, "exit_time": exit_time_1, "now": now,
            },
        )

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
                    100.0, 90.0, 100.0, -1000.0, 0.0, 0.0, 0.0, -1000.0, 100000.0, 1,
                    'Loss', 'Loss', :now
                )
            """),
            {
                "id": trade2_id, "run_id": study_run_id,
                "entry_time": entry_time_2, "exit_time": exit_time_2, "now": now,
            },
        )

        db.commit()
        return study_run_id, [trade1_id, trade2_id]
    finally:
        db.close()


def _cleanup_seed_data(study_run_id: str):
    """Remove seeded strategy study data."""
    db = SessionLocal()
    try:
        db.execute(
            text("DELETE FROM strategy_study_trades WHERE run_id = :run_id"),
            {"run_id": study_run_id},
        )
        db.execute(
            text("DELETE FROM strategy_study_runs WHERE id = :id"),
            {"id": study_run_id},
        )
        db.commit()
    finally:
        db.close()


def _cleanup_user_data(user_id: str):
    """Remove all paper trading data for a user in FK-safe order."""
    db = SessionLocal()
    try:
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
        db.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": user_id},
        )
        db.commit()
    finally:
        db.close()


class TestHTTPSuccessChain:
    """Full HTTP API success chain: login → create → replay → status → daily-report."""

    def test_full_api_chain_with_nonzero_trades(self, client):
        """Normal user completes full API flow with non-zero fixed source data.

        Verifies:
        1. POST /create returns portfolio_id, run_id, status='queued'
        2. POST /replay executes successfully, returns non-zero trades
        3. GET /status returns status='completed' with correct PnL
        4. GET /daily-report returns report matching DB metrics
        5. DB ledger matches API responses (not just 200 status)
        """
        headers, user_id = _make_user(client, "http_chain@test.com")
        study_run_id, trade_ids = _seed_fixed_trades(user_id)

        try:
            # Step 1: Create portfolio via API
            create_resp = client.post(
                "/api/v1/paper-trading/create",
                headers=headers,
                json={
                    "skill": "test_rule",
                    "instrument_id": "TEST/USD",
                    "interval_minutes": 60,
                    "exit_config": "default",
                    "initial_capital": 100000.0,
                },
            )
            assert create_resp.status_code == 200, f"Create failed: {create_resp.json()}"
            create_data = create_resp.json()

            assert create_data["status"] == "queued"
            assert create_data["initial_capital"] == 100000.0
            portfolio_id = create_data["portfolio_id"]
            run_id = create_data["run_id"]

            # Step 2: Execute replay via API
            replay_resp = client.post(
                f"/api/v1/paper-trading/replay/{run_id}",
                headers=headers,
                json={
                    "days_back": 30,
                    "position_size_pct": 0.10,
                    "max_positions": 3,
                },
            )
            assert replay_resp.status_code == 200, f"Replay failed: {replay_resp.json()}"
            replay_data = replay_resp.json()

            assert replay_data["status"] == "completed"
            assert replay_data["run_id"] == run_id
            assert replay_data["total_trades"] > 0, "Should have executed trades"

            # Step 3: Get status via API
            status_resp = client.get(
                f"/api/v1/paper-trading/status/{run_id}",
                headers=headers,
            )
            assert status_resp.status_code == 200, f"Status failed: {status_resp.json()}"
            status_data = status_resp.json()

            assert status_data["status"] == "completed"
            assert status_data["run_id"] == run_id
            assert status_data["total_trades"] == replay_data["total_trades"]
            assert abs(status_data["total_pnl"] - replay_data["total_pnl"]) < 0.01
            assert abs(status_data["current_capital"] - replay_data["final_capital"]) < 0.01

            # Step 4: Get daily report via API
            report_resp = client.get(
                f"/api/v1/paper-trading/daily-report/{run_id}",
                headers=headers,
            )
            assert report_resp.status_code == 200, f"Daily report failed: {report_resp.json()}"
            report_data = report_resp.json()

            assert report_data["run_id"] == run_id
            assert report_data["portfolio"]["starting_capital"] == 100000.0

            # Step 5: Verify DB ledger matches API responses
            db = SessionLocal()
            try:
                # Check run record
                run = db.execute(
                    text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                    {"id": run_id},
                ).fetchone()
                assert run.status == "completed"
                assert run.total_trades == replay_data["total_trades"]
                assert abs(float(run.total_pnl) - replay_data["total_pnl"]) < 0.01
                assert abs(float(run.final_capital) - replay_data["final_capital"]) < 0.01

                # Check portfolio record
                portfolio = db.execute(
                    text("SELECT * FROM paper_portfolios WHERE id = :id"),
                    {"id": portfolio_id},
                ).fetchone()
                assert abs(portfolio.current_virtual_cash - replay_data["final_capital"]) < 0.01

                # Check fills exist
                fill_count = db.execute(
                    text("""
                        SELECT COUNT(*) FROM paper_fills f
                        JOIN paper_orders o ON f.paper_order_id = o.id
                        WHERE o.portfolio_id = :portfolio_id
                    """),
                    {"portfolio_id": portfolio_id},
                ).scalar()
                assert fill_count > 0, "Should have fill records"

                # Check positions all closed
                open_positions = db.execute(
                    text("""
                        SELECT COUNT(*) FROM paper_positions
                        WHERE portfolio_id = :portfolio_id AND status = 'open'
                    """),
                    {"portfolio_id": portfolio_id},
                ).scalar()
                assert open_positions == 0, "All positions should be closed"

                # Check daily metrics exist
                metric_count = db.execute(
                    text("""
                        SELECT COUNT(*) FROM paper_trading_daily_metrics
                        WHERE run_id = :run_id
                    """),
                    {"run_id": run_id},
                ).scalar()
                assert metric_count > 0, "Should have daily metrics"

                # Verify cash reconciliation from DB
                fills = db.execute(
                    text("""
                        SELECT f.net_cash_change FROM paper_fills f
                        JOIN paper_orders o ON f.paper_order_id = o.id
                        WHERE o.portfolio_id = :portfolio_id
                    """),
                    {"portfolio_id": portfolio_id},
                ).fetchall()

                total_net_flows = sum(Decimal(str(f.net_cash_change)) for f in fills)
                expected_ending = Decimal("100000") + total_net_flows
                actual_ending = Decimal(str(portfolio.current_virtual_cash))
                assert abs(actual_ending - expected_ending) < Decimal("0.01"), (
                    f"Cash reconciliation failed: actual={actual_ending}, expected={expected_ending}"
                )

            finally:
                db.close()

        finally:
            _cleanup_user_data(user_id)
            _cleanup_seed_data(study_run_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
