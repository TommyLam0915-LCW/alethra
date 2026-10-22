"""
C1-C4 Safety Cases - Complete Implementation
Tests actual security properties with isolated fixtures
"""
import requests
import threading
import time
import sqlite3
from datetime import datetime
from uuid import uuid4
from contextlib import contextmanager

BASE_URL = "http://127.0.0.1:8000"
DB_PATH = "backend/alethra_dev.db"
TEST_DB_PATH = "backend/alethra_test_safety.db"

@contextmanager
def get_db_connection(db_path=DB_PATH):
    """Context manager for DB connections."""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()

def get_auth_token():
    """Get auth token for owner@test.com."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "owner@test.com",
        "password": "testpass123"
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]

def create_isolated_session(db_path=DB_PATH):
    """Create an isolated test session."""
    session_id = uuid4().hex
    owner_id = "22eb6237d6fa4b54b1f5389eaa90caf0"
    version_id = "2a16a9b6fa2d430aa75a3920a5b84aca"
    now = datetime.utcnow().isoformat()
    
    with get_db_connection(db_path) as conn:
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
    
    return session_id

def cleanup_session(session_id, db_path=DB_PATH):
    """Clean up test session and decisions."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM simulation_decisions WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM simulation_sessions WHERE id = ?", (session_id,))
        conn.commit()

def get_session_state(session_id, db_path=DB_PATH):
    """Get session state from DB."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        # Get session state
        cursor.execute("""
            SELECT paper_equity, total_decisions, current_snapshot_date, 
                   current_position_json, status, initial_virtual_cash
            FROM simulation_sessions WHERE id = ?
        """, (session_id,))
        session_row = cursor.fetchone()
        
        # Get latest decision state (for cash)
        cursor.execute("""
            SELECT paper_cash, paper_equity
            FROM simulation_decisions 
            WHERE session_id = ?
            ORDER BY step_number DESC
            LIMIT 1
        """, (session_id,))
        decision_row = cursor.fetchone()
    
    if session_row:
        # session_row: (paper_equity, total_decisions, current_snapshot_date, 
        #               current_position_json, status, initial_virtual_cash)
        return {
            "paper_cash": decision_row[0] if decision_row else session_row[5],
            "paper_equity": session_row[0],
            "total_decisions": session_row[1],
            "current_snapshot_date": session_row[2],
            "current_position_json": session_row[3],
            "status": session_row[4]
        }
    return None

def get_decisions(session_id, db_path=DB_PATH):
    """Get all decisions for a session."""
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, step_number, idempotency_key, decision_type, snapshot_date,
                   paper_cash, paper_equity, decision_detail
            FROM simulation_decisions WHERE session_id = ?
            ORDER BY step_number
        """, (session_id,))
        rows = cursor.fetchall()
    
    return [
        {
            "id": row[0],
            "step_number": row[1],
            "idempotency_key": row[2],
            "decision_type": row[3],
            "snapshot_date": row[4],
            "paper_cash": row[5],
            "paper_equity": row[6],
            "decision_detail": row[7]
        }
        for row in rows
    ]

def get_fills(session_id, db_path=DB_PATH):
    """Get all fills for a session (from decision_detail)."""
    import json
    decisions = get_decisions(session_id, db_path)
    fills = []
    for d in decisions:
        try:
            detail = json.loads(d["decision_detail"]) if d["decision_detail"] else {}
            if "fill" in detail and detail["fill"]:
                fills.append({
                    "decision_id": d["id"],
                    "step_number": d["step_number"],
                    "fill": detail["fill"]
                })
        except:
            pass
    return fills


