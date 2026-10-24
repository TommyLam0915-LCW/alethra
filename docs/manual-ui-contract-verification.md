# Manual UI Contract Verification Report

**Date**: 2026-10-23  
**Status**: UI implemented; contract verification incomplete due to backend execution blocker

---

## 1. Backend Execution Environment

**Interpreter**: `/opt/homebrew/bin/python3` (system Python)  
**Error**: `ModuleNotFoundError: No module named 'sqlalchemy'`  
**Virtual Environment**: Not found (no `.venv` in backend directory)

**Conclusion**: Backend tests cannot be executed in current environment. Dependencies not installed. This is a **hard blocker** for backend contract verification.

---

## 2. Source Selection Implementation

### Critical Issue Found

**UI Implementation** (`frontend/src/app/experiments/[id]/simulation/page.tsx:355-373`):
```tsx
<select
  className="input"
  value={providerId}
  onChange={(e) => setProviderId(e.target.value)}
  style={{ width: '100%' }}
>
  <option value="">請選擇資料來源</option>
  <option value="twelvedata">Twelve Data（真實歷史資料）</option>
  <option value="demo">Demo（示範資料）</option>
</select>
```

**Problem**: Provider options are **hardcoded**, not queried from backend.

**Backend Contract** (`backend/app/api/simulations.py:290-330`):
- Backend queries `MarketDataBar` for available providers
- Validates specified provider has stored data
- Returns error if provider has no data: `"Provider '{provider_id}' has no stored data for {symbol}. Available: {available}"`
- Auto-selects if only one provider available
- Requires explicit selection if multiple providers exist

**Contract Mismatch**:
- UI shows "twelvedata" and "demo" as options regardless of availability
- User can select a provider that has no stored data
- Backend rejects at session creation time with 400 error
- UI doesn't pre-validate or show available providers

**Required Fix**: UI should either:
1. Query backend for available providers before showing dropdown, OR
2. Omit provider selection and let backend auto-select (if only one), showing error if multiple exist, OR
3. Show backend error clearly when creation fails with invalid provider

**Current Behavior**: User selects "twelvedata" → backend returns 400 "Provider 'twelvedata' has no stored data" → user must retry with different provider.

**Verdict**: ❌ **CONTRACT VIOLATION** — UI does not reflect backend's stored-data validation

---

## 3. Session Creation Payload

**Backend Schema** (`backend/app/api/schemas.py:190-192`):
```python
class SimulationSessionCreate(BaseModel):
    initial_virtual_cash: float = Field(..., gt=0)
    provider_id: Optional[str] = Field(None, description="Explicit provider selection")
```

**Frontend API Call** (`frontend/src/lib/api.ts:186-187`):
```typescript
create: (versionId: string, data: { initial_virtual_cash: number; provider_id?: string }) =>
  apiFetch(`/api/v1/simulations/versions/${versionId}`, { method: 'POST', body: JSON.stringify(data) }),
```

**Frontend Handler** (`frontend/src/app/experiments/[id]/simulation/page.tsx:166-186`):
```typescript
const handleCreate = async () => {
  const createData: { initial_virtual_cash: number; provider_id?: string } = {
    initial_virtual_cash: cash,
  };
  
  if (mode === 'manual' && providerId) {
    createData.provider_id = providerId;
  }
  
  const s = await api.simulations.create(experiment.current_version_id, createData);
  setSession(s);
};
```

**Verification**:
- ✅ Payload structure matches schema
- ✅ `provider_id` sent only in manual mode
- ✅ `provider_id` is optional (matches backend schema)
- ✅ Backend response includes `provider_id` and `provider_confirmed` fields
- ⚠️ No validation that selected provider has data (relies on backend error)

**Verdict**: ✅ **Payload contract correct**, but UX relies on backend error for validation

---

## 4. Manual Decision Payload

**Backend Schema** (`backend/app/api/schemas.py` — ManualDecisionRequest):
```python
class ManualDecisionRequest(BaseModel):
    action: str  # 'buy', 'sell', 'hold'
    quantity: Optional[int] = None
    rationale: Optional[str] = None
    idempotency_key: str
    expected_version: Optional[int] = None
```

**Frontend API Type** (`frontend/src/lib/api.ts:93-100`):
```typescript
export interface ManualDecisionRequest {
  action: 'buy' | 'sell' | 'hold';
  quantity?: number;
  idempotency_key: string;
  rationale?: string;
  reflection?: string;
  expected_version: number;
}
```

**Frontend Handler** (`frontend/src/app/experiments/[id]/simulation/page.tsx:214-231`):
```typescript
const payload: ManualDecisionRequest = {
  action: manualAction,
  quantity,
  rationale: manualRationale || undefined,
  idempotency_key: idempotencyKey,
  expected_version: session.state_version,
};

const result: ManualDecisionResponse = await api.simulations.manualDecision(session.id, payload);
```

