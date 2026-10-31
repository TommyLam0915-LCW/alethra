"""Bounded intraday REST refresh — offline contract tests.

Every provider here is a stub. These tests prove the *contract* (storage
identity, finality, coalescing, budgets, backoff, honest status, API auth), not
that any real account is entitled to intraday data. A real-provider result, when
one exists, is reported separately and is never merged into "live passed".
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from dataclasses import replace
from typing import List, Optional, Tuple

import pytest

from app.config import get_settings
from app.models.market_data import IntradayMarketDataBar, MarketDataBar
from app.providers.data_provider import (
    DataProvider,
    IntradayBar,
    OHLCVBar,
    ProviderRateLimitError,
)
from app.providers.instruments import US_MARKET_STRUCTURE
from app.services import intraday_refresh as refresh_mod
from app.services.crypto_series import Candle
from app.services.intraday_refresh import IntradayRefreshService, reset_intraday_state
from app.services.intraday_series import market_session_view
from app.services.market_data_storage import MarketDataStorage
from tests.conftest import TestSession

# 2026-10-01 is a Thursday; 14:02 UTC is inside the 13:30–20:00 UTC regular
# session (EDT), so a 1-minute bar opened at 14:02 closes at 14:03.
INSTRUMENT = "US:AAPL"
FIXED_SETTINGS = dict(
    data_provider="twelvedata",
    intraday_enabled=True,
    intraday_default_interval="1min",
    intraday_refresh_seconds=60,
    intraday_outputsize=390,
    intraday_instruments="US:AAPL",
    intraday_verified_intervals="1min",
    intraday_max_requests_per_hour=60,
    intraday_max_requests_per_day=400,
    intraday_backoff_base_seconds=30,
    intraday_backoff_max_seconds=300,
)


def ts(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 1, hour, minute, second)


def utc(moment: datetime) -> datetime:
    """The service clock is tz-aware; storage and provider rows are naive UTC."""
    return moment.replace(tzinfo=timezone.utc)


def bar(minute: int, *, price: float = 100.0, volume: Optional[float] = 1000.0) -> IntradayBar:
    return IntradayBar(
        timestamp=ts(14, minute),
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price + 0.5,
        volume=volume,
    )


def candle(minute: int, *, final: bool, close: str = "100.5", volume: str = "1000") -> Candle:
    return Candle(
        instrument_id=INSTRUMENT,
        interval_minutes=1,
        open_time=ts(14, minute),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=Decimal(volume),
        trade_count=None,
        is_final=final,
    )


class FakeIntradayProvider(DataProvider):
    """Deterministic in-memory provider. Records every upstream call."""

    def __init__(
        self,
        bars: Optional[List[IntradayBar]] = None,
        *,
        provider_id: str = "twelvedata",
        error: Optional[BaseException] = None,
        available: bool = True,
        supports: bool = True,
        declared: Tuple[str, ...] = ("1min",),
        status: Optional[dict] = None,
    ) -> None:
        self.bars: List[IntradayBar] = list(bars or [])
        self.error = error
        self.available = available
        self.supports = supports
        self.declared = declared
        self.calls: List[Tuple[str, str, int]] = []
        self._provider_id = provider_id
        self._status = status

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def provider_name(self) -> str:
        return "Fake Intraday"

    @property
    def feed_mode(self) -> str:
        return "fake"

    def list_assets(self):
        return []

    def get_bars(self, symbol, start_date, end_date):
        return []

    def get_latest_bar(self, symbol):
        return None

    def get_date_range(self):
        return {}

    def compute_checksum(self, symbols, start_date, end_date):
        return "fake"

    def is_available(self) -> bool:
        return self.available

    def supports_intraday(self) -> bool:
        return self.supports

    def declared_intraday_intervals(self) -> Tuple[str, ...]:
        return self.declared

    def get_status(self):
        if self._status is not None:
            return self._status
        return super().get_status()

    def get_intraday_bars(self, symbol, interval, *, outputsize=390):
        self.calls.append((symbol, interval, outputsize))
        if self.error is not None:
            raise self.error
        return list(self.bars)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_intraday_state():
    reset_intraday_state()
    yield
    reset_intraday_state()


def configure(monkeypatch, provider, **overrides):
    """Point the refresh service at a stub provider and explicit settings."""
    values = dict(FIXED_SETTINGS)
    values.update(overrides)
    settings = get_settings().model_copy(update=values)
    monkeypatch.setattr(refresh_mod, "get_settings", lambda: settings)
    if isinstance(provider, BaseException):
        def _raise():
            raise provider
        monkeypatch.setattr(refresh_mod, "get_data_provider", _raise)
    else:
        monkeypatch.setattr(refresh_mod, "get_data_provider", lambda: provider)
    return settings


# --------------------------------------------------------------------------
# Storage identity, finality, and locked-research isolation
# --------------------------------------------------------------------------


def test_same_bar_updates_one_row_in_place(db):
    storage = MarketDataStorage(db)
    storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=False, close="100.5")],
    )
    counts = storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=True, close="101.5")],
    )
    rows = storage.get_intraday_candles(INSTRUMENT, "twelvedata", "1min")
    assert len(rows) == 1, "a closing bar must update its own row, not add a candle"
    assert rows[0].is_final is True
    assert rows[0].close == pytest.approx(101.5)
    assert counts["updated"] == 1 and counts["stored"] == 0


def test_closed_bar_never_regresses_to_forming(db):
    storage = MarketDataStorage(db)
    storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=True, close="101.0")],
    )
    counts = storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=False, close="99.0")],
    )
    row = storage.get_intraday_latest(INSTRUMENT, "twelvedata", "1min")
    assert row.is_final is True
    assert row.close == pytest.approx(101.0)
    assert counts["skipped_forming_regression"] == 1


def test_duplicate_and_out_of_order_rows_do_not_double_insert(db):
    storage = MarketDataStorage(db)
    counts = storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[
            candle(7, final=True),
            candle(5, final=True),
            candle(6, final=True),
            candle(6, final=True),  # duplicate timestamp in one response
        ],
    )
    rows = storage.get_intraday_candles(INSTRUMENT, "twelvedata", "1min")
    assert [r.bar_timestamp for r in rows] == [ts(14, 5), ts(14, 6), ts(14, 7)]
    assert counts["stored"] == 3


def test_identity_is_instrument_provider_timeframe_timestamp(db):
    storage = MarketDataStorage(db)
    same = candle(5, final=True)
    for provider in ("twelvedata", "other"):
        for timeframe in ("1min", "5min"):
            storage.store_intraday_candles(
                instrument_id=INSTRUMENT,
                provider_id=provider,
                timeframe=timeframe,
                candles=[same],
            )
    rows = db.query(IntradayMarketDataBar).all()
    assert len(rows) == 4, "one timestamp in four identities is four rows"


def test_intraday_poll_does_not_rewrite_research_bars(db):
    storage = MarketDataStorage(db)
    storage.store_bars(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        bars=[OHLCVBar(date="2026-09-30", open=10, high=11, low=9, close=10.5, volume=100)],
        data_nature="unknown",
    )
    before = db.query(MarketDataBar).one()

    storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=True, close="500.0")],
    )
    db.refresh(before)
    assert before.close == pytest.approx(10.5), "live polls cannot rewrite research bars"


def test_unknown_provider_volume_is_stored_as_null(db):
    storage = MarketDataStorage(db)
    storage.store_intraday_candles(
        instrument_id=INSTRUMENT,
        provider_id="twelvedata",
        timeframe="1min",
        candles=[candle(5, final=True), candle(6, final=True)],
        volume_overrides={ts(14, 5): None, ts(14, 6): 1234.0},
    )
    rows = storage.get_intraday_candles(INSTRUMENT, "twelvedata", "1min")
    assert rows[0].volume is None, "absent volume must not become zero shares"
    assert rows[1].volume == 1234


# --------------------------------------------------------------------------
# Refresh service gates and lifecycle
# --------------------------------------------------------------------------


def _service(db) -> IntradayRefreshService:
    return IntradayRefreshService(db)


def test_disabled_lane_never_calls_upstream(db, monkeypatch):
    provider = FakeIntradayProvider([bar(3)])
    configure(monkeypatch, provider, intraday_enabled=False)
    result = _service(db).ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    assert result["status"] == "disabled"
    assert provider.calls == []
    assert result["refresh"]["attempted_upstream"] is False


def test_missing_provider_reports_not_configured_without_fake_data(db, monkeypatch):
    configure(monkeypatch, ValueError("TWELVEDATA_API_KEY is not set"))
    result = _service(db).ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5)))
    assert result["status"] == "not_configured"
    assert result["provider_error"]
    assert result["series"].completed == ()


def test_adapter_gates_are_reported_separately(db, monkeypatch):
    not_capable = FakeIntradayProvider([bar(3)], supports=False)
    configure(monkeypatch, not_capable)
    assert _service(db).ensure_series(
        instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5))
    )["status"] == "provider_not_intraday_capable"
    assert not_capable.calls == []

    undeclared = FakeIntradayProvider([bar(3)], declared=())
    configure(monkeypatch, undeclared)
    assert _service(db).ensure_series(
        instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5))
    )["status"] == "interval_not_declared"

    unverified = FakeIntradayProvider([bar(3)])
    configure(monkeypatch, unverified, intraday_verified_intervals="")
    assert _service(db).ensure_series(
        instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5))
    )["status"] == "unverified_interval"
    assert unverified.calls == []


def test_unmapped_symbol_is_refused_before_any_request(db, monkeypatch):
    provider = FakeIntradayProvider([bar(3)], provider_id="no_such_provider")
    configure(monkeypatch, provider)
    result = _service(db).ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5)))
    assert result["status"] == "symbol_not_mapped"
    assert provider.calls == []


def test_provider_symbol_mapping_is_used(db, monkeypatch):
    provider = FakeIntradayProvider([bar(3), bar(4)])
    configure(monkeypatch, provider)
    result = _service(db).ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    assert result["status"] == "new_bar"
    assert provider.calls[0][0] == "AAPL", "internal id US:AAPL is not a provider symbol"
    assert provider.calls[0][1] == "1min"


def test_concurrent_reads_coalesce_into_one_upstream_call(db, monkeypatch):
    provider = FakeIntradayProvider([bar(3), bar(4)])
    configure(monkeypatch, provider)
    service = _service(db)
    first = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    second = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 40)))
    assert first["status"] == "new_bar"
    assert second["status"] == "cache_fresh"
    assert len(provider.calls) == 1, "extra readers must not multiply upstream traffic"
    assert second["refresh"]["upstream_requests"] == 0


def test_shared_budget_stops_further_requests(db, monkeypatch):
    provider = FakeIntradayProvider([bar(3)])
    configure(monkeypatch, provider, intraday_max_requests_per_hour=1)
    service = _service(db)
    service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    blocked = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 7, 30)))
    assert blocked["status"] == "budget_exhausted"
    assert blocked["refresh"]["attempted_upstream"] is False
    assert blocked["refresh"]["budget"]["scope"] == "database"
    assert len(provider.calls) == 1


def test_budget_spent_by_one_process_blocks_another(db, monkeypatch):
    """Two sessions model two workers sharing one database.

    A process-local list cannot see its sibling's spend, so the remainder of
    the hourly budget must come from the shared counter table.
    """
    provider = FakeIntradayProvider([bar(3), bar(4)])
    configure(monkeypatch, provider, intraday_max_requests_per_hour=1)
    other_worker = TestSession()
    try:
        first = _service(db).ensure_series(
            instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30))
        )
        sibling = _service(other_worker).ensure_series(
            instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 7, 30))
        )
    finally:
        other_worker.close()
    assert first["status"] == "new_bar"
    assert sibling["status"] == "budget_exhausted"
    assert sibling["refresh"]["attempted_upstream"] is False
    assert len(provider.calls) == 1, "one shared budget, not one budget per process"


def test_rate_limit_honours_retry_after_and_backs_off(db, monkeypatch):
    provider = FakeIntradayProvider(
        error=ProviderRateLimitError("429 too many requests", retry_after_seconds=120)
    )
    configure(monkeypatch, provider)
    service = _service(db)
    first = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    assert first["status"] == "rate_limited"
    assert first["refresh"]["retry_after_seconds"] == 120

    second = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 6, 40)))
    assert second["status"] == "backoff"
    assert second["refresh"]["retry_after_seconds"] == 50
    assert len(provider.calls) == 1, "a 429 must not be retried in a loop"


def test_upstream_failure_keeps_old_bars_and_marks_stale(db, monkeypatch):
    provider = FakeIntradayProvider([bar(2), bar(3), bar(4)])
    configure(monkeypatch, provider)
    service = _service(db)
    ok = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))
    assert ok["status"] == "new_bar"
    assert ok["series"].is_stale is False

    provider.error = TimeoutError("provider timed out")
    failed = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 20, 30)))
    assert failed["status"] == "upstream_error"
    assert len(failed["series"].completed) == 3, "stored bars survive an upstream failure"
    assert failed["series"].is_stale is True
    assert failed["data_as_of"] == utc(ts(14, 5)).isoformat()
    assert failed["refresh"]["last_attempt_at"] != failed["data_as_of"]


def test_new_bar_vs_unchanged_is_distinguished(db, monkeypatch):
    provider = FakeIntradayProvider([bar(2), bar(3), bar(4)])
    configure(monkeypatch, provider)
    service = _service(db)
    service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30)))

    provider.bars = [bar(2), bar(3), bar(4), bar(5)]
    # Past the 60s minimum refresh window, and bar 5 has closed by 14:06:41.
    appended = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 6, 41)))
    assert appended["status"] == "new_bar"
    assert appended["refresh"]["last_success_added_bar"] is True

    unchanged = service.ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 7, 45)))
    assert unchanged["status"] == "no_change"
    assert unchanged["refresh"]["last_success_added_bar"] is False


def test_forming_bar_is_separated_from_closed_bars(db, monkeypatch):
    provider = FakeIntradayProvider([bar(2), bar(3), bar(4), bar(5)])
    configure(monkeypatch, provider)
    result = _service(db).ensure_series(
        instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5, 30))
    )
    series = result["series"]
    assert [c.open_time for c in series.completed] == [
        utc(ts(14, 2)), utc(ts(14, 3)), utc(ts(14, 4))
    ]
    assert series.forming is not None
    assert series.forming.open_time == utc(ts(14, 5))
    assert series.forming not in series.bars_for_strategy


def test_empty_provider_response_is_no_data_not_a_success(db, monkeypatch):
    provider = FakeIntradayProvider([])
    configure(monkeypatch, provider)
    result = _service(db).ensure_series(instrument_id=INSTRUMENT, interval="1min", now=utc(ts(14, 5)))
    assert result["status"] == "no_data"
    assert result["series"].completed == ()
    assert result["refresh"]["last_attempt_ok"] is True


# --------------------------------------------------------------------------
# Session state honesty: an empty holiday calendar is never guessed as open
# --------------------------------------------------------------------------


def test_session_state_does_not_guess_open_without_a_holiday_calendar():
    # Thursday 14:02 UTC = 10:02 ET, inside the regular session.
    view = market_session_view(US_MARKET_STRUCTURE, now=utc(ts(14, 2)))
    assert view["session_status"] == "unknown_holiday_calendar"
    assert view["holiday_calendar_complete"] is False


def test_session_state_reports_closed_outside_the_window_without_a_calendar():
    # Thursday 12:00 UTC = 08:00 ET, before the regular open.
    view = market_session_view(US_MARKET_STRUCTURE, now=utc(ts(12, 0)))
    assert view["session_status"] == "closed"


def test_session_state_reports_weekend():
    saturday = datetime(2026, 10, 3, 14, 2, tzinfo=timezone.utc)
    view = market_session_view(US_MARKET_STRUCTURE, now=saturday)
    assert view["session_status"] == "closed_weekend"


def test_session_state_with_a_complete_calendar_distinguishes_holiday_and_open():
    calendar = replace(US_MARKET_STRUCTURE, holidays={date(2026, 10, 1)})
    holiday = market_session_view(calendar, now=utc(ts(14, 2)))
    assert holiday["session_status"] == "closed_holiday"
    assert holiday["holiday_calendar_complete"] is True

    friday = utc(datetime(2026, 10, 2, 14, 2))
    open_day = market_session_view(calendar, now=friday)
    assert open_day["session_status"] == "open"


# --------------------------------------------------------------------------
# HTTP surface: auth, validation, honest labels, no credential echo
# --------------------------------------------------------------------------


def test_capability_is_honest_about_permissions(client, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(3)]))
    resp = client.get("/api/v1/market/intraday/capability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_nature"] == "unknown"
    assert body["is_realtime"] is False
    assert body["realtime_evidence"] == "none_verified"
    assert body["data_rights"] == "INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION"
    assert body["market_calendar_complete"] is False
    assert body["declared_intervals"] == ["1min"]


def test_capability_reports_missing_key_without_echoing_it(client, monkeypatch):
    configure(monkeypatch, ValueError("TWELVEDATA_API_KEY is absent"))
    resp = client.get("/api/v1/market/intraday/capability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider_connected"] is False
    assert body["adapter_supports_intraday"] is False
    assert body["provider_error"]


def test_series_requires_authentication(client, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(3)]))
    resp = client.get("/api/v1/market/intraday/series", params={"instrument_id": INSTRUMENT})
    assert resp.status_code in (401, 403)


def test_series_rejects_unsupported_interval(client, auth_headers, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(3)]))
    resp = client.get(
        "/api/v1/market/intraday/series",
        params={"instrument_id": INSTRUMENT, "interval": "1h"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["reason"] == "invalid_intraday_request"
    assert "1min" in detail["supported_intervals"]


def test_series_reports_disabled_state(client, auth_headers, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(3)]), intraday_enabled=False)
    resp = client.get(
        "/api/v1/market/intraday/series",
        params={"instrument_id": INSTRUMENT, "interval": "1min"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_series_payload_distinguishes_data_time_and_fetch_time(client, auth_headers, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(2), bar(3), bar(4)]))
    resp = client.get(
        "/api/v1/market/intraday/series",
        params={"instrument_id": INSTRUMENT, "interval": "1min"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["series"]["forming_excluded_from_strategy"] is True
    assert "data_as_of" in body
    assert body["refresh"]["last_attempt_at"] is not None
    assert body["data_nature"] == "unknown"


def test_admin_refresh_route_is_admin_only(client, auth_headers, admin_headers, monkeypatch):
    configure(monkeypatch, FakeIntradayProvider([bar(3)]))

    anonymous = client.post("/api/v1/market/intraday/refresh", params={"instrument_id": INSTRUMENT})
    assert anonymous.status_code in (401, 403)

    normal_user = client.post(
        "/api/v1/market/intraday/refresh",
        params={"instrument_id": INSTRUMENT},
        headers=auth_headers,
    )
    assert normal_user.status_code == 403

    admin = client.post(
        "/api/v1/market/intraday/refresh",
        params={"instrument_id": INSTRUMENT},
        headers=admin_headers,
    )
    assert admin.status_code == 200
    assert admin.json()["requested_by"] == "admin"


def test_provider_status_secret_is_scrubbed_from_payload(client, monkeypatch):
    provider = FakeIntradayProvider(
        [bar(3)],
        status={
            "provider_id": "twelvedata",
            "available": True,
            "last_error": "HTTP 401 https://api.twelvedata.com/time_series?apikey=SECRET_VALUE_123",
        },
    )
    configure(monkeypatch, provider)
    resp = client.get("/api/v1/market/intraday/capability")
    assert resp.status_code == 200
    assert "SECRET_VALUE_123" not in resp.text
    assert "SECRET_VALUE_123" not in str(resp.json().get("provider_status"))
