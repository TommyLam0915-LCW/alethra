# Phase 1 Reconciliation Report

**Date**: 2026-10-11
**Engine Version**: 1.0.0 → 1.1.0
**Status**: Feature Complete — Local Prototype, Demo Data Only

---

## 1. Inspection Summary

Inspected all files listed in Section 1 of the reconciliation specification:

| File | Status |
|------|--------|
| README.md | Updated — status label, test count, Phase 2 scope |
| docs/phase1-implementation-plan.md | Rewritten — reflects actual final state |
| docs/phase1-deliverables.md | Stale — references AAPL/MSFT, old tests (not modified; superseded by implementation-plan.md) |
| docs/research-methodology.md | Rewritten — Model A benchmark, signal semantics, annualization policy |
| docs/phase1-known-limitations.md | Updated — pricing, Phase 2 scope, engine version |
| docs/phase1-manual-acceptance.md | Created — 42-step acceptance script |
| backend/app/lib/brand.py | Correct — no changes needed |
| frontend/src/lib/brand.ts | Correct — no changes needed |
| backend/app/api/schemas.py | Correct — no changes needed |
| backend/app/engine/backtest.py | Updated — ENGINE_VERSION 1.1.0, benchmark passes costs |
| backend/app/engine/strategies.py | Updated — buy-and-hold signal_date=null |
| backend/app/engine/metrics.py | Updated — Model A benchmark, annualization guards |
| backend/app/data/demo_data.py | Correct — no changes needed |
| backend/app/api/experiments.py | Correct — no changes needed |
| backend/app/api/runs.py | Correct — no changes needed |
| backend/app/models/research_run.py | Correct — no changes needed |
| backend/tests/test_engine.py | Updated — 13 new tests added (16 → 29) |
| backend/tests/test_api.py | Correct — no changes needed (9 tests) |

---

## 2. Issue Classification and Resolution

### Critical Issue A — Benchmark Alignment: **FIXED**

**Was**: Model B (frictionless close-price entry, fractional shares, no costs)
**Now**: Model A (execution-consistent: open-price entry, whole shares, same commission/slippage, same liquidation)

**Code changes**:
- `metrics.py:113-151`: `build_benchmark_equity_curve()` rewritten to accept commission_rate and slippage_rate, use open-price entry, whole-share sizing, and final liquidation with costs
- `backtest.py:172`: Benchmark call now passes commission_rate and slippage_rate
- `metrics.py:35-37`: `benchmark_total_return_pct` now uses `initial_cash` as base

**Tests added**: #17-20 (benchmark initial cash, whole shares, zero-cost entry, excess return base)

---

### Critical Issue B — Signal/Observation/Execution Semantics: **FIXED**

**Was**: Buy-and-hold initial BUY had `signal_date = execution_date = first_day`
**Now**: Buy-and-hold initial BUY has `signal_date = null`, rationale = "Initial allocation at first tradable session"

**Code changes**:
- `strategies.py:7`: `signal_date` parameter type changed to `Optional[str]`
- `strategies.py:79`: Buy-and-hold initial BUY sets `signal_date=None`
- `strategies.py:90`: Rationale updated to "Initial allocation at first tradable session"
- `strategies.py:114`: SELL rationale updated to "Forced end-of-period liquidation at final valid close"

**Tests added**: #21-24 (null signal_date, forced liquidation rationale, SMA signal < execution, price-change signal < execution)

---

### Critical Issue C — Annualization and Metric Presentation: **FIXED**

**Was**: annualized_return_pct reason said "Period too short"; annualized_volatility_pct computed with as few as 2 data points
**Now**: Both return `{value: null, reason: "..."}` when `trading_days < 252`

**Code changes**:
- `metrics.py:59-62`: annualized_return_pct reason references 252 trading days
- `metrics.py:64`: annualized_volatility_pct guard changed from `len(daily_equity) >= 2` to `trading_days >= 252`
- `metrics.py:76-78`: annualized_volatility_pct reason references 252 trading days

**Tests added**: #25-27 (annualized return unavailable, volatility unavailable, not represented as zero)

