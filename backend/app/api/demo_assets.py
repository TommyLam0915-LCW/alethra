from fastapi import APIRouter, HTTPException
from app.providers import get_data_provider, get_provider_info
from app.providers.data_provider import compute_staleness

router = APIRouter(tags=["market-data"])


def _provider_failure(symbol: str, exc: Exception) -> HTTPException:
    provider = get_data_provider()
    return HTTPException(
        status_code=503,
        detail={
            "error": f"Data provider failed to serve data for {symbol}: {exc}",
            "provider_status": provider.get_status(),
        },
    )


@router.get("/demo-assets")
def list_demo_assets():
    provider = get_data_provider()
    assets = provider.list_assets()
    date_range = provider.get_date_range()
    return {
        "assets": [
            {
                "symbol": a.symbol,
                "description": a.description,
                "label": f"{provider.provider_name} — {a.symbol}",
            }
            for a in assets
        ],
        "date_range": date_range,
        "provider": get_provider_info(),
        "disclaimer": "Demo data — not live market data.",
        "disclaimer_zh": "模擬數據 — 非即時市場數據。",
    }


@router.get("/market-data/status")
def market_data_status():
    return get_provider_info()


@router.get("/market-data/bars/{symbol}")
def get_bars(
    symbol: str,
    start_date: str,
    end_date: str,
):
    provider = get_data_provider()
    try:
        bars = provider.get_bars(symbol, start_date, end_date)
    except Exception as exc:
        raise _provider_failure(symbol, exc)

    quality = provider.get_data_quality(symbol)
    quality.update(compute_staleness(bars[-1].date if bars else None))

    return {
        "symbol": symbol,
        "provider": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "start_date": start_date,
        "end_date": end_date,
        "bar_count": len(bars),
        "data_quality": quality,
        "bars": [
            {
                "date": b.date,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ],
        "disclaimer": "Data may be delayed, synthetic, or demo-only. Not suitable for trading decisions.",
        "disclaimer_zh": "數據可能延遲、為模擬或演示數據。不適合用於交易決策。",
    }


@router.get("/market-data/latest/{symbol}")
def get_latest_bar(symbol: str):
    provider = get_data_provider()
    try:
        bar = provider.get_latest_bar(symbol)
    except Exception as exc:
        raise _provider_failure(symbol, exc)
    if bar is None:
        return {
            "symbol": symbol,
            "provider": provider.provider_name,
            "available": False,
            "bar": None,
        }
    quality = provider.get_data_quality(symbol)
    quality.update(compute_staleness(bar.date))
    return {
        "symbol": symbol,
        "provider": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "available": True,
        "data_quality": quality,
        "bar": {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        },
    }
