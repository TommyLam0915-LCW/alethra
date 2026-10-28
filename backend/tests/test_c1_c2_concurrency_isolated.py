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
from app.models import (
    SimulationSession, SimulationDecision, MarketDataBar,
    PaperPortfolio, PaperOrder, PaperFill, PaperPosition,
)
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

        # Bind the account these intents settle into. A submission with no linked
        # portfolio has no book of record, and the product refuses it by name
        # rather than inventing or silently picking one.
        resp = self.client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 100000.0,
            "currency": "USD",
            "simulation_session_id": session_id,
        })
        assert resp.status_code == 201, f"Portfolio binding failed: {resp.text}"
        self.portfolio_id = UUID_Type(resp.json()["id"])

        # Run the market clock onto the first bar. An intent needs a market time
        # to be decided against; submitting before anyone has observed a bar is
        # refused rather than priced off an unseen bar.
        resp = self.client.post(
            f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
        )
        assert resp.status_code == 200, f"First advance failed: {resp.text}"
        self.observed_date = resp.json()["session"]["current_snapshot_date"]

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

    def get_book_state(self):
        """The account rows a submission writes: orders, fills, cash.

        A duplicate that survived would double-charge the account, so the race is
        checked where the money moves, not only in the replay's decision rows.
        """
        db = self.SessionFactory()
        try:
            orders = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == self.portfolio_id
            ).all()
            order_ids = [o.id for o in orders]
            fills = (
                db.query(PaperFill)
                .filter(PaperFill.paper_order_id.in_(order_ids))
                .all()
                if order_ids
                else []
            )
            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == self.portfolio_id
            ).first()
            return {
                "order_count": len(orders),
                "pending_count": sum(1 for o in orders if o.status.value == "pending"),
                "order_ids": sorted(str(o.id) for o in orders),
                "fill_count": len(fills),
                "current_cash": round(float(portfolio.current_virtual_cash), 4),
                "positions": {
                    p.asset_id: float(p.quantity)
                    for p in db.query(PaperPosition)
                    .filter(PaperPosition.portfolio_id == self.portfolio_id)
                    .all()
                },
            }
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
            initial_book = harness.get_book_state()
            print(f"Initial state: version={initial_state['state_version']}, "
                  f"decisions={initial_state['decision_count']}")

            # Prepare identical requests. The intent is a SELL because the setup
            # advance let the replay's own buy-and-hold rule enter the position and
            # mirror it into the bound account; a second BUY on a held asset is
            # refused by the product, so it would never reach the order table.
            idem_key = f"c1_concurrent_{uuid4().hex}"
            payload = {
                "action": "sell",
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

            # Verify exactly one decision beyond the observed bar the setup
            # already walked onto.
            assert final_state['decision_count'] == initial_state['decision_count'] + 1, \
                f"Expected {initial_state['decision_count'] + 1} decisions, got {final_state['decision_count']}"

            # Verify state incremented once
            assert final_state['state_version'] == initial_state['state_version'] + 1, \
                f"Expected version {initial_state['state_version'] + 1}, got {final_state['state_version']}"

            # The account is where a surviving duplicate would double-charge, so
            # the race is checked there too: submission books nothing, so one
            # winner means one pending intent and an untouched book.
            book_after_race = harness.get_book_state()
            assert book_after_race['order_count'] == initial_book['order_count'] + 1, \
                (
                    f"Expected {initial_book['order_count'] + 1} orders "
                    f"(baseline + 1), got {book_after_race['order_count']}"
                )
            assert book_after_race['pending_count'] == initial_book['pending_count'] + 1, \
                f"A duplicate was executed at submit: {book_after_race}"
            assert book_after_race['fill_count'] == initial_book['fill_count'], \
                "Submission booked a fill; the market clock owns execution"
            assert book_after_race['positions'] == initial_book['positions'], \
                (
                    f"Pending order changed holdings: "
                    f"{initial_book['positions']} → {book_after_race['positions']}"
                )
            assert book_after_race['current_cash'] == initial_book['current_cash'], \
                f"Pending order moved cash: {initial_book['current_cash']} → {book_after_race['current_cash']}"

            print(f"Account after race: {book_after_race}")

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

            # Now let the market clock reach the intent: one order, one fill,
            # one cash movement. A second pending order left by the race would
            # book a second time here.
            advance = harness.client.post(
                f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
            )
            assert advance.status_code == 200, f"Advance failed: {advance.status_code} {advance.text}"

            book_after_clock = harness.get_book_state()
            assert book_after_clock['order_count'] == initial_book['order_count'] + 1, \
                f"Clock step created another order: {book_after_clock['order_count']}"
            assert book_after_clock['fill_count'] == initial_book['fill_count'] + 1, \
                (
                    f"Expected exactly one booked fill beyond the baseline "
                    f"{initial_book['fill_count']}, got {book_after_clock['fill_count']}"
                )
            assert book_after_clock['pending_count'] == initial_book['pending_count'], \
                "Order still pending after the clock passed its execution bar"
            assert book_after_clock['current_cash'] > initial_book['current_cash'], \
                f"Sale did not credit the account: {initial_book['current_cash']} → {book_after_clock['current_cash']}"
            sold_from = initial_book['positions'].get('US:AAPL', 0.0)
            assert book_after_clock['positions'].get('US:AAPL') == sold_from - 100.0, \
                (
                    "Holdings did not move by exactly one 100-share fill: "
                    f"{initial_book['positions']} → {book_after_clock['positions']}"
                )

            print(f"Account after clock: {book_after_clock}")

            print("\n✓ C1 PASS: Exactly one decision created, no duplicate effects")

        finally:
            # Only cleanup if workers finished successfully
            if not workers_timed_out[0]:
                harness.cleanup()
            else:
                print(f"\n⚠ Workers timed out. Retaining resources for inspection:")
                print(f"  Database: {harness.db_path}")
                print(f"  Temp dir: {harness.temp_dir}")


class TestC3RetryAfterLostResponse:
    """C3: a retry after the response was lost must replay, not re-execute.

    The same contract is only covered conditionally inside C1, behind
    ``if 409 in status_codes``, so it depends on winning a race. This test
    drives it sequentially: the first request commits, then an identical
    retry arrives carrying a now-stale ``expected_version``. The idempotency
    lookup runs before the mutable-state checks, so the retry must return the
    original decision instead of advancing the session twice.
    """

    def test_c3_identical_retry_replays_original_decision(self):
        harness = ConcurrencyTestHarness()
        try:
            print("\n=== C3: Retry After Lost Response ===")

            auth_headers = harness.create_user_and_auth()
            session_id = harness.create_session_with_data(auth_headers)
            initial_state = harness.get_session_state(session_id)
            initial_book = harness.get_book_state()

            idem_key = f"c3_replay_{uuid4().hex}"
            # SELL, as in C1: the replay's own rule holds the position, so a BUY
            # would be refused before it ever reached the order table.
            payload = {
                "action": "sell",
                "quantity": 100,
                "rationale": "C3 replay test",
                "idempotency_key": idem_key,
                "expected_version": initial_state["state_version"],
            }

            first = harness.client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json=payload,
            )
            assert first.status_code in (200, 201), f"First request failed: {first.status_code} {first.text}"
            first_decision = first.json()["decision"]

            after_commit = harness.get_session_state(session_id)
            assert after_commit["state_version"] == initial_state["state_version"] + 1

            retry = harness.client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json=payload,
            )
            assert retry.status_code in (200, 201), \
                f"Replay must not conflict on the stale version: {retry.status_code} {retry.text}"

            replayed = retry.json()["decision"]
            assert replayed["id"] == first_decision["id"], \
                f"Replay returned a new decision: {replayed['id']} != {first_decision['id']}"
            assert replayed["step_number"] == first_decision["step_number"]
            assert replayed["paper_cash"] == first_decision["paper_cash"]

            after_replay = harness.get_session_state(session_id)
            assert after_replay["state_version"] == after_commit["state_version"], \
                "Replay mutated session state"
            assert after_replay["total_decisions"] == after_commit["total_decisions"]
            assert after_replay["paper_equity"] == after_commit["paper_equity"], \
                "Replay re-priced the position"
            assert harness.get_session_state(session_id)["decision_count"] == initial_state["decision_count"] + 1, \
                "Replay inserted a second decision row"

            # A different payload under the same key is a conflict, not a replay.
            conflicting = harness.client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={**payload, "quantity": 500},
            )
            assert conflicting.status_code == 409, \
                f"Same key with a different payload must be rejected: {conflicting.status_code} {conflicting.text}"

            assert harness.get_session_state(session_id)["decision_count"] == initial_state["decision_count"] + 1, \
                "Rejected conflict still wrote a decision"

            # Negative control: the same stale version under a NEW key is rejected,
            # so the replay above only passed because the idempotency lookup
            # precedes the version check rather than because state never moved.
            stale_new_key = harness.client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={**payload, "idempotency_key": f"c3_control_{uuid4().hex}"},
            )
            assert stale_new_key.status_code == 409, \
                f"Stale expected_version under a new key must conflict: {stale_new_key.status_code} {stale_new_key.text}"

            # Same contract on the account: replaying the request must not queue a
            # second intent, or the clock would book two fills from one decision.
            book = harness.get_book_state()
            assert book['order_count'] == initial_book['order_count'] + 1, \
                f"Replay or conflict wrote a second order: {book['order_ids']}"
            assert book['pending_count'] == initial_book['pending_count'] + 1, f"{book}"
            assert book['fill_count'] == initial_book['fill_count'], \
                "Submission booked a fill; the market clock owns execution"
            assert book['positions'] == initial_book['positions'], \
                f"Pending order changed holdings: {initial_book['positions']} → {book['positions']}"
            assert book['current_cash'] == initial_book['current_cash'], \
                f"Pending order moved cash before execution: {initial_book['current_cash']} → {book['current_cash']}"

            print("\n✓ C3 PASS: Retry replayed the original decision with no second execution")
        finally:
            harness.cleanup()


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
            initial_book = harness.get_book_state()
            print(f"Initial state: version={initial_state['state_version']}, "
                  f"decisions={initial_state['decision_count']}")

            # Prepare two DIFFERENT requests with SAME expected_version. Both are
            # SELLs: the replay's rule already holds the position, so a BUY would
            # be refused before it ever reached the order table.
            idem_key_1 = f"c2_competing_1_{uuid4().hex}"
            payload_1 = {
                "action": "sell",
                "quantity": 100,
                "rationale": "C2 competitor 1",
                "idempotency_key": idem_key_1,
                "expected_version": initial_state['state_version']
            }

            idem_key_2 = f"c2_competing_2_{uuid4().hex}"
            payload_2 = {
                "action": "sell",
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

            # Verify exactly one decision beyond the observed bar the setup
            # already walked onto.
            assert final_state['decision_count'] == initial_state['decision_count'] + 1, \
                f"Expected {initial_state['decision_count'] + 1} decisions, got {final_state['decision_count']}"

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

            # The loser's intent was refused before it reached the book, so it
            # must not linger as a second pending order waiting for the clock.
            book_after_race = harness.get_book_state()
            assert book_after_race['order_count'] == initial_book['order_count'] + 1, \
                (
                    f"Expected {initial_book['order_count'] + 1} orders (baseline + winner), "
                    f"got {book_after_race['order_count']}"
                )
            assert book_after_race['pending_count'] == initial_book['pending_count'] + 1, \
                f"Competing intents both reached the book: {book_after_race}"
            assert book_after_race['fill_count'] == initial_book['fill_count'], \
                "Submission booked a fill; the market clock owns execution"
            assert book_after_race['positions'] == initial_book['positions']
            assert book_after_race['current_cash'] == initial_book['current_cash'], \
                f"Pending order moved cash: {initial_book['current_cash']} → {book_after_race['current_cash']}"

            print(f"Account after race: {book_after_race}")

            print(f"\n✓ C2 PASS: Exactly one winner, one stale-version conflict")

        finally:
            # Only cleanup if workers finished successfully
            if not workers_timed_out[0]:
                harness.cleanup()
            else:
                print(f"\n⚠ Workers timed out. Retaining resources for inspection:")
                print(f"  Database: {harness.db_path}")
                print(f"  Temp dir: {harness.temp_dir}")
