import hashlib
import json
from datetime import datetime
from typing import List, Optional, Dict, Any, Union

from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.providers.instruments import Instrument
from app.data.demo_data import (
    DEMO_ASSETS,
    DEMO_DATA,
    TRADE_DATES,
)
from app.data.real_instruments import (
    ALL_REAL_INSTRUMENTS,
    get_provider_mapping,
)


class DemoDataProvider(DataProvider):
    @property
    def provider_id(self) -> str:
        return "demo"

    @property
    def provider_name(self) -> str:
        return "Demo (Synthetic)"

    @property
    def feed_mode(self) -> str:
        return "offline"

    def list_assets(self) -> List[AssetInfo]:
        """List all assets: synthetic demo assets plus real US/HK instruments."""
        assets = [
            AssetInfo(
                symbol=symbol,
                name=symbol,
                description=info["description"],
                asset_type="synthetic",
                currency="USD",
                exchange=None,
            )
            for symbol, info in DEMO_ASSETS.items()
        ]
        # Add real instruments
        assets.extend(
            AssetInfo.from_instrument(inst, description=f"{inst.name} (demo catalog)")
            for inst in ALL_REAL_INSTRUMENTS
        )
        return assets

    def list_demo_assets(self) -> List[AssetInfo]:
        """List only synthetic demo assets (backward compatible)."""
        return [
            AssetInfo(
                symbol=symbol,
                name=symbol,
                description=info["description"],
                asset_type="synthetic",
                currency="USD",
                exchange=None,
            )
            for symbol, info in DEMO_ASSETS.items()
        ]

    def list_real_instruments(self) -> List[AssetInfo]:
        """List only real US/HK instruments (no price data in demo mode)."""
        return [
            AssetInfo.from_instrument(inst, description=f"{inst.name} (demo catalog)")
            for inst in ALL_REAL_INSTRUMENTS
        ]

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        if symbol not in DEMO_DATA:
            return []

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        bars = []
        for date_str, prices in DEMO_DATA[symbol].items():
            date = datetime.strptime(date_str, "%Y-%m-%d")
            if start <= date <= end:
                bars.append(
                    OHLCVBar(
                        date=date_str,
                        open=prices["open"],
                        high=prices["high"],
                        low=prices["low"],
                        close=prices["close"],
                        volume=None,
                    )
                )

        bars.sort(key=lambda b: b.date)
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        if symbol not in DEMO_DATA or not DEMO_DATA[symbol]:
            return None

        latest_date = max(DEMO_DATA[symbol].keys())
        prices = DEMO_DATA[symbol][latest_date]
        return OHLCVBar(
            date=latest_date,
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            volume=None,
        )

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": TRADE_DATES[0],
            "max": TRADE_DATES[-1],
            "total_trading_days": len(TRADE_DATES),
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        serialized = {}
        for symbol in sorted(symbols):
            if symbol not in DEMO_DATA:
                continue
            rows = []
            for date_str, prices in DEMO_DATA[symbol].items():
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if start <= date <= end:
                    rows.append({
                        "date": date_str,
                        "open": prices["open"],
                        "high": prices["high"],
                        "low": prices["low"],
                        "close": prices["close"],
                    })
            rows.sort(key=lambda r: r["date"])
            serialized[symbol] = rows

        raw = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def resolve_instrument(self, instrument_or_symbol: Union[Instrument, str]) -> str:
        """Resolve to provider-native symbol (demo uses bare symbol)."""
        if isinstance(instrument_or_symbol, Instrument):
            return instrument_or_symbol.symbol
        return instrument_or_symbol

    def get_provider_symbol_mapping(
        self, provider_id: str, instrument_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get provider-specific symbol mapping for an instrument.
        
        Returns None if no mapping exists for the given provider/instrument.
        """
        mapping = get_provider_mapping(provider_id, instrument_id)
        if mapping is None:
            return None
        return {
            "provider_id": mapping.provider_id,
            "instrument_id": mapping.instrument_id,
            "provider_symbol": mapping.provider_symbol,
            "provider_exchange": mapping.provider_exchange,
            "metadata": mapping.metadata,
        }

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        status["real_instruments_count"] = len(ALL_REAL_INSTRUMENTS)
        status["demo_assets_count"] = len(DEMO_ASSETS)
        return status