---

### Critical Issue D — Pricing and Commercial Claims: **FIXED**

**Was**: Specific prices "HK$280/月 個人、HK$680/月 教育者" in known-limitations
**Now**: Labelled as "內部試點定價假設 — 非公開定價，未經市場驗證" with statement that Phase 1 does not establish commercial sustainability

**Code changes**: `docs/phase1-known-limitations.md:108-117` rewritten

---

### Critical Issue E — Phase 2 Scope Discipline: **FIXED**

**Was**: "多資產組合 | Phase 2-3" in known-limitations
**Now**: Multi-asset moved to "Future Research Engine Expansion — Unscheduled" with note about separate research effort

**Code changes**:
- `docs/phase1-known-limitations.md:127-140`: Table updated, new "Unscheduled" section added
- `README.md:36-42`: Phase 2 scope clarified, unscheduled items noted

---

### Stale Documentation: **FIXED**

- `docs/phase1-implementation-plan.md`: Completely rewritten to reflect actual state
- `docs/research-methodology.md`: Completely rewritten with Model A, signal semantics, annualization policy
- `README.md`: Status label, test count, Phase 2 scope updated

---

## 3. Code Changes Summary

| File | Change |
|------|--------|
| `backend/app/engine/metrics.py` | Benchmark Model A, benchmark return base, annualization guards |
| `backend/app/engine/backtest.py` | ENGINE_VERSION 1.1.0, benchmark passes costs |
| `backend/app/engine/strategies.py` | signal_date Optional, buy-and-hold null signal_date, rationale text |
| `backend/tests/test_engine.py` | 13 new tests (#17-29) |
| `docs/phase1-known-limitations.md` | Pricing, Phase 2 scope, engine version |
| `docs/research-methodology.md` | Full rewrite |
| `docs/phase1-implementation-plan.md` | Full rewrite |
| `docs/phase1-manual-acceptance.md` | Created |
| `README.md` | Status, test count, Phase 2 scope |

---

## 4. Test Results

**Command**: `cd backend && venv/bin/python -m pytest tests/ -v`
**Result**: **38 passed** (29 engine + 9 API)
**Date**: 2026-10-11

New tests added:
- #17: benchmark_uses_same_initial_cash
- #18: benchmark_whole_shares_with_costs
- #19: benchmark_zero_costs_entry
- #20: excess_return_comparable_base
- #21: buy_and_hold_initial_buy_null_signal_date
- #22: buy_and_hold_sell_forced_liquidation
- #23: sma_signal_date_precedes_execution
- #24: price_change_signal_date_precedes_execution
- #25: annualized_return_unavailable_short_sample
- #26: annualized_volatility_unavailable_short_sample
- #27: unavailable_metrics_not_zero
- #28: engine_version
- #29: trade_date_equals_execution_date

---

## 5. Security Verification

| Check | Result |
|-------|--------|
| Hardcoded secrets | PASS (advisory: add .gitignore) |
| Stack trace exposure | PASS |
| Frontend backend secrets | PASS |
| Arbitrary code execution | PASS |
| Authorization enforcement | PASS |

---

## 6. Unresolved Items

- Manual acceptance test script created but not yet performed by a human tester
- Legal/compliance review: Out of scope for Phase 1
- `.gitignore` advisory from security scan: not critical for local prototype

---

## 7. Final Phase 1 Status

**Phase 1 Status: Feature Complete — Local Prototype, Demo Data Only**

All required reconciliation work completed:
- ✅ Benchmark alignment (Model A)
- ✅ Signal-execution semantics
- ✅ Annualization guards
- ✅ Pricing claims corrected
- ✅ Phase 2 scope discipline
- ✅ Documentation updated
- ✅ Tests added (25 → 38)
- ✅ Security verification passed
- ✅ Manual acceptance script created

---

## 8. Statement

Test success does not establish commercial sustainability, investment performance, or legal compliance. A positive backtest is not proof. A passing test suite is not product-market fit. A clear limitation is more valuable than a misleading claim.

---

**Report version**: 2.0
**Date**: 2026-10-11
