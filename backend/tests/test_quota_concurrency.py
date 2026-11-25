"""PostgreSQL concurrency tests for quota operations (P0-B).

Verifies that quota reservation, settlement, and release are atomic
and safe under concurrent access using real PostgreSQL with multiple
connections/sessions.

Every node is PostgreSQL-only, and the file used to hide that by skipping at
import time -- ``pytest.skip(..., allow_module_level=True)`` behind a bare
``except Exception`` around a ``SELECT 1``. That made the four concurrency
nodes disappear from collection entirely, so they appeared in neither the
passed nor the skipped count, and it turned a real import error into the same
quiet skip as an unavailable server. The gate is a fixture now: the nodes
collect, report SKIPPED and name the command that runs them.

The connection comes from ``ALETHRA_PG_TEST_URL``, the one variable the pg
layer sets, rather than from a second name with a hard-coded
``test:test@localhost:5434`` default sitting in the source as the fallback.

The module also used to build and destroy its own schema: ``Base.metadata
.create_all(engine)`` at setup and ``Base.metadata.drop_all(engine)`` at
teardown, the latter inside ``except Exception: pass``. That statement named
every table in the application metadata rather than the four these nodes write
to, so against a database migrations had already filled it deleted tables other
modules read -- ``users`` alone is a foreign-key target for portfolios, runs,
decisions and api keys -- and a DROP that a dependent object rejected was
swallowed, leaving the target half-deleted under four passing nodes. On this
lane the schema belongs to migrations (``tests/conftest.py`` deliberately
issues no DDL for PostgreSQL, and the other PostgreSQL-only modules insert into
the migrated tables and clean by saved id), so these nodes now treat the schema
as a precondition they never modify, and their teardown deletes only rows whose
id it saved.

Four nodes here need no server. They read this file's own syntax tree, so a
future ``drop_all``, an unfiltered bulk delete, literal SQL that writes, or an
engine built before the target guard has answered fails in the offline lane
instead of waiting for an approved PostgreSQL run.
"""
import ast
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from app.models.user import User
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import reserve_quota, settle_reservation, release_reservation
from app.lib.timeutil import utcnow

from tests.postgresql_only import (
    postgresql_test_url,
    requires_postgresql,
    unmet_setup,
)

PREREQUISITE = (
    "holds two real sessions against one quota row at the same moment; the "
    "SQLite lane runs every session through one shared connection, so it has "
    "no second writer to race with"
)

#: The tables these nodes write. Read as a precondition, never created or
#: dropped from here: a test module that builds its own schema on a database
#: migrations own can only repair it by destroying it.
REQUIRED_TABLES = (
    "users",
    "pilot_entitlements",
    "quota_counters",
    "quota_reservations",
)

#: The only verb this module may send in literal SQL. Rows go in as ORM objects
#: and leave through deletes narrowed by a saved id, so every statement written
#: out is a count.
READ_ONLY_SQL_VERB = "SELECT"

_SOURCE = Path(__file__).read_text()


@pytest.fixture(autouse=True)
def _postgres_required(request):
    """Gate the nodes that hold a database, and only those.

    It used to skip unconditionally, which is a module-wide skip in a per-node
    costume: it also took away any node added here that needs no server, and
    the skip inventory could not tell the two kinds apart. A node that does not
    ask for ``pg_engine`` has nothing to wait for.
    """
    if "pg_engine" in request.fixturenames:
        requires_postgresql(PREREQUISITE)


def _table_names(engine):
    """The tables the target database has at this moment."""
    return set(inspect(engine).get_table_names())


@pytest.fixture(scope="module")
def pg_engine():
    """A target-guarded engine on the disposable database, schema left as found.

    Nothing in this fixture issues DDL. The guard connects first, because a
    database that merely carries the approved name is not the approved
    database; the table check after it is a read, and a missing table is
    reported as the unmet precondition it is rather than fixed by creating it,
    since a schema assembled by a test module is a schema the migration chain
    no longer describes.
    """
    from tests.test_target_guard import create_verified_session_factory

    db_url = postgresql_test_url(PREREQUISITE)
    _, engine = create_verified_session_factory(db_url)
    missing = sorted(set(REQUIRED_TABLES) - _table_names(engine))
    if missing:
        engine.dispose()
        unmet_setup(
            "the tables these nodes write ("
            + ", ".join(missing)
            + ") are created by migrations and this module runs no DDL, so the "
            "migration head has to be applied to the disposable database "
            "before they can run"
        )
    try:
        yield engine
    finally:
        engine.dispose()