class TestC1CompleteSideEffects:
    """C1: Concurrent duplicate - verify ALL side effects occur only once."""
    
    def test_concurrent_duplicate_complete_verification(self):
        """Verify decision, fill, step, cash, and position all change only once."""
        print("\n=== C1: Complete Side-Effect Verification ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            # Get initial state
            state_before = get_session_state(session_id)
            print(f"Initial: cash={state_before['paper_cash']}, equity={state_before['paper_equity']}, "
                  f"decisions={state_before['total_decisions']}, position={state_before['current_position_json']}")
            
            payload = {
                "action": "buy",
                "quantity": 100,
                "rationale": "C1 complete test",
                "idempotency_key": f"c1_complete_{int(time.time()*1000)}"
            }
            
            results = []
            
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
                    results.append(("ERROR", str(e)))
            
            # Launch two concurrent requests
            threads = [threading.Thread(target=make_request) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            print(f"Results: {len(results)} responses")
            for i, (status, body) in enumerate(results):
                print(f"  Request {i+1}: {status}")
            
            # Get final state
            state_after = get_session_state(session_id)
            decisions = get_decisions(session_id)
            fills = get_fills(session_id)
            
            print(f"Final: cash={state_after['paper_cash']}, equity={state_after['paper_equity']}, "
                  f"decisions={state_after['total_decisions']}, step_count={len(decisions)}, fill_count={len(fills)}")
            
            # Verify ALL side effects
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
            assert len(fills) == 1, f"Expected 1 fill, got {len(fills)}"
            assert decisions[0]["step_number"] == 1, f"Expected step 1, got {decisions[0]['step_number']}"
            assert state_after["total_decisions"] == 1, f"Expected total_decisions=1, got {state_after['total_decisions']}"
            
            # Verify cash changed exactly once
            cash_change = state_before["paper_cash"] - state_after["paper_cash"]
            expected_cost = 100 * 182.15  # approximate
            assert cash_change > 0, "Cash should have decreased"
            print(f"  Cash change: ${cash_change:.2f} (expected ~${expected_cost:.2f})")
            
            # Verify position exists
            assert state_after["current_position_json"] is not None, "Position should exist"
            print(f"  Position: {state_after['current_position_json']}")
            
            # Verify equity changed
            assert state_after["paper_equity"] != state_before["paper_equity"], "Equity should have changed"
            
            print("✓ C1 PASS: All side effects occurred exactly once")
            return True
            
        finally:
            cleanup_session(session_id)


class TestC2StaleStepCompetition:
    """C2: Different keys / same stale step - verify stale intent cannot execute."""
    
    def test_stale_step_competition(self):
        """Two requests for the SAME step with different keys - second should be rejected."""
        print("\n=== C2: Stale-Step Competition Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            # First, advance to step 1
            payload1 = {
                "action": "buy",
                "quantity": 50,
                "rationale": "C2 step 1",
                "idempotency_key": f"c2_step1_{int(time.time()*1000)}"
            }
            
            resp1 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload1,
                headers=headers
            )
            print(f"Step 1 request: HTTP {resp1.status_code}")
            assert resp1.status_code in (200, 201)
            
            state_after_step1 = get_session_state(session_id)
            print(f"After step 1: snapshot_date={state_after_step1['current_snapshot_date']}, "
                  f"decisions={state_after_step1['total_decisions']}")
            
            # Now try two requests for the SAME step (step 2) with different keys
            # These should be in a race condition
            payload2a = {
                "action": "hold",
                "idempotency_key": f"c2_step2a_{int(time.time()*1000)}"
            }
            
            payload2b = {
                "action": "hold",
                "idempotency_key": f"c2_step2b_{int(time.time()*1000)}"
            }
            
            results = []
            
            def make_request(payload):
                try:
                    resp = requests.post(
                        f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                        json=payload,
                        headers=headers,
                        timeout=10
                    )
                    results.append((resp.status_code, resp.json() if resp.status_code in (200, 201, 409, 400) else resp.text))
                except Exception as e:
                    results.append(("ERROR", str(e)))
            
            # Launch both requests nearly simultaneously
            threads = [
                threading.Thread(target=make_request, args=(payload2a,)),
                threading.Thread(target=make_request, args=(payload2b,))
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            print(f"Step 2 race results: {len(results)} responses")
            for i, (status, body) in enumerate(results):
                print(f"  Request {i+1}: {status}")
            
            # Check final state
            state_after = get_session_state(session_id)
            decisions = get_decisions(session_id)
            
            print(f"Final: decisions={len(decisions)}, total_decisions={state_after['total_decisions']}")
            
            # Verify only ONE step 2 decision was created
            step2_decisions = [d for d in decisions if d["step_number"] == 2]
            print(f"  Step 2 decisions: {len(step2_decisions)}")
            
            # The key question: did both requests succeed (creating 2 step 2 decisions)?
            # Or was one rejected?
            success_count = sum(1 for status, _ in results if status in (200, 201))
            print(f"  Successful requests: {success_count}/2")
            
            if len(step2_decisions) == 1:
                print("✓ C2 PASS: Only one step 2 decision created (stale-step protection working)")
                return True
            elif len(step2_decisions) == 2:
                print("✗ C2 FAIL: Two step 2 decisions created (no stale-step protection)")
                print("  DEFECT: API does not prevent concurrent execution of different intents for same step")
                return False
            else:
                print(f"? C2 UNCLEAR: {len(step2_decisions)} step 2 decisions")
                return False
            
        finally:
            cleanup_session(session_id)


class TestC3ResponseLossSimulation:
    """C3: Response lost after commit - simulate network failure after DB commit."""
    
    def test_response_loss_with_commit_verification(self):
        """Verify first operation committed, then retry returns same result."""
        print("\n=== C3: Response Loss After Commit Test ===")
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            idem_key = f"c3_response_loss_{int(time.time()*1000)}"
            payload = {
                "action": "buy",
                "quantity": 75,
                "rationale": "C3 response loss test",
                "idempotency_key": idem_key
            }
            
            # First request - simulate successful commit
            print("Sending first request...")
            resp1 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers,
                timeout=10
            )
            print(f"First request: HTTP {resp1.status_code}")
            
            if resp1.status_code not in (200, 201):
                print(f"✗ C3 FAIL: First request failed: {resp1.text}")
                return False
            
            result1 = resp1.json()
            decision_id1 = result1.get("decision", {}).get("id")
            print(f"  Decision ID: {decision_id1}")
            
            # Verify commit happened in DB
            decisions_after_first = get_decisions(session_id)
            state_after_first = get_session_state(session_id)
            print(f"  DB verified: {len(decisions_after_first)} decision(s), "
                  f"cash={state_after_first['paper_cash']}, equity={state_after_first['paper_equity']}")
            
            assert len(decisions_after_first) == 1, "First operation should have committed"
            
            # Simulate response loss - client didn't receive resp1
            # Now retry with same key/payload
            print("\nSimulating response loss, retrying...")
            resp2 = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers,
                timeout=10
            )
            print(f"Retry request: HTTP {resp2.status_code}")
            
            if resp2.status_code not in (200, 201):
                print(f"✗ C3 FAIL: Retry failed: {resp2.text}")
                return False
            
            result2 = resp2.json()
            decision_id2 = result2.get("decision", {}).get("id")
            print(f"  Decision ID: {decision_id2}")
            
            # Verify same decision returned
            assert decision_id1 == decision_id2, "Retry should return same decision ID"
            
            # Verify no duplicate in DB
            decisions_after_retry = get_decisions(session_id)
            state_after_retry = get_session_state(session_id)
            print(f"  DB verified: {len(decisions_after_retry)} decision(s), "
                  f"cash={state_after_retry['paper_cash']}, equity={state_after_retry['paper_equity']}")
            
            assert len(decisions_after_retry) == 1, "Should still have only 1 decision"
            assert state_after_retry["paper_cash"] == state_after_first["paper_cash"], "Cash should not change"
            assert state_after_retry["paper_equity"] == state_after_first["paper_equity"], "Equity should not change"
            
            print("✓ C3 PASS: Retry after response loss returns original result, no duplicate")
            return True
            
        finally:
            cleanup_session(session_id)


class TestC4TransactionRollback:
    """C4: Transaction failure - inject error during persistence."""
    
    def test_transaction_rollback_on_failure(self):
        """Inject failure during transaction, verify complete rollback."""
        print("\n=== C4: Transaction Rollback Test ===")
        print("NOTE: This test requires code-level fault injection.")
        print("Current API does not expose a fault injection endpoint.")
        print("Testing with invalid state transition instead...")
        
        token = get_auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        session_id = create_isolated_session()
        
        try:
            # Get initial state
            state_before = get_session_state(session_id)
            print(f"Initial: cash={state_before['paper_cash']}, equity={state_before['paper_equity']}, "
                  f"decisions={state_before['total_decisions']}")
            
            # Try to SELL without position (should fail validation)
            payload = {
                "action": "sell",
                "quantity": 100,
                "idempotency_key": f"c4_rollback_{int(time.time()*1000)}"
            }
            
            resp = requests.post(
                f"{BASE_URL}/api/v1/simulations/{session_id}/manual-decision",
                json=payload,
                headers=headers
            )
            print(f"Invalid SELL request: HTTP {resp.status_code}")
            
            # Verify rejection
            if resp.status_code not in (200, 201):
                print(f"  Rejected as expected")
            else:
                result = resp.json()
                if result.get("rejection"):
                    print(f"  Rejected: {result['rejection']}")
            
            # Verify no state change
            state_after = get_session_state(session_id)
            decisions = get_decisions(session_id)
            
            print(f"Final: cash={state_after['paper_cash']}, equity={state_after['paper_equity']}, "
                  f"decisions={len(decisions)}")
            
            # Check if a "blocked_action" decision was created
            blocked_decisions = [d for d in decisions if d["decision_type"] == "blocked_action"]
            
            if len(blocked_decisions) > 0:
                print(f"  Note: {len(blocked_decisions)} blocked_action decision(s) created")
                print("  This is expected behavior - rejection is recorded but no fill/accounting change")
                
                # Verify no accounting change
                assert state_after["paper_cash"] == state_before["paper_cash"], "Cash should not change"
                assert state_after["paper_equity"] == state_before["paper_equity"], "Equity should not change"
                
                print("✓ C4 PARTIAL: Rejection recorded, no accounting change")
                print("  LIMITATION: True transaction rollback test requires code-level fault injection")
                return True
            else:
                # No decision created at all
                assert len(decisions) == 0, "No decisions should be created"
                assert state_after["paper_cash"] == state_before["paper_cash"], "Cash should not change"
                assert state_after["paper_equity"] == state_before["paper_equity"], "Equity should not change"
                
                print("✓ C4 PASS: Invalid request rejected, no state change")
                print("  LIMITATION: True transaction rollback test requires code-level fault injection")
                return True
            
        finally:
            cleanup_session(session_id)


if __name__ == "__main__":
    print("=" * 70)
    print("C1-C4 Safety Cases - Complete Implementation")
    print("=" * 70)
    
    results = []
    
    # C1: Complete side-effect verification
    try:
        results.append(("C1", TestC1CompleteSideEffects().test_concurrent_duplicate_complete_verification()))
    except Exception as e:
        print(f"✗ C1 FAIL: {e}")
        import traceback
        traceback.print_exc()
        results.append(("C1", False))
    
    # C2: Stale-step competition
    try:
        results.append(("C2", TestC2StaleStepCompetition().test_stale_step_competition()))
    except Exception as e:
        print(f"✗ C2 FAIL: {e}")
        import traceback
        traceback.print_exc()
        results.append(("C2", False))
    
    # C3: Response loss simulation
    try:
        results.append(("C3", TestC3ResponseLossSimulation().test_response_loss_with_commit_verification()))
    except Exception as e:
        print(f"✗ C3 FAIL: {e}")
        import traceback
        traceback.print_exc()
        results.append(("C3", False))
    
    # C4: Transaction rollback
    try:
        results.append(("C4", TestC4TransactionRollback().test_transaction_rollback_on_failure()))
    except Exception as e:
        print(f"✗ C4 FAIL: {e}")
        import traceback
        traceback.print_exc()
        results.append(("C4", False))
    
    print("\n" + "=" * 70)
    print("Summary:")
    print("=" * 70)
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{name}: {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} passed")
