#!/bin/bash
# Bring up the disposable research-flow E2E runtime and seed it.
#
# Task-owned resources (all removed by stop_e2e_research_flow.sh):
#   - compose project `$E2E_COMPOSE_PROJECT`: containers <project>-{db,backend}-1 + ephemeral volume
#   - backend image `$E2E_COMPOSE_PROJECT/backend:local`, built here from committed files
#   - host process: `next dev` on 127.0.0.1:$E2E_FRONTEND_PORT (pid recorded in $E2E_LOG_DIR/frontend.pid)
#   - local files: $E2E_ENV_FILE (mode 600), $E2E_LOG_DIR/*.log
#
# Self-contained first run: nothing here assumes an application image already
# exists on the host. The backend image is built from the committed
# docker-compose.e2e.yml definition when needed (Docker layer cache reuse only).
#
# Defaults match the ports and project of the original run; override to run a
# second, fully isolated instance from another checkout:
#   E2E_COMPOSE_PROJECT E2E_FRONTEND_PORT E2E_BACKEND_PORT E2E_POSTGRES_DB
#   E2E_ENV_FILE E2E_LOG_DIR
#
# Never touches the review stack (project alethra-unified-dev, DB alethra_unified_browser).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PROJECT="${E2E_COMPOSE_PROJECT:-alethra-e2e}"
ENV_FILE="${E2E_ENV_FILE:-.local/e2e-credentials.env}"
LOG_DIR="${E2E_LOG_DIR:-.local/e2e}"
FRONTEND_PORT="${E2E_FRONTEND_PORT:-3099}"
BACKEND_PORT="${E2E_BACKEND_PORT:-8099}"
BACKEND_DB="${E2E_POSTGRES_DB:-alethra_e2e}"
IMAGE="$PROJECT/backend:local"
COMPOSE="docker compose -p $PROJECT -f docker-compose.e2e.yml --env-file $ENV_FILE"
FREE_DB_BUILD_MIB=10240   # 10 GiB operational line: below it, no image builds

fail() { echo "E2E SETUP ABORTED: $*" >&2; exit 1; }

case "$BACKEND_DB" in
  alethra_e2e*) ;;
  *) fail "E2E_POSTGRES_DB must start with 'alethra_e2e' (the seed script's guard)" ;;
esac

export E2E_BACKEND_IMAGE="$IMAGE"
export E2E_BACKEND_PORT="$BACKEND_PORT"
export E2E_FRONTEND_PORT="$FRONTEND_PORT"

# --- credentials: generated once, 600, never echoed and never in argv ---
mkdir -p "$(dirname "$ENV_FILE")" "$LOG_DIR"
if [ -f "$ENV_FILE" ]; then
  echo "reusing existing $ENV_FILE"
else
  echo "generating synthetic E2E credentials into $ENV_FILE"
  umask 077
  {
    printf 'E2E_POSTGRES_USER=alethra_e2e\n'
    printf 'E2E_POSTGRES_DB=%s\n' "$BACKEND_DB"
    printf 'E2E_POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'E2E_JWT_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
    printf 'E2E_EMAIL=e2e.research-flow@alethra.io\n'
    printf 'E2E_PASSWORD=%s\n' "$(openssl rand -hex 16)"
  } >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
[ "$(stat -f %Lp "$ENV_FILE")" = "600" ] || fail "$ENV_FILE must be mode 600"

# --- idempotent re-run: tear down a previous run of this same task ---
if [ -f "$ENV_FILE" ] && { [ -f "$LOG_DIR/frontend.pid" ] || docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" | grep -q .; }; then
  echo "previous E2E runtime detected - stopping it first (task-owned resources only)"
  E2E_COMPOSE_PROJECT="$PROJECT" E2E_ENV_FILE="$ENV_FILE" E2E_LOG_DIR="$LOG_DIR" \
  E2E_FRONTEND_PORT="$FRONTEND_PORT" E2E_BACKEND_PORT="$BACKEND_PORT" \
    ./stop_e2e_research_flow.sh >/dev/null 2>&1 || echo "note: stop script reported an issue; continuing with a fresh start"
fi

# --- preflight: ports must be unused, nothing shared gets clobbered ---
for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "loopback port $port is already in use - free it or override E2E_FRONTEND_PORT / E2E_BACKEND_PORT"
  fi
done

# --- preflight: disk line, then build this project's own backend image ---
avail_mib=$(/bin/df -km / | awk 'NR==2 {print $4}')
echo "free space: ${avail_mib} MiB (image-build stop line: ${FREE_DB_BUILD_MIB} MiB)"
if [ "$avail_mib" -lt "$FREE_DB_BUILD_MIB" ]; then
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "below the build line but $IMAGE already present - reusing it, no build"
  else
    fail "$IMAGE absent and free space below the 10 GiB build line"
  fi
else
  echo "building $IMAGE from committed files (single build, no concurrent builds)"
  $COMPOSE build backend
fi

# --- database + backend (fresh, empty volume every run) ---
$COMPOSE down --remove-orphans --volumes >/dev/null 2>&1 || true
$COMPOSE up -d db
echo "waiting for db..."
db_id="$($COMPOSE ps -q db)"
[ -n "$db_id" ] || fail "compose did not report a db container for project $PROJECT"
health=""
for _ in $(seq 1 60); do
  health=$(docker inspect --format '{{.State.Health.Status}}' "$db_id" 2>/dev/null || true)
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
LOG_DIR_ABS="$(cd "$LOG_DIR" && pwd)"
ENV_FILE_ABS="$ROOT/$ENV_FILE"
[ -f "$ENV_FILE" ] || fail "$ENV_FILE missing"
(
  cd frontend
  NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" \
    nohup npx next dev -p "$FRONTEND_PORT" >"$LOG_DIR_ABS/frontend-dev.log" 2>&1 &
  echo $! >"$LOG_DIR_ABS/frontend.pid"
)
echo "waiting for frontend on 127.0.0.1:$FRONTEND_PORT..."
ready=""
for _ in $(seq 1 90); do
  if curl -sf "http://127.0.0.1:$FRONTEND_PORT/login" >/dev/null 2>&1; then ready=yes; break; fi
  sleep 2
done
[ -n "$ready" ] || fail "frontend dev server did not answer /login within 180s"

echo
echo "E2E research-flow runtime ready (project $PROJECT):"
echo "  frontend : http://localhost:$FRONTEND_PORT   (host next dev = development server, not a production build)"
echo "  backend  : http://localhost:$BACKEND_PORT"
echo "  run spec : cd frontend && E2E_FRONTEND_BASE=http://localhost:$FRONTEND_PORT \\"
echo "             E2E_CRED_FILE=$ENV_FILE_ABS \\"
echo "             npx playwright test --config playwright.research-flow.config.ts"
echo "  teardown : E2E_COMPOSE_PROJECT=$PROJECT E2E_ENV_FILE=$ENV_FILE E2E_LOG_DIR=$LOG_DIR \\"
echo "             E2E_FRONTEND_PORT=$FRONTEND_PORT E2E_BACKEND_PORT=$BACKEND_PORT ./stop_e2e_research_flow.sh"
