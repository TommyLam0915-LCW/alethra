"""Real API login + replay with fixture-based cleanup and cost reconciliation.

Uses actual /auth/login endpoint (not create_access_token).
Integrates api_test_fixtures for guaranteed cleanup.

Requirements covered:
- Fix weak !=401 assertion -> == 200 on real protected endpoint
- Complete API cost reconciliation (login -> create -> replay -> status -> daily-report -> DB readback)
- Post-replay failure cleanup (confirm committed -> throw -> verify zero residuals)

Environment:
- Database: PostgreSQL ai_investment_lab_test (ALETHRA_TEST_DB)
- Runtime role: alethra_test_runtime (limited permissions)
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from tests.api_test_fixtures import api_test_fixtures
from tests.conftest import TestSession


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
        # --- Entry ---
        total_equity = available_cash + position_value
        pos_value = total_equity * position_size_pct
        qty = pos_value / trade["entry_price"]
        entry_comm = pos_value * commission_rate
        entry_slip = pos_value * slippage_rate
        entry_costs = entry_comm + entry_slip

        available_cash -= pos_value + entry_costs
        position_value += pos_value

        # --- Exit ---
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
        """
        test_email = f"full_chain_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()

        with api_test_fixtures(test_email) as (auth_headers, user_id, study_run_id):
            # Step 1: Real login
            login_resp = client.post(
                "/api/auth/login",
                json={"email": test_email, "password": "testpass123"}
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
            real_token = login_resp.json()["access_token"]
            real_headers = {"Authorization": f"Bearer {real_token}"}

            # Step 2: Create portfolio via API
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

            # Step 3: Execute replay
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

            # Step 3a: Verify replay response vs independently calculated expected
            assert replay_data["status"] == "completed"
            assert replay_data["total_trades"] == expected["total_trades"], (
                f"total_trades: got {replay_data['total_trades']}, "
                f"expected {expected['total_trades']}"
            )
            assert abs(replay_data["total_pnl"] - float(expected["total_pnl"])) < 0.01, (
                f"total_pnl: got {replay_data['total_pnl']}, "
                f"expected {float(expected['total_pnl'])}"
            )
            assert abs(replay_data["win_rate"] - float(expected["win_rate"])) < 0.01, (
                f"win_rate: got {replay_data['win_rate']}, "
                f"expected {float(expected['win_rate'])}"
            )
            assert abs(replay_data["final_capital"] - float(expected["final_capital"])) < 0.01, (
                f"final_capital: got {replay_data['final_capital']}, "
                f"expected {float(expected['final_capital'])}"
            )

            # Step 4: Status API call
            status_resp = client.get(
                f"/api/v1/paper-trading/status/{run_id}",
                headers=real_headers,
            )
            assert status_resp.status_code == 200, f"Status failed: {status_resp.json()}"
            status_data = status_resp.json()
            assert status_data["run_id"] == run_id
            assert status_data["status"] == "completed"
            assert status_data["skill"] == "test_rule"
            assert status_data["instrument_id"] == "TEST/USD"
            assert status_data["total_trades"] == expected["total_trades"]
            assert abs(status_data["total_pnl"] - float(expected["total_pnl"])) < 0.01
            assert abs(status_data["current_capital"] - float(expected["final_capital"])) < 0.01
            assert abs(status_data["win_rate"] - float(expected["win_rate"])) < 0.01
            assert abs(status_data["initial_capital"] - 100000.0) < 0.01

            # Step 5: Daily report API call
            now = datetime.now(timezone.utc)
            trade1_exit_date = (now - timedelta(days=5) + timedelta(hours=1)).date()
            report_resp = client.get(
                f"/api/v1/paper-trading/daily-report/{run_id}"
                f"?report_date={trade1_exit_date.isoformat()}",
                headers=real_headers,
            )
            assert report_resp.status_code == 200, f"Daily report failed: {report_resp.json()}"
            report_data = report_resp.json()
            assert report_data["run_id"] == run_id
            assert report_data["skill"] == "test_rule"
            assert report_data["instrument_id"] == "TEST/USD"
            assert report_data["trades"]["count"] >= 1, "Expected at least 1 trade on day 1"
            assert report_data["trades"]["wins"] >= 1, "Trade 1 should be a win"
            assert abs(report_data["portfolio"]["starting_capital"] - 100000.0) < 0.01

            # Step 6: Independent DB readback (new connection, not engine's)
            db = TestSession()
            try:
                # 6a: Verify run row
                run_row = db.execute(
                    text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                    {"id": run_id},
                ).fetchone()
                assert run_row is not None, "Run not found in DB"
                assert run_row.status == "completed"
                assert run_row.total_trades == expected["total_trades"]
                assert abs(float(run_row.total_pnl) - float(expected["total_pnl"])) < 0.01
                assert abs(float(run_row.win_rate) - float(expected["win_rate"])) < 0.01

                # 6b: Verify portfolio cash
                portfolio_row = db.execute(
                    text("SELECT * FROM paper_portfolios WHERE id = :id"),
                    {"id": portfolio_id},
                ).fetchone()
                assert portfolio_row is not None, "Portfolio not found in DB"
                assert abs(
                    float(portfolio_row.current_virtual_cash) - float(expected["final_capital"])
                ) < 0.01

                # 6c: Verify paper fills (4 total: 2 entry + 2 exit)
                fills = db.execute(
                    text("SELECT * FROM paper_fills WHERE paper_order_id IN "
                         "(SELECT id FROM paper_orders WHERE portfolio_id = :pid)"),
                    {"pid": portfolio_id},
                ).fetchall()
                assert len(fills) == 4, f"Expected 4 fills, got {len(fills)}"

                entry_fills = sorted(
                    [f for f in fills if f.simulated_fill_reason == 'entry_signal'],
                    key=lambda f: f.bar_timestamp,
                )
                exit_fills = sorted(
                    [f for f in fills if f.simulated_fill_reason == 'exit_signal'],
                    key=lambda f: f.bar_timestamp,
                )
                assert len(entry_fills) == 2, f"Expected 2 entry fills, got {len(entry_fills)}"
                assert len(exit_fills) == 2, f"Expected 2 exit fills, got {len(exit_fills)}"

                # 6d: Per-trade cost verification (entry fills)
                for i, trade_exp in enumerate(expected["trades"]):
                    ef = entry_fills[i]
                    assert abs(float(ef.commission_amount) - float(trade_exp["entry_commission"])) < 0.01, (
                        f"Trade {i} entry commission: got {float(ef.commission_amount)}, "
                        f"expected {float(trade_exp['entry_commission'])}"
                    )
                    assert abs(float(ef.slippage_amount) - float(trade_exp["entry_slippage"])) < 0.01, (
                        f"Trade {i} entry slippage: got {float(ef.slippage_amount)}, "
                        f"expected {float(trade_exp['entry_slippage'])}"
                    )

                # 6e: Per-trade cost verification (exit fills)
                for i, trade_exp in enumerate(expected["trades"]):
                    xf = exit_fills[i]
                    assert abs(float(xf.commission_amount) - float(trade_exp["exit_commission"])) < 0.01, (
                        f"Trade {i} exit commission: got {float(xf.commission_amount)}, "
                        f"expected {float(trade_exp['exit_commission'])}"
                    )
                    assert abs(float(xf.slippage_amount) - float(trade_exp["exit_slippage"])) < 0.01, (
                        f"Trade {i} exit slippage: got {float(xf.slippage_amount)}, "
                        f"expected {float(trade_exp['exit_slippage'])}"
                    )

                # 6f: Verify paper positions (2, both closed)
                positions = db.execute(
                    text("SELECT * FROM paper_positions WHERE portfolio_id = :pid"),
                    {"pid": portfolio_id},
                ).fetchall()
                assert len(positions) == 2, f"Expected 2 positions, got {len(positions)}"
                for pos in positions:
                    assert pos.status == "closed", f"Position {pos.id} not closed: {pos.status}"
                    assert pos.realized_pnl is not None, f"Position {pos.id} has no realized_pnl"

                pos_pnls = sorted([float(p.realized_pnl) for p in positions])
                expected_pnls = sorted([float(t["net_pnl"]) for t in expected["trades"]])
                for actual_pnl, exp_pnl in zip(pos_pnls, expected_pnls):
                    assert abs(actual_pnl - exp_pnl) < 0.01, (
                        f"Position PnL mismatch: {actual_pnl} vs expected {exp_pnl}"
                    )

                # 6g: Cash conservation (independent of engine's reconciliation)
                assert abs(float(run_row.total_pnl) - float(expected["total_pnl"])) < 0.01
                assert abs(
                    float(portfolio_row.current_virtual_cash) - float(expected["final_capital"])
                ) < 0.01

                # 6h: Daily metrics exist and sum to correct totals
                metrics = db.execute(
                    text("SELECT * FROM paper_trading_daily_metrics WHERE run_id = :id"),
                    {"id": run_id},
                ).fetchall()
                assert len(metrics) >= 1, "No daily metrics found"
                total_metrics_trades = sum(m.trades_count for m in metrics)
                assert total_metrics_trades == expected["total_trades"], (
                    f"Daily metrics trade count: got {total_metrics_trades}, "
                    f"expected {expected['total_trades']}"
                )

                # 6i: Decision records (2 entry + 2 exit = 4)
                decisions = db.execute(
                    text("SELECT COUNT(*) FROM decision_records WHERE paper_portfolio_id = :pid"),
                    {"pid": portfolio_id},
                ).scalar()
                assert decisions == 4, f"Expected 4 decision records, got {decisions}"

                # 6j: Paper orders (2 entry + 2 exit = 4)
                orders = db.execute(
                    text("SELECT COUNT(*) FROM paper_orders WHERE portfolio_id = :pid"),
                    {"pid": portfolio_id},
                ).scalar()
                assert orders == 4, f"Expected 4 paper orders, got {orders}"

            finally:
                db.close()

    def test_post_replay_failure_cleanup(self, client):
        """Post-replay failure cleanup: confirm committed -> throw -> verify cleanup.

        1. Run full API chain (login -> create -> replay)
        2. Confirm data committed via independent DB read (new connection)
        3. Intentionally throw error inside fixture context
        4. Fixture catches error, runs cleanup, re-raises
        5. Verify zero residuals per table after cleanup
        """
        test_email = f"failure_cleanup_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()
        captured = {"run_id": None, "portfolio_id": None, "user_id": None}

        try:
            with api_test_fixtures(test_email) as (auth_headers, user_id, study_run_id):
                captured["user_id"] = user_id

                # Real login
                login_resp = client.post(
                    "/api/auth/login",
                    json={"email": test_email, "password": "testpass123"}
                )
                assert login_resp.status_code == 200
                real_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

                # Create portfolio
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

                # Execute replay
                replay_resp = client.post(
                    f"/api/v1/paper-trading/replay/{captured['run_id']}",
                    headers=real_headers,
                    json={"days_back": 30, "position_size_pct": 0.10, "max_positions": 3},
                )
                assert replay_resp.status_code == 200
                assert replay_resp.json()["status"] == "completed"
                assert replay_resp.json()["total_trades"] == expected["total_trades"]

                # Confirm data committed via independent DB read (new connection)
                verify_db = TestSession()
                try:
                    run_row = verify_db.execute(
                        text("SELECT status, total_trades FROM paper_trading_runs WHERE id = :id"),
                        {"id": captured["run_id"]},
                    ).fetchone()
                    assert run_row is not None, "Run not found after replay commit"
                    assert run_row.status == "completed"
                    assert run_row.total_trades == expected["total_trades"]

                    fill_count = verify_db.execute(
                        text("SELECT COUNT(*) FROM paper_fills WHERE paper_order_id IN "
                             "(SELECT id FROM paper_orders WHERE portfolio_id = :pid)"),
                        {"pid": captured["portfolio_id"]},
                    ).scalar()
                    assert fill_count == 4, f"Expected 4 fills committed, got {fill_count}"

                    pos_count = verify_db.execute(
                        text("SELECT COUNT(*) FROM paper_positions WHERE portfolio_id = :pid"),
                        {"pid": captured["portfolio_id"]},
                    ).scalar()
                    assert pos_count == 2, f"Expected 2 positions committed, got {pos_count}"
                finally:
                    verify_db.close()

                # Intentionally throw error to test cleanup path
                raise RuntimeError("Intentional post-replay error for cleanup testing")

        except RuntimeError as exc:
            if "Intentional post-replay error" not in str(exc):
                raise
        except ExceptionGroup as eg:
            pytest.fail(f"Cleanup failed after intentional error: {eg}")

        # Verify zero residuals after cleanup (independent connection)
        verify_db = TestSession()
        try:
            user_count = verify_db.execute(
                text("SELECT COUNT(*) FROM users WHERE email = :email"),
                {"email": test_email},
            ).scalar()
            assert user_count == 0, f"User not cleaned up: count={user_count}"

            if captured["portfolio_id"]:
                portfolio_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM paper_portfolios WHERE id = :pid"),
                    {"pid": captured["portfolio_id"]},
                ).scalar()
                assert portfolio_count == 0, (
                    f"Portfolio not cleaned up: count={portfolio_count}"
                )

            if captured["run_id"]:
                run_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM paper_trading_runs WHERE id = :id"),
                    {"id": captured["run_id"]},
                ).scalar()
                assert run_count == 0, f"Run not cleaned up: count={run_count}"

            if captured["portfolio_id"]:
                fill_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM paper_fills WHERE paper_order_id IN "
                         "(SELECT id FROM paper_orders WHERE portfolio_id = :pid)"),
                    {"pid": captured["portfolio_id"]},
                ).scalar()
                assert fill_count == 0, f"Fills not cleaned up: count={fill_count}"

                pos_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM paper_positions WHERE portfolio_id = :pid"),
                    {"pid": captured["portfolio_id"]},
                ).scalar()
                assert pos_count == 0, f"Positions not cleaned up: count={pos_count}"

                decision_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM decision_records WHERE paper_portfolio_id = :pid"),
                    {"pid": captured["portfolio_id"]},
                ).scalar()
                assert decision_count == 0, f"Decisions not cleaned up: count={decision_count}"

            if captured["run_id"]:
                metric_count = verify_db.execute(
                    text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :id"),
                    {"id": captured["run_id"]},
                ).scalar()
                assert metric_count == 0, f"Metrics not cleaned up: count={metric_count}"

            study_count = verify_db.execute(
                text("SELECT COUNT(*) FROM strategy_study_runs WHERE user_id = :uid"),
                {"uid": captured["user_id"]},
            ).scalar()
            assert study_count == 0, f"Study runs not cleaned up: count={study_count}"

        finally:
            verify_db.close()
