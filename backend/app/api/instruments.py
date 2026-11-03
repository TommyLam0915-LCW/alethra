import logging

from fastapi import APIRouter, HTTPException, Depends, Query, Request
from typing import Dict, List, Optional
from datetime import date, datetime, timezone
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.lib.error_safety import safe_error_message, safe_provider_status
from app.auth.jwt import require_admin
from app.auth.safety import rate_limit
from app.models.user import User
from app.providers import get_data_provider
from app.providers.data_provider import ProviderNotConfiguredError, compute_staleness
from app.services.market_data_storage import MarketDataStorage
from app.services.price_view import (
    bar_view,
    market_status_view,
    resolve_mode,
    stored_bar_view,
    validate_timeframe,
)
from app.data.crypto_instruments import (
    CRYPTO_INSTRUMENTS,
    get_crypto_instrument,
    get_kraken_pair,
)
from app.data.real_instruments import (
    ALL_REAL_INSTRUMENTS,
    get_instrument_by_id,
    get_provider_mapping,
)
from app.providers.instruments import (
    Market,
    get_market_structure,
    parse_instrument_id,
)
from app.data import real_synthetic_data

logger = logging.getLogger(__name__)
router = APIRouter(tags=["instruments"])


def _calendar_coverage(structure) -> Optional[List[str]]:
    window = structure.calendar_coverage
    if window is None:
        return None
    return [window[0].isoformat(), window[1].isoformat()]


def _validated_refresh_range(start_date: str, end_date: str) -> None:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="start_date and end_date must be ISO dates (YYYY-MM-DD)",
        )
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="start_date must be on or before end_date",
        )


_STORED_BARS_QUERY_PARAMS = frozenset(
    {"start_date", "end_date", "provider_id", "timeframe", "limit"}
)


def _reject_unknown_query_params(
    request: Request, allowed: frozenset
) -> None:
    """Refuse a query carrying parameters this route does not read.

    FastAPI silently ignores an unrecognised parameter. A caller that filters
    with ``start``/``end`` instead of ``start_date``/``end_date`` would then get
    every stored row back and read the total as if it were the requested
    window — the 2026-11-01 observation where an empty window returned 183
    bars. Refusing the request is the only answer that cannot be mistaken for
    a filtered result.
    """
    unknown = sorted(set(request.query_params.keys()) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unrecognised query parameter(s): {', '.join(unknown)}. "
                f"Accepted: {', '.join(sorted(allowed))}. Nothing was returned."
            ),
        )


def _returned_provider_breakdown(
    bars: list, nature_map: Dict[str, str]
) -> List[dict]:
    """Group the bars actually returned by their storage provider.

    One instrument can hold rows from more than one provider. A single total
    count and one metadata row cannot explain that: the customer sees N bars
    while the metadata names a smaller stored total for a different provider.
    The breakdown is computed from the returned rows so it always sums to
    ``bar_count``.
    """
    grouped: Dict[str, dict] = {}
    for bar in bars:
        pid = getattr(bar, "provider_id", None) or "unknown"
        entry = grouped.setdefault(
            pid,
            {
                "provider_id": pid,
                "data_nature": nature_map.get(pid, "unknown"),
                "bar_count": 0,
                "first_bar_date": None,
                "last_bar_date": None,
            },
        )
        day = bar.bar_date.isoformat()
        entry["bar_count"] += 1
        entry["first_bar_date"] = entry["first_bar_date"] or day
        entry["last_bar_date"] = day
    return [grouped[pid] for pid in sorted(grouped)]


def _series_nature(breakdown: List[dict], fallback: Optional[str]) -> str:
    natures = {entry["data_nature"] for entry in breakdown} - {"unknown"}
    if len(natures) > 1:
        return "mixed"
    if natures:
        return next(iter(natures))
    return fallback or "unknown"


