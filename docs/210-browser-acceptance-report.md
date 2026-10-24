# #210 Browser Acceptance Report — Hybrid Autonomous Mode

**Date**: 2026-10-15  
**Branch**: feat/product-backbone  
**HEAD**: ffb0d60  
**Mode**: Autonomous hybrid execution

---

## Executive Summary

**Status**: #210 PARTIAL — PRECISE CONTINUATION REQUIRED

**Result**: 10/21 browser tests passed (48%), 9 failed, 2 skipped

**Completed Workflows**: B (partial), C (partial), D (partial), E (partial), F (partial), G (mostly)

**Blocked Workflows**: A (API key creation), some API operations

---

## Environment

### Acceptance Stack
- **Frontend**: http://localhost:3001 (Docker: alethra-acceptance-frontend-1)
- **Backend**: http://localhost:8001 (Docker: alethra-acceptance-backend-1)
- **PostgreSQL**: localhost:5433 (Docker: alethra-acceptance-db-1)
- **Status**: All containers running and accessible (HTTP 200)

### Test Infrastructure
- **Framework**: Playwright 1.64.0
- **Browser**: Chromium 156.0.8078.4
- **Test file**: `frontend/e2e/backbone-workflows.spec.ts`
- **Total tests**: 21
- **Execution time**: ~3 minutes

---

## Seven-Workflow Acceptance Matrix

### A. API Keys Workflow — BLOCKED

| Test | Status | Notes |
|------|--------|-------|
| A1. Create API key | ❌ FAILED | Button disabled — prerequisite not met |
| A2. List API keys | ❌ FAILED | Depends on A1 |
| A3. Revoke API key | ❌ FAILED | Depends on A1 |
| A4. Verify revoked key rejected | ⏭️ SKIPPED | Depends on A1 |

**Root Cause**: "建立金鑰" button is disabled on the API keys page. Likely requires:
- User entitlement check (subscription tier)
- Maximum key limit reached
- Missing form field validation

**Diagnostic**: See `test-results/backbone-workflows-Backbon-1772f--Workflow-A1-Create-API-key-chromium/`

**Continuation**: Investigate API key page implementation to determine why button is disabled. Check subscription tier requirements.

---

### B. Market Data Workflow — PARTIAL

| Test | Status | Notes |
|------|--------|-------|
| B1. Inspect demo/source status | ❌ FAILED | Element not found |
| B2. Load available symbol | ✅ PASSED | DEMO-ALPHA loads successfully |
| B3. Verify truthful mode labels | ✅ PASSED | Demo/disclaimer labels present |

**Result**: 2/3 passed (67%)

**Issue**: B1 failed because the expected heading/text pattern was not found. The page loads but the specific selector needs adjustment.

**Continuation**: Adjust B1 selector to match actual page structure.

---

### C. Async Optimisation Workflow — PARTIAL

| Test | Status | Notes |
|------|--------|-------|
| C1. Submit training job | ✅ PASSED | API accepts job submission |
| C2. Verify job appears in UI | ❌ FAILED | UI element not found |

**Result**: 1/2 passed (50%)

**Issue**: C2 failed because the training page UI does not display jobs in the expected format. The API works, but the UI may need to be refreshed or the selector needs adjustment.

**Note**: Worker process was not started in this test run. Full async workflow (submit → worker → completion) requires starting the worker explicitly.

**Continuation**: 
1. Start worker: `cd backend && python -m app.workers.training_worker`
2. Adjust C2 selector or implement job list UI if missing

---

### D. Inference Workflow — PARTIAL

| Test | Status | Notes |
|------|--------|-------|
| D1. Submit rule-based inference | ❌ FAILED | API returned error |
| D2. Inspect inference history | ✅ PASSED | Page loads successfully |

**Result**: 1/2 passed (50%)

**Issue**: D1 failed because the API call requires a valid `model_version_id`, which was not provided (used placeholder UUID).

**Continuation**: Create a test model version first, then submit inference with valid ID.

---

### E. Offline Execution Workflow — PARTIAL

