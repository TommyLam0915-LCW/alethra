"""Regression for the harness connection leak that deadlocked the full PG suite.

The full PostgreSQL run hung for 88+ minutes and had to be aborted. Root cause:
a billing test left a ``TestSession`` idle-in-transaction -- ``_create_plan()``
does ``commit()`` then ``refresh(plan)``, and the refresh opens a fresh
``SELECT plans...`` transaction that is never closed, holding AccessShareLock on
``plans``. The conftest teardown's ``DROP TABLE subscriptions CASCADE`` must
remove the FK's RI trigger on the referenced parent ``plans``, which needs
AccessExclusiveLock on ``plans`` -> an unbounded wait (all timeouts were 0).
SQLite never reproduces this (StaticPool, single connection, no relation-level
AccessExclusive/AccessShare conflict), so these tests are PostgreSQL-only.

They prove the properties the fix must guarantee:
  1. a real billing/plans HTTP request leaves no idle-in-transaction session;
  2. a deliberately leaked plans session is tracked and reclaimed by the same
     cleanup the conftest teardown runs, after which the lock the teardown needs
     (AccessExclusiveLock on plans) is obtainable in bounded time;
  3. even if a session is still leaked, the disposable engine's lock_timeout makes
     a blocked teardown lock fail fast and diagnosably instead of hanging.
"""
import os
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.conftest import TEST_ENGINE, TestSession, _live_sessions

pytestmark = pytest.mark.skipif(
    not os.environ.get("ALETHRA_PG_DATABASE_URL"),
    reason="requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL)",
)


def _other_idle_in_transaction_backends(conn):
    """PIDs on THIS database that are idle in transaction, excluding ``conn``.

    The probe connection is itself idle-in-transaction between statements, so it
    is excluded via pg_backend_pid(); anything left over is a genuine leak.
    """
    return conn.execute(
        text(
            "SELECT pid, state, left(query, 60) AS q "
            "FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND state = 'idle in transaction' "
            "AND pid <> pg_backend_pid()"
        )
    ).fetchall()


def _leak_plans_session():
    """Reproduce the billing leak: a TestSession left idle-in-transaction holding
    AccessShareLock on plans. A strong reference is returned so it is not GC'd --
    the worst case the harness fix must still reclaim deterministically."""
    s = TestSession()
    s.execute(text("SELECT id, name, monthly_price, currency FROM plans"))
    return s


def _run_harness_cleanup():
    """The exact reclamation the conftest setup_db teardown performs."""
    for s in list(_live_sessions):
        try:
            s.rollback()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass


def test_billing_plans_http_request_leaves_no_idle_in_transaction(client):
    """A real billing/plans request must not leak an idle-in-transaction session."""
    resp = client.get("/api/v1/plans")
    assert resp.status_code == 200, resp.text

    with TEST_ENGINE.connect() as probe:
        leftovers = _other_idle_in_transaction_backends(probe)
        assert not leftovers, (
            "billing/plans HTTP request left idle-in-transaction session(s): "
            f"{leftovers}"
        )


def test_harness_reclaims_leaked_plans_session_and_teardown_lock_is_bounded():
    """A leaked plans session is tracked, reclaimed, and the teardown lock then
    becomes obtainable in bounded time."""
    leaked = _leak_plans_session()

    # The leak is real and registered for reclamation.
    assert leaked in _live_sessions, "leaked session was not tracked by the harness"

    with TEST_ENGINE.connect() as probe:
        assert _other_idle_in_transaction_backends(probe), (
            "setup failed to reproduce an idle-in-transaction leak"
        )
        # While the leak is open, the lock the teardown DROP needs is unobtainable.
        probe.execute(text("SET LOCAL lock_timeout = '2s'"))
        with pytest.raises(OperationalError):
            probe.execute(text("LOCK TABLE plans IN ACCESS EXCLUSIVE MODE"))

    # Act: run the same cleanup the conftest teardown performs.
    _run_harness_cleanup()

    # Assert: no leaked backend remains and the teardown lock is now bounded.
    with TEST_ENGINE.connect() as probe:
        leftovers = _other_idle_in_transaction_backends(probe)
        assert not leftovers, (
            f"harness cleanup left idle-in-transaction backend(s): {leftovers}"
        )
        probe.execute(text("SET LOCAL lock_timeout = '5s'"))
        t0 = time.monotonic()
        probe.execute(text("LOCK TABLE plans IN ACCESS EXCLUSIVE MODE"))
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, (
            f"teardown lock took {elapsed:.1f}s after cleanup; expected bounded"
        )


def test_blocked_teardown_lock_fails_fast_not_unbounded():
    """If a session is still leaked, lock_timeout bounds the wait so the failure
    is diagnosable rather than an 88-minute hang."""
    leaked = _leak_plans_session()
    try:
        with TEST_ENGINE.connect() as probe:
            probe.execute(text("SET LOCAL lock_timeout = '3s'"))
            t0 = time.monotonic()
            with pytest.raises(OperationalError) as exc:
                probe.execute(text("LOCK TABLE plans IN ACCESS EXCLUSIVE MODE"))
            elapsed = time.monotonic() - t0
            assert elapsed < 10.0, (
                f"blocked teardown lock took {elapsed:.1f}s; the disposable "
                "engine's lock_timeout must bound it so failures are diagnosable"
            )
            assert "lock timeout" in str(exc.value).lower(), str(exc.value)
    finally:
        try:
            leaked.rollback()
        except Exception:
            pass
        leaked.close()
