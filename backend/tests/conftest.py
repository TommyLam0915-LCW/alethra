import os
import sys

# Opt-in PostgreSQL integration test path:
#   ALETHRA_PG_TEST=1 → use isolated PostgreSQL at port 55432
#   Otherwise → default to SQLite (existing behavior)
_PG_TEST = os.environ.get("ALETHRA_PG_TEST", "").strip() == "1"

if _PG_TEST:
    _PG_URL = "postgresql://tommylam0915@127.0.0.1:55432/alethra_test"
    # Safety guard: abort if target looks non-disposable
    if "prod" in _PG_URL.lower() or "production" in _PG_URL.lower():
        print("FATAL: PostgreSQL test URL contains 'prod' — aborting", file=sys.stderr)
        sys.exit(1)
    if "alethra_test" not in _PG_URL:
        print("FATAL: PostgreSQL test URL must target 'alethra_test' database — aborting", file=sys.stderr)
        sys.exit(1)
    _TEST_DB_PATH = _PG_URL
else:
    # Concurrent sessions in this worktree share backend/test.db, which corrupts
    # parallel pytest runs ("table already exists"). Override the DB file per run:
    #   ALETHRA_TEST_DB=sqlite:///./test-<session>.db
    _TEST_DB_PATH = os.environ.get("ALETHRA_TEST_DB", "sqlite:///./test.db")

os.environ["DATABASE_URL"] = _TEST_DB_PATH

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
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


if _PG_TEST:
    # PostgreSQL path: no SQLite-specific args
    TEST_ENGINE = create_engine(_TEST_DB_PATH)
    # Assert dialect
    if TEST_ENGINE.dialect.name != "postgresql":
        print(f"FATAL: Expected postgresql dialect, got {TEST_ENGINE.dialect.name}", file=sys.stderr)
        sys.exit(1)
    # Verify connectivity and target database
    with TEST_ENGINE.connect() as conn:
        result = conn.execute(text("SELECT current_database()")).scalar()
        if result != "alethra_test":
            print(f"FATAL: Connected to '{result}', expected 'alethra_test'", file=sys.stderr)
            sys.exit(1)
else:
    # SQLite path: existing configuration
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


TestSession = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


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
    if _PG_TEST:
        # PostgreSQL: use CASCADE to handle FK dependencies
        with TEST_ENGINE.connect() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(text(f'DROP TABLE IF EXISTS "{table.name}" CASCADE'))
            conn.commit()
    else:
        # SQLite: use raw SQL to reliably drop tables even when FK cycles prevent
        # Base.metadata.drop_all from sorting them correctly.
        with TEST_ENGINE.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for table in reversed(Base.metadata.sorted_tables):
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table.name}"')
            conn.commit()
    db_mod.engine = _orig_engine


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
