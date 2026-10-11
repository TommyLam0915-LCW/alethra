from fastapi import APIRouter
from app.data.demo_data import get_demo_assets, get_date_range

router = APIRouter(tags=["demo-assets"])


@router.get("/demo-assets")
def list_demo_assets():
    assets = get_demo_assets()
    date_range = get_date_range()
    return {
        "assets": [
            {
                "symbol": a["symbol"],
                "description": a["description"],
                "label": f"Demo data — {a['symbol']}",
            }
            for a in assets
        ],
        "date_range": date_range,
        "disclaimer": "Demo data — not live market data.",
        "disclaimer_zh": "模擬數據 — 非即時市場數據。",
    }
