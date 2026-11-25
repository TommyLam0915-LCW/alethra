"""An account must not exist without an explicit risk-disclosure acknowledgement.

Registration is the only path that creates a customer identity, so it is the
only place the disclosure can be attached. These tests pin the refusal, the
absence of any side effect when refused, the durable acknowledgement record, and
that the refusal cannot be used to probe which emails are registered.

The record's actor label used to be asserted as ``human`` while the endpoint
wrote ``unknown``, and the two documents said the same opposite things:
``docs/api-contract-notes.md`` §7 claimed ``actor_type="human"``, while
``docs/personalization-contract.md`` §11 states that the label comes only from
the credential the server verified -- password session ``human``, API key
``agent``, anything else ``unknown``, never upgraded by inference. Registration
carries no credential at all: there is no token yet, because the account the
token would name is created by the same request. So the endpoint and §11 were
right, and the test and §7 were the inconsistent source; they are what changed.

The acknowledgement is still an unverified self-report, which is what the label
says. Making it ``human`` is not a labelling fix: it would require the person to
confirm through a password-authenticated session, and no such re-confirmation
exists in the product today. Whether one should be added -- re-emit the
acknowledgement on first password login, so the same fact is recorded once as
self-reported and once as credential-verified -- is a product decision, not an
engineering default, and it is listed for the owner rather than assumed here.
"""

import ast
import inspect
from types import SimpleNamespace

from jose import jwt

from app.api import auth as auth_api
from app.api.auth import RISK_DISCLOSURE_AS_OF, RISK_DISCLOSURE_PATH
from app.auth.provenance import (
    HUMAN,
    PASSWORD_AUTH_METHOD,
    UNKNOWN,
    VALID_ACTOR_TYPES,
    request_actor_provenance,
)
from app.config import get_settings
from app.models.audit_event import AuditEvent
from app.models.user import User
from tests.conftest import TestSession

CONSENT_DETAIL_FRAGMENT = "risk disclosure at /legal"

# The password the fixtures register with. One spelling, so the login below
# proves the account this request created is the one the credential opened.
_PASSWORD = "testpass123"


def _payload(email: str, **overrides):
    payload = {
        "email": email,
        "password": _PASSWORD,
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
    # Not "human": nothing authenticated this request, because the account an
    # authentic token would name is created by it. The name on the row is the
    # user the request supplied -- self-reported -- and the channel label says
    # so. See the module docstring for the contract that fixed which side lied.
    assert event.actor_type == UNKNOWN
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


def test_the_request_body_cannot_choose_the_acknowledgement_actor_type(client):
    """§11: a self-report labels itself as self-reported, whatever it claims.

    The fields below are the ones a client would send if the label were
    negotiable. It is not: the route reads the credential and pydantic drops
    the rest, so an acknowledged registration stays ``unknown``.
    """
    email = "self-labelled@alethra.io"
    response = client.post(
        "/api/auth/register",
        json=_payload(
            email,
            risk_acknowledged=True,
            actor_type=HUMAN,
            provenance=HUMAN,
            amr=[PASSWORD_AUTH_METHOD],
        ),
    )

    assert response.status_code == 201
    events = _user_and_events(email)[1]
    assert [event.actor_type for event in events] == [UNKNOWN]


def test_a_password_session_is_the_channel_that_would_record_human(client):
    """Where ``human`` does come from, read off the token this login minted.

    ``get_current_user`` copies the verified token's ``amr`` claim onto the
    request and ``request_actor_provenance`` reads that claim, so the label is
    a property of the credential. The claim below is decoded from the access
    token the password login returned for the account the registration just
    created, not written by the test -- which is the difference between this
    channel and the one that had none. That the label reaches a stored row
    through a real route is pinned in ``tests/test_decision_provenance.py``.
    """
    email = "provenance-channel@alethra.io"
    assert _register(client, email, risk_acknowledged=True).status_code == 201

    login = client.post(
        "/api/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert login.status_code == 200
    settings = get_settings()
    claims = jwt.decode(
        login.json()["access_token"],
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert claims.get("amr") == [PASSWORD_AUTH_METHOD]

    password_session = SimpleNamespace(state=SimpleNamespace(auth_amr=claims["amr"]))
    registration_request = SimpleNamespace(state=SimpleNamespace())
    assert request_actor_provenance(password_session) == HUMAN
    assert request_actor_provenance(registration_request) == UNKNOWN


def test_register_labels_the_event_from_the_credential_not_from_a_literal():
    """The write site asks the credential; it carries no answer of its own.

    Two documents disagreed about one word here, and the tempting repair was to
    put that word into the endpoint. This node is what stops that repair
    landing quietly: no actor label may appear as a literal in ``register()``,
    and the provenance helper has to be what supplies the value.
    """
    source = inspect.getsource(auth_api.register)
    tree = ast.parse(source)
    hardcoded = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in VALID_ACTOR_TYPES
    }
    assert not hardcoded, (
        "actor label written as a literal in register(), which promotes or "
        f"demotes every registration regardless of the credential: {sorted(hardcoded)}"
    )
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "request_actor_provenance" in called, (
        "register() no longer asks the credential who acted, so whatever label "
        "it writes is an assumption rather than a measurement"
    )
