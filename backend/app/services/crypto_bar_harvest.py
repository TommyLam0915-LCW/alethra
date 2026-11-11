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
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.market_data import MarketDataBar
from app.providers.kraken_provider import KrakenProvider
from app.services.crypto_series import Candle, CandleSeries
from app.services.strategy_lab import LabBar

PROVIDER_ID = "kraken"


def _bar_timestamp(open_time: datetime) -> datetime:
    value = open_time if open_time.tzinfo else open_time.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _warehouse_row(
    candle: Candle,
    *,
    instrument_id: str,
    interval_minutes: int,
    fetched_at: datetime,
    provider_id: str = PROVIDER_ID,
) -> Dict[str, Any]:
    """One warehouse row for one completed candle.

    This is the whole contract, in one place: crypto volume is a fraction of a
    coin, so it goes to ``volume_decimal`` and never to the equity ``volume``
    column; ``interval_minutes`` is part of the row's identity, so a daily bar
    and a four-hour bar at the same clock time stay two rows; ``is_adjusted``
    says the price is raw venue output.
    """
    return {
        "instrument_id": instrument_id,
        "provider_id": provider_id,
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
        "fetched_at": _bar_timestamp(fetched_at),
    }


def _insert_warehouse_rows(db: Session, rows: List[Dict[str, Any]]) -> int:
    """Insert rows that are not already there and return how many landed.

    ``result.rowcount`` is not usable here: psycopg2 reports the true count for
    this statement while psycopg (with SQLAlchemy 2.1) reports ``-1``, so a
    count built from it publishes a wrong ``bars_new`` depending on which driver
    the process happens to load. ``RETURNING id`` gives the same answer on both,
    because ``ON CONFLICT DO NOTHING`` only emits a row for an actual insert.
    """
    if not rows:
        return 0
    statement = pg_insert(MarketDataBar).values(rows).on_conflict_do_nothing(
        index_elements=["instrument_id", "provider_id", "interval_minutes",
                        "bar_timestamp"],
    )
    return len(db.execute(statement.returning(MarketDataBar.id)).fetchall())


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
        _warehouse_row(
            candle,
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            fetched_at=series.fetched_at,
        )
        for candle in series.completed
    ]
    inserted = 0
    if rows:
        inserted = _insert_warehouse_rows(db, rows)
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


class WarehouseImportRejected(Exception):
    """A warehouse write that would break the stored series' contract.

    Raised before anything is written, so the database is exactly as it was;
    ``detail`` carries the rows that caused it for the caller to report.
    """

    def __init__(self, reason: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def _lock_key(instrument_id: str, provider_id: str, interval_minutes: int) -> int:
    """One advisory lock per series lane, so imports of the same series queue."""
    import zlib

    return zlib.crc32(f"{instrument_id}|{provider_id}|{interval_minutes}".encode()) & 0x7FFFFFFF


def acquire_import_lock(
    db: Session,
    instrument_id: str,
    provider_id: str,
    interval_minutes: int,
) -> str:
    """Try to hold the series' import lock for this transaction.

    ``"acquired"`` — this transaction is the only importer of the lane until it
    commits or rolls back; ``"contended"`` — another transaction holds it, so the
    caller must not write; ``"unsupported"`` — the dialect has no advisory lock,
    which is reported rather than treated as a pass, because an in-process test
    on SQLite cannot prove anything about server-side serialization.

    It is a *try*, not a wait: an import that finds the lane busy refuses and
    leaves nothing written, instead of sitting on a lock while its plan ages.
    """
    if db.get_bind().dialect.name != "postgresql":
        return "unsupported"
    from sqlalchemy import text

    got = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": _lock_key(instrument_id, provider_id, interval_minutes)},
    ).scalar()
    return "acquired" if got else "contended"


