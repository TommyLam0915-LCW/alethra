import os
from urllib.parse import urlparse

from tests.postgresql_only import is_postgresql_url

# Importing the hook is how it is registered: pytest collects hook
# implementations from this module's namespace. It keeps a failing test's
# report free of the tokens its fixtures are holding (see that module).
from tests.failure_output_hygiene import pytest_runtest_makereport  # noqa: F401

def _safe_db_diagnostic(url: str) -> str:
    """Extract password-free diagnostic info from database URL."""
    try:
        parsed = urlparse(url)
        # Return scheme://user@host:port/database (no password)
        password_masked = parsed._replace(password='***')
        return password_masked.geturl()
    except Exception:
        return "<unparseable URL>"

# === 一、對齊執行基線：unify env vars ===
# ALETHRA_TEST_DB and ALETHRA_PG_TEST_URL name the same PostgreSQL test URL, so
# if both are set and differ there is no single target to run against: refuse
# before any connection. Only a PostgreSQL URL may populate the PostgreSQL-only
# name; see the guard on the alias below.
_env_a = os.environ.get("ALETHRA_TEST_DB")
_env_b = os.environ.get("ALETHRA_PG_TEST_URL")
if _env_a and _env_b and _env_a != _env_b:
    raise RuntimeError(
        f"SAFETY: ALETHRA_TEST_DB and ALETHRA_PG_TEST_URL are both set but differ.\n"
        f"  ALETHRA_TEST_DB    = {_safe_db_diagnostic(_env_a)}\n"
        f"  ALETHRA_PG_TEST_URL = {_safe_db_diagnostic(_env_b)}\n"
        f"Unify to a single value before running tests."
    )
if _env_a and not _env_b and is_postgresql_url(_env_a):
    # Aliased only when the value really is a PostgreSQL URL. Copying
    # ALETHRA_TEST_DB over unconditionally handed the SQLite lane's file path
    # to ALETHRA_PG_TEST_URL, and every reader that asked that name "is
    # PostgreSQL configured?" was told yes -- including the tests that then ran
    # SELECT current_database() against SQLite, and get_test_db_url(), whose
    # whole job is to refuse when no PostgreSQL target was named.
    os.environ["ALETHRA_PG_TEST_URL"] = _env_a
elif _env_b and not _env_a:
    os.environ["ALETHRA_TEST_DB"] = _env_b

def _sqlite_default_test_db_url(env, pid):
    """The database for a run that never named one.

    The default was ``sqlite:///./test.db``, which is relative to the current
    directory -- so every pytest started outside ``scripts/run_tests.sh`` wrote
    into one file inside the shared checkout (measured: ``backend/test.db``,
    3,043,328 bytes at 2026-11-20 03:07:34Z). The replacement is the runner's own
    convention, ``${TMPDIR:-/tmp}/alethra-sqlite-test-<pid>.db``, one file per
    process. A database named by the caller is returned unchanged: this removes
    a silent default, it does not override a decision.
    """
    named = env.get("ALETHRA_TEST_DB")
    if named:
        return named
    tmp = env.get("TMPDIR") or ""
    if not tmp.startswith("/"):
        # A relative TMPDIR would put the file back inside the worktree.
        tmp = "/tmp"
    return f"sqlite:///{tmp}/alethra-sqlite-test-{pid}.db"


_TEST_DB_PATH = _sqlite_default_test_db_url(os.environ, os.getpid())
os.environ["DATABASE_URL"] = _TEST_DB_PATH
# Published rather than kept local: tests/stored_market_data and
# tests/fb17_dataset_provenance re-read ALETHRA_TEST_DB at import time with the
# old ``./test.db`` default of their own. conftest is imported before either, so
# the resolved name becomes the single answer for the whole session.
os.environ["ALETHRA_TEST_DB"] = _TEST_DB_PATH

