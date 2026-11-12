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
* The schema is built by the official migrations, never by this module. There
  is no `create_all`, and no `drop_all`: the previous teardown dropped every
  table in the database to clean up four rows of its own. What this module
  checks here is that its required relations are present — the presence of a
  table is not evidence of which migration created it, and the revision is
  established by the provisioning record, not from inside a test.
* Target identity is confirmed twice: once through the gate's own preflight
  connection, and again on a connection taken from the engine the tests will
  actually use, because a second connection can land on a different backend.
* Cleanup deletes only the rows this module created, by their user id, and lets
  an error surface instead of swallowing it. It refuses to issue any DELETE
  while a worker this round started is still running: the surviving worker can
  still write after the rows are removed, so "deleted N rows" would describe
  state that is no longer the state left behind.
* Concurrency tests join their threads, prove each one stopped, and require
  every thread to report an outcome. An "at most one succeeded" assertion would
  also have passed with zero successes.
* A refusal is classified by the exception it actually is, never by a substring
  of its text: a lock timeout, deadlock or connection error is not a quota
  denial and is reported with its SQLSTATE.
"""
import os
import threading
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
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

#: Relations these tests read and write. Their absence stops the run; this
#: module does not create them. Their presence is checked, their provenance is
#: not — which migration built them belongs to the provisioning record.
REQUIRED_RELATIONS = ("users", "pilot_entitlements", "quota_counters", "quota_reservations")

#: `localhost` is one instance reachable over either stack, so a URL that names
#: it may legitimately be answered as 127.0.0.1 or ::1. A URL that names a
#: specific address gets no such latitude: the answer must match it exactly.
LOCALHOST_ALIASES = {"127.0.0.1", "::1"}

#: SQLSTATE classes that mean the database could not finish the work. None of
#: them is a quota refusal, and treating one as a denial would let a lock
#: timeout be counted as the guard doing its job.
NOT_A_DENIAL = {
    "40001": "serialization_failure",
    "40P01": "deadlock_detected",
    "55P03": "lock_not_available",
    "57014": "query_canceled (statement_timeout)",
    "57P01": "admin_shutdown",
    "57P02": "crash_shutdown",
    "57P03": "cannot_connect_now",
    "53200": "out_of_memory",
    "53300": "too_many_connections",
    "58000": "system_error",
}


def _target_diagnostic(url: str) -> str:
    """Host, port and database only — never the userinfo."""
    parsed = make_url(url)
    return f"{parsed.host}:{parsed.port or 5432}/{parsed.database}"


def _addresses_for(host: str) -> set:
    """The addresses a server may answer with for the host this target names."""
    if host == "localhost":
        return {"localhost"} | LOCALHOST_ALIASES
    return {host}


def _require_answered_server(info: dict, url: str) -> None:
    """The server that answered the preflight must be the instance the URL named.

    The gate checks the URL's host/port against its allow list and separately
    reports the server's own `inet_server_addr()`/`inet_server_port()`, but does
    not compare the two. A connection that answers over a unix socket reports
    NULL, which cannot be matched to a declared host, so it is refused rather
    than assumed. Being *some* loopback instance is not enough either: the
    answer has to be the host and port this target names.
    """
    declared = make_url(url)
    expected_port = declared.port or 5432
    addr, port = info["server_addr"], info["server_port"]
    if addr is None or port is None:
        raise RuntimeError(
            "target guard could not identify the server: "
            f"inet_server_addr()={addr!r} inet_server_port()={port!r} for target "
            f"{_target_diagnostic(url)}. These tests write, so they need a TCP "
            "connection to a named instance (host-based URLs, not a unix socket)."
        )
    if str(addr) not in _addresses_for(declared.host):
        raise RuntimeError(
            f"target guard rejected the server: answered as {addr}:{port}, "
            f"the target names host {declared.host!r} "
            f"(accepted answers: {sorted(_addresses_for(declared.host))})"
        )
    if int(port) != int(expected_port):
        raise RuntimeError(
            f"target guard rejected the server: answered on port {port}, "
            f"the target names {expected_port} ({_target_diagnostic(url)})"
        )


_IDENTITY_SQL = text(
    "SELECT current_database(), current_user, inet_server_addr(), inet_server_port()"
)


def _confirm_target_on_test_engine(engine, url: str, approved: dict) -> dict:
    """Read the identity again on a connection the tests will actually use.

    `verify_test_database` opens its own short-lived connection and closes it, so
    what it saw says nothing on its own about the pooled connections this engine
    hands out. The four identity values are compared against the concrete
    approved target — the host, port, database and login role the URL names — and
    against the gate's own report. "Any loopback instance will do" is not
    accepted in place of the target that was approved, and the allow list is not
    consulted a second time here: it is the gate's, unchanged.
    """
    declared = make_url(url)
    with engine.connect() as conn:
        database, role, addr, port = conn.execute(_IDENTITY_SQL).one()

    answers = {
        "database": None if database is None else str(database),
        "role": None if role is None else str(role).strip('"'),
        "server_addr": None if addr is None else str(addr),
        "server_port": None if port is None else int(port),
    }
    problems = []

    if declared.database and answers["database"] != declared.database:
        problems.append(
            f"current_database()={answers['database']!r} but the target names {declared.database!r}"
        )
    if declared.username and answers["role"] != declared.username:
        problems.append(
            f"current_user={answers['role']!r} but the target logs in as {declared.username!r}"
        )
    if answers["server_addr"] is None or answers["server_port"] is None:
        problems.append(
            f"inet_server_addr()={answers['server_addr']!r} "
            f"inet_server_port()={answers['server_port']!r}; a socket answer cannot be "
            "matched to the declared host"
        )
    else:
        if answers["server_addr"] not in _addresses_for(declared.host):
            problems.append(
                f"answered as {answers['server_addr']!r} but the target names host "
                f"{declared.host!r} (accepted: {sorted(_addresses_for(declared.host))})"
            )
        if answers["server_port"] != int(declared.port or 5432):
            problems.append(
                f"answered on port {answers['server_port']} but the target names "
                f"{declared.port or 5432}"
            )

    for key, label in (("database", "database"), ("user", "role")):
        seen = approved.get(key)
        if seen is not None and str(seen) != answers[label]:
            problems.append(
                f"the gate's preflight connection reported {label}={seen!r} while this "
                f"engine's own connection reports {answers[label]!r}"
            )

    if problems:
        raise RuntimeError(
            "test engine connection does not match the approved target "
            f"{_target_diagnostic(url)}: " + "; ".join(problems)
        )
    return answers


def _require_required_relations(engine) -> None:
    """Prove the required relations are present. Read-only: nothing is created.

    This is a presence check, not a provenance check. `to_regclass` says a
    relation exists; it cannot say which migration built it or at which
    revision, and this module does not read `alembic_version` — reading the
    revision would need privileges the test role is not granted, and widening
    them from inside a test is exactly what the provisioning record is for.
    """
    missing = []
    with engine.connect() as conn:
        for relation in REQUIRED_RELATIONS:
            found = conn.execute(
                text("SELECT to_regclass(:qualified)"),
                {"qualified": f"public.{relation}"},
            ).scalar()
            if found is None:
                missing.append(relation)
    if missing:
        raise RuntimeError(
            "required relations missing "
            f"{sorted(missing)} in {_target_diagnostic(POSTGRES_URL)}. "
            "This module does not create relations; the schema must already be "
            "built by the official migrations (backend/alembic.ini, "
            "`alembic upgrade head`) and its revision recorded in the "
            "provisioning evidence for this target."
        )


def _open_gated_engine():
    """Build the engine only after the official gate has accepted the target."""
    from test_safety_gate import verify_test_database

    info = verify_test_database(POSTGRES_URL)
    _require_answered_server(info, POSTGRES_URL)
    engine = create_engine(POSTGRES_URL, pool_size=10, max_overflow=20, pool_pre_ping=True)
    try:
        answers = _confirm_target_on_test_engine(engine, POSTGRES_URL, info)
        _require_required_relations(engine)
    except Exception:
        engine.dispose()
        raise
    return engine, answers


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
    engine, answers = _gate_or_fail()
    print(
        "quota concurrency target accepted on the test engine's own connection: "
        f"database={answers['database']} role={answers['role']} "
        f"server={answers['server_addr']}:{answers['server_port']}"
    )
    yield engine
    engine.dispose()


class _RoundLifecycle:
    """What this round started, and what it therefore has to leave behind clean.

    The ordering bug this replaces: a timed-out worker made `_start_and_join`
    raise, but the fixture teardown still ran `_delete_round_rows`, so a DELETE
    could be issued next to a live worker that had not finished writing. Now the
    round keeps one record of its own workers and its own user ids, checks them
    again immediately before any DELETE, and refuses the DELETE while one is
    alive. The residual state is named in the failure so the next person
    inherits a list, not a half-cleaned database.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.entries = []          # {"name", "thread", "stopped"} per worker started
        self.users = []            # user ids this round created
        self.cancel = threading.Event()
        self.cancel_reason = None
        self.primary_error = None  # first execution failure, kept for the cleanup message
        self.refusals = []

    def start(self, threads) -> None:
        """Record every worker before starting it, so none can be untracked."""
        with self._lock:
            for thread in threads:
                self.entries.append({"name": thread.name, "thread": thread, "stopped": None})
        for thread in threads:
            thread.start()

    def join_each(self, timeout: float) -> list:
        """Join one worker at a time and record whether it actually stopped."""
        deadline = time.monotonic() + timeout
        for entry in self.entries:
            entry["thread"].join(max(0.0, deadline - time.monotonic()))
            entry["stopped"] = not entry["thread"].is_alive()
        return self.live_workers()

    def live_workers(self) -> list:
        with self._lock:
            return [e["name"] for e in self.entries if e["thread"].is_alive()]

    def note_error(self, exc) -> None:
        """Keep the first execution error so a later cleanup error cannot hide it."""
        with self._lock:
            if self.primary_error is None:
                self.primary_error = f"{type(exc).__name__}: {exc}"

    def request_cancel(self, reason: str) -> str:
        """Cancellation this round controls: a flag its own workers check.

        Not a kill. No signal is sent to a process this round did not start and
        no other backend session is terminated; a worker already inside a
        statement either finishes or the round leaves it running and refuses to
        delete under it.
        """
        self.cancel_reason = reason
        self.cancel.set()
        return reason

    def residual_state(self) -> dict:
        with self._lock:
            return {
                "workers": [
                    {"name": e["name"], "stopped": e["stopped"], "alive": e["thread"].is_alive()}
                    for e in self.entries
                ],
                "users": list(self.users),
                "cancel_reason": self.cancel_reason,
                "primary_error": self.primary_error,
            }

    def add_user(self, user_id) -> None:
        with self._lock:
            self.users.append(str(user_id))

    def refused_cleanup(self, message: str) -> None:
        with self._lock:
            self.refusals.append(message)


