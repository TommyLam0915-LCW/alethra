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
_TWELVEDATA_INITIALIZED = False


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


def get_data_provider() -> DataProvider:
    global _LSEG_INITIALIZED, _TWELVEDATA_INITIALIZED
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
