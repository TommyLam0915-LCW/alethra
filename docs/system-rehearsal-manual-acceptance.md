# System Rehearsal — Manual Acceptance Report

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**Status**: Research Prototype — Rehearsed Locally, Pending Manual Acceptance
**Execution method**: Automated API script (`acceptance-test-execution.py`) against local stack

> **Important distinction**: The A01–A12 scenarios passed through scripted API-level acceptance rehearsal. This does not replace a human browser-based usability acceptance. All scenarios were verified via HTTP API calls, not through the frontend UI in a browser.

---

## Pre-Acceptance Setup

**Branch**: chore/system-rehearsal-20261012
**Working tree**: Clean (all reconciliation changes committed before testing)

### Backend Test Command

```
cd backend
pytest --tb=short -q --cov=app --cov-report=term-missing
```

| Metric | Value |
|--------|-------|
| Test count | 180 |
| Passed | 180 |
| Failed | 0 |
| Skipped | 0 |
| Coverage | 86% |
| Warning count | 16804 |
| Duration | 76.13s |
| Timestamp | 2026-10-12T18:00 UTC+8 |

### Frontend Build

```
cd frontend
npm ci
npm run build
```

Build completed successfully.

### Local Stack

- Backend: `APP_ENV=production` with fresh `JWT_SECRET_KEY` (32+ chars), SQLite via `DATABASE_URL=sqlite:///./acceptance_test.db`
- Backend started: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
- `/health` responds: `{"status": "healthy"}`
- CORS: configured for `http://localhost:3000`

---

## Summary

| Metric | Value |
|--------|-------|
| Total scenarios | 12 |
| Passed | 12 |
| Failed | 0 |
| Blocked / unverified | 0 |
| Pass rate | 100.0% |
| Backend tests | 184 passed, 0 failed, 86% coverage |
| Warning count | 17378 |
| Bugs found & fixed | 2 |
| Tests added | 4 (UUID JSON boundary regression tests) |

---

## Environment

- **Backend**: FastAPI + Python 3.14.7, uvicorn on 127.0.0.1:8000
- **Database**: SQLite (acceptance_test.db, fresh per run)
- **APP_ENV**: production
- **Engine versions**: Backtest 1.1.0, Simulation 1.0.0
- **Demo data range**: 2024-01-02 to 2024-02-13 (30 trading days)

---

## Scenario Results

### A01 — Landing Page and Product Boundaries: PASS

| Check | Result |
|-------|--------|
| Branding | Alethra |
| Tagline | Ideas, tested. Decisions, yours. |
| Disclaimer present | true |
| No broker language | true |
| No live trading claim | true |
| No stock recommendation | true |
| No API key storage | true |

### A02 — Registration and Login: PASS

| Check | Result |
|-------|--------|
| User A registered | true |
| User A login | true |
| User A /me verified | true |
| User B registered | true |
| User B login | true |
| JWT tokens received | true |

### A03 — Create Buy-and-Hold Research Experiment: PASS

| Check | Result |
|-------|--------|
| Template | buy_and_hold |
| Asset | DEMO-ALPHA |
| Benchmark | DEMO-BENCHMARK |
| Commission rate | 0.001 (non-zero) |
| Slippage rate | 0.0005 (non-zero) |
| Initial cash | 100000 |
| Hypothesis present | true |
| Falsification criteria present | true |
| Demo data only (no real market data) | true |

### A04 — Historical Backtest Result: PASS

| Check | Result |
|-------|--------|
| Run status | completed |
| Total return | 8.6735% |
| Total trades | 2 |
| Total commissions | 208.682 |
| Total slippage cost | 104.3432 |
| Config hash present | true |
| Dataset checksum present | true |
| Engine version | 1.1.0 |
| Methodology included | true |
| Historical simulation only (no forward claim) | true |

Metrics keys verified: initial_cash, final_equity, total_return_pct, benchmark_total_return_pct, excess_return_pct, max_drawdown_pct, total_trades, total_commissions, total_slippage_cost, time_in_market_pct, trading_days, annualized_return_pct, annualized_volatility_pct.

### A05 — Versioning and Change Reason: PASS

| Check | Result |
|-------|--------|
| Versions before change | 1 |
| Versions after change | 2 |
| Change reason recorded | "Acceptance test: increased commission from 0.001 to 0.002 to test versioning" |
| Original version preserved | true |
| Original commission unchanged | 0.001 |
| New commission | 0.002 |

### A06 — Event Timeline and Research Notes: PASS

| Check | Result |
|-------|--------|
| Timeline events before note | 10 |
| Timeline events after note | 11 |
| Note created | true |
| Chronological order | true |
| Old events unchanged | true |

Event types present: experiment_created, dataset_selected, strategy_template_selected, strategy_version_created, research_run_started, parameter_changed, research_run_completed, warning_generated, research_note_added.

### A07 — Historical Replay Simulation: PASS

| Check | Result |
|-------|--------|
| Strategy locked before simulation | true |
| Session status after advance | active |
| Snapshots advanced | 3 |
| Total decisions | 3 |
| Observed days | 3 |
| Data cutoff | 2024-02-13 |
| Paper equity | 103740.53 |
| Decision types | simulated_entry, no_action, no_action |
| Labeled as historical replay | true |
| No forward simulation claim | true |

### A08 — Paper Portfolio: PASS

| Check | Result |
|-------|--------|
| Virtual cash only | true |
| No real money | true |
| No broker connection | true |
| Decision recorded (actor_type=human, confidence=medium) | true |
| Order with rationale filled | filled |
| Risk check status | passed |
| Fills count | 1 |
| Positions count | 1 |
| Pause blocks orders | true |
| Resume allows orders | true |
| Retire preserves history | true |
| Order history preserved after retire | 2 |
| Final status | retired |

