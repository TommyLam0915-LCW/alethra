#!/bin/bash
# Bring up the self-contained candidate stack for owner review.
#
# What this creates (all of it owned by this checkout):
#   - a disposable PostgreSQL database named alethra_personal_e2e_candidate_<date>
#     on the local test cluster (default 127.0.0.1:54321), never a shared runtime
#   - schema at this checkout's alembic head (migrations run only on that database)
#   - one synthetic candidate account, one locked strategy version, synthetic
#     daily bars, 205 reviewed decisions and a fixture consent scope
#   - backend + resident training worker (heartbeat file) + Next.js dev server
#   - one URL for the owner, credentials in a 0600 file
#
# Modes:
#   ./start_candidate_stack.sh           RESUME (default): keeps the existing
#                                        candidate database and its progress
#   ./start_candidate_stack.sh --reset   FRESH: drops and rebuilds ONLY the
#                                        candidate database, then reseeds
#
# Overrides:
#   CANDIDATE_BACKEND_PORT (8100) CANDIDATE_FRONTEND_PORT (3100)
#   CANDIDATE_DB_HOST (127.0.0.1) CANDIDATE_DB_PORT (54321)
#   CANDIDATE_DB_USER (postgres) CANDIDATE_DB_PASSWORD (testpass)
#   CANDIDATE_DB_NAME (alethra_personal_e2e_candidate_YYYYMMDD)
#   CANDIDATE_PYTHON   CANDIDATE_ENV_FILE   CANDIDATE_LOG_DIR
#
# This never touches the shared review/runtime containers or their database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_PORT="${CANDIDATE_BACKEND_PORT:-8100}"
FRONTEND_PORT="${CANDIDATE_FRONTEND_PORT:-3100}"
DB_HOST="${CANDIDATE_DB_HOST:-127.0.0.1}"
DB_PORT="${CANDIDATE_DB_PORT:-54321}"
DB_USER="${CANDIDATE_DB_USER:-postgres}"
DB_PASSWORD="${CANDIDATE_DB_PASSWORD:-testpass}"
DB_NAME="${CANDIDATE_DB_NAME:-alethra_personal_e2e_candidate_$(date +%Y%m%d)}"
ENV_FILE="${CANDIDATE_ENV_FILE:-.local/candidate-credentials.env}"
LOG_DIR="${CANDIDATE_LOG_DIR:-.local/candidate}"
HEARTBEAT_FILE="$LOG_DIR/worker-heartbeat.json"
IDENTITY_FILE="$LOG_DIR/runtime-identity.env"

RESET=0
[ "${1:-}" = "--reset" ] && RESET=1

fail() { echo "CANDIDATE SETUP ABORTED: $*" >&2; exit 1; }

case "$DB_NAME" in
  alethra_personal_e2e_candidate*) ;;
  *) fail "CANDIDATE_DB_NAME must start with 'alethra_personal_e2e_candidate'" ;;
esac

# --- python interpreter -------------------------------------------------------
find_python() {
  if [ -n "${CANDIDATE_PYTHON:-}" ]; then echo "$CANDIDATE_PYTHON"; return; fi
  for candidate in \
    "$ROOT/backend/venv/bin/python" \
    "/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend/venv/bin/python" \
    "$(command -v python3 || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && echo "$candidate" && return
  done
  fail "no python interpreter found; set CANDIDATE_PYTHON"
}
PYTHON="$(find_python)"

mkdir -p "$LOG_DIR" "$(dirname "$ENV_FILE")"

# --- credentials: generated once, mode 600, never printed ---------------------
if [ "$RESET" = "1" ] || [ ! -f "$ENV_FILE" ]; then
  umask 077
  {
    # example.com is IANA-reserved for documentation, so it is synthetic, and
    # unlike .local/.test it passes the login endpoint's EmailStr validation.
    printf 'CANDIDATE_EMAIL=candidate.owner@example.com\n'
    printf 'CANDIDATE_PASSWORD=%s\n' "$(openssl rand -hex 12)"
    printf 'CANDIDATE_JWT_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
  } >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "generated candidate credentials in $ENV_FILE"
else
  echo "reusing $ENV_FILE"
fi
[ "$(stat -f %Lp "$ENV_FILE")" = "600" ] || fail "$ENV_FILE must be mode 600"

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
DB_URL="postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
export JWT_SECRET_KEY="$CANDIDATE_JWT_SECRET_KEY"
export APP_ENV="${APP_ENV:-development}"
export TRAINING_WORKER_HEARTBEAT_PATH="$ROOT/$HEARTBEAT_FILE"
export CORS_ORIGINS="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
export CANDIDATE_DB_HOST="$DB_HOST" CANDIDATE_DB_PORT="$DB_PORT"
export CANDIDATE_DB_USER="$DB_USER" CANDIDATE_DB_PASSWORD="$DB_PASSWORD"

