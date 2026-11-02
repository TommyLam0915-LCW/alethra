"""Read-only crypto market data API.

Everything here answers a question about what one venue printed. There is no
order path, no account key, and no substitution of demo or synthetic bars when
the venue is unreachable — a failure is reported as a failure.

The payloads carry the full identity of the series (exchange, pair, base and
quote asset, interval, timestamps, fingerprint) because a bare price is not an
instrument. They also carry the redistribution status, which is *not cleared*:
the venue's market-data terms have to be reviewed before any public or paid
surface displays these prices.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.crypto_failures import crypto_failure
from app.auth.jwt import require_admin
from app.auth.safety import rate_limit
from app.data.crypto_instruments import (
    CRYPTO_INSTRUMENTS,
    KRAKEN_BTC_USD,
    get_crypto_instrument,
)
from app.database import get_db
from app.lib.error_safety import safe_provider_status
from app.models.user import User
from app.providers.crypto_registry import get_crypto_provider
from app.providers.kraken_provider import (
    KRAKEN_INTERVALS,
    KrakenError,
)
from app.services.crypto_series import Candle, CandleSeries
from app.services.market_data_storage import MarketDataStorage

router = APIRouter(tags=["crypto-market"])

DEFAULT_INSTRUMENT_ID = KRAKEN_BTC_USD.instrument_id
MAX_SERIES_HEIGHT = 720

INTERVAL_ALIASES: Dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

MODE_LABELS_ZH = {
    "live_realtime": "即時（來源：交易所公開行情）",
    "stale": "數據陳舊",
    "disconnected": "行情來源未連線",
}

MINUTES_TO_LABEL: Dict[int, str] = {minutes: label for label, minutes in INTERVAL_ALIASES.items()}

RETENTION_NOTE = (
    "The venue returns only its most recent ~720 candles per interval. Raising "
    "the limit can reach older bars inside that window, but bars older than the "
    "window cannot be paged into with 'since'. Long-term research must accumulate "
    "what is served here into storage, or use a licensed historical source."
)
RETENTION_NOTE_ZH = (
    "交易所只提供最近約 720 根 K 線；加大 limit 只可取回此窗口內較早的資料，"
    "更舊的歷史無法透過重複設定 since 取得。長期研究須由本系統持續儲存，"
    "或改用有授權的歷史數據來源。"
)

TERMS_STATUS = "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
TERMS_NOTE_ZH = (
    "現階段只供本機／內部研究及模擬交易使用。交易所公開行情不等於可對外重新分發；"
    "對外展示或收費前，須先核對該交易所之市場數據條款。"
)


def _decimal(value: Optional[Decimal]) -> Optional[str]:
    """Decimal as a plain string so the client keeps the venue's precision."""
    if value is None:
        return None
    return format(value, "f")


def _parse_interval(interval: str) -> int:
    key = (interval or "").strip().lower()
    if key not in INTERVAL_ALIASES:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "unsupported_interval",
                "interval": interval,
                "supported": sorted(INTERVAL_ALIASES),
            },
        )
    minutes = INTERVAL_ALIASES[key]
    if minutes not in KRAKEN_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail={"reason": "venue_does_not_publish_interval", "interval": interval},
        )
    return minutes


def _require_instrument(instrument_id: str):
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason": "unknown_crypto_instrument",
                "instrument_id": instrument_id,
                "declared": [i.instrument_id for i in CRYPTO_INSTRUMENTS],
            },
        )
    return instrument


def _crypto_failure(exc: Exception) -> HTTPException:
    return crypto_failure(exc)


def _instrument_payload(instrument) -> Dict[str, Any]:
    return {
        "instrument_id": instrument.instrument_id,
        "exchange": instrument.exchange.value,
        "market": instrument.market.value,
        "asset_type": instrument.asset_type.value,
        "pair": instrument.symbol.replace("-", "/"),
        "base_asset": instrument.base_asset,
        "quote_asset": instrument.quote_asset,
        "currency": instrument.currency.value,
        "timezone": instrument.timezone,
        "price_precision": instrument.price_precision,
        "quantity_precision": instrument.quantity_precision,
        "min_quantity": _decimal(instrument.min_quantity),
        "min_notional": _decimal(instrument.min_notional),
        "name": instrument.name,
        "read_only": True,
        "orders_supported": False,
    }


