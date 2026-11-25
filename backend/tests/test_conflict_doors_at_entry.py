"""Each decision door is proven at its own entry, not through the shared classifier.

``tests/test_conflict_recognition.py`` measures what an ``IntegrityError`` says
and which branch ``expected_unique_violation`` picks. That is not the same claim
as 「this door cannot rename a foreign failure」: a classifier can be right while
a door ignores it. This file drives the four write doors over HTTP and reads the
tables back from a session the request never touched.

The four doors, in the order their code runs:

``A``  ``app/api/simulations.py:2243`` inside ``_submit_manual_order`` — fires
     while the pending order is being written.
``B``  ``app/api/simulations.py:2665`` at the end of ``manual_decision`` — fires
     when the decision, the ledger event and the audit rows are committed.
``C``  ``app/api/human_decisions.py:214`` in ``POST /human-decisions``.
``D``  ``app/api/human_decisions.py:343`` in ``POST /human-decisions/{id}/correct``.

A and B are the same HTTP entry (``POST /simulations/{id}/manual-decision``) and
both expect ``uq_paper_order_portfolio_idempotency``, so which one a given
failure can even *reach* is decided by the write order, not by the caller: the
order row is flushed inside ``submit_pending``, which sits inside A's ``try``,
so a collision on the order index surfaces at A first. That is a measured fact
about these doors and it is recorded below rather than worked around.

Driving B from its own entry found a defect, and this file keeps the node that
found it: B's guard began at the ``db.flush()`` *after* ``emit_event``, but
``emit_event`` flushes (``app/services/__init__.py:64``), so the decision, the
ledger event and the audit row had already reached the database before the
``except IntegrityError`` existed. A collision on any of them escaped as an
unhandled 500, which made B's documented 409 branch a claim about a line the
request rarely reached. The guard now starts before the audit write.

What is real and what is staged, per node:

* Every ``IntegrityError`` asserted here is raised by the database itself, from
  the real index or the real ``NOT NULL``, on this lane's SQLite. No node
  constructs an exception object; the classifier, the branch, the rollback and
  the response body are all the product's code.
* ``D``'s own-index collision is **not staged at all**: the colliding row is
  committed before the request runs, so the door meets a duplicate the way a
  slow second click would. Its key is generated from ``utcnow()``, so that
  function is pinned for one request — the clock is the staged part, the
  database's refusal is not.
* ``A1``／``B1``／``B2``／``C1``／``D2`` stage **only the interleaving**: another
  door's row is made pending inside the request's own session, which
  ``autoflush=False`` (``tests/conftest.py:264``) keeps invisible to the
  read-checks. They stand in for two overlapping transactions, which SQLite's
  single pooled connection cannot run; the genuine two-transaction version is a
  named PostgreSQL node in this round's A-card, still unexecuted.
* ``A2`` stages nothing either: it points the version at an instrument list
  holding ``None``, so the order this door writes violates ``NOT NULL`` on its
  own.
* The lane's engine is a ``StaticPool`` (``tests/conftest.py:207``): all
  sessions here share one DBAPI connection. "Independent readback" therefore
  means an independent ``Session``／transaction／identity map, not a second
  connection, and the nodes say so by also asserting that a row the fixture
  *committed* survives the door's rollback — which is what proves the cleanup was
  scoped to the request's transaction instead of a broader reset.

The six ``POST /paper-portfolio/{id}/decisions`` regressions in
``tests/test_decision_conflict_repro.py`` are a different entry with a different
idempotency contract. They are left as they are and are not evidence for any of
the four doors above.
"""

import uuid
from datetime import datetime

import pytest
from starlette.testclient import TestClient

import app.api.human_decisions as human_decisions_api
import app.api.simulations as simulations_api
from app.main import app
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment, ExperimentStatus, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderStatus,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.simulation import SimulationDecision, SimulationSession
from app.models.user import User
from tests.conftest import TestSession

MANUAL = "/api/v1/simulations/{session_id}/manual-decision"
CREATE = "/api/v1/human-decisions"
ASSET = "DEMO-ALPHA"
ORDER_DOOR_TEXT = (
    "This idempotency_key was already written by another request; a second "
    "order is not submitted. Refresh the order list to see the existing one."
)
CONCURRENT = "Idempotency key conflict (concurrent)"
COMMITTED_DECOY_KEY = "committed-before-the-race"

