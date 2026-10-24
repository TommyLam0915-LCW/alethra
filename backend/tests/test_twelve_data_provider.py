"""Tests for Twelve Data provider adapter.

All HTTP requests are mocked. No real API calls are made.
Test fixtures use synthetic data matching the documented schema.
"""

import json
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from app.providers.twelve_data_provider import (
    TwelveDataProvider,
    TwelveDataError,
    TwelveDataRateLimitError,
)
from app.providers.data_provider import OHLCVBar


@pytest.fixture(autouse=True)
def block_unmocked_network():
    """Fail-closed guard: block any unmocked network requests.

    This fixture ensures that no test can make real HTTP calls to
    Twelve Data API or any other external service.
    """
    import requests

    original_get = requests.Session.get
    original_post = requests.Session.post
    original_send = requests.Session.send

    def blocked_get(self, *args, **kwargs):
        raise RuntimeError(
            f"UNMOCKED NETWORK CALL BLOCKED: requests.Session.get({args}, {kwargs}). "
            "All HTTP requests must be mocked in tests."
        )

    def blocked_post(self, *args, **kwargs):
        raise RuntimeError(
            f"UNMOCKED NETWORK CALL BLOCKED: requests.Session.post({args}, {kwargs}). "
            "All HTTP requests must be mocked in tests."
        )

    def blocked_send(self, *args, **kwargs):
        raise AssertionError("Unmocked HTTP request blocked")

    requests.Session.get = blocked_get
    requests.Session.post = blocked_post
    requests.Session.send = blocked_send

    yield

    requests.Session.get = original_get
    requests.Session.post = original_post
    requests.Session.send = original_send


@pytest.fixture
def mock_response_success():
    """Mock successful Twelve Data API response with synthetic data."""
    return {
        "status_code": 200,
        "json": {
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
        },
    }


@pytest.fixture
def mock_response_error():
    """Mock Twelve Data API error response."""
    return {
        "status_code": 200,
        "json": {
            "status": "error",
            "message": "Invalid API key",
        },
    }


@pytest.fixture
def mock_response_rate_limit():
    """Mock Twelve Data rate limit response."""
    return {
        "status_code": 429,
        "json": {
            "status": "error",
            "message": "Rate limit exceeded",
        },
    }


