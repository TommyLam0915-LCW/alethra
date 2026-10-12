# System Rehearsal — Final Release-Readiness Report

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**System**: Alethra Investment Research Workspace
**Rehearsal Type**: Pre-release local rehearsal (full system)
**Verdict**: **NOT PRODUCTION-READY — Pilot-Ready for Research Use Only**

---

## 1. Product Identity Verification

**Claim**: Alethra is an Investment Research Workspace.
**Tagline**: "Ideas, tested. Decisions, yours."

**Verified**:
- ✅ Brand endpoint returns correct name and tagline
- ✅ No broker, exchange, or payment integrations present
- ✅ No live trading capability in codebase
- ✅ No investment advice language in UI or API
- ✅ Disclaimers present on all research/trading pages
- ✅ Export packages exclude broker credentials and trading instructions

**Finding**: Product identity is consistent across code, docs, and UI.

---

## 2. Safe Operating Rules

### 2A. No live market data
- ✅ `DATA_PROVIDER=demo` is the only implemented provider
- ✅ No external API calls for market data
- ✅ All price data is deterministically pre-generated

### 2B. No broker/exchange integration
- ✅ No broker SDK, API keys, or order routing code
- ✅ Paper portfolio explicitly labeled as research tool
- ✅ Schema tests verify no broker fields exist

### 2C. No payment processing
- ✅ `ENABLE_LIVE_PAYMENTS=false` default
- ✅ No payment gateway integration
- ✅ Pilot system uses entitlement-based access, not payments

### 2D. Production safety
- ✅ **REPAIRED**: Config now validates JWT secret in production/staging mode
- ✅ Rejects well-known defaults (change-me, secret, changeme, test, empty)
- ✅ Enforces minimum 32-character secret for production
- ✅ 10 new tests verify this behavior

### 2E. Every change has problem/reason/test/docs
- ✅ All 3 repairs documented in docs/system-rehearsal-repair-log.md
- ✅ New tests added for config validation (10 tests)
- ✅ Docs updated for labeling changes

---

## 3. Environment & Build

| Check | Result |
|-------|--------|
| Python version | 3.14.7 ✅ |
| Backend framework | FastAPI ✅ |
| Frontend framework | Next.js 14 + TypeScript ✅ |
| Dependency management | pip (backend), npm (frontend) ✅ |
| Virtual environment | venv/ properly configured ✅ |
| .gitignore | Properly excludes venv/, node_modules/, .env, *.db ✅ |
| .env.example | Contains placeholders only, no secrets ✅ |
| No secrets committed | ✅ Verified — no API keys, passwords, or tokens in repo |

---

## 4. Test Rehearsal

**Command**: `pytest --tb=short -q --cov=app --cov-report=term-missing`

| Metric | Value |
|--------|-------|
| Total tests | 180 |
| Passed | 180 |
| Failed | 0 |
| Coverage | 86% |
| Duration | ~83min (includes full suite with clean DB) |
| Warnings | 16,167 (down from 17,137 after Alethra-owned fixes; >99% are third-party) |

**Test files (per-file counts from `pytest --collect-only -q`)**:
- test_pilot.py: 51 tests
- test_engine.py: 29 tests
- test_api.py: 29 tests
- test_model_research.py: 23 tests
- test_model_research_export.py: 16 tests
- test_paper_portfolio.py: 12 tests
- test_simulation.py: 10 tests
- test_config.py: 10 tests
- **Total: 180 tests**

**Warning remediation applied this rehearsal**:
- ✅ `datetime.utcnow()` → `datetime.now(timezone.utc).replace(tzinfo=None)` (23 call sites)
- ✅ Pydantic `class Config` → `model_config = ConfigDict(...)` (29 schemas)
- ✅ SQLite test isolation: `PRAGMA foreign_keys=OFF` event listener in conftest.py
- ⚠️ `declarative_base()` → `DeclarativeBase` reverted (broke test isolation)
- ⚠️ Remaining warnings are >99% third-party (pytest-asyncio, fastapi, sqlalchemy, jose)

**Verdict**: All tests pass. No functional regressions.

---

## 5. Core Engine Verification

### Backtest Engine (v1.1.0)
- ✅ 3 strategy templates: Buy & Hold, SMA Crossover, Price Change Fixed Hold
- ✅ Transaction ledger: 16 fields per transaction (all required fields present)
- ✅ Benchmark: execution-consistent (same commission, slippage, whole-share sizing)
- ✅ Metrics: total_return, final_equity, benchmark_return, excess_return, max_drawdown, commissions, slippage, trades, time_in_market
- ✅ Annualization guard: < 252 days shows reason dict, not zero
- ✅ Config hash (SHA-256) for determinism
- ✅ Dataset checksum for reproducibility

