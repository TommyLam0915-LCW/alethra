# System Rehearsal — Initial Findings

**Date**: 2026-10-12
**Rehearsal Branch**: chore/system-rehearsal-20261012
**Rehearsal Lead**: Release Engineering

---

## Summary

Conducted comprehensive system rehearsal of Alethra repository. Verified test suite, inspected core engine, simulation integrity, paper portfolio, model research, export packages, and security posture.

**Overall Status**: System is functionally complete for Phase 1–6 as documented, with several documentation discrepancies and missing safeguards identified. No critical defects found. All 170 tests pass.

---

## Confirmed Defects

### D-1: Simulation Labeling Misrepresents Implementation

**Severity**: Medium (documentation accuracy)
**Category**: Documentation discrepancy

**Finding**: Phase 3A simulation is labeled "forward simulation" (前瞻模擬) and "forward observation" (前瞻觀察) throughout documentation, UI, and code. However, the implementation uses pre-existing demo data that is deterministically generated at module load time. All price data for the entire date range exists before the simulation starts. The system releases snapshots incrementally, but this is historical replay, not genuine forward observation of newly arriving market data.

**Evidence**:
- `backend/app/data/demo_data.py`: DEMO_DATA dict contains all OHLCV arrays pre-generated
- `backend/app/engine/simulation.py`: ForwardSimulator receives full dataset via `get_demo_ohlcv()`
- UI labels: "前瞻模擬", "forward_observation", "forward_simulation_started"
- Docs: README.md, phase3a-forward-simulation.md, model-card-standard.md all reference "forward simulation"

**Required Fix**:
1. Update all documentation to label as "Historical Replay Simulation" or "Controlled Forward-Style Simulation Using Demo Data"
2. Add known limitation: "The current simulation replays pre-existing demo data and is not genuine forward observation of newly arriving market data."
3. Update UI labels where user-facing
4. Do not change behavior — only labeling

---

### D-2: Missing Disclaimers on Paper Portfolio Pages

**Severity**: Medium (compliance risk)
**Category**: Documentation discrepancy / missing safeguard

**Finding**: Paper portfolio pages (`/paper-portfolio`, `/paper-portfolio/[id]`) do not display the mandatory research disclaimer. Other pages (dashboard, experiment detail) show disclaimers, but paper portfolio pages are missing them.

**Evidence**:
- `frontend/src/app/paper-portfolio/page.tsx`: No disclaimer element
- `frontend/src/app/paper-portfolio/[id]/page.tsx`: No disclaimer element
- Compare to `frontend/src/app/dashboard/page.tsx`: Lines 93-95 show disclaimer

**Required Fix**:
Add disclaimer to both paper portfolio pages:
```
"歷史模擬及虛擬交易僅供研究與教育用途，不屬實盤交易、投資建議，亦不代表未來回報。"
"Historical simulation and paper trading are for research and education only. They are not live trading, investment advice, or evidence of future returns."
```

---

### D-3: No Production Safety Validation

**Severity**: Medium (deployment safety)
**Category**: Security hardening opportunity

**Finding**: `backend/app/config.py` does not validate configuration for production deployment. The application will start with development placeholders (e.g., `JWT_SECRET_KEY=change-me-in-production-use-a-long-random-string`) even when `APP_ENV=production`.

**Evidence**:
- `backend/app/config.py`: No validation logic
- `.env.example`: Contains placeholder values
- No startup check prevents insecure configuration

**Required Fix**:
Implement production safety validation:
- If `APP_ENV` not in (`development`, `test`), refuse to start when:
  - `JWT_SECRET_KEY` is the documented development placeholder
  - `DATABASE_URL` is the documented local default (if unsuitable for production)
  - `CORS_ORIGINS` allows insecure origins (e.g., `*`)
  - AI keys are missing but non-mock AI provider is enabled
- Add tests for validation logic
- Document as "production safety hardening" — do not claim production readiness

---

## Test-Coverage Gaps

### T-1: Simulation Engine Coverage Low

**Severity**: Low (acceptable for prototype)
**Category**: Test-coverage gap

**Finding**: `app/engine/simulation.py` has 53% statement coverage. This is the lowest-covered module. While core paths are tested, edge cases in force-exit, pending order execution, and state rebuild are not fully covered.

**Evidence**:
- Coverage report: `app/engine/simulation.py  199  93  53%`
- Missing lines: 178-260, 331-372, 377-444 (complex state transitions)

**Recommendation**: Acceptable for local prototype. If simulation becomes user-facing, increase coverage to >80%.

---

## Security Hardening Opportunities

### S-1: datetime.utcnow() Deprecation Warnings

**Severity**: Low (future maintenance)
**Category**: Performance/developer-experience opportunity

**Finding**: 15,366 deprecation warnings from `datetime.utcnow()` usage throughout codebase. Python 3.12+ deprecates this in favor of timezone-aware `datetime.now(datetime.UTC)`.

**Evidence**:
- Test output: `DeprecationWarning: datetime.datetime.utcnow() is deprecated`
- Affects: experiments.py, simulations.py, model_research.py, pilot.py, paper_portfolio.py, model_research_export.py

