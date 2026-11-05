import logging
import os
from functools import lru_cache
from typing import Dict, Any, List, Optional

from app.config import get_settings
from app.lib.error_safety import safe_provider_status
from app.providers.data_provider import (
    AssetInfo,
    DataProvider,
    OHLCVBar,
    ProviderNotConfiguredError,
)
from app.providers.demo_provider import DemoDataProvider

logger = logging.getLogger(__name__)

_PROVIDERS: Dict[str, DataProvider] = {
    "demo": DemoDataProvider(),
}
_UNCONFIGURED: Dict[str, "UnconfiguredDataProvider"] = {}
_LSEG_INITIALIZED = False
_TWELVEDATA_INITIALIZED = False
_SYNTHETIC_STOCK_INITIALIZED = False
_SYNTHETIC_FOREX_INITIALIZED = False


class UnconfiguredDataProvider(DataProvider):
    """Selected provider that has no usable credentials.

    Missing credentials are a normal deployment state: listing instruments and
    reading stored bars must keep working, while new-data requests are refused
    with a stable ``not_configured`` reason. It never returns demo or synthetic
    bars, so a caller cannot mistake this for a working feed.
    """

    def __init__(self, provider_id: str):
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_name(self) -> str:
        return f"{self._provider_id} (not configured)"

    @property
    def feed_mode(self) -> str:
        return "unavailable"

    def is_available(self) -> bool:
        return False

    def list_assets(self) -> List[AssetInfo]:
        return []

    def _refuse(self) -> ProviderNotConfiguredError:
        return ProviderNotConfiguredError(
            f"Market-data provider {self._provider_id!r} is not configured; "
            "no upstream request was sent."
        )

    def get_bars(self, symbol: str, start_date: str, end_date: str) -> List[OHLCVBar]:
        raise self._refuse()

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        raise self._refuse()

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": None,
            "max": None,
            "total_trading_days": None,
            "note": "Provider is not configured; no data range is available.",
        }

    def compute_checksum(self, symbols: List[str], start_date: str, end_date: str) -> str:
        raise self._refuse()

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        return {
            "data_nature": "unknown",
            "adjustment": "unknown",
            "timezone": None,
            "note": "The selected market-data provider is not configured.",
            "note_zh": "已選的行情供應商尚未設定。",
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "provider_id": self._provider_id,
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": False,
            "connected": False,
            "configured": False,
            "reason": "not_configured",
            "detail": (
                "The selected provider has no usable credentials. Catalog and "
                "stored data remain readable; new data requests are unavailable."
            ),
        }


def _try_init_lseg() -> Optional[DataProvider]:
    api_key = os.environ.get("LSEG_API_KEY")
    client_id = os.environ.get("LSEG_CLIENT_ID")
    client_secret = os.environ.get("LSEG_CLIENT_SECRET")

    if not api_key and not (client_id and client_secret):
        return None

    from app.providers.lseg_provider import LSEGDataProvider
    provider = LSEGDataProvider(
        api_key=api_key,
        client_id=client_id,
        client_secret=client_secret,
    )
    if provider.connect():
        logger.info("LSEG provider auto-registered")
        return provider
    logger.warning("LSEG provider failed to connect: %s", provider._last_error)
    return None


def _try_init_twelvedata() -> Optional[DataProvider]:
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        return None

    from app.providers.twelvedata_provider import TwelveDataProvider
    provider = TwelveDataProvider(api_key=api_key)
    if provider.connect():
        logger.info("Twelve Data provider auto-registered")
        return provider
    logger.warning("Twelve Data provider failed to connect: %s", provider._last_error)
    return None


def _try_init_synthetic_stock() -> Optional[DataProvider]:
    """Initialize synthetic stock provider for testing."""
    try:
        from app.providers.synthetic_stock_provider import SyntheticStockProvider
        provider = SyntheticStockProvider()
        logger.info("Synthetic stock provider auto-registered (testing only)")
        return provider
    except Exception as exc:
        logger.warning("Synthetic stock provider failed to initialize: %s", exc)
        return None


def _try_init_synthetic_forex() -> Optional[DataProvider]:
    """Initialize synthetic forex provider for testing."""
    try:
        from app.providers.synthetic_forex_provider import SyntheticForexProvider
        provider = SyntheticForexProvider()
        logger.info("Synthetic forex provider auto-registered (testing only)")
        return provider
    except Exception as exc:
        logger.warning("Synthetic forex provider failed to initialize: %s", exc)
        return None


def get_data_provider() -> DataProvider:
    global _LSEG_INITIALIZED, _TWELVEDATA_INITIALIZED, _SYNTHETIC_STOCK_INITIALIZED, _SYNTHETIC_FOREX_INITIALIZED
    settings = get_settings()
    provider_id = settings.data_provider

    if provider_id == "lseg" and not _LSEG_INITIALIZED:
        _LSEG_INITIALIZED = True
        lseg = _try_init_lseg()
        if lseg:
            _PROVIDERS["lseg"] = lseg

    if provider_id == "twelvedata" and not _TWELVEDATA_INITIALIZED:
        _TWELVEDATA_INITIALIZED = True
        td = _try_init_twelvedata()
        if td:
            _PROVIDERS["twelvedata"] = td

    if provider_id == "synthetic_stock" and not _SYNTHETIC_STOCK_INITIALIZED:
        _SYNTHETIC_STOCK_INITIALIZED = True
        ss = _try_init_synthetic_stock()
        if ss:
            _PROVIDERS["synthetic_stock"] = ss

    if provider_id == "synthetic_forex" and not _SYNTHETIC_FOREX_INITIALIZED:
        _SYNTHETIC_FOREX_INITIALIZED = True
        sf = _try_init_synthetic_forex()
        if sf:
            _PROVIDERS["synthetic_forex"] = sf

    if provider_id not in _PROVIDERS:
        # Missing credentials are a normal deployment state, not a boot failure.
        # The selected name is preserved so status can report *which* provider
        # is unavailable, and every data call refuses instead of swapping in
        # demo/synthetic bars.
        stub = _UNCONFIGURED.get(provider_id)
        if stub is None:
            stub = UnconfiguredDataProvider(provider_id)
            _UNCONFIGURED[provider_id] = stub
        return stub

    return _PROVIDERS[provider_id]


def is_provider_configured(provider: DataProvider) -> bool:
    return not isinstance(provider, UnconfiguredDataProvider)


def get_provider_info() -> Dict[str, Any]:
    provider = get_data_provider()
    # get_status() carries the last upstream error, which is raw exception
    # text; this payload is served to the browser.
    status = safe_provider_status(provider.get_status())
    return {
        "active_provider": provider.provider_id,
        "provider_name": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "available": provider.is_available(),
        "configured": status.get("configured", True),
        "reason": status.get("reason"),
        "status": status,
    }


def register_provider(provider_id: str, provider: DataProvider) -> None:
    _PROVIDERS[provider_id] = provider
