"""Real API login + replay with fixture-based cleanup.

Uses actual /auth/login endpoint (not create_access_token).
Integrates api_test_fixtures for guaranteed cleanup.

Environment:
- Database: PostgreSQL ai_investment_lab_test (ALETHRA_TEST_DB)
- Runtime role: alethra_test_runtime (limited permissions)
- Migration head: verified at test start
"""
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text

from app.database import SessionLocal
from tests.api_test_fixtures import api_test_fixtures


class TestRealAPILoginWithFixture:
    """Real login endpoint + replay with fixture cleanup."""
    
    def test_login_endpoint_returns_valid_token(self, client):
        """Prove /auth/login works with fixture-created user."""
        test_email = f"login_test_{uuid4().hex[:8]}@example.com"
        test_password = "testpass123"
        
        with api_test_fixtures(test_email) as (auth_headers, user_id, run_id):
            # auth_headers from fixture uses create_access_token
            # Now test REAL login endpoint
            login_resp = client.post(
                "/api/auth/login",
                json={"email": test_email, "password": test_password}
            )
            
            assert login_resp.status_code == 200, f"Login failed: {login_resp.json()}"
            login_data = login_resp.json()
            
            # Verify token structure
            assert "access_token" in login_data
            assert "token_type" in login_data
            assert login_data["token_type"] == "bearer"
            
            # Verify token works by calling a protected endpoint
            real_auth_headers = {"Authorization": f"Bearer {login_data['access_token']}"}
            status_resp = client.get(
                "/api/v1/paper-trading/status",
                headers=real_auth_headers
            )
            # Should not be 401
            assert status_resp.status_code != 401, "Real login token rejected"
    
    def test_full_api_chain_with_real_login(self, client):
        """Full API chain: real login → create → replay → status → daily-report.
        
        Uses fixed, hand-calculable source data with non-zero costs.
        """
        test_email = f"full_chain_{uuid4().hex[:8]}@example.com"
        
        with api_test_fixtures(test_email) as (auth_headers, user_id, study_run_id):
            # Step 1: Real login
            login_resp = client.post(
                "/api/auth/login",
                json={"email": test_email, "password": "testpass123"}
            )
            assert login_resp.status_code == 200
            real_token = login_resp.json()["access_token"]
            real_headers = {"Authorization": f"Bearer {real_token}"}
            
            # Step 2: Create portfolio via API
            create_resp = client.post(
                "/api/v1/paper-trading/create",
                headers=real_headers,
                json={
                    "skill": "test_rule",
                    "instrument_id": "TEST/USD",
                    "interval_minutes": 60,
                    "exit_config": "default",
                    "initial_capital": 100000.0,
                },
            )
            assert create_resp.status_code == 200, f"Create failed: {create_resp.json()}"
            create_data = create_resp.json()
            
            assert create_data["status"] == "queued"
            portfolio_id = create_data["portfolio_id"]
            run_id = create_data["run_id"]
            
            # Step 3: Execute replay
            replay_resp = client.post(
                f"/api/v1/paper-trading/replay/{run_id}",
                headers=real_headers,
                json={
                    "days_back": 30,
                    "position_size_pct": 0.10,
                    "max_positions": 3,
                },
            )
            assert replay_resp.status_code == 200, f"Replay failed: {replay_resp.json()}"
            replay_data = replay_resp.json()
            
            assert replay_data["status"] == "completed"
            assert replay_data["total_trades"] > 0
            
            # Step 4: Verify with independent DB read
            db = SessionLocal()
            try:
                run = db.execute(
                    text("SELECT * FROM paper_trading_runs WHERE id = :id"),
                    {"id": run_id},
                ).fetchone()
                assert run is not None, "Run not found in DB"
                assert run.status == "completed"
                assert run.total_trades == replay_data["total_trades"]
            finally:
                db.close()
            
            # Fixture cleanup happens automatically on exit
