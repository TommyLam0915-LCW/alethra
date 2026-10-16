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


def _get_real_instruments():
    from app.data.real_instruments import ALL_REAL_INSTRUMENTS
    return ALL_REAL_INSTRUMENTS


def _get_real_synthetic_data():
    from app.data import real_synthetic_data
    return real_synthetic_data


def _get_provider_mapping(provider_id, instrument_id):
    from app.data.real_instruments import get_provider_mapping
    return get_provider_mapping(provider_id, instrument_id)


def _get_instrument_by_id(instrument_id):
    from app.data.real_instruments import get_instrument_by_id
    return get_instrument_by_id(instrument_id)


def _get_real_instrument_ids():
    return {i.instrument_id for i in _get_real_instruments()}


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
        assets.extend(
            AssetInfo.from_instrument(inst, description=f"{inst.name} (synthetic demo data)")
            for inst in _get_real_instruments()
        )
        return assets

    def list_demo_assets(self) -> List[AssetInfo]:
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
        return [
            AssetInfo.from_instrument(inst, description=f"{inst.name} (synthetic demo data)")
            for inst in _get_real_instruments()
        ]

    def _is_real_instrument(self, symbol: str) -> bool:
        return ":" in symbol and symbol in _get_real_instrument_ids()

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        if self._is_real_instrument(symbol):
            syn = _get_real_synthetic_data()
            raw = syn.get_synthetic_bars(symbol, start_date, end_date)
            return [
                OHLCVBar(
                    date=b["date"],
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    volume=b["volume"],
                )
                for b in raw
            ]

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
        if self._is_real_instrument(symbol):
            syn = _get_real_synthetic_data()
            date_range = syn.get_real_date_range()
            bars = syn.get_synthetic_bars(symbol, date_range["min"], date_range["max"])
            if not bars:
                return None
            last = bars[-1]
            return OHLCVBar(
                date=last["date"],
                open=last["open"],
                high=last["high"],
                low=last["low"],
                close=last["close"],
                volume=last["volume"],
            )

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
        syn = _get_real_synthetic_data()
        real_range = syn.get_real_date_range()
        return {
            "min": TRADE_DATES[0],
            "max": TRADE_DATES[-1],
            "total_trading_days": len(TRADE_DATES),
            "real_instruments_range": real_range,
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        serialized = {}
        for symbol in sorted(symbols):
            if self._is_real_instrument(symbol):
                syn = _get_real_synthetic_data()
                raw = syn.get_synthetic_bars(symbol, start_date, end_date)
                serialized[symbol] = [
                    {"date": b["date"], "open": b["open"], "high": b["high"],
                     "low": b["low"], "close": b["close"]}
                    for b in raw
                ]
                continue

            if symbol not in DEMO_DATA:
                continue

            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
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
        if isinstance(instrument_or_symbol, Instrument):
            return instrument_or_symbol.symbol
        return instrument_or_symbol

    def get_provider_symbol_mapping(
        self, provider_id: str, instrument_id: str
    ) -> Optional[Dict[str, Any]]:
        mapping = _get_provider_mapping(provider_id, instrument_id)
        if mapping is None:
            return None
        return {
            "provider_id": mapping.provider_id,
            "instrument_id": mapping.instrument_id,
            "provider_symbol": mapping.provider_symbol,
            "provider_exchange": mapping.provider_exchange,
            "metadata": mapping.metadata,
        }

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        inst = _get_instrument_by_id(instrument_id)
        if inst is None:
            return None
        return AssetInfo.from_instrument(inst, description=f"{inst.name} (synthetic demo data)")

    def supports_instrument_id(self) -> bool:
        return True

    def get_available_symbols(self) -> List[str]:
        demo_symbols = list(DEMO_ASSETS.keys())
        real_symbols = [inst.instrument_id for inst in _get_real_instruments()]
        return demo_symbols + real_symbols

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        instruments = _get_real_instruments()
        syn = _get_real_synthetic_data()
        status["real_instruments_count"] = len(instruments)
        status["demo_assets_count"] = len(DEMO_ASSETS)
        status["data_is_synthetic"] = True
        status["real_instruments_date_range"] = syn.get_real_date_range()
        return status
