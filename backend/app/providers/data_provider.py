from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from app.providers.instruments import Instrument


@dataclass(frozen=True)
class OHLCVBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


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
