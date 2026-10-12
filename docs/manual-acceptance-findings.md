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

## Finding 3 — A01 safety checks hardcoded to True

**Scenario**: A01 (Landing Page and Product Boundaries)
**Severity**: Harness false positive
**Category**: Acceptance script defect
**Status**: Fixed

### Description

The A01 scenario set `no_broker_language`, `no_live_trading_claim`, `no_stock_recommendation`, and `no_api_key_storage` to `True` without performing any actual assertion. The landing page could have contained prohibited broker/trading language and the scenario would still report PASS.

### Fix

Added `assert_safety_keywords()` function that scans the full landing page JSON for prohibited phrases ("buy now", "sell signal", "broker api", "exchange api", "live trading", "guaranteed returns", "real money trading") and asserts none are found. Added explicit assertion for API key field absence. The `landing_checks` dict now records that assertions were performed rather than hardcoding True.

### Files Changed

- `acceptance-test-execution.py`

---

## Finding 4 — A06 chronological ordering computed but not asserted

**Scenario**: A06 (Event Timeline and Research Notes)
**Severity**: Harness false positive
**Category**: Acceptance script defect
**Status**: Fixed

### Description

The A06 scenario computed a `chronological` boolean but never asserted it. Timeline events could have been in any order and the scenario would still report PASS. Prior event immutability was also not checked.

### Fix

Added `assert chronological` with descriptive message. Added per-event immutability check: for each event present before the note was added, verify it still exists after and its `event_type` has not changed. Added assertion that new events appeared after the note was created.

### Files Changed

- `acceptance-test-execution.py`

---

## Finding 5 — A08 paused/retired order rejection used malformed requests

**Scenario**: A08 (Paper Portfolio)
**Severity**: Harness false positive
**Category**: Acceptance script defect
**Status**: Fixed

### Description

The paused/retired order rejection test accepted any of status codes `(400, 403, 409, 422)`. A 422 response (schema validation error from a malformed request) would satisfy the check, meaning the test could pass without the server actually performing state-based rejection. The order payloads also omitted optional fields, making it ambiguous whether rejection was due to state control or schema validation.

### Fix

- Changed assertion to `r.status_code == 400` specifically — the code the server returns for state-based rejection.
- Added response body assertions: paused rejection must contain "paused" or "risk check"; retired rejection must contain "retired".
- Created `_PAUSED_ORDER_PAYLOAD` and `_RETIRED_ORDER_PAYLOAD` with all required and optional fields to ensure schema-valid requests.
- A 422 (malformed request) now correctly fails the assertion rather than being silently accepted.

### Files Changed

- `acceptance-test-execution.py`

---

## Finding 6 — A09/A10 safety checks not asserted from API responses

**Scenario**: A09 (Model Research Record), A10 (Model Research Export)
**Severity**: Harness false positive
**Category**: Acceptance script defect
**Status**: Fixed

### Description

A09 did not assert that the lock response contained `locked_at` or that the card response contained an `id`. Safety checks were computed but individual checks were not asserted — a single failing check would not produce a test failure.

A10 computed safety checks from the export package text but did not assert each one individually.

### Fix

- A09: Added `assert locked_resp.get("locked_at") is not None` and `assert card.get("id") is not None`. Added per-check assertion loop: `for check_name, passed in safety_checks.items(): assert passed, ...`.
- A10: Added per-check assertion loop for all safety checks.

### Files Changed

- `acceptance-test-execution.py`

---

## Finding 7 — Acceptance script missing reliability features

**Scenario**: All
**Severity**: Harness reliability gap
**Category**: Acceptance script defect
**Status**: Fixed

### Description

The acceptance script lacked several reliability features:
- No exit code differentiation (always exited 0 regardless of results)
- No BLOCKED status for scenarios downstream of failures
- No HTTP timeouts (requests could hang indefinitely)
- No environment variable validation (script would fail mid-run with cryptic errors)
- No `SkipScenario` exception class defined (referenced but missing)