@pytest.fixture
def round_lifecycle():
    """One lifecycle record per test, shared by its workers and its cleanup."""
    return _RoundLifecycle()


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


def _delete_round_rows(Session, user_id, lifecycle) -> None:
    """Delete this round's rows and prove they went. Errors are not swallowed.

    The stop check runs again here rather than relying on whatever the test body
    last saw: a worker that was alive one statement ago can still be writing, and
    deleting underneath it would leave a "cleaned" report describing rows that
    came back afterwards.
    """
    live = lifecycle.live_workers()
    if live:
        residual = lifecycle.residual_state()
        message = (
            f"cleanup refused for user {user_id}: these workers of this round are "
            f"still running: {live}. No DELETE was issued, so the rows are left as "
            f"this worker can still write them. Residual state: "
            f"users={residual['users']} workers={residual['workers']} "
            f"cancel_reason={residual['cancel_reason']!r}. "
            f"Primary execution error preserved: {residual['primary_error']!r}"
        )
        lifecycle.refused_cleanup(message)
        raise AssertionError(message)

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
def round_user(pg_engine, round_lifecycle):
    """Create this test's users, and delete them at teardown under the lifecycle guard.

    Cleanup lives in teardown rather than in a `finally` inside the test body on
    purpose: an error raised by a `finally` is the one the reporter shows and it
    hides the execution error the test just hit. As a teardown, pytest keeps the
    test failure and reports the cleanup problem separately, so both survive.
    """
    Session = sessionmaker(bind=pg_engine)
    created = []

    def create(*, ai_usage_limit: int):
        user_id = _create_round_user(Session, ai_usage_limit=ai_usage_limit)
        round_lifecycle.add_user(user_id)
        created.append(user_id)
        return user_id

    yield create

    failures = []
    for user_id in created:
        try:
            _delete_round_rows(Session, user_id, round_lifecycle)
        except Exception as exc:  # noqa: BLE001 - collected, never dropped
            failures.append(f"user {user_id}: {type(exc).__name__}: {exc}")
    if failures:
        raise AssertionError(
            "round cleanup did not complete: " + " | ".join(failures)
            + f"; residual state: {round_lifecycle.residual_state()}"
        )