def _stored_by_timestamp(
    db: Session,
    timestamps: List[datetime],
    *,
    instrument_id: str,
    provider_id: str,
    for_update: bool = False,
) -> Dict[datetime, List[MarketDataBar]]:
    """Every stored row at these clock times, whatever grain it claims.

    ``for_update`` locks the rows it reads. The dry-run plan does not ask for
    it; the write path does, because a row classified as identical and then
    changed by someone else before the UPDATE would otherwise be overwritten
    from a plan that is no longer true.
    """
    if not timestamps:
        return {}
    query = (
        db.query(MarketDataBar)
        .filter(
            and_(
                MarketDataBar.instrument_id == instrument_id,
                MarketDataBar.provider_id == provider_id,
                MarketDataBar.bar_timestamp.in_(timestamps),
            )
        )
    )
    if for_update and db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()

    grouped: Dict[datetime, List[MarketDataBar]] = {}
    for row in query.all():
        grouped.setdefault(row.bar_timestamp, []).append(row)
    return grouped


def _same_quantity(stored: Optional[Decimal], incoming: Optional[Decimal]) -> bool:
    if stored is None or incoming is None:
        return stored is None and incoming is None
    return Decimal(stored) == Decimal(incoming)


def _matches(row: MarketDataBar, candle: Candle) -> bool:
    return (
        float(row.open) == float(candle.open)
        and float(row.high) == float(candle.high)
        and float(row.low) == float(candle.low)
        and float(row.close) == float(candle.close)
        and _same_quantity(row.volume_decimal, candle.volume)
        and row.volume is None
    )


def _difference(row: MarketDataBar, candle: Candle) -> Dict[str, Any]:
    return {
        "bar_timestamp": row.bar_timestamp.isoformat(),
        "stored": [str(row.open), str(row.high), str(row.low), str(row.close),
                   str(row.volume_decimal), str(row.volume)],
        "incoming": [str(candle.open), str(candle.high), str(candle.low),
                     str(candle.close), str(candle.volume), None],
    }


def classify_series_rows(
    db: Session,
    series: CandleSeries,
    *,
    provider_id: str = PROVIDER_ID,
    lock_for_write: bool = False,
) -> Dict[str, Any]:
    """Say what writing ``series`` would do, without writing anything.

    Every timestamp lands in exactly one of three buckets — absent, already
    stored with the same values, or stored with *different* values — and a
    fourth bucket catches rows that share the clock time under another or no
    grain, which the unique key would let sit beside the new row.

    ``lock_for_write`` holds the rows it classified so they cannot change
    before the write that was planned from them. A report for a human to read
    leaves it off; :func:`store_series_bars` turns it on.
    """
    interval_minutes = series.interval_minutes
    rows = [
        _warehouse_row(
            candle,
            instrument_id=series.instrument_id,
            interval_minutes=interval_minutes,
            fetched_at=series.fetched_at,
            provider_id=provider_id,
        )
        for candle in series.completed
    ]
    grouped = _stored_by_timestamp(
        db,
        [row["bar_timestamp"] for row in rows],
        instrument_id=series.instrument_id,
        provider_id=provider_id,
        for_update=lock_for_write,
    )

    to_insert: List[Dict[str, Any]] = []
    conflicting: List[Dict[str, Any]] = []
    same_grain: Dict[datetime, MarketDataBar] = {}
    ambiguous: List[MarketDataBar] = []
    identical = 0
    for candle, row in zip(series.completed, rows):
        stored_here = grouped.get(row["bar_timestamp"], [])
        match = None
        for stored in stored_here:
            if stored.interval_minutes == interval_minutes:
                match = stored
            else:
                ambiguous.append(stored)
        if match is None:
            to_insert.append(row)
        else:
            same_grain[row["bar_timestamp"]] = match
            if _matches(match, candle):
                identical += 1
            else:
                conflicting.append(_difference(match, candle))

    return {
        "rows": rows,
        "new": to_insert,
        "identical": identical,
        "conflicting": conflicting,
        "grain_ambiguous": ambiguous,
        "same_grain_by_timestamp": same_grain,
    }


