"""Strategy Lab: offline contract tests.

The bars here are deterministic synthetic fixtures, not market data. They exist
to exercise the lab's arithmetic, isolation and refusal rules without a socket.
The live evidence is collected separately and is never written to these tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.main import app  # noqa: F401  (ensures the API package imports cleanly)
from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import KrakenProvider
from app.services.strategy_lab import (
    MAX_WARMUP_BARS,
    STRATEGIES,
    LabSettings,
    StrategyLabError,
    StrategySpec,
    assemble_dataset,
    run_lab,
    run_strategy,
)

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
BASE = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
NO_COSTS = LabSettings(1, Decimal("10000"), Decimal("1000"), Decimal("0"), Decimal("0"))


@dataclass
class FakeCandle:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class FakeSeries:
    completed: tuple
    fetched_at: datetime = BASE
    gaps: tuple = ()
    missing_intervals: int = 0
    integrity_ok: bool = True


def _price(index: int, seed: int = 0) -> Decimal:
    value = 100 + seed + math.sin(index / 9) * 5 + index * 0.01
    return Decimal(str(round(value, 8)))


def _candle(open_time: datetime, price: Decimal) -> FakeCandle:
    delta = Decimal("0.5") if int(open_time.timestamp()) % 3 else Decimal("-0.5")
    return FakeCandle(
        open_time=open_time,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price + delta,
        volume=Decimal("1"),
    )


def _series(interval: int, count: int, end_close: datetime, seed: int = 0) -> FakeSeries:
    first_open = end_close - timedelta(minutes=interval * count)
    candles = [
        _candle(first_open + timedelta(minutes=interval * index), _price(index, seed))
        for index in range(count)
    ]
    return FakeSeries(completed=tuple(candles))


def _full_dataset(bars_per_segment: int = 600):
    one_minute = _series(1, bars_per_segment, BASE, seed=0)
    five_minute = _series(5, bars_per_segment, one_minute.completed[0].open_time, seed=1)
    one_hour = _series(60, bars_per_segment, five_minute.completed[0].open_time, seed=2)
    four_hour = _series(240, bars_per_segment, one_hour.completed[0].open_time, seed=3)
    return assemble_dataset(
        INSTRUMENT,
        {240: four_hour, 60: one_hour, 5: five_minute, 1: one_minute},
        provider_id="kraken",
        data_nature="live",
    )


def _flat_dataset(bar_count: int = 23, *, exit_open: str = "110") -> object:
    """All opens 100 except the last bar, which is the trade's exit."""
    candles = []
    for index in range(bar_count):
        price = Decimal(exit_open) if index == bar_count - 1 else Decimal("100")
        candles.append(
            FakeCandle(
                open_time=BASE + timedelta(minutes=index),
                open=price,
                high=max(price, Decimal("100")) + Decimal("1"),
                low=min(price, Decimal("100")) - Decimal("1"),
                close=price,
                volume=Decimal("1"),
            )
        )
    return assemble_dataset(
        INSTRUMENT,
        {1: FakeSeries(completed=tuple(candles))},
        provider_id="kraken",
        data_nature="live",
    )


def _always(direction: int) -> StrategySpec:
    return StrategySpec(
        strategy_id=f"always_{direction}",
        label="Always",
        family="test",
        thesis="test",
        rule="test",
        parameters={},
        warmup_bars=MAX_WARMUP_BARS,
        signal=lambda bars, index: direction,
    )


# -- dataset assembly -------------------------------------------------------


def test_assemble_dataset_stitches_non_overlapping_windows():
    dataset = _full_dataset()

    assert [segment.interval_minutes for segment in dataset.segments] == [240, 60, 5, 1]
    assert len(dataset.bars) == 2400
    assert dataset.segments[0].start_index == 0
    assert dataset.segments[-1].end_index == 2399
    for previous, current in zip(dataset.bars, dataset.bars[1:]):
        assert current.open_time >= previous.close_time
        assert current.open_time > previous.open_time


def test_assemble_dataset_refuses_empty_series():
    with pytest.raises(StrategyLabError) as excinfo:
        assemble_dataset(
            INSTRUMENT,
            {1: FakeSeries(completed=())},
            provider_id="kraken",
            data_nature="live",
        )
    assert excinfo.value.reason == "insufficient_dataset"
    assert "synthetic" in excinfo.value.message


def test_assemble_dataset_refuses_overlap():
    one_minute = _series(1, 100, BASE)
    # Entirely newer than the 1m window: nothing is non-overlapping.
    five_minute = _series(5, 100, BASE + timedelta(hours=10))
    with pytest.raises(StrategyLabError) as excinfo:
        assemble_dataset(
            INSTRUMENT,
            {1: one_minute, 5: five_minute},
            provider_id="kraken",
            data_nature="live",
        )
    assert excinfo.value.reason == "insufficient_dataset"


# -- wallet arithmetic ------------------------------------------------------