@pytest.fixture
def mock_response_malformed():
    """Mock response with malformed data."""
    return {
        "status_code": 200,
        "json": {
            "meta": {"symbol": "AAPL"},
            "values": [
                {
                    "datetime": "2024-01-03",
                    "open": "invalid",
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
        },
    }


class TestTwelveDataProviderInit:
    """Test provider initialization."""

    def test_init_without_api_key(self):
        """Provider initializes without API key but is unavailable."""
        provider = TwelveDataProvider()
        assert provider.provider_id == "twelve_data"
        assert provider.provider_name == "Twelve Data"
        assert provider.feed_mode == "external_unverified"
        assert not provider.is_available()

    def test_init_with_api_key(self):
        """Provider initializes with API key and is available."""
        provider = TwelveDataProvider(api_key="test_key")
        assert provider.is_available()
        assert provider._api_key == "test_key"

    def test_init_with_custom_symbols(self):
        """Provider accepts custom symbol list."""
        symbols = ["AAPL", "MSFT"]
        provider = TwelveDataProvider(api_key="test_key", supported_symbols=symbols)
        assets = provider.list_assets()
        assert len(assets) == 2
        assert all(a.symbol in symbols for a in assets)


class TestTwelveDataProviderListAssets:
    """Test list_assets() method."""

    def test_list_assets_returns_asset_info(self):
        """list_assets() returns AssetInfo objects with instrument identity."""
        provider = TwelveDataProvider(api_key="test_key")
        assets = provider.list_assets()

        assert len(assets) > 0
        for asset in assets:
            assert asset.symbol
            assert asset.name
            assert asset.asset_type in ["stock", "etf"]
            assert asset.currency == "USD"

    def test_list_assets_includes_instrument_id(self):
        """Assets include provider-independent instrument ID."""
        provider = TwelveDataProvider(api_key="test_key")
        assets = provider.list_assets()

        for asset in assets:
            if asset.instrument:
                assert asset.instrument.instrument_id
                assert ":" in asset.instrument.instrument_id


class TestTwelveDataProviderGetBars:
    """Test get_bars() method with mocked HTTP."""

    @patch("requests.Session.get")
    def test_get_bars_success(self, mock_get, mock_response_success):
        """get_bars() fetches and parses bars successfully."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        bars = provider.get_bars("US:AAPL", "2024-01-01", "2024-01-31")

        assert len(bars) == 2
        assert all(isinstance(b, OHLCVBar) for b in bars)
        assert bars[0].date == "2024-01-02"
        assert bars[1].date == "2024-01-03"
        assert bars[0].open == 183.50
        assert bars[1].close == 185.90

    @patch("requests.Session.get")
    def test_get_bars_chronological_order(self, mock_get, mock_response_success):
        """get_bars() returns bars sorted chronologically."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        bars = provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        dates = [b.date for b in bars]
        assert dates == sorted(dates)

    @patch("requests.Session.get")
    def test_get_bars_no_api_key_raises(self, mock_get):
        """get_bars() raises TwelveDataError when API key not configured."""
        provider = TwelveDataProvider()

        with pytest.raises(TwelveDataError, match="API key not configured"):
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

    @patch("requests.Session.get")
    def test_get_bars_api_error_raises(self, mock_get, mock_response_error):
        """get_bars() raises TwelveDataError on API error response."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_error["status_code"]
        mock_resp.json.return_value = mock_response_error["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(TwelveDataError, match="Invalid API key"):
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

    @patch("requests.Session.get")
    def test_get_bars_rate_limit_raises(self, mock_get, mock_response_rate_limit):
        """get_bars() raises TwelveDataRateLimitError on 429 response."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_rate_limit["status_code"]
        mock_resp.json.return_value = mock_response_rate_limit["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(TwelveDataRateLimitError, match="Rate limit"):
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

    @patch("requests.Session.get")
    def test_get_bars_malformed_data_skipped(self, mock_get, mock_response_malformed):
        """get_bars() skips malformed rows and continues."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_malformed["status_code"]
        mock_resp.json.return_value = mock_response_malformed["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        bars = provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        assert len(bars) == 1
        assert bars[0].date == "2024-01-02"

    @patch("requests.Session.get")
    def test_get_bars_resolves_instrument_id(self, mock_get, mock_response_success):
        """get_bars() resolves US:AAPL to AAPL for API call."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        bars = provider.get_bars("US:AAPL", "2024-01-01", "2024-01-31")

        assert len(bars) == 2
        call_args = mock_get.call_args
        assert call_args[1]["params"]["symbol"] == "AAPL"


class TestTwelveDataProviderGetLatestBar:
    """Test get_latest_bar() method."""

    def test_get_latest_bar_raises_not_implemented(self):
        """get_latest_bar() raises NotImplementedError in Phase 1."""
        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(NotImplementedError, match="not supported in Phase 1"):
            provider.get_latest_bar("AAPL")


class TestTwelveDataProviderGetDateRange:
    """Test get_date_range() method."""

    def test_get_date_range_returns_dict(self):
        """get_date_range() returns dict with earliest and latest dates."""
        provider = TwelveDataProvider(api_key="test_key")
        date_range = provider.get_date_range()

        assert "earliest_date" in date_range
        assert "latest_date" in date_range
        assert date_range["earliest_date"] == "2000-01-01"

    def test_get_date_range_latest_is_today(self):
        """get_date_range() returns today as latest date."""
        provider = TwelveDataProvider(api_key="test_key")
        date_range = provider.get_date_range()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert date_range["latest_date"] == today


class TestTwelveDataProviderComputeChecksum:
    """Test compute_checksum() method."""

    @patch("requests.Session.get")
    def test_compute_checksum_returns_hash(self, mock_get, mock_response_success):
        """compute_checksum() returns SHA-256 hash string."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        checksum = provider.compute_checksum(["AAPL"], "2024-01-01", "2024-01-31")

        assert isinstance(checksum, str)
        assert len(checksum) == 64

    @patch("requests.Session.get")
    def test_compute_checksum_deterministic(self, mock_get, mock_response_success):
        """compute_checksum() returns same hash for same data."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        checksum1 = provider.compute_checksum(["AAPL"], "2024-01-01", "2024-01-31")
        checksum2 = provider.compute_checksum(["AAPL"], "2024-01-01", "2024-01-31")

        assert checksum1 == checksum2

    @patch("requests.Session.get")
    def test_compute_checksum_makes_requests(self, mock_get, mock_response_success):
        """compute_checksum() makes one HTTP request per symbol."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        provider.compute_checksum(["AAPL", "MSFT", "GOOGL"], "2024-01-01", "2024-01-31")

        assert mock_get.call_count == 3


class TestTwelveDataProviderGetDataQuality:
    """Test get_data_quality() method."""

    def test_get_data_quality_without_api_key(self):
        """get_data_quality() returns unknown when API key not configured."""
        provider = TwelveDataProvider()
        quality = provider.get_data_quality("AAPL")

        assert quality["data_nature"] == "unknown"
        assert quality["adjustment"] == "unknown"
        assert quality["timezone"] is None

    def test_get_data_quality_with_api_key_no_response(self):
        """get_data_quality() returns unknown when API key configured but no response yet."""
        provider = TwelveDataProvider(api_key="test_key")
        quality = provider.get_data_quality("AAPL")

        assert quality["data_nature"] == "unknown"
        assert quality["timezone"] is None
        assert "No data fetched" in quality["note"]

    @patch("requests.Session.get")
    def test_get_data_quality_after_response(self, mock_get, mock_response_success):
        """get_data_quality() uses parsed metadata from response."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        quality = provider.get_data_quality("AAPL")

        assert quality["data_nature"] == "unknown"
        assert quality["timezone"] == "America/New_York"
        assert "Currency: USD" in quality["note"]
        assert "Exchange: NASDAQ" in quality["note"]

    @patch("requests.Session.get")
    def test_get_data_quality_returns_last_response_metadata(self, mock_get):
        """get_data_quality() returns metadata only for the last-fetched symbol."""
        # First response for AAPL
        mock_resp_aapl = Mock()
        mock_resp_aapl.status_code = 200
        mock_resp_aapl.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.25", "high": "186.50", "low": "184.80", "close": "185.90", "volume": "50000000"}],
            "status": "ok",
        }

        # Second response for MSFT
        mock_resp_msft = Mock()
        mock_resp_msft.status_code = 200
        mock_resp_msft.json.return_value = {
            "meta": {"symbol": "MSFT", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "370.00", "high": "375.00", "low": "368.00", "close": "373.00", "volume": "30000000"}],
            "status": "ok",
        }

        mock_get.side_effect = [mock_resp_aapl, mock_resp_msft]

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")
        provider.get_bars("MSFT", "2024-01-01", "2024-01-31")

        # Query for AAPL — should NOT return MSFT metadata
        quality_aapl = provider.get_data_quality("AAPL")
        assert quality_aapl["timezone"] is None
        assert "different symbol" in quality_aapl["note"]

        # Query for MSFT — should return MSFT metadata
        quality_msft = provider.get_data_quality("MSFT")
        assert quality_msft["timezone"] == "America/New_York"
        assert "Currency: USD" in quality_msft["note"]

    @patch("requests.Session.get")
    def test_get_data_quality_after_failure_clears_metadata(self, mock_get):
        """get_data_quality() returns conservative values after failed request."""
        # First request succeeds
        mock_resp_success = Mock()
        mock_resp_success.status_code = 200
        mock_resp_success.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.25", "high": "186.50", "low": "184.80", "close": "185.90", "volume": "50000000"}],
            "status": "ok",
        }

        # Second request fails
        mock_resp_failure = Mock()
        mock_resp_failure.status_code = 200
        mock_resp_failure.json.return_value = {"status": "error", "message": "Invalid symbol"}

        mock_get.side_effect = [mock_resp_success, mock_resp_failure]

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        # Metadata is populated
        quality_before = provider.get_data_quality("AAPL")
        assert quality_before["timezone"] == "America/New_York"

        # Second request fails
        with pytest.raises(TwelveDataError):
            provider.get_bars("INVALID", "2024-01-01", "2024-01-31")

        # Metadata should be cleared or marked as stale
        quality_after = provider.get_data_quality("INVALID")
        # After failure, _last_response_meta should be None or cleared
        # The implementation clears it on failure, so we get conservative values
        assert quality_after["timezone"] is None

    @patch("requests.Session.get")
    def test_A_fetch_B_metadata_not_returned_for_A(self, mock_get):
        """A: Fetch A then B with different metadata — quality(A) must not return B's metadata."""
        mock_resp_aapl = Mock()
        mock_resp_aapl.status_code = 200
        mock_resp_aapl.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.00", "high": "186.00", "low": "184.00", "close": "185.50", "volume": "50000000"}],
            "status": "ok",
        }

        mock_resp_msft = Mock()
        mock_resp_msft.status_code = 200
        mock_resp_msft.json.return_value = {
            "meta": {"symbol": "MSFT", "exchange_timezone": "America/Chicago", "currency": "USD", "exchange": "NYSE"},
            "values": [{"datetime": "2024-01-03", "open": "370.00", "high": "375.00", "low": "368.00", "close": "373.00", "volume": "30000000"}],
            "status": "ok",
        }

        mock_get.side_effect = [mock_resp_aapl, mock_resp_msft]

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")
        provider.get_bars("MSFT", "2024-01-01", "2024-01-31")

        quality_a = provider.get_data_quality("AAPL")
        assert quality_a["timezone"] is None
        assert "different symbol" in quality_a["note"]

    @patch("requests.Session.get")
    def test_B_fetch_A_success_then_B_fail_quality_B_not_A(self, mock_get):
        """B: Fetch A successfully, then fail fetching B — quality(B) must not return A's metadata."""
        mock_resp_success = Mock()
        mock_resp_success.status_code = 200
        mock_resp_success.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.00", "high": "186.00", "low": "184.00", "close": "185.50", "volume": "50000000"}],
            "status": "ok",
        }

        mock_resp_failure = Mock()
        mock_resp_failure.status_code = 200
        mock_resp_failure.json.return_value = {"status": "error", "message": "Invalid symbol"}

        mock_get.side_effect = [mock_resp_success, mock_resp_failure]

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        with pytest.raises(TwelveDataError):
            provider.get_bars("INVALID", "2024-01-01", "2024-01-31")

        quality_b = provider.get_data_quality("INVALID")
        assert quality_b["timezone"] is None

        quality_a = provider.get_data_quality("AAPL")
        assert quality_a["timezone"] is None

    @patch("requests.Session.get")
    def test_C_fetch_A_success_then_A_fail_old_not_current(self, mock_get):
        """C: Fetch A successfully, then fail fetching A — quality(A) must not present old response as current."""
        mock_resp_success = Mock()
        mock_resp_success.status_code = 200
        mock_resp_success.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.00", "high": "186.00", "low": "184.00", "close": "185.50", "volume": "50000000"}],
            "status": "ok",
        }

        mock_resp_failure = Mock()
        mock_resp_failure.status_code = 200
        mock_resp_failure.json.return_value = {"status": "error", "message": "Rate limit exceeded"}

        mock_get.side_effect = [mock_resp_success, mock_resp_failure]

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        quality_before = provider.get_data_quality("AAPL")
        assert quality_before["timezone"] == "America/New_York"

        with pytest.raises(TwelveDataError):
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        quality_after = provider.get_data_quality("AAPL")
        assert quality_after["timezone"] is None
        assert "No data fetched" in quality_after["note"] or "No successful response" in quality_after["note"]

    @patch("requests.Session.get")
    def test_D_instrument_id_normalization_match(self, mock_get):
        """D: US:AAPL and AAPL match through existing normalization."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "meta": {"symbol": "AAPL", "exchange_timezone": "America/New_York", "currency": "USD", "exchange": "NASDAQ"},
            "values": [{"datetime": "2024-01-03", "open": "185.00", "high": "186.00", "low": "184.00", "close": "185.50", "volume": "50000000"}],
            "status": "ok",
        }
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("US:AAPL", "2024-01-01", "2024-01-31")

        quality_plain = provider.get_data_quality("AAPL")
        assert quality_plain["timezone"] == "America/New_York"

        quality_id = provider.get_data_quality("US:AAPL")
        assert quality_id["timezone"] == "America/New_York"

    def test_E_get_data_quality_zero_http_calls(self):
        """E: get_data_quality() makes zero HTTP calls."""
        provider = TwelveDataProvider(api_key="test_key")

        with patch("requests.Session.get") as mock_get:
            provider.get_data_quality("AAPL")
            provider.get_data_quality("MSFT")
            provider.get_data_quality("US:GOOGL")
            assert mock_get.call_count == 0


class TestTwelveDataProviderResolveInstrument:
    """Test resolve_instrument() method."""

    def test_resolve_plain_symbol(self):
        """resolve_instrument() returns plain symbol as-is."""
        provider = TwelveDataProvider(api_key="test_key")
        result = provider.resolve_instrument("AAPL")
        assert result == "AAPL"

    def test_resolve_instrument_id(self):
        """resolve_instrument() extracts symbol from instrument ID."""
        provider = TwelveDataProvider(api_key="test_key")
        result = provider.resolve_instrument("US:AAPL")
        assert result == "AAPL"

    def test_resolve_instrument_object(self):
        """resolve_instrument() extracts symbol from Instrument object."""
        from app.providers.instruments import Instrument, Market, Exchange, Currency, AssetType

        provider = TwelveDataProvider(api_key="test_key")
        instrument = Instrument(
            market=Market.US,
            symbol="AAPL",
            name="Apple Inc.",
            asset_type=AssetType.STOCK,
            exchange=Exchange.NASDAQ,
            currency=Currency.USD,
            timezone="America/New_York",
        )
        result = provider.resolve_instrument(instrument)
        assert result == "AAPL"


class TestTwelveDataProviderGetStatus:
    """Test get_status() method."""

    def test_get_status_includes_request_count(self):
        """get_status() includes request count."""
        provider = TwelveDataProvider(api_key="test_key")
        status = provider.get_status()

        assert "request_count" in status
        assert status["request_count"] == 0

    @patch("requests.Session.get")
    def test_get_status_increments_request_count(self, mock_get, mock_response_success):
        """get_status() request count increments after API calls."""
        mock_resp = Mock()
        mock_resp.status_code = mock_response_success["status_code"]
        mock_resp.json.return_value = mock_response_success["json"]
        mock_get.return_value = mock_resp

        provider = TwelveDataProvider(api_key="test_key")
        provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        status = provider.get_status()
        assert status["request_count"] == 1

    def test_get_status_includes_api_key_configured(self):
        """get_status() indicates whether API key is configured."""
        provider = TwelveDataProvider(api_key="test_key")
        status = provider.get_status()

        assert status["api_key_configured"] is True


class TestTwelveDataProviderNetworkSafety:
    """Test network safety guards."""

    def test_session_send_is_blocked(self):
        """Session.send is blocked by the fail-closed guard with correct signature."""
        import inspect
        import requests

        # --- Guard-signature assertion ---
        sig = inspect.signature(requests.Session.send)
        param_kinds = [
            (name, parameter.kind.name)
            for name, parameter in sig.parameters.items()
        ]
        assert param_kinds == [
            ("self", "POSITIONAL_OR_KEYWORD"),
            ("args", "VAR_POSITIONAL"),
            ("kwargs", "VAR_KEYWORD"),
        ]

        # --- Direct send() blocked ---
        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(AssertionError, match="Unmocked HTTP request blocked"):
            provider._session.send(Mock())

        # --- Session().request() blocked (no real network traffic) ---
        with pytest.raises(AssertionError, match="Unmocked HTTP request blocked"):
            requests.Session().request(
                "GET",
                "http://127.0.0.1:9/unreachable",
                timeout=2,
            )

    @patch("requests.Session.get")
    def test_api_key_redacted_in_exception(self, mock_get):
        """API key is redacted from exception messages."""
        import requests

        mock_get.side_effect = requests.ConnectionError("Failed to connect to api.twelvedata.com?apikey=SECRET_KEY_12345")

        provider = TwelveDataProvider(api_key="SECRET_KEY_12345")

        with pytest.raises(TwelveDataError) as exc_info:
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")

        error_message = str(exc_info.value)
        assert "SECRET_KEY_12345" not in error_message
        assert "ConnectionError" in error_message

    @patch("requests.Session.get")
    def test_api_key_not_in_full_traceback(self, mock_get):
        """API key is absent from full traceback output, not just str(error)."""
        import requests
        import traceback

        fake_key = "TRACEBACK_TEST_KEY_98765"
        mock_get.side_effect = requests.ConnectionError(f"Failed: https://api.twelvedata.com/time_series?apikey={fake_key}")

        provider = TwelveDataProvider(api_key=fake_key)

        try:
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")
            assert False, "Should have raised TwelveDataError"
        except TwelveDataError:
            import sys
            tb_lines = traceback.format_exception(*sys.exc_info())
            full_traceback = "".join(tb_lines)
            assert fake_key not in full_traceback, f"API key found in traceback:\n{full_traceback}"

    @patch("requests.Session.get")
    def test_timeout_handling(self, mock_get):
        """Timeout exceptions are caught and converted to TwelveDataError."""
        import requests

        mock_get.side_effect = requests.Timeout("Request timed out")

        provider = TwelveDataProvider(api_key="test_key")

        with pytest.raises(TwelveDataError, match="Request timeout"):
            provider.get_bars("AAPL", "2024-01-01", "2024-01-31")
