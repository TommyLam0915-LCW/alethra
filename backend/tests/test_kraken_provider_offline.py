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
        provider.get_candle_series("CRYPTO:KRAKEN:DOGE-USD", interval_minutes=1)
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
        provider.resolve_instrument("CRYPTO:KRAKEN:DOGE-USD")


def test_status_is_explicitly_read_only_and_not_cleared():
    provider, _ = make_provider({"/0/public/OHLC": ohlc_payload(minute_rows())})
    provider.get_candle_series(INSTRUMENT, interval_minutes=1)
    status = provider.get_status()
    assert status["read_only"] is True
    assert status["orders_supported"] is False
    assert status["redistribution"] == "NOT_CLEARED"
    assert status["declared_pairs"] == [
        "CRYPTO:KRAKEN:BTC-USD",
        "CRYPTO:KRAKEN:ETH-USD",
        "CRYPTO:KRAKEN:SOL-USD",
        "CRYPTO:KRAKEN:ETH-BTC",
    ]
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


# -- history shelf -----------------------------------------------------------

DAILY = 1440
LAST_DAILY_CLOSE_DAY = datetime(2026, 10, 29, tzinfo=timezone.utc)


def daily_universe(days):
    """Ascending daily rows ending on the last fully closed day before NOW."""
    start = LAST_DAILY_CLOSE_DAY - timedelta(days=days - 1)
    return [ohlc_row(start + timedelta(days=i), close=str(100 + i)) for i in range(days)]


def shelf_route(all_rows, page_size=MAX_OHLC_ROWS):
    """Answer the way api.kraken.com answered on 2026-11-10.

    ``since`` filters: asking from the newest bar returned only the bars at and
    after it. What ``since`` cannot do is deepen the shelf — asking from 2020
    returned the identical most recent ``page_size`` buckets. So the served
    window is "at or after since", capped at the venue's newest ``page_size``.
    """
    def handler(params):
        since = int(params.get("since", 0))
        forward = [row for row in all_rows if int(row[0]) >= since]
        page = forward[-page_size:]
        last = str(page[-1][0]) if page else str(since)
        return {"error": [], "result": {"XXBTZUSD": page, "last": last}}
    return handler


def deep_shelf_route(all_rows, page_size=MAX_OHLC_ROWS):
    """A synthetic venue that serves ``since`` forward through a deeper shelf.

    This shape is NOT Kraken — the live probe shows the real venue cannot be
    walked past its most recent 720 buckets. The route exists so the walk's own
    guards (overlapping pages, page budget, conflicts) stay reachable and can be
    pinned without claiming a venue capability.
    """
    def handler(params):
        since = int(params.get("since", 0))
        start = next((i for i, row in enumerate(all_rows) if int(row[0]) >= since),
                     len(all_rows))
        page = all_rows[start:start + page_size]
        last = str(page[-1][0]) if page else str(since)
        return {"error": [], "result": {"XXBTZUSD": page, "last": last}}
    return handler


def make_shelf_provider(all_rows, page_size=MAX_OHLC_ROWS, **kwargs):
    transport = FakeTransport({"/0/public/OHLC": shelf_route(all_rows, page_size)})
    return KrakenProvider(transport=transport, clock=lambda: NOW,
                          min_request_interval=0, **kwargs), transport


def make_deep_provider(all_rows, page_size=MAX_OHLC_ROWS, **kwargs):
    transport = FakeTransport({"/0/public/OHLC": deep_shelf_route(all_rows, page_size)})
    return KrakenProvider(transport=transport, clock=lambda: NOW,
                          min_request_interval=0, **kwargs), transport


def first_day(all_rows):
    return datetime.fromtimestamp(int(all_rows[0][0]), tz=timezone.utc)


