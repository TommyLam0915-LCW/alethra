"""LSEG (London Stock Exchange Group) data provider adapter.

Implements the DataProvider interface for LSEG's market data API.
Requires LSEG API credentials (client_id, client_secret, app_key).

Status: BLOCKED — requires LSEG DataLibrary credentials.
This adapter implements the full contract but will raise on actual
data fetches until credentials are configured. Offline tests verify
the adapter's structure, error handling, and symbol mapping logic.
"""

import logging
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

logger = logging.getLogger(__name__)


class LSEGConfigurationError(Exception):
    """Raised when LSEG credentials or configuration are missing/invalid."""


class LSEGDataError(Exception):
    """Raised when LSEG API returns an error or unexpected data."""


class LSEGDataProvider(DataProvider):
    """LSEG market data provider.

    Requires:
    - LSEG_API_KEY or LSEG_CLIENT_ID + LSEG_CLIENT_SECRET in environment
    - Access to LSEG DataScope Select or Refinitiv Data Library

    Symbol mapping: Internal instrument IDs (US:AAPL) are mapped to
    LSEG RIC format (AAPL.O) via LSEG_MAPPINGS in real_instruments.py.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self._api_key = api_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = None
        self._connected = False
        self._last_error: Optional[str] = None

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

    def connect(self) -> bool:
        """Establish connection to LSEG API.

        Returns True on success, False on failure.
        Sets _last_error on failure for status reporting.
        """
        try:
            self._check_credentials()
        except LSEGConfigurationError as e:
            self._last_error = str(e)
            self._connected = False
            return False

        try:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self._api_key or ''}",
            })
            self._connected = True
            self._last_error = None
            logger.info("LSEG provider connected")
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

    def is_available(self) -> bool:
        return self._connected and self._session is not None

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
        except Exception as e:
            self._last_error = f"get_bars({symbol}) failed: {e}"
            logger.error("LSEG get_bars failed for %s: %s", symbol, e)
            raise LSEGDataError(f"Failed to fetch bars for {symbol}: {e}") from e

    def _fetch_history(
        self,
        ric: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        assert self._session is not None

        url = "https://api.refinitiv.com/data/historical-pricing/v1/views/eod-summaries"
        params = {
            "universe": ric,
            "start": start_date,
            "end": end_date,
        }

        resp = self._session.get(url, params=params, timeout=30)
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
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if not self.is_available():
            return None

        ric = self.resolve_instrument(symbol)

        try:
            assert self._session is not None
            url = f"https://api.refinitiv.com/data/quotes/v1/{ric}"
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            return OHLCVBar(
                date=data.get("trading_date", datetime.utcnow().strftime("%Y-%m-%d")),
                open=float(data.get("open", 0)),
                high=float(data.get("high", 0)),
                low=float(data.get("low", 0)),
                close=float(data.get("last", data.get("close", 0))),
                volume=int(data.get("volume", 0)) or None,
            )
        except Exception as e:
            self._last_error = f"get_latest_bar({symbol}) failed: {e}"
            logger.warning("LSEG get_latest_bar failed for %s: %s", symbol, e)
            return None

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": "2020-01-02",
            "max": datetime.utcnow().strftime("%Y-%m-%d"),
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
        if self._last_error:
            status["last_error"] = self._last_error
        return status

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        inst = get_instrument_by_id(instrument_id)
        if inst is None:
            return None
        mapping = get_provider_mapping("lseg", instrument_id)
        desc = f"{inst.name} (LSEG: {mapping.provider_symbol})" if mapping else inst.name
        return AssetInfo.from_instrument(inst, description=desc)

    def supports_instrument_id(self) -> bool:
        return True
