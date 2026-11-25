"""Only the constraint a door was written for may be answered as a duplicate.

The four decision-write doors (``POST /simulations/{id}/decisions``, its
order-submit sibling, ``POST /human-decisions`` and ``/correct``) used to turn
*any* ``IntegrityError`` into a 409 about an idempotency key. This file measures
what each error shape actually says on this lane and which answer the code gives,
so the claim 「只將可辨識的預期約束映射成 409」 rests on the database's own words
rather than on a reading of the regex.

What is authentic and what is forced, node by node:

* Groups ``uq``／``not-null``／``foreign key`` build real rows and let SQLite
  raise, so the ``IntegrityError`` objects, their ``.orig`` messages and the
  branch the classifier takes are all the product's own. Nothing is mocked.
* The PostgreSQL cases hand the classifier an error object carrying
  ``diag.constraint_name``／``diag.sqlstate`` — those are **synthetic
  diagnostics**, because this lane has no PostgreSQL to produce them. They prove
  the reading order (constraint name, then sqlstate confirmation, then the
  column-list form); they do not claim PostgreSQL was measured. The PG cell
  stays ``NOT RUN`` (§11.6 A-card).
* ``test_the_create_door_answers_a_real_collision_with_409`` forces only the
  *interleaving*: a competing row is made pending inside the request's own
  session so the read-check cannot see it and ``db.flush()`` hits the unique
  index. That is a stand-in for two overlapping transactions, which SQLite's
  single pooled connection cannot run for real; the genuine two-process race is
  the A-card's PG node, still unexecuted. The error, the rolled-back write and
  the 409 body are the code's own, and the response text distinguishes the two
  conflict branches — ``Idempotency key conflict (concurrent)`` can only come
  from the database collision.
* ``test_the_create_door_does_not_rename_a_null_as_a_duplicate`` forces one
  server-derived field to ``None``; the ``NOT NULL`` violation and the re-raise
  are the product's, and the caller sees ``500 internal_error`` with a request
  id instead of a conflict the customer never caused.

Door-level coverage of the ``paper_orders`` index is the existing selection in
``tests/test_c1_c4_complete.py`` (which matches this detail text); the nodes here
add the recognition layer and the human-decision door, and change nothing about
those assertions.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

import app.api.human_decisions as human_decisions_api
from app.api.conflicts import (
    HUMAN_DECISION_IDEMPOTENCY,
    PAPER_ORDER_IDEMPOTENCY,
    expected_unique_violation,
)
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
    OrderType,
    PaperDecisionActorType,
    PaperDecisionType,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.simulation import SimulationSession
from app.models.user import User
from tests.conftest import TEST_ENGINE, TestSession

CREATE = "/api/v1/human-decisions"
CONCURRENT = "Idempotency key conflict (concurrent)"


# --------------------------------------------------------------------------
# rows: complete on purpose, so the only thing wrong is the one under test
# --------------------------------------------------------------------------

def _decision(**over):
    fields = dict(
        owner_id=uuid.uuid4(),
        simulation_session_id=uuid.uuid4(),
        idempotency_key="key-1",
        action=HumanDecisionAction.hold,
        quantity=None,
        rationale="measured row",
        server_strategy_version_id=uuid.uuid4(),
        server_instrument_id="DEMO-ALPHA",
        server_step_number=1,
        server_snapshot_date="2024-01-02",
        server_data_cutoff_at="2024-01-31",
        execution_status=HumanDecisionExecutionStatus.record_only,
        actor_type="human",
    )
    fields.update(over)
    return HumanDecisionEvent(**fields)


def _order(**over):
    fields = dict(
        portfolio_id=uuid.uuid4(),
        asset_id="DEMO-ALPHA",
        side=OrderSide.BUY,
        quantity=10.0,
        order_type=OrderType.market,
        status=OrderStatus.pending,
        idempotency_key="key-1",
        actor_type="human",
    )
    fields.update(over)
    return PaperOrder(**fields)


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def enforced_foreign_keys():
    """Make SQLite check its keys the way PostgreSQL always does.

    The lane switches the pragma off so teardown can drop tables in any order
    (``tests/conftest.py:216``), and the pooled connection makes the switch
    process-wide, so it is restored even when the insert raises.
    """
    with TEST_ENGINE.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    try:
        yield
    finally:
        with TEST_ENGINE.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")


def _collision(db, *rows):
    """Insert ``rows`` and hand back the IntegrityError the database raised.

    The wording travels with every assertion below, so a SQLite that starts
    naming a key differently fails by showing what it said.
    """
    for row in rows:
        db.add(row)
    try:
        with pytest.raises(IntegrityError) as caught:
            db.flush()
        return caught.value
    finally:
        db.rollback()


def test_the_idempotency_keys_name_the_indexes_the_doors_expect(db):
    """Two real duplicates on two real tables, each read back as itself.

    The control inside the node is the pair of orders sharing a portfolio with
    ``idempotency_key`` left NULL: the index is partial, so that is two orders
    and not a conflict, and the collision below is caused by the key alone.
    """
    portfolio_id = uuid.uuid4()
    db.add(_order(portfolio_id=portfolio_id, idempotency_key=None))
    db.add(_order(portfolio_id=portfolio_id, idempotency_key=None))
    db.flush()

    order_clash = _collision(
        db,
        _order(portfolio_id=portfolio_id, idempotency_key="same-key"),
        _order(portfolio_id=portfolio_id, idempotency_key="same-key"),
    )
    assert expected_unique_violation(order_clash) == PAPER_ORDER_IDEMPOTENCY, (
        f"measured wording: {order_clash.orig}"
    )

    shared = _decision()
    decision_clash = _collision(
        db,
        _decision(
            owner_id=shared.owner_id,
            simulation_session_id=shared.simulation_session_id,
            idempotency_key=shared.idempotency_key,
        ),
        _decision(
            owner_id=shared.owner_id,
            simulation_session_id=shared.simulation_session_id,
            idempotency_key=shared.idempotency_key,
            rationale="a different decision",
        ),
    )
    assert expected_unique_violation(decision_clash) == HUMAN_DECISION_IDEMPOTENCY, (
        f"measured wording: {decision_clash.orig}"
    )


def test_a_primary_key_collision_is_not_an_idempotency_duplicate(db):
    """The first row answers in the same sentence shape, and is not a clash.

    ``UNIQUE constraint failed: paper_orders.id`` is a platform bug (or a
    re-run of a generated id), never evidence that the caller pressed twice.
    """
    first = _order()
    db.add(first)
    db.flush()
    clash = _collision(db, _order(id=first.id))
    assert expected_unique_violation(clash) is None, f"measured wording: {clash.orig}"


def test_another_doors_unique_index_is_not_this_one(db):
    """A genuine duplicate on ``uq_decision_records_source_per_portfolio``.

    This is the case a blanket mapping cannot tell apart: the database really
    did refuse a repeated key, but it was the portfolio's source-trade index on
    a different table, and neither decision door is allowed to call it the
    caller's own retry.
    """
    portfolio_id, source_trade_id = uuid.uuid4(), uuid.uuid4()
    fields = dict(
        paper_portfolio_id=portfolio_id,
        source_trade_id=source_trade_id,
        decision_type=PaperDecisionType.entry,
        actor_type=PaperDecisionActorType.human,
    )
    clash = _collision(db, DecisionRecord(**fields), DecisionRecord(**fields))
    assert expected_unique_violation(clash) is None, f"measured wording: {clash.orig}"
    assert "uq_decision_records_source_per_portfolio" not in str(clash.orig)


def test_a_missing_required_value_is_not_a_duplicate(db):
    """``NOT NULL`` is the platform's own contract failing, in its own words."""
    clash = _collision(db, _decision(server_strategy_version_id=None))
    assert expected_unique_violation(clash) is None, f"measured wording: {clash.orig}"


