# Manual Acceptance Findings

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012

---

## Finding 1 — UUID serialization in model version creation

**Scenario**: A09 (Model Research Record and Model Card)
**Severity**: Backend defect
**Category**: Calculation / serialization defect
**Status**: Fixed

### Description

Creating a model version with `linked_experiment_version_ids`, `linked_dataset_snapshot_ids`, or `linked_simulation_session_ids` fails with:

```
TypeError: Object of type UUID is not JSON serializable
```

### Root Cause

In `backend/app/api/model_research.py`, the `create_model_version` endpoint assigned SQLAlchemy UUID objects directly to JSON-typed columns. SQLAlchemy's JSON column serializer cannot serialize raw UUID objects.

### Fix

Convert UUID lists to string lists before assignment:

```python
linked_experiment_version_ids=[str(v) for v in payload.linked_experiment_version_ids] if payload.linked_experiment_version_ids else None,
linked_dataset_snapshot_ids=[str(v) for v in payload.linked_dataset_snapshot_ids] if payload.linked_dataset_snapshot_ids else None,
linked_simulation_session_ids=[str(v) for v in payload.linked_simulation_session_ids] if payload.linked_simulation_session_ids else None,
```

### Files Changed

- `backend/app/api/model_research.py`

### Verification

- A09 scenario passes after fix
- Regression test: `test_model_research_serializes_uuid_references_to_json_strings` in `backend/tests/test_model_research.py`
- Run: `pytest tests/test_model_research.py::TestUUIDJSONBoundaryRegression -v`

---

## Finding 2 — UUID type mismatch in export package builder

**Scenario**: A10 (Model Research Export Package)
**Severity**: Backend defect
**Category**: Calculation / serialization defect
**Status**: Fixed

### Description

Generating an export package for a model version with linked experiment versions or simulation sessions fails with:

```
AttributeError: 'str' object has no attribute 'hex'
```

The export is created with status `failed` and the error is recorded in `error_message`.

### Root Cause

In `backend/app/api/model_research_export.py`, the `_build_package` function reads linked IDs from JSON columns (stored as strings after Finding 1's fix) and passes them directly to SQLAlchemy `.in_()` filters. The `.in_()` filter for UUID columns expects UUID objects, not strings. When SQLAlchemy attempts to convert the string to a bind parameter, it calls `.hex` on the string, which fails.

Affected queries:
- `ExperimentVersion.id.in_(linked_experiment_ids_str)` — line 100
- `SimulationSession.id.in_(linked_simulation_ids)` — line 121

### Fix

Convert string IDs to UUID objects before querying:

```python
linked_experiment_uuids = [uuid.UUID(str(eid)) for eid in linked_experiment_ids]
linked_simulation_uuids = [uuid.UUID(str(sid)) for sid in linked_simulation_ids]
```

### Files Changed

- `backend/app/api/model_research_export.py`

### Verification

- A10 scenario passes after fix
- Regression tests in `backend/tests/test_model_research_export.py`:
  - `test_model_research_export_converts_json_string_ids_to_uuid_objects`
  - `test_model_research_export_rejects_invalid_uuid_strings_safely`
  - `test_model_research_export_handles_duplicate_linked_ids_deterministically`
- Run: `pytest tests/test_model_research_export.py::TestUUIDJSONBoundaryRegression -v`

---

## Shared Root Cause

Both findings stem from the same boundary: the transition between SQLAlchemy UUID columns and JSON-typed columns that store UUID references as strings. The codebase did not consistently convert between these representations at the serialization boundary.

---

## Warning Delta Review

- Warning count before: 16,804
- Warning count after: 17,378
- Delta: +574
- Root cause: 4 new regression tests exercise additional API/DB code paths (+84 warnings); UUID fix code changes cause existing tests to trigger more instances of the same known third-party deprecation warnings (+490 warnings). All warnings are additional instances of pre-existing, documented categories (pytest-asyncio, fastapi, starlette, sqlalchemy, jose).
- New warning categories introduced: no
- New Alethra-owned warning categories introduced: no
- Commit-safe conclusion: SAFE — no new warning class, no unhandled exceptions, no UUID serialization errors. All 7 acceptance rules pass. See `docs/warning-baseline.md` for full classification table.
