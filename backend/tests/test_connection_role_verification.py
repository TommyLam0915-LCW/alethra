"""Verify all database connection paths use the approved runtime role.

This test proves that:
1. TestSession uses alethra_test_runtime
2. app.database.SessionLocal uses alethra_test_runtime
3. API request context (get_db) uses alethra_test_runtime
4. All connections target ai_investment_lab_test database

This ensures no connection path accidentally uses postgres or targets production.
"""
import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient

from app.database import SessionLocal, get_db
from app.main import app
from tests.conftest import TestSession


def test_testsession_uses_runtime_role():
    """Verify TestSession connects as alethra_test_runtime."""
    db = TestSession()
    try:
        result = db.execute(text("SELECT current_user, current_database()"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "alethra_test_runtime", (
            f"TestSession should use alethra_test_runtime, got {row[0]}"
        )
        assert row[1] == "ai_investment_lab_test", (
            f"TestSession should target ai_investment_lab_test, got {row[1]}"
        )
    finally:
        db.close()


def test_sessionlocal_uses_runtime_role():
    """Verify app.database.SessionLocal connects as alethra_test_runtime."""
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT current_user, current_database()"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "alethra_test_runtime", (
            f"SessionLocal should use alethra_test_runtime, got {row[0]}"
        )
        assert row[1] == "ai_investment_lab_test", (
            f"SessionLocal should target ai_investment_lab_test, got {row[1]}"
        )
    finally:
        db.close()


def test_api_get_db_uses_runtime_role():
    """Verify API get_db() context uses alethra_test_runtime."""
    with next(get_db()) as db:
        result = db.execute(text("SELECT current_user, current_database()"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "alethra_test_runtime", (
            f"get_db() should use alethra_test_runtime, got {row[0]}"
        )
        assert row[1] == "ai_investment_lab_test", (
            f"get_db() should target ai_investment_lab_test, got {row[1]}"
        )


def test_http_request_uses_runtime_role():
    """Verify HTTP API request context uses alethra_test_runtime."""
    # Create a test endpoint that returns the current database user
    @app.get("/test/db-user")
    def test_db_user():
        with next(get_db()) as db:
            result = db.execute(text("SELECT current_user, current_database()"))
            row = result.fetchone()
            return {"user": row[0], "database": row[1]}
    
    try:
        client = TestClient(app)
        response = client.get("/test/db-user")
        
        assert response.status_code == 200
        data = response.json()
        assert data["user"] == "alethra_test_runtime", (
            f"HTTP request should use alethra_test_runtime, got {data['user']}"
        )
        assert data["database"] == "ai_investment_lab_test", (
            f"HTTP request should target ai_investment_lab_test, got {data['database']}"
        )
    finally:
        # Clean up the test endpoint
        app.router.routes = [r for r in app.router.routes if not (hasattr(r, 'path') and r.path == "/test/db-user")]


def test_all_sessions_consistent():
    """Verify all session types report the same user and database."""
    results = {}
    
    # TestSession
    db1 = TestSession()
    try:
        r1 = db1.execute(text("SELECT current_user, current_database()")).fetchone()
        results["TestSession"] = (r1[0], r1[1])
    finally:
        db1.close()
    
    # SessionLocal
    db2 = SessionLocal()
    try:
        r2 = db2.execute(text("SELECT current_user, current_database()")).fetchone()
        results["SessionLocal"] = (r2[0], r2[1])
    finally:
        db2.close()
    
    # get_db
    with next(get_db()) as db3:
        r3 = db3.execute(text("SELECT current_user, current_database()")).fetchone()
        results["get_db"] = (r3[0], r3[1])
    
    # All should be identical
    assert len(set(results.values())) == 1, (
        f"All session types should report the same user/database, got {results}"
    )
    
    # And should be the runtime role
    user, database = list(results.values())[0]
    assert user == "alethra_test_runtime"
    assert database == "ai_investment_lab_test"
