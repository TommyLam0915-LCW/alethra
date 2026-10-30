"""C1-C4 safety cases: full side-effect verification plus in-process contracts.

Two lanes, one business property set:

* ``TestC1..C4`` (live) run against a disposable stack named by
  ``ALETHRA_PROBE_API_ORIGIN`` / ``ALETHRA_PROBE_DB_URL`` through
  ``tests.live_stack_env``. They check the *persisted* side effects of a race —
  decision rows, orders, fills, cash, position — not just status codes.
* ``TestC3*`` (in-process) use the tracked ``client`` fixture and the test
  database the conftest owns. They pin the replay contracts that do not need a
  real concurrent race: identical retry, stale version, conflicting payload,
  cross-user isolation, replay after the session stopped accepting new work.

Both lanes submit through the current contract: a manual BUY/SELL is a *pending*
order stamped with the bar the user observed, and the next ``advance`` executes
it at that bar's open. A submission books no cash, position or fill.
"""
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.market_data import MarketDataBar
from tests.live_stack_env import (  # noqa: F401  (fixtures registered by import)
    live_probe,
    new_session,
    probe_version,
    skip_reason,
    submit_concurrently,
)

_SKIP_REASON = skip_reason()
live_only = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=f"live-stack probe: {_SKIP_REASON}",
)

PROVIDER_ID = "twelvedata"
SYMBOL = "US:AAPL"

# Same 20-bar weekday fixture the live probes insert (2024-01-15 is a market
# holiday), so an in-process replay steps onto the same dates as the live one.
FIXTURE_BAR_DATES = tuple(
    day
    for day in (date(2024, 1, 2) + timedelta(days=offset) for offset in range(29))
    if day.weekday() < 5 and day != date(2024, 1, 15)
)
assert len(FIXTURE_BAR_DATES) == 20


def _insert_fixture_bars():
    from tests.conftest import TestSession

    db = TestSession()
    try:
        for index, bar_date in enumerate(FIXTURE_BAR_DATES):
            db.add(MarketDataBar(
                instrument_id=SYMBOL,
                provider_id=PROVIDER_ID,
                bar_date=bar_date,
                bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                open=180.0 + index,
                high=181.0 + index,
                low=179.0 + index,
                close=180.5 + index,
                volume=50000000,
            ))
        db.commit()
    finally:
        db.close()


def _create_session_with_data(client, auth_headers):
    """Experiment -> locked version -> bars -> session -> linked portfolio.

    The strategy is a 200/300 SMA over 20 bars, so the rule engine stays in
    warm-up and never trades: every order and fill these tests see is the one
    the test itself submitted.
    """
    created = client.post("/api/v1/experiments/", headers=auth_headers, json={
        "title": f"C1-C4 in-process {uuid4().hex[:8]}",
        "research_type": "quantitative",
        "strategy_spec": {
            "template_type": "sma_crossover",
            "asset_ids": [SYMBOL],
            "start_date": FIXTURE_BAR_DATES[0].isoformat(),
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "signal_parameters": {"short_window": 200, "long_window": 300},
        },
    })
    assert created.status_code == 201, created.text
    experiment_id = created.json()["id"]
    version_id = created.json()["current_version_id"]

    locked = client.put(f"/api/v1/experiments/{experiment_id}", headers=auth_headers, json={
        "scope": {"status": "locked"},
    })
    assert locked.status_code == 200, locked.text

    _insert_fixture_bars()

    session = client.post(
        f"/api/v1/simulations/versions/{version_id}",
        headers=auth_headers,
        json={"initial_virtual_cash": 100000.0, "provider_id": PROVIDER_ID},
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]

    portfolio = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": 100000.0,
        "simulation_session_id": session_id,
    })
    assert portfolio.status_code == 201, portfolio.text

    return session_id


def _advance(client, auth_headers, session_id):
    advanced = client.post(
        f"/api/v1/simulations/{session_id}/advance", headers=auth_headers,
    )
    assert advanced.status_code == 200, advanced.text
    return advanced.json()