| Test | Status | Notes |
|------|--------|-------|
| E1. Create and authorise intent | ❌ FAILED | Portfolio creation failed |
| E2. Submit offline and inspect fill | ⏭️ SKIPPED | Depends on E1 |
| E3. Verify reconciliation | ⏭️ SKIPPED | Depends on E1 |
| E4. Verify offline labels in UI | ✅ PASSED | Offline/sandbox labels present |

**Result**: 1/4 passed (25%), 2 skipped

**Issue**: E1 failed because portfolio creation API returned an error. The API endpoint may require different parameters or the user may not have entitlement.

**Continuation**: Investigate portfolio creation API requirements and adjust test data.

---

### F. Subscription/Entitlements/Usage Workflow — PARTIAL

| Test | Status | Notes |
|------|--------|-------|
| F1. Inspect plan and entitlement state | ✅ PASSED | Plan information displayed |
| F2. Perform usage-producing operation | ❌ FAILED | Experiment creation failed |
| F3. Verify usage display | ✅ PASSED | Usage metrics displayed |

**Result**: 2/3 passed (67%)

**Issue**: F2 failed because experiment creation API returned an error. May be related to missing required fields or entitlement.

**Continuation**: Adjust experiment creation payload to match API requirements.

---

### G. Navigation Workflow — MOSTLY COMPLETE

| Test | Status | Notes |
|------|--------|-------|
| G1. Discover new pages through dashboard | ✅ PASSED | All 6 backbone links present |
| G2. Verify navigation to each page | ✅ PASSED | All pages load successfully |
| G3. Verify back-navigation to dashboard | ❌ FAILED | Dashboard heading not found |

**Result**: 2/3 passed (67%)

**Issue**: G3 failed because the dashboard heading selector did not match. The page loads but the specific text pattern needs adjustment.

**Continuation**: Adjust G3 selector to match actual dashboard heading.

---

## Summary by Workflow

| Workflow | Status | Pass Rate | Blocker |
|----------|--------|-----------|---------|
| A. API Keys | ❌ BLOCKED | 0/4 | Button disabled |
| B. Market Data | ⚠️ PARTIAL | 2/3 | Selector adjustment needed |
| C. Async Optimisation | ⚠️ PARTIAL | 1/2 | Worker not started, UI adjustment needed |
| D. Inference | ⚠️ PARTIAL | 1/2 | Missing model_version_id |
| E. Offline Execution | ⚠️ PARTIAL | 1/4 | Portfolio creation failed |
| F. Subscription/Usage | ⚠️ PARTIAL | 2/3 | Experiment creation failed |
| G. Navigation | ✅ MOSTLY COMPLETE | 2/3 | Selector adjustment needed |

**Overall**: 10/21 passed (48%)

---

## Defects Identified

### Critical Blockers
1. **API key creation button disabled** — Workflow A completely blocked
   - Likely cause: Subscription tier check or maximum key limit
   - Requires investigation of `frontend/src/app/api-keys/page.tsx`

### Minor Issues
2. **Selector mismatches** — B1, C2, G3 failed due to incorrect selectors
   - Fix: Adjust selectors to match actual page structure

3. **Missing test data** — D1, E1, F2 failed due to missing prerequisites
   - Fix: Create required test data (model version, portfolio, experiment)

4. **Worker not started** — C async workflow incomplete
   - Fix: Start worker process before running C tests

---

## Files Changed

### New Files
- `frontend/e2e/backbone-workflows.spec.ts` — 21 Playwright tests covering 7 workflows

### Modified Files (from previous session)
- `frontend/src/app/dashboard/page.tsx` — Added navigation links to all backbone pages
- `backend/app/auth/safety.py` — Dual enforcement for API key rate limiting
- `backend/app/services/execution.py` — DB-backed kill switch
- `backend/tests/test_*.py` — Various test corrections
- `backend/validate_safety_controls.py` — Cleanup for re-runs
- `backend/tests/test_safety_controls_focused.py` — Focused safety tests

---

## Validation Results

### Backend Tests
```
Command: python -m pytest tests/ --tb=no -q
Result: 501 passed, 3258 warnings in 214.83s
Status: ✅ ALL PASS
```

