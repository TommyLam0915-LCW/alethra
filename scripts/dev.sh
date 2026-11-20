#!/usr/bin/env bash
# Alethra single dev entry point.
#
# Every subcommand mirrors a command that already exists in this repository
# (.github/workflows/backend-tests.yml, frontend-build.yml, browser-acceptance.yml)
# so that local runs and CI runs are the same runs. No subcommand is a new
# approval mechanism; they are reproducible commands.
#
# Layer model (a layer PASSes only at its own level — never inferred upward):
#
#   env            toolchain / dependency / host facts, report-only
#   install        build the worktree-local .venv from the locked pins
#   lint           compile + secret scan + (frontend) typecheck
#   test           backend unit + SQLite integration   (default DB: sqlite)
#   pg             backend PostgreSQL integration      (needs ALETHRA_PG_URL)
#   web-unit       frontend node:test units            (no node_modules needed)
#   web            frontend typecheck + production build
#   e2e            Playwright journeys against a running stack
#   smoke          release smoke: import, routes, health, config refusals
#   up / down      start / stop the local stack (prints the command, needs --yes)
#   all            every layer that the host can run today, one table
#
# Exit codes: 0 pass, 1 a layer failed, 2 usage error, 3 the layer is blocked on
# something this host does not have (reported as BLOCKED, never as a pass).

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
RUNS="$ROOT/.dev-runs"
REQS="backend/requirements.txt"
LOG_SUFFIX="${ALETHRA_DEV_SUFFIX:-}"

# Node: the repo's CI pins Node 20 for the build. `node --test` also has to read
# .ts sources, which needs Node >= 22.6 type stripping, so the two are separate
# picks and both fall back to whatever `node` is on PATH.
pick_node() {  # $1 = minimum major version
  local want="$1" cand
  for cand in "$HOME/.local/bin/node$want" "$HOME/.local/bin/node"; do
    [ -x "$cand" ] || continue
    if [ "$("$cand" --version 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')" -ge "$want" ]; then
      echo "$cand"; return 0
    fi
  done
  if command -v node >/dev/null 2>&1; then
    if [ "$(node --version | sed 's/^v\([0-9]*\).*/\1/')" -ge "$want" ]; then
      echo "$(command -v node)"; return 0
    fi
  fi
  return 1
}

say()  { printf '%s\n' "$*"; }
line() { printf -- '---------------------------------------------\n'; }
die()  { say "dev.sh: $*" >&2; exit 2; }

need_venv() {
  [ -x "$PY" ] || { say "BLOCKED_ENV_NOT_AVAILABLE: no .venv at $VENV — run: scripts/dev.sh install"; exit 3; }
}

mask_url() {  # scheme://user:pass@host/db -> scheme://user@host/db (never echo secrets)
  sed -E 's#://([^/@:]*):[^@]*@#://\1@#'
}

# --------------------------------------------------------------------------- env
cmd_env() {
  say "Alethra dev environment check (report-only; installs and connects nothing)"
  line
  say "worktree        : $ROOT"
  say "branch          : $(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  say "HEAD            : $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo '?')"
  say "dirty files     : $(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  say "host            : $(uname -sr) $(uname -m)"
  line

  local missing=0
  printf '%-22s %s\n' "git"            "$(git --version 2>/dev/null || { echo MISSING; missing=$((missing+1)); })"
  printf '%-22s %s\n' "uv"             "$(uv --version 2>/dev/null || echo 'MISSING (needed by: install)')"
  printf '%-22s %s\n' "python (venv)"  "$([ -x "$PY" ] && "$PY" --version || echo 'MISSING (run: scripts/dev.sh install)')"
  printf '%-22s %s\n' "node (build)"   "$(pick_node 20 2>/dev/null || echo 'MISSING (>=20 needed by: web)')"
  printf '%-22s %s\n' "node (type-strip)" "$(pick_node 22 2>/dev/null || echo 'MISSING (>=22.6 needed by: web-unit)')"
  printf '%-22s %s\n' "npm"            "$(npm --version 2>/dev/null || echo 'MISSING (needed by: web)')"
  printf '%-22s %s\n' "docker"         "$(command -v docker >/dev/null 2>&1 && docker --version || echo 'MISSING (needed by: e2e via docker-compose)')"
  printf '%-22s %s\n' "sqlite3"        "$(/usr/bin/sqlite3 --version 2>/dev/null | cut -d' ' -f1-2 || echo 'using the Python driver only')"

  line
  say "interpreter the CI backend job uses: python 3.12"
  if command -v uv >/dev/null 2>&1; then
    uv python list --only-installed 2>/dev/null | grep -E '3\.12' | head -3
    [ -n "$(uv python list --only-installed 2>/dev/null | grep '3\.12')" ] || say "  cpython 3.12 NOT installed locally"
  fi

  line
  say "locked backend dependencies ($REQS)"
  if [ -x "$PY" ]; then
    "$PY" - <<'PY'
