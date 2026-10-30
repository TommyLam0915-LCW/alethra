"""Candle-series integrity for the crypto research lane.

The exchange is the only source of truth for prices, but it is not a source of
truth about *which* candles are finished, whether a poll skipped a minute, or
whether a row arrived twice. This module answers those questions once, in one
place, so the API, storage and any strategy input all see the same series:

- the forming (still-open) candle is separated from completed ones, so an
  unfinished bar can never be fed into strategy logic;
- duplicate timestamps are collapsed and counted, not silently overwritten;
- gaps are listed explicitly instead of being closed by interpolation;
- staleness is measured against the bar interval, never a calendar day;
- a fingerprint identifies the exact completed bars a result was derived from.

Nothing here fetches or invents data: a caller with no rows gets no candles.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

# Two consecutive intervals of silence from a continuously traded spot pair is
# already abnormal; below 60s every 1s poll would look stale.
MIN_STALE_SECONDS = 60
STALE_INTERVAL_MULTIPLIER = 2


@dataclass(frozen=True)
class RawCandle:
    """One exchange row before de-duplication and finality are decided."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: Optional[int] = None


@dataclass(frozen=True)
class Candle:
    instrument_id: str
    interval_minutes: int
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: Optional[int]
    is_final: bool

    @property
    def close_time(self) -> datetime:
        """Moment this candle stops changing (exclusive)."""
        return self.open_time + timedelta(minutes=self.interval_minutes)


@dataclass(frozen=True)
class CandleSeries:
    instrument_id: str
    interval_minutes: int
    fetched_at: datetime
    completed: Tuple[Candle, ...]
    forming: Optional[Candle]
    duplicate_timestamps: Tuple[datetime, ...]
    off_grid_timestamps: Tuple[datetime, ...]
    invalid_timestamps: Tuple[datetime, ...]
    gaps: Tuple[Tuple[datetime, datetime], ...]
    missing_intervals: int
    out_of_order_rows: int
    last_completed_close_time: Optional[datetime]
    stale_seconds: Optional[int]
    stale_after_seconds: int
    is_stale: bool
    fingerprint: str

    @property
    def bars_for_strategy(self) -> Tuple[Candle, ...]:
        """Completed candles only. The forming candle is never included."""
        return self.completed

    @property
    def integrity_ok(self) -> bool:
        return not (self.duplicate_timestamps or self.off_grid_timestamps
                    or self.invalid_timestamps or self.gaps)


def stale_after_seconds(interval_minutes: int) -> int:
    return max(MIN_STALE_SECONDS, interval_minutes * 60 * STALE_INTERVAL_MULTIPLIER)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fingerprint(instrument_id: str, interval_minutes: int, candles: Sequence[Candle]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{instrument_id}|{interval_minutes}m|".encode())
    for candle in candles:
        digest.update(
            (
                f"{int(candle.open_time.timestamp())}:{candle.open}:{candle.high}:"
                f"{candle.low}:{candle.close}:{candle.volume};"
            ).encode()
        )
    return digest.hexdigest()


def _valid_ohlc(raw: RawCandle) -> bool:
    values = (raw.open, raw.high, raw.low, raw.close, raw.volume)
    if any(not isinstance(v, Decimal) for v in values):
        return False
    if any(not v.is_finite() for v in values):
        return False
    if raw.volume < 0:
        return False
    if raw.high < max(raw.open, raw.close):
        return False
    if raw.low > min(raw.open, raw.close):
        return False
    if raw.high < raw.low:
        return False
    return True


def build_series(
    instrument_id: str,
    interval_minutes: int,
    rows: Sequence[RawCandle],
    *,
    now: Optional[datetime] = None,
) -> CandleSeries:
    """Normalise exchange rows into a series a strategy may safely read."""
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    current = _as_utc(now or datetime.now(timezone.utc))
    interval = timedelta(minutes=interval_minutes)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    off_grid: List[datetime] = []
    invalid: List[datetime] = []
    by_time: dict[datetime, RawCandle] = {}
    duplicates: List[datetime] = []
    out_of_order = 0
    previous: Optional[datetime] = None

    for raw in rows:
        open_time = _as_utc(raw.open_time)
        if previous is not None and open_time < previous:
            out_of_order += 1
        previous = open_time

        if (open_time - epoch) % interval != timedelta(0):
            off_grid.append(open_time)
            continue
        if not _valid_ohlc(raw):
            invalid.append(open_time)
            continue
        if open_time in by_time:
            duplicates.append(open_time)
        by_time[open_time] = raw

    candles: List[Candle] = []
    for open_time in sorted(by_time):
        raw = by_time[open_time]
        candles.append(
            Candle(
                instrument_id=instrument_id,
                interval_minutes=interval_minutes,
                open_time=open_time,
                open=raw.open,
                high=raw.high,
                low=raw.low,
                close=raw.close,
                volume=raw.volume,
                trade_count=raw.trade_count,
                is_final=(open_time + interval) <= current,
            )
        )

    completed = tuple(c for c in candles if c.is_final)
    still_forming = [c for c in candles if not c.is_final]
    forming = still_forming[-1] if still_forming else None
    # Only the newest bucket can legitimately be open. An older bucket still
    # marked unfinished means the exchange stopped updating it; presenting it
    # as live would be a frozen quote, so it is excluded and recorded.
    for candle in still_forming[:-1]:
        invalid.append(candle.open_time)

    gaps: List[Tuple[datetime, datetime]] = []
    missing = 0
    if len(completed) >= 2:
        present = {c.open_time for c in completed}
        last_expected = completed[-1].open_time
        expected = completed[0].open_time
        while expected <= last_expected:
            if expected in present:
                expected += interval
                continue
            gap_start = expected
            while expected <= last_expected and expected not in present:
                missing += 1
                expected += interval
            gaps.append((gap_start, expected - interval))

    last_close = completed[-1].close_time if completed else None
    stale_after = stale_after_seconds(interval_minutes)
    if last_close is None:
        stale_secs: Optional[int] = None
        is_stale = True
    else:
        stale_secs = max(0, int((current - last_close).total_seconds()))
        is_stale = stale_secs > stale_after

    return CandleSeries(
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        fetched_at=current,
        completed=completed,
        forming=forming,
        duplicate_timestamps=tuple(sorted(duplicates)),
        off_grid_timestamps=tuple(sorted(off_grid)),
        invalid_timestamps=tuple(sorted(invalid)),
        gaps=tuple(gaps),
        missing_intervals=missing,
        out_of_order_rows=out_of_order,
        last_completed_close_time=last_close,
        stale_seconds=stale_secs,
        stale_after_seconds=stale_after,
        is_stale=is_stale,
        fingerprint=_fingerprint(instrument_id, interval_minutes, completed),
    )