# === 二、消除會污染證據的入口：開發者的 `backend/.env` 不再重新配置這個進程 ===
# `app/config.py:560` declares `env_file=".env"` — a *relative* path, so
# pydantic-settings resolves it against the process cwd, and both entry points
# put the cwd inside `backend/`: `scripts/run_tests.sh` cds there before pytest,
# and a bare `pytest` under `backend/` starts there too. Measured 2026-11-20 on
# this checkout with only the gitignored local file present:
#
#     Settings()               -> data_provider='twelvedata',
#                                 live_trading_enabled=True,
#                                 research_auth_bypass_enabled=True
#     Settings(_env_file=None) -> 'demo', False, False
#
# The database target is not the exposure (the lane exports `DATABASE_URL`, and
# an environment variable outranks a dotenv value). The exposure is every key
# the lane does *not* export: `app/main.py:13` snapshots `get_settings()` at
# import time and `app/main.py:52` starts the learning scheduler from that
# snapshot during the `client` fixture's startup, so a file nobody reviews could
# arm the cron lanes for a test run — and `DATA_PROVIDER` is what decides
# whether a test may reach a market-data API at all. The dotenv source is
# therefore removed for this process; environment variables keep configuring the
# app exactly as they do in production.
from app import config as _config_module

_DOTENV_WOULD_LOAD = _config_module.Settings.model_config.get("env_file")
_config_module.Settings.model_config["env_file"] = None
# A snapshot cached before the patch would outrank it, so the cache is dropped.
_config_module.get_settings.cache_clear()


def pytest_report_header():
    """Say in the lane's own log which source was removed, not that it is clean."""
    return (
        "developer dotenv: neutralised (env_file "
        f"{_DOTENV_WOULD_LOAD!r} -> None; environment variables still apply)"
    )


import gc
import pytest
from weakref import WeakSet

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.models import *

# CRITICAL: Import app.main at conftest load time, BEFORE any fixtures run.
# This ensures all route modules (e.g., app.api.auth) import the ORIGINAL
# get_db function object. Later monkey-patching by setup_db won't affect
# the function objects already captured by Depends(get_db) at route definition.
# Without this, dependency_overrides[_ORIGINAL_GET_DB] would target a different
# function object than what the routes use, causing the override to silently fail.
from app.main import app as _app


def _safe_dsn(url_str: str) -> str:
    """Return a loggable DSN with password redacted."""
    try:
        u = make_url(url_str)
        if u.password:
            return str(u.set(password="***"))
    except Exception:
        pass
    return "<unparseable DSN>"


def _validate_pg_test_target(url_str: str) -> None:
    """Fail closed: PostgreSQL test URLs must target a disposable database.

    Checks parsed host, port, and database name. Does NOT log passwords.
    """
    u = make_url(url_str)
    if u.drivername not in ("postgresql", "postgresql+psycopg2", "postgresql+pg8000"):
        return  # non-PG URLs are handled elsewhere (e.g. sqlite)

    host = u.host or ""
    if host and host not in ("localhost", "127.0.0.1", "::1", "/tmp"):
        raise RuntimeError(
            f"SAFETY: PostgreSQL test DB must be on localhost, got host={host!r}. "
            f"DSN: {_safe_dsn(url_str)}"
        )

    db_name = u.database or ""
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"SAFETY: PostgreSQL test DB name must contain 'test', got {db_name!r}. "
            f"DSN: {_safe_dsn(url_str)}"
        )

    _PRODUCTION_DB_NAMES = {"ai_investment_lab", "alethra_production", "alethra_prod"}
    if db_name in _PRODUCTION_DB_NAMES:
        raise RuntimeError(
            f"SAFETY: Refusing to use production database {db_name!r} for testing. "
            f"DSN: {_safe_dsn(url_str)}"
        )


_IS_SQLITE = _TEST_DB_PATH.startswith("sqlite")

if not _IS_SQLITE:
    _validate_pg_test_target(_TEST_DB_PATH)
    
    # Integrate comprehensive safety gate for PostgreSQL connections
    # This enforces TEST_MODE, role permissions, and actual database identity
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from test_safety_gate import verify_test_database, TestDatabaseGateError
    
    try:
        _conn_info = verify_test_database(_TEST_DB_PATH)
        print(f"✓ Safety gate passed: database={_conn_info['database']}, user={_conn_info['user']}")
    except TestDatabaseGateError as e:
        raise RuntimeError(
            f"SAFETY: Test database safety gate rejected connection.\n"
            f"Error: {e}\n"
            f"DSN: {_safe_dsn(_TEST_DB_PATH)}\n"
            f"Ensure TEST_MODE=true and DATABASE_URL targets an allowed test database."
        ) from e


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw):
    return "VARCHAR(36)"


