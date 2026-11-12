"""Negative tests for the reworked ``test_quota_concurrency.py`` (directive §二).

The module used to hold two schema-wide operations (``Base.metadata.create_all``
and ``drop_all``) and to swallow whatever the teardown raised, so a run could
erase an entire database and still report no problem. It also turned a failed
connection into a skip, which is how a run against the wrong host looked clean.

The rework promises these things, and each is tested against the module's own
functions rather than against a description of them:

1. an unset target skips, and a supplied target that cannot be verified FAILS;
2. the identity the gate saw is re-read on the connection the engine hands out,
   and compared with the concrete target — not with "some loopback instance";
3. the module issues no DDL, and refuses to continue when a required relation
   is missing;
4. cleanup errors surface instead of being caught and dropped;
5. cleanup is refused outright while a worker this round started is still
   running, so no DELETE is issued beside a thread that can still write;
6. a concurrency test cannot pass on a partial result set, and a database-level
   failure is never counted as a quota denial.

Nothing here connects to a database. This host has a live PostgreSQL on
127.0.0.1:5432, so every path that could reach it is either given a port with no
listener, a database outside the gate's whitelist, or a fake connection object.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / "tests" / "test_quota_concurrency.py"
VARIABLE = "ALETHRA_TEST_POSTGRES_URL"
OPT_IN = "ALETHRA_PG_QUOTA_CONCURRENCY_ENABLE"

#: A target shape the gate accepts textually but that cannot be reached: port
#: 55432 has no listener here (verified with lsof/nc before writing this).
SENTINEL_PASSWORD = "SENTINEL-NOT-A-REAL-PASSWORD"
UNREACHABLE_URL = (
    f"postgresql://alethra_runner:{SENTINEL_PASSWORD}@127.0.0.1:55432/alethra_owner_test"
)

DDL = re.compile(r"\b(CREATE|DROP|ALTER|TRUNCATE)\b", re.I)


def _source() -> str:
    return TARGET.read_text(encoding="utf-8")


def _raised(fn):
    """Return the exception `fn` raised, or None if it returned normally.

    Used instead of `pytest.raises` for the fail-versus-skip cases: a mutation
    that turns the guard's failure into a skip would otherwise skip this test
    too, and a skipped guard test looks like a passing one in a summary line.
    """
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - the point is to classify it
        return exc
    return None


def _assert_failed_not_skipped(raised):
    """Assert the guard failed the run, and did not skip it.

    `pytest.raises(pytest.fail.Exception)` cannot express this: a mutation that
    swaps the failure for a skip raises Skipped, which pytest reads as "test
    skipped" instead of "test failed", so the guard appears to pass by vanishing.
    """
    assert isinstance(raised, pytest.fail.Exception), (
        f"a supplied-but-unverifiable target must fail the run, got "
        f"{type(raised).__name__}: {raised!r}"
    )
    assert not isinstance(raised, pytest.skip.Exception)
    assert "Not skipping" in str(raised), str(raised)


@pytest.fixture(scope="module")
def mod():
    """Import the module under test with its required inputs present.

    Importing is the only way to exercise its functions; with the guard branch
    satisfied it stops before touching a database, because nothing connects at
    import time any more.
    """
    previous = {name: os.environ.get(name) for name in (VARIABLE, OPT_IN)}
    os.environ[VARIABLE] = UNREACHABLE_URL
    os.environ[OPT_IN] = "1"
    sys.modules.pop("tests.test_quota_concurrency", None)
    try:
        import tests.test_quota_concurrency as module
        yield module
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        sys.modules.pop("tests.test_quota_concurrency", None)


# --------------------------------------------------------------------------
# 1. The module no longer contains the operations that made it dangerous.


def test_no_schema_wide_operations_remain():
    names = {
        node.attr
        for node in ast.walk(ast.parse(_source()))
        if isinstance(node, ast.Attribute)
    }
    for forbidden in ("create_all", "drop_all"):
        assert forbidden not in names, f"{forbidden} is still reachable from the module"
    assert "Base.metadata" not in _source()


def test_no_handler_swallows_an_exception():
    """`except Exception: pass` was how a failed teardown stayed invisible."""
    swallowed = []
    for node in ast.walk(ast.parse(_source())):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [stmt for stmt in node.body if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant)]
        if body and all(isinstance(stmt, ast.Pass) for stmt in body):
            swallowed.append(node.lineno)
    assert swallowed == [], f"handlers that only pass, at lines {swallowed}"


def test_no_sleep_only_synchronization():
    source = _source()
    assert "time.sleep" not in source, "sleep is not evidence that a thread stopped"


def test_threads_are_joined_and_proven_stopped():
    """Checked as syntax, not as one literal string a rewrite can rename.

    A bare `join()` can block the suite forever, and a `join(timeout)` says
    nothing about whether the thread stopped, so the module has to pass a
    deadline and then ask `is_alive()` — both recorded and asserted.
    """
    joins, alive = [], 0
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "join":
                joins.append(len(node.args))
            elif node.func.attr == "is_alive":
                alive += 1

    assert joins, "no thread join at all"
    assert all(count == 1 for count in joins), (
        f"every join needs a timeout argument, got arg counts {joins}"
    )
    assert alive >= 2, (
        f"is_alive() appears {alive} times: stop state must be recorded per worker "
        "and asserted before cleanup"
    )


def test_partial_results_cannot_satisfy_the_concurrency_assertions():
    """`success <= 1` also passed with zero successes, which is not atomicity.

    Checked structurally rather than by searching for one string: four races each
    compare an outcome count, and weakening any single one of them has to fail.
    """
    comparisons = []
    for node in ast.walk(ast.parse(_source())):
        left = getattr(node, "left", None)
        if not (isinstance(node, ast.Compare) and isinstance(left, ast.Call)
                and isinstance(left.func, ast.Attribute) and left.func.attr == "count"):
            continue
        comparisons.append((node.lineno, [type(op).__name__ for op in node.ops]))

    assert len(comparisons) >= 4, f"expected one exact count per race, found {comparisons}"
    bounds = [line for line, ops in comparisons if ops != ["Eq"]]
    assert not bounds, f"outcome counts compared with a bound instead of ==, at lines {bounds}"
    assert "require_complete()" in _source(), "every thread has to report an outcome"


# --------------------------------------------------------------------------
# 2. The server that answered must be the target that was declared.


def test_unix_socket_answer_is_refused_because_it_cannot_be_matched(mod):
    with pytest.raises(RuntimeError, match="could not identify the server"):
        mod._require_answered_server(
            {"server_addr": None, "server_port": None}, "postgresql://u:pw@127.0.0.1:5432/alethra_test"
        )


def test_a_server_on_another_host_is_refused(mod):
    with pytest.raises(RuntimeError, match="rejected the server"):
        mod._require_answered_server(
            {"server_addr": "10.0.0.5", "server_port": 5432},
            "postgresql://u:pw@127.0.0.1:5432/alethra_test",
        )


def test_a_server_answering_on_another_port_is_refused(mod):
    with pytest.raises(RuntimeError, match="answered on port 5433"):
        mod._require_answered_server(
            {"server_addr": "127.0.0.1", "server_port": 5433},
            "postgresql://u:pw@127.0.0.1:5432/alethra_test",
        )


def test_the_loopback_answer_the_target_named_is_accepted(mod):
    mod._require_answered_server(
        {"server_addr": "127.0.0.1", "server_port": 5432},
        "postgresql://u:pw@127.0.0.1:5432/alethra_test",
    )


# --------------------------------------------------------------------------
# 2b. The identity is read again on the connection the tests will actually use.


APPROVED = {
    "database": "alethra_owner_test",
    "user": "alethra_runner",
    "server_addr": "127.0.0.1",
    "server_port": 5432,
}

#: The target shape the provisioning approval names. Only ever parsed here:
#: `_confirm_target_on_test_engine` is handed a fake engine, so no socket is
#: opened and the credential stays a sentinel that belongs to no role.
APPROVED_URL = (
    f"postgresql://alethra_runner:{SENTINEL_PASSWORD}@127.0.0.1:5432/alethra_owner_test"
)


def _answers(mod, identity, url=APPROVED_URL, approved=None):
    """Run the engine-side confirmation and return what it reported."""
    statements: list[tuple[str, dict]] = []
    engine = _FakeEngine(statements, present=set(), identity=identity)
    answers = mod._confirm_target_on_test_engine(engine, url, approved or APPROVED)
    return answers, statements


def test_the_engine_connection_reports_the_approved_target(mod):
    answers, statements = _answers(mod, MATCHING_IDENTITY)
    assert answers == {
        "database": "alethra_owner_test",
        "role": "alethra_runner",
        "server_addr": "127.0.0.1",
        "server_port": 5432,
    }
    assert len(statements) == 1, "one identity read is enough; it must not go hunting"
    assert statements[0][0].strip().upper().startswith("SELECT"), statements
    for function in ("current_database()", "current_user", "inet_server_addr()", "inet_server_port()"):
        assert function in statements[0][0], statements


def test_an_engine_connection_on_another_database_is_refused(mod):
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod, ("alethra_acceptance", "alethra_runner", "127.0.0.1", 5432))
    assert "current_database()='alethra_acceptance'" in str(excinfo.value)


def test_an_engine_connection_logged_in_as_another_role_is_refused(mod):
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod, ("alethra_owner_test", "postgres", "127.0.0.1", 5432))
    assert "current_user='postgres'" in str(excinfo.value)


def test_a_different_loopback_stack_is_not_the_declared_target(mod):
    """The rule is the concrete approved target, not "some loopback answered".

    A URL that names 127.0.0.1 must be answered as 127.0.0.1. The other loopback
    address is a different socket on the same machine, and accepting it would be
    substituting a broader contract than the one that was approved.
    """
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod, ("alethra_owner_test", "alethra_runner", "::1", 5432))
    message = str(excinfo.value)
    assert "answered as '::1'" in message and "target names host '127.0.0.1'" in message, message


def test_localhost_may_be_answered_on_either_loopback_stack(mod):
    """One documented exception, because `localhost` resolves to both addresses."""
    answers, _ = _answers(mod,
        ("alethra_owner_test", "alethra_runner", "::1", 5432),
        url="postgresql://alethra_runner:pw@localhost:5432/alethra_owner_test",
    )
    assert answers["server_addr"] == "::1"


def test_an_engine_connection_on_another_port_is_refused(mod):
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod, ("alethra_owner_test", "alethra_runner", "127.0.0.1", 5433))
    assert "answered on port 5433" in str(excinfo.value)


def test_an_engine_connection_over_a_unix_socket_is_refused(mod):
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod, ("alethra_owner_test", "alethra_runner", None, None))
    assert "socket answer cannot be matched" in str(excinfo.value)


def test_engine_and_preflight_connections_must_agree(mod):
    """The point of reading twice: a pooled connection may not be the checked one."""
    with pytest.raises(RuntimeError) as excinfo:
        _answers(mod,
            ("alethra_other", "alethra_runner", "127.0.0.1", 5432),
            approved={"database": "alethra_owner_test", "user": "alethra_runner"},
        )
    assert "the gate's preflight connection reported" in str(excinfo.value)


def test_the_engine_identity_refusal_does_not_echo_the_credential(mod):
    raised = _raised(
        lambda: _answers(mod, ("alethra_other", "alethra_runner", "127.0.0.1", 5432))
    )
    assert isinstance(raised, RuntimeError), raised
    assert SENTINEL_PASSWORD not in str(raised), str(raised)


def test_the_engine_is_built_before_the_identity_and_disposed_on_refusal(mod, monkeypatch):
    """Order inside _open_gated_engine: gate, preflight answer, engine, identity, relations."""
    from test_safety_gate import TestDatabaseGateError  # noqa: F401 - keeps the gate import real

    calls = []
    monkeypatch.setattr(mod, "POSTGRES_URL", APPROVED_URL)
    monkeypatch.setattr(
        "test_safety_gate.verify_test_database",
        lambda _url: calls.append("gate") or dict(APPROVED),
    )
    engine = _FakeEngine([], present=set(), identity=("alethra_other", "alethra_runner", "127.0.0.1", 5432))

    def fake_create_engine(*a, **k):
        calls.append("engine")
        return engine

    def record_relations(_engine):
        calls.append("relations")

    monkeypatch.setattr(mod, "create_engine", fake_create_engine)
    monkeypatch.setattr(mod, "_require_required_relations", record_relations)

    raised = _raised(mod._open_gated_engine)
    assert isinstance(raised, RuntimeError), raised
    assert calls == ["gate", "engine"], f"relations were read from an unverified connection: {calls}"
    assert engine.disposed, "a refused engine must not be handed to a test"


# --------------------------------------------------------------------------
# 3. The schema must already exist; the module reads, never builds.


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def one(self):
        return self._value


class _FakeConn:
    """Records every statement; answers identity reads and to_regclass reads."""

    def __init__(self, statements, present, identity=None):
        self._statements = statements
        self._present = present
        self._identity = identity

    def execute(self, statement, params=None):
        text = str(statement)
        self._statements.append((text, dict(params or {})))
        if "current_database()" in text:
            if self._identity is None:
                raise AssertionError(
                    "the fake connection was asked for engine identity without an "
                    "answer configured; add identity=(database, role, addr, port)"
                )
            return _FakeResult(self._identity)
        table = (params or {}).get("qualified", "").split(".")[1:]
        found = table[0] if table and table[0] in self._present else None
        return _FakeResult(found)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


#: What a correctly provisioned target answers on the engine's own connection.
MATCHING_IDENTITY = ("alethra_owner_test", "alethra_runner", "127.0.0.1", 5432)


class _FakeEngine:
    def __init__(self, statements, present, identity=None):
        self._statements = statements
        self._present = present
        self._identity = identity
        self.disposed = False

    def connect(self):
        return _FakeConn(self._statements, self._present, self._identity)

    def dispose(self):
        self.disposed = True


def test_missing_relations_are_reported_without_any_ddl(mod, monkeypatch):
    statements: list[tuple[str, dict]] = []
    monkeypatch.setattr(mod, "REQUIRED_RELATIONS", ("users", "quota_counters"))
    with pytest.raises(RuntimeError) as excinfo:
        mod._require_required_relations(_FakeEngine(statements, present={"users"}))

    message = str(excinfo.value)
    assert "quota_counters" in message, message
    assert "alembic upgrade head" in message, "the operator needs the command that fixes this"
    assert not any(DDL.search(text) for text, _ in statements), statements


def test_existing_relations_are_verified_with_reads_only(mod, monkeypatch):
    statements: list[tuple[str, dict]] = []
    relations = ("users", "pilot_entitlements", "quota_counters", "quota_reservations")
    monkeypatch.setattr(mod, "REQUIRED_RELATIONS", relations)
    mod._require_required_relations(_FakeEngine(statements, present=set(relations)))

    assert len(statements) == len(relations)
    assert all(text.strip().upper().startswith("SELECT") for text, _ in statements), statements


def test_the_relation_check_does_not_claim_migration_provenance(mod, monkeypatch):
    """`to_regclass` proves presence; the revision belongs to provisioning evidence.

    A message that said "migration verified" would be reading a fact the query
    cannot return, and probing `alembic_version` would need privileges the test
    role is not granted — so the check must not issue that read either.
    """
    relations = ("users", "pilot_entitlements", "quota_counters", "quota_reservations")
    monkeypatch.setattr(mod, "REQUIRED_RELATIONS", relations)

    statements: list[tuple[str, dict]] = []
    mod._require_required_relations(_FakeEngine(statements, present=set(relations)))
    assert len(statements) == len(relations), statements
    assert not [text for text, _ in statements if "alembic_version" in text], statements

    statements = []
    monkeypatch.setattr(mod, "REQUIRED_RELATIONS", ("quota_counters",))
    with pytest.raises(RuntimeError) as excinfo:
        mod._require_required_relations(_FakeEngine(statements, present=set()))
    message = str(excinfo.value)
    assert "required relations missing" in message, message
    assert "provisioning evidence" in message, message
    assert "does not create relations" in message, message
    for claim in ("migration verified", "schema verified", "revision confirmed"):
        assert claim not in message.lower(), f"the check over-claims: {message}"


# --------------------------------------------------------------------------
# 4. The official gate is the one being applied, on the real identity check.


class _FakeCursor:
    def __init__(self, log, identity, role):
        self._log = log
        self._identity = identity
        self._role = role

    def execute(self, statement, params=None):
        self._log.append((str(statement), params))
        self._current = "identity" if "current_database()" in str(statement) else "role"

    def fetchone(self):
        return self._identity if self._current == "identity" else self._role

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _GateConn:
    """A DBAPI connection with just enough surface for verify_database_identity."""

    def __init__(self, identity, role):
        self._identity = identity
        self._role = role
        self.log = []

    def cursor(self):
        return _FakeCursor(self.log, self._identity, self._role)


def _identity(db="alethra_owner_test", user="alethra_runner", addr="127.0.0.1", port=5432):
    return (db, user, addr, port)


def test_the_real_gate_rejects_a_database_that_is_not_the_declared_one():
    from test_safety_gate import TestDatabaseGateError, verify_database_identity

    conn = _GateConn(_identity(db="alethra_acceptance"), (False, False, False, False))
    with pytest.raises(TestDatabaseGateError, match="Database mismatch"):
        verify_database_identity(conn, "alethra_owner_test")
    assert not any(DDL.search(text) for text, _ in conn.log), conn.log


def test_the_real_gate_rejects_an_overprivileged_role():
    from test_safety_gate import TestDatabaseGateError, verify_database_identity

    expectations = (
        ("superuser", (True, False, False, False), "superuser"),
        ("createdb", (False, True, False, False), "elevated permissions"),
        ("createrole", (False, False, True, False), "elevated permissions"),
        ("bypassrls", (False, False, False, True), "BYPASSRLS"),
    )
    for label, role, phrase in expectations:
        conn = _GateConn(_identity(), role)
        with pytest.raises(TestDatabaseGateError) as excinfo:
            verify_database_identity(conn, "alethra_owner_test")
        assert phrase in str(excinfo.value), f"{label} was accepted: {excinfo.value}"
        assert not any(DDL.search(text) for text, _ in conn.log), conn.log


def test_the_real_gate_refuses_an_unknown_role():
    from test_safety_gate import TestDatabaseGateError, verify_database_identity

    conn = _GateConn(_identity(), None)
    with pytest.raises(TestDatabaseGateError, match="not found in pg_roles"):
        verify_database_identity(conn, "alethra_owner_test")


def test_the_real_gate_passes_a_matching_target_and_plain_role():
    from test_safety_gate import verify_database_identity

    conn = _GateConn(_identity(), (False, False, False, False))
    info = verify_database_identity(conn, "alethra_owner_test")
    assert info["database"] == "alethra_owner_test"
    assert info["server_addr"] == "127.0.0.1"
    assert not any(DDL.search(text) for text, _ in conn.log), conn.log


def test_a_refused_target_fails_and_is_not_reported_as_a_skip(mod, monkeypatch):
    from test_safety_gate import TestDatabaseGateError

    def refuse(_url):
        raise TestDatabaseGateError("REJECTED: Database 'alethra_acceptance' is not in the test whitelist")

    monkeypatch.setattr("test_safety_gate.verify_test_database", refuse)
    raised = _raised(mod._gate_or_fail)

    _assert_failed_not_skipped(raised)
    message = str(raised)
    assert "alethra_acceptance" in message
    assert SENTINEL_PASSWORD not in message, "the refusal echoed the credential"


def test_a_connection_failure_fails_and_is_not_reported_as_a_skip(mod, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def boom(_url):
        raise OperationalError("SELECT 1", {}, Exception("could not connect"))

    monkeypatch.setattr("test_safety_gate.verify_test_database", boom)
    raised = _raised(mod._gate_or_fail)
    _assert_failed_not_skipped(raised)
    assert "OperationalError" in str(raised)


def test_an_accepted_target_still_needs_the_answered_server_check(mod, monkeypatch):
    """Order matters: server identity is checked before any engine is built."""
    calls = []
    monkeypatch.setattr(
        "test_safety_gate.verify_test_database",
        lambda _url: calls.append("gate") or {
            "database": "alethra_owner_test", "user": "alethra_runner",
            "server_addr": "10.0.0.9", "server_port": 5432,
        },
    )

    def fake_create_engine(*a, **k):
        calls.append("engine")
        raise AssertionError("an engine must not exist for a server the guard rejects")

    monkeypatch.setattr(mod, "create_engine", fake_create_engine)
    raised = _raised(mod._gate_or_fail)
    _assert_failed_not_skipped(raised)
    assert calls == ["gate"], f"the guard ran before anything else could connect: {calls}"


# --------------------------------------------------------------------------
# 5. Cleanup reports what it could not remove.


class _FakeQuery:
    def __init__(self, session, label):
        self._session = session
        self._label = label

    def filter(self, *args, **kwargs):
        return self

    def delete(self):
        self._session.events.append(("delete", self._label))
        return self._session.deleted.get(self._label, 0)

    def count(self):
        self._session.events.append(("count", self._label))
        return self._session.remaining.get(self._label, 0)


class _FakeSession:
    """Counts rows by model name, so cleanup can be judged without a database."""

    events: list

    def __init__(self, events, deleted, remaining):
        self.events = events
        self.deleted = deleted
        self.remaining = remaining
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def query(self, model):
        return _FakeQuery(self, model.__name__)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _factory(events, deleted, remaining, pool):
    """Hand out a fresh fake session per call, and keep it in `pool`."""
    def make():
        session = _FakeSession(events, deleted, remaining)
        pool.append(session)
        return session
    return make


def _lifecycle(mod) -> object:
    """A round record with no workers and no users — the shape cleanup sees
    when the test body finished and every thread it started has stopped."""
    return mod._RoundLifecycle()


def test_a_cleanup_commit_error_is_not_swallowed(mod):
    """The old teardown caught every exception; a half-cleaned database vanished."""
    class Boom(_FakeSession):
        def commit(self):
            raise RuntimeError("deadlock detected while deleting test rows")

    events = []
    made = []

    def factory():
        session = Boom(events, {}, {})
        made.append(session)
        return session

    with pytest.raises(RuntimeError, match="deadlock"):
        mod._delete_round_rows(factory, "user-1", _lifecycle(mod))
    assert made[0].rolled_back and made[0].closed


def test_surviving_rows_are_reported(mod):
    events: list = []
    sessions: list = []
    factory = _factory(
        events,
        {"QuotaReservation": 1, "QuotaCounter": 1, "PilotEntitlement": 1, "User": 1},
        {"QuotaCounter": 1},
        sessions,
    )

    with pytest.raises(AssertionError, match="quota_counters"):
        mod._delete_round_rows(factory, "user-1", _lifecycle(mod))
    assert len(sessions) == 2, "delete and verify are separate commits, so both must be visible"
    assert sessions[0].committed and not sessions[1].committed


def test_clean_rows_leave_nothing_behind(mod):
    events: list = []
    factory = _factory(
        events,
        {"QuotaReservation": 2, "QuotaCounter": 1, "PilotEntitlement": 1, "User": 1},
        {},
        [],
    )

    mod._delete_round_rows(factory, "user-1", _lifecycle(mod))
    assert [kind for kind, _ in events].count("delete") == 4
    assert [kind for kind, _ in events].count("count") == 4


# --------------------------------------------------------------------------
# 5b. No DELETE is issued beside a worker that can still write (§一).


def _blocked_worker(lifecycle, release: threading.Event, name: str = "quota-stuck"):
    """Start one real thread and register it with the round, as the module does."""
    thread = threading.Thread(target=release.wait, name=name, daemon=True)
    lifecycle.start([thread])
    return thread


def test_cleanup_refuses_to_run_while_a_worker_is_blocked(mod):
    """The ordered case the owner asked for: a blocked worker, then teardown.

    Nothing is deleted, so the row set the next person inherits is the row set
    this worker can still write — and the failure says so instead of reporting a
    clean teardown.
    """
    release = threading.Event()
    lifecycle = mod._RoundLifecycle()
    thread = _blocked_worker(lifecycle, release)
    assert thread.is_alive(), "the worker must actually be blocked for this to prove anything"

    events: list = []
    sessions: list = []
    lifecycle.add_user("user-1")
    raised = _raised(
        lambda: mod._delete_round_rows(_factory(events, {}, {}, sessions), "user-1", lifecycle)
    )

    assert isinstance(raised, AssertionError), f"cleanup was not refused: {raised!r}"
    assert "cleanup refused" in str(raised), str(raised)
    assert events == [] and sessions == [], (
        f"a worker was alive and cleanup still touched the database: {events}"
    )
    assert lifecycle.refusals, "the refusal was not recorded on the round"

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive(), "the released worker must actually end"
    assert thread.daemon, "this test must not leave a non-daemon thread behind"


def test_the_refusal_names_the_worker_and_the_state_left_behind(mod):
    release = threading.Event()
    lifecycle = mod._RoundLifecycle()
    thread = _blocked_worker(lifecycle, release, name="quota-a")
    lifecycle.add_user("user-1")

    raised = _raised(
        lambda: mod._delete_round_rows(_factory([], {}, {}, []), "user-1", lifecycle)
    )
    message = str(raised)
    assert "quota-a" in message, f"the live worker is not named: {message}"
    assert "user-1" in message, f"the user whose rows remain is not named: {message}"
    assert "cancel_reason=None" in message, f"no cancel reason reported: {message}"

    release.set()
    thread.join(timeout=5)


def test_the_refusal_carries_the_execution_error_it_would_have_hidden(mod):
    """Both errors survive: the round's first failure is quoted in the refusal."""
    release = threading.Event()
    lifecycle = mod._RoundLifecycle()
    thread = _blocked_worker(lifecycle, release)
    lifecycle.note_error(RuntimeError("deadlock detected while settling a reservation"))

    raised = _raised(
        lambda: mod._delete_round_rows(_factory([], {}, {}, []), "user-1", lifecycle)
    )
    assert "Primary execution error preserved" in str(raised)
    assert "deadlock detected while settling a reservation" in str(raised), str(raised)

    release.set()
    thread.join(timeout=5)


