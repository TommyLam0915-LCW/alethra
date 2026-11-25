"""The data manifest must answer with the engine's own read, not a second opinion.

Every claim here is checked against what ``run_backtest`` does with the same
rows: the checksum is the value a stored-mode run persists as
``dataset_checksum``, the multi-provider blocker is the engine's own refusal
sentence, the bar-count warning is the template's own warning string, and the
per-instrument key names are the ones ``dataset_coverage`` writes into the run.
A manifest that drifted from the engine would hand a customer a green light the
run then contradicts — the failure mode this surface exists to avoid.

Read-only is pinned too: no run row, no quota movement, no provider call.

The 54-weekday fixture is the exact number of weekdays from 2024-01-02 to
2024-03-15, so every stored bar falls inside the window the spec asks for; a
55th would sit on 2024-03-18 and be read out of the series.

Run with:
    cd alethra && TMPDIR=/tmp/alethra-sqlite-test-dir DATA_PROVIDER=demo \
      ALETHRA_PYTHON=$PWD/backend/.venv/bin/python \
      bash ./backend/scripts/run_tests.sh sqlite tests/test_data_manifest_contract.py
"""
from datetime import date, datetime, timedelta

import pytest

from app.engine.backtest import run_backtest
from app.models.market_data import MarketDataBar
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.research_run import ResearchRun
from app.providers.data_provider import OHLCVBar
from app.providers.instruments import US_CALENDAR_COVERAGE
from app.services.market_data_storage import GrainConflictError, MarketDataStorage
from tests.conftest import TestSession

_PROVIDER = "synthetic"
_INSTRUMENT = "US:AAPL"
_WINDOW_START = "2024-01-02"
_WINDOW_END = "2024-03-15"
_HOLIDAY_WEEKDAY = date(2024, 1, 15)  # MLK Day: a weekday the NYSE was closed
_ORDINARY_WEEKDAY = date(2024, 1, 16)

# The field names frontend/src/lib/dataManifest.ts declares for each response
# model. A manifest key the page does not read is a fact nobody displays, and a
# key the page reads that the server stopped sending renders as UNKNOWN while
# the type still says it is there — so both ends pin against this list, and
# tests/dataManifest.test.mjs checks the TypeScript text against the same names.
_CONTRACT_KEYS = {
    "GrainFact": {
        "interval_minutes", "label", "bar_count",
    },
    "CalendarFact": {
        "market", "reason", "coverage_start", "coverage_end",
        "expected_known_open_sessions", "sessions_the_calendar_cannot_judge",
        "missing_known_open_sessions", "missing_known_open_count",
        "bars_on_days_the_calendar_closes", "note",
    },
    "ManifestInstrument": {
        "instrument_id", "role", "status", "source", "provider_id",
        "provider_ids", "data_nature", "bar_count", "first_bar_date",
        "last_bar_date", "grains", "duplicate_dates", "calendar",
    },
    "ManifestIssue": {"code", "instrument_id", "message", "remedy"},
    "DataManifest": {
        "status", "template_type", "window", "provider_id_bound",
        "instruments", "dataset_checksum", "checksum_basis", "bars_loaded",
        "blockers", "warnings", "run_behavior", "engine_basis", "scope_note",
        "cannot_prove",
    },
}


def _spec(**overrides):
    base = {
        "template_type": "sma_crossover",
        "asset_ids": [_INSTRUMENT],
        "frequency": "1d",
        "start_date": _WINDOW_START,
        "end_date": _WINDOW_END,
        "initial_cash": 100000.0,
        "signal_parameters": {"short_window": 10, "long_window": 30},
    }
    base.update(overrides)
    return base


def _window_weekdays():
    days, cursor = [], date.fromisoformat(_WINDOW_START)
    while len(days) < 54:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, omit=()):
    return [
        OHLCVBar(
            date=day,
            open=150.0 + index,
            high=152.0 + index,
            low=149.0 + index,
            close=151.0 + index,
            volume=1_000_000,
        )
        for index, day in enumerate([day for day in days if day not in omit])
    ]


