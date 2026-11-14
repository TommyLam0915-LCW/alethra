"""A daily replay must price off the dataset its session is bound to.

The workspace chart plots ``MarketDataBar`` rows, and session creation already
resolves a provider from those same rows. If ``advance`` then stepped over a
second series, a rule would fill at prices the user never saw — and a catalog
instrument such as US:AAPL, which has no rows in ``app/data/demo_data.py`` at
all, could not replay in the first place. These tests store a price ladder the
fixture does not contain, so reading from the fixture is a visible failure
rather than a silent substitution.

The second half stores a daily and a four-hour bar at the same midnights, which
is what a venue shelf looks like once both grains are harvested. ``advance`` and
the manual submit path must each select the daily series by name, so those cases
watch the route's own call rather than only the loader helper it delegates to.
"""

import uuid
from datetime import date, datetime

import pytest

from app.api import simulations
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES
from tests.conftest import TestSession


INSTRUMENT = "US:AAPL"
# The demo fixture ends at 2024-02-13 and trades near 44-56, so a 2025 ladder at
# 211-215 cannot have come from it.
WINDOW = ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05", "2025-06-06"]
PRICES = [211.0, 212.0, 213.0, 214.0, 215.0]
START, END = WINDOW[0], WINDOW[-1]
CASH = 10000.0


def _write_bars(rows):
    db = TestSession()
    try:
        for day_str, price in rows:
            day = date.fromisoformat(day_str)
            db.add(MarketDataBar(
                instrument_id=INSTRUMENT,
                provider_id="demo",
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1_000_000,
            ))
        db.commit()
    finally:
        db.close()


def _erase_bars():
    db = TestSession()
    try:
        db.query(MarketDataBar).filter(MarketDataBar.instrument_id == INSTRUMENT).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _stored_daily_bars(setup_db):
    _write_bars(list(zip(WINDOW, PRICES)))
    yield


def _advance(client, auth_headers, session_id):
    return client.post(
        f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
    )


