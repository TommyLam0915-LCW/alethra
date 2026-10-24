# Runtime Gate Replay Fix — Completion Report

**Date:** 2026-10-20  
**Branch:** feat/real-integrations  
**Status:** PARTIAL → Pending fill replay VERIFIED; overall system still PARTIAL

---

## Summary

Successfully identified and fixed the root cause of agent 2 replay failure, added targeted regression tests, strengthened runtime gate assertions, and verified the complete recovery scenario with genuine separate processes.

---

## Completed Scope

### 1. Root Cause Analysis ✓

**Product Code Defect:**
- **File:** `backend/app/execution_agent/agent.py:175`
- **Issue:** `_replay_pending_fill_deliveries()` called non-existent `self.alethra_client.upload_fill()` method
- **Fix:** Changed to `self.alethra_client.report_fill(FillReport(...))` with proper datetime conversion
- **Impact:** Recovery path was completely broken; pending fills could never be replayed

**Test Script Defect:**
- **File:** `backend/runtime_gate.py:164-181`
- **Issue:** `check_server_fills()` queried non-existent `filled_quantity` field on OrderIntent
- **Fix:** Changed to query `/fills` endpoint and sum quantities from FillRecords
- **Impact:** Runtime gate was checking wrong API contract; masked the product defect

### 2. Targeted Regression Tests ✓

**File:** `backend/tests/test_execution_recovery.py`  
**Class:** `TestFillDeliveryReplay` (3 new tests)

1. **test_replay_calls_report_fill_with_fill_report**
   - Verifies replay uses real `AlethraAPIClient.report_fill()` interface
   - Uses constrained mock that validates FillReport object structure
   - Would fail if someone reintroduces `upload_fill()` method call
   - Verifies successful acknowledgement removes pending entry

2. **test_replay_preserves_pending_on_failure**
   - Verifies failed reporting preserves pending fill in journal
   - Simulates network error (ConnectionError)
   - Verifies retry count is incremented
   - Ensures durability across failures

3. **test_replay_would_fail_with_upload_fill_method**
   - Documents the bug that was fixed
   - Uses mock that raises AttributeError if `upload_fill()` is called
   - Ensures regression if bug is reintroduced

**Test Results:**
```
tests/test_execution_recovery.py::TestFillDeliveryReplay::test_replay_calls_report_fill_with_fill_report PASSED
tests/test_execution_recovery.py::TestFillDeliveryReplay::test_replay_preserves_pending_on_failure PASSED
tests/test_execution_recovery.py::TestFillDeliveryReplay::test_replay_would_fail_with_upload_fill_method PASSED
```

### 3. Strengthened Runtime Gate Assertions ✓

**File:** `backend/runtime_gate.py`

**Enhanced `check_server_fills()` to return:**
- `status`: Intent status
- `broker_ref`: Broker order reference
- `fill_count`: Number of fill records
- `filled_quantity`: Sum of fill quantities
- `fills`: Individual fill records

**Verification now asserts:**
1. ✓ fill_count == 1 (exactly one fill, not just quantity sum)
2. ✓ filled_quantity == 10
3. ✓ broker_ref matches expected pattern (FAKE-*)
4. ✓ Individual fill record identity verified (quantity, broker_ref match)
5. ✓ status == filled
6. ✓ journal pending count == 0
7. ✓ broker submit count == 1 (inferred from fill_count)

**Evidence tracking:**
- Logs runtime gate start time, port, journal path, database
- Logs all PIDs (server, agent 1, agent 2)
- Logs detailed final state before verification

### 4. Runtime Gate Execution ✓

**Command:**
```bash
cd backend && source venv/bin/activate && python runtime_gate.py
```

**Results:**
```
======================================================================
VERIFICATION
======================================================================
✓ PASS: fill_count=1 (exactly one fill)
✓ PASS: filled_quantity=10
✓ PASS: broker_ref=FAKE-000001
✓ PASS: fill record identity verified
✓ PASS: status=filled
✓ PASS: journal empty (all fills delivered)
✓ PASS: broker submit count=1 (inferred from fill_count)

======================================================================
RUNTIME GATE: PASS
Recovery scenario verified with genuine separate processes
======================================================================
```