def _store(bars, provider_id=_PROVIDER, data_nature="synthetic", **kwargs):
    db = TestSession()
    try:
        count = MarketDataStorage(db).store_bars(
            _INSTRUMENT, provider_id, bars, data_nature, **kwargs
        )
    finally:
        db.close()
    return count


def _manifest(client, headers, spec, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = "/api/v1/strategy-specs/data-manifest" + (f"?{query}" if query else "")
    resp = client.post(url, json=spec, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _preflight(client, headers, spec):
    resp = client.post("/api/v1/strategy-specs/preflight", json=spec, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(body, instrument_id):
    matches = [row for row in body["instruments"] if row["instrument_id"] == instrument_id]
    assert len(matches) == 1, (instrument_id, body["instruments"])
    return matches[0]


def _codes(entries):
    return [entry["code"] for entry in entries]


def _entry(body, code):
    matches = [entry for entry in body["warnings"] + body["blockers"] if entry["code"] == code]
    assert len(matches) == 1, (code, body["warnings"], body["blockers"])
    return matches[0]


def test_a_complete_window_is_ready_and_carries_the_engines_own_checksum(
    client, auth_headers
):
    days = _window_weekdays()
    assert _store(_bars(days)) == 54

    body = _manifest(client, auth_headers, _spec())

    assert body["status"] == "ready", body["blockers"]
    row = _row(body, _INSTRUMENT)
    assert row["role"] == "traded"
    assert row["status"] == "stored"
    # These keys are the ones run_backtest persists in dataset_coverage, so a
    # manifest row and a run record can be compared field by field.
    assert row["source"] == "stored"
    assert row["provider_id"] == _PROVIDER
    assert row["provider_ids"] == [_PROVIDER]
    assert row["data_nature"] == "synthetic"
    assert row["bar_count"] == 54
    assert row["first_bar_date"] == _WINDOW_START
    assert row["last_bar_date"] == _WINDOW_END

    # The parity claim, measured through the engine itself.
    db = TestSession()
    try:
        result = run_backtest(_spec(), db_session=db, data_source="stored")
        assert result["run_status"] == "completed", result["errors"]
        assert result["dataset_checksum"] == body["dataset_checksum"]
        coverage = result["dataset_coverage"][_INSTRUMENT]
        assert coverage["bar_count"] == row["bar_count"]
        assert coverage["first_bar_date"] == row["first_bar_date"]
        assert coverage["provider_id"] == row["provider_id"]
        assert coverage["data_nature"] == row["data_nature"]
    finally:
        db.close()


def test_two_providers_for_one_window_block_instead_of_merging(client, auth_headers):
    days = _window_weekdays()
    _store(_bars(days))
    _store(_bars(days), provider_id="demo")

    body = _manifest(client, auth_headers, _spec())

    assert body["status"] == "blocked"
    assert "multiple_providers" in _codes(body["blockers"])
    row = _row(body, _INSTRUMENT)
    assert row["status"] == "ambiguous"
    assert row["provider_ids"] == ["demo", _PROVIDER]

    # The blocker is the engine's own sentence, produced by the engine's own
    # function on the same rows — not a paraphrase a reader could not trust.
    db = TestSession()
    try:
        result = run_backtest(_spec(), db_session=db, data_source="stored")
        assert result["run_status"] == "error"
        assert body["blockers"][0]["message"] == result["errors"][0]
    finally:
        db.close()

    # Binding one provider is the remedy the manifest names, and it works.
    bound = _manifest(client, auth_headers, _spec(), provider_id=_PROVIDER)
    assert bound["status"] == "ready"
    assert bound["provider_id_bound"] == _PROVIDER
    assert _row(bound, _INSTRUMENT)["provider_id"] == _PROVIDER


def test_an_empty_window_is_partial_and_says_what_each_mode_does(client, auth_headers):
    body = _manifest(client, auth_headers, _spec())

    assert body["status"] == "partial"
    assert body["dataset_checksum"] is None
    assert body["bars_loaded"] == 0
    row = _row(body, _INSTRUMENT)
    assert row["status"] == "absent"
    assert row["bar_count"] == 0
    assert row["provider_ids"] == []
    assert row["calendar"] is None
    assert "absent_from_storage" in _codes(body["warnings"])
    assert f"No stored data for {_INSTRUMENT}" in body["run_behavior"]["data_source=stored"]
    assert _INSTRUMENT in body["run_behavior"]["data_source=auto"]
    assert body["run_behavior"]["data_source=provider"].startswith("Ignores")


def test_synthetic_rows_are_reported_as_synthetic(client, auth_headers):
    _store(_bars(_window_weekdays()))

    body = _manifest(client, auth_headers, _spec())

    assert _row(body, _INSTRUMENT)["data_nature"] == "synthetic"
    warning = _entry(body, "synthetic_nature")
    assert "not based on real market prices" in warning["message"]
    # The label is the only provenance storage carries; a checksum cannot
    # promote it, and nothing in this answer may do so.
    assert "identifies these bytes, not their market reality" in " ".join(
        body["cannot_prove"]
    )


def test_a_missing_open_session_is_named_only_where_the_calendar_is_authoritative(
    client, auth_headers
):
    _store(_bars(_window_weekdays(), omit=[_ORDINARY_WEEKDAY]))

    body = _manifest(client, auth_headers, _spec())
    calendar = _row(body, _INSTRUMENT)["calendar"]

    assert calendar["market"] == "US"
    assert calendar["coverage_start"] == US_CALENDAR_COVERAGE[0].isoformat()
    assert calendar["missing_known_open_count"] == 1
    assert calendar["missing_known_open_sessions"] == [_ORDINARY_WEEKDAY.isoformat()]
    assert "missing_sessions" in _codes(body["warnings"])
    # The rows that exist are coherent, so this is a warning about what the gap
    # does to the spec, not a blocker that stops the run.
    assert body["status"] == "ready"


def test_a_window_outside_the_calendar_names_no_missing_sessions(client, auth_headers):
    days, cursor = [], date(2026, 1, 5)
    while len(days) < 20:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    _store(_bars(days))

    body = _manifest(
        client, auth_headers, _spec(start_date="2026-01-05", end_date="2026-02-27")
    )
    calendar = _row(body, _INSTRUMENT)["calendar"]

    assert calendar["missing_known_open_count"] == 0
    assert calendar["sessions_the_calendar_cannot_judge"] > 0
    assert "missing_sessions" not in _codes(body["warnings"])
    assert "not proof of missing data" in calendar["note"]


def test_a_bar_on_a_listed_holiday_is_reported_not_dropped(client, auth_headers):
    days = _window_weekdays()
    assert _HOLIDAY_WEEKDAY in days  # a weekday series cannot help but include it
    _store(_bars(days))

    body = _manifest(client, auth_headers, _spec())
    row = _row(body, _INSTRUMENT)

    assert _HOLIDAY_WEEKDAY.isoformat() in row["calendar"]["bars_on_days_the_calendar_closes"]
    warning = _entry(body, "bars_on_closed_days")
    assert "not a market calendar" in warning["message"]
    # Reported as stored: the count still includes the bar the market never traded.
    assert row["bar_count"] == 54


def test_two_rows_on_one_date_block_and_the_writer_that_makes_them_is_refused(
    client, auth_headers
):
    days = _window_weekdays()
    _store(_bars(days))

    # store_bars refuses to create the collision, which is what the guard is for.
    with pytest.raises(GrainConflictError):
        _store(_bars(days), interval_minutes=1440)

    # The shape it refuses is still what a database imported before that guard
    # can hold, so it is inserted directly rather than wished away.
    db = TestSession()
    try:
        for index, day in enumerate(days):
            db.add(
                MarketDataBar(
                    instrument_id=_INSTRUMENT,
                    provider_id=_PROVIDER,
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, datetime.min.time()),
                    interval_minutes=1440,
                    open=180.0 + index,
                    high=182.0 + index,
                    low=179.0 + index,
                    close=181.0 + index,
                )
            )
        db.commit()
    finally:
        db.close()

    body = _manifest(client, auth_headers, _spec())

    assert body["status"] == "blocked"
    assert "duplicate_dates" in _codes(body["blockers"])
    row = _row(body, _INSTRUMENT)
    assert row["bar_count"] == 108
    # Labelled grains first, unlabelled last: NULL is not a grain of zero.
    assert [grain["interval_minutes"] for grain in row["grains"]] == [1440, None]
    assert row["grains"][0]["bar_count"] == 54
    assert len(row["duplicate_dates"]) == 54


def test_unlabelled_grain_is_reported_as_stored_not_as_daily(client, auth_headers):
    _store(_bars(_window_weekdays()))

    body = _manifest(client, auth_headers, _spec(timeframe="1d"))
    row = _row(body, _INSTRUMENT)

    assert row["grains"] == [
        {
            "interval_minutes": None,
            "label": "unlabelled — written before the grain was recorded",
            "bar_count": 54,
        }
    ]
    warning = _entry(body, "unlabelled_grain")
    assert "cannot be told apart" in warning["message"]
    assert any(
        "does not filter by interval_minutes" in text for text in body["cannot_prove"]
    )


def test_a_short_window_warns_in_the_templates_own_words(client, auth_headers):
    _store(_bars(_window_weekdays()[:10]))

    body = _manifest(client, auth_headers, _spec())

    warning = _entry(body, "insufficient_bars_for_template")
    assert "31" in warning["message"]  # long_window 30 + 1
    assert "10 bar(s) loaded" in warning["message"]
    # The template warns rather than refuses, so the manifest must not block.
    db = TestSession()
    try:
        result = run_backtest(_spec(), db_session=db, data_source="stored")
        assert result["run_status"] == "completed"
        assert any("Not enough data for SMA" in text for text in result["warnings"])
    finally:
        db.close()
    assert body["status"] == "ready"


def test_the_benchmark_is_the_one_instrument_a_run_continues_without(
    client, auth_headers
):
    _store(_bars(_window_weekdays()))

    body = _manifest(client, auth_headers, _spec(benchmark_id="US:SPY"))

    assert body["status"] == "ready"
    benchmark = _row(body, "US:SPY")
    assert benchmark["status"] == "absent"
    assert benchmark["role"] == "benchmark"
    assert "benchmark_absent_warns" in _codes(body["warnings"])
    assert "absent_from_storage" not in _codes(body["warnings"])
    assert benchmark["calendar"] is None
    # The engine keeps trading with an empty benchmark curve rather than filling
    # one in, which is why an absent benchmark does not make this partial.
    db = TestSession()
    try:
        result = run_backtest(_spec(benchmark_id="US:SPY"), db_session=db, data_source="stored")
        assert result["run_status"] == "completed"
        assert any("unavailable for this date range" in text for text in result["warnings"])
    finally:
        db.close()


def test_the_contract_gate_refuses_the_same_specs_on_both_surfaces(
    client, auth_headers
):
    cases = [
        ({"template_type": "mean_reversion_zscore"}, "unsupported"),
        ({"template_type": "buy_and_hold", "timeframe": "5m"}, "unsupported"),
        ({"template_type": "buy_and_hold"}, "invalid"),
        (
            {
                "template_type": "buy_and_hold",
                "asset_ids": [_INSTRUMENT],
                "start_date": _WINDOW_START,
                "end_date": "2024-13-45",
                "initial_cash": 1000.0,
            },
            "invalid",
        ),
    ]
    for spec, preflight_status in cases:
        assert _preflight(client, auth_headers, spec)["status"] == preflight_status, spec
        body = _manifest(client, auth_headers, spec)
        # One gate, two surfaces: the manifest has no separate idea of what a
        # readable spec is, so every non-valid spec is invalid_spec here too.
        assert body["status"] == "invalid_spec", spec
        assert body["blockers"][0]["code"] == (
            "unsupported" if preflight_status == "unsupported" else "invalid_spec"
        ), spec
        assert body["dataset_checksum"] is None, spec
        assert body["instruments"] == [], spec


def test_a_malformed_date_is_refused_while_authoring_not_inside_a_run(
    client, auth_headers
):
    # Both readers call date.fromisoformat on these strings, so a value they
    # cannot parse is not an unusual window — it is a window nothing can open.
    broken = _spec(start_date="2024-01-32")

    assert _preflight(client, auth_headers, broken)["status"] == "invalid"
    body = _manifest(client, auth_headers, broken)
    assert body["status"] == "invalid_spec"
    assert any("date.fromisoformat" in entry["message"] for entry in body["warnings"])

    db = TestSession()
    try:
        with pytest.raises(ValueError):
            run_backtest(broken, db_session=db, data_source="stored")
    finally:
        db.close()


def test_reading_a_manifest_writes_nothing_and_calls_no_provider(
    client, auth_headers, monkeypatch
):
    _store(_bars(_window_weekdays()))

    def _no_feed(*args, **kwargs):
        raise AssertionError("the manifest reached a provider feed")

    monkeypatch.setattr("app.engine.backtest.get_demo_ohlcv", _no_feed)

    def _counts():
        db = TestSession()
        try:
            return (
                db.query(ResearchRun).count(),
                db.query(QuotaReservation).count(),
                db.query(QuotaCounter).count(),
                db.query(MarketDataBar).count(),
            )
        finally:
            db.close()

    before = _counts()

    body = _manifest(client, auth_headers, _spec())
    assert body["status"] == "ready"
    # A benchmark the spec does not store is the branch a fetch would belong to,
    # so it is asked for too, under the same guard.
    _manifest(client, auth_headers, _spec(benchmark_id="US:SPY"))

    assert _counts() == before

    assert body["scope_note"].startswith("A contract match only")
    assert "not permission to run" in body["scope_note"]
    assert body["cannot_prove"], "a manifest that proves everything proves nothing"
    assert body["engine_basis"], "an answer with no stated source is a claim"


def test_the_json_carries_exactly_the_fields_the_frontend_types_declare(
    client, auth_headers
):
    _store(_bars(_window_weekdays()))
    body = _manifest(client, auth_headers, _spec(benchmark_id="US:SPY"))

    assert set(body) == _CONTRACT_KEYS["DataManifest"]
    assert set(body["window"]) == {"start_date", "end_date"}
    assert len(body["instruments"]) == 2, body["instruments"]
    for row in body["instruments"]:
        assert set(row) == _CONTRACT_KEYS["ManifestInstrument"], row["instrument_id"]
        for grain in row["grains"]:
            assert set(grain) == _CONTRACT_KEYS["GrainFact"], row["instrument_id"]
        if row["calendar"] is not None:
            assert set(row["calendar"]) == _CONTRACT_KEYS["CalendarFact"], row["instrument_id"]
    for issue in body["blockers"] + body["warnings"]:
        assert set(issue) == _CONTRACT_KEYS["ManifestIssue"], issue

    # The refused branch returns the same shape, not a partial object: the page
    # renders one component for both, and a missing key there would be a crash
    # at the moment the user most needs an explanation.
    refused = _manifest(client, auth_headers, _spec(template_type="bollinger_reversion"))
    assert refused["status"] == "invalid_spec"
    assert set(refused) == _CONTRACT_KEYS["DataManifest"]
    for issue in refused["blockers"] + refused["warnings"]:
        assert set(issue) == _CONTRACT_KEYS["ManifestIssue"], issue


def test_the_manifest_route_refuses_an_unauthenticated_call(client):
    # The endpoint reads the account's stored dataset, so it answers behind the
    # same gate as the rest of /api/v1 and never leaks a checksum on the way by.
    resp = client.post("/api/v1/strategy-specs/data-manifest", json=_spec())

    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Could not validate credentials"
    assert "dataset_checksum" not in resp.text
