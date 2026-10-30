"""Bounded, backend-controlled refresh of intraday bars from a live provider.

Why this exists instead of a frontend interval: the browser must never be the
thing that spends a provider quota. Every upstream call happens here, behind

* a per-series lock so concurrent readers coalesce into one request,
* a minimum refresh interval, so N tabs and N users collapse to one fetch,
* rolling hourly/daily request budgets kept by this process, and
* failure backoff that honours the provider's Retry-After.

The service only ever returns data it actually has. A missing key, an
unverified interval, an exhausted budget or an upstream error is reported as
that state — never filled in with synthetic bars, and never retried in a loop.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.config import get_settings
from app.data.real_instruments import get_instrument_by_id, get_provider_mapping
from app.lib.error_safety import safe_error_message, safe_provider_status
from app.providers.data_provider import (
    INTRADAY_INTERVALS,
    DataProvider,
    IntradayBar,
    ProviderRateLimitError,
    intraday_interval_minutes,
)
from app.providers.instruments import Instrument, Market, US_MARKET_STRUCTURE
from app.providers.provider_registry import get_data_provider
from app.services.crypto_series import CandleSeries
from app.services.intraday_series import (
    build_intraday_series,
    latest_expected_open,
    market_session_view,
)
from app.services.market_data_storage import MarketDataStorage

logger = logging.getLogger(__name__)

_MIN_REFRESH_SECONDS = 60

_STATE_LOCK = threading.Lock()
_SERIES_LOCKS: Dict[Tuple[str, str, str], threading.Lock] = {}
_STATES: Dict[Tuple[str, str, str], "_SeriesState"] = {}
# Every logical upstream attempt made by this process, newest last. A process
# restart forgets it, so it is a local guard, not a statement about the
# provider's real quota (reported as such in the API).
_ATTEMPTS: List[datetime] = []


class _SeriesState:
    def __init__(self) -> None:
        self.last_attempt_at: Optional[datetime] = None
        self.last_success_at: Optional[datetime] = None
        self.last_attempt_ok: bool = False
        self.last_status: str = "idle"
        self.last_error: Optional[str] = None
        self.failures: int = 0
        self.backoff_until: Optional[datetime] = None
        self.rows_last: int = 0
        self.new_completed_last: bool = False


def reset_intraday_state() -> None:
    """Clear process-local budget and series state (used by tests)."""
    with _STATE_LOCK:
        _SERIES_LOCKS.clear()
        _STATES.clear()
        _ATTEMPTS.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _series_lock(key: Tuple[str, str, str]) -> threading.Lock:
    with _STATE_LOCK:
        lock = _SERIES_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SERIES_LOCKS[key] = lock
        return lock


def _state(key: Tuple[str, str, str]) -> _SeriesState:
    with _STATE_LOCK:
        state = _STATES.get(key)
        if state is None:
            state = _SeriesState()
            _STATES[key] = state
        return state


def _budget_snapshot(now: datetime) -> Dict[str, int]:
    with _STATE_LOCK:
        cutoff = now - timedelta(hours=24)
        _ATTEMPTS[:] = [t for t in _ATTEMPTS if t >= cutoff]
        hour_used = sum(1 for t in _ATTEMPTS if t >= now - timedelta(hours=1))
        return {"hour_used": hour_used, "day_used": len(_ATTEMPTS)}


def _record_attempt(now: datetime) -> None:
    with _STATE_LOCK:
        _ATTEMPTS.append(now)


class IntradayRefreshService:
    def __init__(self, db):
        self.db = db
        self.storage = MarketDataStorage(db)
        self.settings = get_settings()

    # ---- configuration / capability -------------------------------------

    @property
    def _refresh_seconds(self) -> int:
        return max(_MIN_REFRESH_SECONDS, self.settings.intraday_refresh_seconds)

    def _resolve_provider(self) -> Tuple[Optional[DataProvider], Optional[str]]:
        try:
            return get_data_provider(), None
        except Exception as exc:  # no key, unknown provider, failed connect
            return None, safe_error_message(exc)

    def capability(self) -> Dict:
        settings = self.settings
        provider, provider_error = self._resolve_provider()
        declared = (
            list(provider.declared_intraday_intervals())
            if provider is not None and provider.supports_intraday()
            else []
        )
        now = _now()
        budget = _budget_snapshot(now)
        cap = {
            "lane": "intraday_rest",
            "enabled": settings.intraday_enabled,
            "provider_id": provider.provider_id if provider else settings.data_provider,
            "provider_name": provider.provider_name if provider else None,
            "provider_connected": provider.is_available() if provider else False,
            "provider_error": provider_error,
            "adapter_supports_intraday": bool(
                provider is not None and provider.supports_intraday()
            ),
            "declared_intervals": declared,
            "verified_intervals": settings.intraday_verified_interval_list,
            "default_interval": settings.intraday_default_interval,
            "allowed_instruments": settings.intraday_instrument_list,
            "refresh_seconds": self._refresh_seconds,
            "output_size": settings.intraday_outputsize,
            "data_nature": "unknown",
            "is_realtime": False,
            "realtime_evidence": "none_verified",
            "data_rights": "INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION",
            "market_calendar_complete": bool(US_MARKET_STRUCTURE.holidays),
            "budget": {
                **budget,
                "hour_limit": settings.intraday_max_requests_per_hour,
                "day_limit": settings.intraday_max_requests_per_day,
                "scope": "process",
            },
            "min_refresh_seconds": _MIN_REFRESH_SECONDS,
        }
        if provider is not None:
            cap["provider_status"] = safe_provider_status(provider.get_status())
        return cap

    # ---- validation ------------------------------------------------------

    def resolve_instrument(self, instrument_id: str, interval: str) -> Instrument:
        interval = (interval or "").strip().lower()
        if interval not in INTRADAY_INTERVALS:
            raise ValueError(
                f"Unsupported intraday interval '{interval}'. Supported: "
                f"{', '.join(INTRADAY_INTERVALS)}"
            )
        instrument_id = (instrument_id or "").strip().upper()
        allowed = self.settings.intraday_instrument_list
        if instrument_id not in allowed:
            raise ValueError(
                f"Instrument '{instrument_id}' is not in the intraday allow-list"
            )

        instrument = get_instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"Unknown instrument '{instrument_id}'")
        if instrument.market != Market.US:
            raise ValueError(
                "This round serves US instruments only; "
                f"'{instrument_id}' is {instrument.market.value}."
            )
        return instrument

    # ---- main entry ------------------------------------------------------

    def ensure_series(
        self,
        *,
        instrument_id: str,
        interval: str,
        limit: Optional[int] = None,
        allow_fetch: bool = True,
        now: Optional[datetime] = None,
    ) -> Dict:
        instrument = self.resolve_instrument(instrument_id, interval)
        current = now or _now()
        provider, provider_error = self._resolve_provider()
        provider_id = provider.provider_id if provider else self.settings.data_provider
        key = (provider_id, instrument.instrument_id, interval)
        series_lock = _series_lock(key)

        with series_lock:
            state = _state(key)
            if allow_fetch:
                refresh = self._maybe_fetch(
                    key=key,
                    state=state,
                    provider=provider,
                    provider_error=provider_error,
                    instrument=instrument,
                    interval=interval,
                    now=current,
                )
            else:
                refresh = {
                    "status": "read_only",
                    "attempted_upstream": False,
                    "upstream_requests": 0,
                    "rows_received": 0,
                    "new_completed_bar": False,
                    "retry_after_seconds": None,
                    "error": None,
                    "last_attempt_at": _iso(state.last_attempt_at),
                    "last_success_at": _iso(state.last_success_at),
                }

            stored = self._load_stored(instrument.instrument_id, provider_id, interval, limit)
            series = build_intraday_series(
                instrument.instrument_id,
                interval,
                stored,
                US_MARKET_STRUCTURE,
                now=current,
            )
            budget = _budget_snapshot(current)
            status = refresh["status"]
            expected = latest_expected_open(
                US_MARKET_STRUCTURE,
                intraday_interval_minutes(interval),
                now=current,
            )
            session = market_session_view(US_MARKET_STRUCTURE, now=current)
            as_of = series.last_completed_close_time

            payload = {
                "status": status,
                "series": series,
                "instrument": instrument,
                "provider_id": provider_id,
                "interval": interval,
                "interval_minutes": intraday_interval_minutes(interval),
                "refresh": {
                    **refresh,
                    "status": status,
                    "last_attempt_at": refresh["last_attempt_at"] or _iso(state.last_attempt_at),
                    "last_success_at": refresh["last_success_at"] or _iso(state.last_success_at),
                    "last_attempt_ok": state.last_attempt_ok,
                    "last_success_added_bar": state.new_completed_last,
                    "refresh_seconds": self._refresh_seconds,
                    "budget": {
                        **budget,
                        "hour_limit": self.settings.intraday_max_requests_per_hour,
                        "day_limit": self.settings.intraday_max_requests_per_day,
                        "scope": "process",
                    },
                },
                "budget": budget,
                "market": {
                    **session,
                    "expected_last_open": _iso(expected),
                    "holidays_configured": bool(US_MARKET_STRUCTURE.holidays),
                },
                "data_as_of": _iso(as_of),
                "provider_error": provider_error,
            }
            return payload

    # ---- fetch path ------------------------------------------------------

    def _maybe_fetch(
        self,
        *,
        key: Tuple[str, str, str],
        state: _SeriesState,
        provider: Optional[DataProvider],
        provider_error: Optional[str],
        instrument: Instrument,
        interval: str,
        now: datetime,
    ) -> Dict:
        def outcome(status: str, **extra) -> Dict:
            state.last_status = status
            return {
                "status": status,
                "attempted_upstream": False,
                "upstream_requests": 0,
                "rows_received": 0,
                "new_completed_bar": False,
                "retry_after_seconds": None,
                "error": None,
                "last_attempt_at": _iso(state.last_attempt_at),
                "last_success_at": _iso(state.last_success_at),
                **extra,
            }

        if not self.settings.intraday_enabled:
            return outcome("disabled")
        if provider is None:
            return outcome("not_configured", error=provider_error)
        if not provider.supports_intraday():
            return outcome("provider_not_intraday_capable")
        if interval not in provider.declared_intraday_intervals():
            return outcome("interval_not_declared")
        if interval not in self.settings.intraday_verified_interval_list:
            return outcome("unverified_interval")
        if state.backoff_until is not None and state.backoff_until > now:
            return outcome(
                "backoff",
                retry_after_seconds=max(
                    1, int((state.backoff_until - now).total_seconds())
                ),
                error=state.last_error,
            )
        if (
            state.last_attempt_at is not None
            and (now - state.last_attempt_at)
            < timedelta(seconds=self._refresh_seconds)
        ):
            return outcome(
                "cache_fresh" if state.last_attempt_ok else state.last_status,
                error=state.last_error,
            )

        budget = _budget_snapshot(now)
        if budget["hour_used"] >= self.settings.intraday_max_requests_per_hour:
            return outcome(
                "budget_exhausted",
                error="hourly upstream request budget exhausted",
            )
        if budget["day_used"] >= self.settings.intraday_max_requests_per_day:
            return outcome(
                "budget_exhausted",
                error="daily upstream request budget exhausted",
            )
        if not provider.is_available():
            return outcome(
                "provider_unavailable",
                error="provider is not connected",
            )
        # Internal identity (``US:AAPL``) is not a provider symbol. Sending it
        # upstream would either fail or, worse, silently resolve to something
        # else, so an unmapped instrument is refused before any request.
        mapping = get_provider_mapping(provider.provider_id, instrument.instrument_id)
        if mapping is None:
            return outcome("symbol_not_mapped")

        state.last_attempt_at = now
        _record_attempt(now)
        try:
            bars = provider.get_intraday_bars(
                mapping.provider_symbol,
                interval,
                outputsize=self.settings.intraday_outputsize,
            )
        except ProviderRateLimitError as exc:
            retry_after = exc.retry_after_seconds or self.settings.intraday_backoff_base_seconds
            retry_after = max(1, min(retry_after, self.settings.intraday_backoff_max_seconds))
            state.failures += 1
            state.last_attempt_ok = False
            state.backoff_until = now + timedelta(seconds=retry_after)
            state.last_error = safe_error_message(exc)
            logger.warning("Intraday provider rate limited for %s: %s", key, state.last_error)
            state.last_status = "rate_limited"
            return {
                "status": "rate_limited",
                "attempted_upstream": True,
                "upstream_requests": 1,
                "rows_received": 0,
                "new_completed_bar": False,
                "retry_after_seconds": retry_after,
                "error": state.last_error,
                "last_attempt_at": _iso(state.last_attempt_at),
                "last_success_at": _iso(state.last_success_at),
            }
        except Exception as exc:
            state.failures += 1
            state.last_attempt_ok = False
            delay = min(
                self.settings.intraday_backoff_base_seconds
                * (2 ** max(0, state.failures - 1)),
                self.settings.intraday_backoff_max_seconds,
            )
            state.backoff_until = now + timedelta(seconds=delay)
            state.last_error = safe_error_message(exc)
            logger.warning("Intraday provider fetch failed for %s: %s", key, state.last_error)
            state.last_status = "upstream_error"
            return {
                "status": "upstream_error",
                "attempted_upstream": True,
                "upstream_requests": 1,
                "rows_received": 0,
                "new_completed_bar": False,
                "retry_after_seconds": None,
                "error": state.last_error,
                "last_attempt_at": _iso(state.last_attempt_at),
                "last_success_at": _iso(state.last_success_at),
            }

        series = build_intraday_series(
            instrument.instrument_id,
            interval,
            bars,
            US_MARKET_STRUCTURE,
            now=now,
        )
        # Must run before storage: it compares the newest closed bar against the
        # newest *previously stored* one. After the write they are identical and
        # every poll would look like a new bar.
        new_completed = self._is_new_completed(
            instrument.instrument_id,
            provider.provider_id,
            interval,
            series,
        )
        candles = list(series.completed)
        if series.forming is not None:
            candles.append(series.forming)
        volume_by_open: Dict[datetime, Optional[float]] = {}
        for bar in bars:
            ts = bar.timestamp
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            volume_by_open[ts] = bar.volume
        counts = self.storage.store_intraday_candles(
            instrument_id=instrument.instrument_id,
            provider_id=provider.provider_id,
            timeframe=interval,
            candles=candles,
            volume_overrides=volume_by_open,
        )

        state.failures = 0
        state.last_attempt_ok = True
        state.backoff_until = None
        state.last_error = None
        state.last_success_at = now
        state.rows_last = len(bars)
        state.new_completed_last = new_completed

        if not bars:
            status = "no_data"
        elif new_completed:
            status = "new_bar"
        elif counts["stored"] + counts["updated"] > 0:
            status = "updated"
        else:
            status = "no_change"
        state.last_status = status

        return {
            "status": status,
            "attempted_upstream": True,
            "upstream_requests": 1,
            "rows_received": len(bars),
            "new_completed_bar": new_completed,
            "retry_after_seconds": None,
            "error": None,
            "storage": counts,
            "provider_symbol": mapping.provider_symbol,
            "last_attempt_at": _iso(state.last_attempt_at),
            "last_success_at": _iso(state.last_success_at),
        }

    def _is_new_completed(
        self,
        instrument_id: str,
        provider_id: str,
        interval: str,
        series: CandleSeries,
    ) -> bool:
        if not series.completed:
            return False
        latest = self.storage.get_intraday_latest(instrument_id, provider_id, interval)
        newest = series.completed[-1].open_time
        if newest.tzinfo is not None:
            newest = newest.replace(tzinfo=None)
        if latest is None:
            return True
        return newest > latest.bar_timestamp

    def _load_stored(
        self,
        instrument_id: str,
        provider_id: str,
        interval: str,
        limit: Optional[int],
    ) -> List[IntradayBar]:
        rows = self.storage.get_intraday_candles(
            instrument_id,
            provider_id,
            interval,
            limit=limit or self.settings.intraday_outputsize,
        )
        return [
            IntradayBar(
                timestamp=row.bar_timestamp,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        ]