def test_a_later_error_does_not_replace_the_first_one(mod):
    """note_error keeps the primary failure; a cleanup error cannot overwrite it."""
    lifecycle = mod._RoundLifecycle()
    lifecycle.note_error(RuntimeError("first: the statement this round actually hit"))
    lifecycle.note_error(AssertionError("second: cleanup did not complete"))

    assert lifecycle.residual_state()["primary_error"].startswith("RuntimeError: first:")


def test_cleanup_proceeds_once_every_worker_has_stopped(mod):
    """The guard is a gate, not a permanent ban: after the join, cleanup runs."""
    release = threading.Event()
    lifecycle = mod._RoundLifecycle()
    thread = _blocked_worker(lifecycle, release)
    release.set()
    thread.join(timeout=5)
    assert lifecycle.live_workers() == []

    events: list = []
    mod._delete_round_rows(_factory(events, {}, {}, []), "user-1", lifecycle)
    assert [kind for kind, _ in events].count("delete") == 4
    assert lifecycle.refusals == []


# --------------------------------------------------------------------------
# 6. A thread that does not stop is an error, not a result.


def test_a_thread_still_running_after_join_is_reported(mod):
    """The whole chain, with a real blocked worker: timeout → cancel → no DELETE."""
    release = threading.Event()
    lifecycle = mod._RoundLifecycle()
    blocked = threading.Thread(target=release.wait, name="quota-stuck", daemon=True)
    try:
        raised = _raised(lambda: mod._start_and_join([blocked], lifecycle, timeout=0.05))
        assert isinstance(raised, AssertionError), f"the live thread was accepted: {raised!r}"
        assert "still running after" in str(raised), str(raised)
        assert blocked.is_alive(), (
            "the round stopped a worker it does not own; it may only set its own flag"
        )
        assert lifecycle.cancel.is_set(), "the round did not raise its own cancel flag"
        assert "quota-stuck" in lifecycle.cancel_reason
        assert "no other backend session was terminated" in str(raised)
        assert lifecycle.residual_state()["primary_error"].startswith(
            "AssertionError: threads still running"
        )
        assert lifecycle.live_workers() == ["quota-stuck"]

        events: list = []
        refusal = _raised(
            lambda: mod._delete_round_rows(_factory(events, {}, {}, []), "user-1", lifecycle)
        )
        assert isinstance(refusal, AssertionError) and "cleanup refused" in str(refusal)
        assert events == [], f"cleanup deleted rows beside the live worker: {events}"
    finally:
        release.set()
    blocked.join(timeout=5)
    assert not blocked.is_alive(), "the released thread must actually end"
    assert blocked.daemon, "this test must not leave a non-daemon thread behind"


