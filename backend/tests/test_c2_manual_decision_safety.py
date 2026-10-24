"""
C2 Manual Decision Safety Tests
Tests for: provider binding, idempotency, optimistic locking, transaction rollback, reflection safety, ownership
"""
import pytest
from datetime import date, datetime
from uuid import uuid4, UUID

from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.simulation import SimulationSession, SimulationDecision
from app.models.market_data import MarketDataBar
from app.auth.jwt import create_access_token
from tests.conftest import TestSession


@pytest.fixture
def db_session():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_user(db_session):
    user = User(
        id=uuid4(),
        email=f"test_{uuid4().hex[:8]}@example.com",
        password_hash="$2b$12$dummy",
        display_name="Test User",
        is_active=True
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def other_user(db_session):
    user = User(
        id=uuid4(),
        email=f"other_{uuid4().hex[:8]}@example.com",
        password_hash="$2b$12$dummy",
        display_name="Other User",
        is_active=True
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def test_experiment(db_session, test_user):
    exp = Experiment(
        id=uuid4(),
        owner_id=test_user.id,
        title="Test Experiment",
        status="locked"
    )
    db_session.add(exp)
    db_session.commit()
    return exp


@pytest.fixture
def test_version(db_session, test_experiment, test_user):
    ver = ExperimentVersion(
        id=uuid4(),
        experiment_id=test_experiment.id,
        version_number=1,
        strategy_spec={
            "template_type": "buy_and_hold",
            "asset_ids": ["US:AAPL"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "commission_rate": 0.001
        },
        created_by=test_user.id,
        locked_at=datetime.utcnow(),
        locked_by=test_user.id
    )
    db_session.add(ver)
    db_session.commit()
    return ver


@pytest.fixture
def test_market_data(db_session):
    bars = [
        MarketDataBar(
            instrument_id="US:AAPL",
            provider_id="twelvedata",
            bar_date=date(2024, 1, 2),
            bar_timestamp=datetime(2024, 1, 2, 16, 0),
            open=185.64, high=186.0, low=182.63, close=185.64, volume=50000000
        ),
        MarketDataBar(
            instrument_id="US:AAPL",
            provider_id="twelvedata",
            bar_date=date(2024, 1, 3),
            bar_timestamp=datetime(2024, 1, 3, 16, 0),
            open=184.22, high=185.0, low=183.0, close=184.25, volume=51000000
        ),
        MarketDataBar(
            instrument_id="US:AAPL",
            provider_id="twelvedata",
            bar_date=date(2024, 1, 4),
            bar_timestamp=datetime(2024, 1, 4, 16, 0),
            open=184.50, high=185.5, low=184.0, close=185.0, volume=52000000
        ),
    ]
    for bar in bars:
        db_session.add(bar)
    db_session.commit()
    return bars


@pytest.fixture
def test_session(db_session, test_version, test_user):
    session = SimulationSession(
        id=uuid4(),
        experiment_version_id=test_version.id,
        owner_id=test_user.id,
        initial_virtual_cash=100000.0,
        provider_id="twelvedata",
        provider_confirmed=True,
        status="pending",
        paper_equity=100000.0,
        total_decisions=0,
        state_version=0,
        data_cutoff_at="2024-01-31"
    )
    db_session.add(session)
    db_session.commit()
    return session


def get_auth_headers(user):
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


class TestAProviderBinding:
    """A: Provider binding - session must use stored data from confirmed provider"""

    def test_session_with_provider_confirmed(self, client, test_session, test_user, test_market_data):
        """Session with provider_confirmed=True can make decisions."""
        headers = get_auth_headers(test_user)
        payload = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"test_{uuid4().hex[:8]}",
            "expected_version": test_session.state_version
        }
        resp = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200

    def test_session_without_provider_confirmed_fails(self, db_session, test_version, test_user, test_market_data):
        """Session with provider_confirmed=False cannot make decisions."""
        session = SimulationSession(
            id=uuid4(),
            experiment_version_id=test_version.id,
            owner_id=test_user.id,
            initial_virtual_cash=100000.0,
            provider_id="twelvedata",
            provider_confirmed=False,  # Not confirmed
            status="pending",
            paper_equity=100000.0,
            total_decisions=0,
            state_version=0,
            data_cutoff_at="2024-01-31"
        )
        db_session.add(session)
        db_session.commit()

        headers = get_auth_headers(test_user)
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            payload = {
                "action": "buy",
                "quantity": 10,
                "idempotency_key": f"test_{uuid4().hex[:8]}",
                "expected_version": 0
            }
            resp = client.post(
                f"/api/v1/simulations/{session.id}/manual-decision",
                json=payload,
                headers=headers
            )
            assert resp.status_code == 400
            assert "provider not confirmed" in resp.json()["detail"].lower()


class TestBIdempotencySameKeySamePayload:
    """B: Idempotency - same key + same payload returns original decision"""

    def test_duplicate_request_returns_original(self, client, test_session, test_user, test_market_data):
        """Second request with same idempotency_key and same payload returns original decision."""
        headers = get_auth_headers(test_user)
        idem_key = f"idem_{uuid4().hex[:8]}"
        payload = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": idem_key,
            "rationale": "Test buy",
            "expected_version": test_session.state_version
        }

        # First request
        resp1 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp1.status_code == 200
        decision1 = resp1.json()["decision"]

        # Second request with same key and payload
        resp2 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp2.status_code == 200
        decision2 = resp2.json()["decision"]

        # Should return the same decision
        assert decision1["id"] == decision2["id"]
        assert decision1["step_number"] == decision2["step_number"]


class TestCIdempotencySameKeyDifferentPayload:
    """C: Idempotency conflict - same key + different payload returns 409"""

    def test_same_key_different_payload_conflicts(self, client, test_session, test_user, test_market_data):
        """Second request with same idempotency_key but different payload returns 409."""
        headers = get_auth_headers(test_user)
        idem_key = f"idem_{uuid4().hex[:8]}"

        # First request
        payload1 = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": idem_key,
            "expected_version": test_session.state_version
        }
        resp1 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload1,
            headers=headers
        )
        assert resp1.status_code == 200

        # Second request with same key but different quantity
        payload2 = {
            "action": "buy",
            "quantity": 20,  # Different
            "idempotency_key": idem_key,
            "expected_version": test_session.state_version + 1
        }
        resp2 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload2,
            headers=headers
        )
        assert resp2.status_code == 409
        assert "idempotency key conflict" in resp2.json()["detail"].lower()


