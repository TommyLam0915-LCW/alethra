#!/bin/bash
# Start or resume the self-contained candidate stack owned by this checkout.
#
# A persistent profile (.local/candidate/candidate-profile.env) records WHICH
# candidate this checkout means: candidate id, database identity, origins,
# credentials and artifact paths. Per-run state (pids, heartbeat, start time)
# lives separately in runtime-identity.env. A pid file is never treated as proof
# of identity; processes are re-checked against the profile before reuse or kill.
#
# Modes:
#   ./start_candidate_stack.sh
#       RESUME (default). Uses the saved profile only. Read-only preflight of the
#       candidate database, schema head and fixture accounts; no migration, no
#       seed, no credential generation, no replacement database. Fails clearly if
#       anything does not match. Verifies backend/worker bind to that database
#       before the stack is called ready.
#   ./start_candidate_stack.sh --plan
#       Read-only preflight. Prints the resume plan (SEED=no) and exits without
#       starting processes or writing files. CANDIDATE_PROBE_CMD may supply the
#       probe JSON (tests use this instead of touching PostgreSQL).
#   ./start_candidate_stack.sh --reset
#       EXPLICIT reset. Stops this candidate's verified processes, drops ONLY the
#       database named in the profile, recreates it from this checkout's schema
#       and reseeds the synthetic fixture. Refuses if the checkout schema head
#       differs from the saved profile head.
#   ./start_candidate_stack.sh --new-candidate --candidate-id <id>
#       EXPLICIT new candidate with a new identity. Refuses to overwrite an
#       existing profile, credentials file or log directory, and refuses a
#       non-empty artifact directory (use a separate checkout for that).
#
# Overrides are deliberately rejected during resume when they disagree with the
# saved profile; change the profile with --new-candidate or --reset instead.
# This never touches the shared review/runtime containers or their database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

STATE_DIR="${CANDIDATE_STATE_DIR:-$ROOT/.local/candidate}"
PROFILE="${CANDIDATE_PROFILE:-$STATE_DIR/candidate-profile.env}"
HELPER="$ROOT/backend/scripts/candidate_identity.py"

fail() { echo "CANDIDATE SETUP ABORTED: $*" >&2; exit 1; }

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
identity() { "$PYTHON" "$HELPER" "$@"; }

# --- mode --------------------------------------------------------------------
MODE=resume
NEW_ID=""
case "${1:-}" in
  "") ;;
  --plan) MODE=plan ;;
  --reset) MODE=reset ;;
  --new-candidate)
    MODE=new
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --candidate-id)
          [ $# -ge 2 ] || fail "--candidate-id needs a value"
          NEW_ID="$2"
          shift 2
          ;;
        *) fail "unknown --new-candidate option: $1" ;;
      esac
    done
    [ -n "$NEW_ID" ] || fail "--new-candidate requires --candidate-id <id>"
    ;;
  *) fail "unknown option: $1 (use --plan, --reset or --new-candidate --candidate-id <id>)" ;;
esac

# --- persistent profile ------------------------------------------------------
profile_get() { sed -n "s/^$1=//p" "$PROFILE" 2>/dev/null | head -1; }

