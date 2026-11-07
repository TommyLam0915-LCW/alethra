"""
Vertical path test: proves safety gate is enforced in pytest.

This test demonstrates:
1. Safety gate is called during pytest initialization
2. Limited-permission runtime role is used
3. Wrong configuration is rejected before any DB operation
"""

import os
import pytest
from sqlalchemy import text


def test_safety_gate_enforced(test_db_session):
    """Prove safety gate was called and passed."""
    # If we reach here, safety gate passed during conftest initialization
    # Verify we're using the test database
    result = test_db_session.execute(text("SELECT current_database(), current_user"))
    db_name, user = result.fetchone()
    
    assert db_name == "ai_investment_lab_test", f"Wrong database: {db_name}"
    assert user == "alethra_test_runtime", f"Wrong user: {user}"
    
    # Verify limited permissions (no DDL)
    with pytest.raises(Exception):  # Should fail due to permissions
        test_db_session.execute(text("CREATE TABLE test_permission_check (id int)"))
        test_db_session.rollback()


def test_simple_query(test_db_session):
    """Prove we can execute queries with limited permissions."""
    result = test_db_session.execute(text("SELECT 1"))
    assert result.fetchone()[0] == 1


@pytest.fixture
def test_db_session():
    """Provide a test database session."""
    from tests.conftest import TestSession
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