def test_started_threads_are_all_joined(mod):
    done = []
    lifecycle = mod._RoundLifecycle()
    threads = [threading.Thread(target=done.append, args=(i,), name=f"t{i}") for i in range(3)]
    mod._start_and_join(threads, lifecycle, timeout=10)
    assert sorted(done) == [0, 1, 2]
    assert lifecycle.live_workers() == []
    assert [entry["stopped"] for entry in lifecycle.entries] == [True, True, True], (
        "each worker's stop state is recorded, not assumed from the join returning"
    )


def test_a_cancelled_round_leaves_its_workers_off_the_database(mod):
    """Once the round has requested cancel, a worker that has not begun reports
    "cancelled" instead of starting a transaction nobody is waiting for."""
    lifecycle = mod._RoundLifecycle()
    lifecycle.request_cancel("threads still running after 0.05s: ['quota-stuck']")
    ran = []

    outcomes = mod._threaded([("a", lambda: ran.append("a"))], lifecycle, timeout=5)

    assert outcomes.results == {"a": "cancelled"}, outcomes.results
    assert ran == [], "a worker ran after the round asked for cancellation"


def test_an_unexpected_thread_exception_is_not_counted_as_a_denial(mod):
    outcomes = mod._Outcomes(["a", "b"])
    outcomes.record("a", "ok")
    outcomes.error("b", ValueError("connection reset"))
    with pytest.raises(AssertionError, match="unexpected exceptions"):
        outcomes.require_complete()