def _snapshot(client, auth_headers, session_id):
    state = client.get(f"/api/v1/simulations/{session_id}", headers=auth_headers)
    assert state.status_code == 200, state.text
    decisions = client.get(
        f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers,
    )
    assert decisions.status_code == 200, decisions.text
    state = state.json()
    rows = decisions.json()
    return {
        "decision_count": len(rows),
        "paper_cash": rows[-1]["paper_cash"] if rows else state["initial_virtual_cash"],
        "paper_equity": state["paper_equity"],
        "total_decisions": state["total_decisions"],
        "observed_days": state["observed_days"],
        "state_version": state["state_version"],
        "current_position": state["current_position_json"],
        "current_snapshot_date": state["current_snapshot_date"],
        "status": state["status"],
    }


def _intent(action, *, quantity=None, rationale="", key=None):
    payload = {
        "action": action,
        "rationale": rationale or f"probe {action}",
        "idempotency_key": key or f"probe-{uuid4().hex}",
    }
    if quantity is not None:
        payload["quantity"] = quantity
    return payload


def _manual_decisions(session):
    return [
        decision
        for decision in session.decisions()
        if (decision["decision_detail"] or {}).get("action")
    ]


@live_only
class TestC1CompleteSideEffects:
    """C1: a concurrent duplicate may write ONE intent and eventually ONE fill."""

    def test_concurrent_duplicate_complete_verification(self, live_probe, new_session):
        session = new_session()
        first_bar = session.advance()["decision"]
        assert first_bar["snapshot_date"] == "2024-01-02"

        payload = _intent("buy", quantity=100, rationale="C1 complete test")
        responses = submit_concurrently(session, [payload, payload])

        statuses = sorted(response.status_code for response in responses)
        assert all(status in (200, 201, 409) for status in statuses), statuses
        winners = [r for r in responses if r.status_code in (200, 201)]
        assert winners, f"no request succeeded: {statuses}"

        # One decision (plus the advance row), one intent, nothing booked yet.
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }
        manual = _manual_decisions(session)
        assert len(manual) == 1
        detail = manual[0]["decision_detail"]
        assert detail["action"] == "buy"
        assert detail["booked_at_submit"] is False
        assert manual[0]["snapshot_date"] == "2024-01-02"
        assert manual[0]["step_number"] == 2

        body = winners[0].json()
        assert body["order"]["status"] == "pending"
        assert body["order"]["quantity"] == 100
        assert body["order"]["booked_at_submit"] is False
        assert body["order"]["idempotency_key"] == payload["idempotency_key"]
        assert body["execution"]["next_window"]["date"] == "2024-01-03"
        order_id = body["order"]["order_id"]

        orders = session.orders()
        assert len(orders) == 1
        assert orders[0]["order"]["id"] == order_id

        state = session.state()
        assert state["paper_equity"] == 100000.0
        assert state["current_position_json"] is None
        assert state["total_decisions"] == 2

        # The next bar is the only execution window: exactly one fill, once.
        advanced = session.advance()["decision"]
        manual_fills = (advanced["decision_detail"] or {})["manual_fills"]
        assert len(manual_fills) == 1
        fill = manual_fills[0]
        assert fill["order_id"] == order_id
        assert fill["execution_date"] == "2024-01-03"
        assert float(fill["quantity"]) == 100

        assert session.counts() == {
            "decisions": 3, "decision_records": 1, "orders": 1, "fills": 1,
        }
        fills = session.fills(order_id)
        assert len(fills) == 1
        assert fills[0]["execution_date"] == "2024-01-03"

        state = session.state()
        assert state["current_position_json"] is not None
        assert state["current_position_json"]["shares"] == 100
        assert state["paper_equity"] != 100000.0


