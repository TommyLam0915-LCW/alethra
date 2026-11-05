"""Test pagination support for fetching extended historical data."""
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from app.providers.kraken_provider import KrakenProvider


NOW = datetime(2025, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"


class PaginatedFakeTransport:
    """Simulates Kraken API with pagination support."""

    def __init__(self, rows_by_page, interval=1440):
        self.rows_by_page = rows_by_page
        self.interval = interval
        self.calls = []
        self.page_index = 0

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        
        if path != "/0/public/OHLC":
            return {"error": [], "result": {}}
        
        since = params.get("since")
        
        if self.page_index >= len(self.rows_by_page):
            return {"error": [], "result": {"XXBTZUSD": [], "last": None}}
        
        rows = self.rows_by_page[self.page_index]
        self.page_index += 1
        
        last_cursor = str(1000000 + self.page_index * 1000) if self.page_index < len(self.rows_by_page) else None
        
        return {
            "error": [],
            "result": {
                "XXBTZUSD": rows,
                "last": last_cursor,
            }
        }


def _make_ohlc_rows(count, end_ts, interval_minutes):
    """Generate synthetic OHLC rows aligned to the interval grid."""
    step = interval_minutes * 60
    aligned_end = (end_ts // step) * step - step
    rows = []
    for i in range(count):
        timestamp = aligned_end - (count - 1 - i) * step
        price = 50000 + i * 10
        rows.append([
            str(timestamp),
            str(price),
            str(price + 5),
            str(price - 5),
            str(price),
            str(price),
            "1.0",
            "10",
        ])
    return rows


def test_paginated_fetches_multiple_pages():
    """Pagination should fetch multiple pages when target_bars > 720."""
    end_ts = int(NOW.timestamp())
    
    page1_rows = _make_ohlc_rows(720, end_ts, 1440)
    page2_rows = _make_ohlc_rows(720, end_ts - 720 * 86400, 1440)
    page3_rows = _make_ohlc_rows(440, end_ts - 1440 * 86400, 1440)
    
    transport = PaginatedFakeTransport([page1_rows, page2_rows, page3_rows])
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
    )
    
    series = provider.get_candle_series_paginated(
        INSTRUMENT,
        interval_minutes=1440,
        target_bars=1880,
        max_pages=5,
    )
    
    assert len(transport.calls) == 3
    assert len(series.completed) == 1880
    
    first_call_params = transport.calls[0][1]
    assert "since" not in first_call_params
    
    second_call_params = transport.calls[1][1]
    assert "since" in second_call_params


def test_paginated_respects_max_pages():
    """Pagination should stop after max_pages even if target_bars not reached."""
    end_ts = int(NOW.timestamp())
    
    page_rows = [_make_ohlc_rows(720, end_ts - i * 720 * 86400, 1440) for i in range(10)]
    
    transport = PaginatedFakeTransport(page_rows)
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
    )
    
    series = provider.get_candle_series_paginated(
        INSTRUMENT,
        interval_minutes=1440,
        target_bars=5000,
        max_pages=3,
    )
    
    assert len(transport.calls) == 3
    assert len(series.completed) == 2160


def test_paginated_stops_on_empty_response():
    """Pagination should stop when API returns empty rows."""
    end_ts = int(NOW.timestamp())
    
    page1_rows = _make_ohlc_rows(720, end_ts, 1440)
    page2_rows = _make_ohlc_rows(300, end_ts - 720 * 86400, 1440)
    
    transport = PaginatedFakeTransport([page1_rows, page2_rows, []])
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
    )
    
    series = provider.get_candle_series_paginated(
        INSTRUMENT,
        interval_minutes=1440,
        target_bars=2000,
        max_pages=5,
    )
    
    assert len(transport.calls) == 3
    assert len(series.completed) == 1020


def test_paginated_validates_parameters():
    """Pagination should validate input parameters."""
    transport = PaginatedFakeTransport([])
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
    )
    
    with pytest.raises(Exception, match="target_bars must be >= 1"):
        provider.get_candle_series_paginated(INSTRUMENT, interval_minutes=1440, target_bars=0)
    
    with pytest.raises(Exception, match="max_pages must be >= 1"):
        provider.get_candle_series_paginated(INSTRUMENT, interval_minutes=1440, target_bars=1000, max_pages=0)
    
    with pytest.raises(Exception, match="Unsupported interval"):
        provider.get_candle_series_paginated(INSTRUMENT, interval_minutes=999, target_bars=1000)
