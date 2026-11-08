# Trading Skill Optimization — Capability Map

**Date:** 2026-11-08T10:53:01Z  
**Baseline HEAD:** 468ca40  
**Branch:** feat/skill-optimization-slice  
**Python:** 3.11.3

---

## Executive Summary

Repository audit reveals a mature backtest and research infrastructure with walk-forward evaluation, but **no explicit parameter optimization service**. The first vertical slice will build a bounded optimization workflow on top of existing capabilities.

---

## Capability Inventory

### ✅ Implemented (Reuse Directly)

| Capability | Location | Evidence |
|------------|----------|----------|
| **SMA Crossover Strategy** | `app/engine/backtest.py:run_sma_crossover` | Imported and executed by `run_backtest` |
| **Parameter Validation** | `app/engine/backtest.py:SIGNAL_PARAMETER_RULES` | Bounds: short_window 2-60, long_window 3-200 |
| **Walk-Forward Evaluation** | `app/services/strategy_research.py` | Line 20-21: "rule/exit selection is walk-forward" |
| **Cost-Aware Metrics** | `app/engine/fill_math.py` | Commission, slippage, net cash change |
| **Backtest Engine** | `app/engine/backtest.py:run_backtest` | ENGINE_VERSION "1.1.0" |
| **Strategy Study Model** | `app/models/strategy_study.py` | StrategyStudyRun with walk_forward_json |
| **Paper Trading** | `app/models/paper_trading_run.py` | Existing simulation infrastructure |
| **Demo Data** | `app/data/demo_data.py` | DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK |
| **Exit Rules** | `app/engine/exit_rules.py` | Stop, target, time-based exits |
| **Signal Parameter Requirements** | `app/engine/backtest.py:SIGNAL_PARAMETER_REQUIREMENTS` | Explicit error messages |

### ⚠️ Partially Implemented (Extend, Don't Replace)

| Capability | Location | Gap |
|------------|----------|-----|
| **Strategy Research Service** | `app/services/strategy_research.py` | Walk-forward exists, but no parameter sweep |
| **Strategy Lab** | `app/services/strategy_lab.py` | StrategySpec exists, but no optimization loop |
| **Agent Strategy** | `app/services/agent_strategy.py` | Training exists, but not bounded optimization |
| **Skill Attribution** | `app/services/skill_attribution.py` | Post-hoc analysis, not prospective optimization |

### ❌ Absent (Build for First Slice)

| Capability | Rationale |
|------------|-----------|
| **Bounded Parameter Optimizer** | No grid search, no candidate generation with budget |
| **Chronological Train/Val/Test Split** | Walk-forward exists for rules, not for parameter selection |
| **Candidate Comparison Service** | No side-by-side comparison with baselines |
| **Versioned Configuration Model** | No explicit config versioning for skill accounts |
| **Explicit User Selection Workflow** | No API for user to review and adopt a candidate |

### ❓ Unverified (Need Test Coverage)

| Capability | Location | Question |
|------------|----------|----------|
| **Future-Data Leakage Prevention** | `strategy_research.py` | Does walk-forward truly freeze parameters before test fold? |
| **Cost Parity** | `fill_math.py` vs `strategy_research.py` | Same fee/slippage conventions across all paths? |
| **Deterministic Results** | `run_backtest` | Same inputs → same outputs? |
| **Cancellation/Bounded Execution** | N/A | No optimization loop to cancel yet |

---

## Strategy Family Selection

**First slice: SMA Crossover**

Rationale:
1. Already implemented in `run_sma_crossover`
2. Parameter bounds defined (short_window 2-60, long_window 3-200)
3. Simple enough for bounded optimization
4. Clear baseline comparison (buy-and-hold, default parameters)
5. No external data dependencies (demo data sufficient for engineering tests)

Candidate parameter space (bounded):
- short_window: [5, 10, 15, 20, 30]
- long_window: [30, 50, 75, 100, 150, 200]
- Total combinations: 5 × 6 = 30 candidates (small enough for local execution)

---

## Data Provenance

**Available without external calls:**
- Demo data: DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK
- Synthetic data: `app/data/real_synthetic_data.py`
- Real instruments: `app/data/real_instruments.py`, `app/data/crypto_instruments.py`

**For first slice:**
- Use demo data for engineering tests
- Record dataset checksum and snapshot hash
- Do NOT fetch paid or unapproved external data
- If real data absent: complete engineering tests with fixtures, label clearly

---

## Architecture Decision

**Extend, don't replace:**
1. Build optimizer as a new service: `app/services/skill_optimization.py`
2. Reuse `run_backtest` for candidate evaluation
3. Reuse walk-forward split logic from `strategy_research.py`
4. Add new model: `SkillOptimizationJob` to track candidates and results
5. Add new API endpoints for optimization workflow
6. Connect to existing skill account model (if present) or document gap

**Separation of concerns:**
- Signal generation (existing)
- Parameter optimization (new)
- Position sizing (existing in `fill_math.py`)
- Execution simulation (existing in `run_backtest`)
- Risk limits (existing in `exit_rules.py`)

---

## Stop Conditions

This slice will NOT:
- Auto-adopt the best candidate
- Promise profitability
- Execute live trades
- Modify production database
- Fetch external data
- Activate production scheduler
- Merge to main or deploy

This slice WILL:
- Deliver one reproducible optimization workflow
- Provide explicit user selection
- Record all attempted configurations
- Compare candidates honestly with baselines
- Add focused tests for safety
- Document limitations and unresolved dependencies

---

## Next Steps

1. ✅ Capability map (this document)
2. ⏳ Inspect existing walk-forward implementation for reuse
3. ⏳ Design bounded optimizer service
4. ⏳ Implement candidate generation with budget
5. ⏳ Add chronological split integrity tests
6. ⏳ Build comparison service with baselines
7. ⏳ Connect to skill account API (or document gap)
8. ⏳ Add focused safety tests
9. ⏳ Deliver one complete slice with evidence

---

## Known Limitations

1. **No explicit optimization exists** — building from scratch
2. **Skill account model unclear** — may need to document dependency
3. **Real data not verified** — will use demo data for engineering tests
4. **Walk-forward for parameters untested** — need to verify no future-data leakage
5. **No versioned configuration model** — need to design or document gap

---

**Status:** Capability map complete. Proceeding to implementation.
