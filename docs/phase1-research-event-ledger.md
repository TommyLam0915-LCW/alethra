# Research Event Ledger — Implementation Plan

**Date**: 2026-10-11
**Engine Version**: 1.1.0 (unchanged)
**Status**: Complete (29 tests passing)

---

## 1. Purpose

Record how a research idea evolves: what the user originally believed, what they tested, what they changed, why they changed it, which dataset and data cutoff were used, what results and warnings appeared, whether the user locked the experiment, and whether the user accepted or rejected an AI draft (future phase).

This is a decision-trail foundation — not a performance claim, not a behavior score, not a public feed.

---

## 2. Scope

### In Scope

- AuditEvent model extension (add `actor_type`, quality metadata)
- Event emission service (helper to create events)
- Event integration into existing API routes (experiment CRUD, run lifecycle)
- Research notes CRUD API
- Timeline API endpoint (chronological event feed per experiment)
- Version comparison API (structured diff between two versions)
- Extended export (timeline, version history, notes, methodology, disclaimer)
- Frontend: research timeline UI, research notes UI, version comparison UI
- Tests for all new endpoints and event integrity rules
- Documentation

### Explicit Non-Goals

- AI/LLM event emission (actor_type=future_ai is schema-ready, not implemented)
- Public or shared timeline (events are private, owner-only)
- User behavior scoring
- Forward paper trading
- Real market data
- Real-money trading
- Brokerage connections

---

## 3. AuditEvent Model Extension

### Current Fields (7)

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| entity_type | String(50) | experiment, version, run, note |
| entity_id | String(36) | UUID of related entity |
| event_type | String(50) | See event types below |
| actor_id | UUID | FK users.id, nullable |
| payload_json | JSONB | Event-specific data |
| created_at | DateTime | Indexed |

### New Fields (4)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| actor_type | String(10) | "human" | human / system / future_ai |
| recorded_before_outcome | Boolean | true | Event was recorded before the user could see the result |
| rules_locked | Boolean | false | Whether strategy rules were locked at event time |
| data_cutoff_at | String(10) | null | ISO date of dataset cutoff, if applicable |

Total: 11 fields.

---

## 4. Required Event Types (14)

| # | event_type | entity_type | When emitted |
|---|-----------|-------------|--------------|
| 1 | experiment_created | experiment | POST /experiments/ |
| 2 | hypothesis_updated | experiment | PUT /experiments/{id} (hypothesis changed) |
| 3 | falsification_condition_added | experiment | PUT /experiments/{id} (falsification_conditions changed) |
| 4 | dataset_selected | experiment | POST /experiments/ or PUT (asset_ids changed) |
| 5 | strategy_template_selected | version | POST /experiments/ or PUT (new version with template_type) |
| 6 | parameter_changed | version | PUT /experiments/{id} (strategy_spec changed, new version) |
| 7 | strategy_version_created | version | PUT /experiments/{id} (new version created) |
| 8 | research_run_started | run | POST /versions/{id}/runs (before backtest) |
| 9 | research_run_completed | run | POST /versions/{id}/runs (after success) |
| 10 | research_run_failed | run | POST /versions/{id}/runs (after failure) |
| 11 | warning_generated | run | POST /versions/{id}/runs (for each warning) |
| 12 | strategy_locked | experiment | PUT /experiments/{id} (status → locked) |
| 13 | research_note_added | note | POST /experiments/{id}/notes |
| 14 | export_generated | experiment | GET /experiments/{id}/export |

---

## 5. Event Quality Rules

1. **recorded_before_outcome = true** for: experiment_created, hypothesis_updated, falsification_condition_added, dataset_selected, strategy_template_selected, parameter_changed, strategy_version_created, research_run_started, strategy_locked, research_note_added.

2. **recorded_before_outcome = false** for: research_run_completed, research_run_failed, warning_generated, export_generated (these occur after or as part of outcome).

3. **rules_locked = true** for events emitted after experiment status is set to locked.

