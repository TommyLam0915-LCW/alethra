# Decision Lineage + Migration Fixes — Delivery Package

> **已被取代（2026-11-17）**：本檔不再是狀態來源。請讀 `alethra/EXECUTION_TABLE_20261117.md` 的「平行摘要取代與更正」節。
> 本檔 §5.1 把成功推論歸因於「authorization infrastructure complexity」是**錯誤歸因**（personal model 曾被錯接 agent-strategy 授權鏈）；
> 現況為 `tests/test_successful_inference.py` 9 passed（隔離 DB）。本檔 §1.3／§2.4 的行號與「8 passed」計數已過期。
> 保留本檔未刪除，因其為未追蹤檔，刪除不可逆。

**Date**: 2026-11-17  
**Target revision**: c3d4e5f6g7h8  
**Migration chain**: f2a3b4c5d6e7 → b2c3d4e5f6g7 → c3d4e5f6g7h8

---

## 1. Migration Fixes Applied

### 1.1 b2c3d4e5f6g7_add_skill_unit_associations.py

**PostgreSQL Compatibility Fix** (line 57-60):
- **Before**: Used `PRAGMA table_info()` (SQLite-only)
- **After**: Uses `sqlalchemy.inspect(conn).get_columns()` (cross-database)

**Nullable FK Reasoning Correction** (line 22-28):
- **Before**: Vague "existing rows cannot be backfilled reliably"
- **After**: Explicit 3-point rationale:
  1. Columns added to existing tables with pre-feature rows
  2. Backfill would be guessing, violating provenance invariant
  3. Entry-point validation enforces consistency for new writes

**Full text**: See `backend/migrations/versions/b2c3d4e5f6g7_add_skill_unit_associations.py`

### 1.2 c3d4e5f6g7h8_add_decision_inference_link.py

**Precondition Checks Added** (line 32-62):
- Verifies `decision_records` table exists
- Verifies `linked_inference_id` column does NOT exist (additive-only)
- Verifies index `ix_decision_records_linked_inference_id` does NOT exist
- Downgrade also checks preconditions in reverse

**Nullable FK Reasoning Expanded** (line 11-20):
- Explicit 4-point rationale for NULLABLE:
  1. Column added to existing table with pre-feature rows
  2. Existing decisions cannot be retroactively linked
  3. Hiding rows (NOT NULL) would violate provenance invariant
  4. Column indexed for efficient lookup

**Additive-only policy statement** added (line 22-24)

**Full text**: See `backend/migrations/versions/c3d4e5f6g7h8_add_decision_inference_link.py`

### 1.3 paper_portfolio.py Skill Metadata Validation

**Location**: `backend/app/api/paper_portfolio.py:1012-1020`

**Before** (too permissive):
```python
inference_skill_unit_id = metadata.get("skill_unit_id")
if inference_skill_unit_id is not None and inference_skill_unit_id != str(portfolio.id):
    raise HTTPException(...)
```
This allowed NULL/missing skill_unit_id to pass.

**After** (refuse NULL/missing):
```python
inference_skill_unit_id = metadata.get("skill_unit_id")
if not inference_skill_unit_id or inference_skill_unit_id != str(portfolio.id):
    raise HTTPException(...)
```
Now refuses if skill_unit_id is missing, NULL, or mismatched.

---

## 2. Decision Lineage Implementation

### 2.1 Schema Changes

**paper_portfolio.py:221** — Added to PaperTradingRun:
```python
linked_inference_id = Column(UUID(as_uuid=True), nullable=True, index=True)
```

**schemas.py:699** — Added to DecisionRecordCreate:
```python
linked_inference_id: Optional[UUID] = None
```

**schemas.py:720** — Added to DecisionRecordResponse:
```python
linked_inference_id: Optional[UUID]
```

### 2.2 Validation Logic

**paper_portfolio.py:986-1020** — POST decisions validation:
1. If `linked_inference_id` provided, verify it exists in `usage_records`
2. Verify it belongs to the same owner (cross-owner refused with 404)
3. Verify `metadata.prediction_type == "personal_decision"` (type check, 400 if not)
4. Verify `metadata.skill_unit_id` matches the portfolio (cross-skill refused with 400)
5. **NEW**: Refuse if `skill_unit_id` is missing/NULL (not just mismatched)

### 2.3 Inference Metadata Capture

**personal_models.py:602-629** — POST predict captures:
```python
usage_record = record_api_call_no_commit(
    db, user_id=current_user.id, endpoint="...", model=str(version.id),
    tokens_used=0, cost=0,
    metadata={
        "prediction_type": "personal_decision",
        "skill_unit_id": str(body.skill_unit_id) if body.skill_unit_id else None,
        "model_version_id": str(version.id),
        "artifact_id": artifact_id,
        "instrument_id": body.instrument_id,
        "as_of_date": body.as_of_date,
    },
)
return { ..., "inference_id": str(usage_record.id) }
```

