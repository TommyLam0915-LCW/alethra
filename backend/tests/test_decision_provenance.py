"""Who wrote a decision, judged only by the credential the server verified.

An API key is a program's credential, so an event written through one is agent
output. A session created by entering a password is labelled human. Everything
else — tokens minted without an authentication-method claim, the local
passwordless test-mode session, historical rows written before provenance was
recorded — stays unknown and is never upgraded.

The consequences under test:

  * the label is stored on the decision row and on the audit event
  * a client cannot select its own provenance via the request body
  * only human-provenance decisions count as confirmed training labels
  * agent/unknown rows are reported as an exclusion, never rewritten
  * consent grants and label revisions require a password-authenticated owner
"""
import uuid
from datetime import date, datetime

import pytest

from tests.conftest import TestSession
from app.auth.api_keys import generate_api_key
from app.auth.jwt import create_access_token
from app.models.audit_event import AuditEvent
from app.models.decision_label import DecisionLabel
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar
from app.models.user import User

INSTRUMENT = "DEMO-ALPHA"

_TRADING_DAYS = [
    date(2024, 1, d)
    for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 24, 25, 26, 29, 30, 31)
]


@pytest.fixture(autouse=True)
def _seed_market_data(setup_db):
    """Stored bars so a locked version can start a simulation session."""
    db = TestSession()
    try:
        for i, day in enumerate(_TRADING_DAYS):
            price = 50.0 + float(i)
            db.add(
                MarketDataBar(
                    instrument_id=INSTRUMENT,
                    provider_id="demo",
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, datetime.min.time()),
                    open=price,
                    high=price + 1.0,
                    low=price - 1.0,
                    close=price + 0.5,
                    volume=1_000_000,
                )
            )
        db.commit()
    finally:
        db.close()
    yield


def _user_id(email: str = "test@alethra.io") -> uuid.UUID:
    db = TestSession()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def _create_locked_experiment(client, headers, title="Provenance"):
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": [INSTRUMENT],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 100000,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    lock = client.post(
        f"/api/v1/experiments/{body['id']}/versions/{body['current_version_id']}/lock",
        headers=headers,
        json={"confirm": True, "reason": "provenance test"},
    )
    assert lock.status_code == 200, lock.text
    return body["id"], body["current_version_id"]


def _create_session(client, headers, version_id) -> str:
    resp = client.post(
        f"/api/v1/simulations/versions/{version_id}",
        headers=headers,
        json={"initial_virtual_cash": 100000},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _api_key_headers(suffix: str = "") -> dict:
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        _, raw_key = generate_api_key(
            db, user.id, f"provenance-test{suffix}", scopes=["read", "write"]
        )
    finally:
        db.close()
    return {"Authorization": f"Bearer {raw_key}"}


def _minted_token_headers() -> dict:
    """A token with no authentication-method claim (legacy/test-mode shape)."""
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        token = create_access_token(data={"sub": str(user.id)})
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def _post_decision(
    client,
    headers,
    session_id,
    key,
    *,
    action="buy",
    quantity=10.0,
    rationale="plan says enter",
    extra=None,
):
    body = {
        "simulation_session_id": session_id,
        "idempotency_key": key,
        "action": action,
        "quantity": quantity,
        "rationale": rationale,
    }
    if extra:
        body.update(extra)
    return client.post("/api/v1/human-decisions", headers=headers, json=body)


def _stored_event(event_id) -> HumanDecisionEvent:
    db = TestSession()
    try:
        return (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.id == uuid.UUID(str(event_id)))
            .one()
        )
    finally:
        db.close()


def _audit_for(event_id) -> AuditEvent:
    db = TestSession()
    try:
        return (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "human_decision",
                AuditEvent.entity_id == str(event_id),
            )
            .one()
        )
    finally:
        db.close()


def _label_count(event_id) -> int:
    db = TestSession()
    try:
        return (
            db.query(DecisionLabel)
            .filter(DecisionLabel.decision_event_id == uuid.UUID(str(event_id)))
            .count()
        )
    finally:
        db.close()


