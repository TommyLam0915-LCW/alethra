"""POST /paper-portfolio/{id}/decisions: which constraint conflict is real, measured.

The claim under test was that a duplicate submission reaches an unhandled 500.
Measured on the sqlite lane (run dir ``sqlite-20261120T092011Z-38924``), the
answer splits in two:

* A repeated identical legal body is **not** a conflict. The table's only unique
  constraint is ``uq_decision_records_source_per_portfolio`` on
  ``(paper_portfolio_id, source_trade_id, decision_type)``
  (``app/models/paper_portfolio.py:240``), and this door never fills
  ``source_trade_id`` — NULLs never collide, so both calls were accepted as two
  decisions (``201``/``201``). That half of the claim is withdrawn here.
* One column in the same body **is** a reachable 500 on PostgreSQL:
  ``simulation_session_id`` is a real foreign key
  (``migrations/versions/55a8d07a7f40_initial_schema.py:583``) and the route
  passed it straight to the insert. An id that does not exist died inside the
  database — measured as ``500 internal_error`` once the key was enforced, which
  is PostgreSQL's permanent state, and as a stored row naming nothing while the
  lane has ``PRAGMA foreign_keys=OFF`` (``tests/conftest.py:216``).

The repair is the refusal the account-opening door has always given the same
column (``require_session_for_owner``), applied before any write, so neither the
500 nor the dangling row can be produced. It is not a conflict handler: an id
that names no session of this owner was never a duplicate of anything.
"""

import uuid

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment, ExperimentStatus, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.paper_portfolio import DecisionRecord, PaperPortfolio, PortfolioStatus
from app.models.simulation import SimulationSession
from tests.conftest import TEST_ENGINE, TestSession

BASE = "/api/v1/paper-portfolio"
REFUSAL = "Simulation session not found for this user"


def _register(client, email):
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "conflict-pass-123",
            "display_name": email.split("@")[0],
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "conflict-pass-123"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _unit(db, owner_id):
    portfolio = PaperPortfolio(
        owner_id=owner_id,
        currency="USD",
        initial_virtual_cash=100000.0,
        current_virtual_cash=100000.0,
        status=PortfolioStatus.active,
    )
    db.add(portfolio)
    db.flush()
    return portfolio


def _replay_session(db, owner_id):
    experiment = Experiment(
        owner_id=owner_id,
        title="Conflict probe experiment",
        hypothesis="A session row is a session row.",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.draft,
    )
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={"probe": True},
    )
    db.add(version)
    db.flush()
    session = SimulationSession(
        experiment_version_id=version.id,
        owner_id=owner_id,
        initial_virtual_cash=100000.0,
        data_cutoff_at="2024-12-01",
    )
    db.add(session)
    db.flush()
    return session


def _body(**extra):
    return {"decision_type": "no_action", "actor_type": "human", **extra}


def _counts(db):
    db.expire_all()
    return db.query(DecisionRecord).count(), db.query(AuditEvent).count()


@pytest.fixture
def desk(client):
    """Two owners, one skill account each with its own replay session."""
    from app.models.user import User

    headers = _register(client, "desk-owner-a@test.com")
    other_headers = _register(client, "desk-owner-b@test.com")
    db = TestSession()
    try:
        owner_a = db.query(User).filter(User.email == "desk-owner-a@test.com").one()
        owner_b = db.query(User).filter(User.email == "desk-owner-b@test.com").one()
        unit = _unit(db, owner_a.id)
        own_session = _replay_session(db, owner_a.id)
        foreign_session = _replay_session(db, owner_b.id)
        db.commit()
        yield {
            "client": client,
            "db": db,
            "headers": headers,
            "unit": unit,
            "own_session": own_session,
            "foreign_session": foreign_session,
        }
    finally:
        db.close()


@pytest.fixture
def enforced_foreign_keys():
    """Make the lane's SQLite check keys the way PostgreSQL always does.

    The lane turns the pragma off so teardown can drop tables in any order
    (``tests/conftest.py:216``); a foreign-key refusal therefore has to be
    measured with the key switched on, and the switch is process-global on this
    pooled connection, so it is restored even when the request raises.
    """
    with TEST_ENGINE.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    try:
        yield
    finally:
        with TEST_ENGINE.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")


def _strict_post(env, **kwargs):
    """The strict client keeps a server error a status code instead of raising it.

    Starlette re-raises an unhandled exception out of ``TestClient`` by default,
    which would turn the very 500 under investigation into a test error and hide
    what the caller actually receives.
    """
    strict = TestClient(app, raise_server_exceptions=False)
    return strict.post(f"{BASE}/{env['unit'].id}/decisions", **kwargs)