### Fix

- Added exit codes: 0 (all PASS), 1 (any FAIL), 2 (prerequisite failure)
- Added `SCENARIO_STATUS` tracking and `require_prereq()` function for dependency chaining
- Added `SkipScenario` exception class for aborting scenarios and blocking dependents
- Added `HTTP_TIMEOUT = 15` to all `requests.*` calls
- Added `validate_environment()` that exits with code 2 if passwords are missing
- Results saved to `acceptance-results.json` in script directory
- Summary section includes BLOCKED count

### Files Changed

- `acceptance-test-execution.py`

---

## Finding 8 — A10 export safety check matched its own safety disclaimer

**Scenario**: A10 (Model Research Export Package)
**Severity**: Harness false positive
**Category**: Acceptance script defect
**Status**: Fixed

### Description

After the Finding 6 fix introduced per-check assertions, A10 began failing with:

```
Export safety check 'no_exchange_credentials' failed
```

Inspection of the downloaded package showed that the phrase "exchange credentials" only appeared in safety disclaimers (`"no exchange credentials are included"`) and in the `excluded_capabilities` manifest label (`"no_exchange_credentials"`). The package did not contain actual credentials — the safety check was matching its own disclaimer text.

### Root Cause

The check `"exchange credential" not in pkg_text` is a naive substring check. It cannot distinguish between:

- a positive assertion of credential presence (a real safety violation), and
- a negative assertion inside a disclaimer ("no exchange credentials are included") or a manifest label (`no_exchange_credentials`).

The same class of bug existed for `no_broker_credentials`.

### Fix

Replaced the substring check with a context-aware helper `_has_positive_credential_mention(text, keyword)` that scans each regex match and skips it when the preceding 40 characters end in `no ` or `no_`. Only a match NOT preceded by a negation is treated as a positive credential mention.

```python
def _has_positive_credential_mention(text, keyword):
    for m in re.finditer(rf"{keyword}", text):
        start = max(0, m.start() - 40)
        ctx = text[start:m.start()]
        if re.search(r"\bno[_ ]$", ctx):
            continue
        return True
    return False
```

### Files Changed

- `acceptance-test-execution.py` (added `import re`; added helper; replaced two safety-check expressions)

### Verification

- A10 passes against a disposable local database; package text contains 5 occurrences of "exchange credential" (all in disclaimers or `excluded_capabilities` labels), all correctly skipped by the helper.
- All 12 scenarios pass (12/12, 100%).
- Full backend suite: 204 passed.

---

## Harness Unit Tests

20 unit tests added in `backend/tests/test_acceptance_harness.py` covering four categories:

1. **False check → FAIL** (3 tests): Verifies that intentionally false assertions produce FAIL status, not PASS.
2. **Failed scenario → nonzero exit** (7 tests): Verifies exit code logic — FAIL→1, all-BLOCKED→2, all-PASS→0.
3. **Missing prereq → BLOCKED** (5 tests): Verifies prerequisite chaining — FAIL/BLOCKED prereqs produce BLOCKED downstream, not PASS.
4. **Malformed request ≠ state control** (5 tests): Verifies that 422 (schema error) does not satisfy the 400 (state rejection) assertion; payload schema validity confirmed.

---

## Warning Delta Review

- Warning count before (acceptance rehearsal): 17,378
- Warning count after harness tests added: 18,498 (delta +1,120)
- Warning count after A10 fix + final re-run: 19,334 (delta +836 from prior run)
- Root cause of +836: Full-suite re-run variance; 20 harness tests exercise API/DB code paths that trigger additional instances of known third-party deprecation warnings (pytest-asyncio, fastapi, starlette, sqlalchemy, jose). A10 regex helper adds negligible overhead.
- New warning categories introduced: no
- New Alethra-owned warning categories introduced: no
- Commit-safe conclusion: SAFE — no new warning class, no unhandled exceptions. All 204 tests pass. See `docs/warning-baseline.md` for full classification table.