require_profile() {
  [ -f "$PROFILE" ] || fail "no persistent candidate profile at $PROFILE. Resume refuses to guess a database; start a new candidate explicitly: ./start_candidate_stack.sh --new-candidate --candidate-id <id>"
  CHECKOUT_DIR="$(profile_get CHECKOUT_DIR)"
  [ "$CHECKOUT_DIR" = "$ROOT" ] || fail "candidate profile $PROFILE belongs to $CHECKOUT_DIR, not $ROOT"
  local key
  for key in CANDIDATE_ID DB_NAME DB_HOST DB_PORT DB_USER CREDENTIALS_FILE ARTIFACT_DIR \
             LOG_DIR BACKEND_ORIGIN FRONTEND_ORIGIN BACKEND_PORT FRONTEND_PORT \
             EXPECTED_SCHEMA_HEAD FIXTURE_OWNER_EMAIL FIXTURE_THIN_EMAIL FIXTURE_EMPTY_EMAIL CREATED_AT; do
    [ -n "$(profile_get "$key")" ] || fail "candidate profile $PROFILE is missing $key"
  done
  CANDIDATE_ID="$(profile_get CANDIDATE_ID)"
  DB_NAME="$(profile_get DB_NAME)"
  DB_HOST="$(profile_get DB_HOST)"
  DB_PORT="$(profile_get DB_PORT)"
  DB_USER="$(profile_get DB_USER)"
  CREDENTIALS_FILE="$(profile_get CREDENTIALS_FILE)"
  ARTIFACT_DIR="$(profile_get ARTIFACT_DIR)"
  LOG_DIR="$(profile_get LOG_DIR)"
  BACKEND_ORIGIN="$(profile_get BACKEND_ORIGIN)"
  FRONTEND_ORIGIN="$(profile_get FRONTEND_ORIGIN)"
  BACKEND_PORT="$(profile_get BACKEND_PORT)"
  FRONTEND_PORT="$(profile_get FRONTEND_PORT)"
  EXPECTED_SCHEMA_HEAD="$(profile_get EXPECTED_SCHEMA_HEAD)"
  FIXTURE_OWNER_EMAIL="$(profile_get FIXTURE_OWNER_EMAIL)"
  FIXTURE_THIN_EMAIL="$(profile_get FIXTURE_THIN_EMAIL)"
  FIXTURE_EMPTY_EMAIL="$(profile_get FIXTURE_EMPTY_EMAIL)"
  case "$DB_NAME" in
    alethra_personal_e2e_candidate*) ;;
    *) fail "candidate profile DB_NAME must start with 'alethra_personal_e2e_candidate'" ;;
  esac
}

# --- credentials (never generated during resume) -----------------------------
CREDENTIAL_KEYS="CANDIDATE_EMAIL CANDIDATE_PASSWORD CANDIDATE_EMPTY_EMAIL CANDIDATE_EMPTY_PASSWORD CANDIDATE_THIN_EMAIL CANDIDATE_THIN_PASSWORD CANDIDATE_JWT_SECRET_KEY"

require_credentials() {
  local file="$1" key
  [ -f "$file" ] || fail "credentials file $file is missing; resume never generates a replacement (use --new-candidate for a new identity)"
  [ "$(stat -f %Lp "$file")" = "600" ] || fail "$file must be mode 600"
  for key in $CREDENTIAL_KEYS; do
    grep -q "^$key=" "$file" || fail "$file is missing $key"
  done
  # shellcheck disable=SC1090
  set -a; . "$file"; set +a
  [ "$CANDIDATE_EMAIL" = "$FIXTURE_OWNER_EMAIL" ] || fail "profile FIXTURE_OWNER_EMAIL does not match $file CANDIDATE_EMAIL"
  [ "$CANDIDATE_THIN_EMAIL" = "$FIXTURE_THIN_EMAIL" ] || fail "profile FIXTURE_THIN_EMAIL does not match $file CANDIDATE_THIN_EMAIL"
  [ "$CANDIDATE_EMPTY_EMAIL" = "$FIXTURE_EMPTY_EMAIL" ] || fail "profile FIXTURE_EMPTY_EMAIL does not match $file CANDIDATE_EMPTY_EMAIL"
}

generate_credentials() {
  local file="$1"
  [ ! -e "$file" ] || fail "refusing to overwrite existing credentials file $file"
  mkdir -p "$(dirname "$file")"
  umask 077
  {
    # example.com is IANA-reserved for documentation, so it is synthetic, and
    # unlike .local/.test it passes the login endpoint's EmailStr validation.
    printf 'CANDIDATE_EMAIL=candidate.owner@example.com\n'
    printf 'CANDIDATE_PASSWORD=%s\n' "$(openssl rand -hex 12)"
    printf 'CANDIDATE_EMPTY_EMAIL=candidate.empty@example.com\n'
    printf 'CANDIDATE_EMPTY_PASSWORD=%s\n' "$(openssl rand -hex 12)"
    printf 'CANDIDATE_THIN_EMAIL=candidate.thin@example.com\n'
    printf 'CANDIDATE_THIN_PASSWORD=%s\n' "$(openssl rand -hex 12)"
    printf 'CANDIDATE_JWT_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
  } >"$file"
  chmod 600 "$file"
  echo "generated candidate credentials in $file"
}

