"""Prove limited-permission runtime role privilege enforcement.

This test demonstrates that the safety gate enforces privilege separation:
- alethra_test_runtime can execute DML (SELECT, INSERT, UPDATE, DELETE) on permitted tables
- alethra_test_runtime cannot execute DDL (CREATE, DROP, ALTER) on tables owned by postgres

All DDL rejection tests use the sacrificial table _test_sacrificial_ (owned by postgres),
not core tables like users. All tests assert specific SQLSTATE 42501 (insufficient_privilege).
"""
import os
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from app.database import get_db


@pytest.fixture
def fixture_data():
    """Create and cleanup dedicated fixture data for DML tests."""
    with next(get_db()) as db:
        # Insert test data into sacrificial table (runtime role can DML on it)
        db.execute(text("""
            INSERT INTO _test_sacrificial_ (id, data) 
            VALUES (999, 'fixture_data')
            ON CONFLICT (id) DO UPDATE SET data = 'fixture_data'
        """))
        db.commit()
        
        yield 999
        
        # Cleanup: delete the test data
        db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 999"))
        db.commit()


def test_limited_role_cannot_drop_table(fixture_data):
    """Prove limited-permission role cannot DROP TABLE on postgres-owned table.
    
    Uses sacrificial table _test_sacrificial_ (owned by postgres), not core tables.
    Asserts SQLSTATE 42501 (insufficient_privilege) specifically.
    """
    with next(get_db()) as db:
        try:
            db.execute(text("DROP TABLE IF EXISTS _test_sacrificial_ CASCADE"))
            db.commit()
            pytest.fail("Expected ProgrammingError with SQLSTATE 42501, but DROP succeeded")
        except ProgrammingError as e:
            # SQLSTATE 42501 = insufficient_privilege
            # SQLAlchemy wraps psycopg2 exceptions; check orig.pgcode
            assert hasattr(e, 'orig'), "ProgrammingError should have orig attribute"
            assert e.orig.pgcode == '42501', f"Expected SQLSTATE 42501, got {e.orig.pgcode}"
            db.rollback()  # Clear aborted transaction state
        except Exception as e:
            db.rollback()
            pytest.fail(f"Expected ProgrammingError with SQLSTATE 42501, got {type(e).__name__}: {e}")


def test_limited_role_cannot_alter_table(fixture_data):
    """Prove limited-permission role cannot ALTER TABLE on postgres-owned table.
    
    Uses sacrificial table _test_sacrificial_ (owned by postgres).
    Asserts SQLSTATE 42501 (insufficient_privilege) specifically.
    """
    with next(get_db()) as db:
        try:
            db.execute(text("ALTER TABLE _test_sacrificial_ ADD COLUMN test_col TEXT"))
            db.commit()
            pytest.fail("Expected ProgrammingError with SQLSTATE 42501, but ALTER succeeded")
        except ProgrammingError as e:
            # SQLSTATE 42501 = insufficient_privilege
            # SQLAlchemy wraps psycopg2 exceptions; check orig.pgcode
            assert hasattr(e, 'orig'), "ProgrammingError should have orig attribute"
            assert e.orig.pgcode == '42501', f"Expected SQLSTATE 42501, got {e.orig.pgcode}"
            db.rollback()  # Clear aborted transaction state
        except Exception as e:
            db.rollback()
            pytest.fail(f"Expected ProgrammingError with SQLSTATE 42501, got {type(e).__name__}: {e}")


def test_limited_role_cannot_create_table():
    """Prove limited-permission role cannot CREATE TABLE.
    
    Asserts SQLSTATE 42501 (insufficient_privilege) specifically.
    """
    with next(get_db()) as db:
        try:
            db.execute(text("""
                CREATE TABLE test_privilege_violation (
                    id SERIAL PRIMARY KEY,
                    data TEXT
                )
            """))
            db.commit()
            pytest.fail("Expected ProgrammingError with SQLSTATE 42501, but CREATE succeeded")
        except ProgrammingError as e:
            # SQLSTATE 42501 = insufficient_privilege
            # SQLAlchemy wraps psycopg2 exceptions; check orig.pgcode
            assert hasattr(e, 'orig'), "ProgrammingError should have orig attribute"
            assert e.orig.pgcode == '42501', f"Expected SQLSTATE 42501, got {e.orig.pgcode}"
            db.rollback()  # Clear aborted transaction state
        except Exception as e:
            db.rollback()
            pytest.fail(f"Expected ProgrammingError with SQLSTATE 42501, got {type(e).__name__}: {e}")


def test_limited_role_can_select(fixture_data):
    """Prove limited-permission role can execute SELECT (DML).
    
    Verifies current_user is alethra_test_runtime and can read fixture data.
    """
    with next(get_db()) as db:
        # Verify current user
        result = db.execute(text("SELECT current_user, current_database()"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "alethra_test_runtime", f"Expected alethra_test_runtime, got {row[0]}"
        assert row[1] == "ai_investment_lab_test", f"Expected ai_investment_lab_test, got {row[1]}"
        
        # Verify can read fixture data
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 999"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "fixture_data", f"Expected 'fixture_data', got {row[0]}"


def test_limited_role_can_insert(fixture_data):
    """Prove limited-permission role can execute INSERT (DML).
    
    Uses sacrificial table with dedicated fixture data, not core tables.
    """
    with next(get_db()) as db:
        try:
            # Insert new test row
            db.execute(text("""
                INSERT INTO _test_sacrificial_ (id, data) 
                VALUES (998, 'insert_test')
            """))
            db.commit()
            
            # Verify insert succeeded
            result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 998"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "insert_test", f"Expected 'insert_test', got {row[0]}"
            
        finally:
            # Cleanup: always delete the test row
            db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 998"))
            db.commit()


def test_limited_role_can_update(fixture_data):
    """Prove limited-permission role can execute UPDATE (DML).
    
    Uses sacrificial table with dedicated fixture data, not core tables.
    """
    with next(get_db()) as db:
        # Update fixture data
        db.execute(text("""
            UPDATE _test_sacrificial_ 
            SET data = 'updated_data' 
            WHERE id = 999
        """))
        db.commit()
        
        # Verify update succeeded
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 999"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == "updated_data", f"Expected 'updated_data', got {row[0]}"


def test_limited_role_can_delete(fixture_data):
    """Prove limited-permission role can execute DELETE (DML).
    
    Uses sacrificial table with dedicated fixture data, not core tables.
    Creates a new row, deletes it, verifies deletion.
    """
    with next(get_db()) as db:
        try:
            # Create a row to delete
            db.execute(text("""
                INSERT INTO _test_sacrificial_ (id, data) 
                VALUES (997, 'delete_test')
            """))
            db.commit()
            
            # Delete the row
            db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 997"))
            db.commit()
            
            # Verify delete succeeded
            result = db.execute(text("SELECT id FROM _test_sacrificial_ WHERE id = 997"))
            row = result.fetchone()
            assert row is None, f"Expected row to be deleted, but found {row}"
            
        finally:
            # Cleanup: ensure no residual data
            db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 997"))
            db.commit()