def _delete_user_rows(session_factory, user_id):
    """Delete the rows one saved user id owns, children first.

    The id comes from the caller because it is the only thing that identifies
    this run's rows; a delete narrowed by anything else -- a status, a date, no
    condition at all -- reaches rows another node or another team owns.
    """
    session = session_factory()
    try:
        for model in (QuotaReservation, QuotaCounter, PilotEntitlement):
            session.query(model).filter(model.user_id == user_id).delete()
        session.query(User).filter(User.id == user_id).delete()
        session.commit()
    finally:
        session.close()


_RESIDUAL_READS = (
    (
        "quota_reservations",
        text("SELECT count(*) FROM quota_reservations WHERE user_id = :user_id"),
    ),
    (
        "quota_counters",
        text("SELECT count(*) FROM quota_counters WHERE user_id = :user_id"),
    ),
    (
        "pilot_entitlements",
        text("SELECT count(*) FROM pilot_entitlements WHERE user_id = :user_id"),
    ),
    ("users", text("SELECT count(*) FROM users WHERE id = :user_id")),
)


def _residual_counts(engine, user_id):
    """Rows still naming ``user_id``, read on a connection no session holds.

    A fresh connection is what makes this a statement about the database: the
    session that ran the deletes has already forgotten the rows it removed and
    would agree with almost anything. The id goes in as text, which compares
    against a native ``uuid`` column and against a 36-character one alike.
    """
    counts = {}
    with engine.connect() as connection:
        for name, statement in _RESIDUAL_READS:
            counts[name] = connection.execute(
                statement, {"user_id": str(user_id)}
            ).scalar_one()
    return counts


def _seed_user(pg_engine, ai_usage_limit, email_prefix):
    """Create one user and its entitlement, and remove them whatever happens.

    The id is made before the first INSERT and the cleanup sits in the
    teardown, so a node that fails at an assertion -- or a setup that fails
    between the two statements -- still leaves the database as it was found.
    The previous version of the fourth node cleaned up in its own last lines,
    which an earlier assertion skipped.
    """
    session_factory = sessionmaker(bind=pg_engine)
    user_id = uuid4()

    session = session_factory()
    try:
        session.add(
            User(
                id=user_id,
                email=f"{email_prefix}-{uuid4()}@example.com",
                password_hash="hashed",
                created_at=utcnow(),
            )
        )
        session.flush()  # the entitlement's FK needs the user row first
        session.add(
            PilotEntitlement(
                user_id=user_id,
                ai_usage_limit=ai_usage_limit,
                pilot_access=True,
            )
        )
        session.commit()
    finally:
        session.close()

    yield user_id

    _delete_user_rows(session_factory, user_id)
    residual = _residual_counts(pg_engine, user_id)
    assert not any(residual.values()), (
        f"rows created for {user_id} survived cleanup, so the target still "
        f"holds this run's data: {residual}"
    )


@pytest.fixture
def user_with_limit(pg_engine):
    """A user with limit=2, so two concurrent requests of 2 cannot both fit."""
    yield from _seed_user(pg_engine, ai_usage_limit=2, email_prefix="concurrent")


@pytest.fixture
def user_with_budget(pg_engine):
    """A user with room for both reservations.

    Here the only contention is the counter row, which is what the fourth node
    races: two writers, no counter yet.
    """
    yield from _seed_user(pg_engine, ai_usage_limit=10, email_prefix="race")