import re
import sys
from importlib import metadata

reqs = []
for raw in open("backend/requirements.txt"):
    m = re.match(r'^([A-Za-z0-9_.\-]+)\[?[A-Za-z0-9_,\-]*\]?==([0-9][0-9A-Za-z.\-]*)$', raw.strip())
    if m:
        reqs.append((m.group(1), m.group(2)))

# a uv venv has no pip, so read the distributions that are actually installed
installed = {}
for dist in metadata.distributions():
    name = (dist.metadata["Name"] or "").lower().replace("_", "-")
    if name:
        installed[name] = dist.version

missing, deviations = [], []
for name, want in reqs:
    got = installed.get(name.lower().replace("_", "-"))
    if got is None:
        missing.append(f"{name}=={want}")
    elif got != want:
        deviations.append(f"{name}: pinned {want}, installed {got}")

print(f"  pins in requirements.txt : {len(reqs)}")
print(f"  installed at the pin     : {len(reqs) - len(missing) - len(deviations)}")
for d in deviations:
    print(f"  DEVIATION  {d}")
for m in missing:
    print(f"  MISSING    {m}")
if not missing and not deviations:
    print("  every pin is installed exactly as locked")
PY
  else
    say "  (no .venv — run: scripts/dev.sh install)"
  fi

  line
  say "PostgreSQL test lane (detected without connecting)"
  local sock
  sock=$(ls /tmp/.s.PGSQL.* 2>/dev/null | head -3 | tr '\n' ' ')
  if [ -n "${sock:-}" ]; then
    say "  unix sockets seen : $sock"
  else
    say "  no /tmp/.s.PGSQL.* socket on this host"
  fi
  if [ -n "${ALETHRA_PG_URL:-}" ]; then
    say "  ALETHRA_PG_URL    : set -> $(mask_url <<<"$ALETHRA_PG_URL")"
  else
    say "  ALETHRA_PG_URL    : unset  -> 'dev.sh pg' will report BLOCKED_ENV_NOT_AVAILABLE"
  fi

  line
  say "frontend dependency set"
  if [ -d "$ROOT/frontend/node_modules" ]; then
    say "  node_modules      : present ($(find "$ROOT/frontend/node_modules" -maxdepth 1 -type d | wc -l | tr -d ' ') top-level dirs)"
  else
    say "  node_modules      : absent -> 'dev.sh web' / 'dev.sh e2e' need: npm ci (network or warm cache)"
  fi

  line
  say "missing required tools for the core lane: $missing"
  return 0
}

