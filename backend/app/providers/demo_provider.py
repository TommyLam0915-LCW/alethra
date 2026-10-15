import hashlib
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.data.demo_data import (
    DEMO_ASSETS,
    DEMO_DATA,
    TRADE_DATES,
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
