"""Tier: the formal HTTP authoring round-trip — request → stored version → lock → session → advance.

``tests/test_daily_replay_reads_stored_bars.py`` proves the *readers*: it writes
``timeframe`` onto the version row with an ORM update, because ``StrategySpec``
used to declare no such field and pydantic dropped it from the request. That
lower tier says nothing about whether a customer's declaration survives
``POST /experiments``. These cases make no ORM writes at all — every spec here
arrives through the authoring endpoint, and every claim is read back through an
API response, so a schema that silently discards the declaration fails here.

The dataset deliberately carries three series on the same midnights: the daily
ladder (1440), a four-hour ladder (240) and an unlabelled one (NULL grain). A
reader that ignores the declared grain therefore sees dates it should not step
onto and prices a fill from a bar the 1d replay never ordered.
"""

import hashlib
import json
import uuid
from datetime import date, datetime

import pytest

from app.api import simulations
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES
from tests.conftest import TestSession


INSTRUMENT = "US:AAPL"
PROVIDER = "demo"
CASH = 10000.0

# Both grains, plus an unlabelled series, on the same three midnights.
MIXED_DAYS = ["2025-11-03", "2025-11-04", "2025-11-05"]
DAILY_LADDER = [281.0, 282.0, 283.0]
FOUR_H_LADDER = [291.0, 292.0, 293.0]
UNLABELLED_LADDER = [301.0, 302.0, 303.0]
FOUR_H = 240
# Days carrying only a non-daily grain: a daily cursor must never step onto them.
FOUR_H_ONLY_DAY = "2025-11-06"
FOUR_H_ONLY_PRICE = 294.0
UNLABELLED_ONLY_DAY = "2025-11-07"
UNLABELLED_ONLY_PRICE = 304.0
MIXED_END = UNLABELLED_ONLY_DAY

# A window whose rows carry no grain at all, which is what versions authored
# before the key existed sit on.
LEGACY_DAYS = ["2025-10-01", "2025-10-02", "2025-10-03"]
LEGACY_LADDER = [311.0, 312.0, 313.0]
LEGACY_END = LEGACY_DAYS[-1]

# What a spec without a declaration must persist, written out rather than
# derived from the model, so a new key shows up here as a failure.
UNDECLARED_SPEC_PERSISTED = {
    "schema_version": "1.0",
    "template_type": "buy_and_hold",
    "asset_ids": [INSTRUMENT],
    "frequency": "1d",
    "start_date": MIXED_DAYS[0],
    "end_date": MIXED_END,
    "initial_cash": CASH,
    "signal_parameters": {},
    "entry_rule": {},
    "exit_rule": {},
    "sizing_rule": {},
    "risk_parameters": {},
    "execution_timing": {"execute_at": "next_open"},
    "commission_rate": 0.0,
    "slippage_rate": 0.0,
    "benchmark_id": None,
    "currency": "USD",
    "user_approved_at": None,
}


def _add_bar(db, day_str, grain, price):
    day = date.fromisoformat(day_str)
    db.add(MarketDataBar(
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        bar_date=day,
        bar_timestamp=datetime.combine(day, datetime.min.time()),
        interval_minutes=grain,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1_000_000,
    ))


def _write_mixed_grain_bars():
    """Daily first, four-hour next, unlabelled last on shared midnights.

    Row order is what a reader without a grain filter ends up pricing from, so
    the daily ladder is deliberately not the last one written.
    """
    db = TestSession()
    try:
        for index, day_str in enumerate(MIXED_DAYS):
            _add_bar(db, day_str, DAILY_INTERVAL_MINUTES, DAILY_LADDER[index])
        for index, day_str in enumerate(MIXED_DAYS):
            _add_bar(db, day_str, FOUR_H, FOUR_H_LADDER[index])
        for index, day_str in enumerate(MIXED_DAYS):
            _add_bar(db, day_str, None, UNLABELLED_LADDER[index])
        _add_bar(db, FOUR_H_ONLY_DAY, FOUR_H, FOUR_H_ONLY_PRICE)
        _add_bar(db, UNLABELLED_ONLY_DAY, None, UNLABELLED_ONLY_PRICE)
        db.commit()
    finally:
        db.close()


def _write_unlabelled_bars():
    db = TestSession()
    try:
        for index, day_str in enumerate(LEGACY_DAYS):
            _add_bar(db, day_str, None, LEGACY_LADDER[index])
        db.commit()
    finally:
        db.close()


@pytest.fixture
def mixed_grain_bars():
    _write_mixed_grain_bars()


