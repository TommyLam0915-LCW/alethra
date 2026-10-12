# PR #2 Focused Review Corrections — Report

**Date**: 2026-10-12
**Branch**: feat/phase2a-research-drafts
**Scope**: 6 confirmed backend review gaps + deletion cascade inspection

---

## Summary

All 6 review gaps corrected. 21 new tests added. Full suite: **253 passed, 0 failed**.

---

## Corrections Applied

### Fix 1: Validate answers values, not only field names

**File**: `backend/app/api/research_drafts.py`
**Change**: `/answers` endpoint now validates answer values through canonical Pydantic validators (`ResearchDraftUpdate`) before applying them. Invalid symbols, templates, and out-of-range values are rejected with HTTP 400.
**Tests**: 4 new tests in `TestAnswerValueValidation`

### Fix 2: Protect finalized drafts in /questions endpoint

**File**: `backend/app/api/research_drafts.py`
**Change**: `/questions` endpoint returns existing questions read-only for accepted/rejected drafts without regenerating or modifying them.
**Tests**: 3 new tests in `TestFinalizedDraftQuestionsProtection`

### Fix 3: Transactional consistency

**File**: `backend/app/api/research_drafts.py`
**Change**: All draft operations (create, update, answers, approve) follow single-commit pattern: draft change → `db.flush()` → add revision → emit audit → `db.commit()`. Validation failures prevent any DB changes.
**Tests**: 4 new tests in `TestTransactionalConsistency` (including failure-injection tests)

### Fix 4: Validate linked experiment ownership

**File**: `backend/app/api/research_drafts.py`
**Change**: `_validate_experiment_link()` checks that linked experiment exists AND belongs to current user (`experiment.owner_id == current_user.id`). Returns 404 if not found, 403 if belongs to another user.
**Tests**: 3 new tests in `TestExperimentOwnershipValidation`

### Fix 5: Correct missing-field detection for zero values

**File**: `backend/app/lib/mock_research_assistant.py`
**Change**: `_is_missing()` distinguishes `None`/empty string (missing) from zero values (valid). `validate_draft_completeness()` now returns `zero_cost_warnings` list for zero commission/slippage.
**Tests**: 6 new tests in `TestMissingFieldDetection`

### Fix 6: Correct completion claims in documentation

**File**: `docs/current-system-status.md`
**Change**: Phase 2A labeled as "backend" with note that frontend draft-review UI is pending. Test count updated 232 → 253. Deletion cascade policy documented in Safety Controls table.

---

## Deletion Cascade Policy

**Inspected**: `backend/app/models/research_drafts.py` lines 54-55
**Policy**: `cascade="all, delete-orphan"` on `revisions` and `audit_events` relationships
**Behavior**: Deleting a `ResearchDraft` removes all associated revisions and audit events
**Rationale**: Privacy by design — draft deletion removes all research history
**Documented**: `docs/current-system-status.md` Safety Controls table
**Test**: `TestDeletionCascade::test_delete_removes_revisions_and_audit_events`

---

## Files Changed

1. `backend/app/api/research_drafts.py` — Fixes 1-4, deletion cleanup
2. `backend/app/lib/mock_research_assistant.py` — Fix 5
3. `backend/tests/test_research_drafts.py` — 21 new tests
4. `docs/current-system-status.md` — Fix 6, test count, cascade policy

---

## Test Results

**Full backend suite**: 253 passed, 0 failed (108.95s)
**Research drafts tests**: 49 passed, 0 failed (25.65s)
**New tests added**: 21
**Existing tests preserved**: 232 → 253 (all passing)

---

## Constraints Respected

- No commits, pushes, merges, or deploys
- No Phase 2B work
- No external AI, live market data, broker/exchange/signals/execution
- Additive changes only — all existing functionality preserved
- Feature branch isolation maintained

---

## Next Steps

Ready for review and commit when authorized.
