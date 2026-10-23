"""Isolated concurrency tests for C1 (simultaneous identical) and C2 (competing distinct)."""
import pytest
import threading
import tempfile
import os
from datetime import date, datetime
from uuid import uuid4, UUID as UUID_Type
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.models import SimulationSession, SimulationDecision, MarketDataBar
from app.main import app


class ConcurrencyTestHarness:
    """Provides isolated file-backed SQLite for concurrent testing."""

    def __init__(self):
        # Create temporary file-backed database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, f"test_concurrent_{uuid4().hex}.db")
        self.db_url = f"sqlite:///{self.db_path}"

        # Create engine with NullPool for independent connections
        self.engine = create_engine(
            self.db_url,
            poolclass=NullPool,
            connect_args={"check_same_thread": False}
        )

        # Register per-connection foreign-key enforcement
        # SQLite FK setting is per-connection, not per-database
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            """Enable foreign-key enforcement on every new DBAPI connection."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            result = cursor.execute("PRAGMA foreign_keys").fetchone()
            cursor.close()
            assert result[0] == 1, f"FK enforcement failed on new connection: got {result[0]}"

        # Create all tables
        Base.metadata.create_all(bind=self.engine)

        # Session factory
        self.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        # Capture previous dependency override state (don't .clear() later)
        self.previous_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._override_get_db

        # Create test client
        self.client = TestClient(app)

    def _override_get_db(self):
        """Yield independent sessions for each request."""
        db = self.SessionFactory()
        try:
            yield db
        finally:
            db.close()

    def create_user_and_auth(self):
        """Create a test user and return auth headers."""
        email = f"concurrent_{uuid4().hex[:8]}@test.io"

        resp = self.client.post("/api/auth/register", json={
            "email": email,
            "password": "testpass123",
            "display_name": "Concurrent Test User",
            "user_type": "individual",
        })

        # Login (whether registration succeeded or user already exists)
        resp = self.client.post("/api/auth/login", json={
            "email": email,
            "password": "testpass123",
        })

        assert resp.status_code == 200, f"Login failed: {resp.status_code} - {resp.text}"
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def create_session_with_data(self, auth_headers):
        """Create experiment, lock, insert market data, create session."""
        # Create experiment
        resp = self.client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": f"Concurrency Test {uuid4().hex[:8]}",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 100000,
            }
        })
        assert resp.status_code == 201, f"Experiment creation failed: {resp.text}"
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        # Lock
        resp = self.client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })
        assert resp.status_code == 200

        # Insert market data
        db = self.SessionFactory()
        try:
            for i in range(20):
                bar_date = date(2024, 1, 2 + i)
                bar = MarketDataBar(
                    instrument_id='US:AAPL',
                    bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                    bar_date=bar_date,
                    open=180.0 + i * 0.5,
                    high=181.0 + i * 0.5,
                    low=179.0 + i * 0.5,
                    close=180.5 + i * 0.5,
                    volume=50000000,
                    provider_id='twelvedata',
                )
                db.add(bar)
            db.commit()
        finally:
            db.close()

        # Create session
        resp = self.client.post(
            f"/api/v1/simulations/versions/{version_id}",
            headers=auth_headers,
            json={"initial_virtual_cash": 100000.0, "provider_id": "twelvedata"}
        )
        assert resp.status_code == 201, f"Session creation failed: {resp.text}"
        session_id = resp.json()["id"]

        return session_id

    def get_session_state(self, session_id):
        """Get current session state via ORM."""
        db = self.SessionFactory()
        try:
            session = db.query(SimulationSession).filter(
                SimulationSession.id == UUID_Type(session_id)
            ).first()
            decision_count = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == UUID_Type(session_id)
            ).count()

            return {
                "state_version": session.state_version,
                "total_decisions": session.total_decisions,
                "observed_days": session.observed_days,
                "paper_equity": session.paper_equity,
                "current_snapshot_date": session.current_snapshot_date,
                "status": session.status,
                "decision_count": decision_count,
            }
        finally:
            db.close()

    def get_decisions(self, session_id):
        """Get all decisions for a session."""
        db = self.SessionFactory()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == UUID_Type(session_id)
            ).order_by(SimulationDecision.step_number).all()
            return [{
                "id": str(d.id),
                "step_number": d.step_number,
                "paper_cash": d.paper_cash,
                "paper_equity": d.paper_equity,
                "decision_type": d.decision_type,
            } for d in decisions]
        finally:
            db.close()

    def cleanup(self):
        """Clean up resources."""
        # Restore previous dependency overrides (not .clear())
        app.dependency_overrides = self.previous_overrides.copy()
        self.client.close()
        self.engine.dispose()
        try:
            os.remove(self.db_path)
            os.rmdir(self.temp_dir)
        except:
            pass


class TestC1SimultaneousIdentical:
    """C1: Simultaneous identical requests should result in exactly one execution."""

    def test_c1_concurrent_duplicate_requests(self):
        """Two simultaneous requests with same key/payload → exactly one decision."""
        harness = ConcurrencyTestHarness()
        workers_timed_out = [False]  # Track if workers timed out

        try:
            print("\n=== C1: Simultaneous Identical Requests ===")

            # Setup
            auth_headers = harness.create_user_and_auth()
            session_id = harness.create_session_with_data(auth_headers)

            # Get initial state
            initial_state = harness.get_session_state(session_id)
            print(f"Initial state: version={initial_state['state_version']}, "
                  f"decisions={initial_state['decision_count']}")

            # Prepare identical requests
            idem_key = f"c1_concurrent_{uuid4().hex}"
            payload = {
                "action": "buy",
                "quantity": 100,
                "rationale": "C1 concurrent test",
                "idempotency_key": idem_key,
                "expected_version": initial_state['state_version']
            }

            # Barrier for synchronized start
            barrier = threading.Barrier(2)
            responses = [None, None]
            exceptions = [None, None]

            def worker(idx):
                try:
                    barrier.wait(timeout=5)  # Synchronize start
                    resp = harness.client.post(
                        f"/api/v1/simulations/{session_id}/manual-decision",
                        headers=auth_headers,
                        json=payload
                    )
                    responses[idx] = {
                        "status_code": resp.status_code,
                        "body": resp.json() if resp.status_code in (200, 201) else resp.text
                    }
                except Exception as e:
                    exceptions[idx] = str(e)

            # Launch concurrent requests
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
            for t in threads:
                t.start()

            # Wait for completion with bounded timeout
            for t in threads:
                t.join(timeout=10)

            # Verify worker lifecycle: all threads must be dead after joins
            alive_workers = []
            for i, t in enumerate(threads):
                if t.is_alive():
                    alive_workers.append(i)

            if alive_workers:
                # Workers timed out - do NOT cleanup, report issue and retain resources
                workers_timed_out[0] = True
                assert False, (
                    f"Workers {alive_workers} still alive after 10s timeout. "
                    f"Retaining test database at: {harness.db_path} for inspection. "
                    f"Temp directory: {harness.temp_dir}"
                )

            # Check for exceptions
            for i, exc in enumerate(exceptions):
                if exc:
                    print(f"✗ Worker {i} exception: {exc}")
                    assert False, f"Worker {i} failed with exception: {exc}"

            # Analyze responses
            print(f"\nWorker 0: HTTP {responses[0]['status_code']}")
            print(f"Worker 1: HTTP {responses[1]['status_code']}")

            # Both should succeed (200/201) or one should get 409
            status_codes = [r['status_code'] for r in responses]
            assert all(code in (200, 201, 409) for code in status_codes), \
                f"Unexpected status codes: {status_codes}"

            # Check for 500 errors
            assert all(code != 500 for code in status_codes), "Unhandled 500 error detected"

            # Get final state
            final_state = harness.get_session_state(session_id)
            decisions = harness.get_decisions(session_id)

            print(f"\nFinal state: version={final_state['state_version']}, "
                  f"decisions={final_state['decision_count']}")

            # Verify exactly one decision
            assert final_state['decision_count'] == 1, \
                f"Expected 1 decision, got {final_state['decision_count']}"

            # Verify state incremented once
            assert final_state['state_version'] == initial_state['state_version'] + 1, \
                f"Expected version {initial_state['state_version'] + 1}, got {final_state['state_version']}"

            # If one got 409, verify it can retry successfully (C3 contract)
            if 409 in status_codes:
                print("\nOne request received 409, testing C3 retry...")
                loser_idx = status_codes.index(409)
                winner_idx = 1 - loser_idx

                # Retry with EXACT SAME payload
                retry_resp = harness.client.post(
                    f"/api/v1/simulations/{session_id}/manual-decision",
                    headers=auth_headers,
                    json=payload
                )

                assert retry_resp.status_code in (200, 201), \
                    f"C3 retry failed: {retry_resp.status_code}"

                retry_decision_id = retry_resp.json()["decision"]["id"]
                winner_decision_id = responses[winner_idx]['body']["decision"]["id"]

                assert retry_decision_id == winner_decision_id, \
                    "C3 retry should return same decision"

                print(f"✓ C3 retry succeeded: returned original decision {retry_decision_id}")

            print("\n✓ C1 PASS: Exactly one decision created, no duplicate effects")

        finally:
            # Only cleanup if workers finished successfully
            if not workers_timed_out[0]:
                harness.cleanup()
            else:
                print(f"\n⚠ Workers timed out. Retaining resources for inspection:")
                print(f"  Database: {harness.db_path}")
                print(f"  Temp dir: {harness.temp_dir}")


class TestC2CompetingDistinct:
    """C2: Competing distinct intents should result in exactly one winner."""

    def test_c2_concurrent_competing_intents(self):
        """Two simultaneous requests with different keys → exactly one wins."""
        harness = ConcurrencyTestHarness()
        workers_timed_out = [False]  # Track if workers timed out

        try:
            print("\n=== C2: Competing Distinct Intents ===")

            # Setup
            auth_headers = harness.create_user_and_auth()
            session_id = harness.create_session_with_data(auth_headers)

            # Get initial state
            initial_state = harness.get_session_state(session_id)
            print(f"Initial state: version={initial_state['state_version']}, "
                  f"decisions={initial_state['decision_count']}")

            # Prepare two DIFFERENT requests with SAME expected_version
            idem_key_1 = f"c2_competing_1_{uuid4().hex}"
            payload_1 = {
                "action": "buy",
                "quantity": 100,
                "rationale": "C2 competitor 1",
                "idempotency_key": idem_key_1,
                "expected_version": initial_state['state_version']
            }

            idem_key_2 = f"c2_competing_2_{uuid4().hex}"
            payload_2 = {
                "action": "buy",
                "quantity": 50,  # Different quantity
                "rationale": "C2 competitor 2",
                "idempotency_key": idem_key_2,
                "expected_version": initial_state['state_version']  # Same version!
            }

            # Barrier for synchronized start
            barrier = threading.Barrier(2)
            responses = [None, None]
            exceptions = [None, None]

            def worker(idx, payload):
                try:
                    barrier.wait(timeout=5)
                    resp = harness.client.post(
                        f"/api/v1/simulations/{session_id}/manual-decision",
                        headers=auth_headers,
                        json=payload
                    )
                    responses[idx] = {
                        "status_code": resp.status_code,
                        "body": resp.json() if resp.status_code in (200, 201) else resp.text
                    }
                except Exception as e:
                    exceptions[idx] = str(e)

            # Launch concurrent requests
            threads = [
                threading.Thread(target=worker, args=(0, payload_1)),
                threading.Thread(target=worker, args=(1, payload_2))
            ]
            for t in threads:
                t.start()

            # Wait for completion with bounded timeout
            for t in threads:
                t.join(timeout=10)

            # Verify worker lifecycle: all threads must be dead after joins
            alive_workers = []
            for i, t in enumerate(threads):
                if t.is_alive():
                    alive_workers.append(i)

            if alive_workers:
                # Workers timed out - do NOT cleanup, report issue and retain resources
                workers_timed_out[0] = True
                assert False, (
                    f"Workers {alive_workers} still alive after 10s timeout. "
                    f"Retaining test database at: {harness.db_path} for inspection. "
                    f"Temp directory: {harness.temp_dir}"
                )

            # Check for exceptions
            for i, exc in enumerate(exceptions):
                if exc:
                    print(f"✗ Worker {i} exception: {exc}")
                    assert False, f"Worker {i} failed with exception: {exc}"

            # Analyze responses
            print(f"\nWorker 0: HTTP {responses[0]['status_code']}")
            print(f"Worker 1: HTTP {responses[1]['status_code']}")

            # Check for 500 errors
            status_codes = [r['status_code'] for r in responses]
            assert all(code != 500 for code in status_codes), "Unhandled 500 error detected"

            # Get final state
            final_state = harness.get_session_state(session_id)
            decisions = harness.get_decisions(session_id)

            print(f"\nFinal state: version={final_state['state_version']}, "
                  f"decisions={final_state['decision_count']}")

            # Verify exactly one decision
            assert final_state['decision_count'] == 1, \
                f"Expected 1 decision, got {final_state['decision_count']}"

            # Verify state incremented once
            assert final_state['state_version'] == initial_state['state_version'] + 1, \
                f"Expected version {initial_state['state_version'] + 1}, got {final_state['state_version']}"

            # One should succeed, one should get 409
            success_count = sum(1 for code in status_codes if code in (200, 201))
            conflict_count = sum(1 for code in status_codes if code == 409)

            assert success_count == 1, f"Expected 1 success, got {success_count}"
            assert conflict_count == 1, f"Expected 1 conflict, got {conflict_count}"

            # Verify the conflict is stale-version
            loser_idx = status_codes.index(409)
            loser_response = responses[loser_idx]['body']
            assert "State version conflict" in loser_response, \
                f"Expected stale-version conflict, got: {loser_response}"

            print(f"\n✓ C2 PASS: Exactly one winner, one stale-version conflict")

        finally:
            # Only cleanup if workers finished successfully
            if not workers_timed_out[0]:
                harness.cleanup()
            else:
                print(f"\n⚠ Workers timed out. Retaining resources for inspection:")
                print(f"  Database: {harness.db_path}")
                print(f"  Temp dir: {harness.temp_dir}")