def test_history_refuses_a_start_older_than_the_venue_shelf():
    """Kraken does not lend the ~6-year window this suite used to assume.

    Probe 2026-11-10: ``since=2020-12-11`` returned the identical newest 720
    daily buckets. A start before the shelf head is a claim the series covers
    it, so it is refused after one request instead of paging to the same answer.
    """
    rows = daily_universe(MAX_OHLC_ROWS)
    provider, transport = make_shelf_provider(rows)
    with pytest.raises(KrakenError, match="shelf starts at"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=date(2020, 12, 11)
        )
    assert len(transport.calls) == 1


def test_history_stops_at_end_without_reading_to_the_live_edge():
    rows = daily_universe(MAX_OHLC_ROWS)
    provider, transport = make_shelf_provider(rows)
    start = first_day(rows)
    series = provider.get_history_candle_series(
        INSTRUMENT, interval_minutes=DAILY, start=start, end=start + timedelta(days=300)
    )
    assert len(series.completed) == 300
    assert series.completed[-1].open_time < start + timedelta(days=300)
    assert len(transport.calls) == 1


def test_history_without_a_start_takes_whatever_the_venue_lends():
    """A harvest asks for the whole shelf, so a head it cannot reach is not a failure.

    The same walk *with* a start raises (see the missing-head test): naming a
    start is a claim the series covers it, and only that claim is refused.
    """
    rows = daily_universe(MAX_OHLC_ROWS)
    provider, transport = make_shelf_provider(rows)
    series = provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(series.completed) == MAX_OHLC_ROWS
    assert series.completed[0].open_time == first_day(rows)
    assert len(transport.calls) == 2  # the shelf, then proof it does not extend


def test_history_keeps_the_forming_candle_out_of_completed():
    shelf = daily_universe(MAX_OHLC_ROWS - 1) + [
        ohlc_row(datetime(2026, 10, 30, tzinfo=timezone.utc), close="999.0")
    ]
    provider, _ = make_shelf_provider(shelf)
    series = provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert series.forming is not None
    assert series.forming.open_time == datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert series.forming.open_time not in {c.open_time for c in series.completed}
    assert series.completed[-1].open_time < series.forming.open_time


def test_history_rejects_an_empty_window_before_calling_the_venue():
    provider, transport = make_shelf_provider(daily_universe(MAX_OHLC_ROWS))
    with pytest.raises(KrakenError, match="history window is empty"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY,
            start=date(2026, 10, 29), end=date(2026, 10, 1)
        )
    with pytest.raises(KrakenError, match="history window is empty"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY,
            start=date(2026, 10, 1), end=date(2026, 10, 1)
        )
    assert transport.calls == []


def test_history_charges_the_budget_gate_once_per_request():
    class CountingGate:
        def __init__(self):
            self.inner = AllowAllBudgetGate()
            self.reserved = 0

        def reserve(self):
            reservation = self.inner.reserve()
            self.reserved += 1
            return reservation

    gate = CountingGate()
    provider, transport = make_shelf_provider(
        daily_universe(MAX_OHLC_ROWS), budget_gate=gate
    )
    provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(transport.calls) == 2
    assert gate.reserved == len(transport.calls)


# -- the page shape the live venue serves ------------------------------------
#
# Probed against api.kraken.com on 2026-11-10: a full daily shelf answered with
# 721 rows — 720 closed buckets plus the bucket still being formed — while
# ``last`` named 2026-11-09, the newest CLOSED bucket rather than the newest row
# returned. Feeding that cursor back as ``since`` therefore re-serves a row
# older than the newest row already in hand. Bounding the walk on ``seen[-1]``
# refused every such harvest, so these routes pin the observed shape instead of
# a fixture's idea of it.

FORMING_DAY = datetime(2026, 10, 30, tzinfo=timezone.utc)
FORMING_TS = int(FORMING_DAY.timestamp())


def venue_shelf(closed_days, forming_close="999.0"):
    """``closed_days`` closed buckets plus the bucket still forming at NOW."""
    return daily_universe(closed_days) + [
        ohlc_row(FORMING_DAY, close=forming_close)
    ]


