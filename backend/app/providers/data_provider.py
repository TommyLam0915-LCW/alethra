from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Dict, Any, Tuple, Union
from app.providers.instruments import Instrument
from app.lib.timeutil import utcnow

STALE_THRESHOLD_DAYS = 7


@dataclass(frozen=True)
class OHLCVBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


# Interval labels this stack can represent as sub-daily bars, and their
# length in minutes. ``1h`` is deliberately absent: an hourly equity bar does
# not open on the epoch hour grid the series integrity check assumes, so it
# would be reported as off-grid rather than served.
INTRADAY_INTERVALS: Dict[str, int] = {
    "1min": 1,
    "5min": 5,
    "15min": 15,
    "30min": 30,
}


def intraday_interval_minutes(interval: str) -> int:
    try:
        return INTRADAY_INTERVALS[interval]
    except KeyError:
        raise ValueError(
            f"Unsupported intraday interval '{interval}'. Supported: "
            f"{', '.join(INTRADAY_INTERVALS)}"
        ) from None


@dataclass(frozen=True)
class IntradayBar:
    """One sub-daily row exactly as the provider timed it.

    ``timestamp`` is the bar's open time in UTC (naive, to match storage).
    Providers must request UTC from upstream so the open time is unambiguous;
    whether the bucket is still forming is decided downstream from the wall
    clock, not trusted from the provider.
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


class ProviderRateLimitError(Exception):
    """Upstream refused because a quota window is exhausted.

    ``retry_after_seconds`` is what the provider asked for, or None when it
    supplied no hint. Callers must honour it; a rate limit is not a transient
    error to hammer through.
    """

    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def compute_staleness(
    latest_bar_date: Optional[str],
    today: Optional[date] = None,
    threshold_days: int = STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Calendar-day staleness of the most recent bar of a series.

    is_stale is True when there is no bar at all or the newest bar is
    older than threshold_days (weekends/holidays make a 2-3 day age normal
    for daily bars, so 7 avoids false alarms).
    """
    result: Dict[str, Any] = {
        "latest_bar_date": latest_bar_date,
        "bar_age_days": None,
        "is_stale": True,
        "stale_threshold_days": threshold_days,
    }
    if not latest_bar_date:
        return result
    if today is None:
        today = utcnow().date()
    try:
        bar_date = date.fromisoformat(latest_bar_date)
    except ValueError:
        return result
    age = (today - bar_date).days
    result["bar_age_days"] = age
    result["is_stale"] = age > threshold_days
    return result


@dataclass(frozen=True)
class AssetInfo:
    """Asset information with provider-independent instrument identity.
    
    Attributes:
        symbol: Legacy symbol string (for backward compatibility)
        name: Asset name
        description: Asset description
        asset_type: Asset type (e.g., "stock", "etf", "synthetic")
        currency: Trading currency (default "USD")
        exchange: Exchange name (optional)
        instrument: Provider-independent instrument identity (optional)
    """
    symbol: str
    name: str
    description: str
    asset_type: str
    currency: str = "USD"
    exchange: Optional[str] = None
    instrument: Optional[Instrument] = None
    
    @classmethod
    def from_instrument(cls, instrument: Instrument, description: str = "") -> "AssetInfo":
        """Create AssetInfo from an Instrument."""
        return cls(
            symbol=instrument.symbol,
            name=instrument.name,
            description=description or instrument.name,
            asset_type=instrument.asset_type.value.lower(),
            currency=instrument.currency.value,
            exchange=instrument.exchange.value,
            instrument=instrument,
        )


class DataProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def feed_mode(self) -> str:
        pass

    @abstractmethod
    def list_assets(self) -> List[AssetInfo]:
        pass

    @abstractmethod
    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        pass

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        pass

    @abstractmethod
    def get_date_range(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        pass

    def is_available(self) -> bool:
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "feed_mode": self.feed_mode,
            "available": self.is_available(),
        }

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        """Provenance metadata for bars this provider serves for a symbol.

        Keys:
        - data_nature: "synthetic" | "live" | "delayed" | "unknown"
        - adjustment: "unadjusted" | "adjusted" | "not_applicable" | "unknown"
          (only claimed when the provider documents it; otherwise "unknown")
        - timezone: IANA timezone of the bar timestamps, or None if unverified
        - note / note_zh: optional human-readable caveat (en / zh-Hant)
        """
        return {
            "data_nature": "unknown",
            "adjustment": "unknown",
            "timezone": None,
            "note": None,
            "note_zh": None,
        }

    def resolve_instrument(self, instrument_or_symbol: Union[Instrument, str]) -> str:
        """Resolve an Instrument or symbol string to the provider's native symbol format.
        
        Default implementation returns the symbol as-is for backward compatibility.
        Providers with instrument mappings should override this.
        """
        if isinstance(instrument_or_symbol, Instrument):
            return instrument_or_symbol.symbol
        return instrument_or_symbol

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        """Get asset info by instrument ID (e.g., 'US:AAPL').
        
        Default implementation searches list_assets() for a matching instrument.
        Providers with efficient lookups should override this.
        """
        for asset in self.list_assets():
            if asset.instrument and asset.instrument.instrument_id == instrument_id:
                return asset
        return None

    def supports_instrument_id(self) -> bool:
        """Whether this provider supports instrument_id-based queries."""
        return any(asset.instrument is not None for asset in self.list_assets())

    def get_available_symbols(self) -> List[str]:
        """Return all symbol strings this provider can serve data for.

        Default implementation extracts symbols from list_assets().
        For providers with instrument_id support, returns instrument_id format.
        """
        symbols = []
        for asset in self.list_assets():
            if asset.instrument:
                symbols.append(asset.instrument.instrument_id)
            else:
                symbols.append(asset.symbol)
        return symbols

    def get_available_range(self, instrument_id: str) -> Optional[Dict[str, str]]:
        """Oldest and newest date this provider can serve bars for, as YYYY-MM-DD.

        Returns None when the provider cannot state the window per instrument —
        callers must then treat availability as unknown rather than infer it
        from what happens to be cached.
        """
        return None

    def supports_intraday(self) -> bool:
        """Whether this adapter has a sub-daily request path at all.

        Adapter capability only. It says nothing about whether the configured
        account may call it, which market/interval is entitled, or whether a
        response actually arrived. Those are verified separately and never
        inferred from this flag.
        """
        return False

    def declared_intraday_intervals(self) -> Tuple[str, ...]:
        """Interval labels the adapter knows how to request, not verified ones."""
        return ()

    def get_intraday_bars(
        self,
        symbol: str,
        interval: str,
        *,
        outputsize: int = 390,
    ) -> List[IntradayBar]:
        """Fetch sub-daily bars, ascending by open time, timestamps in UTC.

        Implementations must raise ``ProviderRateLimitError`` (with the
        provider's retry hint when it gives one) instead of retrying through a
        quota window, and must raise rather than return an empty list when the
        provider itself failed.
        """
        raise NotImplementedError(
            f"{self.provider_id} does not implement intraday bars"
        )
