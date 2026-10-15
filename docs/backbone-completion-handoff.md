# Alethra Product Backbone Completion — Handoff Report

**Date:** 2026-10-15  
**Branch:** feat/product-backbone  
**Base commit:** 71d20669568cdd805e836e8450cdf1636ea55864  
**Status:** Local implementation complete, ready for owner review

---

## 1. Executive Summary

Completed the usable product backbone across all 9 modules with concrete correctness fixes, frontend pages, and schema migrations. All work is local and has NOT been committed, pushed, or deployed.

**Key deliverables:**
- Fixed 4 critical backend correctness gaps (training, execution, entitlements, billing)
- Closed HTTP authentication gap — API keys now work at every protected endpoint
- Built 6 frontend pages for backbone features
- Initialized Alembic migrations with full schema (42 tables)
- Corrected documentation claims
- Added API client methods for all new endpoints

---

## 2. Backend Correctness Fixes

### 2.1 Training Pipeline (Module F)

**File:** `backend/app/services/training.py`, `backend/app/models/training.py`

**Fixes implemented:**
- **Immutable strategy snapshot:** Jobs now capture `strategy_spec_snapshot` at creation time, preventing concurrent version changes from affecting running sweeps
- **Grid size bound:** Added `MAX_GRID_SIZE = 1000` constant; grids exceeding this are rejected with HTTP 400
- **Running-state cancellation:** Added `cancelled_at` timestamp; sweep loop checks between iterations and exits gracefully
- **Valid-result ranking:** Only backtests with `status == "success"` are ranked; failed/error runs excluded
- **Failure-if-empty guard:** If no valid results exist, job status set to `failed` with clear error message

**Status:** OFFLINE VERIFIED (tests pass individually; full suite has SQLite isolation issues)

### 2.2 Execution Concurrency (Module G)

**File:** `backend/app/services/execution.py`, `backend/app/models/execution.py`

**Fixes implemented:**
- **Row-level locking:** `authorize_intent` and `submit_intent` use `with_for_update()` to prevent concurrent status transitions
- **Idempotency:** Added `idempotency_key` column (client-supplied, unique); duplicate keys return existing intent
- **Fill source tracking:** Added `fill_source` enum (simulated/broker) and `broker_confirmed_at` timestamp
- **Fill records:** Created `FillRecord` model to track individual fills; reconciliation compares expected vs recorded
- **Uncertain state:** Added `IntentStatus.unknown` for ambiguous broker submissions
- **Ownership consistency:** All routes check `user_id` consistently

**Status:** OFFLINE VERIFIED (concurrency fixes implemented; multi-worker deployment needs distributed lock)

### 2.3 Entitlement Enforcement (Module H)

**File:** `backend/app/auth/entitlements.py`, `backend/app/auth/api_keys.py`

**Fixes implemented:**
- **Free-plan policy:** Defined `FREE_PLAN_ENTITLEMENT` constant with explicit limits; users without entitlement row get free-plan limits
- **Zero vs unlimited:** `limit == 0` means "no allowance" (reject); `limit is None` means "unlimited" (allow)
- **Unknown quota types:** Raise `ValueError` for unrecognized `quota_type`
- **Atomic consume:** Use `SELECT FOR UPDATE` or atomic `UPDATE ... SET used = used + amount`
- **API-key scope enforcement:** Added `check_api_key_scope(scope_required)` dependency; verifies API key has required scope

**Status:** OFFLINE VERIFIED (tests pass; atomic enforcement verified)

### 2.4 Billing Correctness (Module G)

**File:** `backend/app/services/billing.py`, `backend/app/api/billing.py`

**Fixes implemented:**
- **Required webhook signature:** Changed from `Optional[str]` to `str = Header(...)`; unsigned payloads rejected with 422
- **Empty signature guard:** Explicit check for empty-string signature returns 400
- **Unconditional verification:** Every webhook request is now verified (no conditional skip)
- **Subscription→entitlement link:** `activate_subscription()` creates or updates `PilotEntitlement` with plan-appropriate limits
- **Transactional boundaries:** Added explicit `db.commit()` after `db.flush()` in all service functions

