"""Forex instrument definitions.

Defines major currency pairs for forex market testing.
Uses the same Instrument factory pattern as crypto and stocks.
"""
from typing import List
from app.providers.instruments import (
    Instrument,
    Market,
    AssetType,
    Exchange,
    Currency,
)


# Forex pairs are quoted as XXX/YYY where XXX is the base currency and YYY is
# the quote currency. EUR/USD means "how many USD per 1 EUR".
#
# For forex, we use Market.US as the default since most forex is USD-denominated
# and trades during US/European market hours. The currency field represents the
# quote currency (the second leg of the pair).

EUR_USD = Instrument(
    market=Market.FOREX,
    symbol="EUR/USD",
    name="Euro / US Dollar",
    asset_type=AssetType.FOREX_PAIR,
    exchange=Exchange.FOREX,
    currency=Currency.USD,
    timezone="UTC",  # Forex trades 24/5, use UTC
    lot_size=100000,  # Standard lot
    base_asset="EUR",
    price_precision=5,  # 5 decimal places for major pairs
    quantity_precision=2,
    min_quantity=1.0,  # Minimum 1 unit (micro lot = 0.01 standard lot)
    min_notional=10.0,  # Minimum $10 notional
)

GBP_USD = Instrument(
    market=Market.FOREX,
    symbol="GBP/USD",
    name="British Pound / US Dollar",
    asset_type=AssetType.FOREX_PAIR,
    exchange=Exchange.FOREX,
    currency=Currency.USD,
    timezone="UTC",
    lot_size=100000,
    base_asset="GBP",
    price_precision=5,
    quantity_precision=2,
    min_quantity=1.0,
    min_notional=10.0,
)

USD_JPY = Instrument(
    market=Market.FOREX,
    symbol="USD/JPY",
    name="US Dollar / Japanese Yen",
    asset_type=AssetType.FOREX_PAIR,
    exchange=Exchange.FOREX,
    currency=Currency.JPY,
    timezone="UTC",
    lot_size=100000,
    base_asset="USD",
    price_precision=3,  # 3 decimal places for JPY pairs
    quantity_precision=2,
    min_quantity=1.0,
    min_notional=1000.0,  # Minimum ¥1000
)

FOREX_INSTRUMENTS: List[Instrument] = [EUR_USD, GBP_USD, USD_JPY]


def get_forex_instrument(instrument_id: str):
    """Look up a forex instrument by its stable ID.
    
    Returns None if the instrument_id does not match any declared forex pair.
    """
    for instrument in FOREX_INSTRUMENTS:
        if instrument.instrument_id == instrument_id:
            return instrument
    return None


# Forex markets trade 24/5 (Sunday 5pm ET to Friday 5pm ET), so we support
# a wide range of intervals from 1-minute to daily.
FOREX_INTERVAL_MINUTES = {
    1: "1min",
    5: "5min",
    15: "15min",
    30: "30min",
    60: "1h",
    240: "4h",
    1440: "1d",
    10080: "1w",
    43200: "1mo",
}
