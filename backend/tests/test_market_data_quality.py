"""Offline contract tests for market data quality metadata and provider health.

Covers:
- compute_staleness (deterministic, unit-level)
- DemoDataProvider.get_data_quality (synthetic labeling, timezone from instrument)
- LSEG provider health: 429 rate-limit fail-fast, bounded retry on transient
  failures, health status transitions — all against a fake transport, no network
- Bars/latest API endpoints expose data_quality and map provider failures to 503
"""

from datetime import date

import pytest
import requests

from app.providers.data_provider import STALE_THRESHOLD_DAYS, compute_staleness
from app.providers.demo_provider import DemoDataProvider
from app.providers.lseg_provider import (
    LSEGDataError,
    LSEGDataProvider,
    LSEGRateLimitError,
)


class TestComputeStaleness:
    def test_fresh_bar_not_stale(self):
        result = compute_staleness("2026-01-10", today=date(2026, 1, 12))
        assert result["is_stale"] is False
        assert result["bar_age_days"] == 2

    def test_bar_older_than_threshold_is_stale(self):
        result = compute_staleness("2026-01-01", today=date(2026, 1, 12))
        assert result["is_stale"] is True
        assert result["bar_age_days"] == 11

    def test_bar_at_exactly_threshold_not_stale(self):
        result = compute_staleness("2026-01-05", today=date(2026, 1, 12))
        assert result["is_stale"] is False

    def test_no_bars_is_stale(self):
        result = compute_staleness(None, today=date(2026, 1, 12))
        assert result["is_stale"] is True
        assert result["bar_age_days"] is None
        assert result["latest_bar_date"] is None

    def test_malformed_date_is_stale(self):
        result = compute_staleness("not-a-date", today=date(2026, 1, 12))
        assert result["is_stale"] is True

    def test_threshold_reported(self):
        result = compute_staleness("2026-01-01", today=date(2026, 1, 12))
        assert result["stale_threshold_days"] == STALE_THRESHOLD_DAYS


class TestDemoDataQuality:
    def test_real_instrument_synthetic_with_timezone(self):
        provider = DemoDataProvider()
        quality = provider.get_data_quality("US:AAPL")
        assert quality["data_nature"] == "synthetic"
        assert quality["adjustment"] == "not_applicable"
        assert quality["timezone"] == "America/New_York"
        assert "synthetic" in quality["note"].lower()
        assert quality["note_zh"] is not None

    def test_hk_instrument_timezone(self):
        provider = DemoDataProvider()
        quality = provider.get_data_quality("HK:0700")
        assert quality["timezone"] == "Asia/Hong_Kong"

    def test_legacy_demo_asset_has_no_timezone_claim(self):
        provider = DemoDataProvider()
        quality = provider.get_data_quality("DEMO-ALPHA")
        assert quality["data_nature"] == "synthetic"
        assert quality["timezone"] is None

    def test_unknown_symbol_still_labeled_synthetic(self):
        provider = DemoDataProvider()
        quality = provider.get_data_quality("US:NOPE")
        assert quality["data_nature"] == "synthetic"
        assert quality["timezone"] is None

    def test_base_provider_reports_unknown(self):
        from app.providers.data_provider import DataProvider

        class MinimalProvider(DataProvider):
            @property
            def provider_id(self):
                return "minimal"

            @property
            def provider_name(self):
                return "Minimal"

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

        quality = MinimalProvider().get_data_quality("ANY")
        assert quality["data_nature"] == "unknown"
        assert quality["adjustment"] == "unknown"
        assert quality["timezone"] is None


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


EOD_PAYLOAD = {
    "captures": [
        {
            "trading_date": "2024-01-02",
            "open": "100.0",
            "high": "101.0",
            "low": "99.0",
            "close": "100.5",
            "volume": "1000",
        }
    ]
}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.responses.pop(0) if self.responses else FakeResponse(200, {})
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


def make_connected_provider(responses) -> LSEGDataProvider:
    provider = LSEGDataProvider(api_key="test-key-not-real")
    provider._session = FakeSession(responses)
    provider._connected = True
    provider._sleep = lambda seconds: None
    return provider


