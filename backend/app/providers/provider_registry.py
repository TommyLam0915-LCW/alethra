from functools import lru_cache
from typing import Dict, Any

from app.config import get_settings
from app.providers.data_provider import DataProvider
from app.providers.demo_provider import DemoDataProvider


_PROVIDERS: Dict[str, DataProvider] = {
    "demo": DemoDataProvider(),
}


def get_data_provider() -> DataProvider:
    settings = get_settings()
    provider_id = settings.data_provider

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