@live_only
class TestC2StaleStepCompetition:
    """C2: two intents racing on one observed version - one wins, one is stale."""

    def test_stale_step_competition(self, live_probe, new_session):
        session = new_session()
        session.advance()
        observed_version = session.state()["state_version"]

        payload_a = _intent("buy", quantity=50, rationale="C2 contender A")
        payload_b = _intent("buy", quantity=50, rationale="C2 contender B")
        for payload in (payload_a, payload_b):
            payload["expected_version"] = observed_version

        responses = submit_concurrently(session, [payload_a, payload_b])
        statuses = sorted(response.status_code for response in responses)
        assert statuses.count(200) == 1, f"expected one winner, got {statuses}"
        assert statuses.count(409) == 1, f"expected one stale loser, got {statuses}"
        loser = next(r for r in responses if r.status_code == 409)
        assert "State version conflict" in loser.json()["detail"]

        # The loser wrote nothing: no second decision, no second order.
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }
        manual = _manual_decisions(session)
        assert [decision["step_number"] for decision in manual] == [2]
        assert [d["step_number"] for d in session.decisions()] == [1, 2]
        assert session.state()["total_decisions"] == 2

        orders = session.orders()
        assert len(orders) == 1
        assert orders[0]["order"]["status"] == "pending"
        assert orders[0]["order"]["idempotency_key"] in (
            payload_a["idempotency_key"], payload_b["idempotency_key"],
        )


@live_only
class TestC3ResponseLossSimulation:
    """C3: after a lost response, replaying the key returns the original intent."""

    def test_response_loss_with_commit_verification(self, live_probe, new_session):
        session = new_session()
        session.advance()
        payload = _intent("buy", quantity=75, rationale="C3 response loss test")

        first = session.submit_raw(payload)
        assert first.status_code == 200, first.text
        decision_id = first.json()["decision"]["id"]
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }
        before = session.state()

        replay = session.submit_raw(payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["decision"]["id"] == decision_id

        after = session.state()
        for field in (
            "paper_equity", "total_decisions", "observed_days", "state_version",
            "current_snapshot_date", "current_position_json",
        ):
            assert after[field] == before[field], f"{field} moved on a replay"
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }

        # Replaying after the window opened still returns the original decision.
        session.advance()
        filled = session.counts()
        assert filled == {
            "decisions": 3, "decision_records": 1, "orders": 1, "fills": 1,
        }
        late_replay = session.submit_raw(payload)
        assert late_replay.status_code == 200, late_replay.text
        assert late_replay.json()["decision"]["id"] == decision_id
        assert session.counts() == filled