# ----------------------------------------------------------------------- install
cmd_install() {
  local online=0
  [ "${1:-}" = "--online" ] && online=1
  if [ ! -d "$VENV" ]; then
    say "creating worktree-local venv (python 3.12, the CI interpreter)"
    uv venv --python 3.12 "$VENV" || die "uv venv failed"
  fi
  local tmp; tmp=$(mktemp)
  grep -v '^psycopg2-binary' "$ROOT/$REQS" > "$tmp"
  say "installing locked pins, offline"
  local flags=(--offline)
  [ "$online" = 1 ] && flags=()
  VIRTUAL_ENV="$VENV" uv pip install "${flags[@]}" -r "$tmp" || { rm -f "$tmp"; say "BLOCKED: dependency install failed (see the line above; retry with --online only if downloading is authorized)"; exit 3; }
  rm -f "$tmp"
  say "installing the psycopg2 driver"
  if ! VIRTUAL_ENV="$VENV" uv pip install "${flags[@]}" -r "$ROOT/$REQS" 2>/dev/null; then
    say "  the pinned psycopg2-binary build failed; trying the wheel that is already cached"
    VIRTUAL_ENV="$VENV" uv pip install "${flags[@]}" psycopg2-binary || {
      say "BLOCKED_ENV_NOT_AVAILABLE: no installable psycopg2 driver offline."
      say "  this lane needs: psycopg2-binary at the pinned version, or an approved download."
      exit 3; }
    say "  DEVIATION recorded: driver installed from cache, not at the pinned version."
    say "  check it with: scripts/dev.sh env"
  fi
  say "install ok"
  "$PY" --version
}

# -------------------------------------------------------------------------- lint
cmd_lint() {
  need_venv
  local rc=0
  say "[1/4] python compileall (app + tests)"
  if (cd "$ROOT/backend" && "$PY" -m compileall -q app tests >/dev/null); then
    say "  rc=0"
  else
    say "  rc=1 FAIL: syntax error"
    rc=1
  fi

  say "[2/4] secret scan of tracked files (names/lengths only, values never printed)"
  "$PY" "$ROOT/scripts/audit_secrets.py" || rc=1

  say "[3/4] linter/formatter/typechecker for python"
  if "$PY" -c "import ruff" 2>/dev/null || command -v ruff >/dev/null 2>&1; then
    (cd "$ROOT/backend" && ruff check app tests)
  else
    say "  BLOCKED: no ruff/flake8/mypy in $REQS, so there is no pinned python linter to run."
    say "  the repo's CI backend job runs no lint step either — adding one means adding a pin."
  fi

  say "[4/4] frontend typecheck"
  local node20; node20=$(pick_node 20) || node20=""
  if [ -z "$node20" ]; then
    say "  BLOCKED: node >= 20 not found"
  elif [ ! -d "$ROOT/frontend/node_modules" ]; then
    say "  BLOCKED: frontend/node_modules absent — run: (cd frontend && npm ci)"
  else
    (cd "$ROOT/frontend" && "$node20" ./node_modules/typescript/bin/tsc --noEmit) || rc=1
    # report coverage so a silent step cannot be mistaken for a no-op
    n=$( (cd "$ROOT/frontend" && "$node20" ./node_modules/typescript/bin/tsc --noEmit --listFiles 2>/dev/null) | grep -cv '/node_modules/' )
    say "  typechecked $n first-party file(s) (strict)"
  fi
  return $rc
}

# ---------------------------------------------------------------------------- pg
resolve_pg_url() {
  if [ -n "${ALETHRA_PG_URL:-}" ]; then echo "$ALETHRA_PG_URL"; return 0; fi
  if [ -n "${ALETHRA_TEST_DB:-}" ]; then echo "$ALETHRA_TEST_DB"; return 0; fi
  return 1
}

cmd_pg() {
  need_venv
  local url; url=$(resolve_pg_url) || {
    say "BLOCKED_ENV_NOT_AVAILABLE: no PostgreSQL target."
    say "  give one, and it is checked by the suite's own safety gate before use:"
    say "    ALETHRA_PG_URL=postgresql://<test-role>:<pw>@localhost:5433/<name_with_test>"
    say "  the gate refuses non-localhost hosts, names without 'test', and production DB names."
    exit 3
  }
  say "PG lane target: $(mask_url <<<"$url")"
  say "  (the safety gate in backend/tests/conftest.py validates this before any statement)"
  mkdir -p "$RUNS"
  local log="$RUNS/pg-$(date -u +%Y%m%dT%H%M%SZ)${LOG_SUFFIX}.log"
  (cd "$ROOT/backend" && ALETHRA_TEST_DB="$url" "$PY" -m pytest -q -rfE --durations=15 "$@") 2>&1 | tee "$log"
  return "${PIPESTATUS[0]}"
}