def _candle_payload(candle: Candle) -> Dict[str, Any]:
    return {
        "open_time": candle.open_time.isoformat(),
        "close_time": candle.close_time.isoformat(),
        "open": _decimal(candle.open),
        "high": _decimal(candle.high),
        "low": _decimal(candle.low),
        "close": _decimal(candle.close),
        "volume": _decimal(candle.volume),
        "trade_count": candle.trade_count,
        "is_final": candle.is_final,
    }


def _series_mode(series: CandleSeries, provider) -> str:
    if not provider.is_available():
        return "disconnected"
    if series.is_stale:
        return "stale"
    return "live_realtime"


def _retention_payload(series: CandleSeries) -> Dict[str, Any]:
    oldest = series.completed[0].open_time.isoformat() if series.completed else None
    return {
        "max_bars_per_request": MAX_SERIES_HEIGHT,
        "returned_completed_count": len(series.completed),
        "oldest_returned_open_time": oldest,
        "older_history_retrievable": False,
        "note": RETENTION_NOTE,
        "note_zh": RETENTION_NOTE_ZH,
    }


def _refresh_payload(series: CandleSeries) -> Dict[str, Any]:
    # One bounded REST read per cycle. A bar becomes final only after close_time,
    # so polling faster than the venue's throttle buys nothing.
    return {
        "suggested_seconds": max(15, series.interval_minutes * 15),
        "minimum_seconds": 15,
        "note": (
            "Each cycle is one bounded REST read. The forming candle may change; "
            "a completed candle is appended only after its close_time."
        ),
    }


def _series_payload(series: CandleSeries, instrument, provider) -> Dict[str, Any]:
    mode = _series_mode(series, provider)
    data_as_of = (
        series.last_completed_close_time.isoformat()
        if series.last_completed_close_time
        else None
    )
    return {
        "instrument": _instrument_payload(instrument),
        "interval": MINUTES_TO_LABEL.get(series.interval_minutes),
        "interval_minutes": series.interval_minutes,
        "mode": mode,
        "mode_label_zh": MODE_LABELS_ZH[mode],
        "timestamp_semantics": (
            "open_time is the start of the interval in UTC; a candle is final only "
            "once close_time has passed."
        ),
        "fetched_at": series.fetched_at.isoformat(),
        "data_as_of": data_as_of,
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
        "provenance": {
            "provider_id": provider.provider_id,
            "provider_name": provider.provider_name,
            "feed_mode": provider.feed_mode,
            "data_nature": provider.get_data_quality(instrument.instrument_id)["data_nature"],
            "read_only": True,
            "redistribution": TERMS_STATUS,
            "terms_note_zh": TERMS_NOTE_ZH,
        },
        "retention": _retention_payload(series),
        "refresh": _refresh_payload(series),
        "unavailability_policy": (
            "If the venue cannot be reached the endpoint returns an error and an "
            "empty series; demo or synthetic bars are never substituted."
        ),
    }


@router.get("/market/crypto")
def crypto_market_overview():
    """Declared venue, pair, intervals and the terms status of this lane."""
    provider = get_crypto_provider()
    return {
        "lane": "crypto_research_mode",
        "stage": "local_or_internal_research",
        "read_only": True,
        "orders_supported": False,
        "instruments": [_instrument_payload(i) for i in CRYPTO_INSTRUMENTS],
        "intervals": [
            {"label": label, "minutes": minutes}
            for label, minutes in INTERVAL_ALIASES.items()
            if minutes in KRAKEN_INTERVALS
        ],
        "default_interval": "1m",
        "limits": {
            "max_bars_per_request": MAX_SERIES_HEIGHT,
            "max_stored_rows": 2000,
            "older_history_retrievable": False,
            "note": RETENTION_NOTE,
            "note_zh": RETENTION_NOTE_ZH,
        },
        "venues": [
            {
                "exchange": i.exchange.value,
                "instrument_id": i.instrument_id,
                "pair": i.symbol.replace("-", "/"),
                "quote_asset": i.quote_asset,
                "feed": "public_market_data_only",
            }
            for i in CRYPTO_INSTRUMENTS
        ],
        "redistribution": TERMS_STATUS,
        "terms_note_zh": TERMS_NOTE_ZH,
        "provider": safe_provider_status(provider.get_status()),
    }


@router.get("/market/crypto/instruments")
def crypto_instruments():
    return {"instruments": [_instrument_payload(i) for i in CRYPTO_INSTRUMENTS]}


