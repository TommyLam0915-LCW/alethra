"""Crypto spot instruments available to the research workspace.

Scope of the first slice is deliberately one venue and one pair. Two venues
quoting "BTC-USD" are two different instruments: the venue is part of the
instrument id, never a footnote on a price.

Trading specs below were read from Kraken's public ``AssetPairs`` response on
2026-10-30 (``lot_decimals`` 8, ``pair_decimals`` 1, ``ordermin`` 0.00006,
``costmin`` 0.5, ``tick_size`` 0.1, ``status`` online). Kraken can change them,
so :meth:`KrakenProvider.get_trading_specs` re-reads them at runtime and the
declared values here are the fallback, not the authority.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from app.providers.instruments import (
    Currency,
    Exchange,
    Instrument,
    ProviderSymbolMapping,
)

SPEC_SOURCE = "kraken_assetpairs_2026-10-30"

KRAKEN_BTC_USD = Instrument.create_crypto_pair(
    symbol="BTC-USD",
    name="Bitcoin / US Dollar (spot)",
    base_asset="BTC",
    quote=Currency.USD,
    exchange=Exchange.KRAKEN,
    price_precision=1,
    quantity_precision=8,
    min_quantity=Decimal("0.00006"),
    min_notional=Decimal("0.5"),
)

KRAKEN_ETH_USD = Instrument.create_crypto_pair(
    symbol="ETH-USD",
    name="Ethereum / US Dollar (spot)",
    base_asset="ETH",
    quote=Currency.USD,
    exchange=Exchange.KRAKEN,
    price_precision=2,
    quantity_precision=8,
    min_quantity=Decimal("0.01"),
    min_notional=Decimal("1.0"),
)

KRAKEN_SOL_USD = Instrument.create_crypto_pair(
    symbol="SOL-USD",
    name="Solana / US Dollar (spot)",
    base_asset="SOL",
    quote=Currency.USD,
    exchange=Exchange.KRAKEN,
    price_precision=3,
    quantity_precision=8,
    min_quantity=Decimal("0.1"),
    min_notional=Decimal("1.0"),
)

CRYPTO_INSTRUMENTS: List[Instrument] = [KRAKEN_BTC_USD, KRAKEN_ETH_USD, KRAKEN_SOL_USD]

# Kraken names bitcoin XBT and reports the pair as XXBTZUSD, with XBTUSD as the
# public altname. Both are Kraken's spellings of the same pair; the internal id
# stays CRYPTO:KRAKEN:BTC-USD so nothing downstream sees an exchange nickname.
# ETH uses XETHZUSD/ETHUSD, SOL uses SOLUSD/SOLUSD (no X prefix).
KRAKEN_PAIR_MAP: Dict[str, ProviderSymbolMapping] = {
    KRAKEN_BTC_USD.instrument_id: ProviderSymbolMapping(
        provider_id="kraken",
        instrument_id=KRAKEN_BTC_USD.instrument_id,
        provider_symbol="XBTUSD",
        provider_exchange="KRAKEN",
        metadata={
            "result_key": "XXBTZUSD",
            "result_key_alt": "XBTUSD",
            "wsname": "XBT/USD",
            "spec_source": SPEC_SOURCE,
        },
    ),
    KRAKEN_ETH_USD.instrument_id: ProviderSymbolMapping(
        provider_id="kraken",
        instrument_id=KRAKEN_ETH_USD.instrument_id,
        provider_symbol="ETHUSD",
        provider_exchange="KRAKEN",
        metadata={
            "result_key": "XETHZUSD",
            "result_key_alt": "ETHUSD",
            "wsname": "ETH/USD",
            "spec_source": SPEC_SOURCE,
        },
    ),
    KRAKEN_SOL_USD.instrument_id: ProviderSymbolMapping(
        provider_id="kraken",
        instrument_id=KRAKEN_SOL_USD.instrument_id,
        provider_symbol="SOLUSD",
        provider_exchange="KRAKEN",
        metadata={
            "result_key": "SOLUSD",
            "result_key_alt": "SOLUSD",
            "wsname": "SOL/USD",
            "spec_source": SPEC_SOURCE,
        },
    ),
}


def get_crypto_instrument(instrument_id: str) -> Instrument | None:
    for instrument in CRYPTO_INSTRUMENTS:
        if instrument.instrument_id == instrument_id:
            return instrument
    return None


def get_kraken_pair(instrument_id: str) -> ProviderSymbolMapping | None:
    return KRAKEN_PAIR_MAP.get(instrument_id)
