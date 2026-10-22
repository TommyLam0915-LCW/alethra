# C2 Stale-Step Race Condition - Fix Implementation

**Date:** 2026-10-22  
**Defect Severity:** HIGH  
**Status:** Code fix implemented, pending restart and testing

---

## Defect Summary

**Problem:** The manual decision endpoint does not implement step-level concurrency control. When two requests with different idempotency keys target the same session step concurrently, both succeed, creating duplicate decisions.

**Root Cause:** The endpoint reads session state, performs calculations, then writes back without atomic protection. Two concurrent requests can both read the same state and both write, causing race conditions.

**Impact:**
- Data integrity: Multiple decisions per step
- Accounting inconsistency: Cash/position calculations diverge
- Replay ambiguity: Session history contains conflicting state transitions

---

## Fix Implementation

### 1. Schema Change

**File:** `backend/app/models/simulation.py`

Added `state_version` field to `SimulationSession` model:

```python
class SimulationSession(Base):
    __tablename__ = "simulation_sessions"
    # ... existing fields ...
    total_decisions = Column(Integer, default=0)
    observed_days = Column(Integer, default=0)
    state_version = Column(Integer, default=0, nullable=False)  # NEW
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
```

### 2. Migration

**File:** `backend/migrations/versions/add_state_version_to_sessions.py`

```python
def upgrade():
    op.add_column('simulation_sessions', 
        sa.Column('state_version', sa.Integer(), nullable=False, server_default='0'))

def downgrade():
    op.drop_column('simulation_sessions', 'state_version')
```

**Status:** Migration created, NOT applied to shared DB (per authorization).

### 3. API Schema Update

**File:** `backend/app/api/schemas.py`

Added `expected_version` field to `ManualDecisionRequest`:

```python
class ManualDecisionRequest(BaseModel):
    action: str = Field(..., pattern="^(buy|sell|hold)$")
    quantity: Optional[int] = Field(None, ge=1)
    idempotency_key: str = Field(..., min_length=1, max_length=100)
    rationale: Optional[str] = Field(None, max_length=1000)
    reflection: Optional[str] = Field(None, max_length=2000)
    expected_version: Optional[int] = Field(None, ge=0, 
        description="Expected state version for optimistic locking")  # NEW
```

**Backward Compatibility:** Field is optional. If not provided, no version checking occurs (legacy behavior).

### 4. Atomic Update Implementation

**File:** `backend/app/api/simulations.py`

**Location:** `manual_decision` endpoint (line ~615)

**Changes:**

#### a. Version Validation (after ownership check)

```python
# Optimistic locking: check expected_version if provided
if payload.expected_version is not None:
    if payload.expected_version != session.state_version:
        raise HTTPException(
            status_code=409,
            detail=f"State version conflict: expected {payload.expected_version}, "
                   f"current {session.state_version}. Session has been modified."
        )
```

#### b. Atomic Conditional Update (replacing direct field updates)

**Before:**
```python
# Update session state
session.current_snapshot_date = next_date
session.paper_equity = round(current_equity, 4)
session.current_position_json = simulator._position_dict(observed_close)
session.total_decisions = step_number
session.observed_days = session.observed_days + 1
if session.status == SimulationStatus.pending:
    session.status = SimulationStatus.active
    session.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
```

**After:**
```python
# Update session state with optimistic locking
# Use atomic conditional update to prevent race conditions
current_version = session.state_version
update_result = db.execute(
    SimulationSession.__table__.update().where(
        (SimulationSession.__table__.c.id == session.id) &
        (SimulationSession.__table__.c.state_version == current_version)
    ).values(
        current_snapshot_date=next_date,
        paper_equity=round(current_equity, 4),
        current_position_json=simulator._position_dict(observed_close),
        total_decisions=step_number,
        observed_days=session.observed_days + 1,
        state_version=current_version + 1,
        status=SimulationStatus.active if session.status == SimulationStatus.pending else session.status,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None) if session.status == SimulationStatus.pending else session.started_at,
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )
)

# If update affected 0 rows, another request modified the session
if update_result.rowcount == 0:
    db.rollback()
    raise HTTPException(
        status_code=409,
        detail="State version conflict: session was modified by another request. Please reload and retry."
    )

# Refresh session to get updated values
db.refresh(session)
```

**Key Properties:**
- Atomic conditional UPDATE with WHERE clause
- Database-enforced protection (not Python-level check)
- Rolls back entire transaction on conflict
- Increments `state_version` atomically with state changes

---

## How It Works

### Normal Flow (No Conflict)

1. Client loads session, receives `state_version: 5`
2. Client submits decision with `expected_version: 5`
3. Server validates `expected_version == session.state_version` ✓
4. Server executes decision logic
5. Server performs atomic UPDATE: `WHERE id=? AND state_version=5`
6. UPDATE succeeds (rowcount=1), `state_version` becomes 6
7. Transaction commits
8. Client receives success response

### Conflict Scenario (Race Condition)

**Thread A and Thread B both read session at version 5**

1. Thread A: reads `state_version=5`
2. Thread B: reads `state_version=5`
3. Thread A: validates `expected_version=5 == state_version=5` ✓
4. Thread B: validates `expected_version=5 == state_version=5` ✓
5. Thread A: executes decision logic
6. Thread B: executes decision logic
7. Thread A: atomic UPDATE `WHERE state_version=5` → succeeds, version becomes 6
8. Thread B: atomic UPDATE `WHERE state_version=5` → **fails (rowcount=0)**
9. Thread B: rollback, return HTTP 409
10. Thread A: commit, return success

