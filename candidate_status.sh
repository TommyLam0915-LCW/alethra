#!/bin/bash
# Read-only status for the candidate stack recorded by start_candidate_stack.sh.
#
# Nothing here changes state: no process is started or stopped, and the only
# signal used is signal 0 (candidate_identity.py:330), which asks whether a pid
# exists and delivers nothing. Each check is a verb of
# backend/scripts/candidate_identity.py, which identify a recorded pid from its
# command line and its visible DATABASE_URL instead of trusting the pid file, and
# probe the database inside SET TRANSACTION READ ONLY (candidate_identity.py:302).
#
# A recorded pid that does not match the profile is reported as UNMATCHED, not as
# "stopped": on a shared machine a stale pid file usually points at somebody
# else's process, and calling that this candidate's server would be a guess.
#
# The database check needs the candidate's own DB password, which is never stored
# in the profile. Export CANDIDATE_DB_PASSWORD to get the schema-head line;
# without it the check is reported as SKIPPED rather than guessed at.
#
# Exit: 0 every recorded role verified and the backend answered /health/ready;
#       1 the stack is not fully up - the report names the part;
#       2 refused before reporting anything (no interpreter, no profile).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

STATE_DIR="${CANDIDATE_STATE_DIR:-$ROOT/.local/candidate}"
PROFILE="${CANDIDATE_PROFILE:-$STATE_DIR/candidate-profile.env}"
HELPER="$ROOT/backend/scripts/candidate_identity.py"

fail() { echo "CANDIDATE STATUS REFUSED: $*" >&2; exit 2; }

find_python() {
  if [ -n "${CANDIDATE_PYTHON:-}" ]; then
    [ -f "$CANDIDATE_PYTHON" ] && [ -x "$CANDIDATE_PYTHON" ] ||
      fail "CANDIDATE_PYTHON=$CANDIDATE_PYTHON is not an executable file"
    echo "$CANDIDATE_PYTHON"; return
  fi
  for candidate in \
    "$ROOT/backend/.venv/bin/python" \
    "$ROOT/backend/venv/bin/python"; do
    [ -f "$candidate" ] && [ -x "$candidate" ] && echo "$candidate" && return
  done
  fail "no candidate virtualenv under $ROOT/backend (.venv/bin/python or venv/bin/python); a system python3 would report a different lane than the one the stack runs in - set CANDIDATE_PYTHON"
}
# find_python runs in a command substitution, so its refusal exits the
# subshell only; the parent has to propagate the code rather than carry on with
# an empty interpreter.
PYTHON="$(find_python)" || exit 2
identity() { "$PYTHON" "$HELPER" "$@"; }

[ -f "$PROFILE" ] || fail "no persistent candidate profile at $PROFILE; there is no recorded stack to report on. ./start_candidate_stack.sh --new-candidate --candidate-id <id> writes one"

profile_get() { sed -n "s/^$1=//p" "$PROFILE" | head -1; }
CHECKOUT_DIR="$(profile_get CHECKOUT_DIR)"
[ "$CHECKOUT_DIR" = "$ROOT" ] || fail "candidate profile $PROFILE belongs to $CHECKOUT_DIR, not $ROOT"
LOG_DIR="$(profile_get LOG_DIR)"
BACKEND_PORT="$(profile_get BACKEND_PORT)"
BACKEND_ORIGIN="$(profile_get BACKEND_ORIGIN)"
EXPECTED_HEAD="$(profile_get EXPECTED_SCHEMA_HEAD)"
[ -n "$LOG_DIR" ] && [ -n "$BACKEND_PORT" ] && [ -n "$BACKEND_ORIGIN" ] ||
  fail "candidate profile $PROFILE is missing LOG_DIR, BACKEND_PORT or BACKEND_ORIGIN"

# The heartbeat path is per-run and lives in runtime-identity.env, not in the
# persistent profile; the pids are read from the pid files, never from that
# file's recorded PIDs, which are only ever a claim about a past run.
RUNTIME_ID="$STATE_DIR/runtime-identity.env"
HEARTBEAT_FILE=""
LAST_RUN="no runtime identity at $RUNTIME_ID; nothing has been started from this profile"
ri_get() { sed -n "s/^$1=//p" "$RUNTIME_ID" | head -1; }
if [ -f "$RUNTIME_ID" ]; then
  if [ "$(ri_get CANDIDATE_PROFILE)" = "$PROFILE" ]; then
    LAST_RUN="STATUS=$(ri_get STATUS) RUN_ID=$(ri_get RUN_ID) UPDATED_AT=$(ri_get UPDATED_AT)"
    HEARTBEAT_FILE="$(ri_get HEARTBEAT_FILE)"
  else
    LAST_RUN="runtime-identity.env at $RUNTIME_ID belongs to $(ri_get CANDIDATE_PROFILE), not this profile; ignored"
  fi
fi

NOT_UP=0
pid_of() { [ -f "$LOG_DIR/$1.pid" ] && tr -d '[:space:]' <"$LOG_DIR/$1.pid" || true; }

# listener_on <port> -> the pids actually holding that TCP port
listener_on() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | tr '\n' ' ' | sed 's/ $//'; }