def _grant_consent(client, headers, version_id):
    resp = client.put(
        "/api/v1/ml/personalization/consent",
        headers=headers,
        json={"strategy_version_ids": [version_id], "confirm": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(reasons):
    return {reason["code"] for reason in reasons}


# ---------------------------------------------------------------------------
# 1. The credential decides the label.
# ---------------------------------------------------------------------------


class TestCredentialDeterminesProvenance:

    def test_password_login_records_human(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        resp = _post_decision(client, auth_headers, session_id, "pwd-key-1")
        assert resp.status_code == 201, resp.text
        event_id = resp.json()["id"]
        assert resp.json()["actor_type"] == "human"

        assert _stored_event(event_id).actor_type == "human"
        audit = _audit_for(event_id)
        assert audit.actor_type == "human"
        assert audit.payload_json["actor_type"] == "human"

    def test_api_key_records_agent(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)
        agent = _api_key_headers("agent")

        resp = _post_decision(client, agent, session_id, "agent-key-1")
        assert resp.status_code == 201, resp.text
        event_id = resp.json()["id"]
        assert resp.json()["actor_type"] == "agent"

        assert _stored_event(event_id).actor_type == "agent"
        audit = _audit_for(event_id)
        assert audit.actor_type == "agent"
        assert audit.payload_json["actor_type"] == "agent"

        # The owner reading the same event through their password session sees
        # the stored provenance, not a default.
        listing = client.get(
            f"/api/v1/human-decisions?simulation_session_id={session_id}",
            headers=auth_headers,
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()[0]["actor_type"] == "agent"

    def test_token_without_auth_method_is_unknown(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        resp = _post_decision(
            client, _minted_token_headers(), session_id, "minted-key-1"
        )
        assert resp.status_code == 201, resp.text
        event_id = resp.json()["id"]
        assert resp.json()["actor_type"] == "unknown"

        assert _stored_event(event_id).actor_type == "unknown"
        assert _audit_for(event_id).actor_type == "unknown"

    def test_client_cannot_choose_its_own_provenance(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        forced_body = {
            "actor_type": "human",
            "metadata": {"actor_type": "human"},
        }
        agent = _api_key_headers("force")
        agent_resp = _post_decision(
            client, agent, session_id, "force-agent-1", extra=forced_body
        )
        assert agent_resp.status_code == 201, agent_resp.text
        assert agent_resp.json()["actor_type"] == "agent"
        stored = _stored_event(agent_resp.json()["id"])
        assert stored.actor_type == "agent"
        # The client-supplied metadata is kept as client data only.
        assert stored.metadata_json["actor_type"] == "human"

        human_resp = _post_decision(
            client,
            auth_headers,
            session_id,
            "force-human-1",
            extra={"actor_type": "agent", "metadata": {"actor_type": "agent"}},
        )
        assert human_resp.status_code == 201, human_resp.text
        assert human_resp.json()["actor_type"] == "human"
        assert _stored_event(human_resp.json()["id"]).actor_type == "human"

    def test_correction_uses_the_correction_credential(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        original = _post_decision(client, auth_headers, session_id, "corr-original")
        assert original.status_code == 201, original.text
        original_id = original.json()["id"]

        correction = client.post(
            f"/api/v1/human-decisions/{original_id}/correct",
            headers=_api_key_headers("corr"),
            json={
                "action": "sell",
                "quantity": 5.0,
                "rationale": "agent correction",
                "correction_reason": "replayed by automation",
            },
        )
        assert correction.status_code == 201, correction.text
        body = correction.json()
        assert body["actor_type"] == "agent"
        assert body["supersedes_event_id"] == original_id

        # The original is untouched: a correction is a new row.
        original_row = _stored_event(original_id)
        assert original_row.actor_type == "human"
        assert original_row.action is HumanDecisionAction.buy


# ---------------------------------------------------------------------------
# 2. Only human-provenance rows count as owner-confirmed labels.
# ---------------------------------------------------------------------------


class TestPolicyConsequences:

    def test_agent_decisions_are_an_exclusion_not_a_label(
        self, client, auth_headers
    ):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        human = _post_decision(client, auth_headers, session_id, "mix-human")
        agent = _post_decision(
            client,
            _api_key_headers("mix"),
            session_id,
            "mix-agent",
            rationale="agent proposed entry",
        )
        assert human.status_code == 201, human.text
        assert agent.status_code == 201, agent.text

        _grant_consent(client, auth_headers, version_id)

        eligibility = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        )
        assert eligibility.status_code == 200, eligibility.text
        body = eligibility.json()
        assert body["dataset"]["excluded"]["unverified_provenance"] == 1
        warning_codes = _codes(body["warnings"])
        assert "UNVERIFIED_DECISION_PROVENANCE" in warning_codes
        # Not a refusal: holding historical rows must never lock the account
        # out of eligibility forever.
        assert "UNVERIFIED_DECISION_PROVENANCE" not in _codes(body["reasons"])

        preview = client.post(
            "/api/v1/ml/personal/snapshots/preview",
            headers=auth_headers,
            json={"strategy_version_id": version_id},
        )
        assert preview.status_code == 200, preview.text
        breakdown = preview.json()["exclusion_breakdown"]
        assert breakdown["unverified_provenance"] == 1
        assert breakdown["unconfirmed"] == 1
        assert preview.json()["included_count"] == 0

    def test_legacy_rows_stay_unknown_and_are_not_rewritten(
        self, client, auth_headers
    ):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)
        _grant_consent(client, auth_headers, version_id)

        owner_id = _user_id()
        db = TestSession()
        try:
            legacy = HumanDecisionEvent(
                owner_id=owner_id,
                simulation_session_id=uuid.UUID(session_id),
                idempotency_key="legacy-before-provenance",
                action=HumanDecisionAction.buy,
                quantity=10.0,
                rationale="recorded before the provenance column existed",
                server_strategy_version_id=uuid.UUID(version_id),
                server_instrument_id=INSTRUMENT,
                server_step_number=1,
                server_snapshot_date="2024-01-02",
                server_data_cutoff_at="2024-01-31",
                execution_status=HumanDecisionExecutionStatus.record_only,
            )
            db.add(legacy)
            db.commit()
            db.refresh(legacy)
            legacy_id = legacy.id
        finally:
            db.close()

        assert _stored_event(legacy_id).actor_type == "unknown"

        eligibility = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()
        assert eligibility["dataset"]["excluded"]["unverified_provenance"] == 1
        assert "UNVERIFIED_DECISION_PROVENANCE" in _codes(eligibility["warnings"])

        # Reading eligibility must not promote the row.
        assert _stored_event(legacy_id).actor_type == "unknown"

    def test_review_usable_flag_requires_human_provenance(
        self, client, auth_headers
    ):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)

        agent_resp = _post_decision(
            client, _api_key_headers("review"), session_id, "review-agent"
        )
        assert agent_resp.status_code == 201, agent_resp.text
        agent_id = agent_resp.json()["id"]

        # The owner may review an agent-written decision, but confirming its
        # label cannot turn it into an owner decision.
        confirmed = client.post(
            f"/api/v1/decision-review/{agent_id}/labels",
            headers=auth_headers,
            json={"state": "confirmed"},
        )
        assert confirmed.status_code == 201, confirmed.text

        detail = client.get(
            f"/api/v1/decision-review/{agent_id}", headers=auth_headers
        )
        assert detail.status_code == 200, detail.text
        decision = detail.json()["decision"]
        assert decision["actor_type"] == "agent"
        assert decision["usable_for_training"] is False

        listing = client.get(
            f"/api/v1/decision-review?strategy_version_id={version_id}",
            headers=auth_headers,
        ).json()
        assert listing["summary"]["confirmed"] == 0
        assert listing["summary"]["unverified_provenance"] == 1
        assert listing["summary"]["unreviewed"] == 0

        human_resp = _post_decision(
            client, auth_headers, session_id, "review-human"
        )
        assert human_resp.status_code == 201, human_resp.text
        human_id = human_resp.json()["id"]
        assert (
            client.post(
                f"/api/v1/decision-review/{human_id}/labels",
                headers=auth_headers,
                json={"state": "confirmed"},
            ).status_code
            == 201
        )
        human_detail = client.get(
            f"/api/v1/decision-review/{human_id}", headers=auth_headers
        ).json()["decision"]
        assert human_detail["actor_type"] == "human"
        assert human_detail["usable_for_training"] is True

    def test_experiment_record_exposes_actor_type(self, client, auth_headers):
        exp_id, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)
        assert _post_decision(client, auth_headers, session_id, "rec-human").status_code == 201
        agent_resp = _post_decision(
            client, _api_key_headers("record"), session_id, "rec-agent"
        )
        assert agent_resp.status_code == 201, agent_resp.text

        record = client.get(
            f"/api/v1/experiments/{exp_id}/record", headers=auth_headers
        )
        assert record.status_code == 200, record.text
        sessions = record.json()["decision_review"]["simulation_sessions"]
        entries = [entry for s in sessions for entry in s["human_decisions"]]
        assert {entry["actor_type"] for entry in entries} == {"human", "agent"}


# ---------------------------------------------------------------------------
# 3. Owner judgement acts require a password-authenticated session.
# ---------------------------------------------------------------------------


class TestOwnerActsRequireHumanProvenance:

    def test_consent_grant_refused_for_agent_and_unknown(self, client, auth_headers):
        _, version_id = _create_locked_experiment(client, auth_headers)
        payload = {"strategy_version_ids": [version_id], "confirm": True}

        for headers in (_api_key_headers("consent"), _minted_token_headers()):
            refused = client.put(
                "/api/v1/ml/personalization/consent", headers=headers, json=payload
            )
            assert refused.status_code == 403, refused.text

        granted = client.put(
            "/api/v1/ml/personalization/consent", headers=auth_headers, json=payload
        )
        assert granted.status_code == 200, granted.text

        db = TestSession()
        try:
            events = (
                db.query(AuditEvent)
                .filter(AuditEvent.entity_type == "personalization_consent")
                .all()
            )
        finally:
            db.close()
        granted_events = [
            event
            for event in events
            if event.event_type == "personalization_consent_granted"
        ]
        assert len(granted_events) == 1
        assert granted_events[0].actor_type == "human"

    def test_consent_revocation_records_the_actual_credential(
        self, client, auth_headers
    ):
        _, version_id = _create_locked_experiment(client, auth_headers)
        _grant_consent(client, auth_headers, version_id)

        revoked = client.delete(
            "/api/v1/ml/personalization/consent",
            headers=_api_key_headers("revoke"),
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] == 1

        db = TestSession()
        try:
            event = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.entity_type == "personalization_consent",
                    AuditEvent.event_type == "personalization_consent_revoked",
                )
                .one()
            )
        finally:
            db.close()
        assert event.actor_type == "agent"
        assert event.payload_json["actor_type"] == "agent"

    def test_label_revision_refused_for_agent_and_unknown(
        self, client, auth_headers
    ):
        _, version_id = _create_locked_experiment(client, auth_headers)
        session_id = _create_session(client, auth_headers, version_id)
        event = _post_decision(client, auth_headers, session_id, "label-gate")
        assert event.status_code == 201, event.text
        event_id = event.json()["id"]

        for headers in (_api_key_headers("label"), _minted_token_headers()):
            refused = client.post(
                f"/api/v1/decision-review/{event_id}/labels",
                headers=headers,
                json={"state": "confirmed"},
            )
            assert refused.status_code == 403, refused.text

        assert _label_count(event_id) == 0

        allowed = client.post(
            f"/api/v1/decision-review/{event_id}/labels",
            headers=auth_headers,
            json={"state": "confirmed"},
        )
        assert allowed.status_code == 201, allowed.text

        db = TestSession()
        try:
            audit = (
                db.query(AuditEvent)
                .filter(
                    AuditEvent.entity_type == "decision_label",
                    AuditEvent.entity_id == allowed.json()["id"],
                )
                .one()
            )
        finally:
            db.close()
        assert audit.actor_type == "human"
