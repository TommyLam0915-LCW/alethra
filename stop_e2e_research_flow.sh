#!/bin/bash
# Tear down ONLY the resources created by start_e2e_research_flow.sh.
#
# Removes: compose project `alethra-e2e` (containers + its ephemeral volume) and
# the recorded host `next dev` process. It never targets the review stack
# (`alethra-unified-dev`), its named volume, or any process it did not start.
#
#   ./stop_e2e_research_flow.sh            keep .local/e2e-credentials.env
#   ./stop_e2e_research_flow.sh --purge    also delete the generated credential file
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ENV_FILE=".local/e2e-credentials.env"
LOG_DIR=".local/e2e"
PID_FILE="$LOG_DIR/frontend.pid"
DEV_PORT=3099
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

# --- host dev server: only signal the process recorded by our start script ---
if [ -f "$PID_FILE" ]; then
  pid="$(tr -d '[:space:]' <"$PID_FILE")"
  if [ -n "$pid" ] && ps -p "$pid" >/dev/null 2>&1; then
    cmd="$(ps -p "$pid" -o command= || true)"
    case "$cmd" in
      *next*dev*)
        echo "stopping recorded E2E frontend dev server (pid $pid)"
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 1 15); do
          ps -p "$pid" >/dev/null 2>&1 || break
          sleep 1
        done
        ps -p "$pid" >/dev/null 2>&1 && { echo "forcing pid $pid"; kill -9 "$pid" 2>/dev/null || true; }
        ;;
      *)
        echo "SKIPPING pid $pid: recorded process is no longer a next dev server ('$cmd')" >&2
        ;;
    esac
  fi
  rm -f "$PID_FILE"
else
  echo "no recorded frontend pid file; nothing to stop on the host"
fi

# Leftover listener check (reported, never killed blindly).
if lsof -nP -iTCP:"$DEV_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "WARNING: port $DEV_PORT still has a listener; inspect before re-running the start script" >&2
fi

# --- disposable compose stack ---
if [ -f "$ENV_FILE" ]; then
  docker compose -p alethra-e2e -f docker-compose.e2e.yml --env-file "$ENV_FILE" \
    down --remove-orphans --volumes
else
  echo "$ENV_FILE missing - removing the E2E containers and volume by name only" >&2
  docker rm -f alethra-e2e-backend-1 alethra-e2e-db-1 >/dev/null 2>&1 || true
  docker volume ls -q --filter "label=com.docker.compose.project=alethra-e2e" |
    xargs -r docker volume rm >/dev/null 2>&1 || true
fi

[ "$PURGE" = "1" ] && rm -f "$ENV_FILE"
echo "E2E research-flow runtime removed (review stack untouched)."