@pytest.fixture
def unlabelled_bars():
    _write_unlabelled_bars()


def _author(client, headers, *, timeframe=None, start=MIXED_DAYS[0], end=MIXED_END):
    """Create an experiment the way the research form does: one POST."""
    spec = {
        "template_type": "buy_and_hold",
        "asset_ids": [INSTRUMENT],
        "start_date": start,
        "end_date": end,
        "initial_cash": CASH,
    }
    if timeframe is not None:
        spec["timeframe"] = timeframe
    title = f"grain-http-roundtrip-{uuid.uuid4()}"
    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": title,
        "research_type": "quantitative",
        "strategy_spec": spec,
    })
    assert resp.status_code == 201, resp.text
    return title, resp.json()


def _stored_spec(client, headers, experiment):
    resp = client.get(
        f"/api/v1/experiments/{experiment['id']}/versions", headers=headers
    )
    assert resp.status_code == 200, resp.text
    versions = resp.json()
    assert [v["id"] for v in versions].count(experiment["current_version_id"]) == 1
    return next(v for v in versions if v["id"] == experiment["current_version_id"])


def _lock(client, headers, experiment):
    resp = client.post(
        f"/api/v1/experiments/{experiment['id']}/versions/"
        f"{experiment['current_version_id']}/lock",
        headers=headers,
        json={"confirm": True, "reason": "Freeze the reviewed daily contract."},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _open_session(client, headers, version_id):
    resp = client.post(
        f"/api/v1/simulations/versions/{version_id}",
        headers=headers,
        json={"initial_virtual_cash": CASH, "provider_id": PROVIDER},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _advance(client, headers, session_id):
    return client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)


def _walk(client, headers, session_id, steps):
    dates, prices = [], []
    for _ in range(steps):
        resp = _advance(client, headers, session_id)
        assert resp.status_code == 200, resp.text
        decision = resp.json()["decision"]
        dates.append(decision["snapshot_date"])
        # Only a step that traded carries a fill; later days are holds.
        detail = decision["decision_detail"] or {}
        prices.append(detail.get("execution_price"))
    return dates, prices


def test_declared_1d_survives_the_authoring_request_into_the_stored_version(
    client, auth_headers
):
    """The declaration is persisted, not dropped by the schema's extra="ignore"."""
    _, experiment = _author(client, auth_headers, timeframe="1d")

    version = _stored_spec(client, auth_headers, experiment)
    assert version["strategy_spec"].get("timeframe") == "1d"


def test_lock_keeps_the_declaration_and_freezes_the_hash_that_includes_it(
    client, auth_headers
):
    _, experiment = _author(client, auth_headers, timeframe="1d")
    before = _stored_spec(client, auth_headers, experiment)

    locked = _lock(client, auth_headers, experiment)
    assert locked["strategy_config_hash"] == before["config_hash"]

    after = _stored_spec(client, auth_headers, experiment)
    assert after["strategy_spec"].get("timeframe") == "1d"
    assert after["config_hash"] == before["config_hash"]


def test_a_spelling_variant_is_stored_as_the_canonical_declaration(
    client, auth_headers
):
    """What is stored is what the reader interprets, not what was typed."""
    _, experiment = _author(client, auth_headers, timeframe=" 1D ")

    version = _stored_spec(client, auth_headers, experiment)
    assert version["strategy_spec"]["timeframe"] == "1d"


def test_declared_1d_replays_the_daily_ladder_only(
    client, auth_headers, mixed_grain_bars
):
    """author → lock → session → advance, with no ORM step anywhere."""
    _, experiment = _author(client, auth_headers, timeframe="1d")
    _lock(client, auth_headers, experiment)
    session = _open_session(client, auth_headers, experiment["current_version_id"])
    assert session["timeframe"] == "1d"

    dates, _ = _walk(client, auth_headers, session["id"], len(MIXED_DAYS))
    assert dates == MIXED_DAYS

    # 2025-11-06 has a four-hour bar and 2025-11-07 an unlabelled one, both
    # inside the requested range. A cursor that read either grain would step
    # onto them instead of refusing.
    exhausted = _advance(client, auth_headers, session["id"])
    assert exhausted.status_code == 400
    assert "No more snapshots available" in exhausted.text


def test_entry_is_priced_from_the_daily_row_not_the_other_grains(
    client, auth_headers, mixed_grain_bars
):
    _, experiment = _author(client, auth_headers, timeframe="1d")
    _lock(client, auth_headers, experiment)
    session = _open_session(client, auth_headers, experiment["current_version_id"])

    resp = _advance(client, auth_headers, session["id"])
    assert resp.status_code == 200, resp.text
    detail = resp.json()["decision"]["decision_detail"]
    price = detail["execution_price"]

    assert price == pytest.approx(DAILY_LADDER[0], abs=1e-6)
    assert price != pytest.approx(FOUR_H_LADDER[0], abs=1e-6)
    assert price != pytest.approx(UNLABELLED_LADDER[0], abs=1e-6)

    # Every later number in the chain is arithmetic on the daily ladder, so an
    # equity curve built off another grain shows up here.
    shares = detail["quantity"]
    assert shares == int(CASH / DAILY_LADDER[0])
    second = _advance(client, auth_headers, session["id"])
    assert second.json()["decision"]["paper_equity"] == pytest.approx(
        CASH + shares * (DAILY_LADDER[1] - DAILY_LADDER[0]), abs=1e-6
    )


def test_formally_authored_version_asks_the_loader_for_1440(
    client, auth_headers, mixed_grain_bars, monkeypatch
):
    """The 1440 filter is passed by the route on this authoring path."""
    asked = []
    real_loader = simulations._load_stored_ohlcv

    def spy(*args, **kwargs):
        asked.append(kwargs.get("interval_minutes", "<not passed>"))
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(simulations, "_load_stored_ohlcv", spy)

    _, experiment = _author(client, auth_headers, timeframe="1d")
    _lock(client, auth_headers, experiment)
    session = _open_session(client, auth_headers, experiment["current_version_id"])
    _walk(client, auth_headers, session["id"], len(MIXED_DAYS))

    assert asked, "the replay never reached the stored loader"
    assert all(grain == DAILY_INTERVAL_MINUTES for grain in asked), asked


@pytest.mark.parametrize("timeframe", ["5m", "4h", "1d,5m", ""])
def test_a_non_daily_declaration_is_refused_and_stores_nothing(
    client, auth_headers, timeframe
):
    """No silent re-reading of an intraday grain as daily bars."""
    spec = {
        "template_type": "buy_and_hold",
        "asset_ids": [INSTRUMENT],
        "start_date": MIXED_DAYS[0],
        "end_date": MIXED_END,
        "initial_cash": CASH,
        "timeframe": timeframe,
    }
    title = f"grain-refusal-{uuid.uuid4()}"
    db = TestSession()
    try:
        before_experiments = db.query(Experiment).count()
        before_versions = db.query(ExperimentVersion).count()
    finally:
        db.close()

    resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
        "title": title, "research_type": "quantitative", "strategy_spec": spec,
    })
    assert resp.status_code == 422, resp.text
    assert "timeframe" in resp.text

    db = TestSession()
    try:
        assert db.query(Experiment).filter(Experiment.title == title).first() is None
        assert db.query(Experiment).count() == before_experiments
        assert db.query(ExperimentVersion).count() == before_versions
    finally:
        db.close()