def test_a_missing_outcome_is_not_treated_as_complete(mod):
    outcomes = mod._Outcomes(["a", "b"])
    outcomes.record("a", "ok")
    with pytest.raises(AssertionError, match="every thread must report an outcome"):
        outcomes.require_complete()


# --------------------------------------------------------------------------
# 6b. A database failure is never counted as a quota denial (§四 step 6).


class _DriverError(Exception):
    """Stand-in for what psycopg2 (`pgcode`) / psycopg3 (`sqlstate`) attach."""

    def __init__(self, message, code=None, attribute="pgcode"):
        super().__init__(message)
        if code is not None:
            setattr(self, attribute, code)


def _dbapi_error(message, code=None, driver_error=None):
    orig = driver_error if driver_error is not None else _DriverError(message, code)
    return DBAPIError("SELECT 1 FROM quota_reservations", None, orig)


def _raiser(exc):
    def go():
        raise exc
    return go


@pytest.mark.parametrize(
    ("sqlstate", "label"),
    [
        ("55P03", "lock_not_available"),
        ("40P01", "deadlock_detected"),
        ("40001", "serialization_failure"),
        ("57014", "query_canceled"),
        ("08006", "connection_exception"),
    ],
)
def test_a_database_failure_is_never_counted_as_a_quota_denial(mod, sqlstate, label):
    """The failure this replaces: text matching scored a lock timeout as the
    quota guard refusing, which would read as concurrency behaviour proven."""
    raised = _raised(
        lambda: mod._classify("quota_exceeded", _raiser(_dbapi_error("could not serialize", sqlstate)))
    )

    assert isinstance(raised, AssertionError), (
        f"SQLSTATE {sqlstate} was not refused as a denial, it returned/raised {raised!r}"
    )
    assert "not a quota denial" in str(raised), str(raised)
    assert sqlstate in str(raised), f"the error code is not in the report: {raised}"
    assert label in str(raised), f"{label} not named: {raised}"


