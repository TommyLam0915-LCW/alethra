# Research Event Ledger — Handoff Report

**Date**: 2026-10-11
**Engine Version**: 1.1.0 (unchanged)
**Status**: Complete — 58 tests passing

---

## 1. What Was Built

A decision-trail audit system that records how research ideas evolve: hypothesis changes, strategy parameter changes with reasons, run outcomes, warnings, locking, notes, and exports. Every event carries quality metadata (recorded_before_outcome, rules_locked, data_cutoff_at).

This is a research record, not a performance claim.

---

## 2. Files Changed

### Backend

| File | Change |
|------|--------|
| `app/models/audit_event.py` | Extended from 7 to 11 fields: added actor_type, recorded_before_outcome, rules_locked, data_cutoff_at |
| `app/services/__init__.py` | NEW — Event emission service with auto-computed recorded_before_outcome |
| `app/api/schemas.py` | Added ResearchNoteCreate, ResearchNoteResponse, AuditEventResponse, VersionDiffEntry, VersionDiffResponse |
| `app/api/experiments.py` | Event emission in create/update, new timeline/notes endpoints, extended export |
| `app/api/runs.py` | Event emission in create_run, new version comparison endpoint |

### Frontend

| File | Change |
|------|--------|
| `src/lib/api.ts` | Added timeline, notes, versions.compare API methods |
| `src/app/experiments/[id]/page.tsx` | Added research timeline UI, notes section, version comparison, export button |

### Tests

| File | Change |
|------|--------|
| `tests/test_api.py` | Added 20 new tests (9 existing → 29 total) covering all event ledger requirements |

### Documentation

| File | Change |
|------|--------|
| `docs/phase1-research-event-ledger.md` | Status updated to Complete |
| `docs/phase1-manual-acceptance.md` | Added Test 10: Research Event Ledger (14 steps) |
| `docs/phase1-deliverables.md` | Updated test counts to 58 |

---

## 3. New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/experiments/{id}/timeline` | Chronological event feed (owner-only) |
| POST | `/api/v1/experiments/{id}/notes` | Create research note |
| GET | `/api/v1/experiments/{id}/notes` | List research notes |
| GET | `/api/v1/versions/compare?v1={id}&v2={id}` | Structured diff between versions |

---

## 4. Event Types Implemented (14)

All 14 required event types are emitted at the correct lifecycle points:

1. `experiment_created` — POST /experiments/
2. `hypothesis_updated` — PUT /experiments/{id} (hypothesis changed)
3. `falsification_condition_added` — PUT /experiments/{id} (conditions changed)
4. `dataset_selected` — POST /experiments/
5. `strategy_template_selected` — POST /experiments/
6. `parameter_changed` — PUT /experiments/{id} (strategy_spec changed, requires change_reason)
7. `strategy_version_created` — PUT /experiments/{id} (new version)
8. `research_run_started` — POST /versions/{id}/runs (before backtest)
9. `research_run_completed` — POST /versions/{id}/runs (after success)
10. `research_run_failed` — POST /versions/{id}/runs (after failure)
11. `warning_generated` — POST /versions/{id}/runs (per warning, actor_type=system)
12. `strategy_locked` — PUT /experiments/{id} (status → locked)
13. `research_note_added` — POST /experiments/{id}/notes
14. `export_generated` — GET /experiments/{id}/export

---

## 5. Test Coverage

### New Tests (20)

| Test Class | Tests | What It Verifies |
|-----------|-------|------------------|
| TestExperimentCreationEvents | 2 | 3 events emitted, quality metadata correct |
| TestHypothesisUpdateEvents | 2 | hypothesis_updated, falsification_condition_added |
| TestStrategyChangeEvents | 3 | 400 without change_reason, events with reason, title change exempt |
| TestRunEvents | 2 | started+completed events, warning events with actor_type=system |
| TestTimelineEndpoint | 2 | Chronological order, owner-only access |
| TestNotesEndpoints | 2 | CRUD, event emission |
| TestVersionComparison | 2 | Identical versions, different versions with correct diffs |
| TestExportEndpoint | 2 | All sections present, export_generated event |
| TestStrategyLocking | 3 | strategy_locked event, locked rejects modification, run records rules_locked |

### Full Suite: 58 tests, all passing

---

## 6. Key Design Decisions

1. **recorded_before_outcome is auto-computed** — The emit_event() function determines this from the event_type, preventing caller error.

2. **change_reason required for material changes** — Strategy spec changes require a reason (400 if absent). Title/typo edits are exempt.

3. **Timeline includes all entity types** — Events for experiments, versions, runs, and notes are all included in the timeline.

4. **Owner-only access** — Timeline, notes, and comparison endpoints all verify experiment ownership.

5. **actor_type=future_ai is schema-ready** — The column accepts "future_ai" but no AI emission is implemented.

---

## 7. Privacy

- All events private by default
- Owner-only access (same pattern as experiment queries)
- No shared-model training data collection
- No public timeline endpoint
- No user behavior scoring

---

## 8. What Is NOT Built (Explicit Non-Goals)

- AI/LLM event emission (actor_type=future_ai schema-ready, not implemented)
- Public or shared timeline
- Forward paper trading
- Real market data
- Real-money trading
- Brokerage connections

---

## 9. Statement

Events record research actions. They do not prove strategy quality, investment skill, or future performance. A complete timeline is a research record, not a performance certificate.