@live_only
class TestC4TransactionRollback:
    """C4: a refused intent leaves no partial state behind."""

    def test_transaction_rollback_on_failure(self, live_probe, new_session):
        session = new_session()
        session.advance()
        before_state = session.state()
        before_counts = session.counts()

        # Rejected at validation: nothing persisted at all.
        invalid = session.probe.raw(
            "POST",
            f"/api/v1/simulations/{session.id}/manual-decision",
            json=_intent("buy", quantity=-10, rationale="C4 invalid quantity"),
        )
        assert invalid.status_code == 422, invalid.text
        assert session.counts() == before_counts
        assert session.orders() == []

        # Rejected at business validation: the refusal is recorded, the book is not.
        response = session.submit_raw(_intent("sell", quantity=100, rationale="C4 sell without position"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decision"]["decision_type"] == "blocked_action"
        assert body["order"] is None
        assert body["fill"] is None
        assert body["rejection"]["reason"] == "no_position_to_sell"
        assert body["rejection"]["intent_preserved"] is True

        assert session.counts() == {
            "decisions": 2, "decision_records": 0, "orders": 0, "fills": 0,
        }
        after_state = session.state()
        assert after_state["paper_equity"] == before_state["paper_equity"]
        assert after_state["current_position_json"] is None
        assert after_state["current_snapshot_date"] == before_state["current_snapshot_date"]

        # Neither refused intent can resurface as a fill when the bar opens.
        session.advance()
        assert session.counts()["fills"] == 0
        assert session.state()["current_position_json"] is None


class TestC3IsolatedRetry:
    """C3 (in-process): an identical retry after commit replays the decision."""

    def test_c3_identical_retry_after_commit(self, client, auth_headers):
        from tests.conftest import TestSession
        from app.models import SimulationSession

        session_id = _create_session_with_data(client, auth_headers)

        db = TestSession()
        try:
            row = db.query(SimulationSession).filter(
                SimulationSession.id == UUID(session_id)
            ).first()
            assert row is not None, "Session not found in DB"
            assert row.provider_confirmed is True
            assert row.status == "pending"
            assert row.total_decisions == 0
            initial_state_version = row.state_version
        finally:
            db.close()

        _advance(client, auth_headers, session_id)

        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "C3 isolated retry test",
            "idempotency_key": f"c3_isolated_{uuid4().hex}",
            "expected_version": initial_state_version,
        }
        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=payload,
        )
        assert first.status_code in (200, 201), first.text
        decision_id = first.json()["decision"]["id"]

        snapshot = _snapshot(client, auth_headers, session_id)
        assert snapshot["decision_count"] == 2
        assert snapshot["state_version"] == initial_state_version + 1

        retry = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=payload,
        )
        assert retry.status_code in (200, 201), (
            f"replay must return the original decision, got {retry.status_code}: {retry.text}"
        )
        assert retry.json()["decision"]["id"] == decision_id

        after_retry = _snapshot(client, auth_headers, session_id)
        for field in (
            "decision_count", "paper_cash", "paper_equity", "total_decisions",
            "observed_days", "state_version", "current_position",
            "current_snapshot_date",
        ):
            assert after_retry[field] == snapshot[field], f"{field} moved on a replay"