4. **data_cutoff_at** = end_date from strategy_spec when event involves a strategy_spec or run.

5. **change_reason** is required in payload when event_type = parameter_changed. If change_reason is absent for a material strategy change, the API returns 400.

6. **Idempotent run events**: Repeated POST to /versions/{id}/runs creates separate runs (each is a distinct research action), but each run emits its own started/completed/failed events — no deduplication needed since each run is unique.

---

## 6. API Endpoints

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/experiments/{id}/timeline | Chronological event feed for experiment |
| POST | /api/v1/experiments/{id}/notes | Create research note |
| GET | /api/v1/experiments/{id}/notes | List research notes |
| GET | /api/v1/versions/compare?v1={id}&v2={id} | Structured diff between two versions |

### Modified Endpoints

- POST /experiments/ → emits experiment_created, dataset_selected, strategy_template_selected
- PUT /experiments/{id} → emits hypothesis_updated, falsification_condition_added, parameter_changed, strategy_version_created, strategy_locked (as applicable)
- POST /versions/{id}/runs → emits research_run_started, research_run_completed/failed, warning_generated
- GET /experiments/{id}/export → emits export_generated; extended payload includes timeline, version history, notes

---

## 7. Research Notes

- Model already exists (ResearchNote): id, experiment_id, version_id, author_id, content, created_at
- Need: API routes for CRUD, Pydantic schemas
- Notes are private, owner-only
- Creating a note emits research_note_added event

---

## 8. Version Comparison

Structured diff between two ExperimentVersion records:
- Template type change
- Asset IDs change
- Date range change
- Signal parameters change
- Commission/slippage change
- Initial cash change
- Each diff entry: {field, old_value, new_value}

---

## 9. Extended Export

Add to existing export:
- hypothesis, falsification_conditions, original_idea (already present)
- version_history: list of all versions with config_hash, change_reason, created_at
- events: chronological timeline
- notes: research notes
- dataset metadata: asset_ids, date_range, checksum
- run metadata: all runs (not just latest)
- methodology: engine_version, signal convention, benchmark convention
- disclaimer: full brand disclaimers (already present)

---

## 10. Frontend Changes

### Experiment Detail Page Additions

1. **Research Timeline** — collapsible section showing chronological events
2. **Research Notes** — section with add-note form and note list
3. **Version History** — list with comparison button
4. **Version Comparison** — modal or inline diff view
5. **Export Button** — triggers extended export download

### API Client Additions

- timeline.get(experimentId)
- notes.list(experimentId), notes.create(experimentId, data)
- versions.compare(v1Id, v2Id)

---

## 11. Test Requirements (8)

1. Creating experiment emits experiment_created + dataset_selected + strategy_template_selected
2. Updating hypothesis emits hypothesis_updated
3. Changing strategy_spec emits parameter_changed (with change_reason) + strategy_version_created
4. Creating run emits research_run_started + research_run_completed
5. Run failure emits research_run_failed
6. Warnings emit warning_generated events
7. Timeline endpoint returns events in chronological order, owner-only
8. Export includes version history, events, notes, disclaimer

---

## 12. Privacy

- All events private by default
- Owner-only access (same pattern as experiment queries)
- No shared-model training data collection
- No public timeline endpoint

---

## 13. Execution Order

1. Extend AuditEvent model
2. Create event emission service
3. Add Pydantic schemas for notes, events, timeline, version diff
4. Integrate events into experiments.py routes
5. Integrate events into runs.py routes
6. Add research notes API routes
7. Add timeline API endpoint
8. Add version comparison API endpoint
9. Extend export endpoint
10. Frontend: API client extensions
11. Frontend: timeline UI
12. Frontend: notes UI
13. Frontend: version comparison UI
14. Tests
15. Documentation update

---

## 14. Statement

Events record research actions. They do not prove strategy quality, investment skill, or future performance. A complete timeline is a research record, not a performance certificate.