def test_an_undeclared_spec_still_persists_the_legacy_bytes_and_hash(
    client, auth_headers
):
    """Nothing was defaulted in: the stored dict and its hash are byte-for-byte what
    the same request produced before the field existed."""
    _, experiment = _author(client, auth_headers)

    version = _stored_spec(client, auth_headers, experiment)
    assert "timeframe" not in version["strategy_spec"]

    expected = hashlib.sha256(
        json.dumps(UNDECLARED_SPEC_PERSISTED, sort_keys=True, separators=(",", ":"))
        .encode()
    ).hexdigest()
    assert version["strategy_spec"] == UNDECLARED_SPEC_PERSISTED
    assert version["config_hash"] == expected


def test_an_undeclared_spec_still_reads_unlabelled_bars(
    client, auth_headers, unlabelled_bars
):
    """The anchor on the other side: rows with no grain must still replay.

    Those versions never declared 1440, so narrowing them to the daily grain
    would empty a replay that works today.
    """
    _, experiment = _author(
        client, auth_headers, start=LEGACY_DAYS[0], end=LEGACY_END
    )
    version = _stored_spec(client, auth_headers, experiment)
    assert simulations._declared_daily_interval_minutes(version["strategy_spec"]) is None

    _lock(client, auth_headers, experiment)
    session = _open_session(client, auth_headers, experiment["current_version_id"])

    dates, prices = _walk(client, auth_headers, session["id"], len(LEGACY_DAYS))
    assert dates == LEGACY_DAYS
    assert prices[0] == pytest.approx(LEGACY_LADDER[0], abs=1e-6)
