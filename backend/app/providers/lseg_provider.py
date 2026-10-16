"""LSEG (London Stock Exchange Group) data provider adapter.

Implements the DataProvider interface for LSEG's market data API.
Requires LSEG API credentials (client_id, client_secret, app_key).

Status: BLOCKED — requires LSEG DataLibrary credentials.
This adapter implements the full contract but will raise on actual
data fetches until credentials are configured. Offline tests verify
the adapter's structure, error handling, and symbol mapping logic.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.providers.instruments import Instrument, Market, parse_instrument_id
from app.data.real_instruments import (
    ALL_REAL_INSTRUMENTS,
    LSEG_MAPPINGS,
    get_instrument_by_id,
    get_provider_mapping,
)
from app.lib.timeutil import utcnow

logger = logging.getLogger(__name__)

LSEG_OAUTH2_TOKEN_URL = "https://api.refinitiv.com/auth/oauth2/v2/token"
LSEG_API_BASE_URL = "https://api.refinitiv.com"


class LSEGConfigurationError(Exception):
    """Raised when LSEG credentials or configuration are missing/invalid."""


class LSEGDataError(Exception):
    """Raised when LSEG API returns an error or unexpected data."""


class LSEGRateLimitError(LSEGDataError):
    """Raised on HTTP 429 or while a previously observed rate-limit window is active."""

_RATE_LIMIT_DEFAULT_SECONDS = 60
_RATE_LIMIT_MAX_SECONDS = 300
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0
_DEGRADED_THRESHOLD = 3


class LSEGDataProvider(DataProvider):
    """LSEG market data provider.

    Requires:
    - LSEG_API_KEY or LSEG_CLIENT_ID + LSEG_CLIENT_SECRET in environment
    - Access to LSEG DataScope Select or Refinitiv Data Library

    Symbol mapping: Internal instrument IDs (US:AAPL) are mapped to
    LSEG RIC format (AAPL.O) via LSEG_MAPPINGS in real_instruments.py.

    Authentication:
    - API key mode: static Bearer token (LSEG_API_KEY)
    - OAuth2 mode: client credentials grant with automatic token refresh
      (LSEG_CLIENT_ID + LSEG_CLIENT_SECRET)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        token_url: str = LSEG_OAUTH2_TOKEN_URL,
    ):
        self._api_key = api_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._session = None
        self._connected = False
        self._last_error: Optional[str] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._rate_limited_until: float = 0.0
        self._rate_limited_retry_after: Optional[int] = None
        self._consecutive_errors: int = 0
        self._last_success_at: Optional[str] = None
        self._last_error_at: Optional[str] = None

    @property
    def provider_id(self) -> str:
        return "lseg"

    @property
    def provider_name(self) -> str:
        return "LSEG (Refinitiv)"

    @property
    def feed_mode(self) -> str:
        return "live" if self._connected else "disconnected"

    def _check_credentials(self) -> None:
        if not self._api_key and not (self._client_id and self._client_secret):
            raise LSEGConfigurationError(
                "LSEG credentials not configured. "
                "Set LSEG_API_KEY or LSEG_CLIENT_ID + LSEG_CLIENT_SECRET."
            )

    def _acquire_oauth2_token(self) -> None:
        import requests

        resp = requests.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()

        self._access_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in", 300))
        self._token_expires_at = time.monotonic() + expires_in - 60

    def _ensure_valid_token(self) -> None:
        if self._api_key:
            return
        if self._access_token and time.monotonic() < self._token_expires_at:
            return
        self._acquire_oauth2_token()

    def connect(self) -> bool:
        try:
            self._check_credentials()
        except LSEGConfigurationError as e:
            self._last_error = str(e)
            self._connected = False
            return False

        try:
            import requests
            self._session = requests.Session()

            if self._api_key:
                self._session.headers.update({
                    "Authorization": f"Bearer {self._api_key}",
                })
            else:
                self._acquire_oauth2_token()
                self._session.headers.update({
                    "Authorization": f"Bearer {self._access_token}",
                })

            self._connected = True
            self._last_error = None
            logger.info("LSEG provider connected (auth=%s)", "api_key" if self._api_key else "oauth2")
            return True
        except Exception as e:
            self._last_error = f"Connection failed: {e}"
            self._connected = False
            logger.warning("LSEG connection failed: %s", e)
            return False

    def disconnect(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
        self._connected = False
        self._access_token = None
        self._token_expires_at = 0.0
        self._rate_limited_until = 0.0
        self._rate_limited_retry_after = None
        self._consecutive_errors = 0

    def is_available(self) -> bool:
        return self._connected and self._session is not None

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _record_success(self) -> None:
        self._consecutive_errors = 0
        self._last_success_at = utcnow().isoformat()

    def _record_error(self, message: str) -> None:
        self._consecutive_errors += 1
        self._last_error = message
        self._last_error_at = utcnow().isoformat()

    def _rate_limit_state(self) -> tuple:
        remaining = self._rate_limited_until - time.monotonic()
        if remaining <= 0:
            return False, 0
        return True, max(1, int(remaining))

    def _parse_retry_after(self, value: Optional[str]) -> int:
        try:
            return min(max(int(float(value)), 1), _RATE_LIMIT_MAX_SECONDS)
        except (TypeError, ValueError):
            return _RATE_LIMIT_DEFAULT_SECONDS

    def _request_with_retry(self, url: str, *, timeout: float, params: Optional[dict] = None):
        """GET with fail-fast during rate-limit windows and bounded retry
        for transient failures (timeout, connection error, 5xx).

        429 responses start a rate-limit window honored by subsequent calls
        without touching the network; they are never retried.
        """
        import requests

        rate_limited, retry_after = self._rate_limit_state()
        if rate_limited:
            raise LSEGRateLimitError(
                f"LSEG rate limit active; retry after {retry_after}s"
            )

        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                if resp.status_code == 429:
                    delay = self._parse_retry_after(resp.headers.get("Retry-After"))
                    self._rate_limited_until = time.monotonic() + delay
                    self._rate_limited_retry_after = delay
                    raise LSEGRateLimitError(
                        f"LSEG rate limited (HTTP 429); retry after {delay}s"
                    )
                if resp.status_code >= 500:
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    if attempt < _MAX_RETRIES:
                        self._sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                        continue
                    raise LSEGDataError(
                        f"LSEG server error (HTTP {resp.status_code}) after retries"
                    )
                return resp
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    self._sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
        raise LSEGDataError(f"LSEG request failed after retries: {last_exc}")

    def list_assets(self) -> List[AssetInfo]:
        assets = []
        for inst in ALL_REAL_INSTRUMENTS:
            mapping = get_provider_mapping("lseg", inst.instrument_id)
            if mapping:
                assets.append(AssetInfo.from_instrument(
                    inst,
                    description=f"{inst.name} (LSEG: {mapping.provider_symbol})",
                ))
        return assets

    def resolve_instrument(self, instrument_or_symbol) -> str:
        if isinstance(instrument_or_symbol, Instrument):
            mapping = get_provider_mapping("lseg", instrument_or_symbol.instrument_id)
            if mapping:
                return mapping.provider_symbol
            return instrument_or_symbol.symbol

        if ":" in instrument_or_symbol:
            mapping = get_provider_mapping("lseg", instrument_or_symbol)
            if mapping:
                return mapping.provider_symbol

        return instrument_or_symbol

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        if not self.is_available():
            raise LSEGDataError("LSEG provider not connected. Call connect() first.")

        ric = self.resolve_instrument(symbol)

        try:
            return self._fetch_history(ric, start_date, end_date)
        except LSEGRateLimitError:
            raise
        except Exception as e:
            self._record_error(f"get_bars({symbol}) failed: {e}")
            logger.error("LSEG get_bars failed for %s: %s", symbol, e)
            raise LSEGDataError(f"Failed to fetch bars for {symbol}: {e}") from e

    def _fetch_history(
        self,
        ric: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        assert self._session is not None
        self._ensure_valid_token()
        if not self._api_key and self._access_token:
            self._session.headers["Authorization"] = f"Bearer {self._access_token}"

        url = f"{LSEG_API_BASE_URL}/data/historical-pricing/v1/views/eod-summaries"
        params = {
            "universe": ric,
            "start": start_date,
            "end": end_date,
        }

        resp = self._request_with_retry(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        bars: List[OHLCVBar] = []
        for row in data.get("captures", []):
            try:
                bar = OHLCVBar(
                    date=row["trading_date"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row.get("volume", 0)) or None,
                )
                bars.append(bar)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Skipping malformed LSEG row: %s — %s", row, e)
                continue

        bars.sort(key=lambda b: b.date)
        self._record_success()
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if not self.is_available():
            return None

        ric = self.resolve_instrument(symbol)

        try:
            assert self._session is not None
            self._ensure_valid_token()
            if not self._api_key and self._access_token:
                self._session.headers["Authorization"] = f"Bearer {self._access_token}"

            url = f"{LSEG_API_BASE_URL}/data/quotes/v1/{ric}"
            resp = self._request_with_retry(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            bar = OHLCVBar(
                date=data.get("trading_date", utcnow().strftime("%Y-%m-%d")),
                open=float(data.get("open", 0)),
                high=float(data.get("high", 0)),
                low=float(data.get("low", 0)),
                close=float(data.get("last", data.get("close", 0))),
                volume=int(data.get("volume", 0)) or None,
            )
            self._record_success()
            return bar
        except Exception as e:
            self._record_error(f"get_latest_bar({symbol}) failed: {e}")
            logger.warning("LSEG get_latest_bar failed for %s: %s", symbol, e)
            return None

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": "2020-01-02",
            "max": utcnow().strftime("%Y-%m-%d"),
            "total_trading_days": None,
            "note": "Actual range depends on LSEG subscription entitlements.",
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
                {"date": b.date, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                for b in bars
            ]

        raw = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_status(self) -> Dict[str, Any]:
        status = {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": self.is_available(),
            "connected": self._connected,
            "instruments_available": len(self.list_assets()),
        }
        rate_limited, retry_after = self._rate_limit_state()
        if not self._connected:
            health = "disconnected"
        elif rate_limited:
            health = "rate_limited"
        elif self._consecutive_errors >= _DEGRADED_THRESHOLD:
            health = "degraded"
        else:
            health = "ok"
        status.update({
            "health": health,
            "rate_limited": rate_limited,
            "rate_limited_retry_after_seconds": retry_after if rate_limited else 0,
            "consecutive_errors": self._consecutive_errors,
            "last_success_at": self._last_success_at,
            "last_error_at": self._last_error_at,
        })
        if self._last_error:
            status["last_error"] = self._last_error
        return status

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        inst = get_instrument_by_id(symbol)
        return {
            "data_nature": "live" if self.is_available() else "unknown",
            "adjustment": "unknown",
            "timezone": inst.timezone if inst else None,
            "note": "Corporate-action adjustment depends on LSEG entitlement; treated as unverified.",
            "note_zh": "公司行動調整視 LSEG 訂閱權限而定；未經確認前不作假設。",
        }

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        inst = get_instrument_by_id(instrument_id)
        if inst is None:
            return None
        mapping = get_provider_mapping("lseg", instrument_id)
        desc = f"{inst.name} (LSEG: {mapping.provider_symbol})" if mapping else inst.name
        return AssetInfo.from_instrument(inst, description=desc)

    def supports_instrument_id(self) -> bool:
        return True