**Scenario verified:**
- Agent 1 (PID 72026): submits order → fill reported → upload fails → journal retains pending fill → process stops
- Agent 2 (PID 72106): starts → loads journal → replays pending fill → upload succeeds → journal empty → server shows filled_quantity=10, status=filled

### 5. Regression Test Suite ✓

**Command:**
```bash
python -m pytest tests/test_execution_recovery.py tests/test_fill_journal_requirements.py tests/test_journey_c_execution.py -v
```

**Results:**
```
66 passed, 180 warnings in 14.73s
```

**Test breakdown:**
- test_execution_recovery.py: 40 tests (including 3 new replay tests)
- test_fill_journal_requirements.py: 20 tests
- test_journey_c_execution.py: 6 tests

---

## Remaining Scope

### 1. Acknowledgement Loss Scenario (NOT VERIFIED)

**What we tested:**
- Reporting failed before successful delivery → restart → pending-fill replay

**What we haven't tested:**
- Server applied fill → acknowledgement lost → restart/replay without duplicate accounting

**Why it matters:**
- The idempotency guarantee (same broker_ref + fill_time + quantity → deduplicate) is implemented but not verified in a fresh-process scenario
- Current tests use TestClient (same process), not genuine separate processes

**Action required:**
- Execute separate scenario with:
  1. Agent 1: submit order → fill reported → upload succeeds → server records fill → ack lost before journal removal
  2. Agent 2: starts → journal still has pending fill → replays → server deduplicates → no double-count
- OR: Reuse valid evidence from existing tests if they already cover this path

### 2. Cancellation Report Recovery (NOT VERIFIED)

**What we tested:**
- Fill delivery replay across restart

**What we haven't tested:**
- Cancellation report failure → retry → success (requirement 7)

**Why it matters:**
- Requirement 7 explicitly covers cancellation reporting, not just fill reporting
- Test `test_platform_cancel_report_failure_retries_until_success` exists but uses TestClient (same process)

**Action required:**
- Execute fresh-process scenario with:
  1. Agent 1: cancel order at broker → platform report fails → journal retains pending cancel → process stops
  2. Agent 2: starts → loads journal → replays pending cancel → upload succeeds → server shows status=cancelled

### 3. Position/Cash Reconciliation (NOT VERIFIED)

**What we tested:**
- Fill delivery and intent status

**What we haven't tested:**
- Paper portfolio position and cash updates after fill replay

**Why it matters:**
- The runtime gate doesn't verify that the paper portfolio was updated correctly
- Position/cash reconciliation is a separate concern from fill delivery

**Action required:**
- Add assertions to runtime gate:
  - Query paper portfolio positions after replay
  - Verify position quantity matches filled_quantity
  - Verify cash deduction matches fill price × quantity + commission
- OR: Keep limitation explicit in documentation

### 4. Phase 1 UI Acceptance (PENDING)

**What's missing:**
- Actual preference consumption (not just save to DB)
- Reset scope verification
- Reload/logout/login flow
- Second-user isolation
- Desktop/mobile interaction with screenshots

**Action required:**
- Execute Phase 1 UI gates with browser automation
- Capture screenshots for evidence
- Verify preferences actually affect behavior, not just persist

---

## Changed File Inventory

### Product Code
1. `backend/app/execution_agent/agent.py`
   - Lines 154-208: Fixed `_replay_pending_fill_deliveries()` to use `report_fill(FillReport(...))`
   - Added datetime conversion for fill_time field
   - **Lines changed:** ~35 lines

### Test Code
2. `backend/tests/test_execution_recovery.py`
   - Lines 724-921: Added `TestFillDeliveryReplay` class with 3 new tests
   - **Lines added:** ~200 lines