def _call_names(tree):
    """Every function or method name called anywhere in the tree."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def _call_lines(scope, name):
    """Lines inside ``scope`` on which something called ``name`` is called."""
    lines = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == name:
            lines.append(node.lineno)
    return lines


def _function_definition(name):
    """The module-level ``def name`` node, or None if it is not there."""
    for node in ast.parse(_SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _sql_literals(tree):
    """The statement text this module hands to SQLAlchemy's ``text()``.

    Only literal arguments count. A statement assembled by joining names would
    not be readable here, which is why ``_RESIDUAL_READS`` spells each count
    out in full -- a gate that cannot see a statement cannot restrict it either.
    """
    literals = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            literals.append(node.args[0].value)
    return literals


def _unfiltered_deletes(tree, source):
    """Lines with an ORM bulk delete that no saved id narrows.

    ``session.query(Model).delete()`` empties a table every module on the lane
    shares, which is the teardown this file gave up wearing a smaller hat. A
    ``filter()`` naming ``user_id`` is what tells the two apart.
    """
    offenders = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete"
        ):
            continue
        receiver = node.func.value
        if not (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Attribute)
            and receiver.func.attr == "filter"
        ):
            offenders.append(node.lineno)
        elif "user_id" not in (ast.get_source_segment(source, receiver) or ""):
            offenders.append(node.lineno)
    return offenders


def test_this_module_runs_no_schema_ddl():
    """Nothing here can delete a database it did not build.

    No server and no connection: the node reads this file. A whole-metadata
    ``create_all``/``drop_all`` pair is what a PostgreSQL-only module reaches
    for when the migrated schema is not there, and on a shared target the
    second half of that pair removes tables this file never wrote.
    """
    destructive = sorted(
        _call_names(ast.parse(_SOURCE)) & {"create_all", "drop_all", "exec_driver_sql"}
    )
    assert not destructive, (
        "this module runs against the schema migrations created; schema DDL "
        "here would rebuild or drop tables other modules own: "
        + ", ".join(destructive)
    )


def test_literal_sql_in_this_module_is_read_only():
    """The only statements written out as SQL are counts of one saved id.

    Writes go through mapped objects, so the node that checks their condition
    checks their scope too, and no literal statement can widen it.
    """
    literals = _sql_literals(ast.parse(_SOURCE))
    assert literals, (
        "no literal SQL found, so this node has stopped checking anything -- "
        "the residual reads that keep the cleanup honest are what it reads"
    )
    writing = [
        statement
        for statement in literals
        if not statement.strip().upper().startswith(READ_ONLY_SQL_VERB)
    ]
    assert not writing, f"literal SQL here may only read: {writing}"


def test_cleanup_deletes_only_rows_it_saved_the_id_of():
    """Every bulk delete in this file names the user id it created.

    The teardown may not skip and may not guess: it removes rows by the id the
    setup generated, and nothing else in the table.
    """
    offenders = _unfiltered_deletes(ast.parse(_SOURCE), _SOURCE)
    assert not offenders, (
        "bulk delete not narrowed by a saved user_id, at line(s): "
        + ", ".join(str(line) for line in offenders)
    )


def test_pg_engine_builds_its_engine_only_after_the_target_guard():
    """The guard answers "is this the disposable database" before anything else.

    Order is the whole of it. An engine created first and checked afterwards
    has already handed a connection -- and with it the chance of a write -- to
    a database the safety gate refuses, and the table check that follows the
    guard is a read on the same guarded engine.
    """
    fixture = _function_definition("pg_engine")
    assert fixture is not None, "the pg_engine fixture disappeared"
    guard_lines = _call_lines(fixture, "create_verified_session_factory")
    assert guard_lines, (
        "pg_engine no longer builds its engine through "
        "create_verified_session_factory, so nothing checks the target before "
        "these nodes write"
    )
    guard = min(guard_lines)
    for name in ("create_engine", "_table_names", "sessionmaker"):
        for line in _call_lines(fixture, name):
            assert line > guard, (
                f"{name} at line {line} reaches the server before the target "
                f"guard at line {guard}"
            )


def test_concurrent_reservations_with_limit(pg_engine, user_with_limit):
    """P0-B: Two concurrent reservations with limit=2, each requesting 2 units.

    At most one should succeed. This tests the atomicity of the check-and-reserve
    operation under concurrent access.
    """
    results = {"success": 0, "failed": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)  # Synchronize both threads to start at the same time

    def try_reserve(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()  # Synchronize start
            reservation = reserve_quota(session, user_with_limit, "ai_usage", amount=2)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["failed"] += 1
                if "quota_exceeded" not in str(e):
                    results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_reserve, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] <= 1, f"At most 1 reservation should succeed, got {results['success']}"
    assert results["failed"] >= 1, f"At least 1 should fail due to quota limit"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify final state: exactly 2 units reserved (if one succeeded)
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reserved = session.query(QuotaReservation).filter(
        QuotaReservation.user_id == user_with_limit,
        QuotaReservation.status == "reserved",
    ).all()
    total_reserved = sum(r.amount for r in reserved)
    assert total_reserved <= 2, f"Total reserved should be <= 2, got {total_reserved}"
    session.close()


def test_concurrent_settlement_same_reservation(pg_engine, user_with_limit):
    """P0-B: Two threads try to settle the same reservation concurrently.

    Only one should succeed. The other should fail because the reservation
    is no longer in 'reserved' status.
    """
    # Create a reservation first
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = reserve_quota(session, user_with_limit, "ai_usage", amount=1)
    session.commit()
    reservation_id = reservation.id
    session.close()

    results = {"success": 0, "failed": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_settle(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()  # Synchronize start
            settle_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["failed"] += 1
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_settle, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] == 1, f"Exactly 1 settlement should succeed, got {results['success']}"
    assert results["failed"] == 1, f"Exactly 1 should fail, got {results['failed']}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify counter: should be exactly 1, not 2
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_with_limit,
        QuotaCounter.quota_type == "ai_usage",
    ).first()
    assert counter is not None
    assert counter.used == 1, f"Counter should be 1 (not double-settled), got {counter.used}"
    session.close()


def test_concurrent_settle_and_release(pg_engine, user_with_limit):
    """P0-B: One thread settles, another releases the same reservation.

    Only one should succeed. Final state should be consistent.
    """
    # Create a reservation
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = reserve_quota(session, user_with_limit, "ai_usage", amount=1)
    session.commit()
    reservation_id = reservation.id
    session.close()

    results = {"settled": 0, "released": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_settle():
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            settle_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["settled"] += 1
        except Exception as e:
            with lock:
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Settle: {e}")
        finally:
            session.close()

    def try_release():
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            release_reservation(session, reservation_id)
            session.commit()
            with lock:
                results["released"] += 1
        except Exception as e:
            with lock:
                if "not in 'reserved' status" not in str(e):
                    results["errors"].append(f"Release: {e}")
        finally:
            session.close()

    t1 = threading.Thread(target=try_settle)
    t2 = threading.Thread(target=try_release)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    total = results["settled"] + results["released"]
    assert total == 1, f"Exactly one operation should succeed, got {total}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify final state
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    reservation = session.query(QuotaReservation).filter(
        QuotaReservation.id == reservation_id
    ).first()
    assert reservation.status in ("settled", "released")

    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_with_limit,
        QuotaCounter.quota_type == "ai_usage",
    ).first()

    if reservation.status == "settled":
        assert counter is not None and counter.used == 1
    else:  # released
        # Counter may not exist or used=0
        if counter is not None:
            assert counter.used == 0

    session.close()


def test_reservation_counter_creation_race(pg_engine, user_with_budget):
    """P0-B: Two concurrent reservations when no counter exists yet.

    Both should succeed in creating the counter atomically without
    duplicate key errors or lost updates.
    """
    results = {"success": 0, "errors": []}
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def try_reserve(thread_id):
        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            barrier.wait()
            reservation = reserve_quota(session, user_with_budget, "ai_usage", amount=1)
            settle_reservation(session, reservation.id)
            session.commit()
            with lock:
                results["success"] += 1
        except Exception as e:
            with lock:
                results["errors"].append(f"Thread {thread_id}: {e}")
        finally:
            session.close()

    threads = [threading.Thread(target=try_reserve, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results["success"] == 2, f"Both reservations should succeed, got {results['success']}"
    assert len(results["errors"]) == 0, f"Unexpected errors: {results['errors']}"

    # Verify counter: should be exactly 2
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    counter = session.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_with_budget,
        QuotaCounter.quota_type == "ai_usage",
    ).first()
    session.close()
    assert counter is not None
    assert counter.used == 2, f"Counter should be 2, got {counter.used}"
