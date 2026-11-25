"""Prove data isolation between tests.

This test verifies that data committed in one test does not appear in subsequent tests,
proving that the test infrastructure properly isolates test data.

For PostgreSQL with limited-permission runtime role, we cannot drop/recreate tables
between tests (no DDL permission). Instead, we rely on transaction rollback.

This test suite proves the isolation mechanism works correctly.

Every node is PostgreSQL-only. It writes to ``_test_sacrificial_``, a table a
migration creates on the PostgreSQL test database and which the SQLite lane
does not have, and it uses ``ON CONFLICT``. Offline the three nodes fail with
"no such table", a statement about the missing table rather than about whether
rollback isolates one test from the next.

The steps are numbered because they depend on each other: step 2 asserts what
step 1 left behind, so this file is only meaningful run in file order.
"""
import pytest
from sqlalchemy import text
import app.database

from tests.postgresql_only import requires_postgresql


# Shared state to track data across tests
_test_data_marker = "isolation_test_marker_999"


@pytest.fixture(autouse=True)
def _postgres_required():
    requires_postgresql(
        "writes to the migration-created _test_sacrificial_ table with "
        "ON CONFLICT, neither of which exists on the SQLite lane"
    )


def test_isolation_step1_insert_data():
    """Step 1: Insert marker data and commit."""
    with next(app.database.get_db()) as db:
        # Insert marker data into sacrificial table
        db.execute(text("""
            INSERT INTO _test_sacrificial_ (id, data)
            VALUES (999999, :marker)
            ON CONFLICT (id) DO UPDATE SET data = :marker
        """), {"marker": _test_data_marker})
        db.commit()
        
        # Verify it's there
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 999999"))
        row = result.fetchone()
        assert row is not None, "Marker data should be visible in step 1"
        assert row[0] == _test_data_marker


def test_isolation_step2_verify_cleanup():
    """Step 2: Verify marker data from step 1 was cleaned up.
    
    If isolation works, the marker data should NOT be visible here.
    If isolation fails, the marker data will still be present.
    """
    with next(app.database.get_db()) as db:
        # Check if marker data exists
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 999999"))
        row = result.fetchone()
        
        # Isolation PASSED: data was cleaned up by transaction rollback
        assert row is None, (
            f"Data isolation FAILED: marker data from step 1 still visible in step 2. "
            f"Expected None, got {row[0] if row else 'N/A'}. "
            f"Transaction rollback between tests is not working."
        )


def test_isolation_step3_cleanup_if_needed():
    """Step 3: Cleanup marker data if it exists (for test suite stability)."""
    with next(app.database.get_db()) as db:
        # Clean up any residual marker data
        db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 999999"))
        db.commit()
        
        # Verify cleanup
        result = db.execute(text("SELECT id FROM _test_sacrificial_ WHERE id = 999999"))
        row = result.fetchone()
        assert row is None, "Marker data should be cleaned up"
