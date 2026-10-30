"""Manual replay workflow, probed end to end on a disposable live stack.

The workflow under test is the one the product now ships: pick a stored-data
provider, create a replay session, then submit BUY / HOLD / SELL intents. A
manual BUY/SELL is a *pending order* stamped with the bar the user observed; the
next ``advance`` executes it at that bar's open. That is why the assertions below
separate the decision (now) from the fill (next bar) instead of expecting a fill
in the submission response.

Target identity, the probe account and every bar/session/order these tests touch
come from ``tests.live_stack_env``; nothing here reads or writes an owner
database, and the module refuses to run without a declared disposable stack.
"""
import uuid

import pytest

from tests.live_stack_env import (  # noqa: F401  (fixtures registered by import)
    live_probe,
    new_session,
    probe_version,
    skip_reason,
)

_SKIP_REASON = skip_reason()
pytestmark = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=f"live-stack probe: {_SKIP_REASON}",
)

PROVIDER_ID = "twelvedata"
FIRST_DATE = "2024-01-02"
SECOND_DATE = "2024-01-03"
THIRD_DATE = "2024-01-04"
# Harness bars: open = 180 + index, so the second and third opens are 181 / 182.
SECOND_OPEN = 181.0
THIRD_OPEN = 182.0


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class TestProviderSelection:
    """Test 1: the version names the stored data the session will replay."""

    def test_available_providers_shows_stored_data(self, live_probe, probe_version):
        data = live_probe.get(
            f"/api/v1/simulations/versions/{probe_version.id}/available-providers"
        )

        assert data["symbol"] == probe_version.symbol
        assert data["requested_start"] == "2024-01-01"
        assert data["requested_end"] == "2024-01-31"

        providers = data["available_providers"]
        assert len(providers) >= 1
        twelve = next((p for p in providers if p["provider_id"] == PROVIDER_ID), None)
        assert twelve is not None, providers
        assert twelve["bar_count"] == 20
        assert twelve["min_date"] == FIRST_DATE
        assert twelve["max_date"] == "2024-01-30"

    def test_session_creation_with_explicit_provider(self, live_probe, new_session):
        session = new_session()
        state = session.state()

        assert state["provider_id"] == PROVIDER_ID
        assert state["provider_confirmed"] is True
        assert state["status"] == "pending"


class TestManualDecisions:
    """Test 2: BUY / HOLD / SELL on the observed bar, filled on the next bar."""

    def test_buy_uses_first_available_date(self, live_probe, new_session):
        session = new_session()
        first_bar = session.advance()["decision"]
        assert first_bar["snapshot_date"] == FIRST_DATE

        body = session.submit("buy", quantity=100, rationale="Test buy").json()

        # Stamped with the bar the user watched, executed on the next one.
        assert body["decision"]["snapshot_date"] == FIRST_DATE
        assert body["decision"]["decision_type"] == "blocked_action"
        assert body["fill"] is None
        assert body["order"]["status"] == "pending"
        assert body["execution"]["next_window"]["date"] == SECOND_DATE
        assert str(body["execution"]["state"]).lower() in ("awaiting_window", "pending", "awaiting_execution_window")

        filled = session.advance()["decision"]
        assert filled["snapshot_date"] == SECOND_DATE
        manual_fills = filled["decision_detail"]["manual_fills"]
        assert len(manual_fills) == 1
        fill = manual_fills[0]
        assert fill["side"] == "BUY"
        assert fill["execution_date"] == SECOND_DATE
        assert float(fill["execution_price"]) == SECOND_OPEN

    def test_hold_creates_no_fill(self, live_probe, new_session):
        session = new_session()
        session.advance()
        session.submit("buy", quantity=100, rationale="Buy first")
        session.advance()

        before = session.counts()
        hold = session.submit("hold", rationale="Hold position").json()

        assert hold["decision"]["decision_type"] == "no_action"
        assert hold["decision"]["snapshot_date"] == SECOND_DATE
        assert hold["order"] is None
        assert hold["fill"] is None

        # HOLD is journaled (a no_action decision row) but books nothing: no
        # decision record, no pending order, no fill.
        after_hold = session.counts()
        assert after_hold["decisions"] == before["decisions"] + 1
        assert after_hold["decision_records"] == before["decision_records"]
        assert after_hold["orders"] == before["orders"]
        assert after_hold["fills"] == before["fills"]

        # HOLD does not move the cursor; the market clock does.
        session.advance()
        assert session.state()["current_snapshot_date"] == THIRD_DATE
        after = session.counts()
        assert after["orders"] == before["orders"]
        assert after["fills"] == before["fills"]

    def test_sell_closes_position(self, live_probe, new_session):
        session = new_session()
        session.advance()
        buy = session.submit("buy", quantity=100, rationale="Buy").json()
        buy_order_id = buy["order"]["order_id"]
        session.advance()

        sell = session.submit("sell", quantity=100, rationale="Sell").json()
        assert sell["order"]["status"] == "pending"
        assert sell["fill"] is None
        assert sell["execution"]["next_window"]["date"] == THIRD_DATE

        filled = session.advance()["decision"]
        exit_fills = filled["decision_detail"]["manual_fills"]
        assert len(exit_fills) == 1
        assert exit_fills[0]["side"] == "SELL"
        assert float(exit_fills[0]["execution_price"]) == THIRD_OPEN
        assert float(exit_fills[0]["quantity"]) == 100

        assert session.state()["current_position_json"] is None

        # Round trip: bought at 181, sold at 182, no costs configured.
        buy_fills = session.fills(buy_order_id)
        sell_fills = session.fills(sell["order"]["order_id"])
        assert len(buy_fills) == 1 and len(sell_fills) == 1
        assert buy_fills[0]["net_cash_change"] == -SECOND_OPEN * 100
        assert sell_fills[0]["net_cash_change"] == THIRD_OPEN * 100
        assert (
            buy_fills[0]["net_cash_change"] + sell_fills[0]["net_cash_change"]
        ) == 100.0


