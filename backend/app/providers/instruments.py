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
from decimal import Decimal
from typing import Dict, List, Optional, Set
import zoneinfo


class Market(str, enum.Enum):
    """Supported markets."""
    US = "US"
    HK = "HK"
    CRYPTO = "CRYPTO"


class Exchange(str, enum.Enum):
    """Supported exchanges."""
    # US exchanges
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    ARCA = "ARCA"  # NYSE Arca
    BATS = "BATS"  # Cboe BZX
    
    # HK exchanges
    HKEX = "HKEX"  # Hong Kong Exchanges and Clearing

    # Crypto spot venues (one at a time: a price without its venue is not an
    # instrument, so every crypto instrument_id carries the exchange)
    KRAKEN = "KRAKEN"


class Currency(str, enum.Enum):
    """Supported currencies."""
    USD = "USD"
    HKD = "HKD"


class AssetType(str, enum.Enum):
    """Supported asset types."""
    STOCK = "STOCK"
    ETF = "ETF"
    CRYPTO_PAIR = "CRYPTO_PAIR"
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
    always_open: bool = False
    
    def is_trading_day(self, d: date) -> bool:
        """Check if a date is a trading day."""
        if self.always_open:
            return True
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

# Crypto spot venues quote continuously; there is no session close and no
# weekend, so staleness is measured against the bar interval, never a calendar.
CRYPTO_MARKET_STRUCTURE = MarketStructure(
    market=Market.CRYPTO,
    exchange=Exchange.KRAKEN,
    currency=Currency.USD,
    timezone="UTC",
    sessions=[
        TradingSession(
            name="Continuous",
            start=time(0, 0),
            end=time.max,
            is_regular=True,
        ),
    ],
    holidays=set(),
    lot_size=1,
    always_open=True,
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
        lot_size: Board lot size. 1 for US; per-stock for HK (None if unverified).
    """
    market: Market
    symbol: str
    name: str
    asset_type: AssetType
    exchange: Exchange
    currency: Currency
    timezone: str
    lot_size: Optional[int] = None
    # Crypto spot identity: which asset is priced, how many decimals the venue
    # publishes, and the smallest order it accepts. None means "not declared".
    base_asset: Optional[str] = None
    price_precision: Optional[int] = None
    quantity_precision: Optional[int] = None
    min_quantity: Optional[Decimal] = None
    min_notional: Optional[Decimal] = None
    venue: Optional[str] = None

    @property
    def instrument_id(self) -> str:
        """Stable internal identifier.

        Equities: MARKET:SYMBOL. Crypto: MARKET:VENUE:SYMBOL, because the same
        pair quoted on two venues is two instruments and must never share an id.
        """
        if self.venue:
            return f"{self.market.value}:{self.venue}:{self.symbol}"
        return f"{self.market.value}:{self.symbol}"

    @property
    def quote_asset(self) -> str:
        """Asset the price is denominated in (the pair's second leg)."""
        return self.currency.value

    @property
    def lot_size_verified(self) -> bool:
        return self.lot_size is not None

    @classmethod
    def create_us(cls, symbol: str, name: str, asset_type: AssetType,
                  exchange: Exchange = Exchange.NYSE) -> "Instrument":
        """Create a US instrument. US stocks trade in lots of 1."""
        return cls(
            market=Market.US,
            symbol=symbol.upper(),
            name=name,
            asset_type=asset_type,
            exchange=exchange,
            currency=Currency.USD,
            timezone=US_MARKET_STRUCTURE.timezone,
            lot_size=1,
        )

    @classmethod
    def create_crypto_pair(
        cls,
        *,
        symbol: str,
        name: str,
        base_asset: str,
        quote: Currency,
        exchange: Exchange,
        price_precision: Optional[int] = None,
        quantity_precision: Optional[int] = None,
        min_quantity: Optional[Decimal] = None,
        min_notional: Optional[Decimal] = None,
    ) -> "Instrument":
        """Create a spot pair on one venue.

        The symbol must spell the pair as BASE-QUOTE with the declared quote
        currency. That check is what stops a USDT pair from being handed a USD
        label: USDT is not a Currency here, so "BTC-USDT" cannot be built until
        someone adds it deliberately.
        """
        base = (base_asset or "").strip().upper()
        if not base or not base.isalnum():
            raise ValueError(f"base_asset must be an alphanumeric asset code, got {base_asset!r}")

        expected = f"{base}-{quote.value}"
        if symbol.strip().upper() != expected:
            raise ValueError(
                f"Crypto symbol {symbol!r} does not match BASE-QUOTE for the declared "
                f"currency; expected {expected!r}. A different quote asset needs its own "
                "Currency entry, not a relabel."
            )

        if exchange not in (Exchange.KRAKEN,):
            raise ValueError(f"{exchange.value} is not a declared crypto spot venue")

        for field_name, value in (
            ("price_precision", price_precision),
            ("quantity_precision", quantity_precision),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative int, got {value!r}")

        return cls(
            market=Market.CRYPTO,
            symbol=expected,
            name=name,
            asset_type=AssetType.CRYPTO_PAIR,
            exchange=exchange,
            currency=quote,
            timezone=CRYPTO_MARKET_STRUCTURE.timezone,
            lot_size=None,
            base_asset=base,
            price_precision=price_precision,
            quantity_precision=quantity_precision,
            min_quantity=min_quantity,
            min_notional=min_notional,
            venue=exchange.value,
        )

    @classmethod
    def create_hk(cls, symbol: str, name: str, asset_type: AssetType,
                  exchange: Exchange = Exchange.HKEX,
                  lot_size: Optional[int] = None) -> "Instrument":
        """Create an HK instrument.

        HK symbols are typically 4-digit codes (e.g., "0700" for Tencent).
        Board lot size varies per stock — pass lot_size if known from HKEX,
        otherwise it stays None (unverified). Never assume a universal default.
        """
        return cls(
            market=Market.HK,
            symbol=symbol.zfill(4),
            name=name,
            asset_type=asset_type,
            exchange=exchange,
            currency=Currency.HKD,
            timezone=HK_MARKET_STRUCTURE.timezone,
            lot_size=lot_size,
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
    elif market == Market.CRYPTO:
        return CRYPTO_MARKET_STRUCTURE
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


def parse_crypto_instrument_id(instrument_id: str) -> tuple[Exchange, str, str]:
    """Parse CRYPTO:VENUE:BASE-QUOTE into (exchange, base_asset, quote_asset).

    parse_instrument_id keeps its two-part contract for equities. Crypto ids
    carry the venue as a third part, so reading them with that function would
    return "KRAKEN:BTC-USD" as the symbol and silently drop the venue.
    """
    parts = instrument_id.split(":")
    if len(parts) != 3 or parts[0] != Market.CRYPTO.value:
        raise ValueError(
            f"Not a crypto instrument id (expected CRYPTO:VENUE:BASE-QUOTE): {instrument_id!r}"
        )

    try:
        exchange = Exchange(parts[1])
    except ValueError:
        raise ValueError(f"Unknown crypto venue: {parts[1]!r}")

    base, separator, quote = parts[2].partition("-")
    if not separator or not base or not quote:
        raise ValueError(f"Crypto instrument id must spell BASE-QUOTE: {instrument_id!r}")

    return exchange, base, quote
