"""Instrument identity and market structure for US and HK markets.

This module provides stable, provider-independent instrument identification
and market structure metadata. It separates:

1. Internal instrument identity (stable across providers)
2. Provider-specific symbol mappings (exchange-specific formats)
3. Market structure (sessions, holidays, timezones)
4. Currency and exchange metadata

Design principles:
- Instruments are identified by (market, symbol) tuple, not bare ticker
- Each market has explicit exchange, currency, timezone
- Provider adapters map internal identity to provider-specific symbols
- Market structure (sessions, holidays) is explicit and queryable
"""

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Dict, List, Optional, Set
import zoneinfo


class Market(str, enum.Enum):
    """Supported markets."""
    US = "US"
    HK = "HK"


class Exchange(str, enum.Enum):
    """Supported exchanges."""
    # US exchanges
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    ARCA = "ARCA"  # NYSE Arca
    BATS = "BATS"  # Cboe BZX
    
    # HK exchanges
    HKEX = "HKEX"  # Hong Kong Exchanges and Clearing


class Currency(str, enum.Enum):
    """Supported currencies."""
    USD = "USD"
    HKD = "HKD"


class AssetType(str, enum.Enum):
    """Supported asset types."""
    STOCK = "STOCK"
    ETF = "ETF"
    # Future: WARRANT, RIGHT, BOND, etc.


@dataclass(frozen=True)
class TradingSession:
    """Trading session for a market.
    
    Attributes:
        name: Session name (e.g., "Regular", "Pre-market", "After-hours")
        start: Start time (local market time)
        end: End time (local market time)
        is_regular: Whether this is the regular trading session
    """
    name: str
    start: time
    end: time
    is_regular: bool = True


@dataclass(frozen=True)
class MarketStructure:
    """Market structure metadata.
    
    Attributes:
        market: Market identifier
        exchange: Primary exchange
        currency: Trading currency
        timezone: Market timezone (IANA format)
        sessions: Trading sessions
        holidays: Market holidays (non-trading days)
        lot_size: Default lot size (1 for US, variable for HK)
    """
    market: Market
    exchange: Exchange
    currency: Currency
    timezone: str
    sessions: List[TradingSession]
    holidays: Set[date] = field(default_factory=set)
    lot_size: int = 1
    
    def is_trading_day(self, d: date) -> bool:
        """Check if a date is a trading day."""
        # Weekend check
        if d.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        # Holiday check
        if d in self.holidays:
            return False
        return True
    
    def get_local_time(self, utc_dt: datetime) -> datetime:
        """Convert UTC datetime to local market time."""
        tz = zoneinfo.ZoneInfo(self.timezone)
        return utc_dt.astimezone(tz)


# US market structure (NYSE/NASDAQ)
US_MARKET_STRUCTURE = MarketStructure(
    market=Market.US,
    exchange=Exchange.NYSE,  # Primary, but applies to NASDAQ too
    currency=Currency.USD,
    timezone="America/New_York",
    sessions=[
        TradingSession(
            name="Pre-market",
            start=time(4, 0),
            end=time(9, 30),
            is_regular=False,
        ),
        TradingSession(
            name="Regular",
            start=time(9, 30),
            end=time(16, 0),
            is_regular=True,
        ),
        TradingSession(
            name="After-hours",
            start=time(16, 0),
            end=time(20, 0),
            is_regular=False,
        ),
    ],
    holidays=set(),  # To be populated with actual US market holidays
    lot_size=1,
)

# HK market structure (HKEX)
HK_MARKET_STRUCTURE = MarketStructure(
    market=Market.HK,
    exchange=Exchange.HKEX,
    currency=Currency.HKD,
    timezone="Asia/Hong_Kong",
    sessions=[
        TradingSession(
            name="Pre-opening",
            start=time(9, 0),
            end=time(9, 30),
            is_regular=False,
        ),
        TradingSession(
            name="Morning",
            start=time(9, 30),
            end=time(12, 0),
            is_regular=True,
        ),
        TradingSession(
            name="Afternoon",
            start=time(13, 0),
            end=time(16, 0),
            is_regular=True,
        ),
        TradingSession(
            name="Closing auction",
            start=time(16, 0),
            end=time(16, 10),
            is_regular=False,
        ),
    ],
    holidays=set(),  # To be populated with actual HK market holidays
    lot_size=100,  # HK stocks typically trade in board lots of 100 shares
)