def test_a_broken_foreign_key_is_not_a_duplicate(db, enforced_foreign_keys):
    """With the keys enforced — PostgreSQL's normal state — still not a clash.

    An owner id naming no account is the shape the previous round refused at the
    paper-portfolio door; here it says that the classifier does not hand such an
    error to a caller as a duplicate either.
    """
    clash = _collision(db, _decision(owner_id=uuid.uuid4()))
    assert expected_unique_violation(clash) is None, f"measured wording: {clash.orig}"
    assert "FOREIGN KEY" in str(clash.orig).upper(), f"measured wording: {clash.orig}"


# --------------------------------------------------------------------------
# PostgreSQL spellings: synthetic diagnostics, read the way psycopg2 reports
# --------------------------------------------------------------------------

class _Diag:
    def __init__(self, constraint_name=None, sqlstate=None):
        self.constraint_name = constraint_name
        self.sqlstate = sqlstate


class _PgError(Exception):
    """Stands in for a psycopg2 error: a message plus ``diag``. No PG was run."""

    def __init__(self, message, constraint_name, sqlstate):
        super().__init__(message)
        self.diag = _Diag(constraint_name=constraint_name, sqlstate=sqlstate)


_NOT_NULL_MSG = (
    'null value in column "idempotency_key" of relation '
    '"human_decision_events" violates not-null constraint'
)
_FK_MSG = (
    'insert or update on table "human_decision_events" violates foreign key '
    'constraint "human_decision_events_owner_id_fkey"'
)

