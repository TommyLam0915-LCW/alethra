# Phase 2B Method Review — Verification Report

**Date:** 2026-10-14
**Branch:** `feat/phase2b-method-review`
**Baseline:** `3b4c4c12e2c01e8152ce2124a2593084d88dc8df` (PR #3 merge)
**Status:** Local, uncommitted

---

## 1. Implementation State

### Changed Files

| File | Action | Description |
|------|--------|-------------|
| `backend/app/method_checks/__init__.py` | New | Module exports |
| `backend/app/method_checks/schemas.py` | New | Pydantic models (CheckResult, MethodReviewResponse with completed_at_available) |
| `backend/app/method_checks/rules.py` | New | 10 pure rule functions (RC-001 – RC-010) |
| `backend/app/method_checks/evaluator.py` | New | Rule orchestration |
| `backend/app/method_checks/loader.py` | New | DB loading (run-first selection) |
| `backend/app/method_checks/explanations.py` | New | Deterministic A/B/C explanation templates |
| `backend/app/api/experiments.py` | Modified | +36 lines: GET method-review endpoint with completed_at_available |
| `backend/tests/test_method_checks.py` | New | 66 tests (including RC-004 missing/invalid cost paths and RC-009 per-component coverage) |
| `frontend/src/lib/api.ts` | Modified | +1 line: methodReview() |
| `frontend/src/app/experiments/[id]/page.tsx` | Modified | +135 lines: Method Review section with provenance disclosure and timestamp fallback warning |
| `frontend/e2e/method-review.spec.ts` | New | 9 browser tests (6 integration + 3 intercepted-response rendering regressions) |
| `docs/phase2b-method-checks-spec.md` | New | Specification v0.2 |

---

## 2. Test Results

### Backend Unit Tests

| Suite | Passed | Failed |
|-------|--------|--------|
| Method checks (test_method_checks.py) | 66 | 0 |
| Full backend suite (prior run, before latest additional test) | 305 | 0 |

Command: `cd backend && ./venv/bin/python -m pytest tests/test_method_checks.py -v`
Result: `66 passed (6.35s)`

Coverage includes:
- Unit tests for all 10 rules (RC-001 – RC-010)
- API endpoint tests (auth, ownership, no runs, completed run, determinism)
- `completed_at_available` field tests (null when no run, true when timestamp present, false when missing)
- RC-004 extended coverage: empty spec, missing commission/slippage, null, negative, boolean, NaN, Inf, string costs (14 tests total)
- RC-009 per-component coverage: commission-only zero, slippage-only zero, both zero with partial warnings, missing/invalid costs (12 tests total)
- Explanation tests (all rules × all statuses)

### Frontend Build

Command: `cd frontend && npm run build`
Result: Success (all 16 routes compiled)

### Browser Tests — Phase 2B

**Normal integration tests (6):**

| Test | Result | Duration |
|------|--------|----------|
| A1. Display all 10 rules | PASS | 1.7s |
| A2. Status badges with correct labels | PASS | 1.6s |
| A3. Reviewed run/version identity visible | PASS | 1.5s |
| A4. Provenance disclosure for current-text checks | PASS | 1.4s |
| B1. No completed run state | PASS | 1.4s |
| D1. No side effects on load | PASS | 2.6s |

**Intercepted-response rendering regressions (3):**

| Test | Result | Duration | Description |
|------|--------|----------|-------------|
| A5. Timestamp fallback warning | PASS | 1.5s | Uses Playwright route interception to serve a schema-valid synthetic response with `completed_at_available: false`. Asserts unconditionally that the timestamp warning is visible and states chronological recency is not established. |
| C1. Refresh behavior | PASS | 1.4s | Uses phase-based fixture: initial phase returns RC-001 PASS for all requests; after initial assertions, phase switches to refreshed and returns RC-001 INSUFFICIENT_INFORMATION. Decoupled from React StrictMode double-fetch — no request count assumption. Asserts: initial content visible, refresh triggers additional request(s), changed content appears, button returns to non-loading state. |
| E1. Error handling | PASS | 1.5s | Intercepts method-review with HTTP 500. Asserts unconditionally: error fallback text "方法審查暫時無法載入" visible, rule results (RC-001, RC-005) absent, experiment detail page remains usable (回測結果 heading visible). |

Command: `cd frontend && ./node_modules/.bin/playwright test e2e/method-review.spec.ts --reporter=list`
Result: `9 passed (15.3s)`

**Note:** The timestamp fallback test is a rendering regression, not full backend-to-browser integration. Backend coverage for `completed_at_available=false` is provided separately by `test_completed_at_available_false_when_run_has_no_timestamp` in the backend test suite.

### Browser Tests — Phase 2A (Regression)

| Test | Result |
|------|--------|
| A. Navigation | PASS |
| B. Mock Disclosure | PASS |
| C. Questions/Answers Persistence | PASS |
| D. Invalid Input Handling | PASS |
| E. Zero Values | PASS |
| F. Revision/Audit History | PASS |
| G. Acceptance/Finalization | PASS |
| H. Rejection Prevention | PASS |
| I. Selected-Field Transfer | PASS |
| J. URL Privacy | PASS |
| K. Preview/Cancellation | PASS |
| L. Confirmation/Population | PASS |
| M. Deletion | PASS |
| N. Access Control | PASS |

Command: `cd frontend && npx playwright test e2e/research-drafts.spec.ts --reporter=list`
Result: `14 passed (43.9s)`

### Whitespace Check

Command: `git diff --check`
Result: Clean

---

## 3. Acceptance Stack Isolation

| Component | Port | Database |
|-----------|------|----------|
| DB | 5433 | `ai_investment_lab_acceptance` (disposable volume) |
| Backend | 8001 | Above DB |
| Frontend | 3001 | Connects to backend on 8001 |

Compose project: `alethra-acceptance`
Status: Pre-existing, running. No containers created/destroyed during verification.
User data: Not accessed. All fixtures are test-owned and disposable.

---

## 4. Source Provenance Decisions

### 4A. Experiment Text Mutability

**Finding:** `hypothesis`, `falsification_conditions`, and `original_idea` are mutable via PUT `/api/v1/experiments/{id}` (backend/app/api/experiments.py lines 136-153).

**Consequence:** RC-001 and RC-002 review the **current** experiment text, not the text present when the selected run executed. A hypothesis added after a run completes will show as PASS even though it was not present during the run.

**Mitigation:** The UI now includes a provenance disclosure (yellow box):

> **資料來源說明：** RC-001 及 RC-002 檢查的是實驗目前的文字內容（假說、原始想法、否決條件）。這些欄位可隨時編輯，因此審查的文字可能與回測執行時不同。這並非歷史文字快照。

This explicitly states that the checks use current text which may have changed after the run. No snapshot mechanism exists, and the task prohibits adding tables/migrations.

### 4B. Missing completed_at

**Behavior:** The loader uses `completed_at DESC NULLS LAST, id DESC`. When completed_at is NULL, the run is ordered to the end. If all completed runs lack completed_at, selection falls back to `id DESC` — a deterministic fallback, not proof of chronological recency.

**Disclosure:**
1. API response includes `completed_at_available: Optional[bool]`:
   - `null` — no run selected
   - `true` — selected run has a completion timestamp
   - `false` — selected run lacks a completion timestamp; selection is deterministic but chronological recency cannot be established

2. UI displays a warning (yellow box) when `completed_at_available === false`:

> **時間戳提醒：** 所選的回測執行缺少完成時間戳記。執行排序依據 ID 而非時間先後，因此「最新」不代表時間上最近。

This field reports only the selected run's timestamp status. It does not verify timestamp completeness across all candidate runs.

### 4C. Run/Version Relationship

**Confirmed:** The loader finds the latest completed run across ALL experiment versions, then loads the version from `run.experiment_version_id`. Version-dependent checks (RC-003, RC-004, RC-006, RC-009, RC-010) use this version's strategy_spec, not the experiment's current version.

### 4D. No Completed Run

**Confirmed:** When no completed run exists, the loader returns `(experiment, None, None)`. The API returns `version_id=null`, `run_id=null`. Run-dependent checks (RC-005, RC-006, RC-007, RC-008, RC-009, RC-010) return INSUFFICIENT_INFORMATION or NOT_APPLICABLE. Experiment-level checks (RC-001, RC-002) remain evaluable.

---

## 5. Rule Correctness Review

### Verified Against Actual Schema

| Rule | Schema Source | Status |
|------|--------------|--------|
| RC-001 | Experiment.original_idea / hypothesis | Correct |
| RC-002 | Experiment.falsification_conditions | Correct |
| RC-003 | StrategySpec.start_date/end_date, metrics.trading_days | Correct — uses datetime.fromisoformat() |
| RC-004 | StrategySpec.commission_rate/slippage_rate | Correct — `_is_valid_cost()` rejects None, bool, string, negative, NaN, Inf; missing key → INSUFFICIENT_INFORMATION |
| RC-005 | metrics.total_trades | Correct — handles None, zero, negative |
| RC-006 | StrategySpec.benchmark_id, metrics.benchmark_total_return_pct | Correct — explicit null check |
| RC-007 | metrics keys (13 keys from engine/metrics.py) | Corrected — was using 5 wrong keys |
| RC-008 | ResearchRun.engine_version | Correct |
| RC-009 | StrategySpec costs + ResearchRun.warnings | Correct — exact string matching |
| RC-010 | ExperimentVersion.config_hash, ResearchRun.config_hash | Correct — NULL → INSUFFICIENT_INFORMATION |

### Corrections Applied During Review

1. **EXPECTED_METRIC_KEYS** — replaced 5 non-existent keys (win_rate, sharpe_ratio, net_pnl, alpha_pct, total_slippage) with actual engine keys (initial_cash, final_equity, excess_return_pct, total_slippage_cost, time_in_market_pct)
2. **Loader** — changed from current_version_id lookup to run-first selection per spec 3.D
3. **Optional import** — added to rules.py
4. **Evaluator return type** — fixed to Tuple[List[CheckResult], Optional[bool]]
5. **RC-007 test** — updated fixture to use correct metric keys
6. **RC-004** — added `_is_valid_cost()` helper; missing/invalid costs now return INSUFFICIENT_INFORMATION instead of silently defaulting to 0.0
7. **RC-009** — per-component zero-warning consistency; commission and slippage checked independently
8. **RC-003** — explanation wording corrected to reference engine 252-day threshold policy, not statistical validity

---

## 6. Authentication and Ownership

**Confirmed:** The method-review endpoint uses the same `get_current_user` dependency and ownership pattern as other experiment endpoints:
- Non-existent experiment → 404
- Non-owned experiment → 404 (not 403, matching existing convention)
- Unauthenticated → 401

**No side effects:** The endpoint is read-only. No database mutations, no run creation, no experiment/version state changes.

---

## 7. Remaining Limitations

1. No draft-level or run-level review (future slice)
2. No persistence of check results (computed on demand)
3. No automatic blocking based on warnings
4. No statistical significance testing
5. No look-ahead/overfitting/survivorship bias detection
6. Demo data only — not production-ready
7. Annualized metrics conditional on >= 252 trading days
8. Experiment text (hypothesis, falsification) may have changed post-run — disclosed in UI but no snapshot mechanism
9. No external AI or model training
10. No broker/exchange integration or live signals/execution
11. No automatic experiment/run/order/model creation
12. No new tables or migrations

---

## 8. Browser Verification Status

**Previous status:** 9/9 passed (with vacuous timestamp test)
**Current status:** 9/9 passed (6 integration + 3 intercepted-response rendering regressions)

- Phase 2B browser tests: 9/9 passed (6 integration + 3 intercepted-response rendering regressions)
- Phase 2A regression browser tests: 14/14 passed (prior run, not rerun in this task)
- No regression detected

**Intercepted tests rationale:** C1 (refresh) and E1 (error) use route interception to test rendering behavior under controlled conditions — changed content on refresh, and error fallback on failure. These are rendering regressions, not full backend-to-browser integration. A5 (timestamp fallback) was previously repaired for the same reason.

---

## 9. Correction Verification (2026-10-14)

### Corrections Applied

| # | Category | Change |
|---|----------|--------|
| 1 | RC-004 | Added `_is_valid_cost()` helper; missing/invalid costs → INSUFFICIENT_INFORMATION |
| 2 | RC-009 | Per-component zero-warning consistency; commission and slippage checked independently |
| 3 | RC-003 | Explanation wording corrected to reference engine 252-day threshold policy |
| 4 | Browser A2 | Unconditional per-check status assertion using API response |
| 5 | Browser C1 | Phase-based fixture (initial→PASS, refreshed→INSUFFICIENT_INFORMATION); decoupled from StrictMode request count |
| 6 | Browser E1 | Route-intercepted error test with unconditional assertions |

### Verification Results

| Check | Result |
|-------|--------|
| Backend method checks tests (initial correction run) | 66 passed (7.81s) |
| Browser tests (initial correction run) | 9 passed (15.3s) |
| Backend method checks tests (commit-readiness run) | 66 passed (9.04s) |
| Browser tests (commit-readiness run) | 9 passed (19.0s) |
| `git diff --check` | Clean |
| Frontend build | Not rerun (product frontend inputs unchanged) |

### Browser Test Classification

| Category | Count | Tests |
|----------|-------|-------|
| Normal backend-to-browser integration | 6 | A1, A2, A3, A4, B1, D1 |
| Intercepted-response rendering regressions | 3 | A5, C1, E1 |

### Retained Evidence

- Backend: 66 tests passed in correction run (prior to browser verification)
- Browser: 9/9 passed in this verification run
- Stack isolation: `alethra-acceptance` project confirmed (ports 5433/8001/3001, bind mounts, disposable DB)

### Auth Status Codes (Rechecked)

| Scenario | Status Code | Basis |
|----------|-------------|-------|
| Unauthenticated (no header) | 403 | `HTTPBearer()` auto_error (FastAPI default) |
| Invalid token | 401 | `get_current_user` raises HTTP_401_UNAUTHORIZED |
| Non-owned experiment | 404 | Matches existing convention |
| Nonexistent experiment | 404 | Matches existing convention |

Test assertion (`test_unauthenticated`) confirms 403. No changes to auth logic or assertions.