def venue_route(all_rows, page_size=MAX_OHLC_ROWS):
    def handler(params):
        since = int(params.get("since", 0))
        forward = [row for row in all_rows if int(row[0]) >= since]
        closed = [row for row in forward if int(row[0]) < FORMING_TS]
        forming = [row for row in forward if int(row[0]) >= FORMING_TS]
        page = closed[-page_size:] + forming
        # ``last`` is the newest closed bucket, even though the page carries a
        # newer row on top of it.
        last = str(closed[-1][0]) if closed else str(since)
        return {"error": [], "result": {"XXBTZUSD": page, "last": last}}
    return handler


def make_venue_provider(all_rows, **kwargs):
    transport = FakeTransport({"/0/public/OHLC": venue_route(all_rows)})
    return KrakenProvider(transport=transport, clock=lambda: NOW,
                          min_request_interval=0, **kwargs), transport


def test_harvest_walks_a_shelf_whose_cursor_names_the_closed_bucket():
    """The 2026-11-10 live failure: page 2 legitimately re-serves 2026-10-29."""
    shelf = venue_shelf(MAX_OHLC_ROWS)
    assert len(shelf) == MAX_OHLC_ROWS + 1
    provider, transport = make_venue_provider(shelf)
    series = provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(series.completed) == MAX_OHLC_ROWS
    assert series.forming is not None
    assert series.forming.open_time == FORMING_DAY
    assert series.duplicate_timestamps == ()
    assert len(transport.calls) == 2


def test_forming_bucket_may_change_between_pages_without_being_a_conflict():
    """A bucket still forming is expected to move; the first read is kept."""
    shelf = venue_shelf(MAX_OHLC_ROWS)
    serve = venue_route(shelf)
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        payload = serve(params)
        if calls["n"] == 2:
            payload["result"]["XXBTZUSD"][-1] = ohlc_row(FORMING_DAY, close="500.0")
        return payload

    provider, transport = make_provider({"/0/public/OHLC": handler})
    series = provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(transport.calls) == 2
    assert series.forming is not None
    assert series.forming.open == Decimal("999.0")
    assert len(series.completed) == MAX_OHLC_ROWS


def test_re_served_closed_bucket_with_different_content_is_still_refused():
    """The forming exemption must not become a blanket exemption for overlaps."""
    shelf = venue_shelf(MAX_OHLC_ROWS)
    serve = venue_route(shelf)
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        payload = serve(params)
        if calls["n"] == 2:
            payload["result"]["XXBTZUSD"][0] = ohlc_row(
                LAST_DAILY_CLOSE_DAY, close="500.0"
            )
        return payload

    provider, transport = make_provider({"/0/public/OHLC": handler})
    with pytest.raises(KrakenApiError, match="conflicting content for closed bar"):
        provider.get_history_candle_series(INSTRUMENT, interval_minutes=DAILY)
    assert len(transport.calls) == 2


def test_paginated_collects_the_observed_page_shape():
    shelf = venue_shelf(MAX_OHLC_ROWS)
    provider, transport = make_venue_provider(shelf)
    series = provider.get_candle_series_paginated(
        INSTRUMENT, interval_minutes=DAILY, target_bars=MAX_OHLC_ROWS + 1
    )
    assert len(series.completed) == MAX_OHLC_ROWS
    assert series.forming is not None
    assert len(transport.calls) == 1


# -- walk guards on a shelf deeper than Kraken serves -------------------------
#
# These routes are synthetic: the live probe showed Kraken answers with its 720
# most recent buckets and no ``since`` reaches further. They exist so the walk's
# own guards stay reachable, and passing them proves nothing about venue depth.

def test_walk_merges_overlapping_pages_without_duplicates_or_order_damage():
    rows = daily_universe(2160)
    provider, transport = make_deep_provider(rows)
    series = provider.get_history_candle_series(
        INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
    )
    assert len(series.completed) == len(rows)
    assert len(transport.calls) == 4  # three full pages plus the repeat that ends the walk
    opens = [c.open_time for c in series.completed]
    assert opens == sorted(opens)
    assert series.duplicate_timestamps == ()
    assert series.gaps == ()


