#!/bin/bash
# Stop the candidate stack started by start_candidate_stack.sh.
#
# Stops only processes this stack recorded (pid files under .local/candidate).
# The candidate database is left intact so a later start can resume progress.
# Never touches the shared review/runtime containers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOG_DIR="${CANDIDATE_LOG_DIR:-.local/candidate}"
IDENTITY_FILE="$LOG_DIR/runtime-identity.env"

value_of() { [ -f "$IDENTITY_FILE" ] && sed -n "s/^$1=//p" "$IDENTITY_FILE" | head -1 || true; }

BACKEND_PORT="${CANDIDATE_BACKEND_PORT:-$(value_of BACKEND_PORT)}"
FRONTEND_PORT="${CANDIDATE_FRONTEND_PORT:-$(value_of FRONTEND_PORT)}"
BACKEND_PORT="${BACKEND_PORT:-8100}"
FRONTEND_PORT="${FRONTEND_PORT:-3100}"

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

pid_of() { [ -f "$LOG_DIR/$1.pid" ] && tr -d '[:space:]' <"$LOG_DIR/$1.pid" || true; }

stop_pid "backend" "$(pid_of backend)"
stop_pid "worker" "$(pid_of worker)"
stop_pid "frontend" "$(pid_of frontend)"
free_frontend_listener

rm -f "$LOG_DIR/backend.pid" "$LOG_DIR/worker.pid" "$LOG_DIR/frontend.pid"
echo "candidate stack stopped; database and its progress were NOT removed"
