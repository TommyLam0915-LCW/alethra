"""A replay decision is also a training-label candidate.

The personalization gate counts owner-confirmed ``HumanDecisionEvent`` rows. The
replay UI submits through ``POST /simulations/{id}/manual-decision``, which used
to write only the replay's own decision row, so a password-authenticated owner
could never produce a label by using the product. This module pins the bridge:

  * a password-session submission records a ``human`` event beside the replay
    decision, in one transaction, with the session's own market-clock context
  * an API-key submission records ``agent`` — never owner-confirmed
  * HOLD stores no quantity even though the replay request may carry one
  * an idempotent replay writes one event, not two
  * a key already used by a differently-worded event is a conflict, and neither
    book gets a row

Nothing here grants consent, confirms labels or trains. It only proves the
write path that makes those steps reachable through the UI.
"""

import uuid
from datetime import date, datetime, timedelta

import pytest

from tests.conftest import TestSession
from tests.test_manual_decision_books_linked_portfolio import (
    INITIAL_CASH,
    SYMBOL,
    _advance,
    _link_portfolio,
    _locked_version,
    _start_session,
    _submit,
    seed_authored_bars,
)

from app.auth.api_keys import generate_api_key
from app.models.audit_event import AuditEvent
from app.models.human_decision import HumanDecisionEvent
from app.models.simulation import SimulationDecision, SimulationSession
from app.models.user import User


@pytest.fixture(autouse=True)
def _seed_bound_dataset(setup_db):
    db = TestSession()
    try:
        seed_authored_bars(db)
    finally:
        db.close()
    yield


def _api_key_headers(name="label-capture"):
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        _, raw_key = generate_api_key(db, user.id, name, scopes=["read", "write"])
    finally:
        db.close()
    return {"Authorization": f"Bearer {raw_key}"}


def _session_row(session_id):
    db = TestSession()
    try:
        return db.query(SimulationSession).filter(
            SimulationSession.id == uuid.UUID(str(session_id))
        ).one()
    finally:
        db.close()


def _events(session_id, owner_events_only=False):
    db = TestSession()
    try:
        query = db.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.simulation_session_id == uuid.UUID(str(session_id))
        )
        return query.order_by(HumanDecisionEvent.created_at.asc()).all()
    finally:
        db.close()


def _decisions(session_id):
    db = TestSession()
    try:
        return db.query(SimulationDecision).filter(
            SimulationDecision.session_id == uuid.UUID(str(session_id))
        ).all()
    finally:
        db.close()


def _decisions_with_key(session_id, key):
    return [row for row in _decisions(session_id) if row.idempotency_key == key]


def _audit(event_id):
    db = TestSession()
    try:
        return db.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.entity_id == str(event_id),
            AuditEvent.event_type == "human_decision_created",
        ).one()
    finally:
        db.close()


def _advanced_session(client, headers):
    version_id = _locked_version(client, headers)
    session_id = _start_session(client, headers, version_id, cash=INITIAL_CASH)
    _link_portfolio(client, headers, session_id, cash=INITIAL_CASH)
    advance = _advance(client, headers, session_id)
    assert advance.status_code == 200, advance.text
    return version_id, session_id


class TestManualDecisionLabelCapture:

    def test_buy_records_human_event_with_session_clock(self, client, auth_headers):
        version_id, session_id = _advanced_session(client, auth_headers)

        resp = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=20, key="capture-buy-1",
        )
        assert resp.status_code == 200, resp.text

        events = _events(session_id)
        assert len(events) == 1
        event = events[0]
        row = _session_row(session_id)

        assert event.action.value == "buy"
        assert event.quantity == 20.0
        assert event.rationale == "a stated thesis"
        assert event.actor_type == "human"
        assert event.execution_status.value == "record_only"
        assert event.supersedes_event_id is None
        assert str(event.server_strategy_version_id) == version_id
        assert event.server_instrument_id == SYMBOL
        assert event.server_snapshot_date == row.current_snapshot_date
        assert event.server_step_number == (row.observed_days or 0) + 1
        # Daily replay: the observed bar closed at the end of its date.
        assert event.decision_instant == datetime.combine(
            date.fromisoformat(row.current_snapshot_date) + timedelta(days=1),
            datetime.min.time(),
        )
        assert event.metadata_json["source"] == "manual_decision"

        audit = _audit(event.id)
        assert audit.actor_type == "human"

        listed = client.get(
            "/api/v1/human-decisions", headers=auth_headers,
            params={"simulation_session_id": session_id},
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [str(event.id)]
        assert listed.json()[0]["actor_type"] == "human"

    def test_api_key_submission_is_agent_provenance(self, client, auth_headers):
        _, session_id = _advanced_session(client, auth_headers)

        resp = _submit(
            client, _api_key_headers(), session_id,
            action="buy", quantity=5, key="capture-agent-1",
        )
        assert resp.status_code == 200, resp.text

        events = _events(session_id)
        assert len(events) == 1
        assert events[0].actor_type == "agent"

    def test_hold_stores_no_quantity(self, client, auth_headers):
        _, session_id = _advanced_session(client, auth_headers)

        resp = _submit(
            client, auth_headers, session_id,
            action="hold", quantity=3, key="capture-hold-1",
        )
        assert resp.status_code == 200, resp.text

        events = _events(session_id)
        assert len(events) == 1
        assert events[0].action.value == "hold"
        assert events[0].quantity is None

    def test_idempotent_replay_writes_one_event(self, client, auth_headers):
        _, session_id = _advanced_session(client, auth_headers)

        first = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=20, key="capture-replay-1",
        )
        second = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=20, key="capture-replay-1",
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(_events(session_id)) == 1
        assert len(_decisions_with_key(session_id, "capture-replay-1")) == 1

    def test_same_key_different_payload_is_a_conflict(self, client, auth_headers):
        _, session_id = _advanced_session(client, auth_headers)

        first = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=10, key="capture-conflict-1",
        )
        second = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=11, key="capture-conflict-1",
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 409
        assert len(_events(session_id)) == 1
        assert len(_decisions_with_key(session_id, "capture-conflict-1")) == 1

    def test_key_used_by_a_different_ledger_event_is_refused(self, client, auth_headers):
        _, session_id = _advanced_session(client, auth_headers)

        ledger = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "capture-shared-1",
            "action": "sell",
            "quantity": 1.0,
            "rationale": "written through the ledger endpoint",
        })
        assert ledger.status_code == 201, ledger.text

        resp = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=10, key="capture-shared-1",
        )
        assert resp.status_code == 409
        assert len(_events(session_id)) == 1
        assert _decisions_with_key(session_id, "capture-shared-1") == []

    def test_unobserved_session_writes_no_event(self, client, auth_headers):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id, cash=INITIAL_CASH)

        resp = _submit(
            client, auth_headers, session_id,
            action="buy", quantity=10, key="capture-no-bar-1",
        )
        assert resp.status_code == 400, resp.text
        assert _events(session_id) == []
        assert _decisions(session_id) == []
