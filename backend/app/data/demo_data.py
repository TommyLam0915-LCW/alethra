import hashlib
import json
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional


DEMO_ASSETS = {
    "DEMO-ALPHA": {
        "description": "Demo equity A — synthetic price series with decline and recovery",
        "start_price": 50.0,
    },
    "DEMO-BETA": {
        "description": "Demo equity B — synthetic price series with crossover pattern",
        "start_price": 100.0,
    },
    "DEMO-BENCHMARK": {
        "description": "Demo benchmark index — synthetic steady series",
        "start_price": 200.0,
    },
}

TRADE_DATES = [
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
    "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
    "2024-01-22", "2024-01-23", "2024-01-24", "2024-01-25", "2024-01-26",
    "2024-01-29", "2024-01-30", "2024-01-31",
    "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06", "2024-02-07",
    "2024-02-08", "2024-02-09", "2024-02-12", "2024-02-13",
]

# DEMO-ALPHA: rise → sharp drop → recovery
# Tests: price_change_fixed_hold (buy the dip), buy_and_hold
# Gap: 2024-01-15 (MLK day) is excluded — missing day scenario
_ALPHA_CLOSE = [
    50.0, 51.0, 52.0, 53.0,       # Jan 02-05: steady rise
    53.5, 54.0, 54.5, 55.0, 54.0,  # Jan 08-12: peak then start drop
    # Jan 15 missing (gap)
    51.0, 48.0, 46.0, 45.0,        # Jan 16-19: sharp decline (>10% from peak)
    46.0, 47.5, 49.0, 50.0, 51.0,  # Jan 22-26: recovery begins
    52.0, 51.5, 52.0,              # Jan 29-31: consolidation
    53.0, 52.5, 53.0, 54.0, 53.5,  # Feb 01-07: gradual rise
    54.0, 53.5, 54.0, 54.5,              # Feb 08-13: consolidation
]

_ALPHA_OPEN = [
    50.0, 50.0, 51.0, 52.0,
    53.0, 53.5, 54.0, 54.5, 55.0,
    51.5, 49.0, 47.0, 45.5,
    45.0, 46.5, 48.0, 49.5, 50.5,
    51.5, 52.0, 51.5,
    52.0, 53.0, 52.5, 53.0, 53.5,
    54.0, 54.0, 53.5, 54.0,
]

_ALPHA_HIGH = [
    50.5, 51.5, 52.5, 53.5,
    54.0, 54.5, 55.0, 55.5, 55.0,
    52.0, 49.5, 47.5, 46.0,
    46.5, 48.0, 49.5, 50.5, 51.5,
    52.5, 52.5, 52.5,
    53.5, 53.5, 53.5, 54.5, 54.0,
    54.5, 54.5, 54.5, 55.0,
]

_ALPHA_LOW = [
    49.5, 50.0, 51.0, 52.0,
    52.5, 53.0, 53.5, 54.0, 53.5,
    50.5, 48.0, 46.0, 44.5,
    44.5, 46.0, 47.5, 49.0, 50.0,
    51.0, 51.0, 51.0,
    51.5, 52.0, 52.0, 52.5, 53.0,
    53.5, 53.0, 53.0, 53.5,
]

# DEMO-BETA: decline → rise (SMA cross-up) → decline (SMA cross-down)
# Tests: sma_crossover, buy_and_hold
_BETA_CLOSE = [
    100.0, 99.0, 98.0, 97.0,       # Jan 02-05: decline
    96.5, 96.0, 95.5, 95.0, 94.5,  # Jan 08-12: continued decline
    95.0, 96.0, 97.5, 99.0,        # Jan 16-19: reversal starts
    100.5, 102.0, 103.5, 105.0, 106.0,  # Jan 22-26: strong rise (cross-up ~day 18)
    107.0, 107.5, 108.0,           # Jan 29-31: peak area
    107.0, 106.0, 104.5, 103.0, 102.0,  # Feb 01-07: decline (cross-down ~day 25)
    101.0, 100.5, 100.0, 99.5,           # Feb 08-13: continued decline
]

_BETA_OPEN = [
    100.0, 100.0, 99.0, 98.0,
    97.0, 96.5, 96.0, 95.5, 95.0,
    94.5, 95.5, 96.5, 98.0,
    99.5, 101.0, 102.5, 104.0, 105.5,
    106.5, 107.0, 108.0,
    107.5, 107.0, 105.5, 104.0, 103.0,
    102.0, 101.0, 100.5, 100.0,
]

_BETA_HIGH = [
    100.5, 100.5, 99.5, 98.5,
    97.5, 97.0, 96.5, 96.0, 95.5,
    95.5, 96.5, 98.0, 99.5,
    100.5, 102.0, 103.5, 105.0, 106.5,
    107.5, 108.0, 108.5,
    108.0, 107.5, 106.0, 104.5, 103.5,
    102.5, 101.5, 101.0, 100.5,
]

_BETA_LOW = [
    99.5, 98.5, 97.5, 96.5,
    96.0, 95.5, 95.0, 94.5, 94.0,
    94.0, 95.0, 96.0, 97.0,
    99.0, 100.5, 102.0, 103.5, 105.0,
    106.0, 107.0, 107.0,
    106.5, 106.0, 104.5, 103.0, 102.0,
    101.0, 100.0, 99.5, 99.0,
]

