"""An unconfigured data provider is a normal state, not a 500.

``DATA_PROVIDER=twelvedata`` without ``TWELVEDATA_API_KEY`` used to crash the
catalog endpoint. The catalog and already-stored bars must stay readable, new
fetch requests must be refused with a stable ``not_configured`` reason, and no
demo/synthetic series may be substituted to make the page look healthy.
"""

from types import SimpleNamespace

import pytest

from app.providers.data_provider import OHLCVBar, ProviderNotConfiguredError


@pytest.fixture()
def unconfigured_provider(monkeypatch):
    from app.providers.provider_registry import UnconfiguredDataProvider

    provider = UnconfiguredDataProvider("twelvedata")
    monkeypatch.setattr("app.api.instruments.get_data_provider", lambda: provider)
    monkeypatch.setattr("app.api.demo_assets.get_data_provider", lambda: provider)
    monkeypatch.setattr(
        "app.providers.provider_registry.get_data_provider", lambda: provider
    )
    return provider


class TestRegistryStub:
    def _activate(self, monkeypatch):
        import app.providers.provider_registry as registry

        monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
        monkeypatch.setattr(
            registry,
            "get_settings",
            lambda: SimpleNamespace(data_provider="twelvedata"),
        )
        monkeypatch.setattr(registry, "_TWELVEDATA_INITIALIZED", False)
        monkeypatch.setattr(
            registry,
            "_PROVIDERS",
            {k: v for k, v in registry._PROVIDERS.items() if k != "twelvedata"},
        )
        monkeypatch.setattr(registry, "_UNCONFIGURED", {})
        return registry

    def test_missing_key_selects_a_refusing_stub(self, monkeypatch):
        registry = self._activate(monkeypatch)

        provider = registry.get_data_provider()

        assert isinstance(provider, registry.UnconfiguredDataProvider)
        assert provider.provider_id == "twelvedata"
        assert provider.feed_mode == "unavailable"
        assert provider.is_available() is False
        assert registry.is_provider_configured(provider) is False
        assert registry.get_data_provider() is provider  # cached, not re-probed

    def test_stub_refuses_every_upstream_call(self, monkeypatch):
        registry = self._activate(monkeypatch)
        provider = registry.get_data_provider()

        for call in (
            lambda: provider.get_bars("US:AAPL", "2024-01-02", "2024-01-03"),
            lambda: provider.get_latest_bar("US:AAPL"),
            lambda: provider.compute_checksum(["US:AAPL"], "2024-01-02", "2024-01-03"),
        ):
            with pytest.raises(ProviderNotConfiguredError):
                call()

        # No range is claimed, so callers report availability as unknown.
        assert provider.get_available_range("US:AAPL") is None
        assert provider.list_assets() == []
        quality = provider.get_data_quality("US:AAPL")
        assert quality["data_nature"] == "unknown"
        assert quality["timezone"] is None

    def test_status_and_provider_info_are_explicit_and_secret_free(self, monkeypatch):
        registry = self._activate(monkeypatch)

        status = registry.get_data_provider().get_status()
        assert status["configured"] is False
        assert status["reason"] == "not_configured"
        assert status["available"] is False

        info = registry.get_provider_info()
        assert info["active_provider"] == "twelvedata"
        assert info["configured"] is False
        assert info["available"] is False
        assert info["reason"] == "not_configured"
        text = repr(info)
        assert "TWELVEDATA_API_KEY" not in text
        assert "API_KEY" not in text


class TestCatalogStaysReadable:
    def test_instruments_returns_catalog_with_not_configured_status(
        self, client, unconfigured_provider
    ):
        resp = client.get("/api/v1/instruments")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] > 0
        assert body["data_available"] is False
        assert body["data_status"]["state"] == "not_configured"
        assert body["data_status"]["reason"] == "not_configured"
        assert body["data_status"]["provider_id"] == "twelvedata"
        assert body["provider_id"] == "twelvedata"
        # No fabricated series is attached to the catalog.
        assert "bars" not in body["instruments"][0]
        assert "TWELVEDATA_API_KEY" not in resp.text

    def test_demo_assets_listing_does_not_pretend_to_have_data(
        self, client, unconfigured_provider
    ):
        resp = client.get("/api/v1/demo-assets")

        assert resp.status_code == 200
        body = resp.json()
        assert body["assets"] == []
        assert body["provider"]["configured"] is False


class TestNewDataRequestsAreRefused:
    def test_instrument_bars_returns_stable_not_configured_reason(
        self, client, auth_headers, unconfigured_provider
    ):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars",
            params={"start_date": "2024-01-02", "end_date": "2024-01-05"},
            headers=auth_headers,
        )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["reason"] == "provider_not_configured"
        assert detail["available"] is False
        assert detail["provider"] == "twelvedata"
        assert "bars" not in resp.json()
        assert "TWELVEDATA_API_KEY" not in resp.text

    def test_legacy_market_data_bars_returns_same_reason(
        self, client, unconfigured_provider
    ):
        resp = client.get(
            "/api/v1/market-data/bars/AAPL",
            params={"start_date": "2024-01-02", "end_date": "2024-01-05"},
        )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["reason"] == "provider_not_configured"
        assert "provider_status" in detail
        assert "TWELVEDATA_API_KEY" not in resp.text

    def test_refresh_refuses_without_recording_an_outage(
        self, client, admin_headers, unconfigured_provider
    ):
        resp = client.post(
            "/api/v1/instruments/US:AAPL/bars/refresh",
            params={"start_date": "2024-01-02", "end_date": "2024-01-05"},
            headers=admin_headers,
        )

        assert resp.status_code == 503
        assert resp.json()["detail"]["reason"] == "provider_not_configured"

        # An unconfigured deployment must not be written down as a provider
        # outage, so no storage metadata row is created.
        from app.services.market_data_storage import MarketDataStorage
        from tests.conftest import TestSession

        db = TestSession()
        try:
            assert MarketDataStorage(db).get_metadata("US:AAPL", "twelvedata") is None
        finally:
            db.close()


class TestStoredDataStaysReadable:
    def test_previously_stored_bars_are_served_while_unconfigured(
        self, client, unconfigured_provider
    ):
        from app.services.market_data_storage import MarketDataStorage
        from tests.conftest import TestSession

        db = TestSession()
        try:
            MarketDataStorage(db).store_bars(
                instrument_id="US:AAPL",
                provider_id="twelvedata",
                bars=[
                    OHLCVBar(
                        date="2024-01-02",
                        open=150.0,
                        high=152.0,
                        low=149.0,
                        close=151.0,
                        volume=1_000_000,
                    )
                ],
                data_nature="live",
                adjustment_status="unadjusted",
                timezone="America/New_York",
                currency="USD",
            )
        finally:
            db.close()

        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars/stored",
            params={"provider_id": "twelvedata"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "storage"
        assert body["bar_count"] == 1
        assert body["bars"][0]["date"] == "2024-01-02"
        # A stored read never claims a live feed.
        assert body["market_status"] == "disconnected"
        assert body["market_status_reason"] == "provider_unavailable"