def test_a_driver_error_that_carries_no_sqlstate_is_still_not_a_denial(mod):
    raised = _raised(
        lambda: mod._classify(
            "quota_exceeded",
            _raiser(_dbapi_error("connection reset by peer",
                                 driver_error=ConnectionResetError("connection reset by peer"))),
        )
    )
    assert isinstance(raised, AssertionError), f"a DBAPIError was scored as denied: {raised!r}"
    assert "database error" in str(raised), str(raised)


def test_an_error_message_that_merely_contains_the_marker_is_not_a_denial(mod):
    for kind, message in (
        ("quota_exceeded", "429 quota_exceeded while pruning stale reservations"),
        ("reservation_state", "Reservation 7f2a is not in 'reserved' status"),
    ):
        raised = _raised(lambda m=message, k=kind: mod._classify(k, _raiser(RuntimeError(m))))
        assert isinstance(raised, RuntimeError), (
            f"{kind}: a RuntimeError whose text matched was scored as a denial ({raised!r})"
        )


def test_the_real_refusals_are_still_scored_as_denied(mod):
    def over_limit():
        raise mod.HTTPException(
            status_code=429, detail={"error": "quota_exceeded", "limit": 2}
        )

    def wrong_state():
        raise ValueError("Reservation 7f2a is not in 'reserved' status")

    assert mod._classify("quota_exceeded", over_limit) == "denied"
    assert mod._classify("reservation_state", wrong_state) == "denied"


