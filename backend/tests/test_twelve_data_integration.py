"""Integration tests for Twelve Data provider wiring.

Covers:
A. known-provider configuration accepts "twelve_data"
B. registry selects TwelveDataProvider with a fake key
C. missing key fails without fallback to demo
D. list_assets() provides valid instrument identities
E. TestClient request through real provider selection,
   mocked external HTTP only

All credentials are fake. All external HTTP is mocked.
"""

import os
from unittest.mock import patch, Mock

import pytest

from app.config import get_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_settings(**env_overrides):
    """Build a Settings object without touching .env or real environ."""
    env = {
        "APP_ENV": "development",
        "JWT_SECRET_KEY": "change-me-in-production-use-a-long-random-string",
    }
    env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        from app.config import Settings
        return Settings(_env_file=None)


@pytest.fixture
def reset_registry_state():
    """Save and restore provider_registry module-level state."""
    import app.providers.provider_registry as reg

    saved_providers = dict(reg._PROVIDERS)
    saved_lseg = reg._LSEG_INITIALIZED
    saved_td = reg._TWELVE_DATA_INITIALIZED

    yield

    reg._PROVIDERS.clear()
    reg._PROVIDERS.update(saved_providers)
    reg._LSEG_INITIALIZED = saved_lseg
    reg._TWELVE_DATA_INITIALIZED = saved_td
    get_settings.cache_clear()


@pytest.fixture
def reset_registry_on_failure(reset_registry_state):
    """Like reset_registry_state but the ValueError path is also cleaned."""
    yield


# ---------------------------------------------------------------------------
# A. Known-provider configuration accepts twelve_data
# ---------------------------------------------------------------------------

class TestA_KnownProviderConfig:
    """DATA_PROVIDER=twelve_data must pass validation."""

    def test_production_accepts_twelve_data(self, reset_registry_state):
        """Production mode accepts twelve_data as a known data provider."""
        s = _fresh_settings(
            APP_ENV="production",
            JWT_SECRET_KEY="x" * 48,
            DATABASE_URL="postgresql://user:pass@db.example.com:5432/alethra",
            CORS_ORIGINS="https://app.alethra.com",
            DATA_PROVIDER="twelve_data",
        )
        assert s.data_provider == "twelve_data"

    def test_development_accepts_twelve_data(self, reset_registry_state):
        """Development mode accepts twelve_data."""
        s = _fresh_settings(DATA_PROVIDER="twelve_data")
        assert s.data_provider == "twelve_data"


# ---------------------------------------------------------------------------
# B. Registry selects TwelveDataProvider with a fake key
# ---------------------------------------------------------------------------

class TestB_RegistrySelection:
    """Registry must return a TwelveDataProvider when key is present."""

    def test_registry_returns_twelve_data_provider(self, reset_registry_state, monkeypatch):
        """With TWELVE_DATA_API_KEY set, get_data_provider() returns TwelveDataProvider."""
        from app.providers.provider_registry import get_data_provider
        from app.providers.twelve_data_provider import TwelveDataProvider

        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "fake-test-key-12345")
        get_settings.cache_clear()

        provider = get_data_provider()
        assert isinstance(provider, TwelveDataProvider)
        assert provider.provider_id == "twelve_data"
        assert provider.is_available()
        assert provider._api_key == "fake-test-key-12345"


# ---------------------------------------------------------------------------
# C. Missing key fails without fallback to demo
# ---------------------------------------------------------------------------

class TestC_MissingKeyFailsClear:
    """Missing TWELVE_DATA_API_KEY must raise ValueError, never return demo."""

    def test_missing_key_raises_value_error(self, reset_registry_on_failure, monkeypatch):
        """Without TWELVE_DATA_API_KEY, get_data_provider() raises ValueError."""
        from app.providers.provider_registry import get_data_provider

        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        get_settings.cache_clear()

        with pytest.raises(ValueError) as exc_info:
            get_data_provider()

        error_msg = str(exc_info.value)
        assert "TWELVE_DATA_API_KEY" in error_msg
        assert "missing" in error_msg.lower()

    def test_missing_key_does_not_return_demo(self, reset_registry_on_failure, monkeypatch):
        """Verify the error propagates — demo is never silently returned."""
        from app.providers.provider_registry import get_data_provider
        from app.providers.demo_provider import DemoDataProvider

        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        get_settings.cache_clear()

        with pytest.raises(ValueError):
            get_data_provider()

    def test_empty_key_raises_value_error(self, reset_registry_on_failure, monkeypatch):
        """Empty string TWELVE_DATA_API_KEY also fails clearly."""
        from app.providers.provider_registry import get_data_provider

        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "")
        get_settings.cache_clear()

        with pytest.raises(ValueError, match="TWELVE_DATA_API_KEY"):
            get_data_provider()

    def test_retry_possible_after_failure(self, reset_registry_on_failure, monkeypatch):
        """After a missing-key failure, setting the key allows a successful retry."""
        from app.providers.provider_registry import get_data_provider
        from app.providers.twelve_data_provider import TwelveDataProvider

        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
        get_settings.cache_clear()

        # First attempt: fails
        with pytest.raises(ValueError):
            get_data_provider()

        # Set the key and retry
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "fake-retry-key")
        get_settings.cache_clear()

        provider = get_data_provider()
        assert isinstance(provider, TwelveDataProvider)
        assert provider._api_key == "fake-retry-key"


# ---------------------------------------------------------------------------
# D. list_assets() provides valid instrument identities
# ---------------------------------------------------------------------------