class TestLSEGRateLimiting:
    def test_429_starts_rate_limit_window(self):
        provider = make_connected_provider([
            FakeResponse(429, {"error": "quota"}, headers={"Retry-After": "90"}),
        ])
        with pytest.raises(LSEGRateLimitError, match="retry after 90s"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")

    def test_subsequent_call_fails_fast_without_network(self):
        provider = make_connected_provider([
            FakeResponse(429, {}, headers={"Retry-After": "120"}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        calls_after_first = len(provider._session.calls)
        assert calls_after_first == 1

        with pytest.raises(LSEGRateLimitError, match="rate limit active"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(provider._session.calls) == calls_after_first

    def test_rate_limit_window_never_retried(self):
        provider = make_connected_provider([
            FakeResponse(429, {}, headers={"Retry-After": "60"}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(provider._session.calls) == 1

    def test_status_reports_rate_limited_with_remaining_seconds(self):
        provider = make_connected_provider([
            FakeResponse(429, {}, headers={"Retry-After": "120"}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")

        status = provider.get_status()
        assert status["rate_limited"] is True
        assert status["health"] == "rate_limited"
        assert 0 < status["rate_limited_retry_after_seconds"] <= 120

    def test_missing_retry_after_header_uses_default(self):
        provider = make_connected_provider([
            FakeResponse(429, {}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert provider._rate_limited_retry_after == 60

    def test_retry_after_capped_at_maximum(self):
        provider = make_connected_provider([
            FakeResponse(429, {}, headers={"Retry-After": "99999"}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert provider._rate_limited_retry_after == 300

    def test_rate_limit_error_is_lseg_data_error_subclass(self):
        provider = make_connected_provider([
            FakeResponse(429, {}),
        ])
        with pytest.raises(LSEGDataError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")

    def test_disconnect_clears_rate_limit_window(self):
        provider = make_connected_provider([
            FakeResponse(429, {}, headers={"Retry-After": "120"}),
        ])
        with pytest.raises(LSEGRateLimitError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        provider.disconnect()
        status = provider.get_status()
        assert status["rate_limited"] is False
        assert status["health"] == "disconnected"


class TestLSEGRetryPolicy:
    def test_5xx_retried_then_success(self):
        provider = make_connected_provider([
            FakeResponse(503),
            FakeResponse(503),
            FakeResponse(200, EOD_PAYLOAD),
        ])
        bars = provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(bars) == 1
        assert len(provider._session.calls) == 3

    def test_5xx_exhausts_bounded_retries(self):
        provider = make_connected_provider([
            FakeResponse(500),
            FakeResponse(500),
            FakeResponse(500),
        ])
        with pytest.raises(LSEGDataError, match="after retries"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(provider._session.calls) == 3

    def test_timeout_retried_then_success(self):
        provider = make_connected_provider([
            requests.exceptions.Timeout("timed out"),
            FakeResponse(200, EOD_PAYLOAD),
        ])
        bars = provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(bars) == 1

    def test_timeout_exhausts_retries(self):
        provider = make_connected_provider([
            requests.exceptions.Timeout("timed out"),
            requests.exceptions.Timeout("timed out"),
            requests.exceptions.Timeout("timed out"),
        ])
        with pytest.raises(LSEGDataError, match="failed after retries"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(provider._session.calls) == 3

    def test_client_error_not_retried(self):
        provider = make_connected_provider([
            FakeResponse(404),
        ])
        with pytest.raises(LSEGDataError, match="HTTP 404"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(provider._session.calls) == 1

    def test_success_resets_consecutive_errors(self):
        provider = make_connected_provider([
            FakeResponse(500),
            FakeResponse(500),
            FakeResponse(500),
        ])
        with pytest.raises(LSEGDataError):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert provider._consecutive_errors == 1

        provider._session.responses = [FakeResponse(200, EOD_PAYLOAD)]
        provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert provider._consecutive_errors == 0
        assert provider._last_success_at is not None


class TestLSEGHealthStatus:
    def test_repeated_errors_lead_to_degraded_health(self):
        provider = make_connected_provider([])
        for _ in range(3):
            provider._session.responses = [requests.exceptions.Timeout("t")] * 3
            with pytest.raises(LSEGDataError):
                provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        status = provider.get_status()
        assert status["consecutive_errors"] == 3
        assert status["health"] == "degraded"
        assert status["last_error_at"] is not None

    def test_fresh_connected_provider_is_ok(self):
        provider = make_connected_provider([FakeResponse(200, EOD_PAYLOAD)])
        provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        status = provider.get_status()
        assert status["health"] == "ok"
        assert status["consecutive_errors"] == 0

    def test_disconnected_health(self):
        provider = LSEGDataProvider()
        assert provider.get_status()["health"] == "disconnected"

    def test_health_status_fields_present(self):
        provider = LSEGDataProvider()
        status = provider.get_status()
        assert "health" in status
        assert "rate_limited" in status
        assert "consecutive_errors" in status
        assert "last_success_at" in status
        assert "last_error_at" in status


class TestLSEGDataQuality:
    def test_adjustment_unknown_when_disconnected(self):
        provider = LSEGDataProvider()
        quality = provider.get_data_quality("US:AAPL")
        assert quality["adjustment"] == "unknown"
        assert quality["data_nature"] == "unknown"
        assert quality["timezone"] == "America/New_York"

    def test_data_nature_live_when_connected(self):
        provider = make_connected_provider([FakeResponse(200, EOD_PAYLOAD)])
        assert provider.get_data_quality("US:AAPL")["data_nature"] == "live"


class FailingProvider:
    provider_id = "failing"
    provider_name = "Failing (Test)"
    feed_mode = "disconnected"

    def is_available(self):
        return False

    def get_bars(self, symbol, start_date, end_date):
        raise LSEGDataError("provider not connected. Call connect() first.")

    def get_latest_bar(self, symbol):
        raise LSEGDataError("provider not connected. Call connect() first.")

    def get_status(self):
        return {
            "provider_id": "failing",
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": False,
            "connected": False,
            "health": "disconnected",
            "last_error": "provider not connected. Call connect() first.",
        }

    def get_data_quality(self, symbol):
        return {
            "data_nature": "unknown",
            "adjustment": "unknown",
            "timezone": None,
            "note": None,
        }


class TestBarsAPIProviderFailure:
    def test_instrument_bars_returns_503_with_provider_status(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.instruments.get_data_provider", lambda: FailingProvider()
        )
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars?start_date=2024-01-02&end_date=2024-03-31"
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "US:AAPL" in detail["error"]
        assert detail["provider_status"]["provider_id"] == "failing"
        assert detail["provider_status"]["last_error"] is not None

    def test_instrument_detail_returns_503_when_latest_bar_fails(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.instruments.get_data_provider", lambda: FailingProvider()
        )
        resp = client.get("/api/v1/instruments/US:AAPL")
        assert resp.status_code == 503
        assert resp.json()["detail"]["provider_status"]["health"] == "disconnected"

    def test_market_data_bars_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.demo_assets.get_data_provider", lambda: FailingProvider()
        )
        resp = client.get("/api/v1/market-data/bars/DEMO-ALPHA?start_date=2024-01-02&end_date=2024-02-13")
        assert resp.status_code == 503

    def test_market_data_latest_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.demo_assets.get_data_provider", lambda: FailingProvider()
        )
        resp = client.get("/api/v1/market-data/latest/DEMO-ALPHA")
        assert resp.status_code == 503


class TestBarsAPIDataQuality:
    def test_instrument_bars_include_data_quality(self, client):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars?start_date=2024-01-02&end_date=2024-03-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        quality = data["data_quality"]
        assert quality["data_nature"] == "synthetic"
        assert quality["adjustment"] == "not_applicable"
        assert quality["timezone"] == "America/New_York"
        assert quality["is_stale"] is True
        assert quality["bar_age_days"] is not None
        assert quality["stale_threshold_days"] == STALE_THRESHOLD_DAYS

    def test_instrument_detail_includes_data_quality(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["data_quality"] is not None
        assert data["data_quality"]["data_nature"] == "synthetic"
        assert data["data_quality"]["latest_bar_date"] == data["latest_bar"]["date"]

    def test_market_data_bars_include_data_quality(self, client):
        resp = client.get("/api/v1/market-data/bars/DEMO-ALPHA?start_date=2024-01-02&end_date=2024-02-13")
        assert resp.status_code == 200
        quality = resp.json()["data_quality"]
        assert quality["data_nature"] == "synthetic"
        assert quality["timezone"] is None

    def test_market_data_latest_includes_data_quality(self, client):
        resp = client.get("/api/v1/market-data/latest/DEMO-ALPHA")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        quality = data["data_quality"]
        assert quality["latest_bar_date"] == data["bar"]["date"]
        assert quality["is_stale"] is True

    def test_bars_empty_range_still_reports_staleness(self, client):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars?start_date=2030-01-01&end_date=2030-01-31"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["bar_count"] == 0
        assert data["data_quality"]["is_stale"] is True
        assert data["data_quality"]["latest_bar_date"] is None