### Simulation Engine (v1.0.0)
- ✅ Strategy locking enforced (immutable after lock)
- ✅ Snapshot incremental release
- ✅ Decision audit trail (step_number, snapshot_date, paper_equity)
- ✅ Forced exit on simulation end
- ✅ **REPAIRED**: Labeled as "Historical Replay" not "Forward Simulation"
- ⚠️ Uses pre-existing demo data (known limitation, documented)

---

## 6. Event Ledger

- ✅ AuditEvent model with 11 event types
- ✅ Event emission integrated into API routes
- ✅ Events recorded for: experiment_created, run_started, run_completed, simulation_started, strategy_locked, export_generated, etc.
- ✅ Timeline API endpoint for chronological event view
- ✅ Admin dashboard shows event funnel

---

## 7. Simulation Integrity

**Critical Finding (REPAIRED)**:
- Phase 3A simulation was labeled "forward simulation" (前瞻模擬) but is actually historical replay
- All demo data exists before simulation starts
- Engine releases snapshots incrementally but from pre-existing data

**Repair Applied**:
- ✅ UI labels updated: 前瞻模擬 → 歷史重播模擬
- ✅ Methodology status: forward_observation → historical_replay
- ✅ Documentation updated with methodology clarification
- ✅ Known limitation documented

**Integrity Controls**:
- ✅ Strategy locked before simulation starts
- ✅ strategy_spec immutable after lock
- ✅ config_hash recorded at lock time
- ✅ data_cutoff_at enforced per decision
- ✅ Decision audit trail complete

---

## 8. Paper Portfolio

- ✅ Virtual cash tracking (initial, current, realized P&L, unrealized P&L)
- ✅ Position tracking (entry price, quantity, status)
- ✅ Order recording (side, quantity, rationale, status)
- ✅ Decision recording (actor_type, decision_type, expected_outcome, falsification_condition)
- ✅ Risk controls: position limits, daily order limits, asset limits
- ✅ Status lifecycle: active → paused → retired (immutable after retired)
- ✅ **REPAIRED**: Bilingual disclaimers (English + Traditional Chinese)
- ✅ No broker fields in schema (verified by test)

---

## 9. Model Research Record

- ✅ Model lifecycle: draft → researching → historical_test_complete → locked_for_simulation → forward_observation → evidence_insufficient → retired
- ✅ Version management with locking
- ✅ Locked versions immutable (cannot modify, cannot revoke without reason)
- ✅ Revoked versions permanent (cannot re-lock, cannot revoke again)
- ✅ Evaluation records (type, verdict, notes)
- ✅ Status transition validation (no backward transitions from terminal states)
- ✅ **REPAIRED**: Status label shows "重播觀察中" not "前瞻觀察中"

---

## 10. Export Verification

- ✅ Export package contains: manifest, experiments, runs, simulations, paper_portfolios, model_research, timeline, version_history
- ✅ SHA-256 checksum for integrity
- ✅ Generated_at timestamp
- ✅ **Excludes**: broker credentials, user passwords, JWT secrets, PII, live trading instructions
- ✅ Privacy controls: personal research memory requires user consent
- ✅ Training opt-in is optional and requires explicit confirmation
- ✅ Export disclaimer present

---

## 11. Frontend E2E (Manual Checklist Provided)

**Automated**: Not performed (requires browser automation setup)
**Manual checklist**: Provided in docs/system-rehearsal-manual-acceptance.md

**Key pages verified by code inspection**:
- ✅ /dashboard — disclaimer present
- ✅ /paper-portfolio — bilingual disclaimer present
- ✅ /paper-portfolio/[id] — bilingual disclaimer present
- ✅ /experiments/[id]/simulation — methodology status shows "historical_replay"
- ✅ /models — status label shows "重播觀察中"
- ✅ /admin — funnel label shows "啟動歷史重播模擬"
- ✅ /pilot — label shows "歷史重播模擬上限"

---

## 12. Security & Reliability Hardening

| Control | Status | Notes |
|---------|--------|-------|
| JWT authentication | ✅ | HS256, configurable expiry |
| Admin authorization | ✅ | Required for admin endpoints |
| CORS configuration | ✅ | Configurable origins |
| **Production config validation** | ✅ | **REPAIRED** — rejects insecure defaults |
| No secrets in repo | ✅ | .gitignore + .env.example |
| Input validation | ✅ | Pydantic schemas on all endpoints |
| SQL injection protection | ✅ | SQLAlchemy ORM, parameterized queries |
| XSS protection | ✅ | React escapes by default |
| CSRF protection | ⚠️ | Not explicitly implemented (JWT in header mitigates) |
| Rate limiting | ❌ | Not implemented (Phase 2 scope) |
| DDoS protection | ❌ | Not implemented (infrastructure scope) |
| Audit logging | ✅ | Event ledger records all significant actions |

---

## 13. Limited Optimisation (Repairs Applied)