@pytest.fixture
def test_user_with_limit(round_user):
    """A user with ai_usage_limit=2, created and removed by this module only."""
    return round_user(ai_usage_limit=2)


def _start_and_join(threads, lifecycle, timeout: float = 30.0) -> None:
    """Start every thread, join every thread, and prove none is still running.

    `join(timeout)` alone returns without saying whether the thread finished, so
    a hung worker would let the assertions below read a partial result set. A
    worker that is still alive is not killed from here: this round raises its own
    cancel flag, records the reason, and cleanup is refused until it stops.
    """
    lifecycle.start(threads)
    live = lifecycle.join_each(timeout)
    if live:
        reason = f"threads still running after {timeout}s: {live}"
        lifecycle.request_cancel(reason)
        lifecycle.note_error(AssertionError(reason))
        raise AssertionError(
            f"{reason}. Cancel requested through this round's own flag — no process "
            "outside this round was signalled and no other backend session was "
            "terminated — and cleanup will be refused while any of them is alive. "
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


def _threaded(names_and_fns, lifecycle, timeout: float = 30.0) -> _Outcomes:
    outcomes = _Outcomes([name for name, _ in names_and_fns])
    barrier = threading.Barrier(len(names_and_fns))

    def run(name, fn):
        if lifecycle.cancel.is_set():
            outcomes.record(name, "cancelled")
            return
        try:
            barrier.wait(timeout=timeout)
            outcomes.record(name, fn())
        except Exception as exc:  # noqa: BLE001 - classified by require_complete
            lifecycle.note_error(exc)
            outcomes.error(name, exc)

    threads = [
        threading.Thread(target=run, name=f"quota-{name}", args=(name, fn))
        for name, fn in names_and_fns
    ]
    _start_and_join(threads, lifecycle, timeout=timeout)
    outcomes.require_complete()
    return outcomes


def _sqlstate(exc):
    """The SQLSTATE if the driver attached one, without assuming it did."""
    for candidate in (getattr(exc, "orig", None), exc):
        if candidate is None:
            continue
        for attribute in ("pgcode", "sqlstate"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and value:
                return value
    return None


def _is_named_denial(exc, kind: str) -> bool:
    """Match the exception the guard actually raises, by its identity and fields.

    Text matching is what this replaces: an error message that happens to contain
    the marker would have been scored as the quota guard working.
    """
    if kind == "quota_exceeded":
        detail = getattr(exc, "detail", None)
        return (
            type(exc) is HTTPException
            and getattr(exc, "status_code", None) == status.HTTP_429_TOO_MANY_REQUESTS
            and isinstance(detail, dict)
            and detail.get("error") == "quota_exceeded"
        )
    if kind == "reservation_state":
        return type(exc) is ValueError and "is not in 'reserved' status" in str(exc)
    raise ValueError(f"unknown denial kind: {kind!r}")


def _classify(kind: str, fn):
    """Run fn, mapping only the named refusal to 'denied'.

    A database-level failure is never a denial. If the exception carries a
    SQLSTATE — lock timeout 55P03, deadlock 40P01, serialization failure 40001,
    statement cancel 57014, connection class 08* — or is a DBAPI error at all, it
    is raised with the code in the message so the run says "the database did not
    finish", not "the quota guard refused".
    """
    try:
        fn()
        return "ok"
    except Exception as exc:  # noqa: BLE001 - classified by identity, then re-raised
        code = _sqlstate(exc)
        if code is not None or isinstance(exc, DBAPIError):
            if code in NOT_A_DENIAL:
                label = NOT_A_DENIAL[code]
            elif (code or "").startswith("08"):
                label = "connection_exception"
            else:
                label = "database error"
            raise AssertionError(
                f"not a quota denial: {type(exc).__name__} SQLSTATE={code!r} "
                f"({label}): {exc}"
            ) from exc
        if _is_named_denial(exc, kind):
            return "denied"
        raise


def test_concurrent_reservations_with_limit(pg_engine, test_user_with_limit, round_lifecycle):
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
    ], round_lifecycle)

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


