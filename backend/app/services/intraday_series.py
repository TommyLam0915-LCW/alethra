"""Session-aware series integrity for equity intraday bars.

The crypto lane's ``build_series`` answers "which candle is finished, which
arrived twice, which is missing" for a market that never closes. An equity
session breaks two of its assumptions:

* a gap is a missing bar *inside a regular session*, not every minute of the
  overnight and weekend; and
* data is only stale if a bar should have closed since the last one — a
  Saturday is not a 48-hour outage.

This module reuses the dedup/finality primitives unchanged and replaces only
those two calendar answers, so both lanes agree on what a candle is.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from app.providers.data_provider import IntradayBar, intraday_interval_minutes
from app.providers.instruments import MarketStructure
from app.services.crypto_series import (
    CandleSeries,
    RawCandle,
    build_series,
    stale_after_seconds,
)


def _decimal(value: Optional[float]) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


def _to_raw(rows: Sequence[IntradayBar]) -> List[RawCandle]:
    return [
        RawCandle(
            open_time=row.timestamp,
            open=_decimal(row.open),
            high=_decimal(row.high),
            low=_decimal(row.low),
            close=_decimal(row.close),
            volume=_decimal(row.volume),
        )
        for row in rows
    ]


def _regular_sessions(structure: MarketStructure):
    return sorted(
        (s for s in structure.sessions if s.is_regular), key=lambda s: s.start
    )


def _session_grid(
    day, structure: MarketStructure, interval: timedelta
) -> List[datetime]:
    """All regular-session bar opens for one exchange-local calendar day."""
    tz = ZoneInfo(structure.timezone)
    opens: List[datetime] = []
    for session in _regular_sessions(structure):
        start = datetime.combine(day, session.start, tzinfo=tz)
        end = datetime.combine(day, session.end, tzinfo=tz)
        cursor = start
        while cursor + interval <= end:
            opens.append(cursor)
            cursor += interval
    opens.sort()
    return opens


def session_gaps(
    open_times: Sequence[datetime],
    interval_minutes: int,
    structure: MarketStructure,
) -> Tuple[Tuple[Tuple[datetime, datetime], ...], int]:
    """Missing regular-session bars between the first and last observed bar.

    Bounds are the observed span on purpose: a fetch that only returned the
    last two hours is a short window, not two hours of missing data. Both the
    gap tuples and the count are in UTC to match the stored timestamps.
    """
    interval = timedelta(minutes=interval_minutes)
    tz = ZoneInfo(structure.timezone)
    by_day: dict = {}
    for ts in open_times:
        local = ts.replace(tzinfo=timezone.utc).astimezone(tz)
        by_day.setdefault(local.date(), []).append(local)

    missing: List[datetime] = []
    for day in sorted(by_day):
        if not structure.is_trading_day(day):
            continue
        locals_ = by_day[day]
        observed = {dt.replace(tzinfo=None) for dt in locals_}
        lo = min(observed)
        hi = max(observed)
        expected = [
            dt.replace(tzinfo=None)
            for dt in _session_grid(day, structure, interval)
            if lo <= dt.replace(tzinfo=None) <= hi
        ]
        missing.extend(dt for dt in expected if dt not in observed)

    missing.sort()
    gaps: List[Tuple[datetime, datetime]] = []
    for dt in missing:
        if gaps and dt - gaps[-1][1] == interval:
            gaps[-1] = (gaps[-1][0], dt)
        else:
            gaps.append((dt, dt))
    return (
        tuple(
            (
                start.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None),
                end.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None),
            )
            for start, end in gaps
        ),
        len(missing),
    )


def latest_expected_open(
    structure: MarketStructure,
    interval_minutes: int,
    *,
    now: datetime,
) -> Optional[datetime]:
    """Most recent bar that should have closed by ``now``, in UTC (naive).

    None means no regular session has closed a bar yet (e.g. before the first
    open, or only non-trading days since listing), so staleness cannot be
    judged from the clock alone.
    """
    interval = timedelta(minutes=interval_minutes)
    tz = ZoneInfo(structure.timezone)
    local_now = now.astimezone(tz)
    sessions = _regular_sessions(structure)

    day = local_now.date()
    for _ in range(14):
        if structure.is_trading_day(day):
            for session in reversed(sessions):
                session_start = datetime.combine(day, session.start, tzinfo=tz)
                session_end = datetime.combine(day, session.end, tzinfo=tz)
                limit = min(session_end, local_now)
                if limit <= session_start:
                    continue
                grid = session_start
                while grid + interval <= limit:
                    grid += interval
                # ``grid`` is now the first open whose close is past ``limit``.
                candidate = grid - interval
                if candidate >= session_start and candidate + interval <= local_now:
                    return (
                        candidate.astimezone(timezone.utc).replace(tzinfo=None)
                    )
        day -= timedelta(days=1)
    return None


def build_intraday_series(
    instrument_id: str,
    interval: str,
    rows: Sequence[IntradayBar],
    structure: MarketStructure,
    *,
    now: Optional[datetime] = None,
) -> CandleSeries:
    """Normalise provider rows with session-scoped gaps and staleness."""
    minutes = intraday_interval_minutes(interval)
    current = now or datetime.now(timezone.utc)
    base = build_series(instrument_id, minutes, _to_raw(rows), now=current)

    gaps, missing = session_gaps(
        [c.open_time for c in base.completed], minutes, structure
    )

    last_close = base.last_completed_close_time
    if last_close is None:
        stale_seconds: Optional[int] = None
        is_stale = True
    else:
        stale_seconds = max(0, int((current - last_close).total_seconds()))
        expected = latest_expected_open(structure, minutes, now=current)
        # ``last_close`` is tz-aware (candles carry UTC); the expected grid is
        # naive UTC to match storage. Compare in one frame.
        last_close_utc = last_close.astimezone(timezone.utc).replace(tzinfo=None)
        is_stale = expected is not None and last_close_utc < expected

    return replace(
        base,
        gaps=gaps,
        missing_intervals=missing,
        stale_seconds=stale_seconds,
        stale_after_seconds=stale_after_seconds(minutes),
        is_stale=is_stale,
    )


def market_session_view(
    structure: MarketStructure,
    *,
    now: datetime,
) -> dict:
    """Coarse exchange-local session state for the intraday view.

    ``holiday_calendar_complete`` is scoped to the response date: a calendar
    that ends before it cannot confirm the day either way. Outside a covered
    date inside the session window the answer is
    ``unknown_holiday_calendar`` rather than a guess.
    """
    tz = ZoneInfo(structure.timezone)
    local = now.astimezone(tz)
    sessions = _regular_sessions(structure)
    current = next(
        (
            s
            for s in sessions
            if s.start <= local.time() < s.end
        ),
        None,
    )
    day_status = structure.trading_day_status(local.date())
    if day_status == "weekend":
        status = "closed_weekend"
    elif day_status == "holiday":
        status = "closed_holiday"
    elif day_status == "calendar_unknown":
        # A covered weekend/holiday is settled above. Outside coverage the
        # session window decides: no session is closed whichever it is, but
        # claiming the market open would be a guess.
        status = "unknown_holiday_calendar" if current is not None else "closed"
    else:
        status = "open" if current else "closed"
    return {
        "exchange_local_time": local.isoformat(),
        "timezone": structure.timezone,
        "regular_session": current.name if current else None,
        "session_status": status,
        "holiday_calendar_complete": structure.calendar_covers(local.date())
        and (bool(structure.holidays) or structure.always_open),
    }
