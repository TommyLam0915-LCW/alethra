"""Real API login + replay with fixture-based cleanup and cost reconciliation.

Uses actual /auth/login endpoint (not create_access_token).
Integrates api_test_fixtures for guaranteed cleanup.

Requirements covered:
- Fix weak !=401 assertion -> == 200 on real protected endpoint
- Complete API cost reconciliation (login -> create -> replay -> status -> daily-report -> DB readback)
- Post-replay proactive rollback cleanup (confirm committed -> throw -> verify zero residuals by saved IDs)
- Precise ID snapshots: verify each table by saved IDs, not parent subqueries
- Deterministic timestamps for reproducible business verification
- Cent tolerance for monetary comparisons, 5-digit for Decimal arithmetic

Environment:
- Database: PostgreSQL ai_investment_lab_test (ALETHRA_TEST_DB / ALETHRA_PG_TEST_URL)
- Runtime role: alethra_test_runtime (limited permissions)
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from tests.api_test_fixtures import api_test_fixtures
from tests.conftest import TestSession


FIXED_NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
FIXED_5_DAYS_AGO = FIXED_NOW - timedelta(days=5)
FIXED_3_DAYS_AGO = FIXED_NOW - timedelta(days=3)
FIXED_5_DAYS_AGO_1H = FIXED_5_DAYS_AGO + timedelta(hours=1)
FIXED_3_DAYS_AGO_1H = FIXED_3_DAYS_AGO + timedelta(hours=1)

CENT_TOL = 0.01
DECIMAL_TOL = 0.00001


def _calculate_expected_replay():
    """Independently calculate expected replay results from fixture source data.

    Uses the same formulas as PaperTradingEngine.execute_historical_replay()
    but implemented separately to avoid coupling to production code.
    All arithmetic uses Decimal to match engine precision.

    Source data (from api_test_fixtures):
    - Trade 1: entry_price=100, exit_price=110, direction=long
    - Trade 2: entry_price=100, exit_price=90, direction=long

    Replay parameters:
    - initial_capital=100000, position_size_pct=0.10, max_positions=3
    - commission_rate=0.001 (0.1%), slippage_rate=0.0005 (0.05%)
    """
    initial_capital = Decimal("100000")
    position_size_pct = Decimal("0.1")
    commission_rate = Decimal("0.001")
    slippage_rate = Decimal("0.0005")

    source_trades = [
        {"entry_price": Decimal("100"), "exit_price": Decimal("110")},
        {"entry_price": Decimal("100"), "exit_price": Decimal("90")},
    ]

    available_cash = initial_capital
    position_value = Decimal("0")
    trades = []

    for trade in source_trades:
        total_equity = available_cash + position_value
        pos_value = total_equity * position_size_pct
        qty = pos_value / trade["entry_price"]
        entry_comm = pos_value * commission_rate
        entry_slip = pos_value * slippage_rate
        entry_costs = entry_comm + entry_slip

        available_cash -= pos_value + entry_costs
        position_value += pos_value

        gross_pnl = qty * (trade["exit_price"] - trade["entry_price"])
        exit_gross = qty * trade["exit_price"]
        exit_comm = exit_gross * commission_rate
        exit_slip = exit_gross * slippage_rate
        exit_costs = exit_comm + exit_slip
        net_pnl = gross_pnl - entry_costs - exit_costs

        position_cost = qty * trade["entry_price"]
        available_cash += position_cost + gross_pnl - exit_costs
        position_value -= position_cost

        trades.append({
            "quantity": qty,
            "position_value": pos_value,
            "entry_commission": entry_comm,
            "entry_slippage": entry_slip,
            "exit_commission": exit_comm,
            "exit_slippage": exit_slip,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "entry_price": trade["entry_price"],
            "exit_price": trade["exit_price"],
        })

    final_capital = available_cash + position_value
    total_pnl = final_capital - initial_capital
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    win_rate = (Decimal(wins) / Decimal(len(trades)) * 100) if trades else Decimal("0")

    return {
        "total_trades": len(trades),
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "final_capital": final_capital,
        "trades": trades,
    }


def _capture_all_ids(portfolio_id, run_id):
    """Capture all resource IDs created during replay for precise verification."""
    db = TestSession()
    try:
        order_ids = [r[0] for r in db.execute(
            text("SELECT id FROM paper_orders WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).fetchall()]

        fill_ids = [r[0] for r in db.execute(
            text("SELECT id FROM paper_fills WHERE paper_order_id = ANY(:oids)"),
            {"oids": order_ids},
        ).fetchall()] if order_ids else []

        decision_ids = [r[0] for r in db.execute(
            text("SELECT id FROM decision_records WHERE paper_portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).fetchall()]

        position_ids = [r[0] for r in db.execute(
            text("SELECT id FROM paper_positions WHERE portfolio_id = :pid"),
            {"pid": portfolio_id},
        ).fetchall()]

        metric_ids = [r[0] for r in db.execute(
            text("SELECT id FROM paper_trading_daily_metrics WHERE run_id = :rid"),
            {"rid": run_id},
        ).fetchall()]

        return {
            "portfolio_id": portfolio_id,
            "run_id": run_id,
            "order_ids": order_ids,
            "fill_ids": fill_ids,
            "decision_ids": decision_ids,
            "position_ids": position_ids,
            "metric_ids": metric_ids,
        }
    finally:
        db.close()


def _verify_zero_residual_by_ids(saved_ids, user_email, user_id):
    """Verify zero residuals using saved IDs directly (not parent subqueries)."""
    verify_db = TestSession()
    try:
        user_count = verify_db.execute(
            text("SELECT COUNT(*) FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).scalar()
        assert user_count == 0, f"User {user_id} still exists"

        pf_count = verify_db.execute(
            text("SELECT COUNT(*) FROM paper_portfolios WHERE id = :pid"),
            {"pid": saved_ids["portfolio_id"]},
        ).scalar()
        assert pf_count == 0, f"Portfolio {saved_ids['portfolio_id']} still exists"

        run_count = verify_db.execute(
            text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = :rid"),
            {"rid": saved_ids["run_id"]},
        ).scalar()
        assert run_count == 0, f"Run {saved_ids['run_id']} still exists"

        if saved_ids["order_ids"]:
            ord_count = verify_db.execute(
                text("SELECT COUNT(*) FROM paper_orders WHERE id = ANY(:oids)"),
                {"oids": saved_ids["order_ids"]},
            ).scalar()
            assert ord_count == 0, f"{ord_count} orders still exist"

        if saved_ids["fill_ids"]:
            fill_count = verify_db.execute(
                text("SELECT COUNT(*) FROM paper_fills WHERE id = ANY(:fids)"),
                {"fids": saved_ids["fill_ids"]},
            ).scalar()
            assert fill_count == 0, f"{fill_count} fills still exist"

        if saved_ids["decision_ids"]:
            dec_count = verify_db.execute(
                text("SELECT COUNT(*) FROM decision_records WHERE id = ANY(:dids)"),
                {"dids": saved_ids["decision_ids"]},
            ).scalar()
            assert dec_count == 0, f"{dec_count} decisions still exist"

        if saved_ids["position_ids"]:
            pos_count = verify_db.execute(
                text("SELECT COUNT(*) FROM paper_positions WHERE id = ANY(:pids)"),
                {"pids": saved_ids["position_ids"]},
            ).scalar()
            assert pos_count == 0, f"{pos_count} positions still exist"

        if saved_ids["metric_ids"]:
            met_count = verify_db.execute(
                text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id = ANY(:mids)"),
                {"mids": saved_ids["metric_ids"]},
            ).scalar()
            assert met_count == 0, f"{met_count} metrics still exist"

        study_count = verify_db.execute(
            text("SELECT COUNT(*) FROM strategy_study_runs WHERE user_id = :uid"),
            {"uid": user_id},
        ).scalar()
        assert study_count == 0, f"{study_count} study runs still exist"
    finally:
        verify_db.close()


class TestRealAPILoginWithFixture:
    """Real login endpoint + replay with fixture cleanup and cost reconciliation."""

    def test_login_endpoint_returns_valid_token(self, client):
        """Prove /auth/login works with fixture-created user.

        Fix: replaced weak != 401 assertion with == 200 on a real protected
        endpoint that requires valid authentication.
        """
        test_email = f"login_test_{uuid4().hex[:8]}@example.com"

        with api_test_fixtures(test_email) as (auth_headers, user_id, run_id):
            login_resp = client.post(
                "/api/auth/login",
                json={"email": test_email, "password": "testpass123"}
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
            login_data = login_resp.json()

            assert "access_token" in login_data
            assert "token_type" in login_data
            assert login_data["token_type"] == "bearer"
            token = login_data["access_token"]
            assert token.count('.') == 2, "Token should be valid JWT format"

            real_headers = {"Authorization": f"Bearer {token}"}
            create_resp = client.post(
                "/api/v1/paper-trading/create",
                headers=real_headers,
                json={
                    "skill": "test_rule",
                    "instrument_id": "TEST/USD",
                    "interval_minutes": 60,
                    "exit_config": "default",
                    "initial_capital": 100000.0,
                },
            )
            assert create_resp.status_code == 200, (
                f"Real login token rejected by protected endpoint: "
                f"{create_resp.status_code} {create_resp.json()}"
            )

    def test_full_api_chain_with_cost_reconciliation(self, client):
        """Full API chain with independent cost reconciliation.

        Chain: login -> create -> replay -> status -> daily-report -> DB readback

        Verifies:
        - Replay response matches independently calculated expected values
        - Status endpoint returns consistent data
        - Daily report shows correct trade counts
        - DB readback confirms per-trade costs, cash conservation, positions
        - All verifications use saved IDs, not parent subqueries
        """
        test_email = f"full_chain_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()

        with api_test_fixtures(test_email) as (auth_headers, user_id, study_run_id):
            login_resp = client.post(
                "/api/auth/login",
                json={"email": test_email, "password": "testpass123"}
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
            real_token = login_resp.json()["access_token"]
            real_headers = {"Authorization": f"Bearer {real_token}"}

            create_resp = client.post(
                "/api/v1/paper-trading/create",
                headers=real_headers,
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
            portfolio_id = create_data["portfolio_id"]
            run_id = create_data["run_id"]

            replay_resp = client.post(
                f"/api/v1/paper-trading/replay/{run_id}",
                headers=real_headers,
                json={
                    "days_back": 30,
                    "position_size_pct": 0.10,
                    "max_positions": 3,
                },
            )
            assert replay_resp.status_code == 200, f"Replay failed: {replay_resp.json()}"
            replay_data = replay_resp.json()

            assert replay_data["status"] == "completed"
            assert replay_data["total_trades"] == expected["total_trades"]
            assert abs(replay_data["total_pnl"] - float(expected["total_pnl"])) < CENT_TOL
            assert abs(replay_data["win_rate"] - float(expected["win_rate"])) < DECIMAL_TOL
            assert abs(replay_data["final_capital"] - float(expected["final_capital"])) < CENT_TOL

            status_resp = client.get(
                f"/api/v1/paper-trading/status/{run_id}",
                headers=real_headers,
            )
            assert status_resp.status_code == 200
            status_data = status_resp.json()
            assert status_data["run_id"] == run_id
            assert status_data["status"] == "completed"
            assert status_data["total_trades"] == expected["total_trades"]
            assert abs(status_data["total_pnl"] - float(expected["total_pnl"])) < CENT_TOL
            assert abs(status_data["current_capital"] - float(expected["final_capital"])) < CENT_TOL
            assert abs(status_data["win_rate"] - float(expected["win_rate"])) < DECIMAL_TOL

            trade1_exit_date = FIXED_5_DAYS_AGO_1H.date()
            report_resp = client.get(
                f"/api/v1/paper-trading/daily-report/{run_id}"
                f"?report_date={trade1_exit_date.isoformat()}",
                headers=real_headers,
            )
            assert report_resp.status_code == 200
            report_data = report_resp.json()
            assert report_data["run_id"] == run_id
            assert report_data["trades"]["count"] >= 1
            assert abs(report_data["portfolio"]["starting_capital"] - 100000.0) < CENT_TOL

            saved_ids = _capture_all_ids(portfolio_id, run_id)

            db = TestSession()
            try:
                run_row = db.execute(
                    text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                    {"id": run_id},
                ).fetchone()
                assert run_row is not None
                assert run_row.status == "completed"
                assert run_row.total_trades == expected["total_trades"]
                assert abs(float(run_row.total_pnl) - float(expected["total_pnl"])) < CENT_TOL
                assert abs(float(run_row.win_rate) - float(expected["win_rate"])) < DECIMAL_TOL

                portfolio_row = db.execute(
                    text("SELECT * FROM paper_portfolios WHERE id = :id"),
                    {"id": portfolio_id},
                ).fetchone()
                assert portfolio_row is not None
                assert abs(
                    float(portfolio_row.current_virtual_cash) - float(expected["final_capital"])
                ) < CENT_TOL

                assert len(saved_ids["fill_ids"]) == 4
                fills = db.execute(
                    text("SELECT * FROM paper_fills WHERE id = ANY(:fids)"),
                    {"fids": saved_ids["fill_ids"]},
                ).fetchall()
                assert len(fills) == 4

                entry_fills = sorted(
                    [f for f in fills if f.simulated_fill_reason == 'entry_signal'],
                    key=lambda f: f.bar_timestamp,
                )
                exit_fills = sorted(
                    [f for f in fills if f.simulated_fill_reason == 'exit_signal'],
                    key=lambda f: f.bar_timestamp,
                )
                assert len(entry_fills) == 2
                assert len(exit_fills) == 2

                for i, trade_exp in enumerate(expected["trades"]):
                    ef = entry_fills[i]
                    assert abs(float(ef.commission_amount) - float(trade_exp["entry_commission"])) < CENT_TOL
                    assert abs(float(ef.slippage_amount) - float(trade_exp["entry_slippage"])) < CENT_TOL

                for i, trade_exp in enumerate(expected["trades"]):
                    xf = exit_fills[i]
                    assert abs(float(xf.commission_amount) - float(trade_exp["exit_commission"])) < CENT_TOL
                    assert abs(float(xf.slippage_amount) - float(trade_exp["exit_slippage"])) < CENT_TOL

                assert len(saved_ids["position_ids"]) == 2
                positions = db.execute(
                    text("SELECT * FROM paper_positions WHERE id = ANY(:pids)"),
                    {"pids": saved_ids["position_ids"]},
                ).fetchall()
                assert len(positions) == 2
                for pos in positions:
                    assert pos.status == "closed"
                    assert pos.realized_pnl is not None

                pos_pnls = sorted([float(p.realized_pnl) for p in positions])
                expected_pnls = sorted([float(t["net_pnl"]) for t in expected["trades"]])
                for actual_pnl, exp_pnl in zip(pos_pnls, expected_pnls):
                    assert abs(actual_pnl - exp_pnl) < CENT_TOL

                assert len(saved_ids["metric_ids"]) >= 1
                metrics = db.execute(
                    text("SELECT * FROM paper_trading_daily_metrics WHERE id = ANY(:mids)"),
                    {"mids": saved_ids["metric_ids"]},
                ).fetchall()
                total_metrics_trades = sum(m.trades_count for m in metrics)
                assert total_metrics_trades == expected["total_trades"]

                assert len(saved_ids["decision_ids"]) == 4
                decisions = db.execute(
                    text("SELECT COUNT(*) FROM decision_records WHERE id = ANY(:dids)"),
                    {"dids": saved_ids["decision_ids"]},
                ).scalar()
                assert decisions == 4

                assert len(saved_ids["order_ids"]) == 4
                orders = db.execute(
                    text("SELECT COUNT(*) FROM paper_orders WHERE id = ANY(:oids)"),
                    {"oids": saved_ids["order_ids"]},
                ).scalar()
                assert orders == 4
            finally:
                db.close()

    def test_post_replay_proactive_rollback_cleanup(self, client):
        """Proactive rollback: confirm committed -> throw -> verify cleanup by saved IDs.

        1. Run full API chain (login -> create -> replay)
        2. Confirm data committed via independent DB read (new connection)
        3. Capture ALL resource IDs (orders, fills, decisions, positions, metrics)
        4. Intentionally throw error inside fixture context
        5. Fixture catches error, runs cleanup, re-raises
        6. Verify zero residuals per table using saved IDs (not parent subqueries)
        """
        test_email = f"rollback_cleanup_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()
        captured = {
            "run_id": None, "portfolio_id": None, "user_id": None,
            "saved_ids": None,
        }

        try:
            with api_test_fixtures(test_email) as (auth_headers, user_id, study_run_id):
                captured["user_id"] = user_id

                login_resp = client.post(
                    "/api/auth/login",
                    json={"email": test_email, "password": "testpass123"}
                )
                assert login_resp.status_code == 200
                real_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

                create_resp = client.post(
                    "/api/v1/paper-trading/create",
                    headers=real_headers,
                    json={
                        "skill": "test_rule",
                        "instrument_id": "TEST/USD",
                        "interval_minutes": 60,
                        "exit_config": "default",
                        "initial_capital": 100000.0,
                    },
                )
                assert create_resp.status_code == 200
                captured["run_id"] = create_resp.json()["run_id"]
                captured["portfolio_id"] = create_resp.json()["portfolio_id"]

                replay_resp = client.post(
                    f"/api/v1/paper-trading/replay/{captured['run_id']}",
                    headers=real_headers,
                    json={"days_back": 30, "position_size_pct": 0.10, "max_positions": 3},
                )
                assert replay_resp.status_code == 200
                assert replay_resp.json()["status"] == "completed"
                assert replay_resp.json()["total_trades"] == expected["total_trades"]

                verify_db = TestSession()
                try:
                    run_row = verify_db.execute(
                        text("SELECT status, total_trades FROM paper_trading_runs WHERE id = :id"),
                        {"id": captured["run_id"]},
                    ).fetchone()
                    assert run_row is not None
                    assert run_row.status == "completed"
                    assert run_row.total_trades == expected["total_trades"]
                finally:
                    verify_db.close()

                captured["saved_ids"] = _capture_all_ids(
                    captured["portfolio_id"], captured["run_id"]
                )
                assert len(captured["saved_ids"]["fill_ids"]) == 4
                assert len(captured["saved_ids"]["order_ids"]) == 4
                assert len(captured["saved_ids"]["position_ids"]) == 2
                assert len(captured["saved_ids"]["decision_ids"]) == 4

                raise RuntimeError("Intentional post-replay error for rollback testing")

        except RuntimeError as exc:
            if "Intentional post-replay error" not in str(exc):
                raise
        except ExceptionGroup as eg:
            pytest.fail(f"Cleanup failed after intentional error: {eg}")

        assert captured["saved_ids"] is not None
        _verify_zero_residual_by_ids(
            captured["saved_ids"], test_email, captured["user_id"]
        )