# DEMO-BENCHMARK: steady moderate rise
_BENCHMARK_CLOSE = [
    200.0, 200.5, 201.0, 201.5,
    202.0, 202.5, 203.0, 203.0, 203.5,
    204.0, 204.5, 205.0, 205.5,
    206.0, 206.5, 207.0, 207.0, 207.5,
    208.0, 208.0, 208.5,
    209.0, 209.0, 209.5, 210.0, 210.0,
    210.5, 210.5, 211.0, 211.0,
]

_BENCHMARK_OPEN = [
    200.0, 200.0, 200.5, 201.0,
    201.5, 202.0, 202.5, 203.0, 203.0,
    203.5, 204.0, 204.5, 205.0,
    205.5, 206.0, 206.5, 207.0, 207.0,
    207.5, 208.0, 208.0,
    208.5, 209.0, 209.0, 209.5, 210.0,
    210.0, 210.5, 210.5, 211.0,
]

_BENCHMARK_HIGH = [
    200.5, 201.0, 201.5, 202.0,
    202.5, 203.0, 203.5, 203.5, 204.0,
    204.5, 205.0, 205.5, 206.0,
    206.5, 207.0, 207.5, 207.5, 208.0,
    208.5, 208.5, 209.0,
    209.5, 209.5, 210.0, 210.5, 210.5,
    211.0, 211.0, 211.5, 211.5,
]

_BENCHMARK_LOW = [
    199.5, 200.0, 200.5, 201.0,
    201.5, 202.0, 202.5, 202.5, 203.0,
    203.5, 204.0, 204.5, 205.0,
    205.5, 206.0, 206.5, 206.5, 207.0,
    207.5, 207.5, 208.0,
    208.5, 208.5, 209.0, 209.5, 209.5,
    210.0, 210.0, 210.5, 210.5,
]


def _build_ohlcv(symbol: str, opens, highs, lows, closes, skip_dates=None) -> Dict[str, Dict[str, float]]:
    skip = set(skip_dates or [])
    data = {}
    for i, date_str in enumerate(TRADE_DATES):
        if i >= len(closes):
            break
        if date_str in skip:
            continue
        data[date_str] = {
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
        }
    return data


DEMO_DATA: Dict[str, Dict[str, Dict[str, float]]] = {
    "DEMO-ALPHA": _build_ohlcv("DEMO-ALPHA", _ALPHA_OPEN, _ALPHA_HIGH, _ALPHA_LOW, _ALPHA_CLOSE, skip_dates=["2024-01-18"]),
    "DEMO-BETA": _build_ohlcv("DEMO-BETA", _BETA_OPEN, _BETA_HIGH, _BETA_LOW, _BETA_CLOSE),
    "DEMO-BENCHMARK": _build_ohlcv("DEMO-BENCHMARK", _BENCHMARK_OPEN, _BENCHMARK_HIGH, _BENCHMARK_LOW, _BENCHMARK_CLOSE),
}


def get_demo_assets() -> List[Dict[str, str]]:
    return [
        {"symbol": symbol, "description": info["description"]}
        for symbol, info in DEMO_ASSETS.items()
    ]


def get_available_assets() -> List[str]:
    return list(DEMO_DATA.keys())


def get_trade_dates() -> List[str]:
    return list(TRADE_DATES)


def get_date_range() -> dict:
    return {
        "min": TRADE_DATES[0],
        "max": TRADE_DATES[-1],
        "total_trading_days": len(TRADE_DATES),
    }


def get_demo_prices(
    asset_ids: List[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    all_data = {}
    for asset_id in asset_ids:
        if asset_id not in DEMO_DATA:
            continue
        asset_prices = {}
        for date_str, prices in DEMO_DATA[asset_id].items():
            date = datetime.strptime(date_str, "%Y-%m-%d")
            if start <= date <= end:
                asset_prices[date] = prices["close"]
        if asset_prices:
            all_data[asset_id] = asset_prices

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    df = df.sort_index()
    df.index.name = "date"
    return df


def get_demo_ohlcv(
    asset_ids: List[str],
    start_date: str,
    end_date: str,
) -> Dict[str, pd.DataFrame]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    result = {}
    for asset_id in asset_ids:
        if asset_id not in DEMO_DATA:
            continue
        rows = []
        for date_str, prices in DEMO_DATA[asset_id].items():
            date = datetime.strptime(date_str, "%Y-%m-%d")
            if start <= date <= end:
                rows.append({
                    "date": date,
                    "open": prices["open"],
                    "high": prices["high"],
                    "low": prices["low"],
                    "close": prices["close"],
                })
        if rows:
            df = pd.DataFrame(rows)
            df = df.sort_values("date").reset_index(drop=True)
            result[asset_id] = df
    return result


def compute_dataset_checksum(asset_ids: List[str], start_date: str, end_date: str) -> str:
    ohlcv = get_demo_ohlcv(asset_ids, start_date, end_date)
    serialized = {}
    for symbol in sorted(ohlcv.keys()):
        df = ohlcv[symbol]
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
            })
        serialized[symbol] = rows
    raw = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
