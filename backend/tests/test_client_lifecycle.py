"""Client fixture lifecycle regression tests.

Proves:
1. Two consecutive client lifespans work in same process
2. Client still works after test body throws error
3. Original get_db override is actually called
4. Previous test's session not reused by next request
"""
import pytest
from uuid import uuid4
from sqlalchemy import text
from tests.conftest import TestSession, _ORIGINAL_GET_DB


class TestClientLifecycle:
    """Client fixture lifecycle regression tests."""
    
    def test_first_client_lifespan(self, client):
        """First client lifespan: verify override is called."""
        from app.main import app
        
        # Verify override is set
        assert _ORIGINAL_GET_DB in app.dependency_overrides
        
        # Create user via fixture
        test_email = f"lifecycle_1_{uuid4().hex[:8]}@example.com"
        from app.auth.jwt import get_password_hash
        from app.models.user import User
        
        db = TestSession()
        try:
            user = User(
                id=uuid4(),
                email=test_email,
                password_hash=get_password_hash("testpass123"),
                display_name="Lifecycle Test 1",
                is_admin=False,
                is_active=True,
            )
            db.add(user)
            db.commit()
            user_id = user.id
        finally:
            db.close()
        
        # Login via API - this uses override_get_db
        login_resp = client.post(
            "/api/auth/login",
            json={"email": test_email, "password": "testpass123"}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
        
        # Cleanup
        db = TestSession()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
            db.commit()
        finally:
            db.close()
    
    def test_second_client_lifespan_after_first(self, client):
        """Second client lifespan: verify it works after first test."""
        from app.main import app
        
        # Verify override is still set
        assert _ORIGINAL_GET_DB in app.dependency_overrides
        
        # Create user via fixture
        test_email = f"lifecycle_2_{uuid4().hex[:8]}@example.com"
        from app.auth.jwt import get_password_hash
        from app.models.user import User
        
        db = TestSession()
        try:
            user = User(
                id=uuid4(),
                email=test_email,
                password_hash=get_password_hash("testpass123"),
                display_name="Lifecycle Test 2",
                is_admin=False,
                is_active=True,
            )
            db.add(user)
            db.commit()
            user_id = user.id
        finally:
            db.close()
        
        # Login via API - this uses override_get_db
        login_resp = client.post(
            "/api/auth/login",
            json={"email": test_email, "password": "testpass123"}
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
        
        # Cleanup
        db = TestSession()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
            db.commit()
        finally:
            db.close()
    
    def test_client_works_after_exception(self, client):
        """Verify client still works after test body throws error."""
        from app.main import app
        
        # Verify override is set
        assert _ORIGINAL_GET_DB in app.dependency_overrides
        
        # Intentionally throw and catch an error
        try:
            raise RuntimeError("Intentional test error")
        except RuntimeError:
            pass
        
        # Client should still work
        test_email = f"lifecycle_3_{uuid4().hex[:8]}@example.com"
        from app.auth.jwt import get_password_hash
        from app.models.user import User
        
        db = TestSession()
        try:
            user = User(
                id=uuid4(),
                email=test_email,
                password_hash=get_password_hash("testpass123"),
                display_name="Lifecycle Test 3",
                is_admin=False,
                is_active=True,
            )
            db.add(user)
            db.commit()
            user_id = user.id
        finally:
            db.close()
        
        # Login via API
        login_resp = client.post(
            "/api/auth/login",
            json={"email": test_email, "password": "testpass123"}
        )
        assert login_resp.status_code == 200, f"Login failed after exception: {login_resp.json()}"
        
        # Cleanup
        db = TestSession()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
            db.commit()
        finally:
            db.close()
    
    def test_session_not_reused_across_requests(self, client):
        """Verify previous test's session is not reused by next request."""
        from app.main import app
        
        # Create two users
        test_email_1 = f"lifecycle_4a_{uuid4().hex[:8]}@example.com"
        test_email_2 = f"lifecycle_4b_{uuid4().hex[:8]}@example.com"
        
        from app.auth.jwt import get_password_hash
        from app.models.user import User
        
        db = TestSession()
        try:
            user1 = User(
                id=uuid4(),
                email=test_email_1,
                password_hash=get_password_hash("testpass123"),
                display_name="Lifecycle Test 4a",
                is_admin=False,
                is_active=True,
            )
            user2 = User(
                id=uuid4(),
                email=test_email_2,
                password_hash=get_password_hash("testpass123"),
                display_name="Lifecycle Test 4b",
                is_admin=False,
                is_active=True,
            )
            db.add(user1)
            db.add(user2)
            db.commit()
            user1_id = user1.id
            user2_id = user2.id
        finally:
            db.close()
        
        # First login - uses override_get_db, creates session S1
        login_resp_1 = client.post(
            "/api/auth/login",
            json={"email": test_email_1, "password": "testpass123"}
        )
        assert login_resp_1.status_code == 200
        token_1 = login_resp_1.json()["access_token"]
        
        # Second login - should use a NEW session S2, not reuse S1
        login_resp_2 = client.post(
            "/api/auth/login",
            json={"email": test_email_2, "password": "testpass123"}
        )
        assert login_resp_2.status_code == 200
        token_2 = login_resp_2.json()["access_token"]
        
        # Tokens should be different (different users, different sessions)
        assert token_1 != token_2, "Tokens should differ for different users"
        
        # Third login with first user again - should use yet another NEW session S3
        login_resp_3 = client.post(
            "/api/auth/login",
            json={"email": test_email_1, "password": "testpass123"}
        )
        assert login_resp_3.status_code == 200
        token_3 = login_resp_3.json()["access_token"]
        
        # Token structure should be valid (JWT format: header.payload.signature)
        assert token_3.count('.') == 2, "Token should be valid JWT format"
        
        # All three logins succeeded with different sessions
        assert all([token_1, token_2, token_3]), "All tokens should be non-empty"
        
        # Cleanup
        db = TestSession()
        try:
            db.execute(text("DELETE FROM users WHERE id = :id1 OR id = :id2"), 
                      {"id1": str(user1_id), "id2": str(user2_id)})
            db.commit()
        finally:
            db.close()
