#!/bin/bash
# Stop the acceptance stack start_acceptance_env.sh started.
#
# A pid file is not proof of identity. Each recorded pid is re-checked against
# the command line it was started with, and a pid whose process no longer looks
# like that is reported and LEFT ALONE - on a shared machine a stale pid file can
# point at someone else's server, and stopping it is not this script's call.
#
# The database container is stopped, not removed: the acceptance data stays
# where it is until the operator asks for it to go, explicitly, with --purge.
#
# Exit: 0 stopped, 2 refused before signalling anything, 3 something recorded
# could not be verified and was left running (the report names it).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${ACCEPTANCE_BACKEND_PORT:-8001}"
FRONTEND_PORT="${ACCEPTANCE_FRONTEND_PORT:-3001}"
CONTAINER="${ACCEPTANCE_CONTAINER_NAME:-alethra-acceptance-db}"
LOG_DIR="${ACCEPTANCE_LOG_DIR:-/tmp}"
PURGE="${1:-}"

case "$PURGE" in
  "" | --purge) ;;
  *) echo "usage: ./stop_acceptance_env.sh [--purge]   (--purge also removes the container and its data)" >&2; exit 2 ;;
esac

refuse() {
  echo "REFUSED (nothing was signalled): $*" >&2
  exit 2
}

command -v docker >/dev/null 2>&1 ||
  refuse "docker is not on PATH; the acceptance container cannot be reached, and no process here was started by this pair of scripts on this machine"

LEFT_RUNNING=0

# stop_recorded <role> <pid file> <text the command line must contain>
stop_recorded() {
  local role="$1" file="$2" expect="$3" pid cmd
  if [ ! -f "$file" ]; then
    echo "$role: no pid file at $file; nothing recorded, nothing stopped"
    return 0
  fi
  pid="$(cat "$file")"
  case "$pid" in
    '' | *[!0-9]*) echo "$role: pid file $file does not hold a pid; left alone"; rm -f "$file"; return 0 ;;
  esac
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$role: pid $pid is not running; removing the stale record"
    rm -f "$file"
    return 0
  fi
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$cmd" in
    *"$expect"*)
      echo "$role: stopping pid $pid"
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || { rm -f "$file"; return 0; }
        sleep 1
      done
      echo "$role: pid $pid ignored SIGTERM; LEFT RUNNING and its record kept - killing it forcibly is an explicit call, not this script's default" >&2
      LEFT_RUNNING=1
      ;;
    *)
      echo "$role: pid $pid is running but its command line does not contain '$expect' (it is: ${cmd:-unknown}); LEFT ALONE, record kept" >&2
      LEFT_RUNNING=1
      ;;
  esac
}

echo "=== Stopping Alethra Acceptance Environment ==="
stop_recorded "backend" "$LOG_DIR/alethra-backend.pid" "app.main:app"
stop_recorded "frontend" "$LOG_DIR/alethra-frontend.pid" "next"

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  if [ "$PURGE" = "--purge" ]; then
    echo "container: removing $CONTAINER with its volume - this deletes the acceptance database"
    docker rm -f "$CONTAINER" >/dev/null
  else
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "container: stopping $CONTAINER (data kept; --purge removes it)"
      docker stop "$CONTAINER" >/dev/null
    else
      echo "container: $CONTAINER is already stopped (data kept; --purge removes it)"
    fi
  fi
else
  echo "container: no $CONTAINER on this machine; nothing to stop"
fi

if [ "$LEFT_RUNNING" -ne 0 ]; then
  echo ""
  echo "INCOMPLETE: at least one recorded process was left running because it could not be matched to what these scripts started. Read the lines above before signalling anything by hand." >&2
  exit 3
fi

echo ""
echo "Acceptance environment stopped."
