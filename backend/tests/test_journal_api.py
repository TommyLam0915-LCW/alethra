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
