"""Cost-aware multi-bar research engine: offline contract tests.

The bars in this file are deterministic synthetic fixtures, never market data.
They exercise the arithmetic and refusal rules: no lookahead, walk-forward
selection, the volatility cost gate, stop-first intrabar handling, gap fills,
accounting identity and the win-rate/expectancy separation. Live evidence is
collected outside the suite and is never written back into these tests.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.services.strategy_research as research
from app.main import app  # noqa: F401  (ensures the API package imports cleanly)
from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import KrakenProvider
from app.services.strategy_lab import LabBar
from app.services.strategy_research import (
    EXIT_BY_ID,
    MIN_RESEARCH_BARS,
    ResearchRule,
    ResearchSettings,
    ResearchTrade,
    _simulate_range,
    fold_bounds,
    prepare_indicators,
    run_study,
    summarize,
)

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
BASE = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
NO_COSTS = ResearchSettings(commission_rate=Decimal("0"), slippage_rate=Decimal("0"))


def _bar(index: int, open_, high, low, close) -> LabBar:
    return LabBar(
        open_time=BASE + timedelta(days=index),
        interval_minutes=1440,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


def _make_bars(count: int = 320) -> list[LabBar]:
    """Oscillating market with enough true range to clear the cost gate."""
    bars: list[LabBar] = []
    for index in range(count):
        close = 100 + math.sin(index / 6.0) * 6 + index * 0.02
        open_ = 100 + math.sin((index - 0.5) / 6.0) * 6 + (index - 1) * 0.02
        bars.append(
            _bar(
                index,
                round(open_, 8),
                round(max(open_, close) + 2.0, 8),
                round(min(open_, close) - 2.0, 8),
                round(close, 8),
            )
        )
    return bars


ALWAYS_LONG = ResearchRule(
    rule_id="always_long",
    label_zh="test",
    family="test",
    thesis_zh="test",
    signal=lambda ind, index: 1,
)


# -- fold geometry ----------------------------------------------------------


def test_fold_bounds_cover_every_bar_after_warmup_without_overlap():
    bounds = fold_bounds(300, 60, 5)

    assert bounds[0][0] == 60
    assert bounds[-1][1] == 299
    for (start, end), (next_start, next_end) in zip(bounds, bounds[1:]):
        assert end >= start
        assert next_start == end + 1
        assert next_end >= next_start


def test_fold_bounds_refuse_a_span_smaller_than_the_fold_count():
    with pytest.raises(ValueError):
        fold_bounds(warmup=60, total_bars=65, folds=8)


# -- indicators -------------------------------------------------------------


def test_indicators_do_not_read_future_bars():
    bars = _make_bars(300)
    partial = prepare_indicators(bars[:200])
    full = prepare_indicators(bars)

    for field in (
        "close",
        "atr",
        "ema_fast",
        "ema_slow",
        "rsi",
        "zscore",
        "high_prior",
        "low_prior",
        "roc",
        "trend_strength",
    ):
        assert getattr(partial, field)[:200] == getattr(full, field)[:200], field


# -- simulation rules -------------------------------------------------------


def test_cost_gate_blocks_a_setup_whose_range_cannot_pay_the_round_trip():
    bars = _make_bars(120)
    ind = prepare_indicators(bars)
    settings = ResearchSettings(cost_multiple=Decimal("100"))

    trades, stats = _simulate_range(
        bars, ind, ALWAYS_LONG, EXIT_BY_ID["balanced_1_1"], settings, 60, 110
    )

    assert trades == []
    assert stats["signals"] >= 1
    assert stats["blocked_by_cost_gate"] == stats["signals"]


def test_stop_is_assumed_first_when_one_bar_touches_stop_and_target():
    bars = []
    for index in range(40):
        price = 105 if index % 2 else 95
        bars.append(_bar(index, price, price + 1, price - 1, price))
    bars.append(_bar(40, 100, 101, 99, 100))  # signal bar
    bars.append(_bar(41, 100, 120, 80, 100))  # touches both levels
    bars.extend(_bar(index, 100, 101, 99, 100) for index in range(42, 50))

    ind = prepare_indicators(bars)
    trades, _ = _simulate_range(
        bars, ind, ALWAYS_LONG, EXIT_BY_ID["balanced_1_1"], NO_COSTS, 40, 41
    )

    assert len(trades) == 1
    trade = trades[0]
    expected_stop = Decimal("100") - ind.atr[40]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == expected_stop
    assert trade.exit_index == 41


def test_gap_through_the_stop_fills_at_the_open_not_the_level():
    bars = []
    for index in range(40):
        price = 105 if index % 2 else 95
        bars.append(_bar(index, price, price + 1, price - 1, price))
    bars.append(_bar(40, 100, 101, 99, 100))  # signal bar
    bars.append(_bar(41, 80, 85, 75, 80))  # opens far below the stop
    bars.extend(_bar(index, 80, 81, 79, 80) for index in range(42, 50))

    ind = prepare_indicators(bars)
    stop = Decimal("100") - ind.atr[40]
    assert Decimal("80") < stop

    trades, _ = _simulate_range(
        bars, ind, ALWAYS_LONG, EXIT_BY_ID["balanced_1_1"], NO_COSTS, 40, 41
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "stop_gap"
    assert trade.exit_price == Decimal("80")
    assert trade.exit_index == 41


def test_summary_keeps_cash_accounting_identical_to_the_trade_ledger():
    bars = _make_bars(320)
    ind = prepare_indicators(bars)

    trades, _ = _simulate_range(
        bars, ind, ALWAYS_LONG, EXIT_BY_ID["balanced_1_1"], ResearchSettings(), 60, 300
    )
    summary = summarize(trades, starting_cash=Decimal("10000"))

    assert summary["trades"] == len(trades)
    assert summary["accounting_ok"] is True
    assert Decimal(summary["ending_cash"]) == Decimal("10000") + Decimal(summary["net_pnl"])
    assert trades[-1].cash_after == Decimal(summary["ending_cash"])


# -- metrics ----------------------------------------------------------------


def _trade(net: str, cash_after: str) -> ResearchTrade:
    return ResearchTrade(
        rule_id="test",
        exit_id="test",
        direction=1,
        signal_index=0,
        entry_index=1,
        exit_index=2,
        entry_time=BASE,
        exit_time=BASE + timedelta(days=1),
        entry_price=Decimal("100"),
        exit_price=Decimal("100"),
        quantity=Decimal("1"),
        gross_pnl=Decimal(net),
        commission=Decimal("0"),
        slippage_cost=Decimal("0"),
        net_pnl=Decimal(net),
        exit_reason="target",
        bars_held=1,
        cash_after=Decimal(cash_after),
    )


def test_high_win_rate_does_not_imply_positive_expectancy():
    # Nine small wins and one large loss: 90% win rate, negative net.
    trades = [_trade("1", str(10000 + index + 1)) for index in range(9)]
    trades.append(_trade("-20", "9989"))

    summary = summarize(trades, starting_cash=Decimal("10000"))

    assert Decimal(summary["win_rate"]) > Decimal("0.6")
    assert Decimal(summary["net_pnl"]) == Decimal("-11")
    assert Decimal(summary["expectancy_per_trade"]) < 0
    assert Decimal(summary["profit_factor"]) < 1


# -- study ------------------------------------------------------------------


def test_run_study_uses_only_prior_folds_to_select_each_test_combo(monkeypatch):
    original = research._select_combo
    recorded = []

    def recorder(bars, ind, settings, train_range):
        recorded.append(train_range)
        return original(bars, ind, settings, train_range)

    monkeypatch.setattr(research, "_select_combo", recorder)

    bars = _make_bars(300)
    payload = run_study(bars, folds=4)
    bounds = fold_bounds(len(bars), research.WARMUP_BARS, 4)

    assert recorded == [(bounds[0][0], fold[0] - 1) for fold in bounds[1:]]
    for (_, train_end), (test_start, _) in zip(recorded, bounds[1:]):
        assert train_end < test_start
    assert payload["walk_forward"]["folds"][0]["role"] == "seed"
    assert payload["walk_forward"]["tested_folds"] == 3


def test_run_study_is_deterministic_and_marks_the_grid_as_in_sample():
    bars = _make_bars(320)

    first = run_study(bars, folds=4, include_trades=True)
    second = run_study(bars, folds=4, include_trades=True)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["lane"] == "strategy_research_simulated"
    assert first["walk_forward"]["out_of_sample"]["accounting_ok"] is True
    assert len(first["in_sample_grid"]) == len(research.RULES) * len(research.EXIT_CONFIGS)
    assert any("not evidence" in note for note in first["disclosures"])
    assert any("走步前推" in note for note in first["disclosures_zh"])


def test_run_study_refuses_a_series_shorter_than_the_minimum():
    with pytest.raises(ValueError) as excinfo:
        run_study(_make_bars(MIN_RESEARCH_BARS - 1))
    assert str(MIN_RESEARCH_BARS) in str(excinfo.value)


# -- API contract -----------------------------------------------------------

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, rows_by_interval=None):
        self.rows_by_interval = rows_by_interval or {}
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        interval = int(params.get("interval", "1"))
        return {"error": [], "result": {"XXBTZUSD": self.rows_by_interval[interval], "last": "unused"}}


def _ohlc_rows(interval: int, count: int = 720, seed: int = 0):
    step = interval * 60
    now_ts = int(NOW.timestamp())
    last_open = (now_ts // step) * step - step
    rows = []
    for index in range(count):
        timestamp = last_open - (count - 1 - index) * step
        price = 100 + seed + (index % 37) * 0.3
        rows.append(
            [
                timestamp,
                str(price),
                str(price + 1),
                str(price - 1),
                str(price + (0.5 if index % 3 else -0.5)),
                str(price),
                "1.0",
                4,
            ]
        )
    return rows


def _install(rows_by_interval=None):
    provider = KrakenProvider(
        transport=FakeTransport(rows_by_interval),
        clock=lambda: NOW,
        min_request_interval=0,
    )
    set_crypto_provider(provider)
    return provider


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_crypto_provider()
    yield
    reset_crypto_provider()


def test_research_requires_authentication(client):
    response = client.post("/api/v1/strategy-lab/research", json={"interval_minutes": 1440})
    assert response.status_code == 401


def test_research_returns_walk_forward_evidence_and_dataset_provenance(client, auth_headers):
    _install({1440: _ohlc_rows(1440)})

    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"interval_minutes": 1440, "folds": 3, "include_trades": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lane"] == "strategy_research_simulated"
    assert body["dataset"]["bars"] == 720
    assert body["dataset"]["interval_minutes"] == 1440
    assert body["dataset"]["source"] == "venue_completed_bars"
    assert body["dataset"]["integrity_ok"] is True
    assert body["dataset"]["fingerprint"]
    assert body["instrument"]["instrument_id"] == INSTRUMENT
    assert body["walk_forward"]["folds"][0]["role"] == "seed"
    assert body["walk_forward"]["tested_folds"] == 2
    assert body["walk_forward"]["out_of_sample"]["accounting_ok"] is True
    assert len(body["in_sample_grid"]) == 48  # 12 rules × 4 exit configs (MB-16: 4 mean-reversion, MB-17: 4 volatility)
    assert "out_of_sample_trades" in body


def test_research_rejects_an_interval_the_cost_model_cannot_support(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"interval_minutes": 2},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "unsupported_interval"


def test_research_refuses_a_series_too_short_for_warmup_and_folds(client, auth_headers):
    _install({1440: _ohlc_rows(1440, count=60)})

    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"interval_minutes": 1440},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "insufficient_dataset"
    assert detail["bars"] == 60
    assert detail["minimum_bars"] == MIN_RESEARCH_BARS


def test_research_rejects_unknown_instrument(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"instrument_id": "CRYPTO:KRAKEN:DOGE-USD"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == "unknown_instrument"


def test_research_rejects_notional_above_starting_cash(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"starting_cash": "500", "notional_per_trade": "1000"},
    )
    assert response.status_code == 422


# -- durable records --------------------------------------------------------


def _second_user_headers(client):
    registration = {
        "risk_acknowledged": True,
        "email": "other@alethra.io",
        "password": "otherpass123",
        "display_name": "Other User",
        "user_type": "individual",
    }
    client.post("/api/auth/register", json=registration)
    login = client.post(
        "/api/auth/login",
        json={"email": registration["email"], "password": registration["password"]},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_durable_research_run_stores_every_trade_and_exports_csv(client, auth_headers):
    _install({1440: _ohlc_rows(1440)})

    response = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json={"interval_minutes": 1440, "folds": 3},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["lane"] == "strategy_research_simulated"
    assert body["simulated"] is True
    assert body["orders_supported"] is False
    assert body["bar_count"] == 720
    assert body["dataset_fingerprint"]
    assert body["dataset_integrity_ok"] is True
    assert body["trade_count"] == len(body["trades"])
    assert body["trades"], "the synthetic oscillating series should produce trades"

    running = Decimal(body["settings"]["starting_cash"])
    for trade in body["trades"]:
        for field in (
            "signal_time",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "quantity",
            "gross_pnl",
            "commission",
            "slippage_cost",
            "total_cost",
            "net_pnl",
            "cash_after",
            "bars_held",
            "mfe_pct",
            "mae_pct",
            "pnl_reason_en",
            "pnl_reason_zh",
        ):
            assert trade[field] is not None, field
        assert trade["outcome"] in ("win", "loss", "flat")
        assert trade["direction"] in ("long", "short")
        assert trade["exit_reason"] in (
            "target",
            "target_gap",
            "stop",
            "stop_gap",
            "time_stop",
        )
        running += Decimal(trade["net_pnl"])
        assert Decimal(trade["cash_after"]) == running

    run_id = body["id"]

    listing = client.get(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        params={"instrument_id": INSTRUMENT, "interval_minutes": 1440},
    )
    assert listing.status_code == 200
    records = listing.json()["records"]
    assert [record["id"] for record in records] == [run_id]
    assert "trades" not in records[0]

    detail = client.get(
        f"/api/v1/strategy-lab/research/runs/{run_id}",
        headers=auth_headers,
        params={"include_bars": "true"},
    )
    assert detail.status_code == 200
    stored = detail.json()
    assert len(stored["bars"]) == 720
    assert stored["bars"][0]["open_time"].endswith("+00:00")
    assert stored["trades"] == body["trades"]

    export = client.get(
        f"/api/v1/strategy-lab/research/runs/{run_id}/trades.csv",
        headers=auth_headers,
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    rows = export.text.splitlines()
    assert rows[0].startswith("trade_index,fold,direction,rule_id")
    assert len(rows) == 1 + body["trade_count"]
    assert export.headers["content-disposition"].startswith("attachment;")


def test_durable_research_record_is_owner_scoped(client, auth_headers):
    _install({1440: _ohlc_rows(1440)})
    created = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json={"interval_minutes": 1440, "folds": 3},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    other_headers = _second_user_headers(client)
    assert (
        client.get(
            f"/api/v1/strategy-lab/research/runs/{run_id}", headers=other_headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/strategy-lab/research/runs/{run_id}/trades.csv",
            headers=other_headers,
        ).status_code
        == 404
    )
    other_list = client.get(
        "/api/v1/strategy-lab/research/runs", headers=other_headers
    )
    assert other_list.status_code == 200
    assert other_list.json()["records"] == []


def test_durable_research_refusal_writes_nothing(client, auth_headers):
    _install({1440: _ohlc_rows(1440, count=60)})

    refused = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json={"interval_minutes": 1440},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "insufficient_dataset"

    listing = client.get("/api/v1/strategy-lab/research/runs", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["records"] == []


def test_durable_research_requires_authentication(client):
    response = client.post(
        "/api/v1/strategy-lab/research/runs", json={"interval_minutes": 1440}
    )
    assert response.status_code == 401


def test_trade_csv_neutralizes_formula_cells():
    from app.services.strategy_study_store import _csv_cell

    assert _csv_cell("=SUM(A1)") == "'=SUM(A1)"
    assert _csv_cell("+123") == "'+123"
    assert _csv_cell("-123") == "'-123"
    assert _csv_cell("@cmd") == "'@cmd"
    assert _csv_cell(Decimal("-1.25")) == "-1.25"
    assert _csv_cell(None) == ""
    assert _csv_cell(True) == "true"