**Verification**:
- ✅ `action` sent as lowercase string
- ✅ `quantity` sent only for BUY/SELL, undefined for HOLD
- ✅ `rationale` sent as optional string
- ✅ `idempotency_key` always sent
- ✅ `expected_version` sent from `session.state_version`
- ⚠️ Frontend type includes `reflection` field, but backend schema doesn't show it (may be in decision_detail)

**Verdict**: ✅ **Decision payload contract correct**

---

## 5. Idempotency Key Management

**Key Generation** (`frontend/src/lib/api.ts:117-119`):
```typescript
export function generateIdempotencyKey(): string {
  return `manual_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`;
}
```

**Key Retention Logic** (`frontend/src/app/experiments/[id]/simulation/page.tsx:207-213`):
```typescript
const idempotencyKey = pendingIdempotencyKey || generateIdempotencyKey();
setPendingIdempotencyKey(idempotencyKey);

try {
  // ... submit decision
  
  setPendingIdempotencyKey(null);  // Clear on success
} catch (err: any) {
  if (isIdempotencyConflict(err)) {
    setPendingIdempotencyKey(null);  // Clear on idempotency conflict
  }
  // Key retained on other errors (network timeout, etc.)
}
```

**Verification**:
- ✅ New key generated for each intent
- ✅ Same key retained across retries (pendingIdempotencyKey state)
- ✅ Key cleared after successful submission
- ✅ Key cleared after idempotency conflict (409 with same key, different payload)
- ✅ Key retained on network errors (allows retry with same intent)

**Verdict**: ✅ **Idempotency key management correct**

---

## 6. State Version Conflict Handling

**Conflict Detection** (`frontend/src/lib/api.ts:121-125`):
```typescript
export function isStateVersionConflict(error: any): boolean {
  return error instanceof ApiError && 
         error.status === 409 && 
         error.message.toLowerCase().includes('state version');
}
```

**Handler** (`frontend/src/app/experiments/[id]/simulation/page.tsx:243-247`):
```typescript
if (isStateVersionConflict(err)) {
  setError('狀態版本衝突：工作階段已被修改。請重新載入後再試。');
  await loadSession();  // Reload state without resubmitting
}
```

**Verification**:
- ✅ Detects 409 with "state version" message
- ✅ Shows clear error to user
- ✅ Reloads session state (gets new `state_version`)
- ✅ Does NOT automatically resubmit with old intent
- ✅ User must manually retry with new decision

**Verdict**: ✅ **State version conflict handling correct**

---

## 7. Idempotency Conflict Handling

**Conflict Detection** (`frontend/src/lib/api.ts:127-131`):
```typescript
export function isIdempotencyConflict(error: any): boolean {
  return error instanceof ApiError && 
         error.status === 409 && 
         error.message.toLowerCase().includes('idempotency key');
}
```

**Handler** (`frontend/src/app/experiments/[id]/simulation/page.tsx:248-251`):
```typescript
} else if (isIdempotencyConflict(err)) {
  setError('冪等性金鑰衝突：相同金鑰但不同內容。請使用新的金鑰重試。');
  setPendingIdempotencyKey(null);  // Clear key, user must create new intent
}
```

**Verification**:
- ✅ Detects 409 with "idempotency key" message
- ✅ Shows clear error to user
- ✅ Clears pending key (forces new key generation on next attempt)
- ✅ Does NOT automatically retry
- ✅ User must create new intent with new key

**Verdict**: ✅ **Idempotency conflict handling correct**

---

## 8. Reflection Endpoint Separation

**API Method** (`frontend/src/lib/api.ts:196-197`):
```typescript
updateReflection: (sessionId: string, decisionId: string, data: ReflectionUpdateRequest): Promise<ReflectionUpdateResponse> =>
  apiFetch(`/api/v1/simulations/${sessionId}/decisions/${decisionId}/reflection`, { method: 'PATCH', body: JSON.stringify(data) }),
```

**Handler** (`frontend/src/app/experiments/[id]/simulation/page.tsx:267-281`):
```typescript
const handleUpdateReflection = async (decisionId: string) => {
  await api.simulations.updateReflection(session.id, decisionId, {
    reflection: reflectionText.trim(),
  });
  
  const d = await api.simulations.decisions(session.id);  // Reload decisions
  setDecisions(d);
  setEditingReflection(null);
};
```

**Verification**:
- ✅ Uses separate PATCH endpoint (`/decisions/{id}/reflection`)
- ✅ Does NOT call manual-decision endpoint
- ✅ Does NOT modify trade state
- ✅ Reloads decisions after update
- ✅ Only available in manual mode (UI restriction)

**Verdict**: ✅ **Reflection endpoint correctly separated from decision execution**

---

## 9. Session Reopen State Preservation

