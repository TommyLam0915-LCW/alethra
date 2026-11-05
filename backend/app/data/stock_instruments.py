"""Stock market instrument definitions for Yahoo Finance."""
from typing import List, Optional
from app.providers.instruments import Instrument, Exchange, AssetType


YAHOO_STOCK_INSTRUMENTS: List[Instrument] = [
    Instrument.create_us(
        symbol="AAPL",
        name="Apple Inc.",
        asset_type=AssetType.STOCK,
        exchange=Exchange.NASDAQ,
    ),
    Instrument.create_us(
        symbol="TSLA",
        name="Tesla, Inc.",
        asset_type=AssetType.STOCK,
        exchange=Exchange.NASDAQ,
    ),
    Instrument.create_us(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        asset_type=AssetType.ETF,
        exchange=Exchange.ARCA,
    ),
]


def get_stock_instrument(instrument_id: str) -> Optional[Instrument]:
    """Get stock instrument by ID."""
    for instrument in YAHOO_STOCK_INSTRUMENTS:
        if instrument.instrument_id == instrument_id:
            return instrument
    return None


def get_stock_instruments() -> List[Instrument]:
    """Get all stock instruments."""
    return YAHOO_STOCK_INSTRUMENTS


YAHOO_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "60m",
    "1d": "1d",
    "1wk": "1wk",
    "1mo": "1mo",
}

YAHOO_INTERVAL_MINUTES = {
    1: "1m",
    5: "5m",
    15: "15m",
    30: "30m",
    60: "60m",
    1440: "1d",
    10080: "1wk",
    43200: "1mo",
}