def _locked_session(client, auth_headers, *, start=START, end=END, timeframe=None):
    """Draft → lock → session, the same three calls the research UI makes."""
    spec = {
        "template_type": "buy_and_hold",
        "asset_ids": [INSTRUMENT],
        "start_date": start,
        "end_date": end,
        "initial_cash": CASH,
    }

    resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
        "title": "Daily replay over stored bars",
        "research_type": "quantitative",
        "strategy_spec": spec,
    })
    assert resp.status_code == 201, resp.text
    experiment = resp.json()

    lock = client.put(
        f"/api/v1/experiments/{experiment['id']}",
        headers=auth_headers,
        json={"scope": {"status": "locked"}},
    )
    assert lock.status_code == 200, lock.text

    if timeframe is not None:
        _declare_timeframe(experiment["current_version_id"], timeframe)

    created = client.post(
        f"/api/v1/simulations/versions/{experiment['current_version_id']}",
        headers=auth_headers,
        json={"initial_virtual_cash": CASH, "provider_id": "demo"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _declare_timeframe(version_id, timeframe):
    """Write ``timeframe`` onto the stored row: the reader tier, not the request tier.

    These cases hold what the authoring path cannot prove — which grain a reader
    selects once a declaration is on the row the replay reads. Because they seed
    the row directly, they say nothing about whether ``POST /experiments`` keeps
    the declaration; that is
    ``tests/test_daily_grain_http_authoring_roundtrip.py``. A schema that drops
    the key would leave this file green, which is exactly why the two tiers are
    kept separate rather than merged.
    """
    db = TestSession()
    try:
        version = db.get(ExperimentVersion, uuid.UUID(str(version_id)))
        version.strategy_spec = {**(version.strategy_spec or {}), "timeframe": timeframe}
        db.commit()
    finally:
        db.close()


def test_advance_walks_the_stored_calendar(client, auth_headers):
    """One step per stored bar, in the stored series' own date order."""
    session_id = _locked_session(client, auth_headers)

    walked = []
    for _ in WINDOW:
        resp = _advance(client, auth_headers, session_id)
        assert resp.status_code == 200, resp.text
        walked.append(resp.json()["decision"]["snapshot_date"])

    assert walked == WINDOW

    exhausted = _advance(client, auth_headers, session_id)
    assert exhausted.status_code == 400
    assert "No more snapshots available" in exhausted.text


def test_entry_fills_at_the_bar_the_chart_plots(client, auth_headers):
    """The fill price is the stored open, i.e. the same row the chart draws."""
    session_id = _locked_session(client, auth_headers)

    resp = _advance(client, auth_headers, session_id)
    assert resp.status_code == 200, resp.text
    decision = resp.json()["decision"]
    detail = decision["decision_detail"]
    assert detail["side"] == "BUY"
    assert detail["execution_price"] == pytest.approx(PRICES[0], abs=1e-6)

    plotted = client.get(
        f"/api/v1/instruments/{INSTRUMENT}/bars/stored",
        params={"start_date": START, "end_date": END, "provider_id": "demo", "timeframe": "1d"},
    )
    assert plotted.status_code == 200, plotted.text
    bars = {bar["date"]: bar for bar in plotted.json()["bars"]}
    assert bars[WINDOW[0]]["open"] == pytest.approx(detail["execution_price"], abs=1e-6)

    # 47 shares at 211, no commission or slippage: every number below is
    # arithmetic on the stored bars, so a fixture price in the chain shows up.
    shares = detail["quantity"]
    assert shares == int(CASH / PRICES[0])
    assert decision["paper_cash"] == pytest.approx(CASH - shares * PRICES[0], abs=1e-6)
    assert decision["paper_equity"] == pytest.approx(CASH, abs=1e-6)

    second = _advance(client, auth_headers, session_id)
    assert second.json()["decision"]["paper_equity"] == pytest.approx(
        CASH + shares * (PRICES[1] - PRICES[0]), abs=1e-6
    )


def test_advance_refuses_without_its_bound_bars(client, auth_headers):
    """No stored rows means no step — never a fallback to another dataset."""
    session_id = _locked_session(client, auth_headers)
    assert _advance(client, auth_headers, session_id).status_code == 200

    _erase_bars()

    resp = _advance(client, auth_headers, session_id)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert INSTRUMENT in detail
    assert "has no stored bars" in detail
    assert "no other data source is substituted" in detail

    decisions = client.get(
        f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers
    ).json()
    assert len(decisions) == 1, "a refused step must not leave a decision behind"


# -- the daily caller, not just the loader, must ask for the daily grain ------
#
# A strict ``interval_minutes`` parameter nobody passes is a green test suite
# over the same mixed-grain read. These cases store a second series at the exact
# midnights the daily series already uses, so a caller that forgets the grain
# reads bars from a frequency the 1d replay never ordered.

GRAIN_DAYS = ["2025-09-01", "2025-09-02", "2025-09-03"]
DAILY_LADDER = [241.0, 242.0, 243.0]
FOUR_H_LADDER = [251.0, 252.0, 253.0]
FOUR_H_ONLY_DAY = "2025-09-04"
FOUR_H_ONLY_PRICE = 254.0
FOUR_H = 240


def _write_grained_bars():
    """Both grains at one midnight, four-hour rows inserted last.

    One date carrying two grains means the readers have to choose, and they
    choose differently: the replay prices its entry from ``iloc[-1]`` of the
    frame, while ``_observed_bar_close`` takes ``first()``. Both then tie-break
    on insertion order, so writing the four-hour series last makes the price the
    replay reports come from a four-hour bar whenever the grain filter is
    missing. Agreeing with the daily ladder is therefore not an accident of row
    order.
    """
    db = TestSession()
    try:
        for grain, ladder in ((DAILY_INTERVAL_MINUTES, DAILY_LADDER), (FOUR_H, FOUR_H_LADDER)):
            for index, day_str in enumerate(GRAIN_DAYS):
                _add_bar(db, date.fromisoformat(day_str), grain, ladder[index])
        _add_bar(db, date.fromisoformat(FOUR_H_ONLY_DAY), FOUR_H, FOUR_H_ONLY_PRICE)
        db.commit()
    finally:
        db.close()


def _add_bar(db, day, grain, price):
    db.add(MarketDataBar(
        instrument_id=INSTRUMENT,
        provider_id="demo",
        bar_date=day,
        bar_timestamp=datetime.combine(day, datetime.min.time()),
        interval_minutes=grain,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1_000_000,
    ))


@pytest.fixture
def grained_bars():
    _write_grained_bars()


def _grained_session(client, auth_headers):
    """A session whose spec declares ``1d`` over a range holding both grains."""
    return _locked_session(
        client,
        auth_headers,
        start=GRAIN_DAYS[0],
        end=FOUR_H_ONLY_DAY,
        timeframe="1d",
    )


def test_daily_advance_calls_the_loader_with_the_daily_grain(
    client, auth_headers, grained_bars, monkeypatch
):
    """The replay route itself passes 1440 — not a helper called with it."""
    asked = []
    real_loader = simulations._load_stored_ohlcv

    def spy(*args, **kwargs):
        asked.append(kwargs.get("interval_minutes", "<not passed>"))
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(simulations, "_load_stored_ohlcv", spy)

    session_id = _grained_session(client, auth_headers)
    for _ in GRAIN_DAYS:
        assert _advance(client, auth_headers, session_id).status_code == 200

    assert asked, "the daily advance path never reached the stored loader"
    assert all(grain == DAILY_INTERVAL_MINUTES for grain in asked), asked


def test_1d_replay_never_steps_onto_a_four_hour_only_date(client, auth_headers, grained_bars):
    """A four-hour bar is not a trading day: the daily cursor stops without it."""
    session_id = _grained_session(client, auth_headers)

    walked = []
    for _ in GRAIN_DAYS:
        resp = _advance(client, auth_headers, session_id)
        assert resp.status_code == 200, resp.text
        walked.append(resp.json()["decision"]["snapshot_date"])

    assert walked == GRAIN_DAYS

    exhausted = _advance(client, auth_headers, session_id)
    assert exhausted.status_code == 400
    assert "No more snapshots available" in exhausted.text


def test_entry_fills_at_the_daily_row_not_the_four_hour_one(client, auth_headers, grained_bars):
    """Both grains share the 2025-09-01 midnight; only the daily one may price it."""
    session_id = _grained_session(client, auth_headers)

    resp = _advance(client, auth_headers, session_id)
    assert resp.status_code == 200, resp.text
    price = resp.json()["decision"]["decision_detail"]["execution_price"]

    assert price == pytest.approx(DAILY_LADDER[0], abs=1e-6)
    assert price != pytest.approx(FOUR_H_LADDER[0], abs=1e-6)


def test_manual_order_reads_its_estimate_at_the_daily_grain(
    client, auth_headers, grained_bars, monkeypatch
):
    """The submit path is the replay's other daily reader; it asks the same grain."""
    asked = []
    real_close = simulations._observed_bar_close

    def spy(db, **kwargs):
        asked.append(kwargs.get("interval_minutes", "<not passed>"))
        return real_close(db, **kwargs)

    monkeypatch.setattr(simulations, "_observed_bar_close", spy)

    session_id = _grained_session(client, auth_headers)
    assert _advance(client, auth_headers, session_id).status_code == 200

    linked = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": CASH,
        "currency": "USD",
        "simulation_session_id": session_id,
    })
    assert linked.status_code == 201, linked.text

    submitted = client.post(
        f"/api/v1/simulations/{session_id}/manual-decision",
        headers=auth_headers,
        json={
            "action": "buy",
            "quantity": 5,
            "rationale": "Estimated cost must come from the bar I was watching.",
            "idempotency_key": f"grain-{uuid.uuid4()}",
        },
    )
    assert submitted.status_code == 200, submitted.text
    assert asked, "the manual submit path never read an observed close"
    assert all(grain == DAILY_INTERVAL_MINUTES for grain in asked), asked


@pytest.mark.parametrize(
    "spec,expected",
    [
        ({"timeframe": "1d"}, DAILY_INTERVAL_MINUTES),
        ({"timeframe": "1D"}, DAILY_INTERVAL_MINUTES),
        ({"timeframe": " 1d "}, DAILY_INTERVAL_MINUTES),
        # A spec written before the key existed reads whatever grain its rows
        # carry; narrowing it to 1440 would empty a replay that works today.
        ({}, None),
        ({"timeframe": "5m"}, None),
        (None, None),
    ],
)
def test_only_a_declared_daily_spec_asks_for_the_daily_grain(spec, expected):
    assert simulations._declared_daily_interval_minutes(spec) == expected
