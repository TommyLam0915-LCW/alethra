# Phase 1 Browser Acceptance Evidence Ledger

**Date**: 2026-10-20<br>
**Worktree**: `/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto`<br>
**Branch**: `auto/candidate-integration`<br>
**Frontend Tests**: 14 tests, ALL PASSED

## Evidence Inventory

### A. Core Navigation & Page Structure

| Scenario | Evidence | Status |
|----------|----------|--------|
| Login flow | Browser test: fill email/password, click login, redirect to dashboard | ✅ PASSED |
| Dashboard displays experiments | Browser test: verify 2 Journey Test Experiments visible | ✅ PASSED |
| Workspace navigation links | Browser test: verify 6 links (主頁, 研究, 訓練, 模型, 實驗, API, 設定) | ✅ PASSED |
| Settings page tabs | Browser test: verify 3 tabs (AI 設定, 工作台偏好, 用量資訊) | ✅ PASSED |

**Correction**: Navigation links count is **6** (not 7). Previous report incorrectly listed 7 items including 主頁. Actual links: 研究, 訓練, 模型, 實驗, API, 設定.

### B. Workspace Preferences Consumer Logic

| Scenario | Test Name | Evidence | Status |
|----------|-----------|----------|--------|
| Saved-state provenance | `sets hasSavedState=true when cache exists` | localStorage cache → hasSavedState=true | ✅ PASSED |
| Server layout provenance | `sets hasSavedState=true when server returns layout` | API returns layout → hasSavedState=true | ✅ PASSED |
| Default state | `starts with hasSavedState=false when no cache or server data` | No cache/server → hasSavedState=false | ✅ PASSED |
| Preference application | `applies preferences only when hasSavedState=false` | hasSavedState=false → apply preferences | ✅ PASSED |
| Explicit selection precedence | `does NOT apply preferences when hasSavedState=true` | hasSavedState=true → skip preferences | ✅ PASSED |
| Race condition safety | `delayed preference response cannot overwrite user selection after hasSavedState becomes true` | Delayed response → no overwrite | ✅ PASSED |
| Re-application prevention | `applies preferences only once (appliedRef prevents re-application)` | appliedRef → single application | ✅ PASSED |

### C. User Isolation & Session Management

| Scenario | Test Name | Evidence | Status |
|----------|-----------|----------|--------|
| User isolation | `user isolation: user A preferences do not affect user B` | Separate user contexts → isolated preferences | ✅ PASSED |
| Failed request handling | `handles failed layout load without setting hasSavedState` | Network error → hasSavedState remains false | ✅ PASSED |
| Preference load failure | `handles preference load failure gracefully` | API failure → graceful degradation | ✅ PASSED |
| Null response handling | `handles null preferences response` | Null response → no crash | ✅ PASSED |
| Partial preferences | `handles partial preferences safely` | Partial data → safe handling | ✅ PASSED |

### D. Preference Save & API Integration

| Scenario | Evidence | Status |
|----------|----------|--------|
| Save preferences via UI | Browser test: fill default_symbol="US:MSFT", click save | ✅ PASSED |
| API verification | GET /api/v1/layouts/workspace_preferences returns saved preferences | ✅ PASSED |
| Persistence | Preferences survive page reload (localStorage cache) | ✅ PASSED |

**API Evidence**:
```json
{
  "preferences": {
    "default_symbol": "US:MSFT",
    "default_range": "6M"
  },
  "updated_at": "2026-10-20T01:17:53.667011"
}
```

## Missing Scenarios

### 1. Reset Scope
**Status**: ❌ NOT TESTED<br>
**Requirement**: Verify that "reset preferences" does not affect other layout settings (panels, workspace structure).<br>
**Action Needed**: Add test case for reset button behavior.

### 2. Desktop/Mobile Interaction
**Status**: ❌ NOT TESTED<br>
**Requirement**: Verify preferences UI works correctly on mobile viewports (responsive design).<br>
**Action Needed**: Add browser test with mobile viewport (375x667).

### 3. Reload/Logout/Login Flow
**Status**: ⚠️ PARTIALLY TESTED<br>
**Evidence**:<br>
- ✅ Cache persistence verified (localStorage)
- ✅ hasSavedState tracking verified
- ❌ Full logout → login → preferences reload flow not tested in browser

**Action Needed**: Add end-to-end browser test:
1. Save preferences
2. Logout
3. Login as same user
4. Verify preferences loaded from server

### 4. Actual Preference Consumption in Workspace
**Status**: ⚠️ PARTIALLY TESTED<br>
**Evidence**:
- ✅ Preferences saved via API
- ✅ hasSavedState logic verified
- ❌ Workspace actually uses saved preferences (e.g., default_symbol appears in chart)

**Action Needed**: Add browser test:
1. Save default_symbol="US:MSFT"
2. Navigate to workspace
3. Verify chart displays US:MSFT

## Test Execution Summary

### Frontend Unit Tests
- **File**: `frontend/__tests__/components/workspace/preferences-consumer.test.tsx`
- **Tests**: 14
- **Passed**: 14
- **Failed**: 0
- **Duration**: ~2s

### Browser Acceptance Tests
- **Login**: ✅ PASSED
- **Dashboard**: ✅ PASSED
- **Workspace navigation**: ✅ PASSED (6 links)
- **Settings page**: ✅ PASSED (3 tabs)
- **Preference save**: ✅ PASSED
- **API verification**: ✅ PASSED

## Conclusion

**Phase 1 Status**: 🟡 CORE BROWSER FLOWS PASSED, FULL ACCEPTANCE PENDING

**Valid Evidence**:
- ✅ Login, navigation, page structure
- ✅ Preference save/load via API
- ✅ Saved-state provenance tracking
- ✅ User isolation
- ✅ Race condition safety

**Missing Evidence**:
- ❌ Reset scope
- ❌ Desktop/mobile interaction
- ⚠️ Full logout/login flow
- ⚠️ Actual preference consumption in workspace

**Recommendation**:<br>
1. Add reset scope test
2. Add mobile viewport test
3. Add end-to-end logout/login flow test
4. Add workspace preference consumption test

**Do not label Phase 1 as "fully verified" until missing scenarios are tested.**