class TestC3RegressionCases:
    """C3 contracts that do not need a real race."""

    def test_new_key_stale_version_returns_409(self, client, auth_headers):
        session_id = _create_session_with_data(client, auth_headers)
        _advance(client, auth_headers, session_id)
        initial_version = _snapshot(client, auth_headers, session_id)["state_version"]

        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "First request",
                "idempotency_key": f"regression_new_{uuid4().hex}",
                "expected_version": initial_version,
            },
        )
        assert first.status_code in (200, 201), first.text
        snapshot = _snapshot(client, auth_headers, session_id)

        stale = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 50,
                "rationale": "Second request with stale version",
                "idempotency_key": f"regression_new_{uuid4().hex}",
                "expected_version": initial_version,
            },
        )
        assert stale.status_code == 409, stale.text
        assert "State version conflict" in stale.json()["detail"]

        after = _snapshot(client, auth_headers, session_id)
        assert after["decision_count"] == snapshot["decision_count"]
        assert after["state_version"] == snapshot["state_version"]

    def test_same_key_different_payload_returns_conflict(self, client, auth_headers):
        session_id = _create_session_with_data(client, auth_headers)
        _advance(client, auth_headers, session_id)
        initial_version = _snapshot(client, auth_headers, session_id)["state_version"]

        key = f"regression_conflict_{uuid4().hex}"
        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Original request",
                "idempotency_key": key,
                "expected_version": initial_version,
            },
        )
        assert first.status_code in (200, 201), first.text
        snapshot = _snapshot(client, auth_headers, session_id)

        conflict = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 200,
                "rationale": "Original request",
                "idempotency_key": key,
                "expected_version": initial_version,
            },
        )
        assert conflict.status_code == 409, conflict.text
        assert "Idempotency key conflict" in conflict.json()["detail"]
        assert _snapshot(client, auth_headers, session_id)["decision_count"] == snapshot["decision_count"]

    @pytest.mark.parametrize("field_change,change_value", [
        ("action", "sell"),
        ("rationale", "Changed rationale"),
    ])
    def test_same_key_different_payload_parameterized(
        self, client, auth_headers, field_change, change_value,
    ):
        session_id = _create_session_with_data(client, auth_headers)
        _advance(client, auth_headers, session_id)
        initial_version = _snapshot(client, auth_headers, session_id)["state_version"]

        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "Original request",
            "idempotency_key": f"regression_param_{field_change}_{uuid4().hex}",
            "expected_version": initial_version,
        }
        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=payload,
        )
        assert first.status_code in (200, 201), first.text
        snapshot = _snapshot(client, auth_headers, session_id)

        changed = dict(payload)
        changed[field_change] = change_value
        conflict = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=changed,
        )
        assert conflict.status_code == 409, conflict.text
        assert "Idempotency key conflict" in conflict.json()["detail"]
        assert _snapshot(client, auth_headers, session_id)["decision_count"] == snapshot["decision_count"]

    def test_cross_user_replay_protection(self, client):
        def register(email):
            client.post("/api/auth/register", json={
                "email": email,
                "password": "testpass123",
                "display_name": email.split("@")[0],
                "user_type": "individual",
            })
            login = client.post("/api/auth/login", json={
                "email": email, "password": "testpass123",
            })
            assert login.status_code == 200, login.text
            return {"Authorization": f"Bearer {login.json()['access_token']}"}

        headers_a = register(f"user-a-{uuid4().hex[:8]}@alethra.io")
        headers_b = register(f"user-b-{uuid4().hex[:8]}@alethra.io")

        session_id = _create_session_with_data(client, headers_a)
        _advance(client, headers_a, session_id)
        initial_version = _snapshot(client, headers_a, session_id)["state_version"]

        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "User A's decision",
            "idempotency_key": f"cross_user_{uuid4().hex}",
            "expected_version": initial_version,
        }
        owned = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=headers_a,
            json=payload,
        )
        assert owned.status_code in (200, 201), owned.text
        decision_id = owned.json()["decision"]["id"]
        snapshot = _snapshot(client, headers_a, session_id)

        replay = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=headers_b,
            json=payload,
        )
        assert replay.status_code in (403, 404), replay.text
        assert decision_id not in replay.text, "decision id was disclosed to another user"
        assert _snapshot(client, headers_a, session_id)["decision_count"] == snapshot["decision_count"]

    def test_replay_after_session_status_change(self, client, auth_headers):
        from tests.conftest import TestSession
        from app.models import SimulationSession
        from app.models.simulation import SimulationStatus

        session_id = _create_session_with_data(client, auth_headers)
        _advance(client, auth_headers, session_id)
        initial_version = _snapshot(client, auth_headers, session_id)["state_version"]

        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "Before status change",
            "idempotency_key": f"status_change_{uuid4().hex}",
            "expected_version": initial_version,
        }
        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=payload,
        )
        assert first.status_code in (200, 201), first.text
        decision_id = first.json()["decision"]["id"]
        snapshot = _snapshot(client, auth_headers, session_id)

        db = TestSession()
        try:
            row = db.query(SimulationSession).filter(
                SimulationSession.id == UUID(session_id)
            ).first()
            row.status = SimulationStatus.completed
            db.commit()
        finally:
            db.close()

        replay = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json=payload,
        )
        assert replay.status_code in (200, 201), replay.text
        assert replay.json()["decision"]["id"] == decision_id

        after = _snapshot(client, auth_headers, session_id)
        assert after["decision_count"] == snapshot["decision_count"]
        assert after["paper_equity"] == snapshot["paper_equity"]
        assert after["total_decisions"] == snapshot["total_decisions"]
        assert after["state_version"] == snapshot["state_version"]
        assert after["status"] == "completed"

        refused = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 50,
                "rationale": "New request after status change",
                "idempotency_key": f"new_key_after_status_{uuid4().hex}",
                "expected_version": snapshot["state_version"],
            },
        )
        assert refused.status_code == 400, refused.text
        assert "Cannot make decisions on 'completed' session" in refused.json()["detail"]