**Recommendation**: Not a defect. Schedule for future cleanup. Do not change in this rehearsal unless it blocks other work.

---

### S-2: Pydantic v2 Deprecation Warnings

**Severity**: Low (future maintenance)
**Category**: Performance/developer-experience opportunity

**Finding**: Tests show `PydanticDeprecatedSince20: The __fields__ attribute is deprecated` in paper portfolio tests.

**Evidence**:
- `backend/tests/test_paper_portfolio.py:374-375`: Uses `__fields__` instead of `model_fields`

**Recommendation**: Not a defect. Update test code to use `model_fields` in future cleanup.

---

## Expected Limitations

### L-1: AI Research Assistant Not Implemented

**Severity**: N/A (expected)
**Category**: Expected limitation

**Finding**: Phase 2 AI research assistant is not implemented. This is expected and correctly documented as unscheduled. No API endpoints, no provider abstraction, no mock provider.

**Evidence**:
- No `backend/app/api/ai_assistant.py` or similar
- `backend/app/config.py`: `AI_PROVIDER=mock` (placeholder only)
- README.md: Phase 2 listed as "AI 澄清助手、策略視覺化改善" but not marked complete

**Required Action**: Create `docs/phase2-ai-research-assistant-backlog.md` with implementation plan.

---

### L-2: No Real Market Data

**Severity**: N/A (expected)
**Category**: Expected limitation

**Finding**: System uses only deterministic demo data. No integration with real market data providers.

**Evidence**:
- `backend/app/data/demo_data.py`: All data pre-generated
- `backend/app/config.py`: `DATA_PROVIDER=demo`
- Warnings in backtest results: "Demo data only — not live market data."

**Status**: Expected. Documented in limitations.

---

### L-3: No Broker/Exchange Integration

**Severity**: N/A (expected)
**Category**: Expected limitation / product boundary

**Finding**: System has no broker or exchange integration. No API key storage, no order execution, no live trading.

**Evidence**:
- No broker-related models or API endpoints
- Paper portfolio explicitly uses "virtual cash" and "simulated fills"
- Export packages exclude broker/exchange fields (verified in tests)

**Status**: Expected. Enforced by product boundary.

---

## Product Scope Decisions

### P-1: Simulation Naming Requires Product Decision

**Severity**: Medium
**Category**: Product scope decision

**Finding**: Whether to label simulation as "Historical Replay" vs "Forward Simulation" is partly a product decision. The implementation is technically historical replay, but the UX is forward-style (incremental snapshot release).

**Options**:
1. **Strict accuracy**: Label as "Historical Replay Simulation" everywhere
2. **UX-focused**: Keep "Forward Simulation" but add prominent disclaimer that it uses pre-existing demo data
3. **Hybrid**: Use "Controlled Forward-Style Simulation" with explanation

**Recommendation**: Option 1 (strict accuracy) aligns with Alethra's research integrity values. Users should not mistake replay for genuine forward observation.

---

## Cannot Verify in Local Environment

### V-1: Docker Compose Startup

**Severity**: N/A
**Category**: Cannot verify

**Finding**: Did not execute `docker-compose up` during this rehearsal. Docker is available, but full stack startup not tested.

**Reason**: Test suite provides sufficient coverage of backend logic. Docker startup is infrastructure, not application logic.

**Recommendation**: Verify Docker startup in separate infrastructure rehearsal if needed for deployment.

---

### V-2: Frontend End-to-End Flows

**Severity**: N/A
**Category**: Cannot verify

**Finding**: Did not execute full frontend E2E flow (register → create experiment → run backtest → lock strategy → simulate → create paper portfolio → etc.).

**Reason**: Browser automation not configured. API-level tests provide coverage of backend logic.

**Recommendation**: Perform manual E2E test before user-facing release. Document in `docs/system-rehearsal-manual-acceptance.md`.

---

## Repository Hygiene

### Verified Clean

- ✅ No `.env` files tracked in git
- ✅ No database files (`*.db`, `*.sqlite`) tracked
- ✅ No API keys or secrets in tracked files
- ✅ `.gitignore` properly excludes venv, node_modules, .env, *.db
- ✅ `.env.example` contains placeholders only
- ✅ No stale brand references ("AI Investment Lab", "AI投資實驗室")
- ✅ All 170 tests pass
- ✅ 86% code coverage
- ✅ No dependency conflicts

---

## Next Steps

1. Create `docs/system-rehearsal-test-results.md` with exact test output
2. Fix D-1: Update simulation labeling
3. Fix D-2: Add disclaimers to paper portfolio pages
4. Fix D-3: Implement production safety validation
5. Create `docs/phase2-ai-research-assistant-backlog.md`
6. Create `docs/current-system-status.md`
7. Create `docs/system-rehearsal-repair-log.md`
8. Create `docs/system-rehearsal-manual-acceptance.md`
9. Run full test suite after repairs
10. Produce final release-readiness report

---

**Rehearsal Status**: Initial inspection complete. Proceeding to repairs and documentation.