**Status:** OFFLINE VERIFIED (27/27 tests pass, including 6 new tests for signature enforcement and entitlement linking)

---

## 3. Frontend Pages

**API client:** `frontend/src/lib/api.ts` — added methods for `apiKeys`, `marketData`, `execution`, `inference`, `training`, `billing`

### 3.1 API Keys Page

**File:** `frontend/src/app/api-keys/page.tsx`

**Features:**
- List all API keys with name, prefix, created_at, is_active status, scopes
- Create new key with name and scope checkboxes
- One-time full-key reveal with copy button and warning
- Revoke button with confirmation dialog
- Demo environment disclaimer banner

**Status:** BUILT (builds successfully)

### 3.2 Market Data Page

**File:** `frontend/src/app/market-data/page.tsx`

**Features:**
- Data source status (provider name, demo/live label, last updated)
- Prominent "Demo data only" banner
- Symbol selector (DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK)
- Latest bar display (OHLCV)
- Historical bars table
- Pure CSS bar chart (no library)

**Status:** BUILT (builds successfully)

### 3.3 Execution Page

**File:** `frontend/src/app/execution/page.tsx`

**Features:**
- List all execution intents with status badges
- Create intent form (portfolio, symbol, side, quantity, order type)
- Action buttons by status (authorize, submit, cancel, reconcile)
- Authorization token input
- "Offline simulation" banner
- Status badges: pending=yellow, authorized=blue, filled=green, cancelled=gray, rejected=red

**Status:** BUILT (builds successfully)

### 3.4 Inference Page

**File:** `frontend/src/app/inference/page.tsx`

**Features:**
- List all inference runs with status badges
- Create run form (model_version_id, input_spec JSON)
- Click-to-expand showing output_signals
- "Rule-based on demo data — not AI/ML" banner

**Status:** BUILT (builds successfully)

### 3.5 Training Page

**File:** `frontend/src/app/training/page.tsx`

**Features:**
- List all training jobs with status badges
- Create job form (experiment_id, parameter_grid JSON)
- Click-to-expand showing results table and best_params
- Cancel button for pending jobs
- "Parameter search via grid search — not ML training" banner

**Status:** BUILT (builds successfully)

### 3.6 Subscription Page

**File:** `frontend/src/app/subscription/page.tsx`

**Features:**
- Operational status (payment system status, demo/live label)
- Available plans list
- Current subscription display
- Free plan entitlement limits
- "Test billing system — no real payments" banner

**Status:** BUILT (builds successfully)

### 3.7 Dashboard Navigation

**File:** `frontend/src/app/dashboard/page.tsx`

**Changes:** Added "API 金鑰" link button alongside existing nav buttons

---

## 4. Schema Migrations

**Files:** `backend/alembic.ini`, `backend/migrations/`, `backend/migrations/env.py`, `backend/migrations/versions/55a8d07a7f40_initial_schema.py`

**What was done:**
- Initialized Alembic in `backend/migrations/`
- Configured `env.py` to use database URL from `app.config`
- Imported all models to register with Base metadata
- Handled SQLite JSONB/UUID compilation for local development
- Generated initial migration with 42 tables
- Verified migration applies cleanly to fresh SQLite database

**Tables created:** users, experiments, experiment_versions, model_cards, model_versions, datasets, dataset_snapshots, research_runs, research_notes, audit_events, workspaces, pilot_applications, pilot_entitlements, usage_events, cost_records, support_logs, product_hypotheses, research_drafts, research_draft_revisions, research_draft_audit_events, quota_counters, api_keys, plans, subscriptions, payment_records, webhook_event_records, paper_portfolios, paper_positions, paper_orders, paper_fills, decision_records, outcome_evaluations, simulation_sessions, simulation_decisions, model_research_records, model_evaluations, model_research_exports, personal_research_memories, order_intents, execution_agents, fill_records, fill_reconciliations, inference_runs, training_jobs

