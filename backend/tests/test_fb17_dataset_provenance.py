"""FB-17: reconcile stored dataset provenance and row counts.

The 2026-11-01 observation: a stored-bars request for an empty window answered
HTTP 200 with 183 bars. The route reads ``start_date``/``end_date``, FastAPI
silently drops an unrecognised name, so a caller filtering with ``start``/``end``
received every stored row and read the total as if it were the requested range.
The same response then reported one provider's metadata (43 rows) beside a count
that actually spanned two stored datasets (140 + 43).

These tests pin the contract that makes a count attributable:

- an unknown query parameter is refused, never silently ignored;
- ``providers`` splits ``bar_count`` by storage provider and sums to it;
- ``data_quality.data_nature`` is ``mixed`` when the returned rows carry
  different natures;
- a run records the dataset it priced off and ``bars_used`` matches
  ``metrics["trading_days"]``.

The 140 vs 134 difference is not a constant to change: the seeded fixture is a
weekday series that includes exchange holidays, while the generator's universe
is holiday-aware. The tests below assert that each producer's rows are reported
as stored — including the holiday-row case — rather than being rewritten.
"""

import os
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data import real_synthetic_data
from app.engine.backtest import run_backtest
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import MarketDataStorage

_TEST_DB_PATH = os.environ.get("ALETHRA_TEST_DB", "sqlite:///./test.db")
_ENGINE_KWARGS = (
    {"connect_args": {"check_same_thread": False}}
    if _TEST_DB_PATH.startswith("sqlite")
    else {"pool_pre_ping": True}
)
_test_engine = create_engine(_TEST_DB_PATH, **_ENGINE_KWARGS)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


@pytest.fixture()
def db_session():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def storage(db_session):
    return MarketDataStorage(db_session)


def _weekdays(start: date, count: int):
    """Weekday series, holidays included — the shape of the seeded fixture.

    ``seed_candidate_scenarios`` builds its 140 rows this way, so 2024-01-15
    (MLK Day) is present. ``real_synthetic_data`` removes its own US holiday
    set, which is why the same date range is 140 rows from one producer and 134
    from the other: neither count is wrong, they are different datasets.
    """
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _bars(days, base_price=100.0):
    return [
        OHLCVBar(
            date=day.isoformat(),
            open=base_price + i,
            high=base_price + i + 1,
            low=base_price + i - 1,
            close=base_price + i,
            volume=1000 + i,
        )
        for i, day in enumerate(days)
    ]


@pytest.fixture()
def two_provider_rows(storage):
    """The candidate's actual storage shape: a fixture plus a demo fetch.

    Both rows are labelled ``synthetic`` because the demo provider serves the
    deterministic generator — the candidate DB shows 140 synthetic-fixture rows
    and 43 demo rows, both with ``data_nature='synthetic'``.
    """
    synthetic_days = _weekdays(date(2024, 1, 15), 5)
    demo_days = _weekdays(date(2024, 7, 8), 3)
    storage.store_bars(
        "US:AAPL", "synthetic", _bars(synthetic_days), "synthetic"
    )
    storage.store_bars("US:AAPL", "demo", _bars(demo_days), "synthetic")
    return synthetic_days, demo_days


