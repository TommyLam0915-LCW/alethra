"""C1-C4 safety cases probed over HTTP against a disposable live stack.

Same four business properties as the original module, expressed against the
current replay contract: a manual BUY/SELL is submitted as a *pending* order
stamped with the bar the user has observed, and the next ``advance`` executes it
at that bar's open. A submission therefore books no cash, position or fill, and
the probes assert both halves explicitly:

    C1  concurrent duplicate key   -> one decision, one order, one fill (after
                                      the window opens)
    C2  stale state version race   -> one winner, one 409, one order
    C3  response lost after commit -> replay returns the original decision
    C4  invalid payload            -> 422, no decision/order/fill, no book move

Target identity, the probe account and every row these tests touch come from
``tests.live_stack_env``; nothing here reads or writes an owner database.
"""
import uuid

import pytest

from tests.live_stack_env import (  # noqa: F401  (fixtures registered by import)
    live_probe,
    new_session,
    probe_version,
    skip_reason,
    submit_concurrently,
)

_SKIP_REASON = skip_reason()
pytestmark = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=f"live-stack probe: {_SKIP_REASON}",
)


def _intent(action: str, *, quantity=None, rationale: str = "", key=None):
    payload = {
        "action": action,
        "rationale": rationale or f"probe {action}",
        "idempotency_key": key or f"probe-{uuid.uuid4().hex}",
    }
    if quantity is not None:
        payload["quantity"] = quantity
    return payload


def _manual_decisions(session):
    """Decisions this session recorded from a human submission."""
    return [
        decision
        for decision in session.decisions()
        if (decision["decision_detail"] or {}).get("action")
    ]


class TestC1ConcurrentDuplicate:
    """C1: Concurrent duplicate - same key, same payload, simultaneous requests."""

    def test_concurrent_duplicate_same_key(self, live_probe, new_session):
        session = new_session()
        first_bar = session.advance()["decision"]
        assert first_bar["snapshot_date"] == "2024-01-02"

        payload = _intent("buy", quantity=100, rationale="C1 concurrent test")
        responses = submit_concurrently(session, [payload, payload])

        assert len(responses) == 2
        statuses = sorted(response.status_code for response in responses)
        assert all(status in (200, 201, 409) for status in statuses), statuses
        assert statuses[0] in (200, 201), f"no request succeeded: {statuses}"

        # Pending is not booked: one decision and one order, no fill yet.
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }
        manual = _manual_decisions(session)
        assert len(manual) == 1
        assert manual[0]["snapshot_date"] == "2024-01-02"

        orders = session.orders()
        assert len(orders) == 1
        order = orders[0]["order"]
        assert order["status"] == "pending"
        assert order["quantity"] == 100
        assert orders[0]["execution"]["next_window"]["date"] == "2024-01-03"

        state = session.state()
        assert state["paper_equity"] == 100000.0
        assert state["current_position_json"] is None

        # The next bar is the only execution window: exactly one fill.
        session.advance()
        assert session.counts() == {
            "decisions": 3, "decision_records": 1, "orders": 1, "fills": 1,
        }
        fills = session.fills(order["id"])
        assert len(fills) == 1
        assert fills[0]["execution_date"] == "2024-01-03"
        assert fills[0]["quantity"] == 100
        assert session.state()["paper_equity"] != 100000.0


class TestC2DifferentKeysStaleStep:
    """C2: Different keys competing for the same observed state version."""

    def test_different_keys_same_step(self, live_probe, new_session):
        session = new_session()
        session.advance()
        observed_version = session.state()["state_version"]

        payload_a = _intent("buy", quantity=50, rationale="C2 contender A", key=f"c2a-{uuid.uuid4().hex}")
        payload_b = _intent("buy", quantity=50, rationale="C2 contender B", key=f"c2b-{uuid.uuid4().hex}")
        for payload in (payload_a, payload_b):
            payload["expected_version"] = observed_version

        responses = submit_concurrently(session, [payload_a, payload_b])
        statuses = sorted(response.status_code for response in responses)
        assert statuses.count(200) == 1, f"expected exactly one winner, got {statuses}"
        assert statuses.count(409) == 1, f"expected exactly one stale-version loss, got {statuses}"
        loser = next(response for response in responses if response.status_code == 409)
        assert "State version conflict" in loser.json()["detail"]

        # The rejected contender wrote nothing: one manual decision, one order.
        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }
        manual = _manual_decisions(session)
        assert [decision["step_number"] for decision in manual] == [2]
        steps = [decision["step_number"] for decision in session.decisions()]
        assert steps == [1, 2]
        assert session.state()["total_decisions"] == 2


class TestC3ResponseLostAfterCommit:
    """C3: Response lost after commit - retry with the same key."""

    def test_retry_after_commit(self, live_probe, new_session):
        session = new_session()
        session.advance()
        key = f"c3-retry-{uuid.uuid4().hex}"
        payload = _intent("buy", quantity=75, rationale="C3 retry test", key=key)

        first = session.submit_raw(payload)
        assert first.status_code == 200, first.text
        decision_id = first.json()["decision"]["id"]

        replay = session.submit_raw(payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["decision"]["id"] == decision_id

        assert session.counts() == {
            "decisions": 2, "decision_records": 1, "orders": 1, "fills": 0,
        }

        # Replaying after the window opened still returns the original decision.
        session.advance()
        after_fill = session.counts()
        assert after_fill == {
            "decisions": 3, "decision_records": 1, "orders": 1, "fills": 1,
        }
        late_replay = session.submit_raw(payload)
        assert late_replay.status_code == 200, late_replay.text
        assert late_replay.json()["decision"]["id"] == decision_id
        assert session.counts() == after_fill


class TestC4TransactionFailure:
    """C4: Invalid payload must not leave partial state."""

    def test_invalid_payload_no_partial_state(self, live_probe, new_session):
        session = new_session()
        session.advance()
        before_state = session.state()
        before_counts = session.counts()

        response = session.probe.raw(
            "POST",
            f"/api/v1/simulations/{session.id}/manual-decision",
            json=_intent("buy", quantity=-10, rationale="C4 invalid quantity"),
        )
        assert response.status_code == 422, response.text

        assert session.counts() == before_counts
        after_state = session.state()
        assert after_state["paper_equity"] == before_state["paper_equity"]
        assert after_state["current_snapshot_date"] == before_state["current_snapshot_date"]
        assert after_state["state_version"] == before_state["state_version"]
        assert session.orders() == []

        # The refused intent cannot resurface as a fill when the bar opens.
        session.advance()
        assert session.counts()["fills"] == 0
        assert session.state()["current_position_json"] is None