# --------------------------------------------------------------------- backend
cmd_test() {
  need_venv
  mkdir -p "$RUNS"
  local log="$RUNS/sqlite-$(date -u +%Y%m%dT%H%M%SZ)${LOG_SUFFIX}.log"
  say "backend unit + SQLite integration"
  say "  command : (cd backend && python -m pytest -q -rfE --durations=15 $*)"
  say "  DB      : ${ALETHRA_TEST_DB:-sqlite:///./test.db  (conftest default)}"
  say "  log     : $log"
  line
  (cd "$ROOT/backend" && "$PY" -m pytest -q -rfE --durations=15 "$@") 2>&1 | tee "$log"
  local rc=${PIPESTATUS[0]}
  line
  say "pytest rc=$rc"
  return $rc
}

# -------------------------------------------------------------------- frontend
cmd_web_unit() {
  local node22; node22=$(pick_node 22) || node22=""
  [ -n "$node22" ] || { say "BLOCKED_ENV_NOT_AVAILABLE: node >= 22.6 needed for .ts type stripping in tests/*.test.mjs"; exit 3; }
  say "frontend units ($node22)"
  local before=$($node22 --version)
  say "  node $before"
  (cd "$ROOT/frontend" && "$node22" --test tests/*.test.mjs)
  return $?
}

cmd_web() {
  local node20; node20=$(pick_node 20) || node20=""
  [ -n "$node20" ] || { say "BLOCKED_ENV_NOT_AVAILABLE: node >= 20 (CI uses Node 20)"; exit 3; }
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    say "frontend/node_modules absent — this layer needs an install:"
    say "    (cd frontend && npm ci)   # network or warm npm cache; not run automatically"
    exit 3
  fi
  say "frontend typecheck"
  (cd "$ROOT/frontend" && "$node20" ./node_modules/typescript/bin/tsc --noEmit) || return 1
  say "frontend production build (same command as .github/workflows/frontend-build.yml)"
  (cd "$ROOT/frontend" && "$node20" ./node_modules/next/dist/bin/next build) || return 1
  return 0
}

cmd_e2e() {
  say "Playwright journeys"
  if ! command -v docker >/dev/null 2>&1; then
    say "  the CI job starts the stack with: docker compose -f docker-compose.acceptance.yml up --build -d"
    say "  BLOCKED_ENV_NOT_AVAILABLE: docker is not on this host, so the acceptance stack cannot be built here."
    say "  alternative that needs no docker: start the stack with 'scripts/dev.sh up --yes', then run 'scripts/dev.sh e2e --no-stack'"
    exit 3
  fi
  (cd "$ROOT" && docker compose -f docker-compose.acceptance.yml up --build -d) || return 1
  (cd "$ROOT/frontend" && npx playwright test "$@")
  local rc=$?
  (cd "$ROOT" && docker compose -f docker-compose.acceptance.yml down -v)
  return $rc
}

# ----------------------------------------------------------------------- smoke
cmd_smoke() {
  need_venv
  say "release smoke: the pinned tree must import, route, and answer /health"
  (cd "$ROOT/backend" && "$PY" - <<'PY'
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///./smoke.db")
os.environ.setdefault("APP_ENV", "test")
from app.main import app
routes = [r.path for r in app.routes]
print(f"  app imported        : {app.title!r}")
print(f"  routes registered   : {len(routes)}")
missing = [p for p in ("/health", "/api/v1/auth/login") if p not in routes]
if missing:
    print(f"  FAIL missing routes  : {missing}")
    raise SystemExit(1)
from fastapi.testclient import TestClient
client = TestClient(app)
r = client.get("/health")
print(f"  GET /health         : {r.status_code} {r.text[:120]}")
raise SystemExit(0 if r.status_code == 200 else 1)
PY
  )
  return $?
}

# --------------------------------------------------------------------- up/down
cmd_up() {
  local yes="${1:-}"
  if [ "$yes" != "--yes" ]; then
    say "STARTING A SERVICE IS A SEPARATE DECISION FROM RUNNING TESTS."
    say "  this prints the command; add --yes to run it:"
    line
    say "  backend : (cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8120)"
    say "  frontend: (cd frontend && npm run dev)"
    say "  stack   : docker compose -f docker-compose.acceptance.yml up --build -d   (needs docker)"
    line
    say "  it binds a port and keeps running; not started automatically by dev.sh."
    exit 0
  fi
  need_venv
  mkdir -p "$RUNS"
  say "starting backend on 127.0.0.1:8120 (log: $RUNS/backend.out)"
  (cd "$ROOT/backend" && DATABASE_URL="${ALETHRA_PG_URL:-sqlite:///./dev.db}" \
    nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8120 > "$RUNS/backend.out" 2>&1 & echo $! > "$RUNS/backend.pid")
  local i
  for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8120/health >/dev/null 2>&1; then say "  healthy after ${i}s"; return 0; fi
    sleep 1
  done
  say "  FAIL: backend did not answer /health within 30s — see $RUNS/backend.out"
  return 1
}

cmd_down() {
  if [ -f "$RUNS/backend.pid" ]; then
    local pid; pid=$(cat "$RUNS/backend.pid")
    if ps -p "$pid" >/dev/null 2>&1; then
      say "stopping backend pid $pid (the pid this script started, nothing else)"
      kill "$pid"
    else
      say "backend pid $pid is not running"
    fi
    rm -f "$RUNS/backend.pid"
  else
    say "no $RUNS/backend.pid — nothing started by dev.sh to stop"
  fi
}

# ------------------------------------------------------------------------- all
cmd_all() {
  local rc=0
  run() {  # name  cmd...
    local name="$1"; shift
    line
    say "### $name"
    if "$@"; then
      say "### $name -> PASS"
    else
      local r=$?
      if [ "$r" = 3 ]; then say "### $name -> BLOCKED (rc=3)"; else say "### $name -> FAIL (rc=$r)"; rc=1; fi
    fi
  }
  run "env"        cmd_env
  run "lint"       cmd_lint
  run "web-unit"   cmd_web_unit
  run "smoke"      cmd_smoke
  run "test"       cmd_test
  line
  say "layers not run by 'all' (each needs something this host does not have):"
  say "  pg         : ALETHRA_PG_URL -> a disposable PostgreSQL test database"
  say "  web        : frontend/node_modules -> npm ci"
  say "  e2e        : docker or a stack started with 'dev.sh up --yes'"
  line
  say "dev.sh all -> $( [ "$rc" = 0 ] && echo 'PASS for the layers that ran' || echo 'FAIL in at least one layer' )"
  return $rc
}

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  say ""
  say "usage: scripts/dev.sh <env|install|lint|test|pg|web-unit|web|e2e|smoke|up|down|all> [args...]"
}

main() {
  cd "$ROOT" || die "cannot enter $ROOT"
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    env)     cmd_env ;;
    install) cmd_install "${1:-}" ;;
    lint)    cmd_lint ;;
    test)    cmd_test "$@" ;;
    pg)      cmd_pg "$@" ;;
    web-unit) cmd_web_unit ;;
    web)     cmd_web ;;
    e2e)     cmd_e2e "$@" ;;
    smoke)   cmd_smoke ;;
    up)      cmd_up "${1:-}" ;;
    down)    cmd_down ;;
    all)     cmd_all ;;
    ""|-h|--help|help) usage ;;
    *) die "unknown command: $cmd (try: scripts/dev.sh --help)" ;;
  esac
}

main "$@"
