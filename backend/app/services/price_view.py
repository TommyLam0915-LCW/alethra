"""Honest labeling for price views: timeframe, mode, market status, timestamps.

A price view must never let demo OHLCV, an old close, or a historical replay be
read as a live quote, so every field here is derived from what the provider
itself claims plus the market structure calendar — never from a default.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.providers.data_provider import DataProvider, OHLCVBar
from app.providers.instruments import MarketStructure

# Only daily bars exist in this codebase today: providers serve one bar per
# trading day and market_data_bars stores day-granularity timestamps.
SUPPORTED_TIMEFRAMES: Tuple[str, ...] = ("1d",)

TIMESTAMP_SEMANTICS = {
    "1d": (
        "Bar covers one exchange-local trading day; market_timestamp is that "
        "day's 00:00 in the exchange timezone."
    ),
}

MODE_LABELS_ZH = {
    "live_realtime": "即時",
    "delayed": "延遲",
    "historical_replay": "歷史回放",
    "synthetic_fixture": "合成數據",
    "unknown": "來源未確認",
}

MARKET_STATUS_LABELS_ZH = {
    "open": "開市",
    "closed": "休市",
    "stale": "數據陳舊",
    "no_data": "無數據",
    "disconnected": "供應商未連接",
    "unknown": "狀態未知",
}


def validate_timeframe(timeframe: Optional[str]) -> str:
    """Return the canonical timeframe, rejecting ones the stack cannot serve.

    Silently answering an intraday request with daily bars would present a
    different series than the one asked for, so unsupported timeframes fail
    loudly instead.
    """
    tf = (timeframe or "1d").strip().lower()
    if tf not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Supported: "
            f"{', '.join(SUPPORTED_TIMEFRAMES)}. Intraday bars require an "
            "intraday-capable provider, which is not connected."
        )
    return tf


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def resolve_mode(
    provider: DataProvider,
    quality: Dict[str, Any],
    *,
    timeframe: str,
    latest_bar_date: Optional[str],
    end_date: Optional[str],
    now: Optional[datetime] = None,
    allow_realtime: bool = True,
) -> Dict[str, Any]:
    """Classify the series the caller is about to render.

    synthetic_fixture wins over everything: a deterministic fixture is not a
    quote no matter how the feed is wired. A live provider answering a range
    that ended before today is replaying history, not streaming, and a series
    read back from storage (allow_realtime=False) always is.
    """
    nature = str(quality.get("data_nature") or "unknown").lower()
    feed_mode = str(provider.feed_mode or "").lower()

    if nature == "synthetic":
        mode = "synthetic_fixture"
    elif nature == "delayed":
        mode = "delayed"
    elif nature == "live":
        mode = "historical_replay"
        requested_through = _as_date(end_date)
        exchange_today = (now or _now_utc()).astimezone(
            ZoneInfo(str(quality.get("timezone") or "UTC"))
        ).date()
        if allow_realtime and feed_mode == "live" and timeframe == "1d":
            if requested_through is None or requested_through >= exchange_today:
                mode = "live_realtime"
    else:
        mode = "unknown"

    return {
        "mode": mode,
        "mode_label_zh": MODE_LABELS_ZH[mode],
        "data_nature": nature,
        "feed_mode": feed_mode,
        "is_realtime_capable": mode == "live_realtime",
        "timeframe": timeframe,
        "timestamp_semantics": TIMESTAMP_SEMANTICS.get(timeframe),
        "data_as_of": latest_bar_date,
    }


def market_status_view(
    structure: MarketStructure,
    *,
    provider_available: bool,
    has_bars: bool,
    is_stale: bool,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Exchange-local market state, with data health taking priority.

    A stale or missing series is reported as stale/no_data even while the
    exchange is open: the user needs to know what *this view* can show.
    """
    tz = ZoneInfo(structure.timezone)
    local = (now or _now_utc()).astimezone(tz)
    session, is_regular = _current_session(structure, local)
    upcoming = _upcoming_sessions(structure, local)
    next_session, next_session_open = upcoming[0] if upcoming else (None, None)
    next_regular = next((o for s, o in upcoming if s.is_regular), None)

    if not provider_available:
        status, reason = "disconnected", "provider_unavailable"
    elif not has_bars:
        status, reason = "no_data", "no_bars_for_range"
    elif is_stale:
        status, reason = "stale", "latest_bar_older_than_threshold"
    elif session is not None:
        status, reason = "open", f"in_session:{session}"
    else:
        status, reason = "closed", _closed_reason(structure, local)

    return {
        "market": structure.market.value,
        "exchange": structure.exchange.value,
        "timezone": structure.timezone,
        "exchange_local_time": local.isoformat(),
        "holiday_calendar_populated": bool(structure.holidays),
        "market_status": status,
        "market_status_label_zh": MARKET_STATUS_LABELS_ZH[status],
        "market_status_reason": reason,
        "current_session": session,
        "is_regular_session": is_regular,
        "next_session": next_session.name if next_session else None,
        "next_session_open": next_session_open.isoformat() if next_session_open else None,
        "next_regular_open": next_regular.isoformat() if next_regular else None,
    }


