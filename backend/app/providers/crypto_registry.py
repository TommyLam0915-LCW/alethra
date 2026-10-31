"""Process-wide handle for the crypto market-data provider.

Kept separate from ``app.providers.get_data_provider`` on purpose: that function
is what the equity research lanes resolve, and switching it to a crypto venue
would silently change what every existing route serves. Crypto is an explicit
lane with its own declared pair, reached only through this module.
"""

from __future__ import annotations

from typing import Optional

from app.providers.kraken_provider import KrakenProvider
from app.services.upstream_budget import get_upstream_budget_gate

_provider: Optional[KrakenProvider] = None


def get_crypto_provider() -> KrakenProvider:
    global _provider
    if _provider is None:
        # The shared database gate is injected here, not defaulted inside the
        # adapter, so every application request is counted in one budget while
        # a bare adapter in a unit test needs no database.
        _provider = KrakenProvider(budget_gate=get_upstream_budget_gate())
    return _provider


def set_crypto_provider(provider: Optional[KrakenProvider]) -> None:
    """Replace the handle (tests inject an offline transport this way)."""
    global _provider
    _provider = provider


def reset_crypto_provider() -> None:
    global _provider
    _provider = None
