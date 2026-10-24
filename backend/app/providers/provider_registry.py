import logging
import os
from functools import lru_cache
from typing import Dict, Any, Optional

from app.config import get_settings
from app.providers.data_provider import DataProvider
from app.providers.demo_provider import DemoDataProvider

logger = logging.getLogger(__name__)

_PROVIDERS: Dict[str, DataProvider] = {
    "demo": DemoDataProvider(),
}
_LSEG_INITIALIZED = False
_TWELVE_DATA_INITIALIZED = False


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


def _try_init_twelve_data() -> Optional[DataProvider]:
    """Lazy-init Twelve Data provider.

    Reads TWELVE_DATA_API_KEY from process environment.
    - Missing/empty key: raises ValueError (never silently falls back to demo).
    - Key present: constructs TwelveDataProvider without network calls.
    - On failure the init is NOT cached so a later retry is possible.
    """
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "DATA_PROVIDER=twelve_data requires TWELVE_DATA_API_KEY to be set "
            "in the process environment. The key is missing or empty."
        )

    from app.providers.twelve_data_provider import TwelveDataProvider
    logger.info("Twelve Data provider auto-registered")
    return TwelveDataProvider(api_key=api_key)


def get_data_provider() -> DataProvider:
    global _LSEG_INITIALIZED, _TWELVE_DATA_INITIALIZED
    settings = get_settings()
    provider_id = settings.data_provider

    if provider_id == "lseg" and not _LSEG_INITIALIZED:
        _LSEG_INITIALIZED = True
        lseg = _try_init_lseg()
        if lseg:
            _PROVIDERS["lseg"] = lseg

    if provider_id == "twelve_data" and not _TWELVE_DATA_INITIALIZED:
        td = _try_init_twelve_data()
        # Only mark initialised on success; failure is not cached so the
        # caller can retry after setting TWELVE_DATA_API_KEY.
        _TWELVE_DATA_INITIALIZED = True
        _PROVIDERS["twelve_data"] = td

    if provider_id not in _PROVIDERS:
        raise ValueError(
            f"Unknown data provider: {provider_id}. "
            f"Available: {list(_PROVIDERS.keys())}"
        )

    return _PROVIDERS[provider_id]


def get_provider_info() -> Dict[str, Any]:
    provider = get_data_provider()
    return {
        "active_provider": provider.provider_id,
        "provider_name": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "available": provider.is_available(),
        "status": provider.get_status(),
    }


def register_provider(provider_id: str, provider: DataProvider) -> None:
    _PROVIDERS[provider_id] = provider