def store_series_bars(
    db: Session,
    series: CandleSeries,
    *,
    provider_id: str = PROVIDER_ID,
    on_conflict: str = "reject",
    retire_grain_ambiguous: bool = False,
) -> Dict[str, Any]:
    """Write an already-built series into the warehouse without calling the venue.

    :func:`harvest_interval` is the live path: it reads, then stores. This is the
    same write for a series that was read earlier and is now being restored
    offline, so the file is the input and no request is made. Both paths build
    rows through :func:`_warehouse_row`, so an offline import cannot arrive in a
    shape the venue path would never have written.

    Two decisions the caller has to make explicitly, because both are silent
    data loss if defaulted:

    ``on_conflict`` — what to do when the warehouse already holds *this* key with
    different values. ``"reject"`` refuses the whole import; ``"overwrite"``
    replaces the stored values and counts them as updated.

    ``retire_grain_ambiguous`` — rows at the same clock time under a different or
    missing ``interval_minutes`` do not collide on the unique key, so an import
    would leave two rows for one bar and every grain-blind reader would then see
    the series twice. ``False`` refuses; ``True`` deletes those rows first.
    """
    if on_conflict not in {"reject", "overwrite"}:
        raise ValueError(f"unknown on_conflict policy: {on_conflict!r}")

    interval_minutes = series.interval_minutes
    lock = acquire_import_lock(db, series.instrument_id, provider_id, interval_minutes)
    if lock == "contended":
        raise WarehouseImportRejected(
            f"another import holds {series.instrument_id} at {interval_minutes}m; "
            "nothing was written",
            {"lock": "contended", "interval_minutes": interval_minutes},
        )
    report = {
        "instrument_id": series.instrument_id,
        "provider_id": provider_id,
        "interval_minutes": interval_minutes,
        "fetched_at": series.fetched_at.isoformat(),
        "bars_read": 0,
        "bars_new": 0,
        "bars_already_stored": 0,
        "bars_updated": 0,
        "bars_conflicting": 0,
        "bars_grain_ambiguous": 0,
        "bars_grain_ambiguous_retired": 0,
        "first_open_time": None,
        "last_open_time": None,
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "gap_count": len(series.gaps),
        "missing_intervals": series.missing_intervals,
        "import_lock": lock,
    }
    plan = classify_series_rows(
        db, series, provider_id=provider_id, lock_for_write=True)
    rows = plan["rows"]
    report["bars_read"] = len(rows)
    report["bars_already_stored"] = plan["identical"]
    report["bars_conflicting"] = len(plan["conflicting"])
    report["bars_grain_ambiguous"] = len(plan["grain_ambiguous"])
    if not rows:
        return report
    report["first_open_time"] = rows[0]["bar_timestamp"].isoformat()
    report["last_open_time"] = rows[-1]["bar_timestamp"].isoformat()

    conflicting = plan["conflicting"]
    ambiguous = plan["grain_ambiguous"]

    if conflicting and on_conflict == "reject":
        raise WarehouseImportRejected(
            f"{len(conflicting)} stored bar(s) at the same key hold different "
            "values; nothing was written",
            {"conflicting": conflicting[:5], "conflict_count": len(conflicting)},
        )
    if ambiguous and not retire_grain_ambiguous:
        raise WarehouseImportRejected(
            f"{len(ambiguous)} stored row(s) share these timestamps under a "
            "different or missing grain; nothing was written",
            {
                "grain_ambiguous": [
                    {
                        "bar_timestamp": bar.bar_timestamp.isoformat(),
                        "interval_minutes": bar.interval_minutes,
                        "volume": str(bar.volume),
                        "volume_decimal": str(bar.volume_decimal),
                    }
                    for bar in ambiguous[:5]
                ],
                "grain_ambiguous_count": len(ambiguous),
            },
        )

    # Only now is the database touched, and everything above is a read.
    if ambiguous:
        for bar in ambiguous:
            db.delete(bar)
        report["bars_grain_ambiguous_retired"] = len(ambiguous)
        # Retire first: the insert below must not race the rows it replaces.
        db.flush()

    if conflicting:
        by_time = {row["bar_timestamp"]: candle for row, candle in zip(rows, series.completed)}
        for timestamp, bar in plan["same_grain_by_timestamp"].items():
            candle = by_time.get(timestamp)
            if candle is None or _matches(bar, candle):
                continue
            bar.open = float(candle.open)
            bar.high = float(candle.high)
            bar.low = float(candle.low)
            bar.close = float(candle.close)
            bar.volume = None
            bar.volume_decimal = candle.volume
            bar.is_adjusted = "raw"
            bar.fetched_at = rows[0]["fetched_at"]
            report["bars_updated"] += 1

    report["bars_new"] = _insert_warehouse_rows(db, plan["new"])
    db.commit()
    return report


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