# Nothing in a customer-facing body may quote the database back at them.
LEAK_MARKERS = (
    "UNIQUE constraint",
    "NOT NULL constraint",
    "FOREIGN KEY",
    "INSERT INTO",
    "paper_orders",
    "human_decision_events",
    "decision_records",
    "audit_events",
    "sqlalchemy",
)


def _ledger(db) -> dict:
    """Count the five tables these doors can reach, from a foreign session.

    ``expire_all`` first, so the numbers come from the database and not from an
    identity map this test never wrote through.
    """
    db.expire_all()
    return {
        "paper_orders": db.query(PaperOrder).count(),
        "decision_records": db.query(DecisionRecord).count(),
        "simulation_decisions": db.query(SimulationDecision).count(),
        "human_decision_events": db.query(HumanDecisionEvent).count(),
        "audit_events": db.query(AuditEvent).count(),
    }


def _session_audit_rows(db, owner_id, session_id, *, event_type="human_decision_created"):
    """The audit rows one node's own decision session produced, and nobody else's.

    ``audit_events`` has no owner or session column (``app/models/audit_event.py``),
    so a node's range is named by three axes together: the ``actor_id`` the door
    writes, the ``entity_type`` the door writes, and the session id the door puts
    in ``payload_json`` (``app/api/human_decisions.py:206``). Only the session
    leg is node-owned — the test user is shared by every node in the run — so it
    is matched exactly rather than assumed.

    The session leg is resolved in Python because the only SQL-side spelling of a
    JSON key is dialect-specific (``->>`` on PostgreSQL, ``json_extract`` on
    SQLite) and this file has to answer the same way on both lanes. The count is
    still scoped: the SQL filter narrows to this event type and actor first.

    Counting the table instead was measured red on the isolated PostgreSQL run:
    the rows it counted came from other nodes' committed presses, not this one.
    """
    rows = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.event_type == event_type,
            AuditEvent.entity_type == "human_decision",
            AuditEvent.actor_id == owner_id,
        )
        .all()
    )
    named = str(session_id)
    return [
        row
        for row in rows
        if isinstance(row.payload_json, dict)
        and row.payload_json.get("simulation_session_id") == named
    ]


def _strict(client) -> TestClient:
    """The caller's view of a crash: Starlette re-raises unless told not to."""
    return TestClient(app, raise_server_exceptions=False)


def _assert_body_names_nothing(client, response):
    body = response.text
    for marker in LEAK_MARKERS:
        assert marker.lower() not in body.lower(), (
            f"{response.status_code} body quotes {marker!r}: {body[:200]}"
        )


def _armed_once(monkeypatch, module, name, transform):
    """Run ``transform`` on the first call only, then stand down.

    Single-use is the point: a standing seam would also corrupt the follow-up
    lawful request each node makes, and that would measure the fixture.
    """
    real = getattr(module, name)
    armed = [True]

    def patched(*args, **kwargs):
        result = real(*args, **kwargs)
        if armed[0]:
            armed[0] = False
            transform(*args, **kwargs)
        return result

    monkeypatch.setattr(module, name, patched)


def _pending_order(db, *, portfolio_id, idempotency_key, asset_id=ASSET):
    """A row for another door's lane, pending and unflushed, in this session."""
    db.add(
        PaperOrder(
            portfolio_id=portfolio_id,
            asset_id=asset_id,
            side=OrderSide.BUY,
            quantity=1.0,
            status=OrderStatus.pending,
            idempotency_key=idempotency_key,
            actor_type="human",
        )
    )


def _pending_event(db, *, owner_id, session_id, idempotency_key, version_id):
    db.add(
        HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=session_id,
            idempotency_key=idempotency_key,
            action=HumanDecisionAction.hold,
            quantity=None,
            rationale="another request holds this key",
            server_strategy_version_id=version_id,
            server_instrument_id=ASSET,
            server_step_number=1,
            server_snapshot_date="2024-01-05",
            server_data_cutoff_at="2024-01-31",
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
        )
    )


