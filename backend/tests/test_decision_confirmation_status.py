"""Recording a decision and confirming it for training are separate acts.

The only confirmation this product accepts is an explicit review revision
written through a password-authenticated owner session. An API-key decision
(agent output) stays pending; a rationale that happens to say
"agent-generated" is text and changes nothing; a confirmation revision written
by a non-human channel is reported, never promoted. Without a real owner
confirmation the dataset is NOT_ELIGIBLE.
"""
import uuid
from datetime import date, datetime

import pytest

from tests.conftest import TestSession
from app.auth.api_keys import generate_api_key
from app.models.decision_label import DecisionLabel, DecisionLabelState
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


def _create_locked_experiment(client, headers, title="Confirmation"):
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
        json={"confirm": True, "reason": "confirmation test"},
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
            db, user.id, f"confirmation-test{suffix}", scopes=["read", "write"]
        )
    finally:
        db.close()
    return {"Authorization": f"Bearer {raw_key}"}


def _post_decision(client, headers, session_id, key, rationale="plan says enter"):
    return client.post(
        "/api/v1/human-decisions",
        headers=headers,
        json={
            "simulation_session_id": session_id,
            "idempotency_key": key,
            "action": "buy",
            "quantity": 10.0,
            "rationale": rationale,
        },
    )


def _grant_consent(client, headers, version_id):
    resp = client.put(
        "/api/v1/ml/personalization/consent",
        headers=headers,
        json={"strategy_version_ids": [version_id], "confirm": True},
    )
    assert resp.status_code == 200, resp.text


