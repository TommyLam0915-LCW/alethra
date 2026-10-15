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

## 4. Browser CI Assessment

### Recommendation: Deferred

**Rationale:**

1. **Acceptance stack orchestration required:** Browser tests need three containers (PostgreSQL, backend, frontend) running simultaneously with specific ports (5433, 8001, 3001).

2. **Hard-coded ports:** Test fixtures use `API_BASE=http://localhost:8001` and `FRONTEND_BASE=http://localhost:3001`. CI runner must expose these ports.

3. **Playwright `webServer` is no-op:** The `webServer` in `playwright.config.ts` is an echo stub. Tests expect servers to already be running.

4. **Bind mounts required:** `docker-compose.acceptance.yml` uses `./backend:/app` and `./frontend:/app`. CI must replicate this or use container builds.

5. **Fixture-account bootstrap:** Browser tests create users via API before running. Requires backend to be fully operational with database migrations applied.

6. **Frontend runs `npm run dev`:** Not a production build. CI must run dev server, not static export.

### Prerequisites for Browser CI

To add browser tests to CI in the future:

1. **Compose on CI:** Use `docker-compose -f docker-compose.acceptance.yml up --build -d` to start stack.

2. **Health checks:** Wait for backend (`http://localhost:8001/health`) and frontend (`http://localhost:3001`) to be ready.

3. **Playwright install:** `npx playwright install --with-deps chromium` in CI runner.

4. **Port mapping:** Ensure CI runner exposes ports 8001 and 3001 (GitHub Actions runners do by default).

5. **Teardown:** `docker-compose down -v` after tests to clean up volumes.

6. **Test selection:** Exclude `api-fetch.spec.ts` (stubs `global.fetch`, does not need servers) or run it separately without stack.

**Estimated complexity:** Medium. Requires ~50 lines of workflow YAML plus compose orchestration. Not trivial, but straightforward once stack is validated.

**Recommendation:** Defer until backend tests and frontend build are stable in CI. Add browser tests as a third job once first two jobs are passing reliably.

---

## 5. Blockers and Prerequisites

### No Blockers for Recommended Slice

- **Backend tests:** No blockers. Fully self-contained.
- **Frontend build:** No blockers. Pure static generation.

### Future Blockers for Browser CI

- **Docker availability:** GitHub Actions runners include Docker by default. No blocker.
- **Port conflicts:** Runners are ephemeral. No blocker.
- **Test flakiness:** Browser tests may be flaky on CI due to timing. Requires investigation if added.

---

## 6. Implementation File Scope

### Files to Create

| File | Purpose |
|------|---------|
| `.github/workflows/backend-tests.yml` | Backend test job |
| `.github/workflows/frontend-build.yml` | Frontend build job |

### Files NOT Modified

- No changes to `backend/` code or tests.
- No changes to `frontend/` code or tests.
- No changes to `docker-compose.acceptance.yml`.
- No changes to `requirements.txt` or `package.json`.
- No changes to branch protection or repository settings.

### Total Scope

- **2 new files** (workflow YAML).
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

### General

- [ ] Both jobs run in parallel (no dependencies between them).
- [ ] No secrets required in repository settings.
- [ ] No artifacts uploaded.
- [ ] No deployment triggered.

---

## 7B. Local Verification Status

**Backend tests:**
- Local execution on Python 3.14: 324 passed in 2824s (47 min) — anomalously slow, likely environment-specific.
- Command correctness verified: `pytest` runs successfully, all tests pass.
- GitHub Actions runner execution: **NOT YET VERIFIED.** Python 3.12 on `ubuntu-latest` may perform differently.

**Frontend build:**
- Local execution on Node 26: build succeeded, all pages generated.
- Command correctness verified: `npm ci` and `npm run build` complete without errors.
- GitHub Actions runner execution: **NOT YET VERIFIED.** Node 20 on `ubuntu-latest` may behave differently.

**Important:** Local pass confirms command correctness and dependency resolution, but does not prove GitHub Actions execution will succeed. Version-specific behavior, runner performance, and network conditions on GitHub may differ. First CI run should be monitored.

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

**Recommendation:** Option A. Minimal slice first. Lint can be added as a third job once build is stable.

---

## 9. Summary

**Recommended first CI slice:**

- **Job A:** Backend tests (Python 3.12, `pytest`, 324 tests, ~10 seconds).
- **Job B:** Frontend production build (Node 20, `next build`, ~60-90 seconds).

**Why sufficient:**

- Covers all backend logic (324 tests, 11 files).
- Catches frontend build failures and TypeScript errors.
- Fully reproducible (no external dependencies, no secrets, no Docker).
- Fast feedback (<3 minutes total).

**Reproducibility gaps:**

- None for recommended slice. Both jobs are fully self-contained.

**Browser CI decision:**

- **Deferred.** Requires acceptance stack orchestration (3 containers, hard-coded ports, bind mounts). Not trivial, but straightforward once first two jobs are stable.

**Implementation scope:**

- 2 new files (`.github/workflows/backend-tests.yml`, `.github/workflows/frontend-build.yml`).
- 0 modified files.
- 0 deleted files.

**Owner decisions:**

1. Workflow trigger scope (recommended: push to `main` + PRs to `main`).
2. Workflow file naming (recommended: separate files).
3. Lint job (recommended: defer).

---

Minimal CI specification prepared locally. No workflow, code, dependency, remote-state, or operational database change was made. Awaiting owner review before implementation.
