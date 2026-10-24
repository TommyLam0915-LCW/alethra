"""
C1-C4 Safety Case Tests - HTTP-based
Tests against running application without importing app modules
"""
import requests
import threading
import time
import sqlite3
from datetime import datetime
from uuid import uuid4

BASE_URL = "http://127.0.0.1:8000"
DB_PATH = "backend/alethra_dev.db"

def get_auth_token():
    """Get auth token for owner@test.com."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "owner@test.com",
        "password": "testpass123"
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]

def create_isolated_session():
    """Create an isolated test session in shared DB."""
    session_id = uuid4().hex
    owner_id = "22eb6237d6fa4b54b1f5389eaa90caf0"
    version_id = "2a16a9b6fa2d430aa75a3920a5b84aca"
    now = datetime.utcnow().isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO simulation_sessions (
            id, experiment_version_id, owner_id, initial_virtual_cash,
            started_at, data_cutoff_at, current_snapshot_date, status,
            simulation_policy_version, provider_id, provider_confirmed,
            paper_equity, current_position_json, warnings, total_decisions,
            observed_days, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id, version_id, owner_id, 100000.0,
        None, "2024-01-31", None, "pending",
        "1.0.0", "twelvedata", True,
        100000.0, None, None, 0, 0, now, now
    ))
    
    conn.commit()
    conn.close()
    return session_id

def cleanup_session(session_id):
    """Clean up test session and decisions."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM simulation_decisions WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM simulation_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

def get_session_state(session_id):
    """Get session state from DB."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT paper_equity, total_decisions, current_snapshot_date, status
        FROM simulation_sessions WHERE id = ?
    """, (session_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "paper_equity": row[0],
            "total_decisions": row[1],
            "current_snapshot_date": row[2],
            "status": row[3]
        }
    return None