class TestIdempotency:
    """Test 3: one key, one intent."""

    def test_same_key_same_payload_returns_original(self, live_probe, new_session):
        session = new_session()
        session.advance()
        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "Test",
            "idempotency_key": _key("idem"),
        }

        first = session.submit_raw(payload)
        assert first.status_code == 200, first.text
        counts = session.counts()

        second = session.submit_raw(payload)
        assert second.status_code == 200, second.text

        assert first.json()["decision"]["id"] == second.json()["decision"]["id"]
        assert session.counts() == counts

    def test_same_key_different_payload_conflicts(self, live_probe, new_session):
        session = new_session()
        session.advance()
        key = _key("conflict")
        first = session.submit_raw({
            "action": "buy",
            "quantity": 100,
            "rationale": "First",
            "idempotency_key": key,
        })
        assert first.status_code == 200, first.text
        counts = session.counts()

        conflict = session.submit_raw({
            "action": "buy",
            "quantity": 200,
            "rationale": "Second",
            "idempotency_key": key,
        })
        assert conflict.status_code == 409, conflict.text
        assert "conflict" in conflict.json()["detail"].lower()
        assert session.counts() == counts


class TestPersistence:
    """Test 4: a reopened session keeps its provider, decisions and position."""

    def test_session_preserves_provider_after_reopen(self, live_probe, new_session):
        session = new_session()
        state = session.state()

        assert state["provider_id"] == PROVIDER_ID
        assert state["provider_confirmed"] is True

    def test_decisions_preserved_after_reopen(self, live_probe, new_session):
        session = new_session()
        session.advance()
        session.submit("buy", quantity=100, rationale="Buy")
        session.advance()

        state = session.state()
        assert state["total_decisions"] == 3
        assert state["current_position_json"]["shares"] == 100

        decisions = session.decisions()
        assert len(decisions) == 3
        assert [d["step_number"] for d in decisions] == [1, 2, 3]


class TestLegacyGuard:
    """Test 5: a session without a confirmed provider cannot execute."""

    def test_unconfirmed_session_cannot_execute(self, live_probe, new_session):
        session = new_session()
        session.advance()
        live_probe.set_provider_confirmed(session.id, False)

        counts = session.counts()
        response = session.submit_raw({
            "action": "buy",
            "quantity": 100,
            "rationale": "legacy guard",
            "idempotency_key": _key("legacy"),
        })

        assert response.status_code == 400, response.text
        assert "provider not confirmed" in response.json()["detail"].lower()
        assert session.counts() == counts
        assert session.orders() == []
