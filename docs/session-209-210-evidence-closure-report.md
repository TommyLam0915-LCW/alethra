# #209-#210 Evidence Closure and Navigation Integration — Session Report

**Date**: 2026-10-15  
**Branch**: feat/product-backbone  
**Status**: LOCAL INCREMENT COMPLETE — OWNER REVIEW PENDING

---

## 1. Authentication Status Code Correction

### Root Cause
Two tests expected 403 (Forbidden) for unauthenticated requests, but the implementation correctly returns 401 (Unauthorized) per HTTP standards:
- **401 Unauthorized**: Missing or invalid authentication credentials
- **403 Forbidden**: Authenticated but insufficient permissions

### Evidence
- `app/auth/jwt.py:44-48` — `get_current_user` raises 401 with `WWW-Authenticate: Bearer` header
- FastAPI 0.143.1 installed (requirements.txt pins 0.115.0 — dependency drift, but behavior unchanged)
- Implementation is correct; tests had outdated expectations

### Correction
Updated two tests:
- `tests/test_method_checks.py:423` — `test_unauthenticated` now expects 401
- `tests/test_research_drafts.py:1178` — `test_authentication_required_for_draft_fetch` now expects 401

**Result**: 501/501 backend tests pass

---

## 2. Rate-Limit Semantics Alignment

### Implementation Confirmed
`app/services/rate_limit.py:56-58` uses **fixed hourly windows** aligned to hour boundary:
```python
now = datetime.utcnow()
window_start = now.replace(minute=0, second=0, microsecond=0)
```

All requests within the same hour (UTC) share the same `window_start`. At the next hour boundary, a new window begins. This eliminates race conditions in window determination.

### Critical Fix: Dual Enforcement for API Keys
**Previous behavior**: Used EITHER user_id OR api_key_id as identity key, allowing key rotation to bypass user cap.

**New behavior**: Both user-level and API-key-level limits are checked when an API key is used:
1. User-level limit (aggregate across all API keys for that user) — checked first
2. API-key-level limit (per-key limit) — checked second

Both must pass for the request to be allowed.

**Files modified**:
- `app/auth/safety.py:36-72` — `rate_limit` dependency
- `app/auth/safety.py:75-122` — `safety_control` dependency

**Result**: Rotating API keys cannot bypass the user cap.

---

## 3. #209 Evidence Gaps Closed

Added focused tests in `tests/test_safety_controls_focused.py`:

### Worker Kill Switch Behavior
- `test_worker_blocked_by_global_switch` — Worker does not claim jobs when global kill switch is active
- `test_worker_blocked_by_training_switch` — Worker does not claim jobs when training kill switch is active
- `test_pending_jobs_eligible_after_deactivation` — Pending jobs are claimable after kill switch deactivation

### User-Plus-Key Aggregate Enforcement
- `test_user_aggregate_limit_across_keys` — User-level limit aggregates across all API keys
- `test_dual_enforcement_both_limits_apply` — Both user-level and API-key-level limits must pass

**Result**: 5/5 focused tests pass

### PostgreSQL Validation
`validate_safety_controls.py` — 5/5 tests pass:
- Concurrent rate limit creation (UPSERT safety)
- Rate limit threshold enforcement
- User isolation
- Kill switch cross-process visibility (2 independent sessions)
- Transaction rollback semantics

**Limitation**: Two independent sessions demonstrate shared database visibility; not a full multi-process deployment test.

---

## 4. #210 Navigation Integration (Partial)

### Dashboard Navigation Updated
`frontend/src/app/dashboard/page.tsx:62-79` — Added navigation links to all backbone pages:
- 研究草稿 (/research-drafts)
- 模型研究 (/models)
- 訓練任務 (/training) **[NEW]**
- 推論執行 (/inference) **[NEW]**
- 執行意向 (/execution) **[NEW]**
- 市場數據 (/market-data) **[NEW]**
- API 金鑰 (/api-keys)
- 訂閱方案 (/subscription) **[NEW]**
- 建立實驗 (/experiments/new)

**Result**: Frontend builds successfully. All backbone pages are now discoverable from the dashboard.

---

## 5. #210 Browser Acceptance (Not Started)

### Required Coverage
The following workflows require browser acceptance testing:
1. **API-key creation/list/revocation** — `/api-keys`
2. **Demo market-data status** — `/market-data`
3. **Asynchronous optimisation job submission and result display** — `/training`
4. **Inference output** — `/inference`
5. **Offline execution intent lifecycle and reconciliation** — `/execution`
6. **Subscription/entitlement/usage display** — `/subscription`
7. **Discoverability/navigation** — Dashboard links to all pages