def test_concurrent_settlement_same_reservation(pg_engine, test_user_with_limit, round_lifecycle):
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
        ("a", lambda: _classify("reservation_state", settle)),
        ("b", lambda: _classify("reservation_state", settle)),
    ], round_lifecycle)

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


def test_concurrent_settle_and_release(pg_engine, test_user_with_limit, round_lifecycle):
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
        ("settle", lambda: _classify("reservation_state", lambda: run(settle_reservation))),
        ("release", lambda: _classify("reservation_state", lambda: run(release_reservation))),
    ], round_lifecycle)

    assert outcomes.count("ok") == 1, (
        f"exactly one of settle/release should succeed: {outcomes.results}"
    )
    assert outcomes.count("denied") == 1, f"the other must be refused: {outcomes.results}"

    session = Session()
    try:
        row = session.query(QuotaReservation).filter(
            QuotaReservation.id == reservation_id
        ).first()
        reservation_status = row.status
        counter = session.query(QuotaCounter).filter(
            QuotaCounter.user_id == test_user_with_limit,
            QuotaCounter.quota_type == "ai_usage",
        ).first()
        used = None if counter is None else counter.used
    finally:
        session.close()

    assert reservation_status in ("settled", "released"), f"reservation left in {reservation_status!r}"
    assert used == (1 if reservation_status == "settled" else 0), (
        f"status {reservation_status!r} should pair with used="
        f"{1 if reservation_status == 'settled' else 0}, got {used}"
    )


def test_reservation_counter_creation_race(pg_engine, round_user, round_lifecycle):
    """Two threads create the counter row concurrently: no lost update, no duplicate."""
    Session = sessionmaker(bind=pg_engine)
    user_id = round_user(ai_usage_limit=10)

    def reserve_and_settle():
        inner = Session()
        try:
            reservation = reserve_quota(inner, user_id, "ai_usage", amount=1)
            settle_reservation(inner, reservation.id)
            inner.commit()
        finally:
            inner.close()

    outcomes = _threaded([("a", reserve_and_settle), ("b", reserve_and_settle)], round_lifecycle)
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
