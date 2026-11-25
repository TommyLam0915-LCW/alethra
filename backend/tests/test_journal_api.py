"""Tests for the unified trading journal API (Phase 1).

Covers:
- GET /v1/journal/trades — paginated list across multiple runs
- Filtering by instrument_id, rule_id, date range, rule_family
- Summary stats (total_pnl, win_rate, profit_factor)
- GET /v1/journal/cumulative-pnl — running cumulative P&L series
- GET /v1/journal/equity-curve — equity + drawdown
- GET /v1/journal/trades.csv — CSV export format
- GET /v1/journal/filters — available filter values
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.conftest import TestSession
from app.models.paper_portfolio import (
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User

ANCHOR = datetime(2026, 10, 1, tzinfo=timezone.utc)
INSTRUMENT_BTC = "CRYPTO:KRAKEN:BTC-USD"
INSTRUMENT_ETH = "CRYPTO:KRAKEN:ETH-USD"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _new_user(db) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        email=f"journal-{uuid.uuid4().hex[:10]}@example.test",
        password_hash="test-only",
        display_name="journal test",
    )
    db.add(user)
    db.flush()
    return user.id


def _trade(db, run, *, index, rule_id, net_pnl, entry_day_offset,
           exit_reason="signal_flip", outcome="win", direction="long",
           rule_label_zh="測試規則"):
    entry_time = ANCHOR + timedelta(days=entry_day_offset)
    exit_time = entry_time + timedelta(hours=4)
    gross = Decimal(str(net_pnl)) + Decimal("2")
    trade = StrategyStudyTrade(
        run_id=run.id,
        trade_index=index,
        fold=1,
        rule_id=rule_id,
        rule_label_zh=rule_label_zh,
        exit_id="fixed_exit",
        exit_label_zh="固定停損",
        direction=direction,
        exit_reason=exit_reason,
        outcome=outcome,
        signal_time=entry_time - timedelta(minutes=15),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price="50000",
        exit_price="50100",
        quantity="1",
        gross_pnl=str(gross),
        commission="1",
        slippage_cost="1",
        total_cost="2",
        net_pnl=str(net_pnl),
        cash_after="1000000",
        bars_held=1,
        pnl_reason_zh="測試",
        pnl_reason_en="test fixture",
    )
    db.add(trade)
    return trade


def _run(db, user_id, *, instrument=INSTRUMENT_BTC, interval=1440,
         starting_cash="10000", trade_count=0):
    run = StrategyStudyRun(
        id=uuid.uuid4(),
        user_id=user_id,
        lane="strategy_research_simulated",
        research_version="test",
        instrument_id=instrument,
        interval_minutes=interval,
        provider_id="kraken",
        data_nature="live",
        bar_count=720,
        first_open_time=ANCHOR,
        last_open_time=ANCHOR + timedelta(days=720),
        fetched_at=ANCHOR,
        dataset_fingerprint="fp-test",
        dataset_integrity_ok=True,
        dataset_gap_count=0,
        dataset_missing_intervals=0,
        settings_json={
            "starting_cash": starting_cash,
            "notional_per_trade": "1000",
            "commission_rate": "0.001",
            "slippage_rate": "0.0005",
        },
        walk_forward_json={"folds": [{"fold": 1}]},
        highlights_json=None,
        disclosures_json=None,
        bars_json=[],
        trade_count=trade_count,
    )
    db.add(run)
    db.flush()
    return run


def _seed_multi_run_data(db, user_id):
    """Create 2 runs with different instruments and several trades."""
    # Run 1: BTC, trend rules
    run1 = _run(db, user_id, instrument=INSTRUMENT_BTC, interval=1440,
                starting_cash="10000", trade_count=3)
    _trade(db, run1, index=0, rule_id="breakout_trend", net_pnl="150",
           entry_day_offset=0, outcome="win")
    _trade(db, run1, index=1, rule_id="breakout_trend", net_pnl="-80",
           entry_day_offset=5, outcome="loss", exit_reason="stop_loss")
    _trade(db, run1, index=2, rule_id="momentum_continuation", net_pnl="200",
           entry_day_offset=10, outcome="win")

    # Run 2: ETH, mean-reversion rules
    run2 = _run(db, user_id, instrument=INSTRUMENT_ETH, interval=240,
                starting_cash="10000", trade_count=2)
    _trade(db, run2, index=0, rule_id="range_fade", net_pnl="50",
           entry_day_offset=15, outcome="win")
    _trade(db, run2, index=1, rule_id="bollinger_mean_reversion", net_pnl="-30",
           entry_day_offset=20, outcome="loss", exit_reason="time_stop")

    db.commit()
    return run1, run2


@pytest.fixture()
def user_id():
    db = TestSession()
    try:
        uid = _new_user(db)
        db.commit()
        return uid
    finally:
        db.close()


@pytest.fixture()
def seeded_user_id(user_id):
    db = TestSession()
    try:
        _seed_multi_run_data(db, user_id)
        return user_id
    finally:
        db.close()


def _auth_headers(client, user_id):
    """Get auth headers by logging in as the test user.

    The conftest auth_headers fixture creates its own user, so for tests that
    need a specific user_id we create a token directly.
    """
    from app.auth.jwt import create_access_token
    token = create_access_token(data={"sub": str(user_id)})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestJournalTrades:
    """GET /v1/journal/trades"""

    def test_returns_trades_from_multiple_runs(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/trades", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 5
        assert len(data["trades"]) == 5
        # Default sort: entry_time desc
        entry_times = [t["entry_time"] for t in data["trades"]]
        assert entry_times == sorted(entry_times, reverse=True)

    def test_pagination(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades?limit=2&offset=0", headers=headers
        )
        data = resp.json()
        assert len(data["trades"]) == 2
        assert data["total_count"] == 5

        resp2 = client.get(
            "/api/v1/journal/trades?limit=2&offset=2", headers=headers
        )
        data2 = resp2.json()
        assert len(data2["trades"]) == 2

    def test_filter_by_instrument_id(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            f"/api/v1/journal/trades?instrument_id={INSTRUMENT_BTC}",
            headers=headers,
        )
        data = resp.json()
        assert data["total_count"] == 3
        assert all(t["instrument_id"] == INSTRUMENT_BTC for t in data["trades"])

    def test_filter_by_rule_id(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades?rule_id=breakout_trend", headers=headers
        )
        data = resp.json()
        assert data["total_count"] == 2
        assert all(t["rule_id"] == "breakout_trend" for t in data["trades"])

    def test_filter_by_rule_family(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades?rule_family=mean_reversion", headers=headers
        )
        data = resp.json()
        # range_fade + bollinger_mean_reversion are both mean_reversion family
        assert data["total_count"] == 2
        families = {t["rule_family"] for t in data["trades"]}
        assert families == {"mean_reversion"}

    def test_filter_by_date_range(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        date_from = (ANCHOR + timedelta(days=4)).isoformat()
        date_to = (ANCHOR + timedelta(days=16)).isoformat()
        resp = client.get(
            "/api/v1/journal/trades",
            params={"date_from": date_from, "date_to": date_to},
            headers=headers,
        )
        data = resp.json()
        # Trades at day 5, 10, 15 should match (day 0 and 20 excluded)
        assert data["total_count"] == 3

    def test_filter_by_exit_reason(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades?exit_reason=stop_loss", headers=headers
        )
        data = resp.json()
        assert data["total_count"] == 1
        assert data["trades"][0]["exit_reason"] == "stop_loss"

    def test_filter_by_pnl_range(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades",
            params={"pnl_min": "0", "pnl_max": "100"},
            headers=headers,
        )
        data = resp.json()
        # net_pnl 50 matches (0 <= 50 <= 100)
        assert data["total_count"] == 1
        assert Decimal(data["trades"][0]["net_pnl"]) == Decimal("50")

    def test_unauthorized_without_token(self, client, seeded_user_id):
        resp = client.get("/api/v1/journal/trades")
        assert resp.status_code == 4401 or resp.status_code == 401

    def test_trades_enriched_with_run_metadata(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/trades", headers=headers)
        data = resp.json()
        trade = data["trades"][0]
        assert "instrument_id" in trade
        assert "interval_minutes" in trade
        assert "starting_cash" in trade
        assert "rule_family" in trade
        assert "run_id" in trade
        assert "trade_id" in trade


class TestJournalSummary:
    """Summary stats within GET /v1/journal/trades response."""

    def test_summary_stats(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/trades", headers=headers)
        data = resp.json()
        summary = data["summary"]

        assert summary["total_trades"] == 5
        # 150 + (-80) + 200 + 50 + (-30) = 290
        assert Decimal(summary["total_pnl"]) == Decimal("290")
        # 3 wins out of 5 = 60%
        assert summary["win_rate"] == "60.00"
        # profit_factor = (150+200+50) / (80+30) = 400/110 ≈ 3.64
        assert summary["profit_factor"] is not None
        pf = Decimal(summary["profit_factor"])
        assert pf > Decimal("3.6") and pf < Decimal("3.7")

    def test_summary_best_worst_trade(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/trades", headers=headers)
        summary = resp.json()["summary"]
        assert Decimal(summary["best_trade"]["net_pnl"]) == Decimal("200")
        assert Decimal(summary["worst_trade"]["net_pnl"]) == Decimal("-80")

    def test_summary_empty(self, client, user_id):
        headers = _auth_headers(client, user_id)
        resp = client.get("/api/v1/journal/trades", headers=headers)
        data = resp.json()
        assert data["summary"]["total_trades"] == 0
        assert data["summary"]["total_pnl"] == "0"


class TestCumulativePnl:
    """GET /v1/journal/cumulative-pnl"""

    def test_cumulative_pnl_series(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/cumulative-pnl", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        points = data["points"]
        assert len(points) == 5
        # Points ordered by entry_time ASC
        assert Decimal(points[0]["net_pnl"]) == Decimal("150")
        assert Decimal(points[0]["cumulative_pnl"]) == Decimal("150")
        assert Decimal(points[1]["net_pnl"]) == Decimal("-80")
        assert Decimal(points[1]["cumulative_pnl"]) == Decimal("70")
        # total_pnl = last cumulative
        assert Decimal(data["total_pnl"]) == Decimal("290")

    def test_cumulative_pnl_with_filter(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/cumulative-pnl",
            params={"instrument_id": INSTRUMENT_BTC},
            headers=headers,
        )
        data = resp.json()
        assert len(data["points"]) == 3
        # 150 + (-80) + 200 = 270
        assert Decimal(data["total_pnl"]) == Decimal("270")


class TestEquityCurve:
    """GET /v1/journal/equity-curve"""

    def test_equity_curve_basic(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/equity-curve", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert Decimal(data["baseline_cash"]) == Decimal("10000")
        series = data["series"]
        assert len(series) == 1
        assert series[0]["name"] == "all"
        points = series[0]["points"]
        assert len(points) == 5
        # First point: 10000 + 150 = 10150
        assert Decimal(points[0]["equity"]) == Decimal("10150")
        # After -80: 10150 - 80 = 10070
        assert Decimal(points[1]["equity"]) == Decimal("10070")

    def test_equity_curve_drawdown(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/equity-curve", headers=headers)
        points = resp.json()["series"][0]["points"]
        # Peak after trade 0: 10150
        # After trade 1 (-80): equity=10070, dd = (10150-10070)/10150*100 ≈ 0.79%
        dd = Decimal(points[1]["drawdown_pct"])
        assert dd > Decimal("0.7") and dd < Decimal("0.9")
        # After trade 2 (+200): equity=10270, new peak, dd=0
        assert points[2]["drawdown_pct"] == "0.00"

    def test_equity_curve_grouped_by_instrument(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/equity-curve",
            params={"group_by": "instrument"},
            headers=headers,
        )
        data = resp.json()
        series = data["series"]
        assert len(series) == 2
        names = {s["name"] for s in series}
        assert INSTRUMENT_BTC in names
        assert INSTRUMENT_ETH in names

    def test_equity_curve_empty(self, client, user_id):
        headers = _auth_headers(client, user_id)
        resp = client.get("/api/v1/journal/equity-curve", headers=headers)
        data = resp.json()
        assert data["series"] == []


class TestCsvExport:
    """GET /v1/journal/trades.csv"""

    def test_csv_format(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/trades.csv", headers=headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]

        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        # Header + 5 data rows
        assert len(rows) == 6
        header = rows[0]
        assert "instrument_id" in header
        assert "net_pnl" in header
        assert "rule_family" in header
        assert "entry_time" in header

    def test_csv_with_filter(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get(
            "/api/v1/journal/trades.csv",
            params={"instrument_id": INSTRUMENT_ETH},
            headers=headers,
        )
        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        # Header + 2 ETH trades
        assert len(rows) == 3


class TestFilterOptions:
    """GET /v1/journal/filters"""

    def test_filter_options(self, client, seeded_user_id):
        headers = _auth_headers(client, seeded_user_id)
        resp = client.get("/api/v1/journal/filters", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert INSTRUMENT_BTC in data["instruments"]
        assert INSTRUMENT_ETH in data["instruments"]
        assert 1440 in data["intervals"]
        assert 240 in data["intervals"]
        # Check rule_ids have family info
        rule_ids = data["rule_ids"]
        assert len(rule_ids) >= 4
        families = {r["family"] for r in rule_ids}
        assert "trend" in families
        assert "mean_reversion" in families
        # Check rule_families list
        assert "trend" in data["rule_families"]
        assert "mean_reversion" in data["rule_families"]
        # Check exit_reasons
        assert "signal_flip" in data["exit_reasons"]
        assert "stop_loss" in data["exit_reasons"]

    def test_filter_options_empty(self, client, user_id):
        headers = _auth_headers(client, user_id)
        resp = client.get("/api/v1/journal/filters", headers=headers)
        data = resp.json()
        assert data["instruments"] == []
        assert data["rule_ids"] == []


class TestUnifiedJournal:
    """GET /api/v1/journal/unified — the research half and the paper half.

    Both halves are raw ``text()`` reads, so the route declares no column types
    and the driver returns whatever the writer stored. That is exactly where a
    UUID keyed read goes wrong quietly: a bind spelled one way and a column
    written the other produce an empty list rather than an error, and an owner
    reading their own history cannot tell "no trades" from "not your trades".
    Each half is therefore pinned against the writer it actually has -- the
    research rows are written by the ORM, the paper account by ``POST
    /api/v1/paper-trading/create`` -- and the ids and timestamps are asserted in
    the one form the caller is given, because a 32-character key or a
    space-separated instant in a customer-facing list is a different answer
    depending on which database served it.

    The paper fill is attached with the product's own models instead of by
    running a replay: the replay's final commit guard takes ``SELECT ... FOR
    UPDATE`` on the run row, so making a fill through that path is a PostgreSQL
    measurement. What these nodes measure is the read, and they say so.
    """

    def test_the_research_half_names_the_owner_own_trades(self, client, user_id):
        db = TestSession()
        try:
            run = _run(db, user_id, instrument=INSTRUMENT_BTC, interval=1440,
                       starting_cash="10000", trade_count=1)
            trade = _trade(db, run, index=0, rule_id="breakout_trend",
                           net_pnl="150", entry_day_offset=0)
            db.commit()
            run_id, trade_id = run.id, trade.id
        finally:
            db.close()

        resp = client.get(
            "/api/v1/journal/unified?source=research",
            headers=_auth_headers(client, user_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["research_count"] == 1, body["summary"]
        row = body["trades"][0]
        assert row["id"] == str(trade_id), row
        assert row["run_id"] == str(run_id), row
        # The caller is given one instant: ISO with a T, on this lane too, not
        # the space-separated text the column actually holds here.
        assert row["entry_time"].startswith("2026-10-01T"), row
        assert " " not in row["entry_time"], row

    def test_the_paper_half_names_the_account_the_create_route_made(
        self, client
    ):
        """A create-lane account's fill appears in the unified list.

        ``POST /api/v1/paper-trading/create`` writes the account with its own
        raw SQL, and this read reaches the fill through that account
        (``paper_fills`` → ``paper_orders`` → ``paper_portfolios.owner_id``).
        The account therefore has to be named in the spelling the route stores
        it in, which is what the shared UUID bind contract settles.
        """
        email = "journalunified@alethra.io"
        registered = client.post(
            "/api/auth/register",
            json={
                "risk_acknowledged": True,
                "email": email,
                "password": "testpass123",
                "display_name": "journal unified",
                "user_type": "individual",
            },
        )
        assert registered.status_code in (200, 201), registered.text
        login = client.post(
            "/api/auth/login", json={"email": email, "password": "testpass123"}
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post(
            "/api/v1/paper-trading/create",
            headers=headers,
            json={
                "skill": "fisherman",
                "instrument_id": INSTRUMENT_BTC,
                "interval_minutes": 1440,
                "exit_config": "exit_atr_2x",
                "initial_capital": 100000.0,
            },
        )
        assert created.status_code == 200, created.text
        portfolio_id = uuid.UUID(created.json()["portfolio_id"])

        bar_time = ANCHOR + timedelta(hours=9)
        with TestSession() as db:
            order = PaperOrder(
                portfolio_id=portfolio_id,
                asset_id=INSTRUMENT_BTC,
                side=OrderSide.BUY,
                quantity=1.0,
                actor_type="human",
                status=OrderStatus.filled,
            )
            db.add(order)
            db.flush()
            db.add(
                PaperFill(
                    paper_order_id=order.id,
                    execution_date=bar_time.date().isoformat(),
                    observed_price=50000.0,
                    execution_price_before_costs=49990.0,
                    slippage_amount=10.0,
                    commission_amount=5.0,
                    quantity=1.0,
                    gross_amount=49990.0,
                    net_cash_change=-50000.0,
                    bar_timestamp=bar_time,
                )
            )
            db.commit()
            order_id, fill_id = order.id, order.fills[0].id

        resp = client.get(
            "/api/v1/journal/unified?source=paper", headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["paper_count"] == 1, body["summary"]
        row = body["trades"][0]
        assert row["id"] == str(fill_id), row
        assert row["order_id"] == str(order_id), row
        assert row["entry_time"].startswith("2026-10-01T09:00:00"), row
        assert " " not in row["entry_time"], row
        # ``order.created_at`` is a DateTime column read through text(), so this
        # is the second half of the same rule: one instant form on either
        # backend, not a PostgreSQL answer and a SQLite answer.
        assert "T" in row["created_at"], row

    def test_another_owner_reads_an_empty_unified_list(self, client, user_id):
        """The owner filter is the reason the lists are non-empty, not a label.

        With the account and a research run of their own in the database, a
        second user must get nothing from either half; otherwise a bind that
        matches no row and a filter that matches every row look identical here.
        """
        db = TestSession()
        try:
            run = _run(db, user_id, instrument=INSTRUMENT_BTC, interval=1440,
                       starting_cash="10000", trade_count=1)
            _trade(db, run, index=0, rule_id="breakout_trend", net_pnl="150",
                   entry_day_offset=0)
            portfolio = PaperPortfolio(
                id=uuid.uuid4(),
                owner_id=user_id,
                currency="USD",
                initial_virtual_cash=100000.0,
                current_virtual_cash=100000.0,
                status=PortfolioStatus.active,
            )
            db.add(portfolio)
            db.flush()
            order = PaperOrder(
                portfolio_id=portfolio.id,
                asset_id=INSTRUMENT_BTC,
                side=OrderSide.BUY,
                quantity=1.0,
                actor_type="human",
                status=OrderStatus.filled,
            )
            db.add(order)
            db.flush()
            db.add(
                PaperFill(
                    paper_order_id=order.id,
                    execution_date="2026-10-01",
                    observed_price=50000.0,
                    execution_price_before_costs=50000.0,
                    slippage_amount=0.0,
                    commission_amount=0.0,
                    quantity=1.0,
                    gross_amount=50000.0,
                    net_cash_change=-50000.0,
                )
            )
            db.commit()
        finally:
            db.close()

        other = TestSession()
        try:
            stranger = _new_user(other)
            other.commit()
        finally:
            other.close()

        resp = client.get(
            "/api/v1/journal/unified", headers=_auth_headers(client, stranger)
        )
        assert resp.status_code == 200, resp.text
        summary = resp.json()["summary"]
        assert summary["research_count"] == 0, summary
        assert summary["paper_count"] == 0, summary
