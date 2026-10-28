from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session

from app.database import get_db
from app.providers import get_data_provider
from app.providers.data_provider import compute_staleness
from app.services.market_data_storage import MarketDataStorage
from app.services.price_view import (
    bar_view,
    market_status_view,
    resolve_mode,
    stored_bar_view,
    validate_timeframe,
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

router = APIRouter(tags=["instruments"])


def _provider_failure(instrument_id: str, exc: Exception) -> HTTPException:
    provider = get_data_provider()
    return HTTPException(
        status_code=503,
        detail={
            "error": f"Data provider failed to serve data for {instrument_id}: {exc}",
            "provider_status": provider.get_status(),
        },
    )


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
            instruments = [i for i in instruments if i.market == m]
        except ValueError:
            raise HTTPException(400, f"Unknown market: {market}. Use US or HK.")

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
        "provider": provider.provider_name,
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

    start_date_obj = date.fromisoformat(start_date) if start_date else None
    end_date_obj = date.fromisoformat(end_date) if end_date else None

    bars = storage.get_bars(
        instrument_id=instrument_id,
        provider_id=provider_id,
        start_date=start_date_obj,
        end_date=end_date_obj,
        limit=limit,
    )

    metadata = storage.get_metadata(instrument_id, provider_id)

    def _labels(latest_bar_date: Optional[str], has_bars: bool, is_stale: bool):
        return {
            **resolve_mode(
                provider,
                {"data_nature": (metadata.data_nature if metadata else None) or "unknown",
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
            "data_nature": metadata.data_nature,
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

    return {
        "instrument_id": instrument_id,
        "symbol": inst.symbol,
        "name": inst.name,
        "market": inst.market.value,
        "exchange": inst.exchange.value,
        "currency": inst.currency.value,
        "timezone": inst.timezone,
        "source": "storage",
        "bar_count": len(bars),
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


@router.post("/instruments/{instrument_id}/bars/refresh")
def refresh_instrument_bars(
    instrument_id: str,
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
):
    """Force-refresh market data for an instrument from the provider.

    Fetches fresh data from the provider and updates storage. Useful for:
    - Updating stale cached data
    - Extending the cached date range
    - Ensuring latest data is available for research/backtest

    Returns the number of bars stored and data quality metadata.
    """
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

    except Exception as exc:
        storage.mark_error(instrument_id, provider.provider_id, str(exc))
        raise HTTPException(
            status_code=503,
            detail={
                "error": f"Failed to refresh data for {instrument_id}: {exc}",
                "provider_status": provider.get_status(),
            },
        )


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
