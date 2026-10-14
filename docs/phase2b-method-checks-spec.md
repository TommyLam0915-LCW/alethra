# Phase 2B — Deterministic Research Checks and Result Explanation

**Specification v0.2 — 2026-10-14 (corrected)**

Phase 2A baseline: `3b4c4c12e2c01e8152ce2124a2593084d88dc8df` (PR #3 merged)

---

## 1. Product Scope and Non-Goals

### Scope

A deterministic, explainable review of experiment inputs and existing Historical Replay results. The system evaluates what was configured, what was evaluated, what results show, what is missing, and what conclusions are not supported.

All checks are computed from existing data. No LLM, no external AI, no market data, no model training.

### Non-Goals

- Look-ahead bias, overfitting, or survivorship bias detection (INSUFFICIENT_INFORMATION from summary metrics alone)
- Statistical significance testing with arbitrary thresholds
- Buy/sell recommendations or strategy ranking
- Production-readiness certification
- Automatic experiment blocking or mutation based on warnings
- New database tables for check results (computed on demand)
- Draft-level review (future slice)

---

## 2. Corrections from v0.1 Review

### A. RC-001/RC-002: Experiment-level fields only

**Removed:** Draft-field fallbacks (`ResearchDraft.hypothesis`, `ResearchDraft.falsification_criteria`)

**Rationale:** The experiment has no reverse relationship to drafts. Drafts may be deleted. Multiple drafts could link to the same experiment. The experiment-level review evaluates persisted experiment data only.

**Corrected:** RC-001 checks `Experiment.original_idea` or `Experiment.hypothesis`. RC-002 checks `Experiment.falsification_conditions`. Missing experiment data remains missing even if a draft once had it.

### B. RC-010: Hash comparison semantics

**Corrected:**
- Either hash NULL → INSUFFICIENT_INFORMATION (not WARNING)
- Both present and different → WARNING (run may not reflect current config)
- Both present and equal → PASS (stored-hash consistency only)

**Clarification:** Stored-hash equality does not prove all configuration integrity. It confirms the run was executed with the version's stored configuration hash.

### C. Run selection: Deterministic ordering

**Verified:** `ResearchRun.completed_at` exists (nullable DateTime).

**Policy:** Select completed runs by `completed_at DESC NULLS LAST, id DESC LIMIT 1`.

**Rationale:** `started_at` may be NULL for pending runs. `completed_at` is more reliable for completed runs. NULLS LAST handles edge cases where completed_at is missing despite status=completed.

**Documented:** API response includes `run_selection: "latest_completed_by_completed_at_then_id"`.

### D. Run/version relationship

**Corrected:** Load the version associated with the selected run via `ResearchRun.experiment_version_id`. Do not combine a run with the experiment's current/latest version unless they are the same.

**Exposed:** API response includes `version_id` (the reviewed version, not necessarily the current version).

**No completed run:** Experiment-level checks (RC-001, RC-002, RC-003, RC-004) can still be evaluated. Run-dependent checks (RC-005 through RC-010) return INSUFFICIENT_INFORMATION.

### E. Output contract

**Added fields:**
- `rule_set_version`: "1.0"
- `experiment_id`, `version_id`, `run_id` (nullable if no run)
- `run_selection`: policy string
- `config_hash_match`: true / false / null (from RC-010)
- `generated_at`: ISO timestamp (metadata, excluded from determinism assertions)
- `checks`: ordered list of CheckResult

### F. Clarifications

- `trading_days` comes from run metrics, not calculated from dates
- The 252-day threshold describes existing engine behavior (metrics.py:55,64), not statistical validity
- Benchmark availability uses explicit null checks on `metrics.benchmark_total_return_pct`
- RC-009 uses exact string matching for documented warning literals
- Configured costs (StrategySpec) and evidence of applied costs (metrics.total_commissions) are distinct; RC-004 checks configured costs, RC-005 checks trade execution
- Date parsing uses `datetime.fromisoformat()` for validation, not unrestricted lexicographic comparison

---

## 3. Deterministic Check Rules (Corrected)

### 3.1 Status Model

| Status | Meaning |
|--------|---------|
| PASS | The stated check passed on the available inputs |
| WARNING | A methodological concern is present in the available data |
| INSUFFICIENT_INFORMATION | Required data is not available to evaluate the check |
| NOT_APPLICABLE | The check does not apply to this configuration |

**PASS does NOT mean:** strategy is profitable, suitable for live trading, free from all research biases, or production-ready.

### 3.2 Rule Set (v1.0)

#### RC-001: Research Hypothesis Present
- **Input:** `Experiment.original_idea` or `Experiment.hypothesis` (Text, nullable)
- **Logic:** At least one is non-empty after stripping whitespace
- **PASS:** hypothesis present
- **INSUFFICIENT_INFORMATION:** both null or empty
- **Category:** B (disclosure of missing evidence)

#### RC-002: Falsification Criteria Present
- **Input:** `Experiment.falsification_conditions` (Text, nullable)
- **Logic:** Non-empty after stripping whitespace
- **PASS:** criteria present
- **INSUFFICIENT_INFORMATION:** null or empty
- **Category:** B (disclosure)

#### RC-003: Date Range Validity
- **Input:** `strategy_spec.start_date`, `end_date` (required strings); `metrics.trading_days`
- **Logic:** Parse dates with `datetime.fromisoformat()`; verify end > start; check trading_days >= 252
- **PASS:** valid range, at or above the engine's 252-day threshold; annualized metrics are computed under the current engine rule
- **WARNING:** below the engine's 252-day threshold; annualized metrics are not provided under that rule
- **Note:** The 252-day threshold does not establish statistical validity, data quality, or predictive performance
- **Category:** A (evaluation)

#### RC-004: Transaction Cost Disclosure
- **Input:** `commission_rate`, `slippage_rate` (float, optional)
- **Logic:** Check key presence first, then validity via `_is_valid_cost()` (rejects None, bool, string, negative, NaN, Inf)
- **INSUFFICIENT_INFORMATION:** Either key missing or either value invalid
- **WARNING:** Both values valid and zero
- **PASS:** Both cost keys present; both values valid and non-negative; at least one strictly positive
- **Category:** A (evaluation)

#### RC-005: Trade Count Available
- **Input:** `metrics.total_trades` (always present for completed runs)
- **Logic:** Present and >= 0 → PASS; == 0 → WARNING (no trades)
- **INSUFFICIENT_INFORMATION:** run not completed or key missing
- **Category:** A (evaluation)

#### RC-006: Benchmark Comparison Available
- **Input:** `benchmark_id` (nullable); `metrics.benchmark_total_return_pct` (always key, None if absent)
- **Logic:** benchmark_id NOT NULL AND metric NOT NULL → PASS; benchmark_id NOT NULL AND metric NULL → WARNING; benchmark_id NULL → NOT_APPLICABLE
- **INSUFFICIENT_INFORMATION:** run not completed
- **Category:** A (evaluation)

#### RC-007: Result Completeness
- **Input:** `ResearchRun.status`, `metrics` keys
- **Logic:** status == completed AND 13 expected keys present → PASS; completed but keys missing → WARNING; pending/running/failed → INSUFFICIENT_INFORMATION
- **Category:** A (evaluation)

#### RC-008: Engine Version Recorded
- **Input:** `ResearchRun.engine_version` (String(50), NOT NULL)
- **Logic:** Non-empty → PASS
- **INSUFFICIENT_INFORMATION:** null or empty (should not occur given NOT NULL constraint)
- **Category:** A (evaluation)

#### RC-009: Zero-Cost Warning Consistency
- **Input:** `commission_rate`, `slippage_rate`, `ResearchRun.warnings` (JSONB list)
- **Logic:** Per-component check: commission==0 requires exact warning "Zero commission selected — real trading incurs costs."; slippage==0 requires exact warning "Zero slippage selected — real execution incurs slippage." Each component is checked independently.
- **INSUFFICIENT_INFORMATION:** Cost key missing or value invalid
- **NOT_APPLICABLE:** Run not completed
- **WARNING:** Any zero-cost component lacks its corresponding warning (engine inconsistency)
- **PASS:** All required per-component zero-cost warnings are present, or neither component requires a zero-cost warning
- **Category:** A (evaluation)

#### RC-010: Config Hash Integrity
- **Input:** `ExperimentVersion.config_hash` (nullable), `ResearchRun.config_hash` (NOT NULL)
- **Logic:** Either NULL → INSUFFICIENT_INFORMATION; both present and equal → PASS; both present and different → WARNING
- **Category:** A (evaluation)

---

## 4. Architecture

### 4.1 Module Structure

```
backend/app/method_checks/
  __init__.py
  rules.py          # Rule definitions (RC-001 through RC-010)
  evaluator.py      # evaluate_checks() — pure logic
  loader.py         # load_review_data() — database loading
  explanations.py   # generate_explanation() — deterministic templates
  schemas.py        # Pydantic models
```

**Separation:** `loader.py` handles database queries and ownership. `evaluator.py` is pure functions over loaded data. This allows independent unit testing of rules.

### 4.2 API Endpoint

```
GET /api/v1/experiments/{experiment_id}/method-review
```

**Authentication:** `get_current_user` dependency (same as other experiment endpoints)

**Ownership:** Verify `experiment.owner_id == current_user.id`; return 403 if not

**Nonexistent:** Return 404 (same as other experiment endpoints)

**Response:**
```json
{
  "rule_set_version": "1.0",
  "experiment_id": "uuid",
  "version_id": "uuid" | null,
  "run_id": "uuid" | null,
  "run_selection": "latest_completed_by_completed_at_then_id",
  "config_hash_match": true | false | null,
  "generated_at": "2026-10-14T12:34:56Z",
  "checks": [
    {
      "rule_id": "RC-001",
      "rule_version": "1.0",
      "status": "PASS",
      "evidence": {...},
      "explanation": "A. Observation: ...\nB. Interpretation: ...\nC. Limitation: ...",
      "limitations": ["Does not evaluate hypothesis quality"]
    },
    ...
  ]
}
```

**No side effects:** Read-only. No runs created, no state altered, no records written.

### 4.3 Run Selection

Run-first selection across ALL versions of the experiment:

```python
version_ids = [v.id for v in db.query(ExperimentVersion.id).filter(
    ExperimentVersion.experiment_id == experiment_id,
).all()]

run = db.query(ResearchRun).filter(
    ResearchRun.experiment_version_id.in_(version_ids),
    ResearchRun.status == RunStatus.completed,
).order_by(
    ResearchRun.completed_at.desc().nullslast(),
    ResearchRun.id.desc()
).first()
```

The version is then loaded from the selected run's `experiment_version_id`. If no completed run exists, `run = None` and `version = None`; run-dependent checks return INSUFFICIENT_INFORMATION or NOT_APPLICABLE.

---

## 5. Acceptance Specification

### 5.1 Determinism

Same input and rule version produce the same result. `generated_at` is metadata and excluded from determinism assertions.

### 5.2 Data Handling

- Null/empty/missing → INSUFFICIENT_INFORMATION (not silent defaults)
- Numeric 0 and string "0" handled deliberately (experiments use floats)
- Invalid/non-finite numbers → INSUFFICIENT_INFORMATION with explanation
- Missing metric keys → INSUFFICIENT_INFORMATION

### 5.3 Zero-Cost Disclosure

Zero commission/slippage → WARNING (RC-004). Zero is disclosed, not hidden.

### 5.4 Ownership

All endpoints enforce `owner_id == current_user.id`. Cross-user → 403.

### 5.5 No Side Effects

Review is read-only. No experiment/run/version mutations. No re-execution.

---

## 6. Implementation Plan

### Files

```
backend/app/method_checks/__init__.py
backend/app/method_checks/rules.py
backend/app/method_checks/evaluator.py
backend/app/method_checks/loader.py
backend/app/method_checks/explanations.py
backend/app/method_checks/schemas.py
backend/app/api/experiments.py  (add method-review endpoint)
backend/tests/test_method_checks.py
frontend/src/app/experiments/[id]/page.tsx  (add Method Review section)
```

### Tests

- Unit: all rules with fixed fixtures, all status paths, edge cases
- API: auth, ownership, nonexistent, no runs, multiple runs, determinism
- Browser: display on experiment detail page (optional, if acceptance stack available)

---

*This specification is a design document. Implementation follows.*
