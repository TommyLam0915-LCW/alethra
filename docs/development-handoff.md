# Development handoff — saved-comparison slice

Branch: `handoff/saved-comparison-sync` (created from `verify/alignment-baseline` @ the
migration-graph + manual-decision replay work; NOT merged into `main`).
Status: implemented, **owner acceptance of the live browser flow pending**.

## What this branch contains

**Run comparison + saved comparison (this slice)**
- `backend/app/api/runs.py` — read-only backtest comparison endpoint: per-field
  match/differ/insufficient status, per-side provenance
  (captured/current/derived/absent/unavailable), evidence detail, and
  different-period handling that PRESERVES both recorded returns while
  suppressing only the numeric delta.
- `backend/app/api/schemas.py` — `RunComparisonField` / `RunComparisonResponse`.
- `backend/app/models/saved_comparison.py` +
  `backend/app/api/saved_comparisons.py` — a `SavedComparison` entity with
  create / read / update / list (no delete/share/export/public-link/snapshot).
  Purpose is required and non-blank; purpose ≤ 500, notes ≤ 5000. GET/PATCH
  re-authorize the author, the experiment, and BOTH referenced runs; a
  non-resolvable run yields 404 (never 500). PATCH keeps identity (id / run_a /
  run_b / author) immutable and treats omitted `notes` as "keep", explicit
  `null` as "clear". Saving/updating does not mutate the referenced runs.
- `backend/migrations/versions/h2b3c4d5e6f7_add_saved_comparisons.py` — the
  Alembic migration for the table (repo convention; single head).
- Wiring: `backend/app/api/__init__.py`, `backend/app/models/__init__.py`.
- `frontend/src/app/experiments/[id]/runs/compare/page.tsx` — comparison view,
  save/reopen form. Create POSTs once then PATCHes the same record (no
  duplicate); reopen loads A/B order from the saved record; a stale in-flight
  load for a previously-opened record is discarded; failed save keeps the
  typed text; success shown only after the awaited response.
- `frontend/src/app/experiments/[id]/page.tsx` — "已儲存嘅比較" list linking to
  the reopen route.
- `frontend/src/lib/api.ts` — `savedComparisons` client (create/list/get/update).

**Separate preserved work (wip)**
- `frontend/src/app/experiments/[id]/simulation/page.tsx`,
  `frontend/e2e/manual-decision-retry.spec.ts`,
  `backend/tests/test_state_version_response_contract.py`, and two diagnostic
  tests (`test_db_identity.py`, `test_dependency_trace.py`).

## Tests run (and their limits)
- Focused backend suites pass on disposable DBs via `TestClient`:
  `test_saved_comparisons.py` and `test_run_comparison.py`.
- Frontend production build passes.
- Broad suite NOT clean: representative failures involve an unstarted external
  backend (`ConnectionRefusedError`) and two collection-compatibility issues in
  files untouched by this slice. **No regression traced to this slice yet, but
  full per-failure baseline attribution was not completed** — do not treat the
  broad suite as passing.

## Browser / owner acceptance
- Login succeeds against an isolated review environment (real browser). The full
  owner flow (save → leave → reopen → edit → reopen → refresh, confirming
  purpose/notes/A-B persisted with no duplicate) is **not yet signed off**.

## Environment for a new laptop
- Backend: Python venv from `backend/requirements.txt` (project uses
  `backend/.venv/bin/python` for tests); `cp` a local `.env` with your own
  values (never committed).
- Frontend: install from the lockfile; `NEXT_PUBLIC_API_URL` points at the
  backend.
- Schema: this project's live acquisition path is `Base.metadata.create_all`
  at app startup (`backend/app/main.py`), not Alembic. The full Alembic history
  is not proven to replay cleanly on SQLite, so do NOT run `alembic upgrade
  head` against an existing database without separate verification.
- Rebuild synthetic review fixtures without real data:
  `DATABASE_URL="sqlite:////tmp/review.db" python scripts/seed_review_fixtures.py`
  (a test password is generated at runtime; nothing sensitive is stored).

## Excluded from version control (left on local disk)
`*.db`, `*.db.backup-*` (real DB snapshots), `.env`, uvicorn logs,
`*.tsbuildinfo`, `.venv`, `node_modules`, `/tmp` review resources, and test-login
credentials. The `.gitignore` was extended so these are never swept in by an
accidental `git add .`.
