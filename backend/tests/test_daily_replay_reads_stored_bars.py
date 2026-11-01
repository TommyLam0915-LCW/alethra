"""A daily replay must price off the dataset its session is bound to.

The workspace chart plots ``MarketDataBar`` rows, and session creation already
resolves a provider from those same rows. If ``advance`` then stepped over a
second series, a rule would fill at prices the user never saw — and a catalog
instrument such as US:AAPL, which has no rows in ``app/data/demo_data.py`` at
all, could not replay in the first place. These tests store a price ladder the
fixture does not contain, so reading from the fixture is a visible failure
rather than a silent substitution.
"""

from datetime import date, datetime

import pytest

from app.models.market_data import MarketDataBar
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


def _locked_session(client, auth_headers):
    """Draft → lock → session, the same three calls the research UI makes."""
    resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
        "title": "Daily replay over stored bars",
        "research_type": "quantitative",
        "strategy_spec": {
            "template_type": "buy_and_hold",
            "asset_ids": [INSTRUMENT],
            "start_date": START,
            "end_date": END,
            "initial_cash": CASH,
        },
    })
    assert resp.status_code == 201, resp.text
    experiment = resp.json()

    lock = client.put(
        f"/api/v1/experiments/{experiment['id']}",
        headers=auth_headers,
        json={"scope": {"status": "locked"}},
    )
    assert lock.status_code == 200, lock.text

    created = client.post(
        f"/api/v1/simulations/versions/{experiment['current_version_id']}",
        headers=auth_headers,
        json={"initial_virtual_cash": CASH, "provider_id": "demo"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


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
