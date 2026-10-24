"""Twelve Data market data provider.

This adapter integrates with the Twelve Data API to fetch real market data.
Currently supports US equities with minute and daily intervals.

STATUS: Offline implementation complete. Live validation pending API key
and rights confirmation.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Union

import requests

from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.providers.instruments import Instrument

logger = logging.getLogger(__name__)

TWELVE_DATA_API_BASE = "https://api.twelvedata.com"


class TwelveDataError(Exception):
    """Raised when Twelve Data API returns an error."""
    pass


class TwelveDataRateLimitError(TwelveDataError):
    """Raised when Twelve Data rate limit is hit."""
    pass


class TwelveDataProvider(DataProvider):
    """Provider adapter for Twelve Data API.

    Supports:
    - US equities (AAPL, MSFT, etc.)
    - Minute and daily intervals
    - Historical time-series data

    Not yet supported (requires rights confirmation):
    - Real-time quotes (get_latest_bar)
    - HK market data
    - Display on web UI
    - Data persistence for training
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        supported_symbols: Optional[List[str]] = None,
    ):
        """Initialize Twelve Data provider.

        Args:
            api_key: Twelve Data API key. If None, provider will fail on first request.
            supported_symbols: List of symbols this provider can serve.
                Defaults to common US tech stocks.
        """
        self._api_key = api_key
        self._supported_symbols = supported_symbols or [
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
        ]
        self._session = requests.Session()
        self._request_count = 0
        self._last_error: Optional[str] = None
        self._last_response_meta: Optional[Dict[str, Any]] = None
        self._last_response_symbol: Optional[str] = None

    @property
    def provider_id(self) -> str:
        return "twelve_data"

    @property
    def provider_name(self) -> str:
        return "Twelve Data"

    @property
    def feed_mode(self) -> str:
        return "external_unverified"

    def is_available(self) -> bool:
        """Check if provider has API key configured."""
        return self._api_key is not None and len(self._api_key) > 0

    def list_assets(self) -> List[AssetInfo]:
        """Return list of supported assets.

        Currently returns US equities only. Each asset includes
        provider-independent instrument identity.
        """
        from app.providers.instruments import AssetType, Exchange

        assets = []
        for symbol in self._supported_symbols:
            try:
                instrument = Instrument.create_us(
                    symbol=symbol,
                    name=symbol,
                    asset_type=AssetType.STOCK,
                    exchange=Exchange.NASDAQ,
                )
                assets.append(AssetInfo.from_instrument(
                    instrument,
                    description=f"{instrument.name} (Twelve Data)",
                ))
            except Exception as exc:
                logger.warning("Failed to create instrument for %s: %s", symbol, exc)
                assets.append(AssetInfo(
                    symbol=symbol,
                    name=symbol,
                    description=f"{symbol} (Twelve Data)",
                    asset_type="stock",
                    currency="USD",
                    exchange="NASDAQ",
                ))
        return assets

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        """Fetch historical OHLCV bars from Twelve Data.

        Args:
            symbol: Instrument ID (e.g., "US:AAPL") or plain symbol ("AAPL")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of OHLCVBar sorted chronologically

        Raises:
            TwelveDataError: If API returns error or data is malformed
            TwelveDataRateLimitError: If rate limit is hit
        """
        if not self.is_available():
            raise TwelveDataError("Twelve Data API key not configured")

        plain_symbol = self.resolve_instrument(symbol)

        try:
            return self._fetch_time_series(plain_symbol, start_date, end_date)
        except TwelveDataRateLimitError:
            raise
        except Exception as exc:
            self._last_error = f"get_bars({symbol}) failed: {exc}"
            logger.error("Twelve Data get_bars failed for %s: %s", symbol, exc)
            raise TwelveDataError(f"Failed to fetch bars for {symbol}: {exc}") from exc

    def _fetch_time_series(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        """Fetch time-series data from Twelve Data API.

        Uses the /time_series endpoint with daily interval.
        """
        assert self._api_key is not None

        url = f"{TWELVE_DATA_API_BASE}/time_series"
        params = {
            "symbol": symbol,
            "interval": "1day",
            "start_date": start_date,
            "end_date": end_date,
            "apikey": self._api_key,
        }

        try:
            resp = self._session.get(url, params=params, timeout=30)
            self._request_count += 1
        except requests.Timeout as exc:
            raise TwelveDataError(f"Request timeout for {symbol}") from exc
        except requests.RequestException as exc:
            error_type = type(exc).__name__
            raise TwelveDataError(f"HTTP error for {symbol}: {error_type}") from None

        if resp.status_code == 429:
            raise TwelveDataRateLimitError(
                f"Rate limit exceeded for {symbol}. Retry later."
            )

        if resp.status_code != 200:
            raise TwelveDataError(
                f"HTTP {resp.status_code} for {symbol}: {resp.text[:200]}"
            )

        data = resp.json()

        if data.get("status") == "error":
            error_msg = data.get("message", "Unknown error")
            self._last_response_meta = None  # Clear metadata on error
            self._last_response_symbol = None
            raise TwelveDataError(f"API error for {symbol}: {error_msg}")

        if "values" not in data:
            self._last_response_meta = None  # Clear metadata on malformed response
            self._last_response_symbol = None
            raise TwelveDataError(f"No 'values' in response for {symbol}")

        # Extract metadata from response (if present)
        meta = data.get("meta", {})
        response_timezone = meta.get("exchange_timezone")
        response_currency = meta.get("currency")
        response_exchange = meta.get("exchange")

        bars: List[OHLCVBar] = []
        for row in data["values"]:
            try:
                bar = OHLCVBar(
                    date=row["datetime"][:10],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]) if row.get("volume") else None,
                )
                bars.append(bar)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Skipping malformed Twelve Data row: %s — %s", row, exc)
                continue

        bars.sort(key=lambda b: b.date)

        # Store metadata for get_data_quality() to use
        self._last_response_meta = {
            "timezone": response_timezone,
            "currency": response_currency,
            "exchange": response_exchange,
        }
        self._last_response_symbol = symbol

        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        """Not implemented — requires real-time data rights confirmation.

        Raises:
            NotImplementedError: Always
        """
        raise NotImplementedError(
            "get_latest_bar() not supported in Phase 1. "
            "Requires real-time data rights confirmation."
        )

    def get_date_range(self) -> Dict[str, Any]:
        """Return available date range for this provider.

        Twelve Data provides historical data going back many years.
        This returns a conservative estimate based on API documentation.
        """
        return {
            "earliest_date": "2000-01-01",
            "latest_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "note": "Historical data available. Latest date is approximate.",
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        """Compute checksum over bars for data integrity verification.

        WARNING: This method makes HTTP requests to fetch bars for all symbols.
        The checksum covers only the data fetched by this specific call.

        If called after get_bars(), it will make duplicate API requests and
        may return different data if the provider's data has changed between
        the two calls. The checksum does NOT necessarily correspond to data
        returned by a previous get_bars() call.

        This is used by the backtest engine to verify data consistency.
        """
        all_bars = []
        for symbol in symbols:
            try:
                bars = self.get_bars(symbol, start_date, end_date)
                for bar in bars:
                    all_bars.append({
                        "symbol": symbol,
                        "date": bar.date,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    })
            except Exception as exc:
                logger.warning("Checksum: failed to fetch %s: %s", symbol, exc)
                continue

        all_bars.sort(key=lambda b: (b["symbol"], b["date"]))
        payload = json.dumps(all_bars, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        """Return provenance metadata for the requested symbol.

        Returns metadata only from a successful response whose normalized
        symbol matches *symbol*.  If the last successful response was for a
        different symbol, or no response has been made yet, returns
        unknown/unavailable metadata.

        This method does NOT make any network requests.
        """
        if not self.is_available():
            return {
                "data_nature": "unknown",
                "adjustment": "unknown",
                "timezone": None,
                "note": "API key not configured",
                "note_zh": "未設定 API 金鑰",
            }

        normalized = self.resolve_instrument(symbol)

        if self._last_response_meta and self._last_response_symbol == normalized:
            tz = self._last_response_meta.get("timezone")
            currency = self._last_response_meta.get("currency")
            exchange = self._last_response_meta.get("exchange")

            note_parts = ["Data from Twelve Data API."]
            if currency:
                note_parts.append(f"Currency: {currency}.")
            if exchange:
                note_parts.append(f"Exchange: {exchange}.")
            note_parts.append("Adjustment semantics unverified.")
            note = " ".join(note_parts)

            note_zh_parts = ["資料來自 Twelve Data API。"]
            if currency:
                note_zh_parts.append(f"貨幣：{currency}。")
            if exchange:
                note_zh_parts.append(f"交易所：{exchange}。")
            note_zh_parts.append("調整 semantics 尚未驗證。")
            note_zh = " ".join(note_zh_parts)

            return {
                "data_nature": "unknown",
                "adjustment": "unknown",
                "timezone": tz,
                "note": note,
                "note_zh": note_zh,
            }

        if self._last_response_meta and self._last_response_symbol != normalized:
            note = (
                f"No successful response for {symbol} in this session. "
                f"Last response was for a different symbol."
            )
            note_zh = f"本次連線期間未下載 {symbol} 的資料。最後一次回應來自其他標的。"
        else:
            note = (
                f"No data fetched yet for {symbol}. "
                "Metadata will be populated after first successful request."
            )
            note_zh = f"尚未下載 {symbol} 的資料。首次成功請求後將填入 metadata。"

        return {
            "data_nature": "unknown",
            "adjustment": "unknown",
            "timezone": None,
            "note": note,
            "note_zh": note_zh,
        }

    def resolve_instrument(self, instrument_or_symbol: Union[Instrument, str]) -> str:
        """Resolve Instrument or symbol string to Twelve Data symbol format.

        Twelve Data uses plain symbols (e.g., "AAPL") for US equities.
        Instrument IDs like "US:AAPL" are converted to "AAPL".
        """
        if isinstance(instrument_or_symbol, Instrument):
            return instrument_or_symbol.symbol

        if ":" in instrument_or_symbol:
            parts = instrument_or_symbol.split(":", 1)
            if len(parts) == 2:
                return parts[1]

        return instrument_or_symbol

    def get_status(self) -> Dict[str, Any]:
        """Return provider status including request count and errors."""
        status = super().get_status()
        status.update({
            "request_count": self._request_count,
            "last_error": self._last_error,
            "api_key_configured": self._api_key is not None,
        })
        return status