def _provider_failure(
    instrument_id: str,
    exc: Exception,
    prefix: str = "Data provider failed to serve data for",
) -> HTTPException:
    provider = get_data_provider()
    logger.warning("Data provider failed for %s: %r", instrument_id, exc, exc_info=exc)
    if isinstance(exc, ProviderNotConfiguredError):
        return HTTPException(
            status_code=503,
            detail={
                "error": safe_error_message(exc),
                "reason": "provider_not_configured",
                "available": False,
                "provider": provider.provider_id,
                "provider_status": safe_provider_status(provider.get_status()),
            },
        )
    return HTTPException(
        status_code=503,
        detail={
            "error": f"{prefix} {instrument_id}: {safe_error_message(exc)}",
            "provider_status": safe_provider_status(provider.get_status()),
        },
    )


def _data_status(provider) -> dict:
    """Machine-readable data-service state for the browser.

    ``not_configured`` is a normal deployment state: the catalog and stored
    bars are still readable, but nothing can be fetched from upstream and no
    demo/synthetic series is substituted.
    """
    status = provider.get_status()
    if status.get("configured") is False:
        state = "not_configured"
    elif provider.is_available():
        state = "available"
    else:
        state = "unavailable"
    return {
        "state": state,
        "reason": status.get("reason"),
        "provider_id": provider.provider_id,
        "provider_name": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "detail": status.get("detail"),
    }


def _data_coverage(
    instrument_id: str,
    provider,
    storage: MarketDataStorage,
    timeframe: str = "1d",
) -> dict:
    """What data exists for this instrument, separate from what is cached.

    ``available_*`` answers "can this provider serve bars in this window";
    ``stored_*`` answers "which bars are already in our storage". A provider
    that cannot state a per-instrument window leaves availability UNKNOWN
    instead of implying that the cached rows are all that exists.
    """
    declared = provider.get_available_range(instrument_id)
    metadata = storage.get_metadata(instrument_id, provider.provider_id)
    stored_datasets = [
        {
            "provider_id": m.provider_id,
            "data_nature": m.data_nature,
            "bar_count": m.total_bars or 0,
            "first_bar_date": (
                m.first_bar_date.isoformat() if m.first_bar_date else None
            ),
            "last_bar_date": (
                m.last_bar_date.isoformat() if m.last_bar_date else None
            ),
        }
        for m in storage.get_all_metadata(instrument_id)
    ]
    return {
        "timeframe": timeframe,
        "provider_id": provider.provider_id,
        "available_start": declared["start"] if declared else None,
        "available_end": declared["end"] if declared else None,
        "availability_source": "provider_declared" if declared else "unknown",
        "stored_start": (
            metadata.first_bar_date.isoformat()
            if metadata and metadata.first_bar_date
            else None
        ),
        "stored_end": (
            metadata.last_bar_date.isoformat()
            if metadata and metadata.last_bar_date
            else None
        ),
        "stored_bars": metadata.total_bars if metadata else 0,
        # ``stored_bars`` is the configured provider's total only. These two
        # fields make the whole storage state legible, so an available range
        # (the provider's whole universe) is never confused with the rows that
        # are actually stored, and every stored row is attributable.
        "stored_rows_total": sum(d["bar_count"] for d in stored_datasets),
        "stored_datasets": stored_datasets,
    }


def _data_window(instrument_id: str, provider) -> dict:
    """Whether this provider can serve bars for the instrument, and over what period.

    Availability only — unlike ``_data_coverage`` it never touches storage, because
    a picker labels every listed instrument and must not pay a metadata query each.
    """
    declared = provider.get_available_range(instrument_id)
    return {
        "timeframe": "1d",
        "available_start": declared["start"] if declared else None,
        "available_end": declared["end"] if declared else None,
        "availability_source": "provider_declared" if declared else "unknown",
    }