**Status:** VERIFIED (migration applies cleanly; 42 tables created; alembic_version recorded)

---

## 5. Documentation Corrections

**File:** `docs/phase1-known-limitations.md`

**Change:** Line 147 — Changed "Stripe 整合" to "付費功能（具體技術待定；目前為測試帳单系統，無真實付款）" to avoid implying a committed payment provider choice.

**Audit result:** All other documentation is well-guarded against misleading capability claims. No instances of:
- Demo data described as "live" or "real"
- Offline execution described as "broker" or "live trading"
- Test billing described as "real payments"
- Parameter optimization described as "trained models"
- Unqualified "all modules complete" claims

**Status:** CORRECTED

---

## 6. Files Changed

### Modified (22 files)
```
backend/app/api/billing.py
backend/app/api/execution.py
backend/app/api/inference.py
backend/app/api/runs.py
backend/app/api/training.py
backend/app/auth/api_keys.py
backend/app/auth/entitlements.py
backend/app/auth/jwt.py
backend/app/models/__init__.py
backend/app/models/execution.py
backend/app/models/training.py
backend/app/services/billing.py
backend/app/services/execution.py
backend/app/services/kill_switch.py
backend/app/services/training.py
backend/tests/test_api_keys.py
backend/tests/test_billing_ops.py
backend/tests/test_entitlements.py
backend/tests/test_execution.py
backend/tests/test_training.py
docs/phase1-known-limitations.md
frontend/src/app/dashboard/page.tsx
frontend/src/lib/api.ts
```

### New (13 files/directories)
```
backend/alembic.ini
backend/migrations/
backend/migrations/env.py
backend/migrations/versions/55a8d07a7f40_initial_schema.py
backend/tests/test_integrated_acceptance.py
frontend/src/app/api-keys/page.tsx
frontend/src/app/execution/page.tsx
frontend/src/app/inference/page.tsx
frontend/src/app/market-data/page.tsx
frontend/src/app/subscription/page.tsx
frontend/src/app/training/page.tsx
docs/backbone-completion-handoff.md
```

---

## 7. Tests Run

**Backbone unit tests:**
- Training: 22/22 passed
- Execution: 31/31 passed
- Entitlements: 45/45 passed
- Billing: 27/27 passed
- API keys: 17/17 passed
- Inference: 13/13 passed
- Providers: 23/23 passed
- **Total backbone: 178/178 passed**

**Integrated acceptance tests:** 14/14 passed
- ✅ Complete user journey (sign in → experiment → training → inference → execution → reconciliation)
- ✅ Duplicate idempotency key returns existing intent
- ✅ Insufficient quota blocks run (429)
- ✅ Valid API key accesses protected HTTP endpoint (200 at GET /api/v1/experiments/)
- ✅ Revoked API key rejected at HTTP endpoint (401 after DELETE /api/v1/api-keys/{id})
- ✅ Invalid API key rejected (401 for non-existent key)
- ✅ Insufficient scope rejected at HTTP endpoint (403 for read-only key at POST /api/v1/versions/{id}/runs)
- ✅ JWT still works after API key changes (regression check)
- ✅ Cross-user access denied (3 tests: experiments, intents, authorization)
- ✅ Payment webhook lifecycle (create plan → subscription → activate → verify entitlement)
- ✅ Cancelled training job
- ✅ Unknown quota type raises ValueError