| Repair | Defect | Files Changed | Tests Added |
|--------|--------|---------------|-------------|
| R-1: Simulation labeling | D-1 | 9 files | 0 (labeling only) |
| R-2: Bilingual disclaimers | D-2 | 2 files | 0 (static text) |
| R-3: Production safety | D-3 | 2 files | 10 |

**Total new tests**: 10
**Total tests after repair**: 180 (was 170)
**Coverage after repair**: 86% (unchanged)

---

## 14. Required Documentation

All required documents created:

| Document | Status |
|----------|--------|
| docs/system-rehearsal-initial-findings.md | ✅ Created |
| docs/system-rehearsal-test-results.md | ✅ Created |
| docs/system-rehearsal-repair-log.md | ✅ Created |
| docs/system-rehearsal-manual-acceptance.md | ✅ Created |
| docs/current-system-status.md | ✅ Created |
| docs/phase2-ai-research-assistant-backlog.md | ✅ Created |

**Updated documents**:
- README.md (labeling updated)
- docs/phase3a-forward-simulation.md (title + methodology clarification)
- docs/research-methodology.md (section heading + status table)
- docs/phase1-known-limitations.md (table entry + demo data note)

---

## 15. Known Limitations (Post-Repair)

1. **Demo data only** — No live market data integration
2. **Historical replay** — Simulation uses pre-existing demo data, not live forward observation
3. **Single-asset only** — No multi-asset portfolio support
4. **No AI assistant** — Phase 2 not implemented
5. **SQLite for tests** — Production requires PostgreSQL
6. **No walk-forward optimization** — Not implemented
7. **No rate limiting** — Not implemented (Phase 2 scope)
8. **`declarative_base()` deprecation** — SQLAlchemy 2.0 migration deferred (broke test isolation when attempted)
9. **Third-party deprecation warnings** — pytest-asyncio, fastapi, sqlalchemy, jose emit ~16,000 warnings (all cosmetic, non-blocking, outside Alethra's control)

---

## 16. Release Readiness Assessment

### What IS ready:
✅ Research backtesting with 3 strategy templates
✅ Strategy locking with immutable specs
✅ Historical replay simulation with audit trail
✅ Paper portfolio with risk controls
✅ Model research record with version management
✅ Export packages with integrity verification
✅ Pilot validation infrastructure
✅ Admin dashboard with metrics
✅ Bilingual disclaimers on all research pages
✅ Production safety validation
✅ 180 passing tests, 86% coverage

### What is NOT ready:
❌ Live market data integration
❌ AI research assistant (Phase 2)
❌ Multi-asset portfolio support
❌ Walk-forward optimization
❌ Rate limiting and DDoS protection
❌ Production deployment infrastructure
❌ Security audit by qualified reviewer
❌ Legal review of compliance requirements
❌ Penetration testing

---

## 17. Final Verdict

**Alethra is NOT production-ready.**

**Alethra IS pilot-ready for research use** with the following conditions:

1. **Pilot scope**: Research and education only, not live trading
2. **Data**: Demo data only, no live market data claims
3. **AI**: No AI assistant implemented (Phase 2 deferred)
4. **Infrastructure**: Requires proper deployment setup (PostgreSQL, secrets management, monitoring)
5. **Security**: Requires security audit before any production deployment
6. **Legal**: Requires legal review of disclaimers and compliance
7. **Labeling**: Simulation correctly labeled as "Historical Replay" not "Forward Simulation"

**Recommended next steps**:
1. Conduct manual acceptance testing (docs/system-rehearsal-manual-acceptance.md)
2. Deploy to staging environment with PostgreSQL
3. Conduct security audit
4. Implement Phase 2 AI assistant (docs/phase2-ai-research-assistant-backlog.md)
5. Integrate live market data provider
6. Obtain legal review
7. Conduct penetration testing
8. Only then consider production deployment

---

## Appendix: Test Results Summary

```
180 passed, 16167 warnings in 4981.40s
Coverage: 86% (3318 statements, 467 missed)
```

**Test breakdown** (from `pytest --collect-only -q`):
- Pilot tests: 51 (applications, entitlements, events, admin)
- Engine tests: 29 (strategies, ledger, benchmark, metrics)
- API tests: 29 (brand, auth, experiments, runs, export)
- Model research tests: 23 (lifecycle, versions, evaluations, memory)
- Export tests: 16 (packages, checksums, privacy, authorization)
- Paper portfolio tests: 12 (orders, positions, decisions, risk controls)
- Simulation tests: 10 (locking, cutoff, snapshots, decisions)
- Config tests: 10 (production safety, CORS)

**Warning baseline**: See docs/warning-baseline.md for full categorization.

---

**Report prepared by**: Release Engineering
**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**Base SHA**: 5a9741803b7bea5458bafb05c646cffe62048a82
**Status**: Rehearsal complete. Warning fixes applied. Test isolation repaired. All 180 tests passing.
