# System Rehearsal — Repair Log

**Date**: 2026-10-12
**Branch**: chore/system-rehearsal-20261012
**Reference**: docs/system-rehearsal-initial-findings.md

---

## Repair R-1: Simulation Labeling (D-1)

**Problem**: Phase 3A simulation labeled "forward simulation" (前瞻模擬) / "forward observation" (前瞻觀察) in UI and docs, but implementation uses pre-existing demo data (historical replay).

**Reason**: Users and reviewers could mistakenly believe the system observes genuinely new market data. Accurate labeling is required for research integrity.

**Changes**:

| File | Change |
|------|--------|
| `frontend/src/app/models/page.tsx` | Label: 前瞻觀察中 → 重播觀察中 |
| `frontend/src/app/models/[id]/page.tsx` | Label: 前瞻觀察中 → 重播觀察中; dropdown: 前瞻觀察 → 重播觀察 |
| `frontend/src/app/admin/page.tsx` | FunnelCard: 啟動前瞻模擬 → 啟動歷史重播模擬 |
| `frontend/src/app/pilot/page.tsx` | Label: 前瞻模擬上限 → 歷史重播模擬上限 |
| `frontend/src/app/experiments/[id]/simulation/page.tsx` | Methodology status: forward_observation → historical_replay; added clarification text |
| `README.md` | Phase 3A label: 鎖定與前瞻模擬 → 鎖定與歷史重播模擬; test count description updated |
| `docs/phase3a-forward-simulation.md` | Title: Forward Simulation → Historical Replay Simulation; added methodology clarification block |
| `docs/research-methodology.md` | Section 11 heading: 前瞻模擬 → 歷史重播模擬; status table updated; description updated |
| `docs/phase1-known-limitations.md` | Table entry: 前瞻模擬 → 歷史重播模擬; added demo data note |

**Not changed** (by design): Internal code identifiers — enum values (`forward_observation`), API field names (`forward_simulation_limit`, `forward_simulation_started`), database columns. Renaming these would require database migrations and API versioning with no user-facing benefit.

**Test**: No new tests needed — labeling-only change. Verified by grep that all user-visible labels updated.

---

## Repair R-2: Bilingual Disclaimers on Paper Portfolio (D-2)

**Problem**: Paper portfolio pages had English-only disclaimers. Rehearsal spec requires bilingual (English + Traditional Chinese) disclaimers on all research/trading pages.

**Reason**: Compliance requirement — all users must see disclaimers in their language. Alethra serves both English and Chinese-speaking researchers.

**Changes**:

| File | Change |
|------|--------|
| `frontend/src/app/paper-portfolio/page.tsx` | Disclaimer expanded to bilingual: English text + 提醒 (Chinese) text |
| `frontend/src/app/paper-portfolio/[id]/page.tsx` | Disclaimer expanded to bilingual: English text + 提醒 (Chinese) text |

**Test**: Visual inspection of disclaimer content. No automated test needed — static text change.

---

## Repair R-3: Production Safety Validation (D-3)

**Problem**: `config.py` had no validation preventing deployment with insecure defaults. `JWT_SECRET_KEY` defaulted to a well-known string; `APP_ENV` defaulted to "development" with no guard against production use.

**Reason**: If deployed to production without reading `.env.example` carefully, the system would use a publicly known JWT secret, allowing anyone to forge authentication tokens.

**Changes**:

| File | Change |
|------|--------|
| `backend/app/config.py` | Added `model_validator` that rejects insecure JWT defaults and enforces minimum 32-character secret when `APP_ENV` is "production" or "staging" |
| `backend/tests/test_config.py` | New file: 10 tests covering development permissiveness, production rejection of defaults/short secrets, production acceptance of long secrets, staging validation, CORS parsing |

**Test**: `pytest tests/test_config.py` — 10/10 pass.

---

## Summary

| Repair | Defect | Files Changed | Tests Added | Status |
|--------|--------|---------------|-------------|--------|
| R-1 | D-1 (labeling) | 9 files | 0 (labeling only) | Done |
| R-2 | D-2 (disclaimers) | 2 files | 0 (static text) | Done |
| R-3 | D-3 (config safety) | 2 files | 10 | Done |

**Total new tests**: 10
**Total tests after repair**: 180 (170 existing + 10 new)