@router.get("/instruments")
def list_instruments(
    market: Optional[str] = None,
    asset_type: Optional[str] = None,
    q: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1),
):
    """List all available instruments with metadata.

    Query params:
    - market: Filter by market (US, HK)
    - asset_type: Filter by asset type (STOCK, ETF)
    - q: Case-insensitive substring match against symbol and name
    - limit: Maximum number of instruments to return (>= 1), applied
      after filtering
    """
    instruments = ALL_REAL_INSTRUMENTS

    if market:
        try:
            m = Market(market.upper())
        except ValueError:
            raise HTTPException(400, f"Unknown market: {market}. Use US, HK or CRYPTO.")
        if m is Market.CRYPTO:
            return _crypto_catalog(asset_type=asset_type, q=q, limit=limit)
        instruments = [i for i in instruments if i.market == m]

    if asset_type:
        asset_type_upper = asset_type.upper()
        instruments = [
            i for i in instruments
            if i.asset_type.value == asset_type_upper
        ]

    if q:
        needle = q.lower()
        instruments = [
            i for i in instruments
            if needle in i.symbol.lower() or needle in i.name.lower()
        ]

    if limit is not None:
        instruments = instruments[:limit]

    provider = get_data_provider()
    has_data = provider.is_available()

    return {
        "instruments": [
            {
                "instrument_id": inst.instrument_id,
                "symbol": inst.symbol,
                "name": inst.name,
                "market": inst.market.value,
                "exchange": inst.exchange.value,
                "asset_type": inst.asset_type.value,
                "currency": inst.currency.value,
                "timezone": inst.timezone,
                "lot_size": inst.lot_size,
                "lot_size_verified": inst.lot_size_verified,
                "provider_mappings": _get_provider_mappings(inst.instrument_id),
                "data_window": _data_window(inst.instrument_id, provider),
            }
            for inst in instruments
        ],
        "total": len(instruments),
        "data_available": has_data,
        "data_status": _data_status(provider),
        "provider": provider.provider_name,
        "provider_id": provider.provider_id,
        "data_disclaimer": "Price data is synthetic (demo mode) — not live market data.",
        "data_disclaimer_zh": "價格數據為模擬數據（演示模式）— 非即時市場數據。",
    }


@router.get("/instruments/{instrument_id}")
def get_instrument(instrument_id: str, db: Session = Depends(get_db)):
    """Get detailed info for a single instrument.

    instrument_id format: MARKET:SYMBOL (e.g., US:AAPL, HK:0700)
    """
    inst = get_instrument_by_id(instrument_id)
    if inst is None:
        crypto = get_crypto_instrument(instrument_id)
        if crypto is not None:
            return _crypto_instrument_detail(crypto, db)
        raise HTTPException(404, f"Instrument not found: {instrument_id}")

    provider = get_data_provider()
    structure = get_market_structure(inst.market)
    received_at = datetime.now(timezone.utc)

    try:
        bar = provider.get_latest_bar(instrument_id)
    except Exception as exc:
        raise _provider_failure(instrument_id, exc)

    data_quality = None
    latest_bar = None
    staleness: dict = {"is_stale": True}
    if bar:
        data_quality = provider.get_data_quality(instrument_id)
        data_quality["timezone"] = inst.timezone
        staleness = compute_staleness(bar.date)
        data_quality.update(staleness)
        latest_bar = bar_view(bar, structure, "1d", received_at)

    mode_fields = resolve_mode(
        provider,
        data_quality or provider.get_data_quality(instrument_id),
        timeframe="1d",
        latest_bar_date=bar.date if bar else None,
        end_date=bar.date if bar else None,
        now=received_at,
    )
    status_fields = market_status_view(
        structure,
        provider_available=provider.is_available(),
        has_bars=bool(bar),
        is_stale=bool(staleness.get("is_stale")),
        now=received_at,
    )

    return {
        "instrument_id": inst.instrument_id,
        "symbol": inst.symbol,
        "name": inst.name,
        "market": inst.market.value,
        "exchange": inst.exchange.value,
        "asset_type": inst.asset_type.value,
        "currency": inst.currency.value,
        "timezone": inst.timezone,
        "lot_size": inst.lot_size,
        "lot_size_verified": inst.lot_size_verified,
        "provider_mappings": _get_provider_mappings(instrument_id),
        "latest_bar": latest_bar,
        "data_quality": data_quality,
        "data_coverage": _data_coverage(instrument_id, provider, MarketDataStorage(db)),
        "data_source": provider.provider_name,
        "feed_mode": provider.feed_mode,
        **mode_fields,
        **status_fields,
        "received_at": received_at.isoformat(),
    }


