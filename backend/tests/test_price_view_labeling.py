"""Contract tests for honest price-view labeling (Issue #26 §2).

A price view has to say what it is showing: which timeframe it actually
served, whether the series is synthetic / replayed / live, the exchange-local
market timestamp and the server-side receive time of each bar, and whether the
market is open, closed, stale, or disconnected.

Unit-level cases pin the calendar logic to fixed clocks so they do not drift
with the wall clock; HTTP cases run against the offline demo provider.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.providers.data_provider import OHLCVBar
from app.providers.instruments import HK_MARKET_STRUCTURE, US_MARKET_STRUCTURE
from app.services.price_view import (
    SUPPORTED_TIMEFRAMES,
    bar_view,
    market_status_view,
    provider_bar_timestamp,
    resolve_mode,
    validate_timeframe,
)

US_TZ = ZoneInfo("America/New_York")


class _StubProvider:
    """Minimal DataProvider surface that resolve_mode reads."""

    def __init__(self, feed_mode: str):
        self.feed_mode = feed_mode


def _et(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=US_TZ).astimezone(timezone.utc)


class TestTimeframeValidation:
    def test_default_is_daily(self):
        assert validate_timeframe(None) == "1d"

    def test_explicit_daily_is_canonicalised(self):
        assert validate_timeframe("1D") == "1d"

    def test_intraday_is_rejected_not_coerced(self):
        with pytest.raises(ValueError) as exc:
            validate_timeframe("5m")
        message = str(exc.value)
        assert "Unsupported timeframe '5m'" in message
        for supported in SUPPORTED_TIMEFRAMES:
            assert supported in message

    def test_every_supported_timeframe_has_documented_semantics(self):
        from app.services.price_view import TIMESTAMP_SEMANTICS

        assert set(TIMESTAMP_SEMANTICS) == set(SUPPORTED_TIMEFRAMES)


class TestBarTimestampFields:
    def test_daily_bar_is_stamped_at_exchange_local_midnight(self):
        ts = provider_bar_timestamp("2024-01-15", US_MARKET_STRUCTURE)
        assert ts is not None
        assert ts.isoformat() == "2024-01-15T00:00:00-05:00"

    def test_hk_bar_uses_hong_kong_offset(self):
        ts = provider_bar_timestamp("2024-01-15", HK_MARKET_STRUCTURE)
        assert ts is not None
        assert ts.isoformat() == "2024-01-15T00:00:00+08:00"

    def test_unparsable_date_yields_no_timestamp_instead_of_a_guess(self):
        assert provider_bar_timestamp("nonsense", US_MARKET_STRUCTURE) is None

    def test_bar_view_keeps_ohlcv_and_adds_both_timestamps(self):
        bar = OHLCVBar(date="2024-02-13", open=1.0, high=2.0, low=0.5, close=1.5, volume=10)
        received = datetime(2026, 10, 25, 6, 0, 0, tzinfo=timezone.utc)
        view = bar_view(bar, US_MARKET_STRUCTURE, "1d", received)
        assert view["date"] == "2024-02-13"
        assert (view["open"], view["high"], view["low"], view["close"], view["volume"]) == (
            1.0, 2.0, 0.5, 1.5, 10)
        assert view["market_timestamp"] == "2024-02-13T00:00:00-05:00"
        assert view["timestamp_precision"] == "1d"
        assert view["received_at"] == "2026-10-25T06:00:00+00:00"

    def test_bar_view_marks_precision_unknown_when_timestamp_missing(self):
        bar = OHLCVBar(date="bad", open=1.0, high=2.0, low=0.5, close=1.5, volume=10)
        view = bar_view(bar, US_MARKET_STRUCTURE, "1d",
                        datetime(2026, 10, 25, 6, 0, tzinfo=timezone.utc))
        assert view["market_timestamp"] is None
        assert view["timestamp_precision"] == "unknown"


class TestModeLabel:
    def test_synthetic_series_is_labelled_synthetic(self):
        provider = _StubProvider("offline")
        mode = resolve_mode(
            provider,
            {"data_nature": "synthetic", "timezone": "America/New_York"},
            timeframe="1d",
            latest_bar_date="2024-03-01",
            end_date="2026-10-25",
            now=_et(2026, 10, 25, 2, 0),
        )
        assert mode["mode"] == "synthetic_fixture"
        assert mode["mode_label_zh"] == "合成數據"
        assert mode["is_realtime_capable"] is False
        assert mode["data_as_of"] == "2024-03-01"

    def test_live_provider_serving_a_past_range_is_replay(self):
        provider = _StubProvider("live")
        mode = resolve_mode(
            provider,
            {"data_nature": "live", "timezone": "America/New_York"},
            timeframe="1d",
            latest_bar_date="2026-10-20",
            end_date="2026-10-20",
            now=_et(2026, 10, 25, 2, 0),
        )
        assert mode["mode"] == "historical_replay"
        assert mode["is_realtime_capable"] is False

    def test_live_provider_serving_through_today_is_realtime(self):
        provider = _StubProvider("live")
        mode = resolve_mode(
            provider,
            {"data_nature": "live", "timezone": "America/New_York"},
            timeframe="1d",
            latest_bar_date="2026-10-23",
            end_date="2026-10-23",
            # 02:00 UTC == 21:00 ET on 2026-10-24, so the range ends yesterday.
            now=_et(2026, 10, 24, 21, 0),
        )
        assert mode["mode"] == "historical_replay"

        mode_today = resolve_mode(
            provider,
            {"data_nature": "live", "timezone": "America/New_York"},
            timeframe="1d",
            latest_bar_date="2026-10-24",
            end_date="2026-10-24",
            now=_et(2026, 10, 24, 15, 0),
        )
        assert mode_today["mode"] == "live_realtime"
        assert mode_today["mode_label_zh"] == "即時"
        assert mode_today["is_realtime_capable"] is True

    def test_stored_series_can_never_be_labelled_realtime(self):
        provider = _StubProvider("live")
        mode = resolve_mode(
            provider,
            {"data_nature": "live", "timezone": "America/New_York"},
            timeframe="1d",
            latest_bar_date="2026-10-24",
            end_date="2026-10-24",
            now=_et(2026, 10, 24, 15, 0),
            allow_realtime=False,
        )
        assert mode["mode"] == "historical_replay"

    def test_unverified_nature_is_reported_as_unknown(self):
        provider = _StubProvider("offline")
        mode = resolve_mode(
            provider, {}, timeframe="1d",
            latest_bar_date=None, end_date=None, now=_et(2026, 10, 24, 15, 0),
        )
        assert mode["mode"] == "unknown"
        assert mode["mode_label_zh"] == "來源未確認"


class TestMarketStatus:
    def test_regular_session_is_open(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=_et(2026, 10, 21, 10, 0),
        )
        assert view["market_status"] == "open"
        assert view["market_status_label_zh"] == "開市"
        assert view["current_session"] == "Regular"
        assert view["is_regular_session"] is True
        assert view["market"] == "US"
        assert view["exchange"] == "NYSE"
        assert view["timezone"] == "America/New_York"

    def test_extended_hours_are_open_but_not_regular(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=_et(2026, 10, 21, 8, 0),
        )
        assert view["market_status"] == "open"
        assert view["current_session"] == "Pre-market"
        assert view["is_regular_session"] is False

    def test_weekend_is_closed_with_next_open(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=_et(2026, 10, 24, 12, 0),  # Saturday
        )
        assert view["market_status"] == "closed"
        assert view["market_status_reason"] == "weekend_or_holiday"
        assert view["current_session"] is None
        assert view["next_session_open"] == "2026-10-26T04:00:00-04:00"  # Monday pre-market
        assert view["next_regular_open"] == "2026-10-26T09:30:00-04:00"  # Monday regular

    def test_hk_lunch_break_opens_afternoon_same_day(self):
        view = market_status_view(
            HK_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=datetime(2026, 10, 21, 12, 30, tzinfo=ZoneInfo("Asia/Hong_Kong")).astimezone(timezone.utc),
        )
        assert view["market_status"] == "closed"
        assert view["market_status_reason"] == "outside_trading_sessions"
        assert view["next_session"] == "Afternoon"
        assert view["next_session_open"] == "2026-10-21T13:00:00+08:00"
        assert view["next_regular_open"] == "2026-10-21T13:00:00+08:00"

    def test_stale_series_is_reported_stale_while_market_is_open(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=True,
            now=_et(2026, 10, 21, 10, 0),
        )
        assert view["market_status"] == "stale"
        assert view["market_status_label_zh"] == "數據陳舊"
        # The exchange itself is still open; both facts stay visible.
        assert view["current_session"] == "Regular"

    def test_no_bars_is_reported_separately_from_stale(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=False, is_stale=True,
            now=_et(2026, 10, 21, 10, 0),
        )
        assert view["market_status"] == "no_data"

    def test_disconnected_beats_every_other_state(self):
        view = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=False, has_bars=True, is_stale=False,
            now=_et(2026, 10, 21, 10, 0),
        )
        assert view["market_status"] == "disconnected"
        assert view["market_status_label_zh"] == "供應商未連接"
        # The exchange really is open; only our feed is down. Those are separate
        # facts and the view keeps both.
        assert view["current_session"] == "Regular"

    def test_holiday_calendar_is_published_with_its_coverage(self):
        # The US calendar now carries real closures and declares the window it
        # covers. 2024-12-25 sits inside it, so it is a known closure.
        covered = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=_et(2024, 12, 25, 10, 0),
        )
        assert covered["holiday_calendar_populated"] is True
        assert covered["holiday_calendar_coverage"] == ["2023-01-01", "2025-12-31"]
        assert covered["calendar_covers_date"] is True
        assert covered["market_status"] == "closed"
        assert covered["market_status_reason"] == "market_holiday"

        # The same date two years later is outside the declared window. The
        # weekday rules still say "open", and the response now says that this
        # date is not covered instead of implying the whole calendar is known.
        uncovered = market_status_view(
            US_MARKET_STRUCTURE,
            provider_available=True, has_bars=True, is_stale=False,
            now=_et(2026, 12, 25, 10, 0),
        )
        assert uncovered["holiday_calendar_populated"] is True
        assert uncovered["calendar_covers_date"] is False
        assert uncovered["market_status"] == "open"


class TestBarsEndpoint:
    """The workspace chart reads this endpoint offline."""

    RANGE = {"start_date": "2024-01-01", "end_date": "2024-02-01"}

    def test_bars_are_labelled_synthetic_stale_daily(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL/bars", params=self.RANGE)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["timeframe"] == "1d"
        assert body["mode"] == "synthetic_fixture"
        assert body["mode_label_zh"] == "合成數據"
        assert body["is_realtime_capable"] is False
        assert body["market"] == "US"
        assert body["exchange"] == "NYSE"
        assert body["timezone"] == "America/New_York"
        assert body["currency"] == "USD"
        assert body["provider"]
        assert body["feed_mode"] == "offline"
        # The newest demo fixture bar is long past, so the view must not be
        # readable as a current quote.
        assert body["market_status"] == "stale"
        assert body["data_quality"]["is_stale"] is True
        assert body["series_received_at"].endswith("+00:00")

    def test_every_bar_carries_market_and_receive_timestamps(self, client):
        body = client.get("/api/v1/instruments/US:AAPL/bars", params=self.RANGE).json()
        assert body["bar_count"] > 0
        for bar in body["bars"]:
            assert bar["market_timestamp"].startswith(bar["date"])
            assert bar["market_timestamp"].endswith("-05:00")  # January in New York
            assert bar["timestamp_precision"] == "1d"
            assert bar["received_at"] == body["series_received_at"]
            for key in ("open", "high", "low", "close", "volume"):
                assert key in bar

    def test_intraday_request_is_rejected_instead_of_answered_with_daily_bars(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL/bars",
                          params={**self.RANGE, "timeframe": "5m"})
        assert resp.status_code == 400
        assert "Unsupported timeframe" in resp.json()["detail"]

    def test_unknown_instrument_still_404s(self, client):
        resp = client.get("/api/v1/instruments/XX:NOPE/bars", params=self.RANGE)
        assert resp.status_code == 404


class TestInstrumentDetailEndpoint:
    def test_latest_quote_is_labelled(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] in {"synthetic_fixture", "delayed", "historical_replay",
                                "live_realtime", "unknown"}
        assert body["market_status"] in {"open", "closed", "stale", "no_data",
                                         "disconnected", "unknown"}
        assert body["timeframe"] == "1d"
        assert body["received_at"].endswith("+00:00")
        if body["latest_bar"]:
            assert body["latest_bar"]["market_timestamp"]
            assert body["latest_bar"]["received_at"] == body["received_at"]
