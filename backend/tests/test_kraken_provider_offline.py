"""Kraken adapter offline behaviour against a fake transport.

No socket is opened: every response is canned so the contract (venue-carrying
identity, no synthetic fallback, forming-candle separation, truncation guard,
read-only status) is pinned without touching the network.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.providers.kraken_provider import (
    MAX_OHLC_ROWS,
    KrakenApiError,
    KrakenError,
    KrakenProvider,
    KrakenRateLimitError,
    KrakenUnavailableError,
)
from app.services.upstream_budget import AllowAllBudgetGate

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
NOW = datetime(2026, 10, 30, 12, 0, 30, tzinfo=timezone.utc)
# Last minute bucket that is already closed at NOW; the next one (12:00) is the
# forming candle.
LAST_CLOSED_MINUTE = datetime(2026, 10, 30, 11, 59, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        entry = self._routes[path]
        if callable(entry):
            return entry(params)
        if isinstance(entry, Exception):
            raise entry
        return entry


def ohlc_payload(rows, key="XXBTZUSD"):
    return {"error": [], "result": {key: rows, "last": "unused"}}


def ohlc_row(ts, close="100.0", volume="0.5"):
    c = Decimal(close)
    timestamp = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)
    return [
        timestamp,
        str(c),
        str(c + Decimal("1")),
        str(c - Decimal("1")),
        str(c),
        str(c),
        volume,
        4,
    ]


def minute_rows(count=4, *, include_forming=False):
    """``count`` closed 1-minute buckets ending at 11:59, plus optional 12:00."""
    start = LAST_CLOSED_MINUTE - timedelta(minutes=count - 1)
    rows = [ohlc_row(start + timedelta(minutes=i)) for i in range(count)]
    if include_forming:
        rows.append(ohlc_row(LAST_CLOSED_MINUTE + timedelta(minutes=1)))
    return rows


def daily_rows(days):
    return [
        [int(day.timestamp()), "100.0", "101.0", "99.0", "100.0", "100.0", "1.0", 3]
        for day in days
    ]


def consecutive_days(n, start=datetime(2026, 10, 27, tzinfo=timezone.utc)):
    return [start + timedelta(days=i) for i in range(n)]


def make_provider(routes, **kwargs):
    transport = FakeTransport(routes)
    provider = KrakenProvider(
        transport=transport,
        clock=lambda: NOW,
        min_request_interval=0,
        **kwargs,
    )
    return provider, transport


def test_no_availability_before_a_successful_response():
    provider, _ = make_provider({})
    assert provider.is_available() is False
    assert provider.feed_mode == "disconnected"


def test_successful_request_marks_available_and_live():
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert provider.is_available() is True
    assert provider.feed_mode == "live"


def test_forming_candle_is_separate_from_completed():
    provider, _ = make_provider(
        {"/0/public/OHLC": ohlc_payload(minute_rows(count=4, include_forming=True))}
    )
    series = provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert series.forming is not None
    assert series.forming.is_final is False
    assert all(c.is_final for c in series.completed)
    assert series.forming.open_time not in {c.open_time for c in series.completed}


def test_unknown_instrument_is_refused_before_any_request():
    provider, transport = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    with pytest.raises(KrakenError, match="not a declared crypto instrument"):
        provider.get_candle_series("CRYPTO:KRAKEN:ETH-USD", interval_minutes=1)
    assert transport.calls == []


def test_unsupported_interval_is_refused():
    provider, transport = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    with pytest.raises(KrakenError, match="Unsupported interval"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=2)
    assert transport.calls == []


def test_limit_out_of_range_is_refused():
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    with pytest.raises(KrakenError, match="limit must be between"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1, limit=0)
    with pytest.raises(KrakenError, match="limit must be between"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1, limit=721)


def test_venueless_price_is_not_merged_from_another_key():
    # Kraken answers for a pair we did not ask about.
    provider, _ = make_provider(
        {"/0/public/OHLC": ohlc_payload(minute_rows(), key="XETHZUSD")}
    )
    with pytest.raises(KrakenApiError, match="answered for"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)


def test_altname_result_key_is_also_accepted():
    provider, _ = make_provider(
        {"/0/public/OHLC": ohlc_payload(minute_rows(), key="XBTUSD")}
    )
    series = provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert series.completed


def test_kraken_error_payload_raises_api_error():
    provider, _ = make_provider(
        {"/0/public/OHLC": {"error": ["EQuery:Unknown asset pair"], "result": {}}}
    )
    with pytest.raises(KrakenApiError, match="Unknown asset pair"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert provider.is_available() is False


def test_rate_limit_payload_raises_rate_limit_error():
    provider, _ = make_provider(
        {"/0/public/OHLC": {"error": ["EAPI:Rate limit exceeded"], "result": {}}}
    )
    with pytest.raises(KrakenRateLimitError):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)


def test_transport_failure_is_unavailable_not_synthetic():
    provider, _ = make_provider({"/0/public/OHLC": ConnectionError("boom")})
    with pytest.raises(KrakenUnavailableError):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert provider.is_available() is False
    # Three consecutive failures must keep the adapter unavailable.
    for _ in range(2):
        with pytest.raises(KrakenUnavailableError):
            provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert provider.is_available() is False


def test_negative_volume_row_is_dropped_not_zeroed():
    rows = minute_rows()
    rows[0][6] = "-5"
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(rows)})
    series = provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert len(series.completed) == len(rows) - 1
    assert series.invalid_timestamps


def test_malformed_row_shape_raises():
    rows = minute_rows()
    rows[0] = rows[0][:7]
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(rows)})
    with pytest.raises(KrakenApiError, match="row shape"):
        provider.get_candle_series(INSTRUMENT, interval_minutes=1)


def test_daily_bars_refuse_to_present_truncated_history():
    # 719 completed daily candles is the truncation window: the real start is
    # later than the caller asked for, so an error is the only honest answer.
    days = [
        datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
        for i in range(719)
    ]
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(daily_rows(days))})
    with pytest.raises(KrakenError, match="at most 720 daily candles"):
        provider.get_bars(INSTRUMENT, "2020-01-01", "2026-10-30")


def test_daily_bars_exclude_forming_day_and_filter_range():
    # 10-28..10-30; the 30th closes at 10-31 so it is still forming at NOW and
    # must not be returned even though it falls inside the requested range.
    days = consecutive_days(3, start=datetime(2026, 10, 28, tzinfo=timezone.utc))
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(daily_rows(days))})
    bars = provider.get_bars(INSTRUMENT, "2026-10-28", "2026-10-30")
    assert [b.date for b in bars] == ["2026-10-28", "2026-10-29"]


def test_get_latest_bar_returns_last_completed():
    provider, _ = make_provider(
        {"/0/public/OHLC": ohlc_payload(daily_rows(consecutive_days(3)))}
    )
    bar = provider.get_latest_bar(INSTRUMENT)
    assert bar is not None
    assert bar.date == "2026-10-29"


def test_trading_specs_read_from_assetpairs():
    payload = {
        "error": [],
        "result": {
            "XXBTZUSD": {
                "altname": "XBTUSD",
                "wsname": "XBT/USD",
                "pair_decimals": 1,
                "lot_decimals": 8,
                "tick_size": "0.1",
                "ordermin": "0.00006",
                "costmin": "0.5",
                "status": "online",
            }
        },
    }
    provider, _ = make_provider({"/0/public/AssetPairs": payload})
    specs = provider.get_trading_specs(INSTRUMENT)
    assert specs["exchange"] == "KRAKEN"
    assert specs["quote_asset"] == "USD"
    assert specs["price_precision"] == 1
    assert specs["quantity_precision"] == 8
    assert specs["tick_size"] == Decimal("0.1")
    assert specs["min_quantity"] == Decimal("0.00006")
    assert specs["min_notional"] == Decimal("0.5")
    assert specs["venue_status"] == "online"


def test_resolve_instrument_uses_exchange_spelling():
    provider, _ = make_provider({})
    assert provider.resolve_instrument(INSTRUMENT) == "XBTUSD"
    with pytest.raises(KrakenError, match="does not serve"):
        provider.resolve_instrument("CRYPTO:KRAKEN:ETH-USD")


def test_status_is_explicitly_read_only_and_not_cleared():
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    status = provider.get_status()
    assert status["read_only"] is True
    assert status["orders_supported"] is False
    assert status["redistribution"] == "NOT_CLEARED"
    assert status["declared_pairs"] == [INSTRUMENT]
    assert status["available"] is True


def test_successful_cache_avoids_duplicate_calls():
    provider, transport = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    calls_after_first = len(transport.calls)
    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    assert len(transport.calls) == calls_after_first


def test_checksum_is_deterministic():
    provider, _ = make_provider(
        {"/0/public/OHLC": ohlc_payload(daily_rows(consecutive_days(3)))}
    )
    a = provider.compute_checksum([INSTRUMENT, INSTRUMENT], "2026-10-28", "2026-10-29")
    provider.clear_cache()
    b = provider.compute_checksum([INSTRUMENT, INSTRUMENT], "2026-10-28", "2026-10-29")
    assert a == b


# -- long-history pagination -------------------------------------------------

DAILY = 1440
LAST_DAILY_CLOSE_DAY = datetime(2026, 10, 29, tzinfo=timezone.utc)


def daily_universe(days):
    """Ascending daily rows ending on the last fully closed day before NOW."""
    start = LAST_DAILY_CLOSE_DAY - timedelta(days=days - 1)
    return [ohlc_row(start + timedelta(days=i), close=str(100 + i)) for i in range(days)]


def paged_route(all_rows, page_size=MAX_OHLC_ROWS):
    """Serve ``since`` (a unix timestamp) forward from that instant, as Kraken does."""
    def handler(params):
        since = int(params.get("since", 0))
        start = next((i for i, row in enumerate(all_rows) if int(row[0]) >= since),
                     len(all_rows))
        page = all_rows[start:start + page_size]
        last = str(page[-1][0]) if page else str(since)
        return {"error": [], "result": {"XXBTZUSD": page, "last": last}}
    return handler


def make_paged_provider(all_rows, page_size=MAX_OHLC_ROWS, **kwargs):
    transport = FakeTransport({"/0/public/OHLC": paged_route(all_rows, page_size)})
    return KrakenProvider(transport=transport, clock=lambda: NOW,
                          min_request_interval=0, **kwargs), transport


def first_day(all_rows):
    return datetime.fromtimestamp(int(all_rows[0][0]), tz=timezone.utc)


def test_history_walk_pages_forward_and_spends_one_request_per_page():
    rows = daily_universe(2160)
    provider, transport = make_paged_provider(rows)
    series = provider.get_history_candle_series(
        INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
    )
    assert len(series.completed) == len(rows)
    assert len(transport.calls) == 4  # three full pages plus the empty edge page
    opens = [c.open_time for c in series.completed]
    assert opens == sorted(opens)
    assert series.duplicate_timestamps == ()
    assert series.gaps == ()


def test_history_walk_stops_at_end_without_reading_to_the_live_edge():
    rows = daily_universe(2160)
    provider, transport = make_paged_provider(rows)
    start = first_day(rows)
    series = provider.get_history_candle_series(
        INSTRUMENT, interval_minutes=DAILY, start=start, end=start + timedelta(days=800)
    )
    assert len(series.completed) == 800
    assert series.completed[-1].open_time < start + timedelta(days=800)
    assert len(transport.calls) == 2


def test_history_walk_refuses_to_present_a_short_series_as_full():
    rows = daily_universe(2160)
    provider, transport = make_paged_provider(rows)
    with pytest.raises(KrakenError, match="must not be presented as the full history"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows), max_pages=2
        )
    assert len(transport.calls) == 2


def test_history_walk_names_a_missing_head_instead_of_interpolating_it():
    rows = daily_universe(720)
    provider, _ = make_paged_provider(rows)
    with pytest.raises(KrakenError, match="starts at"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=date(2015, 1, 1), max_pages=3
        )


def test_history_walk_charges_the_budget_gate_once_per_page():
    class CountingGate:
        def __init__(self):
            self.inner = AllowAllBudgetGate()
            self.reserved = 0

        def reserve(self):
            reservation = self.inner.reserve()
            self.reserved += 1
            return reservation

    gate = CountingGate()
    rows = daily_universe(1500)
    provider, transport = make_paged_provider(rows, budget_gate=gate)
    provider.get_history_candle_series(
        INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
    )
    assert len(transport.calls) == 3
    assert gate.reserved == len(transport.calls)


def test_history_walk_rejects_an_empty_window_before_calling_the_venue():
    provider, transport = make_paged_provider(daily_universe(720))
    with pytest.raises(KrakenError, match="history window is empty"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY,
            start=date(2026, 10, 29), end=date(2026, 10, 1)
        )
    assert transport.calls == []


def test_history_walk_refuses_a_stalled_pagination_cursor():
    rows = daily_universe(2160)
    def stalled(params):
        return {"error": [], "result": {"XXBTZUSD": rows[:MAX_OHLC_ROWS], "last": "0"}}
    provider, transport = make_provider({"/0/public/OHLC": stalled})
    with pytest.raises(KrakenApiError, match="did not advance"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
        )
    assert len(transport.calls) == 1


def test_history_walk_without_a_start_takes_whatever_the_venue_lends():
    """A harvest asks for the whole shelf, so a head it cannot reach is not a failure.

    The same walk *with* a start raises (see the missing-head test): naming a
    start is a claim the series covers it, and only that claim is refused.
    """
    rows = daily_universe(720)
    provider, transport = make_paged_provider(rows)
    series = provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(series.completed) == 720
    assert series.completed[0].open_time == first_day(rows)
    assert series.completed[0].open_time.year == 2024  # decades past VENUE_FLOOR
    assert len(transport.calls) == 2  # the shelf, then the empty edge page
