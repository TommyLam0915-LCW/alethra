"""PostgreSQL concurrency tests for quota operations (P0-B).

Verifies that quota reservation, settlement and release are atomic under
concurrent access, using two independent sessions against real PostgreSQL.

What this module is allowed to touch, and why it is written this way:

* The target is a required input. Unset means the module is not run; set but
  broken means it FAILS. An earlier version turned a connection error into a
  skip, which let a run against an unreachable or wrong database look green.
* The target is checked by the project's own safety gate
  (`test_safety_gate.verify_test_database`): TEST_MODE, database whitelist and
  blacklist, host/port, then the database name, role permissions and server
  identity the server actually answers with. Nothing here re-implements that.
* The schema is created by the official migrations, never by this module. There
  is no `create_all`, and no `drop_all`: the previous teardown dropped every
  table in the database to clean up four rows of its own.
* Cleanup deletes only the rows this module created, by their user id, and lets
  an error surface instead of swallowing it.
* Concurrency tests join their threads, prove each one stopped, and require
  every thread to report an outcome. An "at most one succeeded" assertion would
  also have passed with zero successes.
"""
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models.user import User
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.pilot import PilotEntitlement
from app.auth.entitlements import reserve_quota, settle_reservation, release_reservation
from app.lib.timeutil import utcnow

#: The required target. No default: a fallback DSN turned "environment not set"
#: into an unattended run against whatever host happened to be listening.
POSTGRES_URL = os.environ.get("ALETHRA_TEST_POSTGRES_URL")
if not POSTGRES_URL:
    pytest.skip(
        "ALETHRA_TEST_POSTGRES_URL is not set; this module writes to PostgreSQL "
        "and will not guess a host",
        allow_module_level=True,
    )

#: This module is excluded from the official PostgreSQL run list until its
#: target guard has been exercised against the dedicated test database.
#: `run_tests.sh --mode pg` exports ALETHRA_TEST_POSTGRES_URL for every module,
#: so this opt-in is the only thing that keeps it out of that pass.
OPT_IN = "ALETHRA_PG_QUOTA_CONCURRENCY_ENABLE"
if os.environ.get(OPT_IN) != "1":
    pytest.skip(
        f"excluded from the official PG run list; set {OPT_IN}=1 to run these "
        "tests knowingly against a dedicated, migration-built database",
        allow_module_level=True,
    )

#: Tables these tests read and write. Their absence is a missing migration, not
#: something this module may fix by creating tables.
REQUIRED_TABLES = ("users", "pilot_entitlements", "quota_counters", "quota_reservations")

#: Loopback addresses PostgreSQL may answer for a localhost/127.0.0.1 URL.
ANSWERABLE_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


def _target_diagnostic(url: str) -> str:
    """Host, port and database only — never the userinfo."""
    parsed = make_url(url)
    return f"{parsed.host}:{parsed.port or 5432}/{parsed.database}"


def _require_answered_server(info: dict, url: str) -> None:
    """The server that answered must be the loopback instance the URL named.

    The gate checks the URL's host/port against the allow list and reports the
    server's own `inet_server_addr()`/`inet_server_port()`, but does not compare
    the two. A connection that answers over a unix socket reports NULL, which
    cannot be matched to a declared host, so it is refused rather than assumed.
    """
    expected_port = make_url(url).port or 5432
    addr, port = info["server_addr"], info["server_port"]
    if addr is None or port is None:
        raise RuntimeError(
            "target guard could not identify the server: "
            f"inet_server_addr()={addr!r} inet_server_port()={port!r} for target "
            f"{_target_diagnostic(url)}. These tests write, so they need a TCP "
            "connection to a named instance (host-based URLs, not a unix socket)."
        )
    if str(addr) not in ANSWERABLE_ADDRESSES:
        raise RuntimeError(
            f"target guard rejected the server: answered as {addr}:{port}, "
            f"expected a loopback instance for target {_target_diagnostic(url)}"
        )
    if int(port) != int(expected_port):
        raise RuntimeError(
            f"target guard rejected the server: answered on port {port}, "
            f"the target names {expected_port} ({_target_diagnostic(url)})"
        )


