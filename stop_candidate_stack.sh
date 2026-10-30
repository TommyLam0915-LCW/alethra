#!/bin/bash
# Stop the candidate stack recorded by start_candidate_stack.sh.
#
# A recorded pid is not proof of identity: each process is checked against the
# persistent candidate profile (role command line, and for backend/worker the
# DATABASE_URL it was started with) before anything is signalled. A pid that no
# longer matches is reported and left alone.
#
# The candidate database is left intact so a later start can resume progress.
# Never touches the shared review/runtime containers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

STATE_DIR="${CANDIDATE_STATE_DIR:-$ROOT/.local/candidate}"
PROFILE="${CANDIDATE_PROFILE:-$STATE_DIR/candidate-profile.env}"
HELPER="$ROOT/backend/scripts/candidate_identity.py"

fail() { echo "CANDIDATE STOP ABORTED: $*" >&2; exit 1; }

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

[ -f "$PROFILE" ] || fail "no persistent candidate profile at $PROFILE; refusing to stop processes by pid file alone"
profile_get() { sed -n "s/^$1=//p" "$PROFILE" | head -1; }
CHECKOUT_DIR="$(profile_get CHECKOUT_DIR)"
[ "$CHECKOUT_DIR" = "$ROOT" ] || fail "candidate profile $PROFILE belongs to $CHECKOUT_DIR, not $ROOT"
LOG_DIR="$(profile_get LOG_DIR)"
FRONTEND_PORT="$(profile_get FRONTEND_PORT)"
[ -n "$LOG_DIR" ] && [ -n "$FRONTEND_PORT" ] || fail "candidate profile $PROFILE is missing LOG_DIR or FRONTEND_PORT"

pid_of() { [ -f "$LOG_DIR/$1.pid" ] && tr -d '[:space:]' <"$LOG_DIR/$1.pid" || true; }

stop_pid() {
  local name="$1" pid="$2"
  [ -n "$pid" ] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name pid $pid already stopped"
    return 0
  fi
  echo "stopping $name pid $pid (SIGTERM)"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 15); do
    kill -0 "$pid" 2>/dev/null || { echo "$name stopped"; return 0; }
    sleep 1
  done
  echo "$name did not exit after 15s - sending SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
}

stop_role() {
  local role="$1" pid out
  pid="$(pid_of "$role")"
  [ -n "$pid" ] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$role pid $pid already stopped"
    rm -f "$LOG_DIR/$role.pid"
    return 0
  fi
  out="$("$PYTHON" "$HELPER" check-process --profile "$PROFILE" --role "$role" --pid "$pid" 2>&1)" || {
    echo "$role pid $pid could not be checked; left alone: $out"
    return 0
  }
  case "$out" in
    *"VERIFIED=yes"*)
      stop_pid "$role" "$pid"
      rm -f "$LOG_DIR/$role.pid"
      ;;
    *"REASON=not-running"*)
      echo "$role pid $pid already stopped"
      rm -f "$LOG_DIR/$role.pid"
      ;;
    *"REASON=not-this-role"*)
      echo "$role pid $pid is not this candidate's process (stale pid file); left alone"
      rm -f "$LOG_DIR/$role.pid"
      ;;
    *)
      echo "$role pid $pid does not match the saved candidate identity; left alone: $out"
      ;;
  esac
}

# next dev records the wrapper pid; the listener can be its child. Only adopt a
# listener whose cwd is THIS checkout's frontend directory.
free_frontend_listener() {
  local pid cmd cwd
  pid="$(lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  [ -n "$pid" ] || return 0
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  cwd="$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)"
  case "$cmd" in
    *next*) ;;
    *) echo "port $FRONTEND_PORT is held by unrelated pid $pid ($cmd) - left alone"; return 0 ;;
  esac
  [ "$cwd" = "$ROOT/frontend" ] || { echo "port $FRONTEND_PORT belongs to another checkout ($cwd) - left alone"; return 0; }
  stop_pid "frontend listener" "$pid"
}

stop_role backend
stop_role worker
stop_role frontend
free_frontend_listener

echo "candidate stack stopped; database and its progress were NOT removed"