### A09 — Model Research Record and Model Card: PASS

| Check | Result |
|-------|--------|
| Model record created | true |
| Model version created | true |
| Model card created | true |
| Version locked | true |
| No buy/sell output in card | true |
| No target weights | true |
| No broker field | true |
| No exchange credential | true |
| No live execution | true |
| No profitability certification | true |

### A10 — Model Research Export Package: PASS

| Check | Result |
|-------|--------|
| Export status | completed |
| Package checksum present | true |
| Downloadable | true |
| No passwords in package | true |
| No JWT secrets in package | true |
| No API keys in package | true |
| No buy/sell commands | true |
| No live execution instructions | true |

### A11 — Privacy and Access Isolation: PASS

| Check | Result |
|-------|--------|
| User B blocked from User A's experiment | true |
| User B blocked from timeline | true |
| User B blocked from notes | true |
| User B blocked from runs list | true |
| User B blocked from run detail | true |
| User B blocked from simulation | true |
| User B blocked from portfolio | true |
| User B blocked from model research | true |
| User B blocked from admin | true |
| Isolation enforced | true |

### A12 — Production Safety Validation: PASS

| Check | Result |
|-------|--------|
| Config tests passed | 10 |
| Config tests failed | 0 |
| Rejects insecure JWT default | true |
| Enforces minimum secret length (32 chars) | true |
| Production mode validated | true |

---

## Bugs Found and Fixed

### Bug 1: UUID serialization in model_research.py

- **File**: `backend/app/api/model_research.py`
- **Symptom**: `Object of type UUID is not JSON serializable` when creating a model version with `linked_experiment_version_ids`
- **Root cause**: UUID objects from SQLAlchemy stored directly into JSON column without conversion to strings
- **Fix**: Convert UUID lists to string lists before assignment: `[str(v) for v in payload.linked_experiment_version_ids]`

### Bug 2: UUID type mismatch in model_research_export.py

- **File**: `backend/app/api/model_research_export.py`
- **Symptom**: `'str' object has no attribute 'hex'` when building export package with linked experiment versions
- **Root cause**: String IDs from JSON column passed to SQLAlchemy `.in_()` filter, which expects UUID objects
- **Fix**: Convert string IDs back to UUID objects before querying: `[uuid.UUID(str(eid)) for eid in linked_experiment_ids]`

Both bugs share the same root cause: the boundary between JSON storage (strings) and SQLAlchemy UUID columns (UUID objects) was not handled consistently.

**Regression tests added** (4 tests in `TestUUIDJSONBoundaryRegression` class):
- `test_model_research_serializes_uuid_references_to_json_strings` — verifies UUID→string conversion on write
- `test_model_research_export_converts_json_string_ids_to_uuid_objects` — verifies string→UUID conversion on export read
- `test_model_research_export_rejects_invalid_uuid_strings_safely` — verifies Pydantic rejects invalid UUIDs with 422
- `test_model_research_export_handles_duplicate_linked_ids_deterministically` — verifies duplicate IDs produce consistent checksums

---

## Product Boundary Verification

| Boundary | Verified |
|----------|----------|
| Not a broker — no brokerage accounts, no order routing | A01, A08 |
| Not an advisor — no buy/sell recommendations | A01, A09 |
| No live trading — historical replay and paper simulation only | A07, A08 |
| No real market data — demo data only (DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK) | A03, A04 |
| No API key storage — no external provider credentials persisted | A01, A10 |
| No payment processing | A01 |
| User isolation enforced | A11 |
| Production safety (JWT secret validation) | A12 |

---

## Backend Test Suite

```
184 passed, 0 failed (88.59s)
```

No regressions from UUID bug fixes. All pre-existing tests continue to pass. Four new UUID JSON boundary regression tests added.

---

## Final Handoff

1. **Manual scenarios passed**: 12 (A01–A12)
2. **Manual scenarios failed**: 0
3. **Manual scenarios blocked or unverified**: 0
4. **Evidence references**:
   - `docs/system-rehearsal-manual-acceptance.md` (this document)
   - `docs/manual-acceptance-findings.md` (bug details)
   - `/tmp/acceptance-results.json` (machine-readable scenario results)
   - `acceptance-test-execution.py` (reproducible test script)
5. **Bugs found and fixed**: 2
   - UUID serialization in `backend/app/api/model_research.py` (JSON column received UUID objects)
   - UUID type mismatch in `backend/app/api/model_research_export.py` (string IDs passed to SQLAlchemy UUID column filter)
6. **Tests added**: 4 — UUID JSON boundary regression tests
7. **Full test-suite result after fixes**: 184 passed, 0 failed, 86% coverage, 17378 warnings
8. **Current warning count**: 17378 (all from third-party deprecation warnings — Python 3.16 asyncio, SQLAlchemy 2.0, Pydantic V2, jose JWT utcnow)
9. **Current status recommendation**: "Research Prototype — Rehearsed Locally, Pending Manual Acceptance" — retained per specification; no Phase 2 features added, no live market data, no broker connections
10. **Merge recommendation**: Ready for human review and merge
11. **Next recommended development task**: Complete manual browser-based acceptance testing (human tester through frontend UI) to confirm all 12 scenarios pass end-to-end via the browser, not only via API script

---

## Sign-Off

| Tester | Date | Result |
|--------|------|--------|
| Automated acceptance script | 2026-10-12 | Pass (12/12) |

**Do not merge automatically. Human review required.**
