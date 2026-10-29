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
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.models import *


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw):
    return "VARCHAR(36)"


_IS_SQLITE = _TEST_DB_PATH.startswith("sqlite")

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
        with TEST_ENGINE.connect() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table.name}" CASCADE')
            conn.commit()
        # Table drops leave native enum types behind; create_all would then fail
        # on the next test with "type ... already exists".
        with TEST_ENGINE.connect() as conn:
            types = conn.exec_driver_sql(
                "SELECT t.typname FROM pg_type t "
                "JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = current_schema() AND t.typtype = 'e'"
            ).fetchall()
            for (typname,) in types:
                conn.exec_driver_sql(f'DROP TYPE IF EXISTS "{typname}" CASCADE')
            conn.commit()
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