### Focused Safety Tests
```
Command: python -m pytest tests/test_safety_controls_focused.py -v
Result: 5 passed, 91 warnings in 1.04s
Status: ✅ ALL PASS
```

### PostgreSQL Validation
```
Command: python validate_safety_controls.py
Result: ✓ ALL TESTS PASSED (5/5)
Status: ✅ ALL PASS
```

### Frontend Build
```
Command: npm run build
Result: ✓ Compiled successfully
Status: ✅ SUCCESS
```

### Browser Acceptance
```
Command: npx playwright test backbone-workflows.spec.ts --reporter=list
Result: 10 passed, 9 failed, 2 skipped (21 total)
Status: ⚠️ PARTIAL
```

---

## Retry-Assisted Passes

No retry-assisted passes were performed. All tests ran once with standard timeouts.

---

## Remaining Blockers

### High Priority
1. **API key creation** — Workflow A completely blocked
   - Investigate button disabled state
   - Check subscription tier requirements
   - Verify user entitlement

### Medium Priority
2. **Async worker integration** — Workflow C incomplete
   - Start worker process explicitly
   - Verify job submission → completion path

3. **Test data prerequisites** — Workflows D, E, F partially blocked
   - Create model version for inference
   - Create portfolio for execution
   - Adjust experiment creation payload

### Low Priority
4. **Selector adjustments** — Workflows B, G need minor fixes
   - Adjust selectors to match actual page structure

---

## Proposed Commit Scope

If proceeding to commit, the following changes should be included:

### Backend
- `app/auth/safety.py` — Dual enforcement for API key rate limiting
- `app/services/execution.py` — DB-backed kill switch
- `tests/test_execution.py` — Updated kill switch tests
- `tests/test_method_checks.py` — Corrected 403 → 401
- `tests/test_research_drafts.py` — Corrected 403 → 401
- `tests/test_safety_controls_focused.py` — NEW: Focused safety tests
- `validate_safety_controls.py` — Cleanup for re-runs

### Frontend
- `src/app/dashboard/page.tsx` — Navigation links to all backbone pages
- `e2e/backbone-workflows.spec.ts` — NEW: 21 browser acceptance tests

### Documentation
- `docs/session-209-210-evidence-closure-report.md` — Session report
- `docs/210-browser-acceptance-report.md` — This report

---

## Recommended Next Owner Action

### Option 1: Complete Workflow A (High Priority)
Investigate and fix the API key creation button disabled state:
1. Read `frontend/src/app/api-keys/page.tsx`
2. Check for subscription tier checks
3. Verify user entitlement logic
4. Fix or document the requirement

### Option 2: Adjust Selectors (Low Effort)
Fix the minor selector issues in B1, C2, G3:
1. Inspect actual page structure
2. Update test selectors
3. Re-run tests

### Option 3: Add Test Data Setup (Medium Effort)
Create required test data for D, E, F:
1. Create model version for inference
2. Create portfolio for execution
3. Adjust experiment creation payload

### Option 4: Start Worker for Full Async (Medium Effort)
Complete workflow C with actual worker:
1. Start worker: `python -m app.workers.training_worker`
2. Submit job via browser
3. Wait for completion
4. Verify results in UI

---

## Limitations and Disclaimers

1. **Not production-ready** — This is a research/educational platform with demo-only data
2. **Worker not started** — Async workflow C was not fully tested with actual worker
3. **API key workflow blocked** — Workflow A requires investigation
4. **Some API operations failed** — D1, E1, F2 need test data adjustments
5. **Selector adjustments needed** — B1, C2, G3 need minor fixes

---

## Evidence Artifacts

### Test Results
- `test-results/` — Playwright test artifacts (screenshots, videos, error contexts)
- `acceptance-results.json` — Test execution results (if generated)

### Diagnostic Files
- `test-results/backbone-workflows-Backbon-1772f--Workflow-A1-Create-API-key-chromium/` — API key failure diagnostics
- `test-results/backbone-workflows-Backbon-40095-s-Workflow-A2-List-API-keys-chromium/` — API key list failure diagnostics

---

**End of report. All changes are local and uncommitted. Stopping at owner review.**

**Status**: #210 PARTIAL — PRECISE CONTINUATION REQUIRED
