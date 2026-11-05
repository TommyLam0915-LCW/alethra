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
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
import zoneinfo


class Market(str, enum.Enum):
    """Supported markets."""
    US = "US"
    HK = "HK"
    CRYPTO = "CRYPTO"
    FOREX = "FOREX"


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
    
    # Forex (OTC market, no central exchange)
    FOREX = "FOREX"


class Currency(str, enum.Enum):
    """Supported currencies."""
    USD = "USD"
    HKD = "HKD"
    JPY = "JPY"
    EUR = "EUR"
    GBP = "GBP"
    BTC = "BTC"
    ETH = "ETH"


class AssetType(str, enum.Enum):
    """Supported asset types."""
    STOCK = "STOCK"
    ETF = "ETF"
    CRYPTO_PAIR = "CRYPTO_PAIR"
    FOREX_PAIR = "FOREX_PAIR"
    # Future: WARRANT, RIGHT, BOND, etc.


# The single exchange-calendar source. Data generation, market-status views and
# freshness checks all read this table; nothing else may keep its own copy, or
# two answers to "was this a trading day" drift apart.
#
# NYSE/NASDAQ full-day closures. Early-close days (for example the Friday after
# Thanksgiving, or Christmas Eve) are open sessions and are deliberately absent:
# treating them as holidays under-counts real bars.
US_MARKET_HOLIDAYS: Set[date] = {
    # 2023
    date(2023, 1, 2),   # New Year's Day observed (Jan 1 was a Sunday)
    date(2023, 1, 16),  # Martin Luther King Jr. Day
    date(2023, 2, 20),  # Washington's Birthday
    date(2023, 4, 7),   # Good Friday
    date(2023, 5, 29),  # Memorial Day
    date(2023, 6, 19),  # Juneteenth
    date(2023, 7, 4),   # Independence Day
    date(2023, 9, 4),   # Labor Day
    date(2023, 11, 23), # Thanksgiving
    date(2023, 12, 25), # Christmas
    # 2024
    date(2024, 1, 1),   # New Year's Day
    date(2024, 1, 15),  # Martin Luther King Jr. Day
    date(2024, 2, 19),  # Washington's Birthday
    date(2024, 3, 29),  # Good Friday
    date(2024, 5, 27),  # Memorial Day
    date(2024, 6, 19),  # Juneteenth
    date(2024, 7, 4),   # Independence Day
    date(2024, 9, 2),   # Labor Day
    date(2024, 11, 28), # Thanksgiving
    date(2024, 12, 25), # Christmas
    # 2025
    date(2025, 1, 1),   # New Year's Day
    date(2025, 1, 9),   # National Day of Mourning (President Carter)
    date(2025, 1, 20),  # Martin Luther King Jr. Day
    date(2025, 2, 17),  # Washington's Birthday
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 26),  # Memorial Day
    date(2025, 6, 19),  # Juneteenth
    date(2025, 7, 4),   # Independence Day
    date(2025, 9, 1),   # Labor Day
    date(2025, 11, 27), # Thanksgiving
    date(2025, 12, 25), # Christmas
}

US_CALENDAR_COVERAGE: Tuple[date, date] = (date(2023, 1, 1), date(2025, 12, 31))

# HKEX full-day closures. These are the dates the platform already published;
# they have not been independently re-verified against the HKEX notice, so the
# coverage window is stated instead of implied.
HK_MARKET_HOLIDAYS: Set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 2, 12), date(2024, 2, 13),
    date(2024, 2, 14), date(2024, 3, 29), date(2024, 4, 1),
    date(2024, 4, 4), date(2024, 4, 5), date(2024, 5, 1),
    date(2024, 5, 15), date(2024, 6, 10), date(2024, 7, 1),
    date(2024, 9, 18), date(2024, 10, 1), date(2024, 10, 11),
    date(2024, 12, 25), date(2024, 12, 26),
    # 2025
    date(2025, 1, 1), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 1, 31), date(2025, 4, 18), date(2025, 4, 19),
    date(2025, 4, 21), date(2025, 5, 1), date(2025, 5, 5),
    date(2025, 5, 31), date(2025, 7, 1), date(2025, 10, 1),
    date(2025, 10, 7), date(2025, 12, 25), date(2025, 12, 26),
}

HK_CALENDAR_COVERAGE: Tuple[date, date] = (date(2024, 1, 1), date(2025, 12, 31))


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
        calendar_coverage: First and last date the holidays set covers. Outside
            it the calendar is unknown, not "open".
    """
    market: Market
    exchange: Exchange
    currency: Currency
    timezone: str
    sessions: List[TradingSession]
    holidays: Set[date] = field(default_factory=set)
    lot_size: int = 1
    always_open: bool = False
    calendar_coverage: Optional[Tuple[date, date]] = None

    def calendar_covers(self, d: date) -> bool:
        """Whether the holiday table is authoritative for this date."""
        if self.always_open:
            return True
        if self.calendar_coverage is None:
            return False
        start, end = self.calendar_coverage
        return start <= d <= end

    def trading_day_status(self, d: date) -> str:
        """One of 'open', 'weekend', 'holiday', 'calendar_unknown'."""
        if self.always_open:
            return "open"
        if d.weekday() >= 5:
            return "weekend"
        if d in self.holidays:
            return "holiday"
        if not self.calendar_covers(d):
            return "calendar_unknown"
        return "open"

    def is_trading_day(self, d: date) -> bool:
        """Check if a date is a known non-closed weekday.

        Returns True for dates outside the calendar coverage because the
        platform has no evidence they were closed; ``trading_day_status`` is
        the honest query when "unknown" must not read as "open".
        """
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
    holidays=US_MARKET_HOLIDAYS,
    lot_size=1,
    calendar_coverage=US_CALENDAR_COVERAGE,
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
    holidays=HK_MARKET_HOLIDAYS,
    lot_size=100,  # HK stocks typically trade in board lots of 100 shares
    calendar_coverage=HK_CALENDAR_COVERAGE,
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


def trading_days_between(market: Market, start: date, end: date) -> List[date]:
    """Weekdays the calendar does not know to be closed, both ends inclusive.

    Inside the declared coverage a weekday that is a listed holiday is
    skipped. Outside it only weekends are removed: an unverified date is not
    evidence of a closure either, so it is not silently dropped.
    ``trading_day_status`` tells the caller which days are unverified.
    """
    structure = get_market_structure(market)
    days: List[date] = []
    d = start
    while d <= end:
        if structure.trading_day_status(d) in ("open", "calendar_unknown"):
            days.append(d)
        d += timedelta(days=1)
    return days


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
