"""Prove limited-permission runtime role cannot execute DDL.

This test demonstrates that the safety gate enforces privilege separation:
- alethra_test_runtime can execute DML (SELECT, INSERT, UPDATE, DELETE)
- alethra_test_runtime cannot execute DDL (CREATE, DROP, ALTER)
"""
import os
import pytest
from sqlalchemy import text
from app.database import get_db


def test_limited_role_cannot_create_table():
    """Prove limited-permission role cannot execute CREATE TABLE."""
    with next(get_db()) as db:
        with pytest.raises(Exception) as exc_info:
            db.execute(text("""
                CREATE TABLE test_privilege_violation (
                    id SERIAL PRIMARY KEY,
                    data TEXT
                )
            """))
        # PostgreSQL error code 42501 = insufficient_privilege
        assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value)


def test_limited_role_cannot_drop_table():
    """Prove limited-permission role cannot execute DROP TABLE."""
    with next(get_db()) as db:
        with pytest.raises(Exception) as exc_info:
            db.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        # PostgreSQL error: "must be owner of table" or "permission denied"
        error_msg = str(exc_info.value).lower()
        assert "must be owner" in error_msg or "permission denied" in error_msg or "42501" in error_msg


def test_limited_role_cannot_create_type():
    """Prove limited-permission role cannot execute CREATE TYPE."""
    with next(get_db()) as db:
        with pytest.raises(Exception) as exc_info:
            db.execute(text("""
                CREATE TYPE test_enum_type AS ENUM ('a', 'b', 'c')
            """))
        # PostgreSQL error code 42501 = insufficient_privilege
        assert "permission denied" in str(exc_info.value).lower() or "42501" in str(exc_info.value)


def test_limited_role_can_select():
    """Prove limited-permission role can execute SELECT (DML)."""
    with next(get_db()) as db:
        # This should succeed
        result = db.execute(text("SELECT current_user, current_database()"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "alethra_test_runtime"
        assert row[1] == "ai_investment_lab_test"