def test_repeated_identical_body_records_two_decisions(desk):
    """No unique constraint covers a decision this door can write twice.

    ``source_trade_id`` stays NULL here, so the portfolio-scoped unique index is
    silent and a double click is two rows. That is the narrowed claim: the
    duplicate is a business question (should the same judgment count twice?),
    not a database conflict, and nothing in this file makes it a 500.
    """
    env, client = desk, desk["client"]
    before = _counts(env["db"])

    first = client.post(
        f"{BASE}/{env['unit'].id}/decisions", headers=env["headers"], json=_body()
    )
    second = client.post(
        f"{BASE}/{env['unit'].id}/decisions", headers=env["headers"], json=_body()
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    after = _counts(env["db"])
    assert after[0] == before[0] + 2, after
    listed = client.get(
        f"{BASE}/{env['unit'].id}/decisions", headers=env["headers"]
    ).json()
    assert {row["id"] for row in listed} == {first.json()["id"], second.json()["id"]}
    assert len(listed) == 2


def test_missing_session_refused_with_zero_writes(desk):
    """The dangling id the lane used to store is refused before the insert."""
    env, client = desk, desk["client"]
    before = _counts(env["db"])

    response = client.post(
        f"{BASE}/{env['unit'].id}/decisions",
        headers=env["headers"],
        json=_body(simulation_session_id=str(uuid.uuid4())),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == REFUSAL, response.text
    assert _counts(env["db"]) == before, "the refusal still wrote a row"


def test_missing_session_never_reaches_the_database_key(desk, enforced_foreign_keys):
    """With the foreign key live — PostgreSQL's normal state — it is still a 404.

    Before the door existed, this same request died inside the insert and the
    caller got ``500 internal_error`` with a request id and no cause they could
    act on. The refusal has to come from the door, not from the pragma: flipping
    the key off again must not re-open the 500, which is why the node above
    asserts the identical answer with the key off.
    """
    env = desk
    before = _counts(env["db"])

    response = _strict_post(
        env, headers=env["headers"], json=_body(simulation_session_id=str(uuid.uuid4()))
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == REFUSAL, response.text
    assert _counts(env["db"]) == before, "the refusal still wrote a row"


def test_another_owners_session_refused_with_zero_writes(desk):
    """A session that exists but is not the caller's is the same refusal.

    This one never raised an integrity error — the foreign key was satisfied —
    it just stamped someone else's replay cursor onto this owner's decision and
    read back as a journal row naming a session the owner cannot open (403).
    """
    env, client = desk, desk["client"]
    before = _counts(env["db"])

    response = client.post(
        f"{BASE}/{env['unit'].id}/decisions",
        headers=env["headers"],
        json=_body(simulation_session_id=str(env["foreign_session"].id)),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == REFUSAL, response.text
    assert _counts(env["db"]) == before, "the refusal still wrote a row"


def test_owners_own_session_still_records_and_reads_back(desk, enforced_foreign_keys):
    """Positive control: the door refuses other people's ids, not the column.

    Without this node every refusal above would also be satisfied by a check
    that rejects the field outright.
    """
    env, client = desk, desk["client"]
    before = _counts(env["db"])

    response = _strict_post(
        env,
        headers=env["headers"],
        json=_body(simulation_session_id=str(env["own_session"].id)),
    )

    assert response.status_code == 201, response.text
    recorded = response.json()
    assert recorded["simulation_session_id"] == str(env["own_session"].id)
    assert _counts(env["db"]) == (before[0] + 1, before[1] + 1)
    listed = client.get(
        f"{BASE}/{env['unit'].id}/decisions", headers=env["headers"]
    ).json()
    assert [row["id"] for row in listed] == [recorded["id"]]
    assert listed[0]["simulation_session_id"] == str(env["own_session"].id)


def test_account_opening_door_refuses_the_same_ids(desk):
    """One rule, two doors: ``POST /paper-portfolio`` answers identically.

    The decisions door now calls the helper this door already used, so the
    refusal text is pinned for both from a single source.
    """
    env, client = desk, desk["client"]

    for session_id in (str(uuid.uuid4()), str(env["foreign_session"].id)):
        response = client.post(
            "/api/v1/paper-portfolio",
            headers=env["headers"],
            json={
                "initial_virtual_cash": 10000.0,
                "currency": "USD",
                "simulation_session_id": session_id,
            },
        )
        assert response.status_code == 404, (session_id, response.text)
        assert response.json()["detail"] == REFUSAL, response.text