**Result:** Only one decision created, no duplicate state.

---

## Frontend Contract Update Required

### Current Behavior (Before Fix)

Frontend submits decisions without version tracking.

### Required Changes (After Fix)

1. **Load Session:**
   ```javascript
   const session = await api.getSimulation(sessionId);
   const currentVersion = session.state_version;  // NEW
   ```

2. **Submit Decision:**
   ```javascript
   const response = await api.manualDecision(sessionId, {
     action: 'buy',
     quantity: 100,
     idempotency_key: generateKey(),
     rationale: '...',
     expected_version: currentVersion  // NEW
   });
   ```

3. **Handle Conflict:**
   ```javascript
   if (response.status === 409) {
     // Reload session
     const updatedSession = await api.getSimulation(sessionId);
     // Show user-friendly message
     showError('Session was modified. Please review and retry.');
     // DO NOT automatically resubmit with old version
   }
   ```

4. **Idempotency Key Management:**
   - Use same key for retries of same intent
   - Generate new key for new intents
   - Store key with unsent rationale for recovery

---

## Testing Plan

### Isolated Test Database

**File:** `backend/alethra_test_c2_fix.db`

Created with:
- Schema includes `state_version` column
- Test user, experiment, version, and market data copied
- Ready for testing

### Test Cases

#### C2-1: Concurrent Different Keys (Original Defect)

**Setup:**
- Create session with `state_version=0`
- Two threads submit HOLD with different idempotency keys
- Both include `expected_version: 0`

**Expected:**
- Thread A: HTTP 200, decision created, `state_version` becomes 1
- Thread B: HTTP 409, no decision created
- Final: 1 decision, `state_version=1`

#### C2-2: Stale Intent After Advancement

**Setup:**
- Create session, execute step 1 (version 0 → 1)
- Submit step 2 with `expected_version: 0` (stale)

**Expected:**
- HTTP 409: "State version conflict: expected 0, current 1"
- No decision created

#### C2-3: Fresh Intent After Advancement

**Setup:**
- Create session, execute step 1 (version 0 → 1)
- Submit step 2 with `expected_version: 1` (current)

**Expected:**
- HTTP 200, decision created, `state_version` becomes 2

#### C2-4: Backward Compatibility (No expected_version)

**Setup:**
- Create session
- Submit decision WITHOUT `expected_version`

**Expected:**
- HTTP 200 (legacy behavior preserved)
- `state_version` incremented

#### Regression Tests

Re-run existing tests:
- C1: Concurrent same-key retry (idempotency)
- Same-key/changed-payload (409 conflict)
- C3: Committed result with lost response (retry)
- HOLD behavior
- Rejection behavior
- Provider isolation
- Ownership enforcement
- Reflection (does not advance trade state)

---

## Activation Prerequisites

### Required Steps (After Owner UI Testing)

1. **Stop Running Application:**
   ```bash
   # Identify and stop backend process
   pkill -f "uvicorn app.main:app"
   ```

2. **Apply Migration to Shared DB:**
   ```bash
   cd backend
   alembic upgrade head
   ```
   
   **Note:** Shared DB has manually modified schema. Verify migration applies cleanly.

3. **Restart Application:**
   ```bash
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

4. **Run Regression Tests:**
   ```bash
   python3 backend/tests/test_c1_c4_complete.py
   ```

5. **Verify C2 Fix:**
   - Run C2-1 through C2-4 test cases
   - Confirm HTTP 409 on conflict
   - Confirm no duplicate decisions

6. **Update Frontend:**
   - Add `expected_version` to decision submission
   - Implement conflict handling (409 → reload + message)
   - Test UI flow with concurrent tabs

---

## Files Modified

1. `backend/app/models/simulation.py` - Added `state_version` field
2. `backend/app/api/schemas.py` - Added `expected_version` to request
3. `backend/app/api/simulations.py` - Implemented atomic update
4. `backend/migrations/versions/add_state_version_to_sessions.py` - Migration (NEW)

---

## Risk Assessment

### Low Risk

- **Backward Compatibility:** `expected_version` is optional
- **Existing Sessions:** Default `state_version=0` works for all
- **Idempotency:** Existing mechanism unchanged
- **Accounting:** No changes to calculation logic

### Medium Risk

- **Migration:** Shared DB has manual schema changes
  - Mitigation: Test migration on isolated DB first
  - Mitigation: Backup before applying
  
- **Frontend:** Requires contract update
  - Mitigation: Optional field, legacy behavior preserved
  - Mitigation: Can deploy backend first, frontend later

### High Risk (Mitigated)

- **Race Condition:** Original defect
  - Mitigation: Atomic conditional UPDATE
  - Mitigation: Database-enforced protection
  - Mitigation: Comprehensive test coverage

---

## Conclusion

**Fix Status:** Code complete, migration ready  
**Testing Status:** Isolated DB prepared, tests pending restart  
**Deployment Status:** Blocked on owner UI testing completion  

**Next Steps:**
1. Owner completes UI testing
2. Apply migration to shared DB
3. Restart application
4. Run regression tests
5. Verify C2 fix
6. Update frontend contract
7. Retest affected UI flows

**Estimated Time:** 30-45 minutes for activation and testing