if _IS_SQLITE:
    TEST_ENGINE = create_engine(
        _TEST_DB_PATH,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(TEST_ENGINE, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()
else:
    # PostgreSQL-backed disposable run: real connections, real row locks.
    # Integrate safety gate before creating engine - must pass before any DB writes
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from test_safety_gate import verify_test_database, TestDatabaseGateError
    
    try:
        gate_info = verify_test_database(_TEST_DB_PATH)
        print(f"✓ Safety gate passed: {gate_info['database']} on {gate_info['server_addr']}:{gate_info['server_port']}")
    except TestDatabaseGateError as e:
        raise RuntimeError(f"SAFETY GATE FAILED: {e}") from e
    
    TEST_ENGINE = create_engine(_TEST_DB_PATH, pool_pre_ping=True)

    @event.listens_for(TEST_ENGINE, "connect")
    def _set_pg_disposable_timeouts(dbapi_connection, connection_record):
        # Session-level GUCs on the DISPOSABLE test engine's connections only.
        # These are NOT global/production settings (no ALTER DATABASE/SYSTEM):
        # they bound what was previously an unbounded wait, so a leaked
        # idle-in-transaction session self-terminates and any teardown DDL
        # blocked on a lock fails fast and diagnosably instead of hanging for
        # 88+ minutes (the deadlock that aborted the full PG suite).
        cur = dbapi_connection.cursor()
        try:
            cur.execute("SET lock_timeout = '15s'")
            cur.execute("SET statement_timeout = '120s'")
            cur.execute("SET idle_in_transaction_session_timeout = '60s'")
        finally:
            cur.close()


# Every Session handed out by TestSession registers itself here (weakly, so
# normal GC behaviour and QueuePool sizing are unchanged). The autouse teardown
# force-closes whatever is still live BEFORE running destructive DDL, so a
# session leaked idle-in-transaction can never hold a lock that blocks the
# teardown's DROP TABLE ... CASCADE. This fixes the actual leak path rather than
# masking it with engine.dispose() or a pool swap.
_live_sessions = WeakSet()


class _TrackedSession(Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _live_sessions.add(self)


TestSession = sessionmaker(
    autocommit=False, autoflush=False, bind=TEST_ENGINE, class_=_TrackedSession
)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    from app.database import engine as _orig_engine
    import app.database as db_mod
    db_mod.engine = TEST_ENGINE
    # PostgreSQL schema is created by migrations, not create_all.
    # Limited-permission runtime role cannot execute DDL (CREATE TYPE, etc.).
    # Only run create_all for SQLite where migrations don't apply.
    if _IS_SQLITE:
        Base.metadata.create_all(bind=TEST_ENGINE, checkfirst=True)
        yield
    else:
        # PostgreSQL: wrap test in transaction for isolation.
        # All commits (including from API/service code) will be rolled back.
        # This proves data isolation between tests.
        connection = TEST_ENGINE.connect()
        transaction = connection.begin()
        
        # Create a session bound to this connection
        test_session = _TrackedSession(bind=connection)
        
        # Override get_db to return our transactional session
        import app.database as app_db_mod
        original_get_db = app_db_mod.get_db
        
        def transactional_get_db():
            try:
                yield test_session
            finally:
                pass  # Don't close yet; teardown will handle it
        
        app_db_mod.get_db = transactional_get_db
        
        try:
            yield
        finally:
            # Restore original get_db
            app_db_mod.get_db = original_get_db
            
            # Rollback transaction (undoes all commits from this test)
            # This is the key to data isolation
            try:
                transaction.rollback()
            except Exception:
                pass  # Transaction may already be closed
            
            try:
                test_session.close()
            except Exception:
                pass
            
            try:
                connection.close()
            except Exception:
                pass
    
    # Return every connection before destructive DDL. Force-close all still-live
    # TestSession instances (rolling back any open transaction) so a session
    # leaked idle-in-transaction cannot hold a lock that blocks the DROP below.
    # This is the genuine fix for the billing _get_db()/_create_plan leak (a
    # post-commit db.refresh(plan) leaves a SELECT-open transaction holding
    # AccessShareLock on plans, which blocks DROP TABLE subscriptions CASCADE);
    # it is deliberately NOT engine.dispose()/pool-swap masking.
    for _s in list(_live_sessions):
        try:
            _s.rollback()
        except Exception:
            pass
        try:
            _s.close()
        except Exception:
            pass
    # Collect cyclic garbage so any unregistered-but-garbage session also
    # returns its connection before we take AccessExclusiveLocks in the DDL.
    gc.collect()
    if _IS_SQLITE:
        # Use raw SQL to reliably drop tables even when FK cycles prevent
        # Base.metadata.drop_all from sorting them correctly in SQLite.
        with TEST_ENGINE.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for table in reversed(Base.metadata.sorted_tables):
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table.name}"')
            conn.commit()
    else:
        # PostgreSQL: skip teardown DDL. Schema is managed by migrations,
        # and limited-permission runtime role cannot execute DROP TABLE/TYPE.
        # Test data is cleaned by transaction rollback in TestSession.close().
        pass
    db_mod.engine = _orig_engine


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Give every test a clean request budget.

    The per-API-key limiter is a process-wide in-memory store, so a test that
    spends one key's budget would otherwise make an unrelated later test see 429.
    """
    from app.auth.rate_limit import reset_default_limiter

    reset_default_limiter()
    yield
    reset_default_limiter()


# Import the ORIGINAL get_db at module load time, before any fixture replaces it.
# This ensures dependency_overrides targets the correct function.
from app.database import get_db as _ORIGINAL_GET_DB


@pytest.fixture()
def client():
    from app.main import app

    # Save existing overrides before modifying
    saved_overrides = app.dependency_overrides.copy()
    try:
        app.dependency_overrides[_ORIGINAL_GET_DB] = override_get_db
        with TestClient(app) as c:
            yield c
    finally:
        # Restore original state, not just clear
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)


class SecretHeaders(dict):
    """A header mapping whose ``repr`` cannot hand out the credential inside it.

    pytest prints the value of every fixture in a failing test's header block, and
    the suite writes those failures into ``<run dir>/pytest.log``. A plain dict
    here therefore put a live ``Bearer`` token into the lane's own log on every
    assertion failure anywhere in the suite — which is how a credential outlives
    the run that issued it, and why this file cannot be shared as evidence.

    The mapping is still the headers: ``client.get(url, headers=h)``,
    ``h["Authorization"]``, ``{**h, "X": "y"}`` and ``dict(h)`` all behave as
    before. Only the repr is withheld.
    """

    def __repr__(self):
        return "<SecretHeaders keys=%s token-value-redacted>" % (
            ",".join(sorted(self)) or "-",
        )

    __str__ = __repr__


@pytest.fixture()
def auth_headers(client):
    resp = client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": "test@alethra.io",
        "password": "testpass123",
        "display_name": "Test User",
        "user_type": "individual",
    })
    if resp.status_code == 400:
        pass
    login = client.post("/api/auth/login", json={
        "email": "test@alethra.io",
        "password": "testpass123",
    })
    token = login.json()["access_token"]
    return SecretHeaders({"Authorization": f"Bearer {token}"})


@pytest.fixture()
def admin_headers(client):
    """Interactive admin identity for server-side permission checks.

    There is no product endpoint that promotes a user to admin, so the test
    harness seeds the flag directly on the disposable test database.
    """
    from app.auth.jwt import create_access_token, get_password_hash
    from app.models.user import User

    db = TestSession()
    try:
        admin = User(
            email="admin-test@alethra.io",
            password_hash=get_password_hash("adminpass123"),
            display_name="Admin Test",
            is_admin=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        token = create_access_token(data={"sub": str(admin.id)})
    finally:
        db.close()
    return SecretHeaders({"Authorization": f"Bearer {token}"})