def test_the_quota_contract_shape_is_what_counts_not_the_number_429(mod):
    """A 429 without the error field, and the right field at another status,
    are both re-raised: neither says the quota guard refused this reservation."""
    def plain_429():
        raise mod.HTTPException(status_code=429, detail="too many requests")

    def wrong_status():
        raise mod.HTTPException(status_code=400, detail={"error": "quota_exceeded"})

    for fn in (plain_429, wrong_status):
        raised = _raised(lambda: mod._classify("quota_exceeded", fn))
        assert isinstance(raised, mod.HTTPException), f"scored as denied: {raised!r}"


# --------------------------------------------------------------------------
# 7. End to end, in a real pytest process, without a database.


def _run_pytest(tmp_path: Path, env: dict) -> subprocess.CompletedProcess:
    full = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "ALETHRA_TEST_DB": f"sqlite:///{tmp_path / 'harness.db'}",
    }
    full.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
         "tests/test_quota_concurrency.py"],
        cwd=BACKEND, env=full, capture_output=True, text=True, timeout=300,
    )


def test_unset_target_skips_by_name(tmp_path):
    env = {key: value for key, value in os.environ.items() if key not in (VARIABLE, OPT_IN)}
    env["ALETHRA_TEST_DB"] = f"sqlite:///{tmp_path / 'harness.db'}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
         "tests/test_quota_concurrency.py"],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 5, output[-2000:]
    assert VARIABLE in output, "the skip reason must name the required variable"
    assert "Traceback" not in output


def test_this_module_is_excluded_from_the_pg_run_by_default(tmp_path):
    """The pg layer exports the target for every module, so the opt-in is the
    exclusion; without it the module must not reach a database at all."""
    result = _run_pytest(tmp_path, {VARIABLE: UNREACHABLE_URL})
    output = result.stdout + result.stderr
    assert result.returncode == 5, output[-2000:]
    assert OPT_IN in output, f"the skip does not name the opt-in: {output[-1500:]}"
    assert SENTINEL_PASSWORD not in output, "the skip reason echoed the credential"


def test_opted_in_module_with_an_unverifiable_target_does_not_pass(tmp_path):
    """§二: a target that is set but refused must fail. A skip here would let a
    wrong host be recorded as a clean run."""
    result = _run_pytest(tmp_path, {VARIABLE: UNREACHABLE_URL, OPT_IN: "1"})
    output = result.stdout + result.stderr
    assert result.returncode == 1, output[-2500:]
    assert "Not skipping" in output, output[-1500:]
    assert "1 passed" not in output and "error" in output.lower()
    assert SENTINEL_PASSWORD not in output, "the failure message echoed the credential"