@router.get("/instruments/{instrument_id}/bars")
def get_instrument_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
    timeframe: Optional[str] = Query(
        None, description="Bar timeframe. Only '1d' is served today."
    ),
    db: Session = Depends(get_db),
):
    """Get OHLCV bars for a specific instrument.

    instrument_id format: MARKET:SYMBOL (e.g., US:AAPL, HK:0700)

    Bars are fetched from the provider and persisted to storage for future use.

    The response states how the series should be read: which timeframe it
    actually is, whether it is synthetic/replayed/live, the exchange-local
    market timestamp and the server-side receive time of each bar, and the
    market's current state.
    """
    inst = get_instrument_by_id(instrument_id)
    if inst is None:
        raise HTTPException(404, f"Instrument not found: {instrument_id}")

    try:
        tf = validate_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    structure = get_market_structure(inst.market)
    provider = get_data_provider()
    storage = MarketDataStorage(db)

    try:
        bars = provider.get_bars(instrument_id, start_date, end_date)

        if bars:
            quality = provider.get_data_quality(instrument_id)
            storage.store_bars(
                instrument_id=instrument_id,
                provider_id=provider.provider_id,
                bars=bars,
                data_nature=quality.get("data_nature", "unknown"),
                adjustment_status=quality.get("adjustment"),
                timezone=inst.timezone,
                currency=inst.currency.value,
                provider_note=quality.get("note"),
                provider_note_zh=quality.get("note_zh"),
            )
    except ProviderNotConfiguredError as exc:
        # A normal unconfigured state must not be recorded as a provider
        # outage in storage metadata.
        raise _provider_failure(instrument_id, exc)
    except SQLAlchemyError:
        # A storage/schema failure is not a provider failure. Reporting it as
        # one hides a broken dataset behind "provider unavailable", and
        # mark_error() cannot run on a session whose transaction already failed.
        raise
    except Exception as exc:
        storage.mark_error(instrument_id, provider.provider_id, str(exc))
        raise _provider_failure(instrument_id, exc)

    series_received_at = datetime.now(timezone.utc)
    quality = provider.get_data_quality(instrument_id)
    quality["timezone"] = inst.timezone
    staleness = compute_staleness(bars[-1].date if bars else None)
    quality.update(staleness)

    return {
        "instrument_id": instrument_id,
        "symbol": inst.symbol,
        "name": inst.name,
        "market": inst.market.value,
        "exchange": inst.exchange.value,
        "currency": inst.currency.value,
        "timezone": inst.timezone,
        "provider": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "start_date": start_date,
        "end_date": end_date,
        "bar_count": len(bars),
        **resolve_mode(
            provider,
            quality,
            timeframe=tf,
            latest_bar_date=bars[-1].date if bars else None,
            end_date=end_date,
            now=series_received_at,
        ),
        **market_status_view(
            structure,
            provider_available=provider.is_available(),
            has_bars=bool(bars),
            is_stale=bool(staleness.get("is_stale")),
            now=series_received_at,
        ),
        "series_received_at": series_received_at.isoformat(),
        "data_quality": quality,
        "bars": [bar_view(b, structure, tf, series_received_at) for b in bars],
        "disclaimer": "Data may be synthetic or demo-only. Not suitable for trading decisions.",
        "disclaimer_zh": "數據可能為模擬或演示數據。不適合用於交易決策。",
    }