# slug, message, diag.constraint_name, diag.sqlstate, expected
PG_CASES = [
    (
        "named_unique_index",
        "duplicate key value violates unique constraint "
        f'"{PAPER_ORDER_IDEMPOTENCY}"',
        PAPER_ORDER_IDEMPOTENCY,
        "23505",
        PAPER_ORDER_IDEMPOTENCY,
    ),
    (
        "named_only_in_the_message",
        "duplicate key value violates unique constraint "
        f'"{HUMAN_DECISION_IDEMPOTENCY}"',
        None,
        "23505",
        HUMAN_DECISION_IDEMPOTENCY,
    ),
    (
        "another_doors_unique_index",
        "duplicate key value violates unique constraint "
        '"uq_decision_records_source_per_portfolio"',
        "uq_decision_records_source_per_portfolio",
        "23505",
        None,
    ),
    (
        "the_expected_name_but_not_a_unique_violation",
        _FK_MSG,
        HUMAN_DECISION_IDEMPOTENCY,
        "23503",
        None,
    ),
    (
        "not_null",
        _NOT_NULL_MSG,
        "human_decision_events_server_strategy_version_id_not_null",
        "23502",
        None,
    ),
    (
        "a_complaint_this_code_cannot_read",
        "server closed the connection unexpectedly",
        None,
        None,
        None,
    ),
]


@pytest.mark.parametrize("case", PG_CASES, ids=[c[0] for c in PG_CASES])
def test_the_postgresql_spellings_are_read_the_same_way(case):
    """Same question, other dialect: name first, and only a unique answer maps.

    ``the_expected_name_but_not_a_unique_violation`` is a guard rather than an
    observed pairing — a real foreign key names its own constraint — and it
    exists because the sqlstate is the fact, while a name is only a string.
    """
    slug, message, constraint_name, sqlstate, expected = case
    orig = _PgError(message, constraint_name=constraint_name, sqlstate=sqlstate)
    error = IntegrityError("INSERT", {}, orig)
    assert error.orig is orig, slug
    assert expected_unique_violation(error) == expected, f"{slug}: {message}"


# --------------------------------------------------------------------------
# the door itself, over HTTP
# --------------------------------------------------------------------------

@pytest.fixture
def desk(client, auth_headers):
    """The signed-in owner plus one replay session that door will accept."""
    holder = {"client": client, "headers": auth_headers}
    db = TestSession()
    try:
        owner = db.query(User).filter(User.email == "test@alethra.io").one()
        experiment = Experiment(
            owner_id=owner.id,
            title="Conflict recognition experiment",
            hypothesis="A duplicate is a duplicate of something specific.",
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
                "asset_ids": ["DEMO-ALPHA"],
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
        )
        db.add(session)
        db.commit()
        holder["db"] = db
        holder["owner_id"] = owner.id
        holder["session"] = session
        yield holder
    finally:
        db.close()


def _body(desk, idempotency_key, **over):
    return {
        "simulation_session_id": str(desk["session"].id),
        "idempotency_key": idempotency_key,
        "action": "hold",
        **over,
    }


