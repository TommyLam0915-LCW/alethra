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

from tests import sql_membership, uuid_identity
from tests.api_test_fixtures import api_test_fixtures, seed_schedule
from tests.conftest import TestSession


FIXED_NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)

# The two dates the fixture's seeded trades exit on, read off the same clock
# the fixture is seeded with. Previously the fixture wrote rows at the wall
# clock while this file asserted a bucket derived from FIXED_NOW, so the
# reported count for the asserted date was whatever the calendar happened to
# say — 0 since 2026-11-09, and the assertion absorbed it.
SEED = seed_schedule(FIXED_NOW)
TRADE1_BUCKET = SEED[0]["exit_date"]
TRADE2_BUCKET = SEED[1]["exit_date"]
# A date inside the replayed range with no exit on it, so a zero here is the
# filter working rather than a window that selected nothing at all.
EMPTY_BUCKET = TRADE1_BUCKET + timedelta(days=1)

# days_back choices, derived from the seeded ages. Entries sit on whole days
# behind FIXED_NOW, so the cutoff lands exactly on an entry instead of near it:
#   TRADE1_AGE + 1  both trades strictly inside
#   TRADE1_AGE      cutoff == trade1's entry, the inclusive edge
#   TRADE1_AGE - 1  trade1 outside, trade2 inside
#   TRADE2_AGE - 1  both outside
TRADE1_AGE = (FIXED_NOW - SEED[0]["entry"]).days
TRADE2_AGE = (FIXED_NOW - SEED[1]["entry"]).days
IN_WINDOW_DAYS = TRADE1_AGE + 1
AT_CUTOFF_DAYS = TRADE1_AGE
ONE_SHORT_DAYS = TRADE1_AGE - 1
ALL_OUT_DAYS = TRADE2_AGE - 1

CENT_TOL = 0.01
DECIMAL_TOL = 0.00001


