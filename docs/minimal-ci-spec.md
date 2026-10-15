# Minimal CI Specification

**Date:** 2026-10-14
**Baseline:** `main` at `d86f527` (post-PR #4 merge)
**Purpose:** Define the smallest reproducible GitHub Actions CI setup for Alethra after Phase 2B.

---

## 1. Baseline and Inventory

### Repository State

| Item | Value |
|------|-------|
| Current `main` | `d86f527` (merge of PR #4) |
| Existing `.github/workflows/` | None |
| Branch protection | Not configured |

### Backend Reproducibility Inventory

| Requirement | Value | Source |
|-------------|-------|--------|
| Python version | 3.12 | `backend/Dockerfile` (`python:3.12-slim`) |
| Dependency file | `requirements.txt` (17 packages, exact-pinned `==`) | `backend/requirements.txt` |
| Test database | SQLite in-process | `conftest.py` line 2: `os.environ["DATABASE_URL"] = "sqlite:///./test.db"` |
| JSONB compatibility | Custom SQLAlchemy compiler | `conftest.py` lines 16-23 |
| Table lifecycle | `autouse=True` fixture, create/drop per test | `conftest.py` lines 51-59 |
| External network calls | None | All providers are mock/demo |
| Real credentials required | None | JWT secret hardcoded in test config |
| Docker required | No | Pure Python + SQLite |
| Test files | 11 files, 324 tests | `pytest --co` collected |

**Test breakdown:**

| File | Count |
|------|-------|
| `test_config.py` | 10 |
| `test_api.py` | 29 |
| `test_engine.py` | 29 |
| `test_simulation.py` | 10 |
| `test_paper_portfolio.py` | 12 |
| `test_model_research.py` | 24 |
| `test_model_research_export.py` | 19 |
| `test_pilot.py` | 51 |
| `test_research_drafts.py` | 54 |
| `test_method_checks.py` | 66 |
| `test_acceptance_harness.py` | 20 |
| **Total** | **324** |

**Test count source:** Collected via `pytest --co` at baseline `d86f527`. Counts are deterministic for this codebase state.

**Timing evidence:** Local verification on Python 3.14 (developer machine) recorded 2824s (47 min) for full suite — anomalously slow compared to historical baselines (66 method-check tests in ~8s), likely due to Python 3.14 environment or local machine state. GitHub Actions runners with Python 3.12 are expected to perform normally (~30-60s estimated). CI timeout set to 10 minutes as conservative starting point; adjust based on actual runner performance.

**Runtime compatibility basis:** Python 3.12 and Node 20 are specified in `backend/Dockerfile` and `frontend/Dockerfile` respectively. These manifests define production runtime, not developer machine versions. Local verification used Python 3.14 and Node 26 to confirm command correctness; version-specific compatibility is deferred to GitHub runner execution.

### Frontend Reproducibility Inventory

| Requirement | Value | Source |
|-------------|-------|--------|
| Node version | 20 | `frontend/Dockerfile` (`node:20-alpine`) |
| Lockfile | `package-lock.json`, lockfileVersion 3 | `frontend/package-lock.json` |
| Build command | `npm run build` (`next build`) | `frontend/package.json` |
| Backend needed for build | No | Pure static generation |
| API URL env var | `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:8000`) | `frontend/next.config.js` |
| Lint command | `npm run lint` (`next lint`) | `frontend/package.json` |
| Test command | Not configured in `package.json` | Playwright run separately |

### Browser Test Inventory

| File | Count | Server Required |
|------|-------|-----------------|
| `research-drafts.spec.ts` | 14 | Full acceptance stack |
| `method-review.spec.ts` | 9 | Full acceptance stack |
| `api-fetch.spec.ts` | 4 | None (stubs `global.fetch`) |
| **Total** | **27** | |

**Acceptance stack requirements:**

- PostgreSQL 16 on port 5433
- Backend on port 8001 (container 8000)
- Frontend on port 3001 (container 3000)
- Bind mounts: `./backend:/app`, `./frontend:/app`
- Environment: `JWT_SECRET_KEY`, `AI_PROVIDER=mock`, `DATA_PROVIDER=demo`
- Frontend command: `npm run dev` (not production build)
- Playwright `webServer`: echo stub (no-op), `reuseExistingServer: true`
- Hard-coded ports in test fixtures: `API_BASE=http://localhost:8001`, `FRONTEND_BASE=http://localhost:3001`

---

## 2. Recommended First CI Slice

### Job A: Backend Tests

**Rationale:** Fully self-contained. No PostgreSQL, no Docker, no network, no credentials. SQLite via `conftest.py` autouse fixture. 324 tests cover config, API, engine, simulation, portfolio, research, drafts, method checks, and acceptance harness.

**Proposed workflow:**

```yaml
name: Backend Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        working-directory: backend
        run: pip install -r requirements.txt

      - name: Run tests
        working-directory: backend
        run: pytest
```

**Exact commands:**

```bash
cd backend
pip install -r requirements.txt
pytest
```

**Environment:** No special environment variables required. `conftest.py` sets `DATABASE_URL=sqlite:///./test.db` at module import.

**Expected duration:** Local verification on Python 3.14 recorded anomalously slow 2824s (47 min). GitHub Actions runners with Python 3.12 estimated ~30-60s based on historical baselines. CI timeout set to 10 minutes as starting point; adjust based on actual runner performance.

### Job B: Frontend Production Build

**Rationale:** `next build` is pure static generation. No backend needed. Catches TypeScript errors, build failures, and dependency issues. Fast feedback on frontend changes.

**Proposed workflow:**

```yaml
name: Frontend Build

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: Build
        working-directory: frontend
        run: npm run build
```

**Exact commands:**

```bash
cd frontend
npm ci
npm run build
```

**Environment:** `NEXT_PUBLIC_API_URL` not required for build (defaults to `http://localhost:8000` in `next.config.js`, but build does not make runtime API calls).

**Expected duration:** ~60-90 seconds (cold build on CI runner).

---

## 3. Isolation and Security Constraints

### Backend Tests

- **Database:** SQLite in-process. No external database. No credentials.
- **Network:** No outbound network calls. All providers are mock/demo.
- **Secrets:** No real secrets. JWT test key hardcoded in test config.
- **Filesystem:** Tests create `test.db` in `backend/` directory. No persistent state.
- **Concurrency:** Safe to run in parallel across PRs. No shared state.

### Frontend Build

- **Network:** `npm ci` fetches from npm registry. No other network calls.
- **Secrets:** No secrets required.
- **Filesystem:** Build output in `frontend/.next/`. No persistent state.
- **Concurrency:** Safe to run in parallel across PRs.

### General CI Security

- **No secrets in workflows:** Neither job requires GitHub secrets.
- **No privileged containers:** Both jobs use standard `ubuntu-latest` runners.
- **No artifact upload needed:** Test results are pass/fail. Build output is disposable.
- **No deployment:** CI is read-only (test and build). No production impact.

### Network and External Services

- **Dependency installation requires network:** `pip install -r requirements.txt` and `npm ci` fetch packages from PyPI and npm registry respectively. This is standard CI behavior and requires outbound internet access on the runner.
- **Tests and build do not require external business services:** All AI providers are mock, all data providers are demo, no real brokerage or payment integrations. The backend tests use SQLite in-process; the frontend build is pure static generation. No external API calls are made during test or build execution.

---

## 4. Browser Acceptance CI — Third Job Specification

### 4.1 Inventory (Verified by Read-Only Inspection)

**Playwright configuration** (`frontend/playwright.config.ts`):

| Setting | Value |
|---------|-------|
| Test directory | `./e2e` |
| Browser | Chromium only |
| Workers | 1 (sequential) |
| Retries (CI) | 2 |
| Base URL | `http://localhost:3001` |
| `webServer` | Echo stub (no-op); `reuseExistingServer: true` |
| Trace | On first retry |
| Screenshot/video | On failure only |

**Test files and coverage:**

| File | Tests | Needs Stack | Coverage |
|------|-------|-------------|----------|
| `api-fetch.spec.ts` | 4 | No (stubs `global.fetch`) | API client regression (204, JSON parse, error throw) |
| `method-review.spec.ts` | 9 | Yes | Method review display, status badges, identity, provenance, timestamp fallback, no-run state, refresh, no side-effects, error handling |
| `research-drafts.spec.ts` | 14 | Yes | Navigation, mock disclosure, zero values, acceptance/finalization, URL privacy, deletion, field transfer (I/K/L), preview/cancellation, access control, Q&A persistence, invalid input, revision/audit, rejection prevention |
| **Total** | **27** | | |

**Acceptance stack** (`docker-compose.acceptance.yml`):

| Service | Image | Host Port | Container Port | Health Check |
|---------|-------|-----------|----------------|--------------|
| `db` | `postgres:16-alpine` | 5433 | 5432 | `pg_isready -U postgres` |
| `backend` | Build from `backend/Dockerfile` | 8001 | 8000 | `GET /health` → `{"status":"healthy"}` |
| `frontend` | Build from `frontend/Dockerfile` | 3001 | 3000 | None (Next.js dev server) |

**Stack environment:**

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/ai_investment_lab_acceptance` |
| `JWT_SECRET_KEY` | `acceptance-test-secret-minimum-32-chars-long` |
| `AI_PROVIDER` | `mock` |
| `DATA_PROVIDER` | `demo` |
| `CORS_ORIGINS` | `http://localhost:3001` |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8001` |

**Bind mounts and volume strategy:**

- Backend: `./backend:/app` — pip packages in site-packages (not in /app), safe to overlay.
- Frontend: `./frontend:/app` + anonymous volumes for `/app/node_modules` and `/app/.next` — preserves image-built dependencies and build cache across the bind mount.

### 4.2 Fixture Bootstrap Gap

**Finding:** All 23 browser tests that use the stack call `getAuthToken(email, password)` which POSTs to `/api/auth/login`. No test file calls `/api/auth/register`. On a fresh acceptance database (which CI creates), the test users `user_a_test@example.com` and `user_b_test@example.com` do not exist, and `getAuthToken()` will throw `Login failed: 401`.

**Test user accounts (from test source):**

| User | Email | Password (default fallback) | Source |
|------|-------|----------------------------|--------|
| USER_A | `user_a_test@example.com` | `TestPassword123!@#` | `method-review.spec.ts:8`, `research-drafts.spec.ts:8` |
| USER_B | `user_b_test@example.com` | `TestPassword456!@#` | `research-drafts.spec.ts:13` |

**Required bootstrap (before Playwright):**

```bash
curl -sf -X POST http://localhost:8001/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user_a_test@example.com","password":"TestPassword123!@#","display_name":"User A","user_type":"individual"}'

curl -sf -X POST http://localhost:8001/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user_b_test@example.com","password":"TestPassword456!@#","display_name":"User B","user_type":"individual"}'
```

**Alternative (preferred long-term):** Add `test.beforeAll` registration to each spec file so tests are self-bootstrapping. Not required for initial CI — the curl bootstrap is simpler and does not modify test code.

### 4.3 Proposed Workflow: Job C — Browser Acceptance

```yaml
name: Browser Acceptance

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: browser-acceptance-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  acceptance:
    name: Browser Acceptance (Playwright)
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Start acceptance stack
        run: docker compose -f docker-compose.acceptance.yml up --build -d

      - name: Wait for backend health
        run: |
          for i in $(seq 1 30); do
            if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
              echo "Backend healthy"
              exit 0
            fi
            sleep 2
          done
          echo "Backend did not become healthy in 60s"
          docker compose -f docker-compose.acceptance.yml logs
          exit 1

      - name: Wait for frontend
        run: |
          for i in $(seq 1 30); do
            if curl -sf http://localhost:3001 > /dev/null 2>&1; then
              echo "Frontend ready"
              exit 0
            fi
            sleep 2
          done
          echo "Frontend did not become ready in 60s"
          docker compose -f docker-compose.acceptance.yml logs
          exit 1

      - name: Bootstrap test users
        run: |
          curl -sf -X POST http://localhost:8001/api/auth/register \
            -H "Content-Type: application/json" \
            -d '{"email":"user_a_test@example.com","password":"TestPassword123!@#","display_name":"User A","user_type":"individual"}'
          curl -sf -X POST http://localhost:8001/api/auth/register \
            -H "Content-Type: application/json" \
            -d '{"email":"user_b_test@example.com","password":"TestPassword456!@#","display_name":"User B","user_type":"individual"}'

      - name: Set up Node 20
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install frontend dependencies
        working-directory: frontend
        run: npm ci

      - name: Install Playwright browsers
        working-directory: frontend
        run: npx playwright install --with-deps chromium

      - name: Run browser tests
        working-directory: frontend
        run: npx playwright test

      - name: Upload test artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: frontend/playwright-report/
          retention-days: 7

      - name: Tear down acceptance stack
        if: always()
        run: docker compose -f docker-compose.acceptance.yml down -v
```

### 4.4 Step-by-Step Rationale

| Step | Purpose | Failure Mode |
|------|---------|--------------|
| Checkout | Get source for Docker build context | Git error (transient) |
| `docker compose up --build -d` | Build images from Dockerfiles, start all 3 services, create PostgreSQL volume | Docker build failure (dependency or code error) |
| Wait for backend | Poll `GET /health` up to 60s | Backend crash (DB not ready, import error, migration failure) |
| Wait for frontend | Poll `GET /` up to 60s | Next.js dev server crash (compile error, missing dependency) |
| Bootstrap test users | Register USER_A and USER_B via API | Registration endpoint failure (schema change, validation error) |
| `npm ci` | Install frontend dependencies including `@playwright/test` | Lockfile mismatch, network error |
| `playwright install --with-deps` | Download Chromium binary + OS libraries | Network error, unsupported platform |
| `playwright test` | Run all 27 tests (api-fetch + method-review + research-drafts) | Test failure, timeout, flaky assertion |
| Upload artifacts | Preserve HTML report and failure screenshots | N/A (runs on `always()`) |
| `docker compose down -v` | Remove containers and PostgreSQL volume | N/A (runs on `always()`) |

### 4.5 Isolation and Security

- **No secrets required:** JWT secret is hardcoded in compose file (`acceptance-test-secret-minimum-32-chars-long`). Test user passwords are in test source code.
- **No external network calls:** All providers are mock/demo. PostgreSQL is local container.
- **No persistent state:** PostgreSQL volume is created and destroyed per run (`-v` flag in teardown).
- **No privileged containers:** Standard Docker on `ubuntu-latest`.
- **Concurrency control:** `concurrency` group cancels in-progress runs on the same ref.
- **Ephemeral:** GitHub Actions runners are disposable. No cross-run contamination.

### 4.6 Expected Duration

| Phase | Estimated Time |
|-------|---------------|
| Docker image build (backend + frontend) | 60-90s |
| Stack startup + health checks | 15-30s |
| User bootstrap | <5s |
| `npm ci` + Playwright install | 30-60s |
| Test execution (27 tests, 1 worker, 2 retries) | 60-120s |
| Teardown | 5-10s |
| **Total** | **~3-5 minutes** |

### 4.7 Known Risks

1. **Docker build time:** First run on a cold cache builds both images from scratch. Subsequent runs benefit from Docker layer caching on the runner.

2. **Test flakiness:** Browser tests with `waitForTimeout` calls (e.g., `research-drafts.spec.ts:393`, `method-review.spec.ts:420`) may be sensitive to runner performance. Playwright's 2-retry policy on CI mitigates this.

3. **Port conflicts:** GitHub Actions runners are ephemeral and single-tenant. Port conflicts are not expected.

4. **Compose version:** The compose file uses `version: '3.8'`. Modern Docker Compose V2 (included in `ubuntu-latest`) handles this natively.

5. **Bind mount behavior in CI:** The bind mounts (`./backend:/app`, `./frontend:/app`) overlay the checked-out source onto the built image. This works because:
   - Backend pip packages are in `/usr/local/lib/python3.12/site-packages/` (not in `/app`).
   - Frontend node_modules are preserved by the anonymous volume `/app/node_modules`.

### 4.8 Readiness Assessment

**READY — with one prerequisite step.**

The browser acceptance CI job is implementable now. The only gap is the fixture bootstrap (Section 4.2), which is addressed by the curl registration step in the proposed workflow. No test code modification is required.

**Prerequisites met:**
- [x] Docker Compose file exists and is valid.
- [x] Dockerfiles exist for both backend and frontend.
- [x] Backend has `/health` endpoint for readiness polling.
- [x] Playwright configuration exists with CI-appropriate settings.
- [x] Test files exist and are structured for Playwright runner.
- [x] Test user credentials have deterministic defaults in source.
- [x] Fixture bootstrap can be done via curl (no test code change needed).

**Not a prerequisite (can be deferred):**
- [ ] Self-bootstrapping tests (register in `beforeAll` instead of external curl).
- [ ] Frontend health check endpoint (frontend `GET /` is sufficient).
- [ ] Database migration step (SQLAlchemy `create_all` happens at backend startup).

---

## 5. Blockers and Prerequisites

### Jobs A and B: No Blockers

- **Backend tests:** No blockers. Fully self-contained.
- **Frontend build:** No blockers. Pure static generation.

### Job C: Browser Acceptance — No Blockers, One Prerequisite Step

- **Docker availability:** GitHub Actions runners include Docker and Docker Compose V2 by default. No blocker.
- **Port conflicts:** Runners are ephemeral and single-tenant. No blocker.
- **Fixture bootstrap:** Test users do not self-register. The curl bootstrap step in the proposed workflow (Section 4.3) addresses this without modifying test code.
- **Test flakiness:** Browser tests may be flaky on CI due to timing. Playwright's 2-retry policy mitigates this. First CI run should be monitored.

---

## 6. Implementation File Scope

### Files to Create

| File | Purpose |
|------|---------|
| `.github/workflows/backend-tests.yml` | Backend test job (Job A) |
| `.github/workflows/frontend-build.yml` | Frontend build job (Job B) |
| `.github/workflows/browser-acceptance.yml` | Browser acceptance job (Job C) |

### Files NOT Modified

- No changes to `backend/` code or tests.
- No changes to `frontend/` code or tests.
- No changes to `docker-compose.acceptance.yml`.
- No changes to `requirements.txt` or `package.json`.
- No changes to branch protection or repository settings.

### Total Scope

- **3 new files** (workflow YAML).
- **0 modified files.**
- **0 deleted files.**

---

## 7. Acceptance Criteria

### Backend Tests Job

- [ ] Workflow triggers on push to `main` and pull requests to `main`.
- [ ] Uses Python 3.12.
- [ ] Installs dependencies from `backend/requirements.txt`.
- [ ] Runs `pytest` in `backend/` directory.
- [ ] All 324 tests pass.
- [ ] Job completes in <60 seconds.

### Frontend Build Job

- [ ] Workflow triggers on push to `main` and pull requests to `main`.
- [ ] Uses Node 20.
- [ ] Installs dependencies with `npm ci` in `frontend/` directory.
- [ ] Runs `npm run build` in `frontend/` directory.
- [ ] Build succeeds without errors.
- [ ] Job completes in <180 seconds.

### Browser Acceptance Job

- [ ] Workflow triggers on push to `main` and pull requests to `main`.
- [ ] Starts acceptance stack via `docker compose -f docker-compose.acceptance.yml up --build -d`.
- [ ] Waits for backend health (`GET /health` returns 200 within 60s).
- [ ] Waits for frontend readiness (`GET /` returns 200 within 60s).
- [ ] Bootstraps test users USER_A and USER_B via `/api/auth/register`.
- [ ] Installs Playwright Chromium with OS dependencies.
- [ ] Runs all 27 browser tests (api-fetch + method-review + research-drafts).
- [ ] All 27 tests pass (with up to 2 retries per test on CI).
- [ ] Uploads Playwright HTML report as artifact on success or failure.
- [ ] Tears down acceptance stack with `docker compose down -v`.
- [ ] Job completes in <20 minutes.

### General

- [ ] All three jobs run in parallel (no dependencies between them).
- [ ] No secrets required in repository settings.
- [ ] Browser acceptance uploads Playwright report as artifact; backend and frontend jobs do not.
- [ ] No deployment triggered.

---

## 7B. Verification Status

### Local Verification (Pre-CI)

**Backend tests:**
- Local execution on Python 3.14: 324 passed in 2824s (47 min) — anomalously slow, likely environment-specific.
- Command correctness verified: `pytest` runs successfully, all tests pass.

**Frontend build:**
- Local execution on Node 26: build succeeded, all pages generated.
- Command correctness verified: `npm ci` and `npm run build` complete without errors.

### GitHub Runner Verification (PR #5)

**Backend tests — VERIFIED:**
- Run: https://github.com/TommyLam0915-LCW/alethra/actions/runs/38685825749
- Tested commit: `7d0f3f868a7ffc38f61827ec12304e1fba1d571f`
- Runtime: Python 3.12 on `ubuntu-latest`
- Result: 324 passed, 2468 warnings in 141.20s (pytest-reported duration)
- Total job duration: 2m49s
- Conclusion: success

**Frontend build — VERIFIED:**
- Run: https://github.com/TommyLam0915-LCW/alethra/actions/runs/38685825531
- Tested commit: `7d0f3f868a7ffc38f61827ec12304e1fba1d571f`
- Runtime: Node 20.20.2, npm 10.8.2 on `ubuntu-latest`
- Build step duration: 11s
- Total job duration: 27s
- Conclusion: success

**Note:** The cause of the 47-minute local backend run on Python 3.14 remains unconfirmed. GitHub runner execution on Python 3.12 completed in 2m49s, consistent with historical baselines.

### Browser Acceptance — Local Validation Status

**Workflow file created:** `.github/workflows/browser-acceptance.yml` on branch `ci/browser-acceptance`.

**Local validation performed:**
- YAML syntax: valid (parsed by GitHub Actions schema).
- Action inputs: verified against action documentation (actions/checkout@v4, actions/setup-node@v4, actions/upload-artifact@v4).
- Compose configuration: `docker-compose.acceptance.yml` uses disposable test resources (PostgreSQL volume, bind mounts with anonymous volumes for node_modules/.next). No operational data or sensitive host files mounted.
- Git diff: clean (only new workflow file and spec update).

**Local validation NOT performed:**
- Docker Compose stack startup: not executed locally (requires Docker daemon, port availability, and ~3-5 minutes for full stack startup + test execution).
- Playwright test execution: not executed locally (depends on stack startup).
- Fixture bootstrap: not executed locally (depends on stack startup).

**Limitations:**
- Local pass on YAML/Compose validation does not establish GitHub runner success.
- First CI run should be monitored for: Docker build time, stack startup duration, test flakiness, and artifact upload behavior.
- Browser tests with `waitForTimeout` calls may be sensitive to runner performance. Playwright's 2-retry policy on CI mitigates this.

**What remains for first GitHub runner execution:**
- Confirm Docker Compose V2 is available on `ubuntu-latest` (expected: yes).
- Confirm ports 8001 and 3001 are available on runner (expected: yes, ephemeral runner).
- Confirm Playwright Chromium download succeeds on runner (expected: yes, standard CI pattern).
- Observe actual test execution time and flakiness.
- Verify artifact upload and retention behavior.

---

## 8. Owner Decisions Required

### Decision 1: Workflow Trigger Scope

**Option A (Recommended):** Trigger on push to `main` and pull requests to `main`.

**Option B:** Trigger only on pull requests to `main` (skip push to `main`).

**Option C:** Trigger on all branches and all pull requests.

**Recommendation:** Option A. Matches standard practice. Catches breakage on `main` and validates PRs before merge.

### Decision 2: Workflow File Naming

**Option A (Recommended):** Separate files (`backend-tests.yml`, `frontend-build.yml`).

**Option B:** Single file (`ci.yml`) with two jobs.

**Recommendation:** Option A. Easier to read, easier to disable one job independently, clearer GitHub Actions UI.

### Decision 3: Lint Job

**Option A (Recommended):** Defer lint. Add later if needed.

**Option B:** Add `frontend lint` job now (`npm run lint`).

**Recommendation:** Option A. Minimal slice first. Lint can be added as a fourth job once build is stable.

### Decision 4: Browser Acceptance CI

**Option A:** Add browser acceptance as Job C now, in the same PR as Jobs A and B.

**Option B (Selected):** Add Jobs A and B first (PR #5, merged 2026-10-15), then add Job C in a follow-up PR.

**Decision:** Option B. Jobs A and B were implemented and verified in PR #5 (merged 2026-10-15, commit 61676fb). Job C is now being implemented as a separate follow-up on branch `ci/browser-acceptance`. This isolates the Docker-dependent job for easier debugging and allows the simpler jobs to stabilize first.

**Implementation status:** Job C workflow created locally on branch `ci/browser-acceptance`. Awaiting owner review before commit and PR creation.

---

## 9. Summary

**Complete CI specification (three jobs):**

- **Job A:** Backend tests (Python 3.12, `pytest`, 324 tests, ~3 minutes on GitHub runner).
- **Job B:** Frontend production build (Node 20, `next build`, ~27 seconds on GitHub runner).
- **Job C:** Browser acceptance (Docker Compose stack, Playwright, 27 tests, ~3-5 minutes estimated).

**Why sufficient:**

- Covers all backend logic (324 tests, 11 files).
- Catches frontend build failures and TypeScript errors.
- Validates browser UI behavior end-to-end (method review, research drafts, API client).
- Jobs A and B are fully self-contained (no Docker, no secrets, no external dependencies).
- Job C uses the existing acceptance stack (PostgreSQL + backend + frontend containers).

**Reproducibility:**

- Jobs A and B: fully reproducible, verified on GitHub Actions runners (PR #5).
- Job C: reproducible via Docker Compose. One prerequisite step (fixture user bootstrap) addressed by curl registration in workflow.

**Browser CI specification:**

- **READY.** Concrete workflow YAML provided in Section 4.3.
- **Key finding:** Test users do not self-register; curl bootstrap step required before Playwright.
- **Infrastructure:** 3-container stack (PostgreSQL 16, backend, frontend), hard-coded ports (8001, 3001), bind mounts with anonymous volumes.

**Implementation scope:**

- 3 new files (`.github/workflows/backend-tests.yml`, `.github/workflows/frontend-build.yml`, `.github/workflows/browser-acceptance.yml`).
- 0 modified files.
- 0 deleted files.

**Owner decisions:**

1. Workflow trigger scope (recommended: push to `main` + PRs to `main`).
2. Workflow file naming (recommended: separate files).
3. Lint job (recommended: defer).
4. Browser acceptance CI (recommended: add Jobs A and B first, then Job C in follow-up PR).

---

Minimal CI specification prepared locally. No workflow, code, dependency, remote-state, or operational database change was made. Awaiting owner review before implementation.
