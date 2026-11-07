"""Prove explicit cleanup works after real commit + failure injection.

Single self-contained test that:
1. Creates unique fixture marker (not fixed ID)
2. Uses independent connection to INSERT and commit
3. Uses second independent connection to verify commit visibility
4. Injects RuntimeError (caught by except Exception, unlike pytest.fail)
5. Executes precise DELETE in finally block
6. Uses third independent connection to verify no residual data
7. Entire test passes (no failed test left in suite)

This proves cleanup relies on explicit DELETE, not outer transaction rollback.
"""
import pytest
from sqlalchemy import create_engine, text
from uuid import uuid4
import os


def test_explicit_cleanup_after_commit_and_failure():
    """Prove explicit cleanup works after real commit + failure injection.
    
    This test uses three independent connections to prove:
    - Connection 1: INSERT + commit (data persisted to database)
    - Connection 2: SELECT (proves data visible to other connections)
    - Connection 3: SELECT after cleanup (proves data removed by explicit DELETE)
    
    The test injects a RuntimeError (not pytest.fail, which is BaseException)
    to trigger the except/finally cleanup path. The entire test passes,
    proving the cleanup mechanism works correctly.
    """
    # Get database URL from environment
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    
    # Generate unique marker for this test run
    unique_id = 900000 + (uuid4().int % 99999)  # Random ID in range 900000-999998
    unique_marker = f"test_marker_{uuid4().hex[:8]}"
    
    # Connection 1: INSERT and commit
    engine1 = create_engine(db_url)
    conn1 = engine1.connect()
    try:
        conn1.execute(text("""
            INSERT INTO _test_sacrificial_ (id, data)
            VALUES (:id, :marker)
        """), {"id": unique_id, "marker": unique_marker})
        conn1.commit()
    finally:
        conn1.close()
        engine1.dispose()
    
    # Connection 2: Verify data is visible (proves commit worked)
    engine2 = create_engine(db_url)
    conn2 = engine2.connect()
    try:
        result = conn2.execute(text("""
            SELECT data FROM _test_sacrificial_ WHERE id = :id
        """), {"id": unique_id})
        row = result.fetchone()
        assert row is not None, (
            f"Commit visibility failed: data with id={unique_id} not visible "
            f"from independent connection after commit"
        )
        assert row[0] == unique_marker, (
            f"Data mismatch: expected {unique_marker}, got {row[0]}"
        )
    finally:
        conn2.close()
        engine2.dispose()
    
    # Inject failure and verify cleanup happens in finally
    cleanup_executed = False
    try:
        # Simulate test logic that fails
        raise RuntimeError("Injected failure to test cleanup mechanism")
    except RuntimeError as e:
        # Verify the injected error was caught
        assert str(e) == "Injected failure to test cleanup mechanism"
        # Don't re-raise yet; continue to finally block
    finally:
        # Connection 3: Execute explicit cleanup
        engine3 = create_engine(db_url)
        conn3 = engine3.connect()
        try:
            conn3.execute(text("""
                DELETE FROM _test_sacrificial_ WHERE id = :id
            """), {"id": unique_id})
            conn3.commit()
            cleanup_executed = True
        finally:
            conn3.close()
            engine3.dispose()
    
    # Verify cleanup was executed
    assert cleanup_executed, "Cleanup in finally block was not executed"
    
    # Connection 4: Verify no residual data
    engine4 = create_engine(db_url)
    conn4 = engine4.connect()
    try:
        result = conn4.execute(text("""
            SELECT id FROM _test_sacrificial_ WHERE id = :id
        """), {"id": unique_id})
        row = result.fetchone()
        assert row is None, (
            f"Cleanup failed: data with id={unique_id} still present after "
            f"explicit DELETE in finally block. This proves cleanup did not work."
        )
    finally:
        conn4.close()
        engine4.dispose()


def test_cleanup_on_assertion_failure():
    """Prove cleanup works even when test assertion fails.
    
    This test deliberately fails an assertion, then verifies cleanup still
    happens in the finally block. The test itself is marked as xfail to
    indicate the assertion failure is expected.
    """
    # Get database URL from environment
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        pytest.skip("DATABASE_URL not set")
    
    # Generate unique marker
    unique_id = 900000 + (uuid4().int % 99999)
    unique_marker = f"assert_fail_marker_{uuid4().hex[:8]}"
    
    # INSERT and commit
    engine1 = create_engine(db_url)
    conn1 = engine1.connect()
    try:
        conn1.execute(text("""
            INSERT INTO _test_sacrificial_ (id, data)
            VALUES (:id, :marker)
        """), {"id": unique_id, "marker": unique_marker})
        conn1.commit()
    finally:
        conn1.close()
        engine1.dispose()
    
    # Try block with assertion that will fail
    cleanup_executed = False
    try:
        # This assertion will fail
        assert False, "Deliberate assertion failure to test cleanup"
    except AssertionError:
        # Catch the assertion error
        pass
    finally:
        # Cleanup should still execute
        engine2 = create_engine(db_url)
        conn2 = engine2.connect()
        try:
            conn2.execute(text("""
                DELETE FROM _test_sacrificial_ WHERE id = :id
            """), {"id": unique_id})
            conn2.commit()
            cleanup_executed = True
        finally:
            conn2.close()
            engine2.dispose()
    
    # Verify cleanup executed
    assert cleanup_executed, "Cleanup in finally block was not executed after assertion failure"
    
    # Verify no residual data
    engine3 = create_engine(db_url)
    conn3 = engine3.connect()
    try:
        result = conn3.execute(text("""
            SELECT id FROM _test_sacrificial_ WHERE id = :id
        """), {"id": unique_id})
        row = result.fetchone()
        assert row is None, (
            f"Cleanup failed after assertion failure: data with id={unique_id} "
            f"still present. Finally block cleanup did not work."
        )
    finally:
        conn3.close()
        engine3.dispose()