**Session Loading** (`frontend/src/app/experiments/[id]/simulation/page.tsx:102-114`):
```typescript
const loadSession = useCallback(async () => {
  if (!session?.id) return;
  try {
    const [s, d] = await Promise.all([
      api.simulations.get(session.id),
      api.simulations.decisions(session.id),
    ]);
    setSession(s);  // Includes state_version, provider_id, provider_confirmed
    setDecisions(d);
  } catch (err: any) {
    setError(err.message);
  }
}, [session?.id]);
```

**Verification**:
- ✅ Reloads full session object (includes `state_version`, `provider_id`, `provider_confirmed`)
- ✅ Reloads all decisions
- ✅ Provider ID preserved in session object
- ✅ State version preserved and used for next decision
- ⚠️ Mode (automatic/manual) not persisted — user must re-select on page refresh

**Verdict**: ✅ **Session state correctly preserved on reopen**, but mode selection not persisted

---

## 10. Frontend Build Verification

**Command**: `cd frontend && npm run build`  
**Result**: ✅ PASS
- Compiled successfully
- Type checking passed
- All pages built without errors

**Verdict**: ✅ **Frontend compilation successful**

---

## 11. Backend Test Execution

**Command**: `cd backend && python3 -m pytest tests/test_c2_manual_decision_safety.py -v`  
**Result**: ❌ BLOCKED
- Error: `ModuleNotFoundError: No module named 'sqlalchemy'`
- System Python missing required dependencies
- No virtual environment found

**Verdict**: ❌ **Backend tests cannot be executed** — hard blocker

---

## Summary

| Contract Point | Status | Evidence |
|---|---|---|
| Source selection from stored-data query | ❌ VIOLATION | UI hardcodes twelvedata/demo options |
| Session create payload matches schema | ✅ PASS | Code inspection, payload structure correct |
| Provider/confirmation/interval saved | ⚠️ PARTIAL | Backend saves correctly, UI doesn't validate availability |
| Decision includes expected_version | ✅ PASS | Code inspection, session.state_version sent |
| Retry retains same key | ✅ PASS | Code inspection, pendingIdempotencyKey state |
| New intent uses new key | ✅ PASS | Code inspection, generateIdempotencyKey() called |
| 409 refreshes state without resubmission | ✅ PASS | Code inspection, loadSession() called on conflict |
| Successful execution updates version | ✅ PASS | Backend increments state_version, UI reloads |
| Reopen restores source/state/version | ✅ PASS | Code inspection, session object preserved |
| Reflection uses independent endpoint | ✅ PASS | Code inspection, PATCH /decisions/{id}/reflection |
| Frontend build | ✅ PASS | npm run build succeeded |
| Backend tests | ❌ BLOCKED | Missing SQLAlchemy dependency |

---

## Critical Issues

### 1. Provider Selection Hardcoded (CONTRACT VIOLATION)

**Severity**: HIGH  
**Location**: `frontend/src/app/experiments/[id]/simulation/page.tsx:355-373`

**Problem**: UI shows hardcoded provider options (twelvedata, demo) instead of querying backend for available providers with stored data.

**Impact**:
- User can select provider that has no stored data
- Backend rejects at session creation with 400 error
- Poor UX: user must retry with different provider
- Doesn't reflect actual data availability

**Required Fix**: Implement one of:
1. Query backend endpoint for available providers before showing dropdown
2. Remove provider selection from UI, let backend auto-select (show error if multiple exist)
3. Show backend error clearly and guide user to correct selection

**Backend Support**: Backend already validates provider availability and returns clear error messages. UI just needs to surface this better.

### 2. Backend Tests Not Executed (HARD BLOCKER)

**Severity**: HIGH  
**Location**: Backend execution environment

**Problem**: System Python missing SQLAlchemy dependency. No virtual environment found. Cannot execute backend tests.

**Impact**:
- Cannot verify backend schema changes work correctly
- Cannot verify C2 safety tests still pass
- Cannot verify end-to-end contract compliance
- Schema changes are backward-compatible (adding optional fields), but not tested

**Required Fix**: Install backend dependencies or provide working virtual environment.

---

## Conclusion

**UI Implementation**: ✅ Complete  
**Frontend Build**: ✅ PASS  
**Contract Compliance**: ⚠️ PARTIAL — provider selection violates stored-data contract  
**Backend Verification**: ❌ BLOCKED — cannot execute tests

**Status**: **UI 已實作、整合待驗證** (UI implemented, integration pending verification)

**Not "fully functional"**: Provider selection does not reflect backend's stored-data validation. Backend tests not executed.

**Activation Prerequisites**:
1. Fix provider selection to query available providers from backend (or remove UI selection and rely on backend auto-selection with clear error handling)
2. Install backend dependencies and verify C2 tests pass
3. Resolve migration graph issues (duplicate revision IDs) before shared database activation

---

**Report complete**. Stopping as instructed.