@dataclass(frozen=True)
class Instrument:
    """Stable, provider-independent instrument identity.
    
    An instrument is uniquely identified by (market, symbol).
    The symbol is the primary listing symbol on the primary exchange.
    
    Attributes:
        market: Market (US or HK)
        symbol: Primary listing symbol (e.g., "AAPL", "0700")
        name: Instrument name
        asset_type: Asset type (STOCK, ETF, etc.)
        exchange: Primary exchange
        currency: Trading currency (derived from market)
        timezone: Market timezone (derived from market)
        lot_size: Default lot size (derived from market)
    """
    market: Market
    symbol: str
    name: str
    asset_type: AssetType
    exchange: Exchange
    currency: Currency
    timezone: str
    lot_size: int
    
    @property
    def instrument_id(self) -> str:
        """Stable internal identifier: MARKET:SYMBOL."""
        return f"{self.market.value}:{self.symbol}"
    
    @classmethod
    def create_us(cls, symbol: str, name: str, asset_type: AssetType, 
                  exchange: Exchange = Exchange.NYSE) -> "Instrument":
        """Create a US instrument."""
        return cls(
            market=Market.US,
            symbol=symbol.upper(),
            name=name,
            asset_type=asset_type,
            exchange=exchange,
            currency=Currency.USD,
            timezone=US_MARKET_STRUCTURE.timezone,
            lot_size=US_MARKET_STRUCTURE.lot_size,
        )
    
    @classmethod
    def create_hk(cls, symbol: str, name: str, asset_type: AssetType,
                  exchange: Exchange = Exchange.HKEX) -> "Instrument":
        """Create an HK instrument.
        
        HK symbols are typically 4-digit codes (e.g., "0700" for Tencent).
        """
        return cls(
            market=Market.HK,
            symbol=symbol.zfill(4),  # Pad to 4 digits
            name=name,
            asset_type=asset_type,
            exchange=exchange,
            currency=Currency.HKD,
            timezone=HK_MARKET_STRUCTURE.timezone,
            lot_size=HK_MARKET_STRUCTURE.lot_size,
        )


@dataclass(frozen=True)
class ProviderSymbolMapping:
    """Maps internal instrument identity to provider-specific symbol.
    
    Different providers use different symbol formats:
    - LSEG: May use RIC (e.g., "AAPL.O" for NASDAQ, "0700.HK" for HKEX)
    - IBKR: Uses contract specification (symbol, exchange, currency, secType)
    - Demo: Uses bare symbol
    
    This mapping allows provider adapters to translate between internal
    identity and provider-specific formats.
    """
    provider_id: str
    instrument_id: str  # MARKET:SYMBOL format
    provider_symbol: str  # Provider-specific symbol
    provider_exchange: Optional[str] = None  # Provider-specific exchange code
    metadata: Dict[str, str] = field(default_factory=dict)  # Additional provider-specific fields


def get_market_structure(market: Market) -> MarketStructure:
    """Get market structure for a market."""
    if market == Market.US:
        return US_MARKET_STRUCTURE
    elif market == Market.HK:
        return HK_MARKET_STRUCTURE
    else:
        raise ValueError(f"Unknown market: {market}")


def parse_instrument_id(instrument_id: str) -> tuple[Market, str]:
    """Parse instrument ID into (market, symbol) tuple.
    
    Format: MARKET:SYMBOL (e.g., "US:AAPL", "HK:0700")
    """
    parts = instrument_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid instrument ID format: {instrument_id}")
    
    market_str, symbol = parts
    try:
        market = Market(market_str)
    except ValueError:
        raise ValueError(f"Unknown market: {market_str}")
    
    return market, symbol