class TestDOptimisticLockingDifferentKeys:
    """D: Optimistic locking - different keys with same observed version"""

    def test_concurrent_requests_with_same_version(self, client, test_session, test_user, test_market_data):
        """Two requests with different keys but same expected_version - first succeeds, second fails."""
        headers = get_auth_headers(test_user)
        version_before = test_session.state_version

        # First request
        payload1 = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"key1_{uuid4().hex[:8]}",
            "expected_version": version_before
        }
        resp1 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload1,
            headers=headers
        )
        assert resp1.status_code == 200

        # Second request with same expected_version (stale)
        payload2 = {
            "action": "buy",
            "quantity": 5,
            "idempotency_key": f"key2_{uuid4().hex[:8]}",
            "expected_version": version_before  # Stale
        }
        resp2 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload2,
            headers=headers
        )
        assert resp2.status_code == 409
        assert "state version conflict" in resp2.json()["detail"].lower()

    def test_true_concurrent_requests_with_threading(self, test_market_data):
        """True concurrent requests using threading with file-backed SQLite."""
        import threading
        import tempfile
        import os
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import Base, get_db
        
        # Create a temporary file-backed SQLite database
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            # Create engine and tables
            engine = create_engine(f'sqlite:///{db_path}', connect_args={'check_same_thread': False})
            Base.metadata.create_all(bind=engine)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            
            # Setup test data in a dedicated session
            setup_session = SessionLocal()
            
            # Create user in the file-backed database
            from app.models.user import User
            from app.models.experiment import Experiment
            from app.models.experiment_version import ExperimentVersion
            from datetime import datetime
            
            test_user = User(
                id=uuid4(),
                email="concurrent_test@example.com",
                password_hash="hashed_password",
                display_name="Concurrent Test User",
                user_type="individual",
                is_active=True,
                is_admin=False
            )
            setup_session.add(test_user)
            setup_session.commit()
            
            experiment = Experiment(
                id=uuid4(),
                owner_id=test_user.id,
                title="Concurrent Test",
                status="locked"
            )
            setup_session.add(experiment)
            setup_session.commit()
            
            version = ExperimentVersion(
                id=uuid4(),
                experiment_id=experiment.id,
                version_number=1,
                strategy_spec={
                    "template_type": "buy_and_hold",
                    "asset_ids": ["US:AAPL"],
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-31",
                    "initial_cash": 100000,
                    "commission_rate": 0.001
                },
                created_by=test_user.id,
                locked_at=datetime.utcnow(),
                locked_by=test_user.id
            )
            setup_session.add(version)
            setup_session.commit()
            
            # Create simulation session
            test_session = SimulationSession(
                id=uuid4(),
                experiment_version_id=version.id,
                owner_id=test_user.id,
                initial_virtual_cash=100000.0,
                provider_id="twelvedata",
                provider_confirmed=True,
                status="pending",
                paper_equity=100000.0,
                total_decisions=0,
                state_version=0,
                data_cutoff_at="2024-01-31"
            )
            setup_session.add(test_session)
            setup_session.commit()
            
            # Create market data in the file-backed database
            from app.models.market_data import MarketDataBar
            from datetime import date
            
            market_bars = [
                MarketDataBar(
                    instrument_id="US:AAPL",
                    provider_id="twelvedata",
                    bar_date=date(2024, 1, 2),
                    bar_timestamp=datetime(2024, 1, 2, 16, 0),
                    open=185.64, high=186.0, low=182.63, close=185.64, volume=50000000
                ),
                MarketDataBar(
                    instrument_id="US:AAPL",
                    provider_id="twelvedata",
                    bar_date=date(2024, 1, 3),
                    bar_timestamp=datetime(2024, 1, 3, 16, 0),
                    open=184.22, high=185.0, low=183.0, close=184.25, volume=51000000
                ),
                MarketDataBar(
                    instrument_id="US:AAPL",
                    provider_id="twelvedata",
                    bar_date=date(2024, 1, 4),
                    bar_timestamp=datetime(2024, 1, 4, 16, 0),
                    open=184.50, high=185.20, low=184.00, close=185.10, volume=49000000
                )
            ]
            for bar in market_bars:
                setup_session.add(bar)
            setup_session.commit()
            
            session_id = test_session.id
            initial_version = test_session.state_version
            user_id = test_user.id  # Save user_id before closing session
            setup_session.close()
            
            # Override get_db ONCE to use our SessionLocal
            # Each request will get its own session automatically
            def override_get_db():
                db = SessionLocal()
                try:
                    yield db
                finally:
                    db.close()
            
            app.dependency_overrides[get_db] = override_get_db
            
            # Synchronization barrier to ensure both threads start simultaneously
            barrier = threading.Barrier(2)
            results = {}
            errors = {}
            
            def make_request(key_suffix):
                try:
                    # Wait for both threads to be ready
                    barrier.wait(timeout=5)
                    
                    # Create a simple user object with just the ID for auth
                    from app.models.user import User
                    user_for_auth = User(id=user_id)
                    headers = get_auth_headers(user_for_auth)
                    payload = {
                        "action": "buy",
                        "quantity": 10,
                        "idempotency_key": f"concurrent_{key_suffix}_{uuid4().hex[:8]}",
                        "expected_version": initial_version  # Both use same version
                    }
                    
                    with TestClient(app) as client:
                        resp = client.post(
                            f"/api/v1/simulations/{session_id}/manual-decision",
                            json=payload,
                            headers=headers
                        )
                        results[key_suffix] = {
                            "status": resp.status_code,
                            "detail": resp.json().get("detail", ""),
                            "decision_id": resp.json().get("decision", {}).get("id") if resp.status_code == 200 else None
                        }
                except Exception as e:
                    import traceback
                    errors[key_suffix] = f"{str(e)}\n{traceback.format_exc()}"
            
            # Launch two threads
            thread1 = threading.Thread(target=make_request, args=("A",))
            thread2 = threading.Thread(target=make_request, args=("B",))
            
            thread1.start()
            thread2.start()
            
            thread1.join(timeout=10)
            thread2.join(timeout=10)
            
            # Clear the override and close all sessions
            app.dependency_overrides.pop(get_db, None)
            SessionLocal.close_all()
            
            # Debug: print results if we got unexpected status codes
            if results.get("A", {}).get("status") != 200 or results.get("B", {}).get("status") not in (200, 409):
                print(f"\nDEBUG - Thread A: status={results.get('A', {}).get('status')}, detail={results.get('A', {}).get('detail')}")
                print(f"DEBUG - Thread B: status={results.get('B', {}).get('status')}, detail={results.get('B', {}).get('detail')}")
                if errors:
                    print(f"DEBUG - Errors: {errors}")
            
            # Verify results
            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(results) == 2, f"Expected 2 results, got {len(results)}"
            
            # Exactly one should succeed and one should fail with 409
            statuses = [results["A"]["status"], results["B"]["status"]]
            assert 200 in statuses, f"Expected one 200, got {statuses}"
            assert 409 in statuses, f"Expected one 409, got {statuses}"
            
            # Verify the successful one created a decision
            success_key = "A" if results["A"]["status"] == 200 else "B"
            assert results[success_key]["decision_id"] is not None
            
            # Verify the failed one got a state version conflict
            fail_key = "B" if success_key == "A" else "A"
            assert "state version conflict" in results[fail_key]["detail"].lower()
            
        finally:
            # Cleanup
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestERetryAfterStateAdvancement:
    """E: Retry after state advancement - client can retry with updated version"""

    def test_retry_with_updated_version(self, client, test_session, test_user, test_market_data):
        """After conflict, client can retry with updated state_version."""
        headers = get_auth_headers(test_user)

        # First request succeeds
        payload1 = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"key1_{uuid4().hex[:8]}",
            "expected_version": test_session.state_version
        }
        resp1 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload1,
            headers=headers
        )
        assert resp1.status_code == 200
        new_version = resp1.json()["session"]["state_version"]

        # Retry with updated version
        payload2 = {
            "action": "buy",
            "quantity": 5,
            "idempotency_key": f"key2_{uuid4().hex[:8]}",
            "expected_version": new_version
        }
        resp2 = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload2,
            headers=headers
        )
        assert resp2.status_code == 200


