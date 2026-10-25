#!/bin/bash
# Bring up the disposable research-flow E2E runtime and seed it.
#
# Task-owned resources (all removed by stop_e2e_research_flow.sh):
#   - compose project `alethra-e2e`: containers alethra-e2e-{db,backend}-1 + ephemeral volume
#   - host process: `next dev` on 127.0.0.1:3099 (pid recorded in .local/e2e/frontend.pid)
#   - local files: .local/e2e-credentials.env (mode 600), .local/e2e/*.log
#
# Never touches the review stack (project alethra-unified-dev, DB alethra_unified_browser).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ENV_FILE=".local/e2e-credentials.env"
LOG_DIR=".local/e2e"
COMPOSE="docker compose -p alethra-e2e -f docker-compose.e2e.yml --env-file $ENV_FILE"
FRONTEND_PORT=3099
BACKEND_PORT=8099
FREE_DB_BUILD_MIB=10240   # 10 GiB operational line: below it, no image builds

fail() { echo "E2E SETUP ABORTED: $*" >&2; exit 1; }

# --- idempotent re-run: tear down a previous E2E run of this same task ---
if [ -f ".local/e2e-credentials.env" ] || [ -f ".local/e2e/frontend.pid" ]; then
  echo "previous E2E runtime detected - stopping it first (task-owned resources only)"
  ./stop_e2e_research_flow.sh >/dev/null 2>&1 || echo "note: stop script reported an issue; continuing with a fresh start"
fi

# --- preflight: ports must be unused, nothing shared gets clobbered ---
for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "loopback port $port is already in use - free it or edit the port in this script"
  fi
done

# --- preflight: disk line + no image build unless space allows ---
avail_mib=$(/bin/df -km / | awk 'NR==2 {print $4}')
echo "free space: ${avail_mib} MiB (image-build stop line: ${FREE_DB_BUILD_MIB} MiB)"
if ! docker image inspect alethra-unified-dev/backend:local >/dev/null 2>&1; then
  if [ "$avail_mib" -lt "$FREE_DB_BUILD_MIB" ]; then
    fail "backend image absent and free space below the 10 GiB build line"
  fi
  echo "backend image absent - building it (single build, no concurrent builds)"
  docker build -t alethra-unified-dev/backend:local -f backend/Dockerfile backend
fi

# --- credentials: generated once, 600, never echoed and never in argv ---
mkdir -p .local "$LOG_DIR"
if [ -f "$ENV_FILE" ]; then
  echo "reusing existing $ENV_FILE"
else
  echo "generating synthetic E2E credentials into $ENV_FILE"
  umask 077
  {
    printf 'E2E_POSTGRES_USER=alethra_e2e\n'
    printf 'E2E_POSTGRES_DB=alethra_e2e\n'
    printf 'E2E_POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'E2E_JWT_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
    printf 'E2E_EMAIL=e2e.research-flow@alethra.io\n'
    printf 'E2E_PASSWORD=%s\n' "$(openssl rand -hex 16)"
  } >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
[ "$(stat -f %Lp "$ENV_FILE")" = "600" ] || fail "$ENV_FILE must be mode 600"

# --- database + backend (fresh, empty volume every run) ---
$COMPOSE down --remove-orphans --volumes >/dev/null 2>&1 || true
$COMPOSE up -d db
echo "waiting for db..."
health=""
for _ in $(seq 1 60); do
  health=$(docker inspect --format '{{.State.Health.Status}}' alethra-e2e-db-1 2>/dev/null || true)
  [ "$health" = "healthy" ] && break
  sleep 2
done
[ "$health" = "healthy" ] || fail "db did not become healthy"

# `alembic upgrade head` runs as the backend entrypoint against the empty
# disposable database; the log is kept as fresh-upgrade migration evidence.
$COMPOSE up -d backend
echo "waiting for backend on 127.0.0.1:$BACKEND_PORT (alembic upgrade head runs first)..."
ready=""
for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then ready=yes; break; fi
  sleep 2
done
[ -n "$ready" ] || fail "backend did not answer /health within 180s"
$COMPOSE logs --no-color backend >"$LOG_DIR/backend-upgrade.log" 2>&1
grep -q "Running upgrade 8616c0c9aa3e -> h8i9j0k1l2m3" "$LOG_DIR/backend-upgrade.log" \
  || fail "expected migration head not observed in $LOG_DIR/backend-upgrade.log"
echo "migrations applied to head h8i9j0k1l2m3 (log: $LOG_DIR/backend-upgrade.log)"

# --- deterministic synthetic seed (guarded to the alethra_e2e database) ---
$COMPOSE exec -T backend python scripts/seed_e2e_research_flow.py | tee "$LOG_DIR/seed.log"

# --- frontend: host dev server on the working tree, no image build ---
[ -d frontend/node_modules ] || fail "frontend/node_modules missing - run npm ci in frontend first"
(
  cd frontend
  NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" \
    nohup npx next dev -p "$FRONTEND_PORT" >"../$LOG_DIR/frontend-dev.log" 2>&1 &
  echo $! >"../$LOG_DIR/frontend.pid"
)
echo "waiting for frontend on 127.0.0.1:$FRONTEND_PORT..."
ready=""
for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$FRONTEND_PORT/login" >/dev/null 2>&1; then ready=yes; break; fi
  sleep 2
done
[ -n "$ready" ] || fail "frontend dev server did not answer /login within 180s"

echo
echo "E2E research-flow runtime ready:"
echo "  frontend : http://localhost:$FRONTEND_PORT"
echo "  backend  : http://localhost:$BACKEND_PORT"
echo "  run spec : cd frontend && E2E_FRONTEND_BASE=http://localhost:$FRONTEND_PORT \\"
echo "             E2E_API_BASE=http://localhost:$BACKEND_PORT E2E_CRED_FILE=../$ENV_FILE \\"
echo "             npx playwright test --config playwright.research-flow.config.ts"
echo "  teardown : ./stop_e2e_research_flow.sh"