class _PlainDatetimeChecks(type):
    """Make a datetime subclass answer isinstance() like datetime itself."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, datetime)

    def __subclasscheck__(cls, obj):
        return issubclass(obj, datetime)


class _FrozenDatetime(datetime, metaclass=_PlainDatetimeChecks):
    """datetime, except that ``now()`` returns the declared clock.

    Subclassing rather than substituting a duck-typed stand-in matters here:
    ``app/services/paper_trading.py`` calls ``isinstance(x, datetime)`` and
    ``datetime.fromisoformat()`` on the way in, and a replacement that only
    implemented ``now`` would flip those checks and change which timestamps
    get normalised. The metaclass keeps a plain datetime passing the check.
    """

    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW

    @classmethod
    def fromisoformat(cls, value):
        """Parse with the real class, so nothing downstream gets this subclass.

        ``_as_utc`` rebuilds the ISO text SQLite hands back for a timestamp
        column through ``datetime.fromisoformat``, and the result is later
        bound into another statement. sqlite3's default adapter matches the
        exact type, so a subclass instance raises
        ``type '_FrozenDatetime' is not supported`` — the clock is pinned, the
        type of the values is not.
        """
        return datetime.fromisoformat(value)


class frozen_replay_clock:
    """Pin the replay window to ``FIXED_NOW`` for the duration of a block.

    ``execute_historical_replay`` derives both edges of its window from the
    wall clock (``cutoff = now - days_back``, ``as_of = now``) while the rows it
    selects are seeded from this file's declared clock. Two clocks is what made
    "which trades are in the window" unanswerable, which is U8. Only the
    module that cuts the window is pinned; every other timestamp in the
    request still comes from the real clock.
    """

    def __enter__(self):
        from app.services import paper_trading as service

        self._service = service
        self._original = service.datetime
        service.datetime = _FrozenDatetime
        return _FrozenDatetime

    def __exit__(self, *exc_info):
        self._service.datetime = self._original
        return False


def _login_and_create(client, email):
    """Real login + portfolio create; returns (headers, portfolio_id, run_id)."""
    login_resp = client.post(
        "/api/auth/login", json={"email": email, "password": "testpass123"}
    )
    assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
    headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

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
    return headers, create_data["portfolio_id"], create_data["run_id"]


def _replay(client, headers, run_id, days_back):
    resp = client.post(
        f"/api/v1/paper-trading/replay/{run_id}",
        headers=headers,
        json={
            "days_back": days_back,
            "position_size_pct": 0.10,
            "max_positions": 3,
        },
    )
    assert resp.status_code == 200, f"Replay failed: {resp.json()}"
    return resp.json()


def _report(client, headers, run_id, on_date):
    resp = client.get(
        f"/api/v1/paper-trading/daily-report/{run_id}?report_date={on_date.isoformat()}",
        headers=headers,
    )
    assert resp.status_code == 200, f"Daily report failed: {resp.json()}"
    return resp.json()


def _seeded_trade_count(study_run_id):
    """How many source trades the fixture really wrote for this study run.

    An out-of-window verdict is only evidence if the rows were there to be
    excluded; asking the database keeps that from being an assumption.
    """
    clause, params = uuid_identity.match("run_id", study_run_id, name="srid")
    db = TestSession()
    try:
        return db.execute(
            text(f"SELECT COUNT(*) FROM strategy_study_trades WHERE {clause}"), params
        ).scalar()
    finally:
        db.close()


def _calculate_expected_replay(kept=(0, 1)):
    """Independently calculate expected replay results from fixture source data.

    Uses the same formulas as PaperTradingEngine.execute_historical_replay()
    but implemented separately to avoid coupling to production code.
    All arithmetic uses Decimal to match engine precision.

    ``kept`` selects which of the two seeded trades the replay window is
    expected to admit; the window tests assert against the same arithmetic with
    a different subset instead of re-deriving it inline.

    Source data (from api_test_fixtures):
    - Trade 0: entry_price=100, exit_price=110, direction=long
    - Trade 1: entry_price=100, exit_price=90, direction=long

    Replay parameters:
    - initial_capital=100000, position_size_pct=0.10, max_positions=3
    - commission_rate=0.001 (0.1%), slippage_rate=0.0005 (0.05%)
    """
    initial_capital = Decimal("100000")
    position_size_pct = Decimal("0.1")
    commission_rate = Decimal("0.001")
    slippage_rate = Decimal("0.0005")

    all_source_trades = [
        {"entry_price": Decimal("100"), "exit_price": Decimal("110")},
        {"entry_price": Decimal("100"), "exit_price": Decimal("90")},
    ]
    source_trades = [all_source_trades[i] for i in kept]

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
            sql_membership.list_sql(
                "SELECT id FROM paper_fills WHERE paper_order_id IN :oids",
                name="oids",
                values=order_ids,
            )
        ).fetchall()]

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
    """Verify zero residuals using saved IDs directly (not parent subqueries).

    Every predicate here runs unconditionally. The saved collections can be
    empty — and when they are, an ``if saved_ids[...]`` guard makes the check
    pass without reading a row, so the guard is dropped rather than inherited:
    an empty list under ``IN`` matches nothing and reports zero, which is the
    same verdict but is one the database actually gave. The owner-scoped count
    at the end is the falsifiable half: it asks the database for anything this
    user still owns, whether or not the snapshots captured a single id.
    """
    verify_db = TestSession()
    try:
        uid_clause, uid_params = uuid_identity.match("id", user_id, name="uid")
        owner_clause, owner_params = uuid_identity.match("owner_id", user_id, name="uid")
        study_clause, _ = uuid_identity.match("user_id", user_id, name="uid")

        user_count = verify_db.execute(
            text(f"SELECT COUNT(*) FROM users WHERE {uid_clause}"), uid_params
        ).scalar()
        assert user_count == 0, f"User {user_id} still exists"

        pf_clause, pf_params = uuid_identity.match(
            "id", saved_ids["portfolio_id"], name="pid"
        )
        pf_count = verify_db.execute(
            text(f"SELECT COUNT(*) FROM paper_portfolios WHERE {pf_clause}"), pf_params
        ).scalar()
        assert pf_count == 0, f"Portfolio {saved_ids['portfolio_id']} still exists"

        run_clause, run_params = uuid_identity.match(
            "id", saved_ids["run_id"], name="rid"
        )
        run_count = verify_db.execute(
            text(f"SELECT COUNT(*) FROM paper_trading_runs WHERE {run_clause}"), run_params
        ).scalar()
        assert run_count == 0, f"Run {saved_ids['run_id']} still exists"

        ord_count = verify_db.execute(
            sql_membership.list_sql(
                "SELECT COUNT(*) FROM paper_orders WHERE id IN :oids",
                name="oids",
                values=saved_ids["order_ids"],
            )
        ).scalar()
        assert ord_count == 0, f"{ord_count} orders still exist"

        fill_count = verify_db.execute(
            sql_membership.list_sql(
                "SELECT COUNT(*) FROM paper_fills WHERE id IN :fids",
                name="fids",
                values=saved_ids["fill_ids"],
            )
        ).scalar()
        assert fill_count == 0, f"{fill_count} fills still exist"

        dec_count = verify_db.execute(
            sql_membership.list_sql(
                "SELECT COUNT(*) FROM decision_records WHERE id IN :dids",
                name="dids",
                values=saved_ids["decision_ids"],
            )
        ).scalar()
        assert dec_count == 0, f"{dec_count} decisions still exist"

        pos_count = verify_db.execute(
            sql_membership.list_sql(
                "SELECT COUNT(*) FROM paper_positions WHERE id IN :pids",
                name="pids",
                values=saved_ids["position_ids"],
            )
        ).scalar()
        assert pos_count == 0, f"{pos_count} positions still exist"

        met_count = verify_db.execute(
            sql_membership.list_sql(
                "SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE id IN :mids",
                name="mids",
                values=saved_ids["metric_ids"],
            )
        ).scalar()
        assert met_count == 0, f"{met_count} metrics still exist"

        study_count = verify_db.execute(
            text(f"SELECT COUNT(*) FROM strategy_study_runs WHERE {study_clause}"),
            uid_params,
        ).scalar()
        assert study_count == 0, f"{study_count} study runs still exist"

        for table, column in (
            ("paper_orders", "portfolio_id"),
            ("paper_fills", "paper_order_id"),
            ("decision_records", "paper_portfolio_id"),
            ("paper_positions", "portfolio_id"),
            ("paper_trading_runs", "portfolio_id"),
        ):
            left = verify_db.execute(
                text(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {column} IN (SELECT id FROM paper_portfolios "
                    f"WHERE {owner_clause})"
                ),
                owner_params,
            ).scalar()
            assert left == 0, (
                f"{left} {table} rows still reference this user's portfolios, "
                f"whether or not the id snapshot captured them"
            )
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

        with api_test_fixtures(test_email, now=FIXED_NOW) as (auth_headers, user_id, run_id):
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

        with api_test_fixtures(test_email, now=FIXED_NOW) as (auth_headers, user_id, study_run_id), frozen_replay_clock():
            real_headers, portfolio_id, run_id = _login_and_create(client, test_email)

            # IN_WINDOW_DAYS is derived from the seeded ages, so "both seeded
            # trades are inside the window" is a property of the declared
            # clock. It used to be `days_back: 30` measured against a fixture
            # seeded at the wall clock, which made the answer depend on when
            # the suite ran.
            replay_data = _replay(client, real_headers, run_id, IN_WINDOW_DAYS)

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

            # Per-bucket counts derived from the seed clock: the seeded
            # schedule exits exactly one trade on each of these two days, so
            # a 2 (both trades landing in one bucket) and a 0 (an empty window
            # the report still answers happily for) are both refused here. The
            # previous form was `>= 1`, which accepted either.
            report_data = _report(client, real_headers, run_id, TRADE1_BUCKET)
            assert report_data["run_id"] == run_id
            assert report_data["date"] == TRADE1_BUCKET.isoformat()
            assert report_data["trades"]["count"] == 1
            assert report_data["trades"]["wins"] == 1
            assert report_data["trades"]["losses"] == 0
            assert abs(report_data["portfolio"]["starting_capital"] - 100000.0) < CENT_TOL

            trade2_report = _report(client, real_headers, run_id, TRADE2_BUCKET)
            assert trade2_report["trades"]["count"] == 1
            assert trade2_report["trades"]["wins"] == 0
            assert trade2_report["trades"]["losses"] == 1

            # A calendar day inside the replayed span with no exit on it. This
            # is what makes the two counts above informative: without a date
            # that must answer 0, "every date answers 1" would look identical.
            empty_report = _report(client, real_headers, run_id, EMPTY_BUCKET)
            assert empty_report["trades"]["count"] == 0

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
                    sql_membership.list_sql(
                        "SELECT * FROM paper_fills WHERE id IN :fids",
                        name="fids",
                        values=saved_ids["fill_ids"],
                    )
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
                    sql_membership.list_sql(
                        "SELECT * FROM paper_positions WHERE id IN :pids",
                        name="pids",
                        values=saved_ids["position_ids"],
                    )
                ).fetchall()
                assert len(positions) == 2
                for pos in positions:
                    assert pos.status == "closed"
                    assert pos.realized_pnl is not None

                pos_pnls = sorted([float(p.realized_pnl) for p in positions])
                expected_pnls = sorted([float(t["net_pnl"]) for t in expected["trades"]])
                for actual_pnl, exp_pnl in zip(pos_pnls, expected_pnls):
                    assert abs(actual_pnl - exp_pnl) < CENT_TOL

                # One metric row per seeded exit date, and the rows together
                # account for every trade the window admitted.
                assert len(saved_ids["metric_ids"]) == len(SEED)
                metrics = db.execute(
                    sql_membership.list_sql(
                        "SELECT * FROM paper_trading_daily_metrics WHERE id IN :mids",
                        name="mids",
                        values=saved_ids["metric_ids"],
                    )
                ).fetchall()
                total_metrics_trades = sum(m.trades_count for m in metrics)
                assert total_metrics_trades == expected["total_trades"]

                assert len(saved_ids["decision_ids"]) == 4
                decisions = db.execute(
                    sql_membership.list_sql(
                        "SELECT COUNT(*) FROM decision_records WHERE id IN :dids",
                        name="dids",
                        values=saved_ids["decision_ids"],
                    )
                ).scalar()
                assert decisions == 4

                assert len(saved_ids["order_ids"]) == 4
                orders = db.execute(
                    sql_membership.list_sql(
                        "SELECT COUNT(*) FROM paper_orders WHERE id IN :oids",
                        name="oids",
                        values=saved_ids["order_ids"],
                    )
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
            with api_test_fixtures(test_email, now=FIXED_NOW) as (auth_headers, user_id, study_run_id), frozen_replay_clock():
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
                    json={"days_back": IN_WINDOW_DAYS, "position_size_pct": 0.10, "max_positions": 3},
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

    def test_trades_inside_the_window_are_counted_in_their_exit_buckets(self, client):
        """Every trade the window admits is reported, on its own exit day.

        This is the half of U8 that ``assert count >= 1`` could not answer. The
        floor accepted a 2 (both trades credited to one day) and a 0 (a window
        that selected nothing) alike, so the suite stayed green while per-day
        attribution went unmeasured.

        ``IN_WINDOW_DAYS`` is derived from the seeded ages, so "both rows are
        inside" is a property of the declared clock rather than the calendar.
        """
        test_email = f"window_in_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()

        with api_test_fixtures(test_email, now=FIXED_NOW) as (
            auth_headers, user_id, study_run_id
        ), frozen_replay_clock():
            headers, _, run_id = _login_and_create(client, test_email)

            # The rows the window is about to admit must exist for this user
            # first; otherwise "counted" and "nothing to count" look identical.
            assert _seeded_trade_count(study_run_id) == len(SEED)

            replay_data = _replay(client, headers, run_id, IN_WINDOW_DAYS)
            assert replay_data["status"] == "completed"
            assert replay_data["total_trades"] == len(SEED)
            assert abs(
                replay_data["win_rate"] - float(expected["win_rate"])
            ) < DECIMAL_TOL

            # One trade per day, and the money on each day is that one trade's
            # net P&L: a count alone would still pass if both trades had been
            # credited to the same bucket.
            trade1_report = _report(client, headers, run_id, TRADE1_BUCKET)
            assert trade1_report["trades"]["count"] == 1
            assert trade1_report["trades"]["wins"] == 1
            assert trade1_report["trades"]["losses"] == 0
            assert abs(
                trade1_report["portfolio"]["daily_pnl"]
                - float(expected["trades"][0]["net_pnl"])
            ) < CENT_TOL

            trade2_report = _report(client, headers, run_id, TRADE2_BUCKET)
            assert trade2_report["trades"]["count"] == 1
            assert trade2_report["trades"]["wins"] == 0
            assert trade2_report["trades"]["losses"] == 1
            assert abs(
                trade2_report["portfolio"]["daily_pnl"]
                - float(expected["trades"][1]["net_pnl"])
            ) < CENT_TOL

            # A date the window spans with no exit on it. Without this row the
            # two counts above could simply be "every date answers 1".
            gap_report = _report(client, headers, run_id, EMPTY_BUCKET)
            assert gap_report["trades"]["count"] == 0
            assert gap_report["portfolio"]["daily_pnl"] == 0.0

    def test_trades_older_than_the_window_are_excluded(self, client):
        """An entry before the cutoff is dropped even though the row is there.

        Each ``days_back`` choice gets its own run: the service refuses a second
        replay of a completed run (``app/services/paper_trading.py:276``), and a
        fresh run also stops the two verdicts from sharing written state. The
        source rows are seeded identically for both, so the cutoff is the only
        thing these assertions can be measuring.
        """
        test_email = f"window_out_{uuid4().hex[:8]}@example.com"
        expected_kept = _calculate_expected_replay(kept=(1,))

        with api_test_fixtures(test_email, now=FIXED_NOW) as (
            auth_headers, user_id, study_run_id
        ), frozen_replay_clock():
            assert _seeded_trade_count(study_run_id) == len(SEED)

            headers, _, run_id = _login_and_create(client, test_email)
            replay_data = _replay(client, headers, run_id, ONE_SHORT_DAYS)

            assert replay_data["status"] == "completed"
            assert replay_data["total_trades"] == 1
            assert abs(
                replay_data["total_pnl"] - float(expected_kept["total_pnl"])
            ) < CENT_TOL
            assert abs(
                replay_data["final_capital"] - float(expected_kept["final_capital"])
            ) < CENT_TOL

            # The dropped trade's exit day answers 0 while its source row is
            # still in the database — the filter excluded it, it was never
            # missing. Re-count after the replay to show the replay itself did
            # not consume the source row.
            dropped_report = _report(client, headers, run_id, TRADE1_BUCKET)
            assert dropped_report["trades"]["count"] == 0
            assert dropped_report["portfolio"]["daily_pnl"] == 0.0
            assert _seeded_trade_count(study_run_id) == len(SEED)

            kept_report = _report(client, headers, run_id, TRADE2_BUCKET)
            assert kept_report["trades"]["count"] == 1
            assert kept_report["trades"]["losses"] == 1
            assert abs(
                kept_report["portfolio"]["daily_pnl"]
                - float(expected_kept["trades"][0]["net_pnl"])
            ) < CENT_TOL

            # A window shorter than both ages admits nothing. Zero admitted is
            # a completed run with no win rate, not a run that lost everything.
            empty_headers, _, empty_run_id = _login_and_create(client, test_email)
            empty_replay = _replay(client, empty_headers, empty_run_id, ALL_OUT_DAYS)
            assert empty_replay["status"] == "completed"
            assert empty_replay["total_trades"] == 0
            assert empty_replay["win_rate"] is None

            for bucket in (TRADE1_BUCKET, TRADE2_BUCKET):
                nothing = _report(client, empty_headers, empty_run_id, bucket)
                assert nothing["trades"]["count"] == 0

    def test_replay_window_boundary_is_inclusive_at_the_cutoff(self, client):
        """The entry edge is ``>=``, pinned by a one-day step across the cutoff.

        Entries land on whole days behind the declared clock, so one day of
        ``days_back`` moves the cutoff from exactly on trade 1's entry to one
        day past it:

            AT_CUTOFF_DAYS  cutoff == trade1.entry_time        -> 2 admitted
            ONE_SHORT_DAYS  cutoff == trade1.entry_time + 1d   -> 1 admitted

        A strict ``>`` would answer 1 on the first line and an off-by-one in the
        delta would answer 2 on the second, so the pair pins the boundary in
        both directions instead of asserting a convention.
        """
        test_email = f"window_edge_{uuid4().hex[:8]}@example.com"
        expected = _calculate_expected_replay()

        # The exit edge is deliberately not what these two cases test: both
        # seeded exits precede the pinned ``as_of``, so the entry clause is
        # always the one that can drop a trade here.
        assert SEED[0]["exit"] < FIXED_NOW and SEED[1]["exit"] < FIXED_NOW

        with api_test_fixtures(test_email, now=FIXED_NOW) as (
            auth_headers, user_id, study_run_id
        ), frozen_replay_clock():
            headers, _, run_id = _login_and_create(client, test_email)
            on_cutoff = _replay(client, headers, run_id, AT_CUTOFF_DAYS)
            assert on_cutoff["total_trades"] == len(SEED), (
                "cutoff == entry_time must admit the trade; a strict > would "
                f"have returned {on_cutoff['total_trades']}"
            )

            edge_report = _report(client, headers, run_id, TRADE1_BUCKET)
            assert edge_report["trades"]["count"] == 1
            assert abs(
                edge_report["portfolio"]["daily_pnl"]
                - float(expected["trades"][0]["net_pnl"])
            ) < CENT_TOL

            past_headers, _, past_run_id = _login_and_create(client, test_email)
            one_day_past = _replay(client, past_headers, past_run_id, ONE_SHORT_DAYS)
            assert one_day_past["total_trades"] == len(SEED) - 1
            assert (
                _report(client, past_headers, past_run_id, TRADE1_BUCKET)["trades"]["count"]
                == 0
            )
            assert (
                _report(client, past_headers, past_run_id, TRADE2_BUCKET)["trades"]["count"]
                == 1
            )