class TestFTransactionRollback:
    """F: Transaction rollback - no partial state on error"""

    def test_no_decision_created_on_validation_error(self, client, test_session, test_user, test_market_data, db_session):
        """Invalid request doesn't create partial decision or advance state."""
        headers = get_auth_headers(test_user)
        version_before = test_session.state_version
        decisions_before = db_session.query(SimulationDecision).filter(
            SimulationDecision.session_id == test_session.id
        ).count()

        # Invalid request (quantity = 0)
        payload = {
            "action": "buy",
            "quantity": 0,  # Invalid
            "idempotency_key": f"test_{uuid4().hex[:8]}",
            "expected_version": version_before
        }
        resp = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 422  # Validation error

        # Verify no decision was created
        decisions_after = db_session.query(SimulationDecision).filter(
            SimulationDecision.session_id == test_session.id
        ).count()
        assert decisions_after == decisions_before

        # Verify state_version didn't change
        db_session.refresh(test_session)
        assert test_session.state_version == version_before


class TestGReflectionNoSideEffects:
    """G: Reflection update - no economic side effects"""

    def test_reflection_update_no_state_change(self, client, test_session, test_user, test_market_data, db_session):
        """Updating reflection doesn't change state_version or create new decisions."""
        headers = get_auth_headers(test_user)

        # Create a decision first
        payload = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"test_{uuid4().hex[:8]}",
            "expected_version": test_session.state_version
        }
        resp = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200
        decision_id_str = resp.json()["decision"]["id"]
        decision_id = UUID(decision_id_str) if isinstance(decision_id_str, str) else decision_id_str
        version_after_decision = resp.json()["session"]["state_version"]

        # Update reflection
        reflection_payload = {
            "reflection": "This was a good decision based on technical analysis."
        }
        resp = client.patch(
            f"/api/v1/simulations/{test_session.id}/decisions/{decision_id_str}/reflection",
            json=reflection_payload,
            headers=headers
        )
        assert resp.status_code == 200

        # Verify state_version didn't change
        db_session.refresh(test_session)
        assert test_session.state_version == version_after_decision

        # Verify reflection was saved
        decision = db_session.query(SimulationDecision).filter(
            SimulationDecision.id == decision_id
        ).first()
        assert decision.decision_detail.get("reflection") == reflection_payload["reflection"]