### Environment Requirements
- Start backend with worker process: `python -m app.workers.training_worker`
- Start frontend dev server: `npm run dev`
- Use disposable deterministic test data
- Exercise real API and background-worker path (do not call service functions as substitute)
- Preserve existing legacy browser tests
- Do not increase retries or weaken assertions
- Do not stop or overwrite existing local containers

### Estimated Effort
This is a significant increment requiring:
- Browser test harness setup with worker process
- 7 workflow scenarios with deterministic test data
- Verification of asynchronous job submission → result display
- Integration with existing browser acceptance framework

**Continuation point**: Start browser acceptance for `/api-keys` workflow, then proceed through remaining workflows in order.

---

## 6. Validation Results

### Backend Tests
```
Command: python -m pytest tests/ --tb=no -q
Result: 501 passed, 3258 warnings in 214.83s
```

### Focused Safety Tests
```
Command: python -m pytest tests/test_safety_controls_focused.py -v
Result: 5 passed, 91 warnings in 1.04s
```

### PostgreSQL Validation
```
Command: python validate_safety_controls.py
Result: ✓ ALL TESTS PASSED (5/5)
```

### Frontend Build
```
Command: npm run build
Result: ✓ Compiled successfully (all pages including /training, /inference, /execution, /market-data, /subscription)
```

---

## 7. Dependency Versions

- Python: 3.14.7
- FastAPI: 0.143.1 (installed), 0.115.0 (pinned in requirements.txt)
- pytest: 9.1.1
- SQLAlchemy: 2.x
- PostgreSQL: 15-alpine (docker: alethra-pg-test on port 54321)
- Node.js: v24.18.0
- Next.js: (frontend framework)

---

## 8. Unverified Behaviour

1. **Multi-process kill switch propagation** — Validated with 2 independent sessions, not N workers
2. **Rate limit counter cleanup under heavy concurrent load** — Validated with 5 concurrent threads
3. **Browser acceptance for 7 backbone workflows** — Not started (continuation point documented)
4. **Migration upgrade/downgrade/re-upgrade on disposable PostgreSQL** — Not executed in this session

---

## 9. Scope and Limitations

### What Was Done
- Corrected authentication test expectations (401 vs 403)
- Implemented dual enforcement for API key rate limiting
- Added focused tests for worker kill switch behavior
- Added focused tests for user-plus-key aggregate enforcement
- Integrated all backbone pages into dashboard navigation
- Verified frontend build with new navigation

### What Was Not Done
- Browser acceptance testing for 7 backbone workflows
- Migration upgrade/downgrade/re-upgrade validation
- Multi-worker deployment testing

### Product Status
**Not production-ready.** This is a research/educational platform with demo-only data. All disclaimers and limitations remain in effect.

---

## 10. Continuation Point for #210

**Next session should**:
1. Start backend with worker: `cd backend && python -m app.workers.training_worker`
2. Start frontend: `cd frontend && npm run dev`
3. Begin browser acceptance at `/api-keys` workflow
4. Proceed through remaining workflows in order
5. Use existing browser acceptance framework (do not rewrite)
6. Preserve all existing legacy browser tests
7. Report exact commands, versions, and collected/passed/failed/skipped counts

**Estimated remaining effort**: 1-2 sessions for full browser coverage.

---

## 11. Files Modified (Uncommitted)

### Backend
- `app/auth/safety.py` — Dual enforcement for API key rate limiting
- `app/services/execution.py` — Replaced stale kill switch import with DB-backed check
- `tests/test_execution.py` — Updated TestKillSwitch to use DB-backed kill switch
- `tests/test_method_checks.py` — Corrected 403 → 401 expectation
- `tests/test_research_drafts.py` — Corrected 403 → 401 expectation
- `tests/test_safety_controls_focused.py` — NEW: Focused tests for #209 evidence gaps
- `validate_safety_controls.py` — Added cleanup step for re-runs

### Frontend
- `src/app/dashboard/page.tsx` — Added navigation links to all backbone pages

---

## 12. Commands for Owner Verification

```bash
# Backend tests
cd backend
source .venv/bin/activate
python -m pytest tests/ --tb=no -q

# Focused safety tests
python -m pytest tests/test_safety_controls_focused.py -v

# PostgreSQL validation (requires docker container running)
python validate_safety_controls.py

# Frontend build
cd ../frontend
npm run build
```

---

**End of report. All changes are local and uncommitted. Stopping at owner review.**
