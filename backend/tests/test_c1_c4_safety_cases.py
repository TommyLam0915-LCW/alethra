"""
C1-C4 Safety Case Tests
Concurrent duplicate, stale step, response retry, transaction rollback
"""
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.models.user import User
from app.models.experiment import Experiment, ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession, SimulationDecision
from app.auth.security import create_access_token

# Isolated in-memory SQLite for testing
TEST_DB_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create all tables
Base.metadata.create_all(bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

# Test data
TEST_USER_ID = "test_user_c1_c4"
TEST_USER_EMAIL = "test_c1_c4@example.com"
TEST_USER_PASSWORD = "TestPass123!"
TEST_VERSION_ID = "test_version_c1_c4"
TEST_EXPERIMENT_ID = "test_exp_c1_c4"


@pytest.fixture(autouse=True)
def setup_test_data():
    """Setup test user, experiment, and market data."""
    db = TestingSessionLocal()
    try:
        # Create user
        user = User(
            id=TEST_USER_ID,
            email=TEST_USER_EMAIL,
            hashed_password="$2b$12$LQv3c1yqBo9Qv3c1yqBo9OeKqZqZqZqZqZqZqZqZqZqZqZqZqZq",  # dummy hash
            display_name="C1-C4 Test User",
            is_active=True
        )
        db.merge(user)
        
        # Create experiment
        exp = Experiment(
            id=TEST_EXPERIMENT_ID,
            owner_id=TEST_USER_ID,
            title="C1-C4 Test Experiment",
            symbol="US:AAPL",
            status="active"
        )
        db.merge(exp)
        
        # Create experiment version
        ver = ExperimentVersion(
            id=TEST_VERSION_ID,
            experiment_id=TEST_EXPERIMENT_ID,
            version_number=1,
            symbol="US:AAPL",
            start_date="2024-01-01",
            end_date="2024-01-31",
            strategy_spec={"asset_ids": ["US:AAPL"], "start_date": "2024-01-01", "end_date": "2024-01-31"}
        )
        db.merge(ver)
        
        # Create market data (twelvedata)
        from datetime import date
        for i, (d, o, h, l, c, v) in enumerate([
            ("2024-01-02", 185.64, 186.0, 182.63, 185.64, 50000000),
            ("2024-01-03", 184.22, 185.0, 183.0, 184.25, 51000000),
            ("2024-01-04", 182.15, 183.0, 181.0, 182.0, 52000000),
            ("2024-01-05", 181.18, 182.0, 180.0, 181.0, 53000000),
            ("2024-01-08", 182.09, 183.0, 181.5, 182.5, 54000000),
        ]):
            bar = MarketDataBar(
                instrument_id="US:AAPL",
                provider_id="twelvedata",
                bar_date=date.fromisoformat(d),
                open=o, high=h, low=l, close=c, volume=v
            )
            db.merge(bar)
        
        db.commit()
    finally:
        db.close()
    
    yield
    
    # Cleanup
    db = TestingSessionLocal()
    try:
        db.query(SimulationDecision).filter(SimulationDecision.session_id.like('%c1_c4%')).delete(synchronize_session=False)
        db.query(SimulationSession).filter(SimulationSession.id.like('%c1_c4%')).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def get_auth_headers():
    """Get authentication headers."""
    token = create_access_token(data={"sub": TEST_USER_ID})
    return {"Authorization": f"Bearer {token}"}


def create_test_session(provider_id="twelvedata", confirmed=True):
    """Create a test session."""
    db = TestingSessionLocal()
    try:
        session_id = f"test_session_c1_c4_{int(time.time()*1000)}"
        session = SimulationSession(
            id=session_id,
            experiment_version_id=TEST_VERSION_ID,
            owner_id=TEST_USER_ID,
            initial_virtual_cash=100000.0,
            provider_id=provider_id,
            provider_confirmed=confirmed,
            status="pending",
            paper_equity=100000.0,
            total_decisions=0
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session_id
    finally:
        db.close()


class TestC1ConcurrentDuplicate:
    """C1: Concurrent duplicate - same key, same payload, simultaneous requests."""
    
    def test_concurrent_duplicate_same_key(self):
        """Two simultaneous requests with same idempotency key should produce only one decision."""
        session_id = create_test_session()
        headers = get_auth_headers()
        
        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "C1 concurrent test",
            "idempotency_key": "c1_test_key_001"
        }
        
        results = []
        errors = []
        
        def make_request():
            try:
                resp = client.post(
                    f"/api/v1/simulations/{session_id}/manual-decision",
                    json=payload,
                    headers=headers
                )
                results.append((resp.status_code, resp.json()))
            except Exception as e:
                errors.append(str(e))
        
        # Launch two concurrent requests
        threads = [threading.Thread(target=make_request) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 2
        
        # At least one should succeed (200/201)
        success_results = [r for r in results if r[0] in (200, 201)]
        assert len(success_results) >= 1, f"No successful results: {results}"
        
        # Check that only one decision was created
        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id,
                SimulationDecision.idempotency_key == "c1_test_key_001"
            ).all()
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
            
            # Verify session state
            session = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            assert session.total_decisions == 1, f"Expected total_decisions=1, got {session.total_decisions}"
            assert session.paper_equity != 100000.0, "Equity should have changed"
        finally:
            db.close()


class TestC2DifferentKeysStaleStep:
    """C2: Different keys against same stale step."""
    
    def test_different_keys_same_step(self):
        """Two different idempotency keys should not both execute against same step."""
        session_id = create_test_session()
        headers = get_auth_headers()
        
        # First request - should succeed
        payload1 = {
            "action": "buy",
            "quantity": 50,
            "rationale": "C2 first request",
            "idempotency_key": "c2_key_001"
        }
        
        resp1 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload1,
            headers=headers
        )
        assert resp1.status_code in (200, 201)
        
        # Second request with different key but same step context
        # This should either succeed (advancing to next step) or fail with clear error
        payload2 = {
            "action": "hold",
            "idempotency_key": "c2_key_002"
        }
        
        resp2 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload2,
            headers=headers
        )
        
        # Verify only expected decisions exist
        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).order_by(SimulationDecision.step_number).all()
            
            # Should have 1 or 2 decisions depending on implementation
            assert len(decisions) >= 1, "At least one decision should exist"
            
            # Verify step numbers are sequential
            for i, dec in enumerate(decisions):
                assert dec.step_number == i + 1, f"Step numbers not sequential: {dec.step_number} != {i+1}"
            
            # Verify session state matches decision count
            session = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            assert session.total_decisions == len(decisions), "total_decisions mismatch"
        finally:
            db.close()