# app.main reads its settings (and opens no pool) at import, but some modules
# resolve the engine immediately, so the probe points at the always-present
# maintenance database instead of a candidate database that does not exist yet.
DATABASE_URL="postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/postgres" \
  "$PYTHON" - <<'PY' || fail "the interpreter cannot import app/alembic/psycopg2 or reach $DB_HOST:$DB_PORT"
import psycopg2, sqlalchemy, alembic  # noqa: F401
import sys
sys.path.insert(0, 'backend')
import app.main  # noqa: F401
PY

export DATABASE_URL="$DB_URL"

db_admin() {
  # $1 = sql. Connects to the maintenance database; prints rows.
  "$PYTHON" - "$1" <<'PY'
import os, sys
import psycopg2
sql = sys.argv[1]
conn = psycopg2.connect(
    host=os.environ["CANDIDATE_DB_HOST"], port=os.environ["CANDIDATE_DB_PORT"],
    user=os.environ["CANDIDATE_DB_USER"], password=os.environ["CANDIDATE_DB_PASSWORD"],
    dbname="postgres", connect_timeout=5,
)
conn.autocommit = True
cur = conn.cursor()
cur.execute(sql)
if cur.description is not None:
    for row in cur.fetchall():
        print("|".join("" if value is None else str(value) for value in row))
conn.close()
PY
}

DB_EXISTS="$(db_admin "select 1 from pg_database where datname = '$DB_NAME'" || true)"

if [ "$RESET" = "1" ]; then
  echo "mode: FRESH - dropping ONLY database $DB_NAME"
  db_admin "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$DB_NAME' and pid <> pg_backend_pid()" >/dev/null || true
  db_admin "drop database if exists \"$DB_NAME\"" >/dev/null || fail "could not drop $DB_NAME"
  DB_EXISTS=""
fi

if [ -z "$DB_EXISTS" ]; then
  echo "creating database $DB_NAME"
  db_admin "create database \"$DB_NAME\"" >/dev/null || fail "could not create $DB_NAME"
  FRESH_SEED=1
else
  echo "database $DB_NAME already exists - resume keeps its data"
  FRESH_SEED=0
fi

# --- schema -------------------------------------------------------------------
echo "applying this checkout's migrations to $DB_NAME only"
(cd backend && "$PYTHON" -m alembic -c alembic.ini upgrade head) \
  >"$LOG_DIR/migrate.log" 2>&1 || { tail -40 "$LOG_DIR/migrate.log" >&2; fail "alembic upgrade failed (log: $LOG_DIR/migrate.log)"; }
HEAD="$(cd backend && "$PYTHON" -m alembic -c alembic.ini current 2>/dev/null | sed -n 's/.*\([0-9a-z]\{12\}\).*/\1/p' | head -1)"
echo "schema head: ${HEAD:-unknown} (log: $LOG_DIR/migrate.log)"

# --- deterministic synthetic fixture -----------------------------------------
if [ "$FRESH_SEED" = "1" ]; then
  echo "seeding synthetic candidate fixture (no real market or personal data)"
  (cd backend && E2E_EMAIL="$CANDIDATE_EMAIL" E2E_PASSWORD="$CANDIDATE_PASSWORD" E2E_GRANT_CONSENT=1 \
    "$PYTHON" scripts/seed_e2e_personal_learning.py) | tee "$LOG_DIR/seed.log"
else
  echo "resume keeps the existing fixture and any progress made in it"
fi

# --- process helpers ----------------------------------------------------------
listener_on() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

pid_from_file() { [ -f "$1" ] && tr -d '[:space:]' <"$1" || true; }

wait_http() {
  local url="$1" label="$2"
  for _ in $(seq 1 90); do
    if "$PYTHON" - "$url" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2)
PY
    then return 0; fi
    sleep 2
  done
  fail "$label did not answer $url within 180s"
}

# --- backend ------------------------------------------------------------------
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
BACKEND_PID="$(pid_from_file "$BACKEND_PID_FILE")"
if pid_alive "$BACKEND_PID" && [ -n "$(listener_on "$BACKEND_PORT")" ]; then
  echo "reusing backend (pid $BACKEND_PID)"
else
  [ -z "$(listener_on "$BACKEND_PORT")" ] || fail "port $BACKEND_PORT is already in use by another process"
  echo "starting backend on 127.0.0.1:$BACKEND_PORT"
  (
    cd backend
    nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
      >"$ROOT/$LOG_DIR/backend.log" 2>&1 &
    echo $! >"$ROOT/$BACKEND_PID_FILE"
  )
  wait_http "http://127.0.0.1:$BACKEND_PORT/health" "backend"
