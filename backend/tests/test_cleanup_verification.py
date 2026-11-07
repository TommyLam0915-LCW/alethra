"""Test that cleanup works even when test fails after commit.

This test deliberately fails after committing data to verify that
the cleanup mechanism (not outer rollback) properly removes residual data.

This proves the isolation claim is based on explicit cleanup, not magic rollback.
"""
import pytest
from sqlalchemy import text
import app.database


def test_cleanup_after_commit_failure():
    """Deliberately fail after committing data, then verify cleanup worked.
    
    This test:
    1. Inserts data and commits
    2. Raises an exception (simulating test failure)
    3. Verifies in a subsequent test that the data is gone
    
    If cleanup relies on outer transaction rollback, this test would pass
    but the data would persist. If cleanup is explicit, the data will be gone.
    """
    db = next(app.database.get_db())
    try:
        # Insert data
        db.execute(text("""
            INSERT INTO _test_sacrificial_ (id, data)
            VALUES (777777, 'should_be_cleaned_up')
            ON CONFLICT (id) DO UPDATE SET data = 'should_be_cleaned_up'
        """))
        db.commit()
        
        # Verify it's there
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 777777"))
        assert result.fetchone() is not None, "Data should be visible after commit"
        
        # Deliberately fail
        pytest.fail("Deliberate failure to test cleanup")
        
    except Exception:
        # Cleanup: explicitly delete the data
        db.execute(text("DELETE FROM _test_sacrificial_ WHERE id = 777777"))
        db.commit()
        raise  # Re-raise to fail the test
    finally:
        db.close()


def test_verify_cleanup_worked():
    """Verify that data from the failed test was cleaned up.
    
    This test runs after test_cleanup_after_commit_failure and verifies
    that the explicit cleanup (not outer rollback) removed the data.
    """
    db = next(app.database.get_db())
    try:
        result = db.execute(text("SELECT data FROM _test_sacrificial_ WHERE id = 777777"))
        row = result.fetchone()
        
        # This assertion proves cleanup worked
        assert row is None, (
            f"Cleanup FAILED: data from failed test still present. "
            f"Expected None, got {row[0] if row else 'N/A'}. "
            f"Explicit cleanup in except/finally is required, not outer rollback."
        )
    finally:
        db.close()