def _require_migration_built_schema(engine) -> None:
    """Prove the schema exists. Read-only: this module never creates or drops."""
    missing = []
    with engine.connect() as conn:
        for table in REQUIRED_TABLES:
            found = conn.execute(
                text("SELECT to_regclass(:qualified)"),
                {"qualified": f"public.{table}"},
            ).scalar()
            if found is None:
                missing.append(table)
    if missing:
        raise RuntimeError(
            "schema is not built: missing tables "
            f"{sorted(missing)} in {_target_diagnostic(POSTGRES_URL)}. "
            "This module does not create tables; run the official migrations "
            "(backend/alembic.ini, `alembic upgrade head`) against this target first."
        )


def _open_gated_engine():
    """Build the engine only after the official gate has accepted the target."""
    from test_safety_gate import verify_test_database

    info = verify_test_database(POSTGRES_URL)
    _require_answered_server(info, POSTGRES_URL)
    engine = create_engine(POSTGRES_URL, pool_size=10, max_overflow=20, pool_pre_ping=True)
    try:
        _require_migration_built_schema(engine)
    except Exception:
        engine.dispose()
        raise
    return engine, info


def _gate_or_fail():
    """Accept the target through the official gate, or fail — never skip.

    A target was supplied, so an unusable target is a failure: skipping here
    would let a wrong host, an unpermitted role or a missing migration be
    recorded as a clean run.
    """
    try:
        return _open_gated_engine()
    except Exception as exc:
        pytest.fail(
            f"ALETHRA_TEST_POSTGRES_URL is set to {_target_diagnostic(POSTGRES_URL)} "
            f"but the target guard refused it ({type(exc).__name__}: {exc}). "
            "Not skipping: a supplied target that cannot be verified is a failure.",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def pg_engine():
    """A PostgreSQL engine for this module's own rows, target-gated."""
    engine, info = _gate_or_fail()
    print(f"quota concurrency target accepted: {info['database']} as {info['user']} "
          f"on {info['server_addr']}:{info['server_port']}")
    yield engine
    engine.dispose()


def _create_round_user(Session, *, ai_usage_limit: int):
    """Insert exactly the two rows this module needs and return the user id."""
    user_id = uuid4()
    session = Session()
    try:
        session.add(User(
            id=user_id,
            email=f"concurrent-{uuid4()}@example.com",
            password_hash="hashed",
            created_at=utcnow(),
        ))
        session.flush()  # the entitlement has a FK to this row
        session.add(PilotEntitlement(
            user_id=user_id,
            ai_usage_limit=ai_usage_limit,
            pilot_access=True,
        ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return user_id


def _delete_round_rows(Session, user_id) -> None:
    """Delete this round's rows and prove they went. Errors are not swallowed."""
    session = Session()
    try:
        reservations = session.query(QuotaReservation).filter(
            QuotaReservation.user_id == user_id
        ).delete()
        counters = session.query(QuotaCounter).filter(
            QuotaCounter.user_id == user_id
        ).delete()
        entitlements = session.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user_id
        ).delete()
        users = session.query(User).filter(User.id == user_id).delete()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # The delete reports what it removed; a surviving row would be inherited by
    # the next module, so silence here is not acceptable.
    survivors = {
        "reservations": reservations,
        "counters": counters,
        "entitlements": entitlements,
        "users": users,
    }
    session = Session()
    try:
        remaining = {
            "quota_reservations": session.query(QuotaReservation).filter(
                QuotaReservation.user_id == user_id
            ).count(),
            "quota_counters": session.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id
            ).count(),
            "pilot_entitlements": session.query(PilotEntitlement).filter(
                PilotEntitlement.user_id == user_id
            ).count(),
            "users": session.query(User).filter(User.id == user_id).count(),
        }
    finally:
        session.close()
    left = {name: count for name, count in remaining.items() if count}
    assert not left, (
        f"cleanup deleted {survivors} but these rows still exist for user "
        f"{user_id}: {left}"
    )


@pytest.fixture
def test_user_with_limit(pg_engine):
    """A user with ai_usage_limit=2, created and removed by this module only."""
    Session = sessionmaker(bind=pg_engine)
    user_id = _create_round_user(Session, ai_usage_limit=2)
    yield user_id
    _delete_round_rows(Session, user_id)


def _start_and_join(threads, timeout: float = 30.0) -> None:
    """Start every thread, join every thread, and prove none is still running.

    `join(timeout)` alone returns without saying whether the thread finished, so
    a hung worker would let the assertions below read a partial result set.
    """
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    still_running = [thread.name for thread in threads if thread.is_alive()]
    assert not still_running, (
        f"threads still running after {timeout}s: {still_running}. "
        "Refusing to judge results a stopped worker never wrote."
    )


class _Outcomes:
    """Per-thread results: what happened, plus anything unexpected."""

    def __init__(self, names):
        self._lock = threading.Lock()
        self.results = {}
        self.errors = []
        self._expected = set(names)

    def record(self, name, outcome) -> None:
        with self._lock:
            self.results[name] = outcome

    def error(self, name, exc) -> None:
        with self._lock:
            self.errors.append(f"{name}: {exc}")

    def require_complete(self) -> dict:
        if self.errors:
            raise AssertionError(f"unexpected exceptions: {self.errors}")
        assert set(self.results) == self._expected, (
            f"every thread must report an outcome: got {self.results} "
            f"for {sorted(self._expected)}"
        )
        return self.results

    def count(self, outcome) -> int:
        return sum(1 for value in self.results.values() if value == outcome)


def _threaded(names_and_fns, timeout: float = 30.0) -> _Outcomes:
    outcomes = _Outcomes([name for name, _ in names_and_fns])
    barrier = threading.Barrier(len(names_and_fns))

    def run(name, fn):
        try:
            barrier.wait(timeout=timeout)
            outcomes.record(name, fn())
        except Exception as exc:  # noqa: BLE001 - classified by require_complete
            outcomes.error(name, exc)

    threads = [
        threading.Thread(target=run, name=f"quota-{name}", args=(name, fn))
        for name, fn in names_and_fns
    ]
    _start_and_join(threads, timeout=timeout)
    outcomes.require_complete()
    return outcomes


def _classify(denied_marker: str, fn):
    """Run fn, mapping the expected refusal to 'denied'.

    Anything else raises, so an unrelated failure can never be counted as the
    quota guard working.
    """
    try:
        fn()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - matched against the marker below
        if denied_marker in str(exc):
            return "denied"
        raise


def test_concurrent_reservations_with_limit(pg_engine, test_user_with_limit):
    """Two reservations of 2 against limit=2: exactly one may succeed."""
    Session = sessionmaker(bind=pg_engine)

    def reserve():
        session = Session()
        try:
            reserve_quota(session, test_user_with_limit, "ai_usage", amount=2)
            session.commit()
        finally:
            session.close()

    outcomes = _threaded([
        ("a", lambda: _classify("quota_exceeded", reserve)),
        ("b", lambda: _classify("quota_exceeded", reserve)),
    ])

    assert outcomes.count("ok") == 1, (
        f"exactly one reservation should succeed, got {outcomes.results}"
    )
    assert outcomes.count("denied") == 1, (
        f"the other must be refused by the quota check, got {outcomes.results}"
    )

    session = Session()
    try:
        reserved = session.query(QuotaReservation).filter(
            QuotaReservation.user_id == test_user_with_limit,
            QuotaReservation.status == "reserved",
        ).all()
        total = sum(row.amount for row in reserved)
    finally:
        session.close()
    assert total == 2, f"the winning reservation should hold exactly 2 units, got {total}"


def test_concurrent_settlement_same_reservation(pg_engine, test_user_with_limit):
    """Two threads settling one reservation: exactly one may commit the settle."""
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    try:
        reservation = reserve_quota(session, test_user_with_limit, "ai_usage", amount=1)
        session.commit()
        reservation_id = reservation.id
    finally:
        session.close()

    def settle():
        inner = Session()
        try:
            settle_reservation(inner, reservation_id)
            inner.commit()
        finally:
            inner.close()

    outcomes = _threaded([
        ("a", lambda: _classify("not in 'reserved' status", settle)),
        ("b", lambda: _classify("not in 'reserved' status", settle)),
    ])

    assert outcomes.count("ok") == 1, f"exactly 1 settlement should succeed: {outcomes.results}"
    assert outcomes.count("denied") == 1, f"exactly 1 must be refused: {outcomes.results}"

    session = Session()
    try:
        counter = session.query(QuotaCounter).filter(
            QuotaCounter.user_id == test_user_with_limit,
            QuotaCounter.quota_type == "ai_usage",
        ).first()
        used = None if counter is None else counter.used
    finally:
        session.close()
    assert used == 1, f"counter should be 1 (not double-settled), got {used}"


def test_concurrent_settle_and_release(pg_engine, test_user_with_limit):
    """Settle and release racing on one reservation: one wins, state stays consistent."""
    Session = sessionmaker(bind=pg_engine)
    session = Session()
    try:
        reservation = reserve_quota(session, test_user_with_limit, "ai_usage", amount=1)
        session.commit()
        reservation_id = reservation.id
    finally:
        session.close()

    def run(op):
        inner = Session()
        try:
            op(inner, reservation_id)
            inner.commit()
        finally:
            inner.close()

    outcomes = _threaded([
        ("settle", lambda: _classify("not in 'reserved' status", lambda: run(settle_reservation))),
        ("release", lambda: _classify("not in 'reserved' status", lambda: run(release_reservation))),
    ])

    assert outcomes.count("ok") == 1, (
        f"exactly one of settle/release should succeed: {outcomes.results}"
    )
    assert outcomes.count("denied") == 1, f"the other must be refused: {outcomes.results}"

    session = Session()
    try:
        row = session.query(QuotaReservation).filter(
            QuotaReservation.id == reservation_id
        ).first()
        status = row.status
        counter = session.query(QuotaCounter).filter(
            QuotaCounter.user_id == test_user_with_limit,
            QuotaCounter.quota_type == "ai_usage",
        ).first()
        used = None if counter is None else counter.used
    finally:
        session.close()

    assert status in ("settled", "released"), f"reservation left in {status!r}"
    assert used == (1 if status == "settled" else 0), (
        f"status {status!r} should pair with used={1 if status == 'settled' else 0}, got {used}"
    )


def test_reservation_counter_creation_race(pg_engine):
    """Two threads create the counter row concurrently: no lost update, no duplicate."""
    Session = sessionmaker(bind=pg_engine)
    user_id = _create_round_user(Session, ai_usage_limit=10)
    try:
        def reserve_and_settle():
            inner = Session()
            try:
                reservation = reserve_quota(inner, user_id, "ai_usage", amount=1)
                settle_reservation(inner, reservation.id)
                inner.commit()
            finally:
                inner.close()

        outcomes = _threaded([("a", reserve_and_settle), ("b", reserve_and_settle)])
        assert outcomes.count("ok") == 2, (
            f"both reservations should succeed, got {outcomes.results}"
        )

        session = Session()
        try:
            counter = session.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == "ai_usage",
            ).first()
            used = None if counter is None else counter.used
        finally:
            session.close()
        assert used == 2, f"counter should be 2, got {used}"
    finally:
        _delete_round_rows(Session, user_id)