@pytest.fixture
def desk(client, auth_headers):
    """One owner, one replay cursor, one bound account and one committed order.

    The committed order is the decoy: it has to survive every rollback below,
    and it is what makes another door's index reachable at the doors that never
    write an order of their own. ``provider_confirmed`` is not decoration —
    ``simulations.py:2413`` answers 400 before either order door exists, so an
    unconfirmed session would prove nothing about the doors at all.
    """
    db = TestSession()
    try:
        owner = db.query(User).filter(User.email == "test@alethra.io").one()
        experiment = Experiment(
            owner_id=owner.id,
            title="Door-level conflict experiment",
            hypothesis="A door answers for one constraint, not for any.",
            research_type=ResearchType.quantitative,
            status=ExperimentStatus.draft,
        )
        db.add(experiment)
        db.flush()
        version = ExperimentVersion(
            experiment_id=experiment.id,
            version_number=1,
            created_by=owner.id,
            strategy_spec={
                "asset_ids": [ASSET],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        )
        db.add(version)
        db.flush()
        session = SimulationSession(
            experiment_version_id=version.id,
            owner_id=owner.id,
            initial_virtual_cash=100000.0,
            data_cutoff_at="2024-01-31",
            current_snapshot_date="2024-01-05",
            observed_days=1,
            provider_id="demo",
            provider_confirmed=True,
            paper_equity=100000.0,
        )
        db.add(session)
        db.flush()
        portfolio = PaperPortfolio(
            owner_id=owner.id,
            simulation_session_id=session.id,
            initial_virtual_cash=100000.0,
            current_virtual_cash=100000.0,
            status=PortfolioStatus.active,
        )
        db.add(portfolio)
        db.flush()
        db.add(
            PaperOrder(
                portfolio_id=portfolio.id,
                asset_id=ASSET,
                side=OrderSide.SELL,
                quantity=1.0,
                status=OrderStatus.pending,
                idempotency_key=COMMITTED_DECOY_KEY,
                actor_type="human",
            )
        )
        db.commit()
        yield {
            "client": client,
            "headers": auth_headers,
            "db": db,
            "owner_id": owner.id,
            "version_id": version.id,
            "session_id": session.id,
            "portfolio_id": portfolio.id,
        }
    finally:
        db.close()


def _manual(desk, idempotency_key, **over):
    body = {"action": "buy", "quantity": 1, "idempotency_key": idempotency_key}
    body.update(over)
    return desk["client"].post(
        MANUAL.format(session_id=desk["session_id"]),
        headers=desk["headers"],
        json=body,
    )


def _manual_strict(desk, idempotency_key, **over):
    body = {"action": "buy", "quantity": 1, "idempotency_key": idempotency_key}
    body.update(over)
    return _strict(desk["client"]).post(
        MANUAL.format(session_id=desk["session_id"]),
        headers=desk["headers"],
        json=body,
    )


# --------------------------------------------------------------------------
# door A — the order write inside ``_submit_manual_order``
# --------------------------------------------------------------------------

def test_door_A_answers_only_its_own_index_with_a_conflict(desk, monkeypatch):
    """A competing order row → 409 in A's own words, and nothing written.

    The seam is ``_observed_bar_close``, a read-only price estimate that runs
    after ``find_existing_by_key`` and before the order is written: staging there
    keeps the door's own read honest, which is what makes the flush reach the
    database at all.
    """
    key = "order-race-1"
    _armed_once(
        monkeypatch,
        simulations_api,
        "_observed_bar_close",
        lambda db, **kw: _pending_order(
            db, portfolio_id=desk["portfolio_id"], idempotency_key=key
        ),
    )
    before = _ledger(desk["db"])

    response = _manual(desk, key)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == ORDER_DOOR_TEXT, response.text
    _assert_body_names_nothing(desk["client"], response)
    after = _ledger(desk["db"])
    assert after == before, f"a refused order still wrote rows: {before} → {after}"
    assert COMMITTED_DECOY_KEY in {
        row.idempotency_key
        for row in desk["db"].query(PaperOrder).filter(
            PaperOrder.portfolio_id == desk["portfolio_id"]
        )
    }, "the door's rollback took committed rows with it"

    recovered = _manual(desk, "order-after-race")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["order"]["idempotency_key"] == "order-after-race"
    written = _ledger(desk["db"])
    assert written["paper_orders"] == before["paper_orders"] + 1
    assert written["audit_events"] > before["audit_events"]


def test_door_A_does_not_rename_its_own_not_null_failure(desk, monkeypatch):
    """The order this door writes violates ``NOT NULL`` → 500, never 409.

    Nothing is injected: the version's instrument list holds ``None``, so the
    asset the order is written for is ``NULL`` and SQLite refuses the insert.
    The caller pressed once, and a 409 would tell them they pressed twice.
    """
    db = desk["db"]
    broken_version = ExperimentVersion(
        experiment_id=db.get(ExperimentVersion, desk["version_id"]).experiment_id,
        version_number=99,
        created_by=desk["owner_id"],
        strategy_spec={
            "asset_ids": [None],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
        },
    )
    db.add(broken_version)
    db.flush()
    broken_session = SimulationSession(
        experiment_version_id=broken_version.id,
        owner_id=desk["owner_id"],
        initial_virtual_cash=100000.0,
        data_cutoff_at="2024-01-31",
        current_snapshot_date="2024-01-05",
        observed_days=1,
        provider_id="demo",
        provider_confirmed=True,
        paper_equity=100000.0,
    )
    db.add(broken_session)
    db.flush()
    db.add(
        PaperPortfolio(
            owner_id=desk["owner_id"],
            simulation_session_id=broken_session.id,
            initial_virtual_cash=100000.0,
            current_virtual_cash=100000.0,
            status=PortfolioStatus.active,
        )
    )
    db.commit()
    before = _ledger(db)

    response = _strict(desk["client"]).post(
        MANUAL.format(session_id=broken_session.id),
        headers=desk["headers"],
        json={"action": "buy", "quantity": 1, "idempotency_key": "null-asset-1"},
    )

    assert response.status_code == 500, (
        "a NOT NULL failure on the door's own insert was answered as "
        f"{response.status_code}: {response.text[:200]}"
    )
    assert response.json()["code"] == "internal_error", response.text
    assert response.headers.get("X-Request-ID"), "no reference to quote when reporting"
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(db) == before, "the refused write left rows behind"


# --------------------------------------------------------------------------
# door B — the guarded tail of ``manual_decision``
# --------------------------------------------------------------------------

def test_door_B_answers_a_conflict_on_the_order_index_it_owns(desk, monkeypatch):
    """An order row that appears after A's flush collides at B, and B says 409.

    B's conflict sentence is identical to A's on purpose: both doors answer for
    the same index, and a caller cannot tell which one replied. What this node
    pins is that B *can* answer it, and where: the real index refuses the row at
    the first flush inside the guard, which is ``emit_event``'s — the tail
    statement that drains the decision, the ledger event and the order. Before
    this node existed that flush sat outside the ``except``, and the same
    collision left as a 500. Rolling back there also removes the order A had
    already written, or the account would hold an order nobody submitted.
    """
    key = "outer-race-1"
    _armed_once(
        monkeypatch,
        simulations_api,
        "_derive_server_context",
        lambda session, db: _pending_order(
            db, portfolio_id=desk["portfolio_id"], idempotency_key=key
        ),
    )
    before = _ledger(desk["db"])

    response = _manual(desk, key)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == ORDER_DOOR_TEXT, response.text
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(desk["db"]) == before, (
        "B rolled back its own rows but left the order A had flushed"
    )

    recovered = _manual(desk, "outer-after-race")
    assert recovered.status_code == 200, recovered.text
    assert _ledger(desk["db"])["paper_orders"] == before["paper_orders"] + 1


def test_door_B_lets_another_doors_index_through_as_a_500(desk, monkeypatch):
    """A collision on the human-decision index is not this door's duplicate.

    The competing row is pending in the request's own session, so the event
    read-check at ``simulations.py:2387`` cannot see it and the door writes its
    own event under the same key; the database is what refuses. The answer has to
    be an error the platform investigates, not a conflict the caller caused.

    This node only discriminates because the refusal now arrives *inside* the
    guard: while the guard started after ``emit_event``'s flush, both this
    failure and a genuine duplicate answered 500, and the node measured the
    fixture rather than the branch.
    """
    key = "cross-door-1"
    _armed_once(
        monkeypatch,
        simulations_api,
        "_derive_server_context",
        lambda session, db: _pending_event(
            db,
            owner_id=desk["owner_id"],
            session_id=desk["session_id"],
            idempotency_key=key,
            version_id=desk["version_id"],
        ),
    )
    before = _ledger(desk["db"])

    response = _manual_strict(desk, key)

    assert response.status_code == 500, response.text
    assert response.json()["code"] == "internal_error", response.text
    assert "conflict" not in response.text.lower(), response.text
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(desk["db"]) == before, (
        "the order and the replay decision were committed while the ledger "
        "event failed — the books now disagree with the order list"
    )

    recovered = _manual(desk, "cross-door-after")
    assert recovered.status_code == 200, recovered.text


# --------------------------------------------------------------------------
# door C — POST /human-decisions (its own index and NOT NULL are covered in
# tests/test_conflict_recognition.py; only the foreign index was missing)
# --------------------------------------------------------------------------

def test_door_C_does_not_answer_another_doors_order_index_as_its_own(desk, monkeypatch):
    """A paper-order collision inside the human-decision door stays unclaimed.

    This door never writes an order, so the colliding row is staged as pending;
    the committed decoy is what the real index refuses to duplicate. The claim
    under test is narrow: whichever ``IntegrityError`` arrives, this door may
    only answer a conflict for ``uq_human_decision_owner_session_key``.

    The audit read is scoped to this node's session rows rather than counted
    across the table, and the node's own lawful press below is the control that
    shows the scoped filter can see a row when one exists — without it the
    ``== 0`` would be satisfied by a filter that never matches anything.
    """
    _armed_once(
        monkeypatch,
        human_decisions_api,
        "_derive_server_context",
        lambda session, db: _pending_order(
            db,
            portfolio_id=desk["portfolio_id"],
            idempotency_key=COMMITTED_DECOY_KEY,
        ),
    )
    before = _ledger(desk["db"])

    response = _strict(desk["client"]).post(
        CREATE,
        headers=desk["headers"],
        json={
            "simulation_session_id": str(desk["session_id"]),
            "idempotency_key": "human-foreign-index",
            "action": "hold",
        },
    )

    assert response.status_code == 500, response.text
    assert response.json()["code"] == "internal_error", response.text
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(desk["db"]) == before
    assert _session_audit_rows(
        desk["db"], desk["owner_id"], desk["session_id"]
    ) == [], "the event row went away but its audit row stayed"

    recovered = desk["client"].post(
        CREATE,
        headers=desk["headers"],
        json={
            "simulation_session_id": str(desk["session_id"]),
            "idempotency_key": "human-after-foreign",
            "action": "hold",
        },
    )
    assert recovered.status_code == 201, recovered.text
    settled = _session_audit_rows(desk["db"], desk["owner_id"], desk["session_id"])
    assert len(settled) == 1, (
        "the scoped audit read cannot see this node's own committed press, so "
        f"its zero above proves nothing: {len(settled)} rows"
    )
    assert settled[0].entity_id == recovered.json()["id"], (
        "the audit row this node can see names a different event than the one "
        "the door returned"
    )


# --------------------------------------------------------------------------
# door D — POST /human-decisions/{id}/correct (no entry-level test existed)
# --------------------------------------------------------------------------

def _original(desk, key="to-correct-1"):
    response = desk["client"].post(
        CREATE,
        headers=desk["headers"],
        json={
            "simulation_session_id": str(desk["session_id"]),
            "idempotency_key": key,
            "action": "hold",
            "rationale": "the decision the owner is about to correct",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_door_D_answers_its_own_index_with_a_conflict(desk, monkeypatch):
    """A correction whose generated key already exists → 409, nothing written.

    The key is ``correction-{original}-{utcnow()}``, so pinning one clock call
    is enough to make a committed row collide with the one this request is
    about to write. Unlike the other nodes the duplicate is then genuinely
    already in the database before the request starts: no interleaving, no
    pending row, and the door's read-side has no key check to be blinded. The
    pin lives only around the refused request — a standing clock would make the
    recovery leg generate the same key again and collide with the decoy, which
    would measure the fixture instead of the door standing down.
    """
    pinned = datetime(2026, 1, 1, 0, 0, 0)
    original = _original(desk)
    colliding_key = f"correction-{original['idempotency_key']}-{pinned.isoformat()}"

    db = desk["db"]
    db.add(
        HumanDecisionEvent(
            owner_id=desk["owner_id"],
            simulation_session_id=desk["session_id"],
            idempotency_key=colliding_key,
            action=HumanDecisionAction.hold,
            quantity=None,
            rationale="someone else corrected this decision first",
            server_strategy_version_id=uuid.UUID(
                original["server_strategy_version_id"]
            ),
            server_instrument_id=ASSET,
            server_step_number=1,
            server_snapshot_date="2024-01-05",
            server_data_cutoff_at="2024-01-31",
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
        )
    )
    db.commit()
    before = _ledger(db)

    with monkeypatch.context() as pinned_clock:
        pinned_clock.setattr(human_decisions_api, "utcnow", lambda: pinned)
        response = desk["client"].post(
            f"{CREATE}/{original['id']}/correct",
            headers=desk["headers"],
            json={
                "action": "hold",
                "rationale": "corrected by this request",
                "correction_reason": "the wording was wrong",
            },
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == CONCURRENT, response.text
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(db) == before, "a refused correction still wrote rows"
    assert (
        db.query(AuditEvent)
        .filter(AuditEvent.event_type == "human_decision_corrected")
        .count()
        == 0
    )
    stored = db.get(HumanDecisionEvent, uuid.UUID(original["id"]))
    db.refresh(stored)
    assert stored.rationale == "the decision the owner is about to correct"
    assert stored.supersedes_event_id is None

    corrected = desk["client"].post(
        f"{CREATE}/{original['id']}/correct",
        headers=desk["headers"],
        json={
            "action": "hold",
            "rationale": "corrected after the clash",
            "correction_reason": "the wording was wrong",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["supersedes_event_id"] == original["id"]


def test_door_D_does_not_rename_the_order_index(desk, monkeypatch):
    """Another door's unique index fails inside D → the owner is not blamed.

    This is the door the round found unguarded first: its own comment says a
    correction "almost cannot collide" on its key, so before the narrow mapping
    every integrity failure here was reported as a clash between the owner's two
    clicks. The staged part is one pending order row; the refusal is the index's.
    """
    original = _original(desk, key="to-correct-2")
    _armed_once(
        monkeypatch,
        human_decisions_api,
        "_get_session_with_ownership",
        lambda session_id, db, current_user: _pending_order(
            db,
            portfolio_id=desk["portfolio_id"],
            idempotency_key=COMMITTED_DECOY_KEY,
        ),
    )
    before = _ledger(desk["db"])

    response = _strict(desk["client"]).post(
        f"{CREATE}/{original['id']}/correct",
        headers=desk["headers"],
        json={
            "action": "hold",
            "rationale": "corrected while a foreign row failed",
            "correction_reason": "shape of the answer matters more than the cause",
        },
    )

    assert response.status_code == 500, response.text
    assert response.json()["code"] == "internal_error", response.text
    assert "conflict" not in response.text.lower(), response.text
    _assert_body_names_nothing(desk["client"], response)
    assert _ledger(desk["db"]) == before, "the failed correction left rows behind"

    recovered = desk["client"].post(
        f"{CREATE}/{original['id']}/correct",
        headers=desk["headers"],
        json={"action": "hold", "correction_reason": "after the failure"},
    )
    assert recovered.status_code == 201, recovered.text


# --------------------------------------------------------------------------
# why the index needs concurrency, measured rather than assumed
# --------------------------------------------------------------------------

def test_the_entries_refuse_an_empty_key_so_the_index_cannot_be_reached_sequentially(
    desk,
):
    """Every sequential duplicate is answered by a read, not by the index.

    ``find_existing_by_key`` skips its lookup when the key is falsy, and the
    order index is partial (``WHERE idempotency_key IS NOT NULL``), so an empty
    key would have been the one way to reach the index with a plain second
    click. Both entries refuse it at the schema (``min_length=1``), which is why
    the nodes above stage the interleaving instead of sending a retry. Recorded
    here so nobody later "proves" the index path with an empty key that the API
    does not accept.
    """
    for payload in (
        {
            "simulation_session_id": str(desk["session_id"]),
            "idempotency_key": "",
            "action": "hold",
        },
    ):
        refused = desk["client"].post(CREATE, headers=desk["headers"], json=payload)
        assert refused.status_code == 422, refused.text

    refused_manual = _manual(desk, "")
    assert refused_manual.status_code == 422, refused_manual.text
    assert _ledger(desk["db"]) == _ledger(desk["db"])