export_process_env() {
  DB_URL="postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
  export JWT_SECRET_KEY="$CANDIDATE_JWT_SECRET_KEY"
  export APP_ENV="${APP_ENV:-development}"
  export TRAINING_WORKER_HEARTBEAT_PATH="$HEARTBEAT_FILE"
  export CORS_ORIGINS="http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
  export CANDIDATE_DB_HOST="$DB_HOST" CANDIDATE_DB_PORT="$DB_PORT"
  export CANDIDATE_DB_USER="$DB_USER" CANDIDATE_DB_PASSWORD="$DB_PASSWORD"
  export DATABASE_URL="$DB_URL"

  # Password-free local entry: opt-in through the persistent profile only, and
  # only for a development APP_ENV. The backend re-checks this as well, but a
  # clear refusal here beats a backend that fails halfway through startup.
  CANDIDATE_PASSWORDLESS_MODE="$(profile_get CANDIDATE_PASSWORDLESS_MODE)"
  [ "$CANDIDATE_PASSWORDLESS_MODE" = "on" ] || CANDIDATE_PASSWORDLESS_MODE="off"
  if [ "$CANDIDATE_PASSWORDLESS_MODE" = "on" ] && [ "$APP_ENV" != "development" ]; then
    fail "CANDIDATE_PASSWORDLESS_MODE=on requires APP_ENV=development (got $APP_ENV); this entry is local-only"
  fi
  export CANDIDATE_PASSWORDLESS_MODE
  export CANDIDATE_PASSWORDLESS_ACCOUNTS="$(profile_get CANDIDATE_PASSWORDLESS_ACCOUNTS)"
  if [ -z "$CANDIDATE_PASSWORDLESS_ACCOUNTS" ]; then
    CANDIDATE_PASSWORDLESS_ACCOUNTS="owner=$CANDIDATE_EMAIL,thin=$CANDIDATE_THIN_EMAIL,empty=$CANDIDATE_EMPTY_EMAIL"
    export CANDIDATE_PASSWORDLESS_ACCOUNTS
  fi
  export CANDIDATE_PASSWORDLESS_SESSION_MINUTES="$(profile_get CANDIDATE_PASSWORDLESS_SESSION_MINUTES)"
  # An empty string would fail int parsing in Settings; leaving it unset lets
  # the backend default (30 minutes) apply.
  if [ -z "$CANDIDATE_PASSWORDLESS_SESSION_MINUTES" ]; then
    unset CANDIDATE_PASSWORDLESS_SESSION_MINUTES
  fi
}

import_check() {
  DATABASE_URL="postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/postgres" \
    "$PYTHON" - <<'PY' || fail "the interpreter cannot import app/alembic/psycopg2 or reach $DB_HOST:$DB_PORT"
import psycopg2, sqlalchemy, alembic  # noqa: F401
import sys
sys.path.insert(0, 'backend')
import app.main  # noqa: F401
PY
}

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

# --- read-only preflight -----------------------------------------------------
DB_PASSWORD="${CANDIDATE_DB_PASSWORD:-testpass}"
HEARTBEAT_FILE=""
PROBE_JSON=""
PLAN_ARGS=()
PLAN_OVERRIDE_KEYS="CANDIDATE_DB_NAME CANDIDATE_DB_HOST CANDIDATE_DB_PORT CANDIDATE_DB_USER CANDIDATE_BACKEND_PORT CANDIDATE_FRONTEND_PORT CANDIDATE_ENV_FILE CANDIDATE_ARTIFACT_DIR CANDIDATE_LOG_DIR"

