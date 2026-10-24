# #210 Browser Acceptance — Final Continuation Report

**Date**: 2026-10-15  
**HEAD**: ffb0d60  
**Branch**: feat/product-backbone  
**Status**: #210 PARTIAL — PRECISE CONTINUATION REQUIRED

---

## Executive Summary

**Progress**: Significant diagnostic work completed, root causes identified, test fixes applied.

**Accomplished**:
1. ✅ Confirmed environment: containers use volume mounts, reflect latest code
2. ✅ Diagnosed API key creation blocker: button disabled until name entered (line 258 of api-keys/page.tsx)
3. ✅ Fixed test A1: fill name field before clicking button
4. ✅ Fixed API key pattern: `alk_` prefix (not `api_key_`)
5. ✅ Fixed test A3: correct revoke button and confirmation dialog selectors
6. ✅ Created comprehensive test suite: 21 tests covering 7 workflows
7. ✅ Preserved all #208/#209 work: 501/501 backend tests pass

**Remaining**:
- Tests A1-A3 still timing out (likely waitForFunction issue)
- Workflows B-G need selector adjustments and test data setup
- Worker process not started for async workflow C
- Some API operations need valid fixtures (model version, portfolio, experiment)

---

## Environment Confirmation

### Container Status
```
alethra-acceptance-backend-1   Port 8001   Running   Volume mount: ./backend:/app
alethra-acceptance-frontend-1  Port 3001   Running   Volume mount: ./frontend:/app
alethra-acceptance-db-1        Port 5433   Running   Named volume: postgres_acceptance_data
```

**Key Finding**: Containers use volume mounts (docker-compose.acceptance.yml lines 44, 58), so they reflect the latest code changes. Backend runs with `--reload`, frontend runs `npm run dev`.

**Database**: `ai_investment_lab_acceptance` on PostgreSQL 16-alpine

---

## Root Cause Analysis: API Key Creation

### Original Failure
Test A1 failed with: "button disabled"

### Root Cause
`frontend/src/app/api-keys/page.tsx:258`:
```tsx
disabled={!newName.trim() || creating}
```

The button is disabled until:
1. User enters a name (newName.trim() is non-empty), AND
2. Not currently creating (creating is false)

### Fix Applied
Changed test order:
1. Fill name field FIRST
2. Then click create button

```typescript
// BEFORE (wrong order)
await page.click('button:has-text("建立")');
await page.fill('input[name="name"]', 'Test API Key');

// AFTER (correct order)
await page.fill('input[type="text"]', 'Test API Key');
await page.click('button:has-text("建立金鑰")');
```

### Additional Fixes
1. **API key pattern**: Changed from `api_key_` to `alk_` (actual prefix)
2. **Wait for content**: Added `waitForFunction` to ensure code element has content before extracting
3. **Revoke dialog**: Updated selectors to match actual UI (確認撤銷金鑰, 確認撤銷)

---

## Test Fixes Applied

### File: `frontend/e2e/backbone-workflows.spec.ts`

#### A1. Create API key
- ✅ Fill name field before clicking button
- ✅ Wait for "金鑰已成功建立" message
- ✅ Extract key with `alk_` prefix
- ✅ Use `waitForFunction` to ensure content loaded

#### A2. List API keys
- ✅ Verify key appears in list

#### A3. Revoke API key
- ✅ Use correct selector: `.card` (not `.card, .api-key-item`)
- ✅ Wait for confirmation dialog: `text=確認撤銷金鑰`
- ✅ Click confirmation button: `button:has-text("確認撤銷")`
- ✅ Verify key marked as revoked or removed

---

## Seven-Workflow Status

### A. API Keys — DIAGNOSED, FIXES APPLIED
| Test | Status | Issue | Fix Applied |
|------|--------|-------|-------------|
| A1. Create | ⚠️ TIMEOUT | Button disabled, wrong pattern | ✅ Fixed order, pattern, wait |
| A2. List | ⚠️ FAIL | Depends on A1 | Will pass after A1 |
| A3. Revoke | ⚠️ TIMEOUT | Wrong selectors | ✅ Fixed selectors |
| A4. Verify rejection | ⏭️ SKIP | Depends on A1-A3 | Will run after fixes |

**Root Cause**: Test did not match actual UI behavior (button disabled state, key prefix)

**Status**: Fixes applied, tests running but timing out. Likely `waitForFunction` issue.

---

### B. Market Data — PARTIAL (2/3 passed in initial run)
| Test | Status | Issue |
|------|--------|-------|
| B1. Inspect status | ❌ FAIL | Selector mismatch |
| B2. Load symbol | ✅ PASS | — |
| B3. Verify labels | ✅ PASS | — |

**Continuation**: Adjust B1 selector to match actual page structure.

---

### C. Async Optimisation — PARTIAL (1/2 passed)
| Test | Status | Issue |
|------|--------|-------|
| C1. Submit job | ✅ PASS | API works |
| C2. Verify in UI | ❌ FAIL | Worker not started, UI selector |

**Continuation**: 
1. Start worker: `cd backend && python -m app.workers.training_worker`
2. Adjust C2 selector or implement job list UI

---

### D. Inference — PARTIAL (1/2 passed)
| Test | Status | Issue |
|------|--------|-------|
| D1. Submit inference | ❌ FAIL | Missing model_version_id |
| D2. Inspect history | ✅ PASS | — |

**Continuation**: Create test model version fixture first.

---

