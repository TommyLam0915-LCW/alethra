"""Negative tests for the reworked ``test_quota_concurrency.py`` (directive §二).

The module used to hold two schema-wide operations (``Base.metadata.create_all``
and ``drop_all``) and to swallow whatever the teardown raised, so a run could
erase an entire database and still report no problem. It also turned a failed
connection into a skip, which is how a run against the wrong host looked clean.

The rework promises five things, and each is tested against the module's own
functions rather than against a description of them:

1. an unset target skips;
2. a supplied target that cannot be verified FAILS — never skips;
3. the module issues no DDL, and refuses to continue when the schema is missing;
4. cleanup errors surface instead of being caught and dropped;
5. a concurrency test cannot pass on a partial result set.

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
from pathlib import Path

import pytest

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
    source = _source()
    assert "thread.join(timeout)" in source
    assert "is_alive()" in source, "a join without an is_alive check accepts a hung thread"


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
# 3. The schema must already exist; the module reads, never builds.


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """Records every statement, and answers to_regclass from `present`."""

    def __init__(self, statements, present):
        self._statements = statements
        self._present = present

    def execute(self, statement, params=None):
        self._statements.append((str(statement), dict(params or {})))
        table = (params or {}).get("qualified", "").split(".")[1:]
        found = table[0] if table and table[0] in self._present else None
        return _FakeResult(found)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, statements, present):
        self._statements = statements
        self._present = present
        self.disposed = False

    def connect(self):
        return _FakeConn(self._statements, self._present)

    def dispose(self):
        self.disposed = True


def test_missing_schema_is_reported_without_any_ddl(mod, monkeypatch):
    statements: list[tuple[str, dict]] = []
    monkeypatch.setattr(mod, "REQUIRED_TABLES", ("users", "quota_counters"))
    with pytest.raises(RuntimeError) as excinfo:
        mod._require_migration_built_schema(_FakeEngine(statements, present={"users"}))

    message = str(excinfo.value)
    assert "quota_counters" in message, message
    assert "alembic upgrade head" in message, "the operator needs the command that fixes this"
    assert not any(DDL.search(text) for text, _ in statements), statements


def test_existing_schema_is_verified_with_reads_only(mod, monkeypatch):
    statements: list[tuple[str, dict]] = []
    tables = ("users", "pilot_entitlements", "quota_counters", "quota_reservations")
    monkeypatch.setattr(mod, "REQUIRED_TABLES", tables)
    mod._require_migration_built_schema(_FakeEngine(statements, present=set(tables)))

    assert len(statements) == len(tables)
    assert all(text.strip().upper().startswith("SELECT") for text, _ in statements), statements


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
        mod._delete_round_rows(factory, "user-1")
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
        mod._delete_round_rows(factory, "user-1")
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

    mod._delete_round_rows(factory, "user-1")
    assert [kind for kind, _ in events].count("delete") == 4
    assert [kind for kind, _ in events].count("count") == 4


# --------------------------------------------------------------------------
# 6. A thread that does not stop is an error, not a result.


def test_a_thread_still_running_after_join_is_reported(mod):
    import threading

    release = threading.Event()
    blocked = threading.Thread(target=release.wait, name="quota-stuck")
    try:
        with pytest.raises(AssertionError, match="still running after"):
            mod._start_and_join([blocked], timeout=0.05)
    finally:
        release.set()
    blocked.join(timeout=5)
    assert not blocked.is_alive(), "the released thread must actually end"


def test_started_threads_are_all_joined(mod):
    import threading

    done = []
    threads = [threading.Thread(target=done.append, args=(i,), name=f"t{i}") for i in range(3)]
    mod._start_and_join(threads, timeout=10)
    assert sorted(done) == [0, 1, 2]
    assert not any(thread.is_alive() for thread in threads)


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


def test_only_the_expected_refusal_counts_as_denied(mod):
    def refused():
        raise RuntimeError("429 quota_exceeded for this user")

    def unrelated():
        raise ValueError("connection reset")

    assert mod._classify("quota_exceeded", refused) == "denied"
    with pytest.raises(ValueError):
        mod._classify("quota_exceeded", unrelated)


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
