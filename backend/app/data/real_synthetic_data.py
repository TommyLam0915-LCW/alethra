"""Deterministic synthetic OHLCV data for real instruments.

Generates realistic-looking price series using seeded random walks.
Each instrument has a unique seed derived from its instrument_id, so data
is reproducible and consistent across calls.

This module exists so the full research/backtest pipeline can be exercised
with real instrument symbols (US:AAPL, HK:0700, etc.) even without live
market data credentials. All data is clearly marked as synthetic.
"""

import hashlib
from datetime import date, timedelta
from typing import Dict, List, Optional

from app.providers.instruments import Market, parse_instrument_id


_BASE_PRICES: Dict[str, float] = {
    "US:AAPL": 185.0,
    "US:MSFT": 420.0,
    "US:GOOGL": 155.0,
    "US:AMZN": 185.0,
    "US:NVDA": 130.0,
    "US:META": 500.0,
    "US:TSLA": 245.0,
    "US:SPY": 520.0,
    "US:QQQ": 440.0,
    "US:VTI": 260.0,
    "HK:0700": 370.0,
    "HK:9988": 82.0,
    "HK:0005": 62.0,
    "HK:2318": 42.0,
    "HK:0941": 68.0,
    "HK:1299": 60.0,
    "HK:3690": 130.0,
    "HK:2800": 22.0,
    "HK:3067": 10.5,
}

_DAILY_VOL: Dict[str, float] = {
    "US:AAPL": 0.015,
    "US:MSFT": 0.014,
    "US:GOOGL": 0.016,
    "US:AMZN": 0.018,
    "US:NVDA": 0.025,
    "US:META": 0.020,
    "US:TSLA": 0.030,
    "US:SPY": 0.010,
    "US:QQQ": 0.012,
    "US:VTI": 0.010,
    "HK:0700": 0.020,
    "HK:9988": 0.022,
    "HK:0005": 0.014,
    "HK:2318": 0.018,
    "HK:0941": 0.012,
    "HK:1299": 0.016,
    "HK:3690": 0.024,
    "HK:2800": 0.010,
    "HK:3067": 0.018,
}

_US_HOLIDAYS_2024_2025 = {
    date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4),
    date(2024, 9, 2), date(2024, 11, 28), date(2024, 11, 29),
    date(2024, 12, 25),
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
    date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
    date(2025, 11, 28), date(2025, 12, 25),
}

_HK_HOLIDAYS_2024_2025 = {
    date(2024, 1, 1), date(2024, 2, 12), date(2024, 2, 13),
    date(2024, 2, 14), date(2024, 3, 29), date(2024, 4, 1),
    date(2024, 4, 4), date(2024, 4, 5), date(2024, 5, 1),
    date(2024, 5, 15), date(2024, 6, 10), date(2024, 7, 1),
    date(2024, 9, 18), date(2024, 10, 1), date(2024, 10, 11),
    date(2024, 12, 25), date(2024, 12, 26),
    date(2025, 1, 1), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 1, 31), date(2025, 4, 18), date(2025, 4, 19),
    date(2025, 4, 21), date(2025, 5, 1), date(2025, 5, 5),
    date(2025, 5, 31), date(2025, 7, 1), date(2025, 10, 1),
    date(2025, 10, 7), date(2025, 12, 25), date(2025, 12, 26),
}


def _seed_for(instrument_id: str) -> int:
    h = hashlib.sha256(f"alethra-synthetic-v1:{instrument_id}".encode()).hexdigest()
    return int(h[:8], 16)


def _trading_days(market: Market, start: date, end: date) -> List[date]:
    holidays = _US_HOLIDAYS_2024_2025 if market == Market.US else _HK_HOLIDAYS_2024_2025
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


def _generate_series(
    instrument_id: str,
    trading_days: List[date],
) -> List[Dict]:
    base = _BASE_PRICES.get(instrument_id, 100.0)
    vol = _DAILY_VOL.get(instrument_id, 0.018)
    seed = _seed_for(instrument_id)

    bars: List[Dict] = []
    price = base
    rng_state = seed

    for i, day in enumerate(trading_days):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        daily_return = ((rng_state / 0x7FFFFFFF) - 0.5) * 2 * vol
        trend = 0.0002 if i % 60 < 40 else -0.0001
        close = round(price * (1 + daily_return + trend), 4)

        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        intraday_range = close * vol * (0.5 + (rng_state / 0x7FFFFFFF))
        high = round(max(price, close) + intraday_range * 0.3, 4)
        low = round(min(price, close) - intraday_range * 0.3, 4)

        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        open_offset = ((rng_state / 0x7FFFFFFF) - 0.5) * intraday_range * 0.2
        open_price = round(price + open_offset, 4)

        low = max(0.01, low)
        open_price = max(0.01, open_price)
        close = max(0.01, close)
        high = max(open_price, close, high)

        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        base_volume = 1_000_000 if instrument_id.startswith("US:") else 500_000
        volume = int(base_volume * (0.5 + (rng_state / 0x7FFFFFFF) * 1.5))

        bars.append({
            "date": day.strftime("%Y-%m-%d"),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

        price = close

    return bars


_DATA_RANGE_START = date(2023, 1, 3)
_DATA_RANGE_END = date(2025, 12, 31)

_cache: Dict[str, List[Dict]] = {}


def get_synthetic_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
) -> List[Dict]:
    market, _ = parse_instrument_id(instrument_id)
    key = instrument_id

    if key not in _cache:
        days = _trading_days(market, _DATA_RANGE_START, _DATA_RANGE_END)
        _cache[key] = _generate_series(instrument_id, days)

    all_bars = _cache[key]
    return [b for b in all_bars if start_date <= b["date"] <= end_date]


def get_available_real_symbols() -> List[str]:
    from app.data.real_instruments import ALL_REAL_INSTRUMENTS
    return [inst.instrument_id for inst in ALL_REAL_INSTRUMENTS]


def get_real_date_range() -> Dict[str, str]:
    return {
        "min": _DATA_RANGE_START.strftime("%Y-%m-%d"),
        "max": _DATA_RANGE_END.strftime("%Y-%m-%d"),
    }


def clear_cache() -> None:
    _cache.clear()
