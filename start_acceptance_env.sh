#!/usr/bin/env bash
# Starts the local acceptance stack: PostgreSQL on :5433, API on :8001, web on :3001.
#
# Every required input and precondition is checked before the first side effect.
# With a missing password this script exits without invoking docker, alembic, npm,
# uvicorn, or any service control.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

DB_NAME="ai_investment_lab_acceptance"
CONTAINER="alethra-acceptance-db"

die() {
  echo "REFUSING: $*" >&2
  echo "No container, migration, build, or server was started by this run." >&2
  exit 1
}

require_var() {
  local name="$1"
  [ -n "${!name:-}" ] || die "required environment variable ${name} is not set (or is empty)"
}

# ── 1. Required inputs ─────────────────────────────────────────────────────
require_var POSTGRES_PASSWORD
require_var JWT_SECRET_KEY

# Length only — the value itself is never printed.
if [ "${#JWT_SECRET_KEY}" -lt 32 ]; then
  die "JWT_SECRET_KEY must be at least 32 characters (got ${#JWT_SECRET_KEY})"
fi
export POSTGRES_PASSWORD JWT_SECRET_KEY

# ── 2. Preconditions (read-only) ───────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
[ -f backend/venv/bin/activate ] || die "backend/venv/bin/activate not found; create the virtualenv first"
[ -d frontend ] || die "frontend/ not found"

# ── 3. Side effects start here ─────────────────────────────────────────────
echo "=== Starting Alethra Acceptance Environment ==="

# The value is passed by name, not as argv, so it never appears in `ps` output.
# A name conflict makes this fail rather than replacing an existing container.
echo "1. Starting PostgreSQL..."
docker run -d --name "$CONTAINER" \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD \
  -e POSTGRES_DB="$DB_NAME" \
  -p 5433:5432 \
  postgres:16-alpine

echo "Waiting for PostgreSQL to be ready..."
sleep 5

echo "2. Running database migrations..."
cd backend
source venv/bin/activate
export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@localhost:5433/${DB_NAME}"
export JWT_ALGORITHM="HS256"
export AI_PROVIDER="mock"
export DATA_PROVIDER="demo"
export APP_ENV="development"

python -m alembic upgrade head

echo "3. Starting backend server..."
nohup uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/alethra-backend.log 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > /tmp/alethra-backend.pid
echo "Backend started with PID $BACKEND_PID (logs: /tmp/alethra-backend.log)"

echo "4. Building frontend for production..."
cd ../frontend
npm run build

echo "5. Starting frontend server..."
nohup npm start -- -p 3001 > /tmp/alethra-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > /tmp/alethra-frontend.pid
echo "Frontend started with PID $FRONTEND_PID (logs: /tmp/alethra-frontend.log)"

echo ""
echo "=== Acceptance Environment Ready ==="
echo "Backend:  http://localhost:8001"
echo "Frontend: http://localhost:3001"
echo "Database: localhost:5433/${DB_NAME}"
echo ""
echo "To stop: kill \$(cat /tmp/alethra-frontend.pid /tmp/alethra-backend.pid) && docker stop ${CONTAINER}"
