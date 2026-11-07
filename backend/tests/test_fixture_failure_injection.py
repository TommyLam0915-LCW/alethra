"""Test fixture cleanup with injected failures.

Proves that cleanup works even when:
1. User commit succeeds but refresh fails
2. Test body raises exception
3. Cleanup DELETE fails
4. Both body and cleanup fail

Each test uses unique email/marker to avoid conflicts.
"""
import pytest
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import text
from tests.api_test_fixtures import api_test_fixtures
from app.database import SessionLocal


def _count_user(user_id: str) -> int:
    """Count user in database using independent connection."""
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM users WHERE id = :id"),
            {"id": user_id}
        )
        return result.scalar()
    finally:
        db.close()


def _count_run(run_id: str) -> int:
    """Count run in database using independent connection."""
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM strategy_study_runs WHERE id = :id"),
            {"id": run_id}
        )
        return result.scalar()
    finally:
        db.close()


def test_user_commit_succeeds_refresh_fails():
    """Prove cleanup runs even if refresh() fails after commit."""
    test_email = f"refresh_fail_{uuid4().hex[:8]}@example.com"
    
    # Mock refresh to raise exception
    with patch('sqlalchemy.orm.Session.refresh') as mock_refresh:
        mock_refresh.side_effect = RuntimeError("Simulated refresh failure")
        
        with pytest.raises(RuntimeError, match="Simulated refresh failure"):
            with api_test_fixtures(test_email) as (auth, user_id, run_id):
                # Should never reach here
                pytest.fail("Should have raised during fixture creation")
    
    # Verify user was cleaned up (even though we don't have the ID)
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :email"),
            {"email": test_email}
        )
        count = result.scalar()
        assert count == 0, f"User with email {test_email} was not cleaned up"
    finally:
        db.close()


def test_body_raises_exception_cleanup_still_runs():
    """Prove cleanup runs even when test body raises exception."""
    test_email = f"body_fail_{uuid4().hex[:8]}@example.com"
    captured_user_id = None
    captured_run_id = None
    
    with pytest.raises(ValueError, match="Test body failure"):
        with api_test_fixtures(test_email) as (auth, user_id, run_id):
            captured_user_id = user_id
            captured_run_id = run_id
            raise ValueError("Test body failure")
    
    # Verify cleanup ran
    assert captured_user_id is not None
    assert captured_run_id is not None
    assert _count_user(captured_user_id) == 0, f"User {captured_user_id} was not cleaned up"
    assert _count_run(captured_run_id) == 0, f"Run {captured_run_id} was not cleaned up"


def test_cleanup_delete_fails_test_fails():
    """Prove test fails when cleanup DELETE fails."""
    test_email = f"cleanup_fail_{uuid4().hex[:8]}@example.com"
    captured_user_id = None
    
    # Mock _cleanup_user_data to fail
    with patch('tests.api_test_fixtures._cleanup_user_data') as mock_cleanup:
        mock_cleanup.side_effect = RuntimeError("Simulated cleanup failure")
        
        with pytest.raises(RuntimeError, match="Cleanup failed"):
            with api_test_fixtures(test_email) as (auth, user_id, run_id):
                captured_user_id = user_id
                # Test body succeeds
        
        # Verify cleanup was attempted
        assert mock_cleanup.called
    
    # User still exists because cleanup failed (this is expected)
    # In real scenario, this would indicate a serious problem
    if captured_user_id:
        assert _count_user(captured_user_id) == 1, "User should still exist after cleanup failure"
        
        # Manual cleanup for this test
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": captured_user_id})
            db.commit()
        finally:
            db.close()


def test_both_body_and_cleanup_fail_both_visible():
    """Prove both errors are visible when body and cleanup both fail."""
    test_email = f"both_fail_{uuid4().hex[:8]}@example.com"
    
    # Mock cleanup to fail
    with patch('tests.api_test_fixtures._cleanup_user_data') as mock_cleanup:
        cleanup_error = RuntimeError("Simulated cleanup failure")
        mock_cleanup.side_effect = cleanup_error
        
        body_error = ValueError("Test body failure")
        
        # Should raise ExceptionGroup containing both errors
        with pytest.raises(ExceptionGroup) as exc_info:
            with api_test_fixtures(test_email) as (auth, user_id, run_id):
                raise body_error
        
        # Verify both errors are in the ExceptionGroup
        exceptions = exc_info.value.exceptions
        assert len(exceptions) == 2, f"Expected 2 exceptions, got {len(exceptions)}"
        assert body_error in exceptions, "Body error not in ExceptionGroup"
        assert cleanup_error in exceptions, "Cleanup error not in ExceptionGroup"
        
        # Verify cleanup was attempted
        assert mock_cleanup.called
    
    # Manual cleanup
    db = SessionLocal()
    try:
        db.execute(
            text("DELETE FROM users WHERE email = :email"),
            {"email": test_email}
        )
        db.commit()
    finally:
        db.close()


# BLOCKED: Real PostgreSQL commit failure during cleanup
# Reason: Cannot intercept Session.commit() during cleanup phase without
# complex mocking that may interfere with fixture creation.
# Alternative: test_cleanup_delete_fails_test_fails mocks the helper function,
# which is clearly labeled as "simulated" not "real DB failure".
# To test real DB failure, would need to:
# 1. Create fixture
# 2. Verify exists
# 3. Corrupt DB connection
# 4. Attempt cleanup
# 5. Verify exception
# This is beyond current test infrastructure scope.
#
# Owner guidance: "若困難，明確列 blocked，不能聲稱已覆蓋"
# Status: BLOCKED — documented, not implemented


@pytest.mark.skip(reason="BLOCKED: Cannot intercept cleanup commit without complex mocking")
def test_real_db_commit_failure_during_cleanup():
    """BLOCKED — see comment above.
    
    This test would prove real PostgreSQL commit failure during cleanup,
    but requires infrastructure beyond current scope.
    """
    pass
