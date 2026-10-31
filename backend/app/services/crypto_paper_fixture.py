"""Explicitly labelled synthetic quotes for the crypto paper lane.

This module exists so the paper lane can be exercised end to end without a new
venue read. It never pretends to be captured market data: every row it writes
carries ``data_nature="fixture"`` and a ``price_source`` beginning with
``fixture:``, and the API/receipt surface that label to the user.

The numbers are deterministic functions of the requested clock, not a replay of
any real Kraken response.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.lib.timeutil import utcnow
from app.services.crypto_paper import record_quote_bars
from app.services.crypto_series import Candle

FIXTURE_PRICE_SOURCE = "fixture:kraken_btc_usd_synthetic_v1"
FIXTURE_LABEL = "Synthetic quote fixture — not live venue data"
FIXTURE_NOTE = (
    "Deterministic synthetic BTC/USD candles for engineering the paper lane. "
    "They are not captured Kraken data and must not be presented as live prices."
)

# Relative to a 105000.0 base, in quote currency, one decimal.
_OFFSETS: List[str] = ["-120.0", "-80.0", "-40.0", "-10.0", "25.0", "60.0"]


def build_fixture_candles(
    *,
    count: int = 6,
    interval_minutes: int = 1,
    anchor: Optional[datetime] = None,
) -> List[Candle]:
    """Return ``count`` completed synthetic candles ending at ``anchor``.

    The final candle closes exactly at the floored anchor minute, so a caller
    that prices with the same clock gets a fresh quote. Every candle is final.
    """
    if count <= 0:
        raise ValueError("count must be positive")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    clock = anchor if anchor is not None else utcnow()
    anchor_minute = clock.replace(second=0, microsecond=0)
    base = Decimal("105000.0")
    candles: List[Candle] = []

    for index in range(count):
        open_time = anchor_minute - timedelta(
            minutes=interval_minutes * (count - index)
        )
        close = base + Decimal(_OFFSETS[index % len(_OFFSETS)])
        open_price = close - Decimal("5.0")
        high = close + Decimal("12.5")
        low = open_price - Decimal("7.5")
        candles.append(
            Candle(
                instrument_id=KRAKEN_BTC_USD.instrument_id,
                interval_minutes=interval_minutes,
                open_time=open_time,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=Decimal("1.25000000") + Decimal(index) / Decimal("10"),
                trade_count=100 + index,
                is_final=True,
            )
        )
    return candles


def seed_crypto_paper_fixture(
    db: Session,
    *,
    interval_minutes: int = 1,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Upsert the labelled fixture into ``crypto_quote_bars``.

    The caller owns the commit. Repeated calls with the same clock update the
    same rows; calls at later minutes add newer rows (the table is keyed by open
    time) and never overwrite a live row, because live and fixture rows share
    the same key space but the newest fixture is what a fixture seed intends.
    """
    clock = now if now is not None else utcnow()
    candles = build_fixture_candles(interval_minutes=interval_minutes, anchor=clock)
    counts = record_quote_bars(
        db,
        instrument_id=KRAKEN_BTC_USD.instrument_id,
        provider_id="kraken",
        interval_minutes=interval_minutes,
        candles=candles,
        price_source=FIXTURE_PRICE_SOURCE,
        data_nature="fixture",
        fetched_at=clock,
    )
    return {
        **counts,
        "instrument_id": KRAKEN_BTC_USD.instrument_id,
        "interval_minutes": interval_minutes,
        "label": FIXTURE_LABEL,
        "note": FIXTURE_NOTE,
        "simulated": True,
    }