def _eligibility(client, headers) -> dict:
    resp = client.get("/api/v1/ml/personalization/eligibility", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(body) -> set:
    return {reason["code"] for reason in body["reasons"]}


def _warning_codes(body) -> set:
    return {warning["code"] for warning in body["warnings"]}


def _add_human_event(owner_id, version_id) -> uuid.UUID:
    db = TestSession()
    try:
        event = HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            action=HumanDecisionAction.buy,
            quantity=10.0,
            rationale="plan says enter",
            server_strategy_version_id=version_id,
            server_instrument_id=INSTRUMENT,
            server_step_number=1,
            server_snapshot_date="2024-01-10",
            server_data_cutoff_at="2024-01-31",
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id
    finally:
        db.close()


def _add_label(owner_id, event_id, *, actor_type, state="confirmed", action="buy"):
    db = TestSession()
    try:
        db.add(
            DecisionLabel(
                owner_id=owner_id,
                decision_event_id=event_id,
                revision_number=1,
                state=DecisionLabelState(state),
                action_label=action if state == "confirmed" else None,
                exclusion_reason="test_trade" if state == "excluded" else None,
                author_id=owner_id,
                actor_type=actor_type,
            )
        )
        db.commit()
    finally:
        db.close()


def _label_rows(event_id) -> int:
    db = TestSession()
    try:
        return (
            db.query(DecisionLabel)
            .filter(DecisionLabel.decision_event_id == event_id)
            .count()
        )
    finally:
        db.close()


def test_agent_decision_stays_pending_and_marker_text_does_not_confirm(
    client, auth_headers
):
    owner_id = _user_id()
    _, version_id = _create_locked_experiment(client, auth_headers)
    session_id = _create_session(client, auth_headers, version_id)

    resp = _post_decision(
        client,
        _api_key_headers(),
        session_id,
        "agent-marker",
        rationale="agent-generated rationale: buy on strength",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["actor_type"] == "agent"
    assert body["confirmation_status"] == "pending_owner_confirmation"
    assert body["confirmed_by"] is None
    assert body["confirmation_provenance"] is None
    assert body["owner_verified"] is False

    _grant_consent(client, auth_headers, version_id)
    eligibility = _eligibility(client, auth_headers)
    assert eligibility["status"] == "NOT_ELIGIBLE"
    assert eligibility["dataset"]["eligible_events"] == 0
    assert eligibility["dataset"]["excluded"]["unverified_provenance"] == 1
    assert eligibility["dataset"]["excluded"]["pending_owner_confirmation"] == 0
    assert "INSUFFICIENT_CONFIRMED_DECISIONS" in _codes(eligibility)
    assert "OWNER_CONFIRMATION_REQUIRED" not in _codes(eligibility)

    review = client.get(
        f"/api/v1/decision-review?strategy_version_id={version_id}",
        headers=auth_headers,
    ).json()
    row = review["decisions"][0]
    assert row["actor_type"] == "agent"
    assert row["confirmation_status"] == "pending_owner_confirmation"
    assert row["confirmed_by"] is None
    assert row["usable_for_training"] is False
    assert row["label"] is None


def test_human_recording_needs_an_explicit_owner_confirmation(client, auth_headers):
    _, version_id = _create_locked_experiment(client, auth_headers)
    session_id = _create_session(client, auth_headers, version_id)
    owner_id = _user_id()

    recorded = _post_decision(client, auth_headers, session_id, "human-pending")
    assert recorded.status_code == 201, recorded.text
    event_id = recorded.json()["id"]
    assert recorded.json()["actor_type"] == "human"
    assert recorded.json()["confirmation_status"] == "pending_owner_confirmation"
    assert recorded.json()["confirmed_by"] is None
    assert recorded.json()["owner_verified"] is False

    _grant_consent(client, auth_headers, version_id)
    before = _eligibility(client, auth_headers)
    assert before["status"] == "NOT_ELIGIBLE"
    assert before["dataset"]["eligible_events"] == 0
    assert before["dataset"]["excluded"]["pending_owner_confirmation"] == 1
    assert "OWNER_CONFIRMATION_REQUIRED" in _codes(before)

    confirmed = client.post(
        f"/api/v1/decision-review/{event_id}/labels",
        headers=auth_headers,
        json={"state": "confirmed"},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["state"] == "confirmed"
    assert confirmed.json()["actor_type"] == "human"
    assert confirmed.json()["author_id"] == str(owner_id)

    detail = client.get(
        f"/api/v1/human-decisions/{event_id}", headers=auth_headers
    ).json()
    assert detail["confirmation_status"] == "confirmed"
    assert detail["confirmed_by"] == str(owner_id)
    assert detail["confirmation_provenance"] == "human"
    assert detail["owner_verified"] is True
    assert detail["confirmation_label_id"] == confirmed.json()["id"]
    assert detail["confirmed_at"] is not None

    after = _eligibility(client, auth_headers)
    assert after["dataset"]["eligible_events"] == 1
    assert after["dataset"]["excluded"]["pending_owner_confirmation"] == 0
    assert "OWNER_CONFIRMATION_REQUIRED" not in _codes(after)


def test_confirmation_written_by_a_non_human_channel_is_not_counted(
    client, auth_headers
):
    owner_id = _user_id()
    _, version_id = _create_locked_experiment(client, auth_headers)
    version_uuid = uuid.UUID(version_id)
    event_id = _add_human_event(owner_id, version_uuid)
    _add_label(owner_id, event_id, actor_type="agent")
    _grant_consent(client, auth_headers, version_id)

    detail = client.get(
        f"/api/v1/human-decisions/{event_id}", headers=auth_headers
    ).json()
    assert detail["confirmation_status"] == "confirmed"
    assert detail["confirmation_provenance"] == "agent"
    assert detail["owner_verified"] is False

    eligibility = _eligibility(client, auth_headers)
    assert eligibility["status"] == "NOT_ELIGIBLE"
    assert eligibility["dataset"]["eligible_events"] == 0
    assert eligibility["dataset"]["excluded"]["unverified_confirmation"] == 1
    assert eligibility["dataset"]["excluded"]["pending_owner_confirmation"] == 0
    assert "UNVERIFIED_CONFIRMATION_PROVENANCE" in _warning_codes(eligibility)
    assert "OWNER_CONFIRMATION_REQUIRED" not in _codes(eligibility)

    review = client.get(
        f"/api/v1/decision-review?strategy_version_id={version_id}",
        headers=auth_headers,
    ).json()
    row = review["decisions"][0]
    assert row["confirmation_provenance"] == "agent"
    assert row["usable_for_training"] is False
    assert review["summary"]["unreviewed"] == 1


def test_label_api_refuses_an_agent_credential(client, auth_headers):
    _, version_id = _create_locked_experiment(client, auth_headers)
    session_id = _create_session(client, auth_headers, version_id)
    recorded = _post_decision(client, auth_headers, session_id, "human-for-403")
    assert recorded.status_code == 201, recorded.text
    event_id = recorded.json()["id"]

    refused = client.post(
        f"/api/v1/decision-review/{event_id}/labels",
        headers=_api_key_headers("label-403"),
        json={"state": "confirmed"},
    )
    assert refused.status_code == 403, refused.text
    assert _label_rows(uuid.UUID(event_id)) == 0


def test_owner_exclusion_is_reported_but_never_training_data(client, auth_headers):
    owner_id = _user_id()
    _, version_id = _create_locked_experiment(client, auth_headers)
    event_id = _add_human_event(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)

    excluded = client.post(
        f"/api/v1/decision-review/{event_id}/labels",
        headers=auth_headers,
        json={"state": "excluded", "exclusion_reason": "test_trade"},
    )
    assert excluded.status_code == 201, excluded.text
    assert excluded.json()["state"] == "excluded"
    assert excluded.json()["actor_type"] == "human"

    eligibility = _eligibility(client, auth_headers)
    assert eligibility["status"] == "NOT_ELIGIBLE"
    assert eligibility["dataset"]["eligible_events"] == 0
    assert eligibility["dataset"]["excluded"]["excluded_by_owner"] == 1
    assert "OWNER_CONFIRMATION_REQUIRED" not in _codes(eligibility)

    review = client.get(
        f"/api/v1/decision-review?strategy_version_id={version_id}",
        headers=auth_headers,
    ).json()
    assert review["summary"]["excluded"] == 1
    assert review["decisions"][0]["confirmation_status"] == "excluded"
    assert review["decisions"][0]["usable_for_training"] is False

    detail = client.get(
        f"/api/v1/human-decisions/{event_id}", headers=auth_headers
    ).json()
    assert detail["confirmation_status"] == "excluded"
    assert detail["owner_verified"] is False