fi

# --- resident training worker (managed, not test-started) ---------------------
WORKER_PID_FILE="$LOG_DIR/worker.pid"
WORKER_PID="$(pid_from_file "$WORKER_PID_FILE")"
if pid_alive "$WORKER_PID"; then
  echo "reusing resident training worker (pid $WORKER_PID)"
else
  echo "starting resident training worker (heartbeat: $HEARTBEAT_FILE)"
  # A heartbeat left by a previous run must not be read as this worker's proof.
  rm -f "$HEARTBEAT_FILE"
  (
    cd backend
    nohup "$PYTHON" -m app.workers.training_worker 2 \
      >"$ROOT/$LOG_DIR/worker.log" 2>&1 &
    echo $! >"$ROOT/$WORKER_PID_FILE"
  )
fi

# The worker must prove liveness, not just be spawned.
WORKER_READY=""
for _ in $(seq 1 30); do
  if [ -f "$HEARTBEAT_FILE" ]; then WORKER_READY=yes; break; fi
  sleep 1
done
[ -n "$WORKER_READY" ] || { tail -20 "$LOG_DIR/worker.log" >&2; fail "worker did not publish a heartbeat"; }
echo "worker heartbeat: $(cat "$HEARTBEAT_FILE")"

# --- frontend -----------------------------------------------------------------
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
FRONTEND_PID="$(pid_from_file "$FRONTEND_PID_FILE")"
if pid_alive "$FRONTEND_PID" && [ -n "$(listener_on "$FRONTEND_PORT")" ]; then
  echo "reusing frontend (pid $FRONTEND_PID)"
else
  [ -z "$(listener_on "$FRONTEND_PORT")" ] || fail "port $FRONTEND_PORT is already in use by another process"
  [ -d frontend/node_modules ] || fail "frontend/node_modules missing - run npm ci in frontend first"
  echo "starting frontend on 127.0.0.1:$FRONTEND_PORT"
  (
    cd frontend
    NEXT_DEV_DIST_DIR=".next/candidate-$FRONTEND_PORT" \
    NEXT_PUBLIC_API_URL="http://127.0.0.1:$BACKEND_PORT" \
      nohup ./node_modules/.bin/next dev -p "$FRONTEND_PORT" \
      >"$ROOT/$LOG_DIR/frontend.log" 2>&1 &
    echo $! >"$ROOT/$FRONTEND_PID_FILE"
  )
  wait_http "http://127.0.0.1:$FRONTEND_PORT/login" "frontend"
fi

# --- identity -----------------------------------------------------------------
{
  printf 'UPDATED_AT=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  printf 'DB_NAME=%s\n' "$DB_NAME"
  printf 'DB_HOST=%s\n' "$DB_HOST"
  printf 'DB_PORT=%s\n' "$DB_PORT"
  printf 'ALEMBIC_HEAD=%s\n' "${HEAD:-unknown}"
  printf 'BACKEND_PORT=%s\n' "$BACKEND_PORT"
  printf 'FRONTEND_PORT=%s\n' "$FRONTEND_PORT"
  printf 'BACKEND_PID=%s\n' "$(pid_from_file "$BACKEND_PID_FILE")"
  printf 'WORKER_PID=%s\n' "$(pid_from_file "$WORKER_PID_FILE")"
  printf 'FRONTEND_PID=%s\n' "$(pid_from_file "$FRONTEND_PID_FILE")"
  printf 'HEARTBEAT_FILE=%s\n' "$HEARTBEAT_FILE"
} >"$IDENTITY_FILE"
chmod 600 "$IDENTITY_FILE"

ENV_FILE_ABS="$ROOT/$ENV_FILE"
echo
echo "Candidate stack ready (database $DB_NAME, mode: $([ "$RESET" = "1" ] && echo FRESH || echo RESUME)):"
echo "  open     : http://localhost:$FRONTEND_PORT/login"
echo "  login    : $CANDIDATE_EMAIL   password: $ENV_FILE_ABS (key CANDIDATE_PASSWORD, never printed)"
echo "  backend  : http://127.0.0.1:$BACKEND_PORT"
echo "  worker   : resident pid $(pid_from_file "$WORKER_PID_FILE"), heartbeat $HEARTBEAT_FILE"
echo "  data     : synthetic fixture only - 205 reviewed decisions, 140 synthetic bars; not real market data"
echo "  progress : this database is disposable and owned by this checkout"
echo "  reset    : ./start_candidate_stack.sh --reset   (drops ONLY $DB_NAME and reseeds)"
echo "  stop     : ./stop_candidate_stack.sh            (keeps the database)"