build_plan_args() {
  PLAN_ARGS=()
  local name flag value
  for name in $PLAN_OVERRIDE_KEYS; do
    value="${!name:-}"
    [ -n "$value" ] || continue
    case "$name" in
      CANDIDATE_DB_NAME) flag=--db-name ;;
      CANDIDATE_DB_HOST) flag=--db-host ;;
      CANDIDATE_DB_PORT) flag=--db-port ;;
      CANDIDATE_DB_USER) flag=--db-user ;;
      CANDIDATE_BACKEND_PORT) flag=--backend-port ;;
      CANDIDATE_FRONTEND_PORT) flag=--frontend-port ;;
      CANDIDATE_ENV_FILE) flag=--credentials-file ;;
      CANDIDATE_ARTIFACT_DIR) flag=--artifact-dir ;;
      CANDIDATE_LOG_DIR) flag=--log-dir ;;
    esac
    PLAN_ARGS[${#PLAN_ARGS[@]}]="$flag"
    PLAN_ARGS[${#PLAN_ARGS[@]}]="$value"
  done
}

run_probe() {
  if [ -n "${CANDIDATE_PROBE_CMD:-}" ]; then
    [ "$MODE" = "plan" ] || fail "CANDIDATE_PROBE_CMD is only accepted in --plan mode"
    sh -c "$CANDIDATE_PROBE_CMD"
  else
    CANDIDATE_DB_PASSWORD="$DB_PASSWORD" "$PYTHON" "$HELPER" probe \
      --host "$DB_HOST" --port "$DB_PORT" --user "$DB_USER" --dbname "$DB_NAME" \
      --fixture-email "$FIXTURE_OWNER_EMAIL" \
      --fixture-email "$FIXTURE_THIN_EMAIL" \
      --fixture-email "$FIXTURE_EMPTY_EMAIL"
  fi
}

# Read-only: probe + profile/DB/schema/fixture validation. No writes, no seeds.
readonly_preflight() {
  local probe_err plan_out
  build_plan_args
  probe_err="$(mktemp "${TMPDIR:-/tmp}/candidate-probe.XXXXXX")"
  if ! PROBE_JSON="$(run_probe 2>"$probe_err")"; then
    cat "$probe_err" >&2 || true
    rm -f "$probe_err"
    fail "read-only database probe failed for $DB_NAME on $DB_HOST:$DB_PORT"
  fi
  rm -f "$probe_err"
  if [ ${#PLAN_ARGS[@]} -eq 0 ]; then
    plan_out="$(printf '%s' "$PROBE_JSON" | identity plan --profile "$PROFILE" --probe-json -)"
  else
    plan_out="$(printf '%s' "$PROBE_JSON" | identity plan --profile "$PROFILE" --probe-json - "${PLAN_ARGS[@]}")"
  fi
  printf '%s\n' "$plan_out"
}

# --- process helpers ---------------------------------------------------------
listener_on() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

pid_from_file() { [ -f "$1" ] && tr -d '[:space:]' <"$1" || true; }

process_state() {
  # Echoes reuse | stale | mismatch:<detail>; never exits the caller's shell.
  local role="$1" pid="$2" out
  out="$("$PYTHON" "$HELPER" check-process --profile "$PROFILE" --role "$role" --pid "$pid" 2>&1)" || {
    echo "mismatch:$out"
    return 0
  }
  case "$out" in
    *"VERIFIED=yes"*) echo "reuse" ;;
    *"REASON=not-running"*|*"REASON=not-this-role"*) echo "stale" ;;
    *) echo "mismatch:$out" ;;
  esac
}

wait_http() {
  local url="$1" label="$2" tries="${3:-90}"
  for _ in $(seq 1 "$tries"); do
    if "$PYTHON" - "$url" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(sys.argv[1], timeout=2)
PY
    then return 0; fi
    sleep 2
  done
  fail "$label did not answer $url within $((tries * 2))s"
}

wait_worker_heartbeat() {
  local pid="$1"
  for _ in $(seq 1 30); do
    if "$PYTHON" - "$HEARTBEAT_FILE" "$pid" <<'PY' >/dev/null 2>&1
import json, os, sys, time
try:
    beat = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
sys.exit(0 if str(beat.get("pid")) == sys.argv[2] and time.time() - os.path.getmtime(sys.argv[1]) <= 60 else 1)
PY
    then return 0; fi
    sleep 1
  done
  tail -20 "$LOG_DIR/worker.log" >&2 || true
  fail "worker pid $pid did not publish a fresh heartbeat"
}

# --- stack lifecycle ---------------------------------------------------------
start_backend() {
  local pid state
  pid="$(pid_from_file "$BACKEND_PID_FILE")"
  if [ -n "$pid" ] && pid_alive "$pid"; then
    state="$(process_state backend "$pid")"
    case "$state" in
      reuse) echo "reusing backend (pid $pid, identity verified)"; return 0 ;;
      stale) echo "ignoring stale backend pid file ($pid)"; rm -f "$BACKEND_PID_FILE" ;;
      *) fail "${state#mismatch:}" ;;
    esac
  fi
  [ -z "$(listener_on "$BACKEND_PORT")" ] || fail "port $BACKEND_PORT is held by a process this candidate did not start"
  echo "starting backend on 127.0.0.1:$BACKEND_PORT"
  (
    cd backend
    nohup "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
      >"$LOG_DIR/backend.log" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
  wait_http "$BACKEND_ORIGIN/health" "backend"
}