class TestD_ListAssets:
    """list_assets() must use Instrument.create_us() and return valid IDs."""

    def test_list_assets_returns_instrument_id(self, reset_registry_state):
        """Each asset has a provider-independent instrument_id."""
        from app.providers.twelve_data_provider import TwelveDataProvider

        provider = TwelveDataProvider(api_key="fake-key")
        assets = provider.list_assets()

        assert len(assets) > 0
        for asset in assets:
            assert asset.instrument is not None, (
                f"Asset {asset.symbol} has no Instrument object"
            )
            assert ":" in asset.instrument.instrument_id, (
                f"instrument_id {asset.instrument.instrument_id!r} missing ':'"
            )
            assert asset.instrument.market.value == "US"
            assert asset.instrument.asset_type.value == "STOCK"
            assert asset.instrument.currency.value == "USD"

    def test_list_assets_aapl_instrument_id(self, reset_registry_state):
        """AAPL has instrument_id 'US:AAPL'."""
        from app.providers.twelve_data_provider import TwelveDataProvider

        provider = TwelveDataProvider(api_key="fake-key")
        assets = provider.list_assets()
        aapl_assets = [a for a in assets if a.symbol == "AAPL"]
        assert len(aapl_assets) == 1
        assert aapl_assets[0].instrument.instrument_id == "US:AAPL"


# ---------------------------------------------------------------------------
# E. TestClient endpoint test with mocked HTTP
# ---------------------------------------------------------------------------

MOCK_TWELVE_DATA_RESPONSE = {
    "meta": {
        "symbol": "AAPL",
        "interval": "1day",
        "currency": "USD",
        "exchange_timezone": "America/New_York",
        "exchange": "NASDAQ",
        "mic_code": "XNGS",
        "type": "Common Stock",
    },
    "values": [
        {
            "datetime": "2024-01-03",
            "open": "185.25",
            "high": "186.50",
            "low": "184.80",
            "close": "185.90",
            "volume": "50000000",
        },
        {
            "datetime": "2024-01-02",
            "open": "183.50",
            "high": "184.90",
            "low": "182.75",
            "close": "184.20",
            "volume": "45000000",
        },
    ],
    "status": "ok",
}


class TestE_EndpointIntegration:
    """GET /api/v1/market-data/bars/US:AAPL through full provider selection."""

    @patch("requests.Session.get")
    def test_endpoint_returns_bars_with_provenance(
        self, mock_session_get, client, monkeypatch, reset_registry_state
    ):
        """Full integration test: TestClient -> provider selection -> mocked HTTP."""
        # Configure provider via environment
        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "fake-key-for-integration-test")
        get_settings.cache_clear()

        # Mock the external HTTP response
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_TWELVE_DATA_RESPONSE
        mock_session_get.return_value = mock_resp

        # Make the actual API request
        response = client.get(
            "/api/v1/market-data/bars/US:AAPL",
            params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
        )

        # --- Status ---
        assert response.status_code == 200, f"Unexpected status: {response.text}"
        data = response.json()

        # --- Endpoint schema ---
        required_keys = {
            "symbol", "provider", "feed_mode",
            "start_date", "end_date", "bar_count",
            "data_quality", "bars",
            "disclaimer", "disclaimer_zh",
        }
        assert required_keys.issubset(data.keys()), (
            f"Missing keys: {required_keys - data.keys()}"
        )

        # --- Provenance: provider identity ---
        assert data["symbol"] == "US:AAPL"
        assert data["provider"] == "Twelve Data"
        assert data["feed_mode"] == "external_unverified"
        assert data["start_date"] == "2024-01-01"
        assert data["end_date"] == "2024-01-31"
        assert data["bar_count"] == 2

        # --- Bars ---
        bars = data["bars"]
        assert len(bars) == 2
        # Bars are sorted chronologically
        assert bars[0]["date"] == "2024-01-02"
        assert bars[1]["date"] == "2024-01-03"
        for bar in bars:
            assert all(k in bar for k in ("date", "open", "high", "low", "close", "volume"))

        # --- Data quality / provenance ---
        dq = data["data_quality"]
        assert "data_nature" in dq
        assert "adjustment" in dq
        assert "timezone" in dq
        assert dq["timezone"] == "America/New_York"

        # --- Adjustment caveat ---
        assert "note" in dq
        assert dq["note"] is not None
        assert "Adjustment" in dq["note"] or "adjustment" in dq["note"]
        # The note must clearly signal unverified semantics
        assert "unverified" in dq["note"].lower() or "unknown" in dq.get("adjustment", "").lower()

        # --- Exactly one mocked upstream request ---
        assert mock_session_get.call_count == 1, (
            f"Expected exactly 1 upstream HTTP call, got {mock_session_get.call_count}"
        )

        # --- Upstream URL correctness (no key in URL assertion) ---
        call_args = mock_session_get.call_args
        upstream_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "twelvedata.com" in upstream_url
        # The key must not appear in the logged URL
        assert "fake-key-for-integration-test" not in upstream_url

    @patch("requests.Session.get")
    def test_endpoint_disclaimer_present(
        self, mock_session_get, client, monkeypatch, reset_registry_state
    ):
        """Response includes both English and Chinese disclaimers."""
        monkeypatch.setenv("DATA_PROVIDER", "twelve_data")
        monkeypatch.setenv("TWELVE_DATA_API_KEY", "fake-key-disclaimer-test")
        get_settings.cache_clear()

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_TWELVE_DATA_RESPONSE
        mock_session_get.return_value = mock_resp

        response = client.get(
            "/api/v1/market-data/bars/US:AAPL",
            params={"start_date": "2024-01-01", "end_date": "2024-01-31"},
        )
        assert response.status_code == 200
        data = response.json()

        assert "disclaimer" in data
        assert "disclaimer_zh" in data
        assert len(data["disclaimer"]) > 0
        assert len(data["disclaimer_zh"]) > 0