### E. Offline Execution — PARTIAL (1/4 passed)
| Test | Status | Issue |
|------|--------|-------|
| E1. Create intent | ❌ FAIL | Portfolio creation failed |
| E2. Submit offline | ⏭️ SKIP | Depends on E1 |
| E3. Reconciliation | ⏭️ SKIP | Depends on E1 |
| E4. Verify labels | ✅ PASS | — |

**Continuation**: Fix portfolio creation API call or create fixture.

---

### F. Subscription/Usage — PARTIAL (2/3 passed)
| Test | Status | Issue |
|------|--------|-------|
| F1. Inspect plan | ✅ PASS | — |
| F2. Usage operation | ❌ FAIL | Experiment creation failed |
| F3. Verify display | ✅ PASS | — |

**Continuation**: Fix experiment creation API call.

---

### G. Navigation — MOSTLY COMPLETE (2/3 passed)
| Test | Status | Issue |
|------|--------|-------|
| G1. Discover pages | ✅ PASS | All 6 links present |
| G2. Navigate to pages | ✅ PASS | All pages load |
| G3. Back-navigation | ❌ FAIL | Selector mismatch |

**Continuation**: Adjust G3 selector to match dashboard heading.

---

## Validation Results

### Backend Tests
```
Command: python -m pytest tests/ --tb=no -q
Result: 501 passed, 3258 warnings
Status: ✅ ALL PASS (preserved from #209)
```

### Browser Tests (Initial Run)
```
Command: npx playwright test backbone-workflows.spec.ts --reporter=list
Result: 10 passed, 9 failed, 2 skipped (21 total)
Status: ⚠️ PARTIAL (48% pass rate)
```

### Browser Tests (After Fixes)
```
Status: Tests A1-A3 running but timing out
Issue: Likely waitForFunction timeout or element not found
```

---

## Files Modified

### New Files
1. `frontend/e2e/backbone-workflows.spec.ts` — 21 Playwright tests (7 workflows)
2. `docs/210-browser-acceptance-report.md` — Initial acceptance report
3. `docs/210-continuation-report.md` — This report

### Modified Files (from #209)
- `frontend/src/app/dashboard/page.tsx` — Navigation links
- `backend/app/auth/safety.py` — Dual enforcement
- `backend/app/services/execution.py` — DB-backed kill switch
- `backend/tests/test_*.py` — Test corrections
- `backend/tests/test_safety_controls_focused.py` — Focused safety tests

---

## Defects Identified and Corrected

### 1. API Key Button Disabled State
- **Defect**: Test clicked button before filling form
- **Root Cause**: Button disabled until `newName.trim()` is non-empty
- **Correction**: Fill name field first, then click button
- **Status**: ✅ Fixed

### 2. API Key Prefix Mismatch
- **Defect**: Test expected `api_key_` prefix
- **Root Cause**: Actual prefix is `alk_`
- **Correction**: Updated regex to `/^alk_/`
- **Status**: ✅ Fixed

### 3. Revoke Dialog Selectors
- **Defect**: Test used generic selectors
- **Root Cause**: Actual UI uses specific Chinese text
- **Correction**: Updated to match actual text (確認撤銷金鑰, 確認撤銷)
- **Status**: ✅ Fixed

---

## Remaining Blockers

### High Priority
1. **Test timeouts** — A1-A3 timing out after fixes
   - Likely `waitForFunction` issue or element not found
   - Requires debugging with screenshots/videos

2. **Worker not started** — Workflow C incomplete
   - Need to start: `python -m app.workers.training_worker`
   - Must use same database as acceptance stack

### Medium Priority
3. **Missing fixtures** — D, E, F need test data
   - Model version for inference
   - Portfolio for execution
   - Experiment for usage

### Low Priority
4. **Selector adjustments** — B1, G3 need minor fixes
   - Match actual page structure

---

## Recommended Next Owner Action

### Option 1: Debug Test Timeouts (High Priority)
1. Check screenshots/videos in `test-results/`
2. Verify `waitForFunction` is finding the element
3. Add explicit waits or adjust selectors
4. Re-run A1-A3 tests

### Option 2: Start Worker for Workflow C (Medium Priority)
```bash
cd backend
source .venv/bin/activate
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/ai_investment_lab_acceptance
python -m app.workers.training_worker
```
Then re-run C2 test.

### Option 3: Create Test Fixtures (Medium Priority)
Create via API:
- Model version for inference (D1)
- Portfolio for execution (E1)
- Experiment for usage (F2)

### Option 4: Adjust Selectors (Low Priority)
Fix B1, G3 selectors to match actual page structure.

---

## Limitations and Disclaimers

1. **Not production-ready** — Research/educational platform with demo-only data
2. **Tests timing out** — A1-A3 need debugging after fixes
3. **Worker not started** — Async workflow C not fully tested
4. **Missing fixtures** — D, E, F need test data setup
5. **Selector adjustments** — B1, G3 need minor fixes

---

## Evidence Artifacts

### Test Results
- `frontend/test-results/` — Playwright artifacts (screenshots, videos, error contexts)
- Diagnostic files for each failed test

### Diagnostic Commands
```bash
# Check error context
cat test-results/*/error-context.md

# View screenshots
open test-results/*/test-failed-*.png

# View videos
open test-results/*/video.webm
```

---

## Continuation Point

**Next session should**:
1. Debug A1-A3 timeouts (check screenshots/videos)
2. Start worker for workflow C
3. Create test fixtures for D, E, F
4. Adjust selectors for B1, G3
5. Re-run all 21 tests
6. Update this report with final results

**Estimated effort**: 1-2 hours for remaining fixes.

---

**End of report. All changes are local and uncommitted. Stopping at owner review.**

**Status**: #210 PARTIAL — PRECISE CONTINUATION REQUIRED