### Test Infrastructure
3. `backend/runtime_gate.py`
   - Lines 164-195: Enhanced `check_server_fills()` to return detailed fill information
   - Lines 186-195: Updated first call site to handle dict return
   - Lines 277-360: Strengthened verification section with 7 assertions
   - Lines 186-195: Added evidence tracking (timestamps, PIDs, paths)
   - **Lines changed:** ~80 lines

### Test Scripts
4. `backend/runtime_gate_agent.py`
   - No changes (already correct)

---

## Proposed Commit Boundaries

### Commit 1: Fix replay path interface mismatch
**Files:**
- `backend/app/execution_agent/agent.py` (product fix)
- `backend/tests/test_execution_recovery.py` (regression tests)

**Message:**
```
fix: correct fill replay path to use report_fill() interface

The _replay_pending_fill_deliveries() method was calling a non-existent
upload_fill() method, which passed unit tests (permissive mocks) but
failed in production (real AlethraAPIClient).

Changed to use report_fill(FillReport(...)) with proper datetime
conversion. Added 3 regression tests with constrained mocks that
validate the interface and would fail if the bug is reintroduced.

Fixes runtime gate failure where agent 2 could not replay pending
fills from journal after agent 1 stopped.
```

### Commit 2: Strengthen runtime gate assertions
**Files:**
- `backend/runtime_gate.py`

**Message:**
```
test: strengthen runtime gate to verify fill identity and count

Enhanced check_server_fills() to query /fills endpoint and return
detailed fill information (count, quantity, broker_ref, individual
records). Updated verification to assert:
- fill_count == 1 (not just quantity sum)
- filled_quantity == 10
- broker_ref matches expected pattern
- individual fill record identity
- status == filled
- journal pending count == 0
- broker submit count == 1 (inferred)

Added evidence tracking (timestamps, PIDs, paths) for auditability.
This catches API contract mismatches that the previous version missed.
```

---

## Honesty Statement

### What is VERIFIED:
- ✓ Pending fill replay across genuine separate processes
- ✓ Journal durability (file-based, atomic writes, fsync)
- ✓ Fill delivery idempotency (unit tests, not fresh-process)
- ✓ Fill identity tracking (broker_ref, fill_time, quantity)
- ✓ Intent status transitions (submitted → filled)
- ✓ Regression tests with constrained mocks

### What is NOT VERIFIED:
- ✗ Acknowledgement loss scenario (server applied fill → ack lost → replay without duplicate)
- ✗ Cancellation report recovery across fresh processes
- ✗ Position/cash reconciliation after fill replay
- ✗ Phase 1 UI acceptance (preferences, reset, logout/login, isolation)

### Durability Guarantee:
已實作檔案同步（atomic writes + fsync），保證範圍依檔案、目錄同步與儲存環境而定。

### Test Coverage:
- Unit tests: 66 passed (test_execution_recovery, test_fill_journal_requirements, test_journey_c_execution)
- Integration tests: All use TestClient (same process) or FakeBrokerTransport
- Fresh-process tests: Runtime gate (genuine separate processes) — PASS

---

## Next Steps

1. **Owner review:** Review changed files and proposed commit boundaries
2. **Decision:** Commit locally or request changes
3. **Remaining gates:**
   - Execute acknowledgement loss scenario (if not already covered)
   - Execute cancellation report recovery scenario
   - Add position/cash reconciliation to runtime gate (or document limitation)
   - Complete Phase 1 UI acceptance with screenshots
4. **Final status:** Upgrade from PARTIAL only after all gates pass

---

## Evidence Artifacts

- Runtime gate output: See console output above
- Test results: 66 passed, 180 warnings in 14.73s
- PIDs: Server 72002, Agent 1 72026, Agent 2 72106
- Timestamp: 2026-10-20 16:47:09
- Database: sqlite:///./runtime-gate.db (cleaned up after run)
- Journal: /tmp/alethra-fill-journal-runtime-gate.json (cleaned up after run)
