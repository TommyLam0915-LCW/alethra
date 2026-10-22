"""Twelve Data market data provider adapter.

Implements the DataProvider interface for Twelve Data's REST API.
Requires a Twelve Data API key set in TWELVEDATA_API_KEY env var.

API reference: https://twelvedata.com/docs#time-series
Symbol format: bare ticker for US (AAPL), ticker/exchange for HK (0700/HKG)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.providers.instruments import Instrument, Market, parse_instrument_id
from app.data.real_instruments import (
    ALL_REAL_INSTRUMENTS,
    get_instrument_by_id,
    get_provider_mapping,
)
from app.lib.timeutil import utcnow

logger = logging.getLogger(__name__)

TWELVEDATA_API_BASE = "https://api.twelvedata.com"
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0

_TWELVEDATA_EXCHANGE_MAP = {
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "ARCA": "ARCA",
    "BATS": "BATS",
    "HKEX": "HKG",
}


class TwelveDataConfigurationError(Exception):
    """Raised when Twelve Data credentials are missing/invalid."""


class TwelveDataError(Exception):
    """Raised when Twelve Data API returns an error."""


class TwelveDataRateLimitError(TwelveDataError):
    """Raised on HTTP 429 or while a rate-limit window is active."""


class TwelveDataProvider(DataProvider):
    """Twelve Data market data provider.

    Requires TWELVEDATA_API_KEY in environment.
    Free tier: 8 requests/minute, 800/day.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self._client: Optional[httpx.Client] = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._rate_limited_until: float = 0.0
        self._consecutive_errors: int = 0
        self._last_success_at: Optional[str] = None
        self._last_error_at: Optional[str] = None
        self._request_count: int = 0

    @property
    def provider_id(self) -> str:
        return "twelvedata"

    @property
    def provider_name(self) -> str:
        return "Twelve Data"

    @property
    def feed_mode(self) -> str:
        return "live" if self._connected else "disconnected"

    def _resolve_api_key(self) -> str:
        import os
        key = self._api_key or os.environ.get("TWELVEDATA_API_KEY", "")
        if not key:
            raise TwelveDataConfigurationError(
                "Twelve Data API key not configured. Set TWELVEDATA_API_KEY."
            )
        return key

    def connect(self) -> bool:
        try:
            api_key = self._resolve_api_key()
        except TwelveDataConfigurationError as e:
            self._last_error = str(e)
            self._connected = False
            return False

        try:
            self._client = httpx.Client(
                base_url=TWELVEDATA_API_BASE,
                timeout=30.0,
            )
            self._connected = True
            self._last_error = None
            logger.info("Twelve Data provider connected")
            return True
        except Exception as e:
            self._last_error = f"Connection failed: {e}"
            self._connected = False
            logger.warning("Twelve Data connection failed: %s", e)
            return False

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        self._rate_limited_until = 0.0
        self._consecutive_errors = 0

    def is_available(self) -> bool:
        return self._connected and self._client is not None

    def _check_rate_limit(self) -> None:
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            raise TwelveDataRateLimitError(
                f"Twelve Data rate limit active; retry after {int(remaining)}s"
            )

    def _record_success(self) -> None:
        self._consecutive_errors = 0
        self._last_success_at = utcnow().isoformat()
        self._request_count += 1

    def _record_error(self, message: str) -> None:
        self._consecutive_errors += 1
        self._last_error = message
        self._last_error_at = utcnow().isoformat()

    def _to_twelvedata_symbol(self, instrument_id: str) -> str:
        """Convert internal instrument ID to Twelve Data symbol format.

        US:AAPL -> AAPL
        HK:0700 -> 0700/HKG
        """
        mapping = get_provider_mapping("twelvedata", instrument_id)
        if mapping:
            return mapping.provider_symbol

        try:
            market, symbol = parse_instrument_id(instrument_id)
        except ValueError:
            return instrument_id

        if market == Market.HK:
            return f"{symbol}/HKG"
        return symbol

    def _request_with_retry(
        self,
        endpoint: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        assert self._client is not None
        self._check_rate_limit()

        api_key = self._resolve_api_key()
        params["apikey"] = api_key

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._client.get(endpoint, params=params)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    self._rate_limited_until = time.monotonic() + min(retry_after, 300)
                    raise TwelveDataRateLimitError(
                        f"Twelve Data rate limited (HTTP 429); retry after {retry_after}s"
                    )

                if resp.status_code >= 500:
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    if attempt < _MAX_RETRIES:
                        time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    raise TwelveDataError(
                        f"Twelve Data server error (HTTP {resp.status_code}) after retries"
                    )

                data = resp.json()

                if data.get("status") == "error":
                    code = data.get("code", 0)
                    msg = data.get("message", "Unknown API error")
                    if code == 429:
                        self._rate_limited_until = time.monotonic() + 60
                        raise TwelveDataRateLimitError(f"Twelve Data rate limited: {msg}")
                    raise TwelveDataError(f"Twelve Data API error ({code}): {msg}")

                return data

            except (TwelveDataRateLimitError, TwelveDataError):
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue

        raise TwelveDataError(f"Twelve Data request failed after retries: {last_exc}")

    def list_assets(self) -> List[AssetInfo]:
        assets = []
        for inst in ALL_REAL_INSTRUMENTS:
            mapping = get_provider_mapping("twelvedata", inst.instrument_id)
            if mapping:
                assets.append(AssetInfo.from_instrument(
                    inst,
                    description=f"{inst.name} (Twelve Data: {mapping.provider_symbol})",
                ))
        return assets

    def resolve_instrument(self, instrument_or_symbol: Any) -> str:
        if isinstance(instrument_or_symbol, Instrument):
            return self._to_twelvedata_symbol(instrument_or_symbol.instrument_id)
        if ":" in str(instrument_or_symbol):
            return self._to_twelvedata_symbol(str(instrument_or_symbol))
        return str(instrument_or_symbol)

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        if not self.is_available():
            raise TwelveDataError("Twelve Data provider not connected. Call connect() first.")

        self._check_rate_limit()

        td_symbol = self._to_twelvedata_symbol(symbol) if ":" in symbol else symbol

        data = self._request_with_retry(
            "/time_series",
            params={
                "symbol": td_symbol,
                "interval": "1day",
                "start_date": start_date,
                "end_date": end_date,
                "order": "ASC",
                "format": "JSON",
            },
        )

        values = data.get("values", [])
        bars: List[OHLCVBar] = []
        for row in values:
            try:
                bar = OHLCVBar(
                    date=row["datetime"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row.get("volume", 0)) or None,
                )
                bars.append(bar)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Skipping malformed Twelve Data row: %s — %s", row, e)
                continue

        bars.sort(key=lambda b: b.date)
        self._record_success()
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if not self.is_available():
            return None

        self._check_rate_limit()

        td_symbol = self._to_twelvedata_symbol(symbol) if ":" in symbol else symbol

        try:
            data = self._request_with_retry(
                "/time_series",
                params={
                    "symbol": td_symbol,
                    "interval": "1day",
                    "outputsize": 1,
                    "format": "JSON",
                },
            )

            values = data.get("values", [])
            if not values:
                return None

            row = values[0]
            bar = OHLCVBar(
                date=row["datetime"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)) or None,
            )
            self._record_success()
            return bar
        except TwelveDataRateLimitError:
            raise
        except Exception as e:
            self._record_error(f"get_latest_bar({symbol}) failed: {e}")
            logger.warning("Twelve Data get_latest_bar failed for %s: %s", symbol, e)
            return None

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": "2020-01-02",
            "max": utcnow().strftime("%Y-%m-%d"),
            "total_trading_days": None,
            "note": "Twelve Data free tier supports up to 5000 rows per request.",
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        import hashlib
        import json

        serialized = {}
        for sym in sorted(symbols):
            bars = self.get_bars(sym, start_date, end_date)
            serialized[sym] = [
                {"date": b.date, "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close}
                for b in bars
            ]

        raw = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        inst = get_instrument_by_id(symbol) if ":" in str(symbol) else None
        return {
            "data_nature": "live" if self.is_available() else "unknown",
            "adjustment": "unknown",
            "timezone": inst.timezone if inst else None,
            "note": "Twelve Data provides split/dividend-adjusted prices by default; adjustment method is not explicitly documented.",
            "note_zh": "Twelve Data 預設提供經拆股及股息調整之價格；調整方法未有明確文件說明。",
        }

    def get_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": self.is_available(),
            "connected": self._connected,
            "instruments_available": len(self.list_assets()),
            "request_count": self._request_count,
        }

        rate_limited = time.monotonic() < self._rate_limited_until
        if not self._connected:
            health = "disconnected"
        elif rate_limited:
            health = "rate_limited"
        elif self._consecutive_errors >= 3:
            health = "degraded"
        else:
            health = "ok"

        status.update({
            "health": health,
            "rate_limited": rate_limited,
            "consecutive_errors": self._consecutive_errors,
            "last_success_at": self._last_success_at,
            "last_error_at": self._last_error_at,
        })
        if self._last_error:
            status["last_error"] = self._last_error
        return status

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        inst = get_instrument_by_id(instrument_id)
        if inst is None:
            return None
        mapping = get_provider_mapping("twelvedata", instrument_id)
        desc = f"{inst.name} (Twelve Data: {mapping.provider_symbol})" if mapping else inst.name
        return AssetInfo.from_instrument(inst, description=desc)

    def supports_instrument_id(self) -> bool:
        return True
