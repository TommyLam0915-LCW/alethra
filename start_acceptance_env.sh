#!/bin/bash
# Start a docker-backed acceptance stack (PostgreSQL container + backend + built
# frontend). Its pair is ./stop_acceptance_env.sh. docs/deployment.md is about the
# docker-compose production stack, not this one; this file pair is self-contained.
#
# Everything this script needs is either exported by the caller or derived from
# the directory the script lives in: no credential, no absolute path and no
# pre-existing database is assumed. Anything missing is refused BEFORE any
# container or process is created, so a refusal never leaves a half-built
# acceptance environment behind for the next person to mistake for theirs.
#
# Exit codes: 0 started, 2 refused before touching anything, 3 started but a
# readiness gate did not pass (the stack is not usable; ./stop_acceptance_env.sh
# cleans it up).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT="${ACCEPTANCE_BACKEND_PORT:-8001}"
FRONTEND_PORT="${ACCEPTANCE_FRONTEND_PORT:-3001}"
DB_PORT="${ACCEPTANCE_DB_PORT:-5433}"
DB_NAME="${ACCEPTANCE_DB_NAME:-ai_investment_lab_acceptance}"
CONTAINER="${ACCEPTANCE_CONTAINER_NAME:-alethra-acceptance-db}"
LOG_DIR="${ACCEPTANCE_LOG_DIR:-/tmp}"

refuse() {
  echo "REFUSED (nothing was started): $*" >&2
  exit 2
}

echo "=== Starting Alethra Acceptance Environment ==="

command -v docker >/dev/null 2>&1 ||
  refuse "docker is not on PATH; this stack is defined by docker-compose and PostgreSQL runs in a container"
[ -d "$BACKEND_DIR" ] || refuse "no backend directory at $BACKEND_DIR"
[ -d "$FRONTEND_DIR/node_modules" ] ||
  refuse "frontend/node_modules missing - run npm ci in $FRONTEND_DIR first"

# Both the container and the migration have to agree on the same password, so
# it is read once from the caller and used for both. Nothing is embedded here:
# a value committed into the repo is a value anyone with the repo can use.
[ -n "${POSTGRES_PASSWORD:-}" ] ||
  refuse "POSTGRES_PASSWORD is not exported; it is the password the container is created WITH, so it cannot be invented per step"
[ -n "${JWT_SECRET_KEY:-}" ] ||
  refuse "JWT_SECRET_KEY is not exported; this script embeds no signing key"

ACTIVATE=""
for candidate in "$BACKEND_DIR/.venv/bin/activate" "$BACKEND_DIR/venv/bin/activate"; do
  if [ -f "$candidate" ]; then ACTIVATE=$candidate; break; fi
done
[ -n "$ACTIVATE" ] ||
  refuse "no virtualenv under $BACKEND_DIR (.venv/bin/activate or venv/bin/activate)"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  refuse "container $CONTAINER already exists; this script does not recreate a database it did not create. Remove it explicitly if you own it"
fi

# --- PostgreSQL ---------------------------------------------------------------
echo "1. Starting PostgreSQL ($CONTAINER on 127.0.0.1:$DB_PORT)..."
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e POSTGRES_DB="$DB_NAME" \
  -p 127.0.0.1:"$DB_PORT":5432 \
  postgres:16-alpine >/dev/null

echo "Waiting for PostgreSQL to be ready..."
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U postgres -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec "$CONTAINER" pg_isready -U postgres -d "$DB_NAME" >/dev/null 2>&1 || {
  echo "NOT READY: PostgreSQL in $CONTAINER never accepted connections. Leaving the container for inspection; ./stop_acceptance_env.sh removes it." >&2
  exit 3
}

# --- migrations ---------------------------------------------------------------
echo "2. Running database migrations..."
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1090
  source "$ACTIVATE"
  export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${DB_PORT}/${DB_NAME}"
  export JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}"
  export AI_PROVIDER="${AI_PROVIDER:-mock}"
  export DATA_PROVIDER="${DATA_PROVIDER:-demo}"
  export APP_ENV="${APP_ENV:-development}"
  python -m alembic upgrade head
) || {
  echo "NOT READY: migrations failed; nothing else was started. ./stop_acceptance_env.sh removes the container." >&2
  exit 3
}

# --- backend ------------------------------------------------------------------
echo "3. Starting backend on 127.0.0.1:$BACKEND_PORT..."
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1090
  source "$ACTIVATE"
  export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:${DB_PORT}/${DB_NAME}"
  export JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}"
  export AI_PROVIDER="${AI_PROVIDER:-mock}"
  export DATA_PROVIDER="${DATA_PROVIDER:-demo}"
  export APP_ENV="${APP_ENV:-development}"
  nohup python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
    >"$LOG_DIR/alethra-backend.log" 2>&1 &
  echo $! >"$LOG_DIR/alethra-backend.pid"
)
BACKEND_PID=$(cat "$LOG_DIR/alethra-backend.pid")
echo "Backend started with PID $BACKEND_PID (logs: $LOG_DIR/alethra-backend.log)"

# --- frontend -----------------------------------------------------------------
echo "4. Building frontend for production..."
(cd "$FRONTEND_DIR" && NEXT_TELEMETRY_DISABLED=1 npm run build)

echo "5. Starting frontend on 127.0.0.1:$FRONTEND_PORT..."
(
  cd "$FRONTEND_DIR"
  NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_API_URL="http://127.0.0.1:$BACKEND_PORT" \
    nohup npm run start -- -H 127.0.0.1 -p "$FRONTEND_PORT" \
    >"$LOG_DIR/alethra-frontend.log" 2>&1 &
  echo $! >"$LOG_DIR/alethra-frontend.pid"
)
FRONTEND_PID=$(cat "$LOG_DIR/alethra-frontend.pid")
echo "Frontend started with PID $FRONTEND_PID (logs: $LOG_DIR/alethra-frontend.log)"

# Liveness is not readiness: the backend answering /health says nothing about the
# database, so /health/ready is the gate this stack is called ready by.
READY=""
for _ in $(seq 1 30); do
  if READY=$(curl -fsS "http://127.0.0.1:$BACKEND_PORT/health/ready" 2>/dev/null); then
    break
  fi
  sleep 2
done
if [ -z "${READY:-}" ]; then
  echo "NOT READY: backend did not answer http://127.0.0.1:$BACKEND_PORT/health/ready. The stack is NOT usable." >&2
  exit 3
fi

echo ""
echo "=== Acceptance Environment Ready ==="
echo "Backend:   http://127.0.0.1:$BACKEND_PORT  ($READY)"
echo "Frontend:  http://127.0.0.1:$FRONTEND_PORT"
echo "Database:  127.0.0.1:$DB_PORT/$DB_NAME in container $CONTAINER"
echo ""
echo "To stop: ./stop_acceptance_env.sh"