report_role() {
  local role="$1" pid out
  pid="$(pid_of "$role")"
  if [ -z "$pid" ]; then
    echo "$role: NOT STARTED (no $LOG_DIR/$role.pid recorded)"
    NOT_UP=1
    return
  fi
  case "$pid" in
    *[!0-9]*)
      echo "$role: $LOG_DIR/$role.pid does not hold a pid (it says '$pid'); nothing can be reported for this role"
      NOT_UP=1
      return
      ;;
  esac
  if ! out="$(identity check-process --profile "$PROFILE" --role "$role" --pid "$pid" 2>&1)"; then
    echo "$role: pid $pid COULD NOT BE CHECKED: $(echo "$out" | tr '\n' ' ')"
    NOT_UP=1
    return
  fi
  case "$out" in
    *VERIFIED=yes*)
      echo "$role: RUNNING, pid $pid matches the profile"
      [ "$role" = worker ] && WORKER_OK=1
      ;;
    *REASON=not-running*)
      echo "$role: pid $pid is not running; the record is stale and nothing of this candidate's is live under it"
      NOT_UP=1
      ;;
    *)
      echo "$role: pid $pid UNMATCHED - $(echo "$out" | tr '\n' ' ')"
      echo "       (reported as unmatched, not as stopped; ./stop_candidate_stack.sh will not signal it either)"
      NOT_UP=1
      ;;
  esac
}

WORKER_OK=0
echo "=== Alethra candidate status ==="
echo "profile : $PROFILE"
echo "database: $(profile_get DB_NAME) on $(profile_get DB_HOST):$(profile_get DB_PORT) as $(profile_get DB_USER)"
echo "last run  : $LAST_RUN"
for role in backend worker frontend; do
  report_role "$role"
done

if [ "$WORKER_OK" -eq 1 ]; then
  if [ -z "$HEARTBEAT_FILE" ]; then
    echo "worker heartbeat: NOT CHECKED (no HEARTBEAT_FILE in the runtime identity; the profile does not store it)"
  elif hb="$(identity check-heartbeat --heartbeat "$HEARTBEAT_FILE" \
              --worker-pid "$(pid_of worker)" --max-age-seconds 60 2>&1)"; then
    echo "worker heartbeat: OK ($(echo "$hb" | tr '\n' ' '))"
  else
    echo "worker heartbeat: NOT READY - $(echo "$hb" | tr '\n' ' ')"
    NOT_UP=1
  fi
fi

backend_listener="$(listener_on "$BACKEND_PORT")"
recorded_backend="$(pid_of backend)"
if [ -z "$backend_listener" ]; then
  echo "backend port $BACKEND_PORT: no listener"
  NOT_UP=1
elif [ -n "$recorded_backend" ] && echo " $backend_listener " | grep -q " $recorded_backend "; then
  echo "backend port $BACKEND_PORT: pid $backend_listener (the recorded backend)"
else
  # Readiness is probed at BACKEND_ORIGIN, so whoever listens there is what
  # answered - a foreign listener cannot be reported as this candidate being up,
  # even when the recorded backend process is itself verified as running.
  echo "backend port $BACKEND_PORT: held by pid $backend_listener, which is not what the profile recorded"
  echo "       (the readiness line below answers whoever listens there, not necessarily this stack)"
  NOT_UP=1
fi

if ready="$(identity check-ready --url "$BACKEND_ORIGIN/health/ready" 2>&1)"; then
  echo "readiness : $BACKEND_ORIGIN/health/ready OK"
else
  echo "readiness : FAILED - $(echo "$ready" | tr '\n' ' ')"
  NOT_UP=1
fi

if [ -n "${CANDIDATE_DB_PASSWORD:-}" ]; then
  probe_status=0
  probe="$(identity probe --host "$(profile_get DB_HOST)" --port "$(profile_get DB_PORT)" \
            --user "$(profile_get DB_USER)" --dbname "$(profile_get DB_NAME)" 2>/dev/null)" || probe_status=$?
  if [ "$probe_status" -ne 0 ]; then
    # The driver's message can carry connection details, so only the fact and the
    # exit code are reported; the operator reads the cause from their own command.
    echo "schema    : PROBE FAILED (exit $probe_status) against $(profile_get DB_HOST):$(profile_get DB_PORT) - the driver error text is not echoed here"
    NOT_UP=1
  else
    exists="$(echo "$probe" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("exists"))' 2>/dev/null)"
    head_actual="$(echo "$probe" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin).get("head") or "")' 2>/dev/null)"
    if [ "$exists" != "True" ]; then
      echo "schema    : the recorded database $(profile_get DB_NAME) does not exist on that server"
      NOT_UP=1
    elif [ -n "$EXPECTED_HEAD" ] && [ "$head_actual" != "$EXPECTED_HEAD" ]; then
      echo "schema    : $head_actual, profile expected $EXPECTED_HEAD - CHANGED since the stack was planned"
      NOT_UP=1
    else
      echo "schema    : $head_actual"
    fi
  fi
else
  echo "schema    : SKIPPED (CANDIDATE_DB_PASSWORD is not exported; the profile stores no password to read)"
fi

echo ""
if [ "$NOT_UP" -ne 0 ]; then
  echo "STATUS: not fully up - see the lines above."
  exit 1
fi
echo "STATUS: up - every recorded role verified against the profile and the backend is ready."
exit 0