@router.get("/instruments/{instrument_id}/bars/stored")
def get_stored_bars(
    instrument_id: str,
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    provider_id: Optional[str] = None,
    timeframe: Optional[str] = Query(
        None, description="Bar timeframe. Only '1d' is stored today."
    ),
    limit: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Query market data directly from storage without provider fetch.

    Returns bars that have been previously fetched and persisted. Useful for:
    - Faster historical queries (no API call)
    - Backtesting with cached data
    - Checking data availability before provider fetch

    Query params:
    - start_date: Filter bars from this date (YYYY-MM-DD)
    - end_date: Filter bars until this date (YYYY-MM-DD)
    - provider_id: Filter by provider (e.g., demo, lseg)
    - timeframe: Bar timeframe; only 1d is stored, others are rejected
    - limit: Maximum number of bars to return

    ``bar_count`` is the number of rows in this response. ``providers`` splits
    that same total by storage provider, because one instrument can hold rows
    from more than one provider and a single metadata row cannot speak for all
    of them.
    """
    _reject_unknown_query_params(request, _STORED_BARS_QUERY_PARAMS)

    inst = get_instrument_by_id(instrument_id)
    if inst is None:
        raise HTTPException(404, f"Instrument not found: {instrument_id}")

    try:
        tf = validate_timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    structure = get_market_structure(inst.market)
    provider = get_data_provider()
    storage = MarketDataStorage(db)

    try:
        start_date_obj = date.fromisoformat(start_date) if start_date else None
        end_date_obj = date.fromisoformat(end_date) if end_date else None
    except (TypeError, ValueError):
        raise HTTPException(
            400, "start_date and end_date must be ISO dates (YYYY-MM-DD)"
        )
    if start_date_obj and end_date_obj and start_date_obj > end_date_obj:
        raise HTTPException(400, "start_date must be on or before end_date")

    bars = storage.get_bars(
        instrument_id=instrument_id,
        provider_id=provider_id,
        start_date=start_date_obj,
        end_date=end_date_obj,
        limit=limit,
    )

    metadata = storage.get_metadata(instrument_id, provider_id)
    nature_map = storage.get_nature_map([instrument_id]).get(instrument_id, {})
    providers = _returned_provider_breakdown(bars, nature_map)
    series_nature = _series_nature(
        providers, metadata.data_nature if metadata else None
    )

    def _labels(latest_bar_date: Optional[str], has_bars: bool, is_stale: bool):
        return {
            **resolve_mode(
                provider,
                {"data_nature": series_nature,
                 "timezone": inst.timezone},
                timeframe=tf,
                latest_bar_date=latest_bar_date,
                end_date=end_date,
                allow_realtime=False,
            ),
            **market_status_view(
                structure,
                provider_available=provider.is_available(),
                has_bars=has_bars,
                is_stale=is_stale,
            ),
        }

    if not bars and not metadata:
        return {
            "instrument_id": instrument_id,
            "symbol": inst.symbol,
            "name": inst.name,
            "market": inst.market.value,
            "exchange": inst.exchange.value,
            "currency": inst.currency.value,
            "timezone": inst.timezone,
            "source": "storage",
            "bar_count": 0,
            "bars": [],
            "metadata": None,
            **_labels(None, False, True),
            "message": "No stored data available. Fetch from provider first.",
            "message_zh": "無儲存數據。請先從數據供應商獲取。",
        }

    quality = None
    staleness = {}
    if metadata:
        quality = {
            "data_nature": series_nature,
            "metadata_data_nature": metadata.data_nature,
            "returned_bars": len(bars),
            "adjustment": metadata.adjustment_status,
            "timezone": metadata.timezone,
            "currency": metadata.currency,
            "provider_note": metadata.provider_note,
            "provider_note_zh": metadata.provider_note_zh,
            "first_bar_date": metadata.first_bar_date.isoformat() if metadata.first_bar_date else None,
            "last_bar_date": metadata.last_bar_date.isoformat() if metadata.last_bar_date else None,
            "total_bars": metadata.total_bars,
            "last_success_at": metadata.last_success_at.isoformat() if metadata.last_success_at else None,
            "health_status": metadata.health_status,
        }
        staleness = compute_staleness(metadata.last_bar_date.isoformat() if metadata.last_bar_date else None)
        quality.update(staleness)

    response = {
        "instrument_id": instrument_id,
        "symbol": inst.symbol,
        "name": inst.name,
        "market": inst.market.value,
        "exchange": inst.exchange.value,
        "currency": inst.currency.value,
        "timezone": inst.timezone,
        "source": "storage",
        "bar_count": len(bars),
        "series_nature": series_nature,
        "providers": providers,
        "data_quality": quality,
        **_labels(
            (quality or {}).get("latest_bar_date"),
            bool(bars),
            bool((staleness or {}).get("is_stale", True)),
        ),
        "bars": [stored_bar_view(bar, structure, tf) for bar in bars],
        "metadata": {
            "storage_provider": metadata.provider_id if metadata else None,
            "query_provider_filter": provider_id,
            "query_date_range": {
                "start": start_date,
                "end": end_date,
            },
        },
    }
    if not bars:
        response["message"] = "No stored bars in the requested window."
        response["message_zh"] = "所要求的時間範圍內沒有已儲存的數據。"
    return response


@router.post("/instruments/{instrument_id}/bars/refresh")
def refresh_instrument_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _rate_limit=Depends(rate_limit("market_data_refresh")),
):
    """Force-refresh market data for an instrument from the provider.

    Admin-only write path: refresh triggers outbound provider work, so it is
    gated server-side, validated, and rate limited before any provider call.
    GET read endpoints are unaffected.

    Fetches fresh data from the provider and updates storage. Useful for:
    - Updating stale cached data
    - Extending the cached date range
    - Ensuring latest data is available for research/backtest

    Returns the number of bars stored and data quality metadata.
    """
    _validated_refresh_range(start_date, end_date)

    inst = get_instrument_by_id(instrument_id)
    if inst is None:
        raise HTTPException(404, f"Instrument not found: {instrument_id}")

    provider = get_data_provider()
    storage = MarketDataStorage(db)

    try:
        bars = provider.get_bars(instrument_id, start_date, end_date)

        if not bars:
            return {
                "instrument_id": instrument_id,
                "symbol": inst.symbol,
                "name": inst.name,
                "status": "no_data",
                "bars_stored": 0,
                "message": "Provider returned no data for the requested range.",
                "message_zh": "數據供應商在請求的範圍內沒有返回數據。",
            }

        quality = provider.get_data_quality(instrument_id)
        stored_count = storage.store_bars(
            instrument_id=instrument_id,
            provider_id=provider.provider_id,
            bars=bars,
            data_nature=quality.get("data_nature", "unknown"),
            adjustment_status=quality.get("adjustment"),
            timezone=inst.timezone,
            currency=inst.currency.value,
            provider_note=quality.get("note"),
            provider_note_zh=quality.get("note_zh"),
        )

        metadata = storage.get_metadata(instrument_id, provider.provider_id)

        return {
            "instrument_id": instrument_id,
            "symbol": inst.symbol,
            "name": inst.name,
            "status": "refreshed",
            "bars_stored": stored_count,
            "provider": provider.provider_name,
            "feed_mode": provider.feed_mode,
            "data_quality": {
                "data_nature": quality.get("data_nature", "unknown"),
                "adjustment": quality.get("adjustment"),
                "timezone": inst.timezone,
                "currency": inst.currency.value,
                "provider_note": quality.get("note"),
                "provider_note_zh": quality.get("note_zh"),
            } if metadata else None,
            "storage_metadata": {
                "first_bar_date": metadata.first_bar_date.isoformat() if metadata and metadata.first_bar_date else None,
                "last_bar_date": metadata.last_bar_date.isoformat() if metadata and metadata.last_bar_date else None,
                "total_bars": metadata.total_bars if metadata else 0,
                "last_success_at": metadata.last_success_at.isoformat() if metadata and metadata.last_success_at else None,
                "health_status": metadata.health_status if metadata else None,
            } if metadata else None,
            "message": f"Successfully refreshed {stored_count} bars.",
            "message_zh": f"成功更新 {stored_count} 條數據。",
        }

    except ProviderNotConfiguredError as exc:
        raise _provider_failure(instrument_id, exc)
    except SQLAlchemyError:
        # See get_instrument_bars: a storage failure is not a provider failure.
        raise
    except Exception as exc:
        logger.warning("Data refresh failed for %s: %r", instrument_id, exc, exc_info=exc)
        storage.mark_error(instrument_id, provider.provider_id, str(exc))
        raise _provider_failure(instrument_id, exc, prefix="Failed to refresh")


@router.get("/markets")
def list_markets():
    """List supported markets with structure info."""
    markets = []
    for m in [Market.US, Market.HK]:
        structure = get_market_structure(m)
        instrument_count = sum(1 for i in ALL_REAL_INSTRUMENTS if i.market == m)
        markets.append({
            "market": m.value,
            "exchange": structure.exchange.value,
            "currency": structure.currency.value,
            "timezone": structure.timezone,
            "sessions": [
                {
                    "name": s.name,
                    "start": s.start.strftime("%H:%M"),
                    "end": s.end.strftime("%H:%M"),
                    "is_regular": s.is_regular,
                }
                for s in structure.sessions
            ],
            "holidays_count": len(structure.holidays),
            "calendar_coverage": _calendar_coverage(structure),
            "default_lot_size": structure.lot_size,
            "instrument_count": instrument_count,
        })
    return {"markets": markets}


@router.get("/markets/{market_code}")
def get_market(market_code: str):
    """Get detailed market structure for a specific market."""
    try:
        m = Market(market_code.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown market: {market_code}. Use US or HK.")

    structure = get_market_structure(m)
    instruments = [i for i in ALL_REAL_INSTRUMENTS if i.market == m]

    return {
        "market": m.value,
        "exchange": structure.exchange.value,
        "currency": structure.currency.value,
        "timezone": structure.timezone,
        "sessions": [
            {
                "name": s.name,
                "start": s.start.strftime("%H:%M"),
                "end": s.end.strftime("%H:%M"),
                "is_regular": s.is_regular,
            }
            for s in structure.sessions
        ],
        "holidays": sorted([h.isoformat() for h in structure.holidays]),
        "calendar_coverage": _calendar_coverage(structure),
        "default_lot_size": structure.lot_size,
        "instruments": [
            {
                "instrument_id": i.instrument_id,
                "symbol": i.symbol,
                "name": i.name,
                "asset_type": i.asset_type.value,
            }
            for i in instruments
        ],
    }


CRYPTO_REDISTRIBUTION = "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
CRYPTO_TERMS_NOTE_ZH = (
    "Kraken 公開行情只供本機／內部研究及模擬交易使用，未經核對不得對外重新分發。"
)
CRYPTO_AVAILABILITY_NOTE = (
    "The venue serves only its most recent ~720 candles per interval; older "
    "history cannot be paged, so the provider declares no historical window."
)


def _crypto_instrument_payload(instrument) -> dict:
    mapping = get_kraken_pair(instrument.instrument_id)
    return {
        "instrument_id": instrument.instrument_id,
        "symbol": instrument.symbol,
        "name": instrument.name,
        "market": instrument.market.value,
        "exchange": instrument.exchange.value,
        "asset_type": instrument.asset_type.value,
        "currency": instrument.currency.value,
        "timezone": instrument.timezone,
        "lot_size": None,
        "lot_size_verified": False,
        "price_precision": instrument.price_precision,
        "quantity_precision": instrument.quantity_precision,
        "min_quantity": (
            format(instrument.min_quantity, "f") if instrument.min_quantity is not None else None
        ),
        "min_notional": (
            format(instrument.min_notional, "f") if instrument.min_notional is not None else None
        ),
        "provider_mappings": (
            [
                {
                    "provider_id": mapping.provider_id,
                    "provider_symbol": mapping.provider_symbol,
                    "provider_exchange": mapping.provider_exchange,
                    "metadata": mapping.metadata,
                }
            ]
            if mapping
            else []
        ),
        "data_window": {
            "timeframe": "1d",
            "available_start": None,
            "available_end": None,
            "availability_source": "unknown",
            "availability_note": CRYPTO_AVAILABILITY_NOTE,
        },
    }


def _crypto_catalog(
    asset_type: Optional[str],
    q: Optional[str],
    limit: Optional[int],
) -> dict:
    """Crypto pairs are listed only when explicitly asked for.

    Every other catalog reader (the market-data page, the training picker)
    expects an equity bar feed and one synthetic-data disclaimer. Appending
    crypto to their default list would put a live venue series behind that
    label, so this lane requires ``market=CRYPTO``.
    """
    instruments = list(CRYPTO_INSTRUMENTS)
    if asset_type:
        wanted = asset_type.upper()
        instruments = [i for i in instruments if i.asset_type.value == wanted]
    if q:
        needle = q.lower()
        instruments = [
            i for i in instruments
            if needle in i.symbol.lower() or needle in i.name.lower()
        ]
    if limit is not None:
        instruments = instruments[:limit]

    return {
        "instruments": [_crypto_instrument_payload(i) for i in instruments],
        "total": len(instruments),
        "data_available": True,
        "data_status": {
            "state": "venue_public_data",
            "provider_id": "kraken",
            "provider_name": "Kraken",
            "feed_mode": "public_market_data_only",
            "detail": "Live public spot data; stored daily bars back research runs.",
        },
        "provider": "Kraken",
        "provider_id": "kraken",
        "redistribution": CRYPTO_REDISTRIBUTION,
        "data_disclaimer": (
            "Live public venue data (Kraken). Internal research and simulated "
            "fills only — not cleared for external redistribution."
        ),
        "data_disclaimer_zh": (
            "Kraken 公開即時行情，只供內部研究及模擬成交，未經核可不得對外重新分發。"
        ),
    }


def _crypto_coverage(instrument_id: str, db: Session) -> dict:
    storage = MarketDataStorage(db)
    metadata = storage.get_metadata(instrument_id, "kraken")
    datasets = [
        {
            "provider_id": m.provider_id,
            "data_nature": m.data_nature,
            "bar_count": m.total_bars or 0,
            "first_bar_date": m.first_bar_date.isoformat() if m.first_bar_date else None,
            "last_bar_date": m.last_bar_date.isoformat() if m.last_bar_date else None,
        }
        for m in storage.get_all_metadata(instrument_id)
    ]
    return {
        "timeframe": "1d",
        "provider_id": "kraken",
        "available_start": None,
        "available_end": None,
        "availability_source": "unknown",
        "availability_note": CRYPTO_AVAILABILITY_NOTE,
        "stored_start": (
            metadata.first_bar_date.isoformat() if metadata and metadata.first_bar_date else None
        ),
        "stored_end": (
            metadata.last_bar_date.isoformat() if metadata and metadata.last_bar_date else None
        ),
        "stored_bars": metadata.total_bars if metadata else 0,
        "stored_rows_total": sum(d["bar_count"] for d in datasets),
        "stored_datasets": datasets,
    }


def _crypto_instrument_detail(instrument, db: Session) -> dict:
    """One crypto pair as the research form reads it: identity + stored bars.

    The equity detail route asks the configured equity provider for a latest
    bar; doing that here would answer about a different lane. This branch only
    reads what storage already holds and says so.
    """
    storage = MarketDataStorage(db)
    rows = sorted(
        storage.get_bars(instrument_id=instrument.instrument_id, provider_id="kraken"),
        key=lambda r: r.bar_timestamp,
    )
    latest = rows[-1] if rows else None
    metadata = storage.get_metadata(instrument.instrument_id, "kraken")
    coverage = _crypto_coverage(instrument.instrument_id, db)
    payload = _crypto_instrument_payload(instrument)
    return {
        **payload,
        "lane": "crypto_live_research",
        "read_only": True,
        "orders_supported": False,
        "data_source": "Kraken",
        "feed_mode": "public_market_data_only",
        "latest_bar": (
            {
                "date": latest.bar_timestamp.date().isoformat(),
                "open": latest.open,
                "high": latest.high,
                "low": latest.low,
                "close": latest.close,
                "volume": (
                    format(latest.volume_decimal, "f")
                    if latest.volume_decimal is not None
                    else None
                ),
                "source": "storage",
                "stored_at": latest.fetched_at.isoformat() if latest.fetched_at else None,
            }
            if latest
            else None
        ),
        "data_quality": {
            "data_nature": metadata.data_nature if metadata else None,
            "timezone": instrument.timezone,
            "currency": instrument.currency.value,
            "last_bar_date": (
                metadata.last_bar_date.isoformat()
                if metadata and metadata.last_bar_date
                else None
            ),
            "total_bars": metadata.total_bars if metadata else 0,
            "last_success_at": (
                metadata.last_success_at.isoformat()
                if metadata and metadata.last_success_at
                else None
            ),
            "redistribution": CRYPTO_REDISTRIBUTION,
            "note_zh": CRYPTO_TERMS_NOTE_ZH,
        },
        "data_coverage": coverage,
        "redistribution": CRYPTO_REDISTRIBUTION,
        "note_zh": CRYPTO_TERMS_NOTE_ZH,
    }


def _get_provider_mappings(instrument_id: str):
    mappings = []
    for provider_id in ["lseg", "ibkr", "twelvedata"]:
        mapping = get_provider_mapping(provider_id, instrument_id)
        if mapping:
            mappings.append({
                "provider_id": mapping.provider_id,
                "provider_symbol": mapping.provider_symbol,
                "provider_exchange": mapping.provider_exchange,
                "metadata": mapping.metadata if mapping.metadata else None,
            })
    return mappings