start_worker() {
  local pid state
  pid="$(pid_from_file "$WORKER_PID_FILE")"
  if [ -n "$pid" ] && pid_alive "$pid"; then
    state="$(process_state worker "$pid")"
    case "$state" in
      reuse) echo "reusing resident training worker (pid $pid, identity verified)"; return 0 ;;
      stale) echo "ignoring stale worker pid file ($pid)"; rm -f "$WORKER_PID_FILE" ;;
      *) fail "${state#mismatch:}" ;;
    esac
  fi
  echo "starting resident training worker (heartbeat: $HEARTBEAT_FILE)"
  # A heartbeat left by a previous run must not be read as this worker's proof.
  rm -f "$HEARTBEAT_FILE"
  (
    cd backend
    nohup "$PYTHON" -m app.workers.training_worker 2 \
      >"$LOG_DIR/worker.log" 2>&1 &
    echo $! >"$WORKER_PID_FILE"
  )
  WORKER_PID="$(pid_from_file "$WORKER_PID_FILE")"
  wait_worker_heartbeat "$WORKER_PID"
}

start_frontend() {
  local pid state
  pid="$(pid_from_file "$FRONTEND_PID_FILE")"
  if [ -n "$pid" ] && pid_alive "$pid"; then
    state="$(process_state frontend "$pid")"
    case "$state" in
      reuse) echo "reusing frontend (pid $pid, identity verified)"; return 0 ;;
      stale) echo "ignoring stale frontend pid file ($pid)"; rm -f "$FRONTEND_PID_FILE" ;;
      *) fail "${state#mismatch:}" ;;
    esac
  fi
  [ -z "$(listener_on "$FRONTEND_PORT")" ] || fail "port $FRONTEND_PORT is held by a process this candidate did not start"
  [ -d frontend/node_modules ] || fail "frontend/node_modules missing - run npm ci in frontend first"
  echo "starting frontend on 127.0.0.1:$FRONTEND_PORT"
  (
    cd frontend
    NEXT_DEV_DIST_DIR=".next/candidate-$FRONTEND_PORT" \
    NEXT_PUBLIC_API_URL="$BACKEND_ORIGIN" \
      nohup ./node_modules/.bin/next dev -p "$FRONTEND_PORT" \
      >"$LOG_DIR/frontend.log" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
  wait_http "$FRONTEND_ORIGIN/login" "frontend"
}

