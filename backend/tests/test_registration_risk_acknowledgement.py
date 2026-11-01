"""An account must not exist without an explicit risk-disclosure acknowledgement.

Registration is the only path that creates a customer identity, so it is the
only place the disclosure can be attached. These tests pin the refusal, the
absence of any side effect when refused, the durable acknowledgement record, and
that the refusal cannot be used to probe which emails are registered.
"""

from app.api.auth import RISK_DISCLOSURE_AS_OF, RISK_DISCLOSURE_PATH
from app.models.audit_event import AuditEvent
from app.models.user import User
from tests.conftest import TestSession

CONSENT_DETAIL_FRAGMENT = "risk disclosure at /legal"


def _payload(email: str, **overrides):
    payload = {
        "email": email,
        "password": "testpass123",
        "display_name": "Consent Test",
        "user_type": "individual",
    }
    payload.update(overrides)
    return payload


def _user_and_events(email: str):
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            return None, []
        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "user",
                AuditEvent.entity_id == str(user.id),
            )
            .all()
        )
        return user, events
    finally:
        db.close()


def _register(client, email: str, **overrides):
    return client.post("/api/auth/register", json=_payload(email, **overrides))


def test_omitting_the_acknowledgement_is_refused_and_creates_no_account(client):
    response = _register(client, "no-consent@alethra.io")

    assert response.status_code == 400
    assert CONSENT_DETAIL_FRAGMENT in response.json()["detail"]
    user, events = _user_and_events("no-consent@alethra.io")
    assert user is None
    assert events == []


def test_explicit_false_is_refused_the_same_way(client):
    response = _register(client, "false-consent@alethra.io", risk_acknowledged=False)

    assert response.status_code == 400
    assert CONSENT_DETAIL_FRAGMENT in response.json()["detail"]
    assert _user_and_events("false-consent@alethra.io")[0] is None


def test_the_refusal_does_not_reveal_whether_an_email_is_registered(client):
    assert _register(client, "known-email@alethra.io", risk_acknowledged=True).status_code == 201

    known = _register(client, "known-email@alethra.io")
    unknown = _register(client, "unknown-email@alethra.io")

    assert known.status_code == 400
    assert unknown.status_code == 400
    assert known.json()["detail"] == unknown.json()["detail"]


def test_an_acknowledged_registration_records_one_audit_event(client):
    response = _register(client, "consent-ok@alethra.io", risk_acknowledged=True)

    assert response.status_code == 201
    user, events = _user_and_events("consent-ok@alethra.io")
    assert user is not None
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "risk_disclosure_acknowledged"
    assert event.actor_type == "human"
    assert str(event.actor_id) == str(user.id)
    assert event.payload_json == {
        "disclosure_path": RISK_DISCLOSURE_PATH,
        "disclosure_as_of_date": RISK_DISCLOSURE_AS_OF,
        "acknowledged": True,
    }


def test_the_response_carries_no_credential_material(client):
    response = _register(client, "response-shape@alethra.io", risk_acknowledged=True)

    assert response.status_code == 201
    body = response.json()
    assert "password_hash" not in body
    assert "access_token" not in body
    assert set(body) == {
        "id",
        "email",
        "display_name",
        "user_type",
        "created_at",
        "is_active",
        "is_admin",
    }