class TestHOwnershipEnforcement:
    """H: Ownership enforcement - users can only access their own sessions"""

    def test_other_user_cannot_access_session(self, client, test_session, test_user, other_user, test_market_data):
        """Different user cannot make decisions on session they don't own."""
        headers = get_auth_headers(other_user)
        payload = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"test_{uuid4().hex[:8]}",
            "expected_version": test_session.state_version
        }
        resp = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 403  # Forbidden

    def test_other_user_cannot_update_reflection(self, client, test_session, test_user, other_user, test_market_data, db_session):
        """Different user cannot update reflection on session they don't own."""
        headers = get_auth_headers(test_user)

        # Create a decision as owner
        payload = {
            "action": "buy",
            "quantity": 10,
            "idempotency_key": f"test_{uuid4().hex[:8]}",
            "expected_version": test_session.state_version
        }
        resp = client.post(
            f"/api/v1/simulations/{test_session.id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200
        decision_id_str = resp.json()["decision"]["id"]
        decision_id = UUID(decision_id_str) if isinstance(decision_id_str, str) else decision_id_str

        # Try to update reflection as other user
        other_headers = get_auth_headers(other_user)
        reflection_payload = {
            "reflection": "Malicious reflection"
        }
        resp = client.patch(
            f"/api/v1/simulations/{test_session.id}/decisions/{decision_id_str}/reflection",
            json=reflection_payload,
            headers=other_headers
        )
        assert resp.status_code == 403  # Forbidden
