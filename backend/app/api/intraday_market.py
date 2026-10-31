"""Read-only intraday market-data API with a backend-controlled refresh.

The browser never talks to the provider. It reads this endpoint; the endpoint
decides — behind a per-series lock, a minimum refresh interval and rolling
request budgets — whether anything is fetched upstream at all. A page that is
open in ten tabs therefore produces the same upstream traffic as one tab.

Nothing here invents prices. A missing key, an unverified interval, an
exhausted budget, a provider 429 or a timeout is reported as that state with the
last known good bars attached, and the client is expected to render "stale"
rather than a moving chart.

``data_rights`` is deliberately not cleared for redistribution: an API key is
not a licence to publish a venue's prices.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.jwt import require_admin
from app.auth.safety import rate_limit
from app.database import get_db
from app.models.user import User
from app.providers.data_provider import INTRADAY_INTERVALS
from app.providers.instruments import Instrument
from app.services.crypto_series import Candle, CandleSeries
from app.services.intraday_refresh import IntradayRefreshService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["intraday-market"])

MAX_SERIES_HEIGHT = 2000

DATA_RIGHTS = "INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION"
DATA_RIGHTS_NOTE_ZH = (
    "現階段只供已授權之本機／內部測試使用。擁有 API key 不等於可對外分發行情；"
    "任何公開頁面或收費展示前，須先取得該供應商之市場數據授權。"
)

TIMESTAMP_SEMANTICS = (
    "open_time is the start of the bar in UTC and is inclusive; close_time is "
    "exclusive. A bar is final only once close_time has passed. The forming bar "
    "is reported separately and is never part of a strategy input."
)

UNAVAILABILITY_POLICY = (
    "When the provider cannot be reached, the last stored bars are returned with "
    "an explicit status and stale flag; demo or synthetic bars are never "
    "substituted, and no request is retried in a loop."
)


def _instrument_payload(instrument: Instrument) -> Dict[str, Any]:
    return {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "market": instrument.market.value,
        "asset_type": instrument.asset_type.value,
        "exchange": instrument.exchange.value,
        "currency": instrument.currency.value,
        "timezone": instrument.timezone,
        "name": instrument.name,
    }


def _candle_payload(candle: Candle) -> Dict[str, Any]:
    return {
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        "open": format(candle.open, "f"),
        "high": format(candle.high, "f"),
        "low": format(candle.low, "f"),
        "close": format(candle.close, "f"),
        "volume": format(candle.volume, "f"),
        "is_final": candle.is_final,
    }


def _series_payload(series: CandleSeries, interval: str) -> Dict[str, Any]:
    return {
        "interval": interval,
        "interval_minutes": series.interval_minutes,
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "fetched_at": series.fetched_at.isoformat(),
        "completed": [_candle_payload(c) for c in series.completed],
        "forming": _candle_payload(series.forming) if series.forming else None,
        "forming_excluded_from_strategy": True,
        "integrity": {
            "fingerprint": series.fingerprint,
            "completed_count": len(series.completed),
            "duplicate_timestamps": [t.isoformat() for t in series.duplicate_timestamps],
            "off_grid_timestamps": [t.isoformat() for t in series.off_grid_timestamps],
            "invalid_timestamps": [t.isoformat() for t in series.invalid_timestamps],
            "gaps": [
                {"from": start.isoformat(), "to": end.isoformat()}
                for start, end in series.gaps
            ],
            "missing_intervals": series.missing_intervals,
            "out_of_order_rows": series.out_of_order_rows,
            "integrity_ok": series.integrity_ok,
            "last_completed_close_time": (
                series.last_completed_close_time.isoformat()
                if series.last_completed_close_time
                else None
            ),
            "stale_seconds": series.stale_seconds,
            "stale_after_seconds": series.stale_after_seconds,
            "is_stale": series.is_stale,
        },
    }


def _payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    instrument = payload["instrument"]
    series = payload["series"]
    return {
        "instrument": _instrument_payload(instrument),
        "provider_id": payload["provider_id"],
        "interval": payload["interval"],
        "interval_minutes": payload["interval_minutes"],
        "status": payload["status"],
        "refresh": payload["refresh"],
        "budget": payload["budget"],
        "market": payload["market"],
        # Newest closed bar in the series. This is *data time*, not the time of
        # the last successful request; the two answer different questions.
        "data_as_of": payload["data_as_of"],
        "provider_error": payload["provider_error"],
        "data_nature": "unknown",
        "is_realtime": False,
        "realtime_evidence": "none_verified",
        "data_rights": DATA_RIGHTS,
        "data_rights_note_zh": DATA_RIGHTS_NOTE_ZH,
        "series": _series_payload(series, payload["interval"]),
        "unavailability_policy": UNAVAILABILITY_POLICY,
    }


def _resolve(service: IntradayRefreshService, instrument_id: Optional[str], interval: Optional[str]):
    settings = service.settings
    resolved_id = (instrument_id or (settings.intraday_instrument_list or ["US:AAPL"])[0])
    resolved_interval = interval or settings.intraday_default_interval
    try:
        return service.resolve_instrument(resolved_id, resolved_interval)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "invalid_intraday_request",
                "instrument_id": resolved_id,
                "interval": resolved_interval,
                "message": str(exc),
                "allowed_instruments": settings.intraday_instrument_list,
                "supported_intervals": sorted(INTRADAY_INTERVALS),
            },
        )


@router.get("/market/intraday/capability")
def intraday_capability(db: Session = Depends(get_db)):
    """What this lane can and cannot do right now, without spending a request.

    The budget shown is read from the shared database counter, so it reports
    the spend of every process against this database, not just this worker.
    """
    service = IntradayRefreshService(db)
    return service.capability()


@router.get("/market/intraday/series")
def intraday_series(
    instrument_id: Optional[str] = Query(None),
    interval: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=MAX_SERIES_HEIGHT),
    db: Session = Depends(get_db),
    _rate_limit=Depends(rate_limit("intraday_market_read")),
):
    """Stored intraday bars, refreshed at most once per configured interval.

    Every call may trigger one upstream fetch, but only if the per-series
    minimum refresh has elapsed and the process budget allows it. Concurrent
    callers coalesce on a per-series lock, so extra tabs add no provider traffic.
    """
    service = IntradayRefreshService(db)
    instrument = _resolve(service, instrument_id, interval)
    result = service.ensure_series(
        instrument_id=instrument.instrument_id,
        interval=(interval or service.settings.intraday_default_interval),
        limit=limit,
    )
    return _payload(result)


@router.post("/market/intraday/refresh")
def intraday_refresh_now(
    instrument_id: Optional[str] = Query(None),
    interval: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=1, le=MAX_SERIES_HEIGHT),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _rate_limit=Depends(rate_limit("market_data_refresh")),
):
    """Admin-only explicit refresh.

    Same bounded path as the read endpoint, but it is the only route a caller
    can use to force a fetch; it is gated by admin auth, the shared refresh rate
    limit, the per-series minimum interval and the shared database budget. The
    forming bar is stored as non-final and can never be frozen as a closed bar.
    """
    service = IntradayRefreshService(db)
    instrument = _resolve(service, instrument_id, interval)
    result = service.ensure_series(
        instrument_id=instrument.instrument_id,
        interval=(interval or service.settings.intraday_default_interval),
        limit=limit,
    )
    body = _payload(result)
    body["requested_by"] = "admin"
    return body
