from fastapi import APIRouter
from app.providers import get_data_provider, get_provider_info

router = APIRouter(tags=["market-data"])


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
    bars = provider.get_bars(symbol, start_date, end_date)
    return {
        "symbol": symbol,
        "provider": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "start_date": start_date,
        "end_date": end_date,
        "bar_count": len(bars),
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
    bar = provider.get_latest_bar(symbol)
    if bar is None:
        return {
            "symbol": symbol,
            "provider": provider.provider_name,
            "available": False,
            "bar": None,
        }
    return {
        "symbol": symbol,
        "provider": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "available": True,
        "bar": {
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        },
    }
