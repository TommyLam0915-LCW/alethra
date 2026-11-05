"""Accumulating warehouse for crypto candles the venue only lends.

Kraken's public OHLC answers with at most the newest 721 rows and does not
honour ``since`` beyond that window, so a venue read is a *loan*: the bars age
out roughly 120 days later at 4h and 720 days later daily. The only way this
deployment can hold a multi-year live series is to store what it reads, every
time it reads — which is what :func:`harvest_interval` does into
``market_data_bars``.

Rules this module keeps:

- only completed candles are stored; a forming bar would freeze a price that is
  still moving, and the venue would later disagree with it;
- the write is idempotent (unique on instrument + provider + grain + timestamp),
  so harvesting the same day twice neither duplicates nor rewrites a stored bar,
  and a daily bar can never overwrite the four-hour bar at the same clock time;
- ``data_nature`` stays ``live``: every row here came from the venue, never
  from a demo or synthetic generator;
- ``bar_timestamp`` is naive UTC, matching that column's existing convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.market_data import MarketDataBar
from app.providers.kraken_provider import KrakenProvider
from app.services.crypto_series import CandleSeries
from app.services.strategy_lab import LabBar

PROVIDER_ID = "kraken"


def _bar_timestamp(open_time: datetime) -> datetime:
    value = open_time if open_time.tzinfo else open_time.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def harvest_interval(
    db: Session,
    provider: KrakenProvider,
    instrument_id: str,
    interval_minutes: int,
    start: Optional[Any] = None,
) -> Dict[str, Any]:
    """Read one interval from the venue and store every completed candle.

    ``start=None`` (the default) takes the whole shelf the venue lends. A
    ``start`` older than the venue's retention does not buy extra depth — it
    only makes :meth:`KrakenProvider.get_history_candle_series` refuse, because
    a named start is a claim that the series covers it.
    """
    kwargs: Dict[str, Any] = {"interval_minutes": interval_minutes}
    if start is not None:
        kwargs["start"] = start
    series: CandleSeries = provider.get_history_candle_series(instrument_id, **kwargs)

    rows = [
        {
            "instrument_id": instrument_id,
            "provider_id": PROVIDER_ID,
            "bar_date": candle.open_time.date(),
            "bar_timestamp": _bar_timestamp(candle.open_time),
            "open": float(candle.open),
            "high": float(candle.high),
            "low": float(candle.low),
            "close": float(candle.close),
            "volume": None,
            "volume_decimal": candle.volume,
            "is_adjusted": "raw",
            "interval_minutes": interval_minutes,
            "fetched_at": _bar_timestamp(series.fetched_at),
        }
        for candle in series.completed
    ]
    inserted = 0
    if rows:
        statement = pg_insert(MarketDataBar).values(rows)
        statement = statement.on_conflict_do_nothing(
            index_elements=["instrument_id", "provider_id", "interval_minutes",
                            "bar_timestamp"],
        )
        inserted = db.execute(statement).rowcount or 0
        db.commit()

    return {
        "instrument_id": instrument_id,
        "interval_minutes": interval_minutes,
        "provider_id": PROVIDER_ID,
        "fetched_at": series.fetched_at.isoformat(),
        "bars_read": len(rows),
        "bars_new": inserted,
        "bars_already_stored": len(rows) - inserted,
        "first_open_time": rows[0]["bar_timestamp"].isoformat() if rows else None,
        "last_open_time": rows[-1]["bar_timestamp"].isoformat() if rows else None,
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "gap_count": len(series.gaps),
        "missing_intervals": series.missing_intervals,
        "data_nature": "live",
    }


def load_stored_bars(
    db: Session,
    instrument_id: str,
    interval_minutes: int,
    provider_id: str = PROVIDER_ID,
) -> List[LabBar]:
    """Read the warehouse back as study input, oldest first.

    Rows are selected by the grain they were *written* with, not by whether
    their timestamp happens to divide evenly: a daily candle stamped 00:00 is
    also on the four-hour grid, and mixing the two would quietly corrupt every
    indicator computed from the series. Rows predating ``interval_minutes``
    (NULL grain) are unknown and stay out.
    """
    step = interval_minutes * 60
    stored = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == instrument_id,
            MarketDataBar.provider_id == provider_id,
            MarketDataBar.interval_minutes == interval_minutes,
        )
        .order_by(MarketDataBar.bar_timestamp.asc())
        .all()
    )
    bars: List[LabBar] = []
    for row in stored:
        epoch = int(row.bar_timestamp.replace(tzinfo=timezone.utc).timestamp())
        if epoch % step:
            continue
        bars.append(
            LabBar(
                open_time=datetime.fromtimestamp(epoch, tz=timezone.utc),
                interval_minutes=interval_minutes,
                open=Decimal(str(row.open)),
                high=Decimal(str(row.high)),
                low=Decimal(str(row.low)),
                close=Decimal(str(row.close)),
                volume=row.volume_decimal
                if row.volume_decimal is not None
                else Decimal(row.volume or 0),
            )
        )
    return bars
