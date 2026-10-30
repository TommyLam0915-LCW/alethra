"""Process-wide handle for the crypto market-data provider.

Kept separate from ``app.providers.get_data_provider`` on purpose: that function
is what the equity research lanes resolve, and switching it to a crypto venue
would silently change what every existing route serves. Crypto is an explicit
lane with its own declared pair, reached only through this module.
"""

from __future__ import annotations

from typing import Optional

from app.providers.kraken_provider import KrakenProvider

_provider: Optional[KrakenProvider] = None


def get_crypto_provider() -> KrakenProvider:
    global _provider
    if _provider is None:
        _provider = KrakenProvider()
    return _provider


def set_crypto_provider(provider: Optional[KrakenProvider]) -> None:
    """Replace the handle (tests inject an offline transport this way)."""
    global _provider
    _provider = provider


def reset_crypto_provider() -> None:
    global _provider
    _provider = None