class TestC3ResponseLostAfterCommit:
    """C3: Response lost after commit - retry with same key."""
    
    def test_retry_after_commit(self):
        """Retry with same key/payload should return original result, not create duplicate."""
        session_id = create_test_session()
        headers = get_auth_headers()
        
        payload = {
            "action": "buy",
            "quantity": 75,
            "rationale": "C3 retry test",
            "idempotency_key": "c3_retry_key_001"
        }
        
        # First request - simulate successful commit
        resp1 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp1.status_code in (200, 201)
        result1 = resp1.json()
        decision_id1 = result1.get("decision", {}).get("id")
        
        # Second request - simulate retry after lost response
        resp2 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp2.status_code in (200, 201)
        result2 = resp2.json()
        decision_id2 = result2.get("decision", {}).get("id")
        
        # Should return same decision
        assert decision_id1 == decision_id2, "Retry should return same decision ID"
        
        # Verify only one decision exists
        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id,
                SimulationDecision.idempotency_key == "c3_retry_key_001"
            ).all()
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
        finally:
            db.close()


class TestC4TransactionFailure:
    """C4: Transaction failure / rollback."""
    
    def test_invalid_payload_no_partial_state(self):
        """Invalid payload should not leave partial state."""
        session_id = create_test_session()
        headers = get_auth_headers()
        
        # Get initial state
        db = TestingSessionLocal()
        try:
            session_before = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            decisions_before = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).count()
        finally:
            db.close()
        
        # Send invalid request (negative quantity)
        payload = {
            "action": "buy",
            "quantity": -10,  # Invalid
            "idempotency_key": "c4_invalid_key"
        }
        
        resp = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )
        
        # Should fail validation
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
        
        # Verify no partial state
        db = TestingSessionLocal()
        try:
            session_after = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            decisions_after = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).count()
            
            assert session_after.paper_equity == session_before.paper_equity, "Equity should not change"
            assert session_after.total_decisions == session_before.total_decisions, "total_decisions should not change"
            assert decisions_after == decisions_before, "Decision count should not change"
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
