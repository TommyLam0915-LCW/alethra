from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any


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
    symbol: str
    name: str
    description: str
    asset_type: str
    currency: str = "USD"
    exchange: Optional[str] = None


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
