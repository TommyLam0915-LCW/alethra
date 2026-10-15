"""Real market instrument definitions for US and HK markets.

These are the instruments Alethra supports for real market data and trading.
Demo provider uses these to demonstrate multi-market support.
"""

from app.providers.instruments import (
    Instrument,
    Market,
    Exchange,
    AssetType,
    ProviderSymbolMapping,
)


# US Stocks
US_STOCKS = [
    Instrument.create_us("AAPL", "Apple Inc.", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("MSFT", "Microsoft Corporation", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("GOOGL", "Alphabet Inc.", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("AMZN", "Amazon.com Inc.", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("NVDA", "NVIDIA Corporation", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("META", "Meta Platforms Inc.", AssetType.STOCK, Exchange.NASDAQ),
    Instrument.create_us("TSLA", "Tesla Inc.", AssetType.STOCK, Exchange.NASDAQ),
]

# US ETFs
US_ETFS = [
    Instrument.create_us("SPY", "SPDR S&P 500 ETF Trust", AssetType.ETF, Exchange.ARCA),
    Instrument.create_us("QQQ", "Invesco QQQ Trust", AssetType.ETF, Exchange.NASDAQ),
    Instrument.create_us("VTI", "Vanguard Total Stock Market ETF", AssetType.ETF, Exchange.ARCA),
]

# HK Stocks (all HKEX)
HK_STOCKS = [
    Instrument.create_hk("0700", "Tencent Holdings Ltd.", AssetType.STOCK),
    Instrument.create_hk("9988", "Alibaba Group Holding Ltd.", AssetType.STOCK),
    Instrument.create_hk("0005", "HSBC Holdings plc", AssetType.STOCK),
    Instrument.create_hk("2318", "Ping An Insurance Group", AssetType.STOCK),
    Instrument.create_hk("0941", "China Mobile Ltd.", AssetType.STOCK),
    Instrument.create_hk("1299", "AIA Group Ltd.", AssetType.STOCK),
    Instrument.create_hk("3690", "Meituan", AssetType.STOCK),
]

# HK ETFs
HK_ETFS = [
    Instrument.create_hk("2800", "Tracker Fund of Hong Kong", AssetType.ETF),
    Instrument.create_hk("3067", "iShares Hang Seng TECH ETF", AssetType.ETF),
]

ALL_REAL_INSTRUMENTS = US_STOCKS + US_ETFS + HK_STOCKS + HK_ETFS


# Provider symbol mappings for LSEG (RIC format)
LSEG_MAPPINGS = {
    "US:AAPL": ProviderSymbolMapping("lseg", "US:AAPL", "AAPL.O", "NAS"),
    "US:MSFT": ProviderSymbolMapping("lseg", "US:MSFT", "MSFT.O", "NAS"),
    "US:GOOGL": ProviderSymbolMapping("lseg", "US:GOOGL", "GOOGL.O", "NAS"),
    "US:AMZN": ProviderSymbolMapping("lseg", "US:AMZN", "AMZN.O", "NAS"),
    "US:NVDA": ProviderSymbolMapping("lseg", "US:NVDA", "NVDA.O", "NAS"),
    "US:META": ProviderSymbolMapping("lseg", "US:META", "META.O", "NAS"),
    "US:TSLA": ProviderSymbolMapping("lseg", "US:TSLA", "TSLA.O", "NAS"),
    "US:SPY": ProviderSymbolMapping("lseg", "US:SPY", "SPY.A", "ARC"),
    "US:QQQ": ProviderSymbolMapping("lseg", "US:QQQ", "QQQ.O", "NAS"),
    "US:VTI": ProviderSymbolMapping("lseg", "US:VTI", "VTI.A", "ARC"),
    "HK:0700": ProviderSymbolMapping("lseg", "HK:0700", "0700.HK", "HKG"),
    "HK:9988": ProviderSymbolMapping("lseg", "HK:9988", "9988.HK", "HKG"),
    "HK:0005": ProviderSymbolMapping("lseg", "HK:0005", "0005.HK", "HKG"),
    "HK:2318": ProviderSymbolMapping("lseg", "HK:2318", "2318.HK", "HKG"),
    "HK:0941": ProviderSymbolMapping("lseg", "HK:0941", "0941.HK", "HKG"),
    "HK:1299": ProviderSymbolMapping("lseg", "HK:1299", "1299.HK", "HKG"),
    "HK:3690": ProviderSymbolMapping("lseg", "HK:3690", "3690.HK", "HKG"),
    "HK:2800": ProviderSymbolMapping("lseg", "HK:2800", "2800.HK", "HKG"),
    "HK:3067": ProviderSymbolMapping("lseg", "HK:3067", "3067.HK", "HKG"),
}


# Provider symbol mappings for IBKR (contract specification)
IBKR_MAPPINGS = {
    "US:AAPL": ProviderSymbolMapping(
        "ibkr", "US:AAPL", "AAPL", "SMART",
        {"secType": "STK", "currency": "USD", "primaryExch": "NASDAQ"},
    ),
    "US:MSFT": ProviderSymbolMapping(
        "ibkr", "US:MSFT", "MSFT", "SMART",
        {"secType": "STK", "currency": "USD", "primaryExch": "NASDAQ"},
    ),
    "US:SPY": ProviderSymbolMapping(
        "ibkr", "US:SPY", "SPY", "SMART",
        {"secType": "STK", "currency": "USD", "primaryExch": "ARCA"},
    ),
    "HK:0700": ProviderSymbolMapping(
        "ibkr", "HK:0700", "0700", "SEHK",
        {"secType": "STK", "currency": "HKD"},
    ),
    "HK:9988": ProviderSymbolMapping(
        "ibkr", "HK:9988", "9988", "SEHK",
        {"secType": "STK", "currency": "HKD"},
    ),
    "HK:0005": ProviderSymbolMapping(
        "ibkr", "HK:0005", "0005", "SEHK",
        {"secType": "STK", "currency": "HKD"},
    ),
}


def get_all_instruments() -> list[Instrument]:
    """Get all supported real-market instruments."""
    return ALL_REAL_INSTRUMENTS


def get_instrument_by_id(instrument_id: str) -> Instrument | None:
    """Look up an instrument by its instrument_id (e.g., 'US:AAPL')."""
    for inst in ALL_REAL_INSTRUMENTS:
        if inst.instrument_id == instrument_id:
            return inst
    return None


def get_provider_mapping(provider_id: str, instrument_id: str) -> ProviderSymbolMapping | None:
    """Get provider-specific symbol mapping for an instrument."""
    mappings = {
        "lseg": LSEG_MAPPINGS,
        "ibkr": IBKR_MAPPINGS,
    }
    provider_map = mappings.get(provider_id, {})
    return provider_map.get(instrument_id)