class TestStoredBarsRequestStrictness:
    def test_start_end_are_refused_not_silently_dropped(self, client):
        """The exact 183-row defect: an ignored parameter must not look filtered."""
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored"
            "?start=2025-06-01&end=2025-06-10"
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "Unrecognised query parameter(s): end, start." in detail
        assert "start_date" in detail
        assert "bars" not in resp.json()

    def test_unknown_parameter_is_refused_even_when_data_exists(
        self, client, two_provider_rows
    ):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored", params={"from": "2024-01-01"}
        )
        assert resp.status_code == 422
        assert "from" in resp.json()["detail"]

    def test_malformed_start_date_is_400_not_500(self, client):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={"start_date": "01/02/2024"},
        )
        assert resp.status_code == 400
        assert "ISO dates" in resp.json()["detail"]

    def test_reversed_range_is_400(self, client):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={"start_date": "2024-07-10", "end_date": "2024-07-01"},
        )
        assert resp.status_code == 400
        assert "on or before" in resp.json()["detail"]

    def test_window_with_no_rows_returns_zero_and_says_so(
        self, client, two_provider_rows
    ):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={"start_date": "2025-06-01", "end_date": "2025-06-10"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["bar_count"] == 0
        assert body["bars"] == []
        assert body["providers"] == []
        assert "No stored bars in the requested window" in body["message"]


class TestStoredBarsProviderBreakdown:
    def test_count_is_split_by_provider_and_sums_to_total(
        self, client, two_provider_rows
    ):
        synthetic_days, demo_days = two_provider_rows
        body = client.get("/api/v1/instruments/US:AAPL/bars/stored").json()

        assert body["bar_count"] == len(synthetic_days) + len(demo_days)
        assert sum(p["bar_count"] for p in body["providers"]) == body["bar_count"]

        by_provider = {p["provider_id"]: p for p in body["providers"]}
        assert by_provider["synthetic"]["bar_count"] == len(synthetic_days)
        assert by_provider["synthetic"]["data_nature"] == "synthetic"
        assert by_provider["synthetic"]["first_bar_date"] == synthetic_days[0].isoformat()
        assert by_provider["synthetic"]["last_bar_date"] == synthetic_days[-1].isoformat()
        assert by_provider["demo"]["bar_count"] == len(demo_days)
        assert by_provider["demo"]["data_nature"] == "synthetic"
        assert by_provider["demo"]["first_bar_date"] == demo_days[0].isoformat()
        assert by_provider["demo"]["last_bar_date"] == demo_days[-1].isoformat()

    def test_rows_of_one_nature_report_that_nature(self, client, two_provider_rows):
        body = client.get("/api/v1/instruments/US:AAPL/bars/stored").json()
        assert body["series_nature"] == "synthetic"
        assert body["data_quality"]["data_nature"] == "synthetic"
        assert body["data_quality"]["metadata_data_nature"] == "synthetic"
        assert body["data_quality"]["returned_bars"] == body["bar_count"]

    def test_differing_natures_are_reported_as_mixed(
        self, client, two_provider_rows, storage
    ):
        live_days = _weekdays(date(2024, 8, 5), 2)
        storage.store_bars("US:AAPL", "kraken", _bars(live_days), "live")

        body = client.get("/api/v1/instruments/US:AAPL/bars/stored").json()
        assert body["bar_count"] == 10
        assert body["series_nature"] == "mixed"
        assert body["data_quality"]["data_nature"] == "mixed"
        assert {p["data_nature"] for p in body["providers"]} == {
            "synthetic", "live"
        }

    def test_date_window_returns_only_that_window_across_providers(
        self, client, two_provider_rows
    ):
        _, demo_days = two_provider_rows
        body = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={
                "start_date": demo_days[0].isoformat(),
                "end_date": demo_days[-1].isoformat(),
            },
        ).json()

        assert body["bar_count"] == len(demo_days)
        assert [b["date"] for b in body["bars"]] == [
            d.isoformat() for d in demo_days
        ]
        assert {p["provider_id"] for p in body["providers"]} == {"demo"}

    def test_provider_filter_binds_one_dataset(self, client, two_provider_rows):
        body = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={"provider_id": "synthetic"},
        ).json()
        assert body["bar_count"] == 5
        assert body["series_nature"] == "synthetic"
        assert body["data_quality"]["data_nature"] == "synthetic"
        assert [p["provider_id"] for p in body["providers"]] == ["synthetic"]

    def test_holiday_weekday_rows_are_counted_as_stored(
        self, client, two_provider_rows
    ):
        """One week, two producers, two counts — both reported as they are."""
        window = {"start_date": "2024-01-15", "end_date": "2024-01-19"}
        body = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={**window, "provider_id": "synthetic"},
        ).json()
        stored_dates = [b["date"] for b in body["bars"]]
        assert stored_dates == [
            "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"
        ]
        assert body["bar_count"] == 5

        # The generator behind the 761-day universe holds its own US holiday
        # set and drops 2024-01-15 (MLK Day). Same window, different dataset:
        # 5 stored rows vs 4 generated bars. Storing the fixture does not
        # rewrite either producer.
        generated = real_synthetic_data.get_synthetic_bars(
            "US:AAPL", window["start_date"], window["end_date"]
        )
        assert len(generated) == 4
        assert "2024-01-15" not in [b["date"] for b in generated]


class TestCoverageShowsEveryStoredDataset:
    def test_detail_coverage_lists_both_providers(self, client, two_provider_rows):
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        datasets = {d["provider_id"]: d for d in cov["stored_datasets"]}
        assert set(datasets) == {"synthetic", "demo"}
        assert datasets["synthetic"]["bar_count"] == 5
        assert datasets["demo"]["bar_count"] == 3
        assert cov["stored_rows_total"] == 8

    def test_configured_provider_total_is_not_presented_as_the_whole(self, client, two_provider_rows):
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        # The configured provider is demo, whose own total is 3; the storage
        # state is 8. Both numbers must be visible side by side.
        assert cov["provider_id"] == "demo"
        assert cov["stored_bars"] == 3
        assert cov["stored_rows_total"] == 8


class _StoredRun:
    def __init__(self, storage, days):
        self.days = days
        storage.store_bars("US:AAPL", "synthetic", _bars(days), "synthetic")

    def run(self, session):
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["US:AAPL"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        return run_backtest(
            spec, db_session=session, data_source="stored", provider_id="synthetic"
        )


class TestRunRecordsDatasetCoverage:
    def test_stored_run_pins_source_provider_and_rows(self, db_session, storage):
        days = _weekdays(date(2024, 1, 1), 22)
        result = _StoredRun(storage, days).run(db_session)

        assert result["run_status"] == "completed"
        coverage = result["dataset_coverage"]["US:AAPL"]
        assert coverage["source"] == "stored"
        assert coverage["provider_id"] == "synthetic"
        assert coverage["provider_ids"] == ["synthetic"]
        assert coverage["data_nature"] == "synthetic"
        assert coverage["bar_count"] == len(days)
        assert coverage["first_bar_date"] == days[0].isoformat()
        assert coverage["last_bar_date"] == days[-1].isoformat()

    def test_bars_used_matches_the_reported_metric(self, db_session, storage):
        days = _weekdays(date(2024, 1, 1), 22)
        result = _StoredRun(storage, days).run(db_session)
        assert result["bars_used"] == result["metrics"]["trading_days"] == len(days)

    def test_checksum_is_stable_for_the_same_stored_series(self, db_session, storage):
        days = _weekdays(date(2024, 1, 1), 22)
        run = _StoredRun(storage, days)
        first = run.run(db_session)
        second = run.run(db_session)
        assert first["dataset_checksum"] == second["dataset_checksum"]
        assert first["dataset_checksum"] is not None
        assert len(first["dataset_checksum"]) == 64

    def test_provider_run_states_the_provider_it_priced_off(self, db_session):
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        result = run_backtest(spec, db_session=db_session, data_source="auto")

        assert result["data_source_used"] == "provider"
        coverage = result["dataset_coverage"]["DEMO-ALPHA"]
        assert coverage["source"] == "provider"
        assert coverage["provider_id"] == "demo"
        assert coverage["provider_ids"] == []
        assert coverage["bar_count"] == result["bars_used"]
        assert result["bars_used"] == result["metrics"]["trading_days"]