def test_walk_refuses_to_present_a_short_series_as_full():
    rows = daily_universe(2160)
    provider, transport = make_deep_provider(rows)
    with pytest.raises(KrakenError, match="must not be presented as the full history"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows), max_pages=2
        )
    assert len(transport.calls) == 2


def test_walk_refuses_a_repeated_bucket_with_conflicting_content():
    """De-duplication must not quietly choose which price the ledger trusts."""
    rows = daily_universe(2 * MAX_OHLC_ROWS)
    overlap = rows[MAX_OHLC_ROWS - 1]
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        if calls["n"] == 1:
            page = rows[:MAX_OHLC_ROWS]
        else:
            page = list(rows[MAX_OHLC_ROWS - 1:])
            page[0] = ohlc_row(
                datetime.fromtimestamp(int(overlap[0]), tz=timezone.utc), close="500.0"
            )
        return {"error": [], "result": {"XXBTZUSD": page, "last": str(page[-1][0])}}

    provider, transport = make_provider({"/0/public/OHLC": handler})
    with pytest.raises(KrakenApiError, match="conflicting content"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
        )
    assert len(transport.calls) == 2


def test_walk_refuses_a_page_that_runs_backwards():
    rows = daily_universe(2160)
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        page = rows[:MAX_OHLC_ROWS] if calls["n"] == 1 else rows[700:700 + MAX_OHLC_ROWS]
        return {"error": [], "result": {"XXBTZUSD": page, "last": str(page[-1][0])}}

    provider, transport = make_provider({"/0/public/OHLC": handler})
    with pytest.raises(KrakenApiError, match="went backwards"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows)
        )
    assert len(transport.calls) == 2


@pytest.mark.parametrize("cursor_offset", [0, -86_400], ids=["stalled", "regressed"])
def test_walk_refuses_a_cursor_that_does_not_move_forward(cursor_offset):
    """A venue still handing out new bars while the cursor stands still cannot
    be trusted to have finished; looping on it would burn budget forever."""
    rows = daily_universe(3 * MAX_OHLC_ROWS)

    def handler(params):
        since = int(params["since"])
        start = next((i for i, r in enumerate(rows) if int(r[0]) >= since), len(rows))
        page = rows[start:start + MAX_OHLC_ROWS]
        return {"error": [], "result": {"XXBTZUSD": page,
                                        "last": str(since + cursor_offset)}}

    provider, transport = make_provider({"/0/public/OHLC": handler})
    with pytest.raises(KrakenApiError, match="did not advance"):
        provider.get_history_candle_series(
            INSTRUMENT, interval_minutes=DAILY, start=first_day(rows), max_pages=5
        )
    assert len(transport.calls) == 1


def test_paginated_refuses_when_the_venue_cannot_lend_target_bars():
    """strategy_lab asks for target_bars above 720; Kraken cannot deliver it."""
    rows = daily_universe(MAX_OHLC_ROWS)
    provider, transport = make_shelf_provider(rows)
    with pytest.raises(KrakenError, match="must not be presented as the requested history"):
        provider.get_candle_series_paginated(
            INSTRUMENT, interval_minutes=DAILY, target_bars=2000
        )
    assert len(transport.calls) == 2


def test_paginated_returns_the_target_when_the_shelf_covers_it():
    shelf = daily_universe(MAX_OHLC_ROWS - 1) + [
        ohlc_row(datetime(2026, 10, 30, tzinfo=timezone.utc), close="999.0")
    ]
    provider, transport = make_shelf_provider(shelf)
    series = provider.get_candle_series_paginated(
        INSTRUMENT, interval_minutes=DAILY, target_bars=500
    )
    # target_bars counts buckets: the newest one is still forming at NOW.
    assert len(series.completed) == 499
    assert series.forming is not None
    assert len(transport.calls) == 1