write_runtime_identity() {
  local state
  state="$(sed -n 's/.*"state": "\([^"]*\)".*/\1/p' "$HEARTBEAT_FILE" 2>/dev/null | head -1)"
  {
    printf 'MODE=%s\n' "$MODE"
    printf 'STARTED_AT=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf 'CANDIDATE_PROFILE=%s\n' "$PROFILE"
    printf 'BACKEND_PID=%s\n' "$BACKEND_PID"
    printf 'WORKER_PID=%s\n' "$WORKER_PID"
    printf 'FRONTEND_PID=%s\n' "$FRONTEND_PID"
    printf 'HEARTBEAT_FILE=%s\n' "$HEARTBEAT_FILE"
    printf 'WORKER_STATE=%s\n' "${state:-unknown}"
  } >"$STATE_DIR/runtime-identity.env"
  chmod 600 "$STATE_DIR/runtime-identity.env"
}

start_stack() {
  mkdir -p "$LOG_DIR" "$STATE_DIR"
  BACKEND_PID_FILE="$LOG_DIR/backend.pid"
  WORKER_PID_FILE="$LOG_DIR/worker.pid"
  FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
  export_process_env
  start_backend
  BACKEND_PID="$(pid_from_file "$BACKEND_PID_FILE")"
  start_worker
  WORKER_PID="$(pid_from_file "$WORKER_PID_FILE")"
  start_frontend
  FRONTEND_PID="$(pid_from_file "$FRONTEND_PID_FILE")"
  identity verify-runtime --profile "$PROFILE" \
    --backend-pid "$BACKEND_PID" --worker-pid "$WORKER_PID" \
    --frontend-pid "$FRONTEND_PID" --heartbeat "$HEARTBEAT_FILE" \
    || fail "candidate identity verification failed; the stack is NOT ready"
  # Identity is proven above; these checks only confirm the bound processes serve.
  wait_http "$BACKEND_ORIGIN/health" "backend" 15
  wait_http "$FRONTEND_ORIGIN/login" "frontend" 15
  write_runtime_identity
  print_ready
}

print_ready() {
  echo
  echo "Candidate stack ready (candidate $CANDIDATE_ID, database $DB_NAME, mode: $MODE):"
  echo "  open     : $FRONTEND_ORIGIN/login"
  if [ "${CANDIDATE_PASSWORDLESS_MODE:-off}" = "on" ]; then
    echo "  enter    : $FRONTEND_ORIGIN/   (password-free local test mode - synthetic data; the login form still works)"
  else
    echo "  enter    : password-free entry OFF (set CANDIDATE_PASSWORDLESS_MODE=on in $PROFILE to enable it)"
  fi
  echo "  login    : $CANDIDATE_EMAIL   password: $CREDENTIALS_FILE (key CANDIDATE_PASSWORD, never printed)"
  echo "  accounts : empty $CANDIDATE_EMPTY_EMAIL · thin $CANDIDATE_THIN_EMAIL (passwords in the same 0600 file)"
  echo "  backend  : $BACKEND_ORIGIN"
  echo "  worker   : resident pid $WORKER_PID, heartbeat $HEARTBEAT_FILE (identity verified)"
  echo "  data     : synthetic only - owner 205 reviewed decisions; thin 5 decisions (3 confirmed); empty account has no data"
  echo "  profile  : $PROFILE (database identity is persisted here, not in runtime-identity.env)"
  echo "  reset    : ./start_candidate_stack.sh --reset   (drops ONLY $DB_NAME and reseeds)"
  echo "  stop     : ./stop_candidate_stack.sh            (keeps the database)"
}

# --- modes -------------------------------------------------------------------
plan_mode() {
  require_profile
  HEARTBEAT_FILE="$LOG_DIR/worker-heartbeat.json"
  echo "mode: PLAN (read-only; nothing will be started or written into the repository)"
  readonly_preflight
  echo "PROFILE=$PROFILE"
  echo "CHECKOUT_DIR=$CHECKOUT_DIR"
}

resume_mode() {
  require_profile
  HEARTBEAT_FILE="$LOG_DIR/worker-heartbeat.json"
  require_credentials "$CREDENTIALS_FILE"
  echo "mode: RESUME (saved profile $PROFILE; no migration, seed or credential change)"
  readonly_preflight
  import_check
  start_stack
}

reset_candidate() {
  require_profile
  HEARTBEAT_FILE="$LOG_DIR/worker-heartbeat.json"
  require_credentials "$CREDENTIALS_FILE"
  local checkout_head
  checkout_head="$(cd backend && "$PYTHON" -m alembic heads 2>/dev/null | sed -n 's/^\([0-9a-z]\{12\}\).*/\1/p' | head -1)"
  [ "$checkout_head" = "$EXPECTED_SCHEMA_HEAD" ] || fail "this checkout's schema head ${checkout_head:-unknown} differs from the saved candidate head $EXPECTED_SCHEMA_HEAD; create a new candidate instead of rewriting this one"
  echo "mode: RESET - explicit, drops ONLY database $DB_NAME and rebuilds it"
  CANDIDATE_PROFILE="$PROFILE" "$ROOT/stop_candidate_stack.sh" || fail "could not stop the recorded candidate processes"
  export_process_env
  import_check
  mkdir -p "$LOG_DIR" "$STATE_DIR"
  db_admin "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$DB_NAME' and pid <> pg_backend_pid()" >/dev/null || true
  db_admin "drop database if exists \"$DB_NAME\"" >/dev/null || fail "could not drop $DB_NAME"
  db_admin "create database \"$DB_NAME\"" >/dev/null || fail "could not create $DB_NAME"
  (cd backend && "$PYTHON" -m alembic -c alembic.ini upgrade head) \
    >"$LOG_DIR/migrate.log" 2>&1 || { tail -40 "$LOG_DIR/migrate.log" >&2; fail "alembic upgrade failed (log: $LOG_DIR/migrate.log)"; }
  seed_fixture
  start_stack
}

new_candidate() {
  CANDIDATE_ID="$NEW_ID"
  DB_NAME="${CANDIDATE_DB_NAME:-alethra_personal_e2e_candidate_$(printf '%s' "$CANDIDATE_ID" | tr '-' '_')}"
  DB_HOST="${CANDIDATE_DB_HOST:-127.0.0.1}"
  DB_PORT="${CANDIDATE_DB_PORT:-54321}"
  DB_USER="${CANDIDATE_DB_USER:-postgres}"
  DB_PASSWORD="${CANDIDATE_DB_PASSWORD:-testpass}"
  CREDENTIALS_FILE="${CANDIDATE_ENV_FILE:-$STATE_DIR/credentials-$CANDIDATE_ID.env}"
  ARTIFACT_DIR="${CANDIDATE_ARTIFACT_DIR:-$ROOT/backend/app/ml/artifacts}"
  LOG_DIR="${CANDIDATE_LOG_DIR:-$STATE_DIR/$CANDIDATE_ID}"
  BACKEND_PORT="${CANDIDATE_BACKEND_PORT:-8101}"
  FRONTEND_PORT="${CANDIDATE_FRONTEND_PORT:-3101}"
  BACKEND_ORIGIN="http://127.0.0.1:$BACKEND_PORT"
  FRONTEND_ORIGIN="http://127.0.0.1:$FRONTEND_PORT"
  FIXTURE_OWNER_EMAIL="candidate.owner@example.com"
  FIXTURE_THIN_EMAIL="candidate.thin@example.com"
  FIXTURE_EMPTY_EMAIL="candidate.empty@example.com"
  HEARTBEAT_FILE="$LOG_DIR/worker-heartbeat.json"

  echo "mode: NEW CANDIDATE $CANDIDATE_ID (explicit; never overwrites an existing candidate)"
  identity new-check --candidate-id "$CANDIDATE_ID" --profile "$PROFILE" \
    --credentials "$CREDENTIALS_FILE" --log-dir "$LOG_DIR" \
    --db-name "$DB_NAME" --artifact-dir "$ARTIFACT_DIR"
  [ -z "$(listener_on "$BACKEND_PORT")" ] || fail "backend port $BACKEND_PORT is already in use"
  [ -z "$(listener_on "$FRONTEND_PORT")" ] || fail "frontend port $FRONTEND_PORT is already in use"

  export CANDIDATE_DB_HOST="$DB_HOST" CANDIDATE_DB_PORT="$DB_PORT"
  export CANDIDATE_DB_USER="$DB_USER" CANDIDATE_DB_PASSWORD="$DB_PASSWORD"
  if [ -n "$(db_admin "select 1 from pg_database where datname = '$DB_NAME'" || true)" ]; then
    fail "database $DB_NAME already exists; a new candidate must not adopt or overwrite it"
  fi

  generate_credentials "$CREDENTIALS_FILE"
  require_credentials "$CREDENTIALS_FILE"
  import_check
  mkdir -p "$LOG_DIR" "$STATE_DIR"
  db_admin "create database \"$DB_NAME\"" >/dev/null || fail "could not create $DB_NAME"
  (cd backend && "$PYTHON" -m alembic -c alembic.ini upgrade head) \
    >"$LOG_DIR/migrate.log" 2>&1 || { tail -40 "$LOG_DIR/migrate.log" >&2; fail "alembic upgrade failed (log: $LOG_DIR/migrate.log)"; }
  local head
  head="$(cd backend && "$PYTHON" -m alembic -c alembic.ini current 2>/dev/null | sed -n 's/.*\([0-9a-z]\{12\}\).*/\1/p' | head -1)"
  [ -n "$head" ] || fail "could not read the new database schema head"
  seed_fixture

  local values_file
  values_file="$(mktemp "${TMPDIR:-/tmp}/candidate-profile.XXXXXX")"
  {
    printf 'CANDIDATE_ID=%s\n' "$CANDIDATE_ID"
    printf 'CHECKOUT_DIR=%s\n' "$ROOT"
    printf 'DB_NAME=%s\n' "$DB_NAME"
    printf 'DB_HOST=%s\n' "$DB_HOST"
    printf 'DB_PORT=%s\n' "$DB_PORT"
    printf 'DB_USER=%s\n' "$DB_USER"
    printf 'CREDENTIALS_FILE=%s\n' "$CREDENTIALS_FILE"
    printf 'ARTIFACT_DIR=%s\n' "$ARTIFACT_DIR"
    printf 'LOG_DIR=%s\n' "$LOG_DIR"
    printf 'BACKEND_ORIGIN=%s\n' "$BACKEND_ORIGIN"
    printf 'FRONTEND_ORIGIN=%s\n' "$FRONTEND_ORIGIN"
    printf 'BACKEND_PORT=%s\n' "$BACKEND_PORT"
    printf 'FRONTEND_PORT=%s\n' "$FRONTEND_PORT"
    printf 'EXPECTED_SCHEMA_HEAD=%s\n' "$head"
    printf 'FIXTURE_OWNER_EMAIL=%s\n' "$FIXTURE_OWNER_EMAIL"
    printf 'FIXTURE_THIN_EMAIL=%s\n' "$FIXTURE_THIN_EMAIL"
    printf 'FIXTURE_EMPTY_EMAIL=%s\n' "$FIXTURE_EMPTY_EMAIL"
    printf 'CREATED_AT=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
  } >"$values_file"
  identity write-profile --path "$PROFILE" --values-file "$values_file" >/dev/null
  rm -f "$values_file"
  echo "wrote persistent candidate profile $PROFILE"
  start_stack
}

seed_fixture() {
  echo "seeding synthetic candidate fixture (no real market or personal data)"
  (cd backend && E2E_EMAIL="$CANDIDATE_EMAIL" E2E_PASSWORD="$CANDIDATE_PASSWORD" E2E_GRANT_CONSENT=1 \
    "$PYTHON" scripts/seed_e2e_personal_learning.py) | tee "$LOG_DIR/seed.log"
  echo "ensuring first-use scenario accounts exist (create-if-missing)"
  (cd backend && E2E_EMPTY_EMAIL="$CANDIDATE_EMPTY_EMAIL" E2E_EMPTY_PASSWORD="$CANDIDATE_EMPTY_PASSWORD" \
    E2E_THIN_EMAIL="$CANDIDATE_THIN_EMAIL" E2E_THIN_PASSWORD="$CANDIDATE_THIN_PASSWORD" \
    "$PYTHON" scripts/seed_candidate_scenarios.py) | tee -a "$LOG_DIR/seed.log"
}

case "$MODE" in
  plan) plan_mode ;;
  resume) resume_mode ;;
  reset) reset_candidate ;;
  new) new_candidate ;;
esac