def timestamp_fields(
    market_timestamp: Optional[datetime],
    received_at: Optional[datetime],
    *,
    precision: str,
) -> Dict[str, Any]:
    """Exchange-local bar timestamp + UTC receive time, or an explicit null.

    Nulls are preferred over an invented timestamp: a label that says "unknown"
    is a fact about the data, while a guessed one is a false quote.
    """
    return {
        "market_timestamp": market_timestamp.isoformat() if market_timestamp else None,
        "timestamp_precision": precision if market_timestamp else "unknown",
        "received_at": received_at.isoformat() if received_at else None,
    }


def provider_bar_timestamp(bar_date: str, structure: MarketStructure) -> Optional[datetime]:
    """Start of the bar's trading day in the exchange timezone."""
    day = _as_date(bar_date)
    if day is None:
        return None
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(structure.timezone))


def bar_view(
    bar: OHLCVBar,
    structure: MarketStructure,
    timeframe: str,
    received_at: datetime,
) -> Dict[str, Any]:
    """One provider bar plus exchange-local and receive timestamps."""
    return {
        "date": bar.date,
        **timestamp_fields(
            provider_bar_timestamp(bar.date, structure),
            received_at,
            precision=timeframe,
        ),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def stored_bar_view(
    bar,
    structure: MarketStructure,
    timeframe: str,
) -> Dict[str, Any]:
    """One persisted bar row, keeping the row's own fetch time as receive time.

    Storage writes bar_timestamp naive in the exchange timezone and fetched_at
    naive in UTC, so each is labeled with the timezone it was actually stored in
    rather than being re-derived from the calendar day.
    """
    market_ts = bar.bar_timestamp
    if market_ts is not None and market_ts.tzinfo is None:
        market_ts = market_ts.replace(tzinfo=ZoneInfo(structure.timezone))
    received = bar.fetched_at
    if received is not None and received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return {
        "date": bar.bar_date.isoformat(),
        **timestamp_fields(market_ts, received, precision=timeframe),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "provider_id": bar.provider_id,
    }


def _as_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _sessions_by_start(structure: MarketStructure) -> List[Any]:
    return sorted(structure.sessions, key=lambda s: s.start)


def _current_session(
    structure: MarketStructure, local: datetime
) -> Tuple[Optional[str], Optional[bool]]:
    """(session name, is_regular) for a local wall clock, or (None, None)."""
    if not structure.is_trading_day(local.date()):
        return None, None
    t = local.time()
    for s in _sessions_by_start(structure):
        if s.start <= t < s.end:
            return s.name, s.is_regular
    return None, None


def _upcoming_sessions(
    structure: MarketStructure, local: datetime, limit: int = 5
) -> List[Tuple[Any, datetime]]:
    """Next few session opens after a local wall clock, in chronological order.

    US pre-market opens before the regular session, so "next open" alone is
    ambiguous; callers pick the session they mean.
    """
    tz = ZoneInfo(structure.timezone)
    sessions = _sessions_by_start(structure)
    upcoming: List[Tuple[Any, datetime]] = []
    day = local.date()
    for _ in range(14):
        if structure.is_trading_day(day):
            for s in sessions:
                open_dt = datetime.combine(day, s.start, tzinfo=tz)
                if open_dt > local:
                    upcoming.append((s, open_dt))
                    if len(upcoming) >= limit:
                        return upcoming
        day = day + timedelta(days=1)
    return upcoming


def _closed_reason(structure: MarketStructure, local: datetime) -> str:
    if not structure.is_trading_day(local.date()):
        return "weekend_or_holiday" if local.weekday() >= 5 else "market_holiday"
    return "outside_trading_sessions"
