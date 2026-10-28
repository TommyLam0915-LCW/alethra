"""Instrument data coverage: what a provider can serve vs what is cached.

The workspace used to open on a today-anchored range while the only series
available was historical, so the chart drew nothing and said nothing. These
tests pin the contract that lets a client pick a period that actually has bars.
"""

from datetime import datetime, timedelta, timezone

from app.data import real_synthetic_data
from app.providers.data_provider import DataProvider
from app.providers.demo_provider import DemoDataProvider


def test_provider_base_declares_no_range_by_default():
    class Bare(DataProvider):
        @property
        def provider_id(self):
            return "bare"

        @property
        def provider_name(self):
            return "Bare"

        @property
        def feed_mode(self):
            return "offline"

        def list_assets(self):
            return []

        def get_bars(self, symbol, start_date, end_date):
            return []

        def get_latest_bar(self, symbol):
            return None

        def get_date_range(self):
            return {}

        def compute_checksum(self, symbols, start_date, end_date):
            return ""

    assert Bare().get_available_range("US:AAPL") is None


class TestDemoProviderAvailableRange:
    def test_real_instrument_is_the_generated_synthetic_window(self):
        rng = DemoDataProvider().get_available_range("US:AAPL")
        declared = real_synthetic_data.get_real_date_range()
        assert rng == {"start": declared["min"], "end": declared["max"]}

    def test_legacy_demo_asset_is_its_own_series_window(self):
        rng = DemoDataProvider().get_available_range("DEMO-ALPHA")
        assert rng is not None
        assert rng["start"] < rng["end"]

    def test_unknown_instrument_has_no_declared_range(self):
        assert DemoDataProvider().get_available_range("US:UNKNOWN") is None


class TestInstrumentDetailCoverage:
    def test_coverage_names_the_provider_window(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL")
        assert resp.status_code == 200
        cov = resp.json()["data_coverage"]
        declared = real_synthetic_data.get_real_date_range()
        assert cov["availability_source"] == "provider_declared"
        assert cov["available_start"] == declared["min"]
        assert cov["available_end"] == declared["max"]
        assert cov["timeframe"] == "1d"
        assert cov["provider_id"] == "demo"

    def test_declared_window_actually_returns_bars(self, client):
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars"
            f"?start_date={cov['available_start']}&end_date={cov['available_end']}"
        )
        assert resp.status_code == 200
        assert resp.json()["bar_count"] > 0

    def test_recent_window_has_no_bars_while_coverage_is_populated(self, client):
        """The empty-chart case: a current-anchored request, valid coverage."""
        today = datetime.now(timezone.utc).date()
        start = (today - timedelta(days=90)).isoformat()
        resp = client.get(
            f"/api/v1/instruments/US:AAPL/bars?start_date={start}&end_date={today.isoformat()}"
        )
        assert resp.status_code == 200
        assert resp.json()["bar_count"] == 0
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        assert cov["available_end"] < today.isoformat()

    def test_stored_coverage_tracks_what_has_been_cached(self, client):
        client.get(
            "/api/v1/instruments/US:AAPL/bars?start_date=2024-01-02&end_date=2024-03-28"
        )
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        assert cov["stored_bars"] > 0
        assert cov["stored_start"] <= cov["stored_end"]
        assert cov["stored_end"] <= cov["available_end"]

    def test_unavailable_provider_leaves_availability_unknown(self, client, monkeypatch):
        class NoRangeProvider(DemoDataProvider):
            def get_available_range(self, instrument_id):
                return None

        monkeypatch.setattr(
            "app.api.instruments.get_data_provider", lambda: NoRangeProvider()
        )
        cov = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        assert cov["availability_source"] == "unknown"
        assert cov["available_start"] is None
        assert cov["available_end"] is None


class TestInstrumentListWindow:
    """The picker labels every listed instrument, so the list must answer availability."""

    def test_each_row_declares_its_window(self, client):
        rows = client.get("/api/v1/instruments").json()["instruments"]
        assert rows, "the instrument list must not be empty"
        declared = real_synthetic_data.get_real_date_range()
        for row in rows:
            window = row["data_window"]
            assert window["availability_source"] == "provider_declared"
            assert window["available_start"] == declared["min"]
            assert window["available_end"] == declared["max"]

    def test_list_window_agrees_with_the_detail_window(self, client):
        row = next(
            r
            for r in client.get("/api/v1/instruments", params={"q": "AAPL"}).json()["instruments"]
            if r["instrument_id"] == "US:AAPL"
        )
        detail = client.get("/api/v1/instruments/US:AAPL").json()["data_coverage"]
        assert row["data_window"]["available_start"] == detail["available_start"]
        assert row["data_window"]["available_end"] == detail["available_end"]

    def test_provider_without_a_window_reports_unknown(self, client, monkeypatch):
        class NoRangeProvider(DemoDataProvider):
            def get_available_range(self, instrument_id):
                return None

        monkeypatch.setattr(
            "app.api.instruments.get_data_provider", lambda: NoRangeProvider()
        )
        rows = client.get("/api/v1/instruments", params={"limit": 3}).json()["instruments"]
        for row in rows:
            window = row["data_window"]
            assert window["availability_source"] == "unknown"
            assert window["available_start"] is None
            assert window["available_end"] is None
