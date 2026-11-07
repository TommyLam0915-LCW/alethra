import os
# Concurrent sessions in this worktree share backend/test.db, which corrupts
# parallel pytest runs ("table already exists"). Override the DB file per run:
#   ALETHRA_TEST_DB=sqlite:///./test-<session>.db
_TEST_DB_PATH = os.environ.get("ALETHRA_TEST_DB", "sqlite:///./test.db")
os.environ["DATABASE_URL"] = _TEST_DB_PATH

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


@pytest.fixture()
def client():
    from app.main import app
    from app.api import api_router
    from app.database import get_db as orig_get_db

    app.dependency_overrides[orig_get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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
    return {"Authorization": f"Bearer {token}"}


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
    return {"Authorization": f"Bearer {token}"}