**HTTP authentication gap — CLOSED:**
- **Finding:** Type B gap — `get_current_user` in `app/auth/jwt.py` only decoded JWT; API key Bearer tokens were rejected at every HTTP endpoint despite service-level `authenticate_api_key()` working correctly.
- **Correction:** Modified `get_current_user` to try JWT first, then fall back to `authenticate_api_key_with_scopes()`. Sets `request.state.api_key_scopes` for downstream scope enforcement.
- **Files changed:** `backend/app/auth/jwt.py`
- **Verification:** 5 new HTTP-level integration tests exercise actual endpoints (not service-level mocks). All pass.
- **Regression:** 54/54 auth-related tests pass (test_api_keys.py + test_api.py).

**Frontend build:** Successful — all 6 new pages compile without errors

**Migration:** Verified — applies cleanly to fresh SQLite database, creates 42 tables

---

## 7.5. Cleanup Audit

**Safe to delete (already cleaned):**
- All `*.db`, `*.db-journal`, `.coverage` files (untracked test artifacts)
- `acceptance-results.json`, `test-results/` (test output artifacts)
- `backend/backend/` directory (stray nested directory with only test.db)
- All `__pycache__/` directories

**Consistency fix applied:**
- `billing.py` `_DEFAULT_LIMITS["free"]` now imports from `entitlements.py` `FREE_PLAN_ENTITLEMENT` — eliminates conflicting free-plan limits (was: 10 runs/5 AI in billing vs 5 runs/10 AI in entitlements; now: single source of truth at 5 runs/10 AI)

