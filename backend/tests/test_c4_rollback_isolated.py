"""Isolated C4 transaction rollback test with failure injection."""
import pytest
import tempfile
import os
from datetime import date, datetime
from uuid import uuid4, UUID as UUID_Type
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.database import Base, get_db
from app.models import SimulationSession, SimulationDecision, MarketDataBar, AuditEvent
from app.main import app


class C4TestHarness:
    """Provides isolated file-backed SQLite for C4 rollback testing."""

    def __init__(self):
        # Create temporary file-backed database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, f"test_c4_{uuid4().hex}.db")
        self.db_url = f"sqlite:///{self.db_path}"

        # Create engine with NullPool
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
        self.client = TestClient(app, raise_server_exceptions=False)

    def _override_get_db(self):
        """Yield independent sessions for each request."""
        db = self.SessionFactory()
        try:
            yield db
        finally:
            db.close()

    def create_user_and_auth(self):
        """Create a test user and return auth headers."""
        email = f"c4_test_{uuid4().hex[:8]}@test.io"

        resp = self.client.post("/api/auth/register", json={
            "email": email,
            "password": "testpass123",
            "display_name": "C4 Test User",
            "user_type": "individual",
        })

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
            "title": f"C4 Test {uuid4().hex[:8]}",
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
            audit_event_count = db.query(AuditEvent).filter(
                AuditEvent.entity_id == str(session_id)
            ).count()

            return {
                "state_version": session.state_version,
                "total_decisions": session.total_decisions,
                "observed_days": session.observed_days,
                "paper_equity": session.paper_equity,
                "current_snapshot_date": session.current_snapshot_date,
                "status": session.status,
                "decision_count": decision_count,
                "audit_event_count": audit_event_count,
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

    def get_audit_events(self, session_id):
        """Get all audit events for a session."""
        db = self.SessionFactory()
        try:
            events = db.query(AuditEvent).filter(
                AuditEvent.entity_id == str(session_id)
            ).all()
            return [{
                "id": str(e.id),
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
            } for e in events]
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


class TestC4TransactionRollback:
    """C4: Verify transaction rollback on failure during execution."""

    def test_c4_rollback_on_emit_event_failure(self):
        """Inject failure after mutations but before commit, verify full rollback."""
        harness = C4TestHarness()

        try:
            print("\n=== C4: Transaction Rollback on Failure ===")

            # Setup
            auth_headers = harness.create_user_and_auth()
            session_id = harness.create_session_with_data(auth_headers)

            # Get baseline state
            baseline = harness.get_session_state(session_id)
            print(f"Baseline: version={baseline['state_version']}, "
                  f"decisions={baseline['decision_count']}, "
                  f"equity={baseline['paper_equity']}, "
                  f"audit_events={baseline['audit_event_count']}")

            # Prepare request
            idem_key = f"c4_rollback_{uuid4().hex}"
            payload = {
                "action": "buy",
                "quantity": 100,
                "rationale": "C4 rollback test",
                "idempotency_key": idem_key,
                "expected_version": baseline['state_version']
            }

            # Inject failure in emit_event AFTER mutations but BEFORE commit
            print("\nInjecting failure in emit_event...")

            # Import original emit_event to call it before raising
            from app.services import emit_event as original_emit_event
            injection_reached = [False]
            audit_event_written = [False]

            def failing_emit_event(*args, **kwargs):
                """Call original emit_event (flushes audit event), then raise."""
                injection_reached[0] = True
                print("  [INJECTION] Calling original emit_event...")
                # Call original - this will db.add(event) + db.flush()
                result = original_emit_event(*args, **kwargs)
                audit_event_written[0] = True
                print("  [INJECTION] emit_event flushed, now raising exception")
                # Raise AFTER flush but BEFORE commit
                raise RuntimeError("C4 injected failure: simulated transaction error")

            # Patch emit_event in the simulations module
            with patch('app.api.simulations.emit_event', side_effect=failing_emit_event):
                # Issue request
                resp = harness.client.post(
                    f"/api/v1/simulations/{session_id}/manual-decision",
                    headers=auth_headers,
                    json=payload
                )

                print(f"Request response: HTTP {resp.status_code}")

                # Should fail with 500 (unhandled exception)
                assert resp.status_code == 500, \
                    f"Expected 500, got {resp.status_code}"

                # Verify injection was reached
                assert injection_reached[0], "Injection was not reached"
                print("✓ Injection confirmed: emit_event was called")

                # Verify audit event was flushed before exception
                assert audit_event_written[0], "Audit event was not flushed before exception"
                print("✓ Audit event flushed (but not committed) before exception")

            # Verify full rollback
            print("\nVerifying rollback...")
            after_failure = harness.get_session_state(session_id)
            decisions_after = harness.get_decisions(session_id)
            audit_events_after = harness.get_audit_events(session_id)

            print(f"After failure: version={after_failure['state_version']}, "
                  f"decisions={after_failure['decision_count']}, "
                  f"equity={after_failure['paper_equity']}, "
                  f"audit_events={len(audit_events_after)}")

            # Compare against baseline
            assert after_failure['state_version'] == baseline['state_version'], \
                f"state_version changed: {baseline['state_version']} → {after_failure['state_version']}"

            assert after_failure['decision_count'] == baseline['decision_count'], \
                f"decision_count changed: {baseline['decision_count']} → {after_failure['decision_count']}"

            assert after_failure['total_decisions'] == baseline['total_decisions'], \
                f"total_decisions changed"

            assert after_failure['observed_days'] == baseline['observed_days'], \
                f"observed_days changed"

            assert after_failure['paper_equity'] == baseline['paper_equity'], \
                f"paper_equity changed: {baseline['paper_equity']} → {after_failure['paper_equity']}"

            assert after_failure['current_snapshot_date'] == baseline['current_snapshot_date'], \
                f"current_snapshot_date changed"

            assert len(decisions_after) == baseline['decision_count'], \
                f"Decision count mismatch: {len(decisions_after)} vs {baseline['decision_count']}"

            # Verify audit event was rolled back (flushed but not committed)
            assert len(audit_events_after) == baseline['audit_event_count'], \
                f"Audit event not rolled back: {len(audit_events_after)} vs {baseline['audit_event_count']}"

            print("✓ Full rollback verified: no partial effects (including audit event)")

            # Verify retry after rollback
            print("\nRetrying with EXACT SAME payload...")
            retry_resp = harness.client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json=payload  # Same payload
            )

            assert retry_resp.status_code in (200, 201), \
                f"Retry failed: {retry_resp.status_code} - {retry_resp.text}"

            retry_decision_id = retry_resp.json()["decision"]["id"]
            print(f"✓ Retry succeeded: decision_id={retry_decision_id}")

            # Verify retry committed correctly
            after_retry = harness.get_session_state(session_id)
            decisions_after_retry = harness.get_decisions(session_id)

            print(f"After retry: version={after_retry['state_version']}, "
                  f"decisions={after_retry['decision_count']}, "
                  f"equity={after_retry['paper_equity']}")

            # Verify exactly one decision created
            assert after_retry['decision_count'] == baseline['decision_count'] + 1, \
                f"Expected {baseline['decision_count'] + 1} decisions, got {after_retry['decision_count']}"

            # Verify state advanced once
            assert after_retry['state_version'] == baseline['state_version'] + 1, \
                f"Expected version {baseline['state_version'] + 1}, got {after_retry['state_version']}"

            # Verify no residue from failed attempt
            assert len(decisions_after_retry) == after_retry['decision_count'], \
                "Decision count mismatch"

            print("\n✓ C4 PASS: Full rollback on failure, retry succeeded")

        finally:
            harness.cleanup()