def _written(desk):
    db = desk["db"]
    db.expire_all()
    return (
        db.query(HumanDecisionEvent).count(),
        db.query(AuditEvent).count(),
    )


def _armed_once(monkeypatch, transform):
    """Run ``transform`` against the first request only, then stand down.

    The seam has to be single-use: a standing alteration would also corrupt the
    follow-up request each node makes to prove the door still accepts a lawful
    decision, and that would measure the fixture instead of the door.
    """
    real = human_decisions_api._derive_server_context
    armed = [True]

    def patched(session, db):
        ctx = real(session, db)
        if armed[0]:
            armed[0] = False
            transform(session, db, ctx)
        return ctx

    monkeypatch.setattr(human_decisions_api, "_derive_server_context", patched)


def _competing_request(session, db, ctx):
    """Park a second decision with the same key, unwritten, in this session.

    ``autoflush=False`` (``tests/conftest.py:266``) is what keeps the read-check
    blind to it, which is the whole point: the door reaches the database and the
    database is what refuses.
    """
    db.add(
        HumanDecisionEvent(
            owner_id=session.owner_id,
            simulation_session_id=session.id,
            idempotency_key="race-key-1",
            action=HumanDecisionAction.hold,
            quantity=None,
            rationale="the other request got there first",
            server_strategy_version_id=ctx["strategy_version_id"],
            server_instrument_id=ctx["instrument_id"],
            server_step_number=ctx["step_number"],
            server_snapshot_date=ctx["snapshot_date"],
            server_data_cutoff_at=ctx["data_cutoff_at"],
            decision_instant=ctx["decision_instant"],
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
        )
    )


def _context_that_cannot_be_stored(session, db, ctx):
    ctx["strategy_version_id"] = None


def test_the_create_door_answers_a_real_collision_with_409(desk, monkeypatch):
    """Collision → the concurrent 409, nothing written, and the next click works.

    The detail text is the one the front end already matches
    (``OrderPreviewPanel.tsx``／the simulation page), so it stays a string; the
    ``read-check`` conflict branch answers with a different sentence, which is
    why this node can tell the two apart.
    """
    _armed_once(monkeypatch, _competing_request)
    before = _written(desk)

    response = desk["client"].post(
        CREATE, headers=desk["headers"], json=_body(desk, "race-key-1")
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == CONCURRENT, response.text
    assert _written(desk) == before, "a refused decision still wrote rows"

    after = desk["client"].post(
        CREATE, headers=desk["headers"], json=_body(desk, "later-key-1")
    )
    assert after.status_code == 201, after.text
    assert after.json()["idempotency_key"] == "later-key-1"
    listed = desk["client"].get(
        f"{CREATE}?simulation_session_id={desk['session'].id}",
        headers=desk["headers"],
    ).json()
    assert [row["idempotency_key"] for row in listed] == ["later-key-1"]


def test_the_create_door_does_not_rename_a_null_as_a_duplicate(desk, monkeypatch):
    """An unrecognised integrity failure keeps its own shape: 500, not 409.

    The forced part is one server-derived field set to ``None``; the
    ``NOT NULL`` violation, the re-raise past the conflict branch and the
    ``internal_error`` body with its request id are the product's. A customer
    who pressed once must not be told they pressed twice, and the platform must
    not lose the bug behind a conflict.
    """
    _armed_once(monkeypatch, _context_that_cannot_be_stored)
    before = _written(desk)
    # The strict client is the caller's view: Starlette re-raises an unhandled
    # exception out of TestClient by default, which would hide the 500 body.
    strict = TestClient(app, raise_server_exceptions=False)

    response = strict.post(CREATE, headers=desk["headers"], json=_body(desk, "null-key"))

    assert response.status_code == 500, response.text
    assert response.json()["code"] == "internal_error", response.text
    assert response.json()["code"] != "conflict", response.text
    assert response.headers.get("X-Request-ID"), "no reference to quote when reporting"
    assert _written(desk) == before, "the failed request still wrote rows"

    after = desk["client"].post(
        CREATE, headers=desk["headers"], json=_body(desk, "after-null-key")
    )
    assert after.status_code == 201, after.text