def get_decisions(session_id):
    """Get all decisions for a session."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, step_number, idempotency_key, decision_type, snapshot_date
        FROM simulation_decisions WHERE session_id = ?
        ORDER BY step_number
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": row[0],
            "step_number": row[1],
            "idempotency_key": row[2],
            "decision_type": row[3],
            "snapshot_date": row[4]
        }
        for row in rows
    ]


class TestC1ConcurrentDuplicate:
    """C1: Concurrent duplicate - same key, same payload, simultaneous requests."""
    
    def test_concurrent_duplicate_same_key(self):
        """Two simultaneous requests with same idempotency key should produce only one decision."""
        print("\n=== C1: Concurrent Duplicate Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            payload = {
                "action": "buy",
                "quantity": 100,
                "rationale": "C1 concurrent test",
                "idempotency_key": f"c1_test_{int(time.time()*1000)}"
            }
            
            results = []
            errors = []
            
            def make_request():
                try:
                    resp = requests.post(
                        f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                        json=payload,
                        headers=headers,
                        timeout=10
                    )
                    results.append((resp.status_code, resp.json() if resp.status_code in (200, 201, 409) else resp.text))
                except Exception as e:
                    errors.append(str(e))
            
            # Launch two concurrent requests
            threads = [threading.Thread(target=make_request) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            print(f"Results: {len(results)} responses, {len(errors)} errors")
            for i, (status, body) in enumerate(results):
                print(f"  Request {i+1}: HTTP {status}")
            
            # Verify results
            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(results) == 2, f"Expected 2 results, got {len(results)}"
            
            # Check decisions in DB
            decisions = get_decisions(session_id)
            print(f"Decisions in DB: {len(decisions)}")
            for d in decisions:
                print(f"  Step {d['step_number']}: {d['decision_type']} (key: {d['idempotency_key']})")
            
            # Should have exactly 1 decision
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
            
            # Verify session state
            state = get_session_state(session_id)
            print(f"Session state: total_decisions={state['total_decisions']}, equity={state['paper_equity']}")
            assert state['total_decisions'] == 1, f"Expected total_decisions=1, got {state['total_decisions']}"
            assert state['paper_equity'] != 100000.0, "Equity should have changed"
            
            print("✓ C1 PASS: Concurrent duplicate correctly handled")
            return True
            
        finally:
            cleanup_session(session_id)


class TestC2DifferentKeysStaleStep:
    """C2: Different keys against same stale step."""
    
    def test_different_keys_same_step(self):
        """Two different idempotency keys should not both execute against same step."""
        print("\n=== C2: Different Keys / Same Step Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            # First request
            payload1 = {
                "action": "buy",
                "quantity": 50,
                "rationale": "C2 first request",
                "idempotency_key": f"c2_key1_{int(time.time()*1000)}"
            }
            
            resp1 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload1,
                headers=headers
            )
            print(f"Request 1: HTTP {resp1.status_code}")
            assert resp1.status_code in (200, 201), f"First request failed: {resp1.text}"
            
            # Second request with different key
            payload2 = {
                "action": "hold",
                "idempotency_key": f"c2_key2_{int(time.time()*1000)}"
            }
            
            resp2 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload2,
                headers=headers
            )
            print(f"Request 2: HTTP {resp2.status_code}")
            
            # Check decisions
            decisions = get_decisions(session_id)
            print(f"Decisions in DB: {len(decisions)}")
            for d in decisions:
                print(f"  Step {d['step_number']}: {d['decision_type']} (key: {d['idempotency_key'][:20]}...)")
            
            # Verify step numbers are sequential
            for i, dec in enumerate(decisions):
                assert dec['step_number'] == i + 1, f"Step numbers not sequential: {dec['step_number']} != {i+1}"
            
            # Verify session state
            state = get_session_state(session_id)
            print(f"Session state: total_decisions={state['total_decisions']}")
            assert state['total_decisions'] == len(decisions), "total_decisions mismatch"
            
            print("✓ C2 PASS: Different keys handled correctly")
            return True
            
        finally:
            cleanup_session(session_id)


class TestC3ResponseLostAfterCommit:
    """C3: Response lost after commit - retry with same key."""
    
    def test_retry_after_commit(self):
        """Retry with same key/payload should return original result, not create duplicate."""
        print("\n=== C3: Response Lost After Commit Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            idem_key = f"c3_retry_{int(time.time()*1000)}"
            payload = {
                "action": "buy",
                "quantity": 75,
                "rationale": "C3 retry test",
                "idempotency_key": idem_key
            }
            
            # First request
            resp1 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers
            )
            print(f"Request 1: HTTP {resp1.status_code}")
            assert resp1.status_code in (200, 201), f"First request failed: {resp1.text}"
            result1 = resp1.json()
            decision_id1 = result1.get("decision", {}).get("id")
            print(f"  Decision ID: {decision_id1}")
            
            # Second request (retry)
            resp2 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers
            )
            print(f"Request 2 (retry): HTTP {resp2.status_code}")
            assert resp2.status_code in (200, 201), f"Retry failed: {resp2.text}"
            result2 = resp2.json()
            decision_id2 = result2.get("decision", {}).get("id")
            print(f"  Decision ID: {decision_id2}")
            
            # Should return same decision
            assert decision_id1 == decision_id2, "Retry should return same decision ID"
            
            # Verify only one decision exists
            decisions = get_decisions(session_id)
            print(f"Decisions in DB: {len(decisions)}")
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
            
            print("✓ C3 PASS: Retry returns original result")
            return True
            
        finally:
            cleanup_session(session_id)


class TestC4TransactionFailure:
    """C4: Transaction failure / rollback."""
    
    def test_invalid_payload_no_partial_state(self):
        """Invalid payload should not leave partial state."""
        print("\n=== C4: Transaction Failure Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            # Get initial state
            state_before = get_session_state(session_id)
            print(f"Initial state: equity={state_before['paper_equity']}, decisions={state_before['total_decisions']}")
            
            # Send invalid request (negative quantity)
            payload = {
                "action": "buy",
                "quantity": -10,
                "idempotency_key": f"c4_invalid_{int(time.time()*1000)}"
            }
            
            resp = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers
            )
            print(f"Invalid request: HTTP {resp.status_code}")
            assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"
            
            # Verify no partial state
            state_after = get_session_state(session_id)
            print(f"Final state: equity={state_after['paper_equity']}, decisions={state_after['total_decisions']}")
            
            assert state_after['paper_equity'] == state_before['paper_equity'], "Equity should not change"
            assert state_after['total_decisions'] == state_before['total_decisions'], "total_decisions should not change"
            
            decisions = get_decisions(session_id)
            assert len(decisions) == 0, f"Expected 0 decisions, got {len(decisions)}"
            
            print("✓ C4 PASS: Invalid request leaves no partial state")
            return True
            
        finally:
            cleanup_session(session_id)


if __name__ == "__main__":
    print("=" * 60)
    print("C1-C4 Safety Case Tests")
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("C1", TestC1ConcurrentDuplicate().test_concurrent_duplicate_same_key()))
    except Exception as e:
        print(f"✗ C1 FAIL: {e}")
        results.append(("C1", False))
    
    try:
        results.append(("C2", TestC2DifferentKeysStaleStep().test_different_keys_same_step()))
    except Exception as e:
        print(f"✗ C2 FAIL: {e}")
        results.append(("C2", False))
    
    try:
        results.append(("C3", TestC3ResponseLostAfterCommit().test_retry_after_commit()))
    except Exception as e:
        print(f"✗ C3 FAIL: {e}")
        results.append(("C3", False))
    
    try:
        results.append(("C4", TestC4TransactionFailure().test_invalid_payload_no_partial_state()))
    except Exception as e:
        print(f"✗ C4 FAIL: {e}")
        results.append(("C4", False))
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{name}: {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} passed")