### 2.4 Tests

**test_decision_lineage.py** — 8 comprehensive tests, all PASS on SQLite:

1. **T1**: `test_decision_can_link_to_specific_inference` — Create inference, link decision, verify UUID stable
2. **T2**: `test_two_inferences_decision_points_to_one` — Two inferences, decision links to one, not both
3. **T3**: `test_later_model_switch_does_not_change_original_decision` — Switch model, old decision keeps old link
4. **T4**: `test_cross_owner_inference_refused` — Inference from owner A cannot link to decision from owner B (404)
5. **T5**: `test_cross_skill_inference_refused` — Inference for skill A cannot link to decision for skill B (400)
6. **T6**: `test_non_personal_decision_inference_refused` — Non-model-execution inference refused (400)
7. **T7**: `test_nonexistent_inference_refused` — Non-existent inference ID refused (404)
8. **T8**: `test_decision_without_inference_link_still_works` — Backward compatibility: decisions without link still work

**Test file**: `backend/tests/test_decision_lineage.py`

---

## 3. Usage Record Lifecycle Check

**What was checked**:
- Searched for DELETE operations on UsageRecord model
- Searched for cascade deletes from related tables
- Checked billing.py for any cleanup/retention logic

**Finding**: No application-level deletion paths found in limited check.

**Limitation**: This is NOT a guarantee of immutability. Database-level retention policies, manual DBA operations, or future code changes could delete records. The correct statement is: "No application deletion paths found in limited check" — not "append-only protection guaranteed."

**Location**: `backend/app/models/billing.py:82-99` (UsageRecord model)

---

## 4. Environment Specifications

**Runtime**:
- **OS**: macOS 13.3 arm64 (no brew/Docker/sudo)
- **Python**: 3.12 (via uv)
- **Node**: 18.20.8 (user-local)
- **PostgreSQL**: port 5432, socket /tmp, PGDATA not specified
- **SQLite**: default test database

**Test execution**:
```bash
cd alethra/backend
~/.local/bin/uv run python -m pytest tests/test_decision_lineage.py -v
```

**Migration execution** (NOT YET TESTED on PostgreSQL):
```bash
cd alethra/backend
alembic upgrade head
```

**Starting revision**: UNKNOWN (not measured — would require `alembic current` on live DB)  
**Target revision**: c3d4e5f6g7h8

---

## 5. What's NOT Complete

### 5.1 Successful Inference Test

**Blocked by**: Authorization infrastructure complexity

The `check_model_version_inference_authorization()` function requires:
1. `training_job_id` in `model_specification_json`
2. An `AgentStrategyTrainingJob` row with `status="completed"`
3. An `AgentStrategyConsent` row with `status="active"`
4. An `ExperimentVersion` row (FK target for `strategy_version_id`)
5. An `AgentModelActivationApproval` row with:
   - `job_id` pointing to the training job
   - `decision="approved"`
   - `skill_unit_id` matching the request
   - `artifact_checksum` matching the model spec
   - `channel="owner_ui"`
   - Many other required fields

**What was attempted**: Created test fixture with MarketDataBar + personal model artifact + ModelVersion + PersonalModelSelection, but the authorization check failed with "no_training_job" because the full training workflow infrastructure was not set up.

**What's needed**: Either:
- A full training workflow test setup (training job + consent + experiment version + approval)
- OR a simplified authorization bypass for testing (not recommended — weakens the gate)
- OR acceptance that successful inference requires the full product flow (train → approve → infer)

**Test file started**: `backend/tests/test_successful_inference.py` (incomplete)

### 5.2 Paper Create/Engine Consistency

**Not started**. Requires:
- Verify request version/dataset → validation → engine uses → run saves → query reads
- Prove specified content affects actual execution
- Test with distinguishable data/version differences

### 5.3 UI/Client Integration

**Not started**. Requires testing:
- Skill selection and status
- Strategy create/revise/lock
- Data and cost selection
- Execution and result query
- Model selection
- Artifact approval and revocation
- Inference and decision source review
- Export, pause, stop

### 5.4 PostgreSQL Migration Execution

**Not tested**. Migrations have been fixed for PostgreSQL compatibility (Inspector instead of PRAGMA), but have not been executed on a live PostgreSQL database.

**Approval boundary**: Code/schema/test writing is allowed. PostgreSQL execution, migration on live DB, service startup, and external calls are NOT allowed without approval.

---

## 6. Key Diffs with Line Numbers