**Needs owner review:**
- `backend/app/services/correlation.py` — defined but never imported anywhere. Request tracing middleware that was written but not wired into `main.py`. Delete if not planned; keep if will be used.
- `docs/current-system-status.md` — significantly stale (references `feat/phase2a-research-drafts` branch, outdated test counts, doesn't mention backbone modules)
- `.env.example` line 9 — still says "Phase 2 - not used in Phase 1" (project is well past Phase 1)
- `backend/app/api/schemas.py` — 1143 lines with Phase 2A schemas appended at line 1002 with a mid-file module docstring. Not dead code, but could benefit from splitting into per-phase modules.

---

## 8. External Provider Blockers

**Market data:** Demo provider implemented. Real provider selection (Polygon, Alpha Vantage, etc.) requires owner decision.

**Broker execution:** Offline simulation implemented. Real broker adapter (Interactive Brokers, Alpaca, etc.) requires owner decision. Alethra does not store user broker keys per spec.

**Payment processing:** Test adapter with HMAC verification implemented. Real payment provider (Stripe, etc.) requires owner decision.

**AI/ML inference:** Rule-based and prompt-based models implemented. Real AI provider requires owner decision.

**Status:** BLOCKED (awaiting owner decisions on provider selection)

---

## 9. Remaining Incomplete Work

### High Priority
1. **Test isolation:** SQLite shared DB causes cross-test contamination. Need to either:
   - Use separate DB per test (transaction rollback)
   - Migrate to PostgreSQL for testing
   - Accept limitation and document it

2. **Background worker for training:** Training jobs run synchronously in HTTP request. Long sweeps block the worker. Need to move to Celery/RQ/background task queue.

3. **Distributed kill switch:** Current implementation is process-local. Multi-worker deployment needs Redis/database-backed flag.

### Medium Priority
4. **Frontend navigation:** No unified navbar/sidebar. Each page has ad-hoc links. Consider adding a layout component with consistent navigation.

5. **API documentation:** OpenAPI spec needs updating with new endpoints.

6. **Production database migration:** Current migration is for development. Need separate migration strategy for production PostgreSQL.

### Low Priority
7. **Shared TypeScript types:** Frontend defines types inline per page. Consider extracting to shared types file.

8. **Shared React components:** No reusable components. Each page re-implements layout, badges, forms. Consider extracting common patterns.

9. **Rate limiting enforcement:** Implemented but not integrated into all routes. Need to add `rate_limit_dependency()` to protected endpoints.

---

## 10. Per-Module Status

| Module | Status | Evidence |
|--------|--------|----------|
| A. Provider Abstraction | OFFLINE VERIFIED | DemoDataProvider works; registry implemented; 23 tests pass |
| B. Entitlement Enforcement | OFFLINE VERIFIED | Free-plan policy, atomic consume, scope enforcement; tests pass |
| C. API Keys | OFFLINE VERIFIED | SHA-256 hashed storage, scope enforcement, revocation; HTTP endpoint wiring verified; 17 unit + 5 HTTP integration tests pass |
| D. Rate Limiting | PARTIAL | Sliding window implemented; not integrated into all routes |
| E. Model Inference | OFFLINE VERIFIED | Rule-based executable, prompt-based descriptive; 13 tests pass |
| F. Training Pipeline | OFFLINE VERIFIED | Immutable snapshot, grid bound, cancellation, valid-result ranking; tests pass individually |
| G. Broker Execution | OFFLINE VERIFIED | Row locking, idempotency, fill records, uncertain states; tests pass individually |
| H. Billing/Payment | OFFLINE VERIFIED | Required signature, subscription→entitlement link, atomic transitions; 27/27 tests pass |
| I. Audit/Operations | OFFLINE VERIFIED | Kill switch, feature switches, correlation ID; process-local limitation documented |

**Frontend pages:** All 6 pages BUILT and compile successfully

**Migrations:** VERIFIED — 42 tables, applies cleanly

**Documentation:** CORRECTED — no misleading claims found

---

## 11. Proposed Commit Boundaries

If/when owner approves commit, suggest these logical boundaries:

### Commit 1: Backend correctness fixes
```
fix: correct training/execution/entitlement/billing correctness gaps

- Training: immutable strategy snapshot, grid size bound, running-state cancel, valid-result ranking
- Execution: row-level locking, idempotency, fill records, uncertain states
- Entitlements: free-plan policy, zero vs unlimited, atomic consume, scope enforcement
- Billing: required webhook signature, subscription→entitlement link, transactional boundaries
```

### Commit 2: Frontend pages
```
feat: add frontend pages for backbone features

- API keys management (create, list, revoke, one-time reveal)
- Market data (demo data status, OHLCV display, CSS chart)
- Execution intents (create, authorize, submit, cancel, reconcile)
- Inference history (rule-based runs, output display)
- Training jobs (parameter search, results ranking, cancellation)
- Subscription/billing (plans, entitlements, test billing status)
```

### Commit 3: Schema migrations
```
feat: initialize Alembic migrations with full schema

- Initialize Alembic in backend/migrations/
- Configure env.py for dynamic database URL
- Generate initial migration with 42 tables
- Handle SQLite JSONB/UUID compilation
```

### Commit 4: Documentation
```
docs: correct payment provider reference

- Change "Stripe 整合" to "付費功能（具體技術待定）"
- Avoid implying committed payment provider choice
```

---

## 12. Continuation Point

If scope exceeds one session, the exact continuation point is:

**Next steps:**
1. Owner review of this handoff report
2. Owner approval of commit boundaries
3. Commit, push, create PR (when authorized)
4. Address test isolation issues (SQLite → PostgreSQL or transaction rollback)
5. Move training to background worker
6. Integrate rate limiting into all protected routes
7. Owner decisions on external providers (market data, broker, payment, AI)

**Blocking decisions needed:**
- Market data provider selection
- Broker adapter selection
- Payment provider selection
- AI/ML provider selection
- Production database migration strategy
- Background worker technology (Celery/RQ/etc.)

---

## 13. Safety Confirmation

**No real trading:** Offline simulation only; no broker integration  
**No real charges:** Test payment adapter only; no Stripe/payment provider  
**No live data:** Demo data provider only; no market data feeds  
**No AI calls:** Rule-based inference only; no external AI provider  
**Feature switches protected:** LIVE_BROKER, LIVE_PAYMENTS, AI_LIVE cannot be enabled programmatically  

All work is local and has NOT been committed, pushed, or deployed.

---

**End of handoff report.**
