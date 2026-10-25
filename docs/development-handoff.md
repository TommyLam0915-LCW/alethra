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
- Frontend: `npm ci` in `frontend/` (install from the lockfile, no pin changes);
  `NEXT_PUBLIC_API_URL` points at the backend. For the Playwright regression,
  browsers come from `npx playwright install chromium`.
- Schema: this project's live acquisition path is `Base.metadata.create_all`
  at app startup (`backend/app/main.py`). On **PostgreSQL** the Alembic chain has
  since been replayed in disposable databases two ways — from empty to head
  `h8i9j0k1l2m3`, and from the `h2b3c4d5e6f7` predecessor schema with synthetic
  predecessor rows (`backend/scripts/verify_upgrade_with_predecessor_data.py`).
  The full Alembic history is still **not** proven to replay cleanly on SQLite,
  so do NOT run `alembic upgrade head` against an existing SQLite database
  without separate verification.
- Rebuild synthetic review fixtures without real data:
  `DATABASE_URL="sqlite:////tmp/review.db" python scripts/seed_review_fixtures.py`
  (a test password is generated at runtime; nothing sensitive is stored).

## First run on a new laptop (branch `work/unified-research-flow`)

Both runtime images build from tracked files only. The review stack's recipes
live at the repository root, alongside `Dockerfile.backend` / `Dockerfile.frontend`:

- `Dockerfile.backend.unified` — review/E2E-style backend (deps + `COPY . .`)
- `Dockerfile.frontend.unified` — multi-stage **production** frontend build;
  `NEXT_PUBLIC_API_URL` is a build arg (a value baked into the client bundle,
  so it must be set at build time, not at run time)

`docker-compose.unified-dev.yml` references them as `../Dockerfile.<name>` because
Compose resolves `dockerfile` relative to the build `context`, same as
`docker-compose.prod.yml` does.

1. Review stack (production frontend on `127.0.0.1:3011`, backend on `127.0.0.1:8011`,
   unpublished PostgreSQL on a named volume). Create your own git-ignored,
   mode-600 env file with values of your choosing — only these variable NAMES are
   required: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `JWT_SECRET_KEY`
   (plus `SEED_EMAIL`, `SEED_PASSWORD` if you seed a login yourself). Never put
   values in CLI arguments or commit the file.
   ```bash
   docker compose -p alethra-unified-dev -f docker-compose.unified-dev.yml \
     --env-file .local/unified-dev-credentials.env up -d --build
   ```
   Note: seeding the review database's synthetic experiment/runs currently uses a
   local-only script; a fresh volume gives you a schema-complete but empty app.
2. Disposable E2E runtime + regression (its own compose project, ports, ephemeral
   volume, database, generated credentials and backend image — it builds that
   image from `docker-compose.e2e.yml` if absent, and never touches the review DB):
   ```bash
   ./start_e2e_research_flow.sh          # http://localhost:3099 (next dev), :8099 backend
   cd frontend && E2E_FRONTEND_BASE=http://localhost:3099 \
     E2E_CRED_FILE=../.local/e2e-credentials.env \
     npx playwright test --config playwright.research-flow.config.ts
   ./stop_e2e_research_flow.sh           # task-owned resources only (--purge also drops creds)
   ```
   `:3099` is a `next dev` **development server** on the working tree, so it proves
   source behaviour, not production-image behaviour; the production image is the
   `:3011` service built in step 1.
   To run a second, fully isolated instance alongside the first (e.g. from another
   checkout), override `E2E_COMPOSE_PROJECT`, `E2E_FRONTEND_PORT`, `E2E_BACKEND_PORT`,
   `E2E_POSTGRES_DB` (must start with `alethra_e2e`), `E2E_ENV_FILE`, `E2E_LOG_DIR`, and
   pass the same values to the stop script. For the review stack, `UNIFIED_BACKEND_IMAGE`
   and `UNIFIED_FRONTEND_IMAGE` let you build without retagging a running stack's images.
3. Migration chain check (disposable databases only, never the review DB):
   ```bash
   docker compose -p alethra-e2e -f docker-compose.e2e.yml \
     --env-file .local/e2e-credentials.env exec backend \
     python scripts/verify_upgrade_with_predecessor_data.py
   ```

## Excluded from version control (left on local disk)
`*.db`, `*.db.backup-*` (real DB snapshots), `.env`, uvicorn logs,
`*.tsbuildinfo`, `.venv`, `node_modules`, `/tmp` review resources, and test-login
credentials. The `.gitignore` was extended so these are never swept in by an
accidental `git add .`.