def test_long_trade_accounting_matches_declared_costs():
    dataset = _flat_dataset(exit_open="110")
    settings = LabSettings(
        1, Decimal("10000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    result = run_strategy(dataset, _always(1), settings)
    trade = result["first_trade"]

    # qty = 1000 / 100 = 10; entry 100.1; exit 109.89
    assert trade["quantity"] == "10.00000000"
    assert trade["exec_entry"] == "100.10000000"
    assert trade["exec_exit"] == "109.89000000"
    assert Decimal(trade["gross_pnl"]) == Decimal("97.90000000")
    assert Decimal(trade["commission"]) == Decimal("2.09990000")
    assert Decimal(trade["net_pnl"]) == Decimal("95.80010000")
    assert result["ending_cash"] == "10095.80010000"
    assert result["accounting"]["ending_equals_start_plus_net"] is True
    assert result["trades_executed"] == 1
    # The request was satisfied, so no halt reason is reported.
    assert result["halted_reason"] is None


def test_short_trade_accounting_matches_declared_costs():
    dataset = _flat_dataset(exit_open="90")
    settings = LabSettings(
        1, Decimal("10000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    result = run_strategy(dataset, _always(-1), settings)
    trade = result["first_trade"]

    assert trade["side"] == "short"
    assert trade["exec_entry"] == "99.90000000"
    assert trade["exec_exit"] == "90.09000000"
    assert Decimal(trade["gross_pnl"]) == Decimal("98.10000000")
    assert Decimal(trade["commission"]) == Decimal("1.89990000")
    assert Decimal(trade["net_pnl"]) == Decimal("96.20010000")
    assert result["ending_cash"] == "10096.20010000"


def test_short_loss_is_clamped_at_posted_collateral():
    dataset = _flat_dataset(exit_open="250")
    result = run_strategy(dataset, _always(-1), NO_COSTS)
    trade = result["first_trade"]

    assert trade["side"] == "short"
    assert Decimal(trade["gross_pnl"]) == Decimal("-1500.00000000")
    assert Decimal(trade["net_pnl"]) == Decimal("-1000.00000000")
    assert trade["liquidated"] is True
    assert result["ending_cash"] == "9000.00000000"
    assert result["liquidations"] == 1


def test_insufficient_cash_halts_without_a_fabricated_trade():
    dataset = _flat_dataset(exit_open="110")
    settings = LabSettings(
        5, Decimal("1000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    result = run_strategy(dataset, _always(1), settings)

    assert result["trades_executed"] == 0
    assert result["halted_reason"] == "insufficient_cash"
    assert result["ending_cash"] == "1000.00000000"
    assert result["accounting"]["ending_equals_start_plus_net"] is True


# -- full lab ---------------------------------------------------------------


def test_run_lab_completes_1000_trades_per_strategy_in_isolated_wallets():
    dataset = _full_dataset()
    settings = LabSettings(
        1000, Decimal("10000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    result = run_lab(dataset, settings=settings)

    assert result["totals"] == {
        "strategies": 5,
        "trades_requested": 5000,
        "trades_executed": 5000,
        "all_strategies_completed": True,
    }
    assert result["isolation"]["separate_wallets"] is True
    assert result["isolation"]["shared_cash"] is False
    assert result["dataset"]["non_overlapping"] is True
    assert result["dataset"]["synthetic_bars_used"] is False

    hashes = set()
    for summary in result["strategies"]:
        assert summary["trades_executed"] == 1000
        assert summary["completed"] is True
        assert summary["halted_reason"] is None
        assert summary["starting_cash"] == "10000.00000000"
        assert summary["accounting"]["ending_equals_start_plus_net"] is True
        assert Decimal(summary["ending_cash"]) == Decimal("10000") + Decimal(
            summary["net_change"]
        )
        assert summary["direction_counts"]["long"] + summary["direction_counts"]["short"] == 1000
        hashes.add(summary["ledger_sha256"])
    assert len(hashes) == 5

    pairs = result["agreement"]["pairs"]
    assert len(pairs) == 10
    assert all(pair["identical_signal_series"] is False for pair in pairs)


def test_run_lab_is_deterministic_and_order_independent():
    dataset = _full_dataset()
    settings = LabSettings(
        50, Decimal("10000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    first = run_lab(dataset, settings=settings)
    second = run_lab(dataset, settings=settings)
    reversed_run = run_lab(dataset, settings=settings, strategies=list(reversed(STRATEGIES)))

    def fingerprint(result):
        return {
            item["strategy_id"]: (item["ending_cash"], item["ledger_sha256"])
            for item in result["strategies"]
        }

    assert fingerprint(first) == fingerprint(second) == fingerprint(reversed_run)


def test_trade_cap_is_exact():
    dataset = _full_dataset()
    settings = LabSettings(
        7, Decimal("10000"), Decimal("1000"), Decimal("0.001"), Decimal("0.001")
    )
    result = run_lab(dataset, settings=settings, include_trades=True)

    for summary in result["strategies"]:
        assert summary["trades_executed"] == 7
        assert len(summary["trades"]) == 7
        assert [trade["trade_index"] for trade in summary["trades"]] == [1, 2, 3, 4, 5, 6, 7]


def test_run_lab_refuses_dataset_too_short_for_one_trade():
    short = assemble_dataset(
        INSTRUMENT,
        {1: _series(1, MAX_WARMUP_BARS + 1, BASE)},
        provider_id="kraken",
        data_nature="live",
    )
    with pytest.raises(StrategyLabError) as excinfo:
        run_lab(short, settings=LabSettings(1, Decimal("10000"), Decimal("1000"), Decimal("0"), Decimal("0")))
    assert excinfo.value.reason == "insufficient_dataset"
    assert excinfo.value.context["bars_available"] == MAX_WARMUP_BARS + 1


# -- API contract -----------------------------------------------------------


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, rows_by_interval=None, error=None):
        self.rows_by_interval = rows_by_interval or {}
        self.error = error
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        if self.error is not None:
            raise self.error
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


def _install(rows_by_interval=None, error=None):
    provider = KrakenProvider(
        transport=FakeTransport(rows_by_interval, error),
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


def test_plan_declares_five_isolated_strategies(client, auth_headers):
    response = client.get("/api/v1/strategy-lab/plan", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["lane"] == "strategy_lab_simulated"
    assert len(body["strategies"]) == 5
    assert len({item["strategy_id"] for item in body["strategies"]}) == 5
    assert body["isolation"]["separate_wallets"] is True
    assert body["isolation"]["shared_cash"] is False
    assert body["settings"]["trades_per_strategy_default"] == 1000
    assert body["data_plan"]["no_synthetic_substitution"] is True
    assert body["cost_model"]["commission"]
    assert body["limitations"]


def test_plan_requires_authentication(client):
    response = client.get("/api/v1/strategy-lab/plan")
    assert response.status_code == 401


def test_run_executes_requested_trades_on_non_overlapping_windows(client, auth_headers):
    _install(
        {
            240: _ohlc_rows(240, seed=1),
            60: _ohlc_rows(60, seed=2),
            5: _ohlc_rows(5, seed=3),
            1: _ohlc_rows(1, seed=4),
        }
    )
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={"trades_per_strategy": 25, "intervals": [240, 60, 5, 1]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["lane"] == "strategy_lab_simulated"
    assert body["totals"]["trades_requested"] == 125
    assert body["totals"]["trades_executed"] == 125
    assert len(body["strategies"]) == 5
    assert [segment["interval"] for segment in body["dataset"]["segments"]] == [
        "4h",
        "1h",
        "5m",
        "1m",
    ]
    assert body["dataset"]["total_bars"] > 2000
    assert body["dataset"]["non_overlapping"] is True
    assert body["dataset"]["forming_candles_used"] is False
    assert body["dataset"]["synthetic_bars_used"] is False
    assert body["instrument"]["instrument_id"] == INSTRUMENT

    for summary in body["strategies"]:
        assert summary["trades_executed"] == 25
        assert summary["accounting"]["ending_equals_start_plus_net"] is True
        assert Decimal(summary["ending_cash"]) == Decimal(summary["starting_cash"]) + Decimal(
            summary["net_change"]
        )
        assert summary["first_trade"]["segment_interval"] in {"4h", "1h", "5m", "1m"}


def test_run_can_select_one_strategy(client, auth_headers):
    _install(
        {
            240: _ohlc_rows(240, seed=1),
            60: _ohlc_rows(60, seed=2),
            5: _ohlc_rows(5, seed=3),
            1: _ohlc_rows(1, seed=4),
        }
    )
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={"trades_per_strategy": 5, "strategy_ids": ["momentum_roc"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["strategies"]) == 1
    assert body["strategies"][0]["strategy_id"] == "momentum_roc"
    assert body["totals"]["trades_requested"] == 5


def test_run_refuses_venue_outage_without_fallback(client, auth_headers):
    _install(error=ConnectionError("network down"))
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={"trades_per_strategy": 5},
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason"] == "venue_unreachable"
    assert detail["no_fallback"]
    assert detail["available"] is False


def test_run_rejects_unsupported_interval(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={"trades_per_strategy": 5, "intervals": [2]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "unsupported_interval"


def test_run_rejects_unknown_instrument(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={"trades_per_strategy": 5, "instrument_id": "CRYPTO:KRAKEN:ETH-USD"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == "unknown_crypto_instrument"


def test_run_rejects_notional_above_starting_cash(client, auth_headers):
    response = client.post(
        "/api/v1/strategy-lab/run",
        headers=auth_headers,
        json={
            "trades_per_strategy": 5,
            "starting_cash": "500",
            "notional_per_trade": "1000",
        },
    )
    assert response.status_code == 422