@router.get("/market/crypto/series")
def crypto_series(
    instrument_id: str = Query(DEFAULT_INSTRUMENT_ID),
    interval: str = Query("1m"),
    limit: int = Query(200, ge=1, le=MAX_SERIES_HEIGHT),
):
    """Completed + forming candles straight from the venue (no storage read)."""
    instrument = _require_instrument(instrument_id)
    minutes = _parse_interval(interval)
    provider = get_crypto_provider()
    try:
        series = provider.get_candle_series(instrument_id, interval_minutes=minutes, limit=limit)
    except KrakenError as exc:
        raise _crypto_failure(exc)
    return _series_payload(series, instrument, provider)


@router.post("/market/crypto/snapshot")
def crypto_snapshot(
    instrument_id: str = Query(DEFAULT_INSTRUMENT_ID),
    interval: str = Query("1m"),
    limit: int = Query(500, ge=1, le=MAX_SERIES_HEIGHT),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _rate_limit=Depends(rate_limit("market_data_refresh")),
):
    """Persist completed candles for later research.

    Admin-only and rate limited: this is the only route in the crypto lane that
    writes. The forming candle is refused by storage, so a bar can never be
    frozen mid-interval.
    """
    instrument = _require_instrument(instrument_id)
    minutes = _parse_interval(interval)
    provider = get_crypto_provider()
    try:
        series = provider.get_candle_series(instrument_id, interval_minutes=minutes, limit=limit)
    except KrakenError as exc:
        raise _crypto_failure(exc)

    if not series.completed:
        return {
            "status": "no_data",
            "instrument_id": instrument_id,
            "interval_minutes": minutes,
            "message": "The venue returned no completed candles for this request.",
            "message_zh": "交易所未返回任何已完成之 K 線。",
            "integrity": _series_payload(series, instrument, provider)["integrity"],
        }

    quality = provider.get_data_quality(instrument_id)
    storage = MarketDataStorage(db)
    counts = storage.store_candles(
        instrument_id=instrument_id,
        provider_id=provider.provider_id,
        candles=series.completed,
        timezone=instrument.timezone,
        currency=instrument.currency.value,
        data_nature=quality["data_nature"],
        adjustment_status=quality["adjustment"],
        provider_note=quality["note"],
        provider_note_zh=quality["note_zh"],
    )
    metadata = storage.get_metadata(instrument_id, provider.provider_id)

    return {
        "status": "stored",
        "instrument_id": instrument_id,
        "interval_minutes": minutes,
        **counts,
        "fingerprint": series.fingerprint,
        "integrity": _series_payload(series, instrument, provider)["integrity"],
        "metadata": {
            "first_bar_date": metadata.first_bar_date.isoformat() if metadata and metadata.first_bar_date else None,
            "last_bar_date": metadata.last_bar_date.isoformat() if metadata and metadata.last_bar_date else None,
            "total_bars": metadata.total_bars if metadata else 0,
            "data_nature": metadata.data_nature if metadata else None,
            "timezone": metadata.timezone if metadata else None,
            "currency": metadata.currency if metadata else None,
        },
    }


@router.get("/market/crypto/stored")
def crypto_stored(
    instrument_id: str = Query(DEFAULT_INSTRUMENT_ID),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Read back persisted candles, labelled with the nature they were saved as."""
    instrument = _require_instrument(instrument_id)
    provider = get_crypto_provider()
    storage = MarketDataStorage(db)
    rows = storage.get_bars(
        instrument_id=instrument_id,
        provider_id=provider.provider_id,
        limit=limit,
    )
    metadata = storage.get_metadata(instrument_id, provider.provider_id)
    ordered = sorted(rows, key=lambda r: r.bar_timestamp)

    return {
        "instrument": _instrument_payload(instrument),
        "provider_id": provider.provider_id,
        "count": len(ordered),
        "stored_bars": [
            {
                "open_time": row.bar_timestamp.isoformat() + "Z",
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": _decimal(row.volume_decimal) if row.volume_decimal is not None else None,
                "fetched_at": row.fetched_at.isoformat() + "Z",
            }
            for row in ordered
        ],
        "metadata": {
            "data_nature": metadata.data_nature if metadata else None,
            "timezone": metadata.timezone if metadata else None,
            "currency": metadata.currency if metadata else None,
            "last_success_at": metadata.last_success_at.isoformat() if metadata and metadata.last_success_at else None,
            "total_bars": metadata.total_bars if metadata else 0,
        },
        "note_zh": "此為已儲存之歷史 K 線；即時價格請讀 /market/crypto/series。",
    }