### Migration b2c3d4e5f6g7

**Line 57-60** — PostgreSQL-compatible column check:
```python
def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table (SQLite + PostgreSQL compatible)."""
    inspector = inspect(conn)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns
```

**Line 22-28** — Explicit nullable reasoning:
```python
All columns are NULLABLE because:
1. These columns are added to existing tables with rows that predate the feature.
2. Existing rows cannot be backfilled reliably — attributing an old selection to
   a skill unit would be guessing, and guessing ownership violates the provenance
   invariant.
3. The entry-point validation (see ``app/api/paper_portfolio.py`` POST decisions
   and ``app/api/paper_trading.py`` POST create) enforces consistency for new
   writes without requiring old rows to change.
```

### Migration c3d4e5f6g7h8

**Line 32-62** — Precondition checks:
```python
def upgrade() -> None:
    conn = op.get_bind()

    # Phase 1: Verify preconditions
    if not _table_exists(conn, "decision_records"):
        raise RuntimeError(
            "Precondition failed: decision_records table does not exist. "
            "This migration requires the decision_records table to be present."
        )

    if _column_exists(conn, "decision_records", "linked_inference_id"):
        raise RuntimeError(
            "Precondition failed: decision_records.linked_inference_id already exists. "
            "This migration is additive-only and will not overwrite existing state."
        )

    if _index_exists(conn, "decision_records", "ix_decision_records_linked_inference_id"):
        raise RuntimeError(
            "Precondition failed: index ix_decision_records_linked_inference_id already exists. "
            "This migration is additive-only and will not overwrite existing state."
        )

    # Phase 2: Add column and index
    op.add_column(
        'decision_records',
        sa.Column('linked_inference_id', sa.UUID(), nullable=True)
    )
    op.create_index(
        'ix_decision_records_linked_inference_id',
        'decision_records',
        ['linked_inference_id']
    )
```

### paper_portfolio.py

**Line 1012-1020** — Refuse NULL/missing skill_unit_id:
```python
# Verify skill_unit_id matches — inference for skill A cannot be linked to decision for skill B
# MUST be present and match — NULL/missing skill_unit_id is refused, not allowed
inference_skill_unit_id = metadata.get("skill_unit_id")
if not inference_skill_unit_id or inference_skill_unit_id != str(portfolio.id):
    raise HTTPException(
        status_code=400,
        detail={
            "reason": "inference_skill_mismatch",
            "message": f"Linked inference was generated for skill unit {inference_skill_unit_id}, not {portfolio.id}.",
        },
    )
```

---

## 7. Verification Commands

**Run decision lineage tests**:
```bash
cd alethra/backend
~/.local/bin/uv run python -m pytest tests/test_decision_lineage.py -v
```

**Expected output**: 8 passed

**Check migration syntax** (without executing):
```bash
cd alethra/backend
~/.local/bin/uv run python -c "from migrations.versions import b2c3d4e5f6g7_add_skill_unit_associations, c3d4e5f6g7h8_add_decision_inference_link; print('Migration modules load successfully')"
```

**Verify migration chain**:
```bash
cd alethra/backend
alembic history
```

**Expected output** (tail):
```
f2a3b4c5d6e7 -> b2c3d4e5f6g7, add skill-unit association columns for unified tracing
b2c3d4e5f6g7 -> c3d4e5f6g7h8, add linked_inference_id to decision_records for model-execution traceability
```

---

## 8. Stop Conditions

**Do NOT proceed without approval**:
- Execute migrations on PostgreSQL
- Start backend services
- Make external API calls
- Modify production data

**Safe to proceed**:
- Code changes (already done)
- Schema changes (already done)
- Test writing (already done)
- Local SQLite testing (already done, 8/8 pass)

---

## 9. Summary

**Completed**:
- ✅ Migration PostgreSQL compatibility fixes
- ✅ Migration precondition check additions
- ✅ Nullable FK reasoning corrections
- ✅ Skill metadata validation fix (refuse NULL/missing)
- ✅ Decision lineage implementation (schema + validation + metadata capture)
- ✅ 8 comprehensive decision lineage tests (all PASS on SQLite)
- ✅ Usage record lifecycle check (no deletion paths found in limited check)

**Not completed**:
- ❌ Successful inference test (blocked by authorization infrastructure complexity)
- ❌ Paper create/engine consistency verification
- ❌ UI/client integration testing
- ❌ PostgreSQL migration execution (not tested on live DB)

**Approval boundary respected**:
- ✅ Code/schema/test writing done
- ❌ PostgreSQL execution not done (requires approval)
- ❌ Service startup not done (requires approval)
- ❌ External calls not done (requires approval)

---

**End of delivery package**
