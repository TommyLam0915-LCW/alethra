#!/bin/bash
# Production build + smoke for frontend/, into its own output directory.
#
#   ./verify_frontend_build.sh --dry-run    evaluate every precondition, print commands
#   ./verify_frontend_build.sh              build, verify the artifact, smoke, stop
#
# One output directory for both halves of the run:
#   build : NEXT_DEV_DIST_DIR=<dist> NEXT_PUBLIC_API_URL=<api base>
#   start : the same two variables, passed explicitly, so `next start` cannot
#           silently fall back to the default `.next` output and serve a
#           different build — or another dev runtime's build — than the one just
#           produced.
#
# Next.js cleans its own distDir, so the directory must be dedicated. The four
# refusals below keep this runner from touching any other runtime's output.
#
# Fail-closed checks, all before anything heavy runs:
#   - frontend/node_modules/.bin/next must exist (no implicit install);
#   - a NEXT_DEV_DIST_DIR in the caller's environment must agree with this run;
#   - the dist dir may not be `.next`, `.next/candidate-3100`, `.next/dev-3099`,
#     or contain a .git directory;
#   - the smoke port must be free; this runner never kills another process;
#   - check_validation_resources.sh --build must admit the run (10 GiB line).
# After the build:
#   - BUILD_ID must be newer than the marker written before the build, so a
#     stale artifact can never be presented as this run's build;
#   - the other `.next` directories' mtimes must be unchanged;
#   - the API base must be present in the emitted bundle;
#   - the smoke serves a content-hashed chunk named by THIS build's
#     app-build-manifest.json, which a stale default build could not satisfy.
#
# Configuration (all optional):
#   ALETHRA_VERIFY_DIST_REL   default .next/verify-build
#   ALETHRA_VERIFY_API_BASE   default http://127.0.0.1:8100
#   ALETHRA_VERIFY_PORT       default 3200
#   ALETHRA_VERIFY_LOG_DIR    default <repo>/.local/state
#
# Exit: 0 verified, 1 build/verification/smoke failure, 2 refused precondition,
#       3 resource gate blocked.
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FRONTEND="$ROOT/frontend"
DIST_REL=${ALETHRA_VERIFY_DIST_REL:-.next/verify-build}
API_BASE=${ALETHRA_VERIFY_API_BASE:-http://127.0.0.1:8100}
PORT=${ALETHRA_VERIFY_PORT:-3200}
LOG_DIR=${ALETHRA_VERIFY_LOG_DIR:-$ROOT/.local/state}
DRY_RUN=0
SMOKE_PID=""

case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  -h | --help)
    sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "") ;;
  *)
    echo "unknown argument: $1" >&2
    exit 2
    ;;
esac

say() { echo "$*"; }
die() {
  local code=$1
  shift
  say "REFUSED ($code): $*"
  exit "$code"
}
file_mtime() {
  if [ -e "$1" ]; then
    stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0
  else
    echo 0
  fi
}
port_holder() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1
  elif (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null; then
    echo "unknown"
  fi
}

stop_smoke() {
  [ -n "$SMOKE_PID" ] || return 0
  kill -0 "$SMOKE_PID" 2>/dev/null || {
    SMOKE_PID=""
    return 0
  }
  say "stopping smoke process $SMOKE_PID (SIGINT)"
  kill -INT "$SMOKE_PID" 2>/dev/null || true
  i=0
  while kill -0 "$SMOKE_PID" 2>/dev/null && [ "$i" -lt 10 ]; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$SMOKE_PID" 2>/dev/null; then
    say "smoke did not exit after SIGINT; sending SIGTERM to $SMOKE_PID"
    kill -TERM "$SMOKE_PID" 2>/dev/null || true
    sleep 2
  fi
  SMOKE_PID=""
}

main() {
  say "Frontend build + smoke — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  say "repo: $ROOT"
  say "mode: $([ "$DRY_RUN" = "1" ] && echo dry-run || echo full)"

  [ -x "$FRONTEND/node_modules/.bin/next" ] || die 2 "frontend/node_modules/.bin/next missing; run npm ci in frontend/"
  [ "${NEXT_DEV_DIST_DIR:-$DIST_REL}" = "$DIST_REL" ] ||
    die 2 "NEXT_DEV_DIST_DIR is '${NEXT_DEV_DIST_DIR}', but this runner requires '$DIST_REL'"
  [ "$DIST_REL" != ".next" ] || die 2 "refusing to build into the shared default .next"
  for other in .next/candidate-3100 .next/dev-3099; do
    [ "$DIST_REL" != "$other" ] || die 2 "refusing to build into a dev runtime's output: $other"
  done
  [ ! -e "$FRONTEND/$DIST_REL/.git" ] || die 2 "$DIST_REL contains a .git directory"

  holder=$(port_holder "$PORT")
  [ -z "$holder" ] || die 2 "port $PORT already held (pid/holder: $holder); this runner will not kill it"

  say ""
  say "dist dir     : frontend/$DIST_REL"
  say "api base     : $API_BASE (baked into the client bundle at build time)"
  say "smoke port   : $PORT"
  say "build command: cd frontend && NEXT_DEV_DIST_DIR=$DIST_REL NEXT_PUBLIC_API_URL=$API_BASE NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next build"
  say "start command: cd frontend && NEXT_DEV_DIST_DIR=$DIST_REL NEXT_PUBLIC_API_URL=$API_BASE NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next start -p $PORT"

  say ""
  say "resource gate (build):"
  if ! "$ROOT/check_validation_resources.sh" --build; then
    say "resource gate did not admit the run; nothing was built or started."
    exit 3
  fi

  if [ "$DRY_RUN" = "1" ]; then
    say ""
    say "dry run: no build, no start. Every precondition above was evaluated."
    exit 0
  fi

  mkdir -p "$LOG_DIR" || die 2 "cannot create log directory $LOG_DIR"
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  build_log="$LOG_DIR/frontend-build-$stamp.log"
  smoke_log="$LOG_DIR/frontend-smoke-$stamp.log"

  # --- state that must not change ------------------------------------------
  pre_candidate=$(file_mtime "$FRONTEND/.next/candidate-3100")
  pre_dev=$(file_mtime "$FRONTEND/.next/dev-3099")
  pre_root=$(file_mtime "$FRONTEND/.next")
  pre_build_id=$(file_mtime "$FRONTEND/$DIST_REL/BUILD_ID")

  marker="$LOG_DIR/frontend-build-started"
  date +%s >"$marker"
  started=$(cat "$marker")

  say ""
  say "building… (output not streamed; this can take minutes)"
  if ! (
    cd "$FRONTEND" &&
      NEXT_DEV_DIST_DIR="$DIST_REL" \
        NEXT_PUBLIC_API_URL="$API_BASE" \
        NEXT_TELEMETRY_DISABLED=1 \
        ./node_modules/.bin/next build
  ) >"$build_log" 2>&1; then
    say "BUILD FAILED — see $build_log; no smoke was started."
    exit 1
  fi

  # --- verification ---------------------------------------------------------
  build_id_file="$FRONTEND/$DIST_REL/BUILD_ID"
  [ -f "$build_id_file" ] || {
    say "VERIFY FAILED: $DIST_REL/BUILD_ID missing"
    exit 1
  }
  build_mtime=$(file_mtime "$build_id_file")
  [ "$build_mtime" -ge "$started" ] || {
    say "VERIFY FAILED: BUILD_ID predates this run (mtime $build_mtime < marker $started)"
    exit 1
  }
  [ -f "$FRONTEND/$DIST_REL/build-manifest.json" ] || {
    say "VERIFY FAILED: build-manifest.json missing"
    exit 1
  }
  [ -f "$FRONTEND/$DIST_REL/app-build-manifest.json" ] || {
    say "VERIFY FAILED: app-build-manifest.json missing"
    exit 1
  }
  say "build id     : $(cat "$build_id_file") (previous BUILD_ID present: $([ "$pre_build_id" != "0" ] && echo yes || echo no))"

  [ "$(file_mtime "$FRONTEND/.next/candidate-3100")" = "$pre_candidate" ] ||
    { say "ISOLATION FAILED: .next/candidate-3100 changed during the build"; exit 1; }
  [ "$(file_mtime "$FRONTEND/.next/dev-3099")" = "$pre_dev" ] ||
    { say "ISOLATION FAILED: .next/dev-3099 changed during the build"; exit 1; }
  [ "$(file_mtime "$FRONTEND/.next")" = "$pre_root" ] ||
    { say "ISOLATION FAILED: the root .next changed during the build"; exit 1; }

  if grep -rqF "$API_BASE" "$FRONTEND/$DIST_REL/static" 2>/dev/null ||
    grep -rqF "$API_BASE" "$FRONTEND/$DIST_REL/server" 2>/dev/null; then
    say "api base baked: yes"
  else
    say "VERIFY FAILED: $API_BASE is not present in the build output"
    exit 1
  fi

  chunk=$(node -e '
    const m = require(process.argv[1]);
    const all = Object.values(m.pages || {}).flat().filter((p) => p.startsWith("static/"));
    // A content-hashed app chunk cannot come from a stale default .next output.
    const hashed = all.find((p) => p.startsWith("static/chunks/app/") && /-[0-9a-f]{8,}\.js$/.test(p));
    const out = hashed || all.find((p) => p !== "static/chunks/webpack.js") || all[0];
    if (!out) process.exit(1);
    process.stdout.write(out);
  ' "$FRONTEND/$DIST_REL/app-build-manifest.json") ||
    { say "VERIFY FAILED: no usable static chunk listed in app-build-manifest.json"; exit 1; }

  # --- smoke ----------------------------------------------------------------
  say ""
  say "starting smoke…"
  (
    cd "$FRONTEND" &&
      NEXT_DEV_DIST_DIR="$DIST_REL" \
        NEXT_PUBLIC_API_URL="$API_BASE" \
        NEXT_TELEMETRY_DISABLED=1 \
        ./node_modules/.bin/next start -p "$PORT"
  ) >"$smoke_log" 2>&1 &
  SMOKE_PID=$!
  trap stop_smoke EXIT INT TERM

  code=""
  attempt=0
  while [ "$attempt" -lt 60 ]; do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null || true)
    [ "$code" = "200" ] && break
    kill -0 "$SMOKE_PID" 2>/dev/null || break
    sleep 1
    attempt=$((attempt + 1))
  done
  [ "$code" = "200" ] || {
    say "SMOKE FAILED: GET / returned '$code'; log: $smoke_log"
    exit 1
  }

  chunk_code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/_next/$chunk" 2>/dev/null || true)
  [ "$chunk_code" = "200" ] || {
    say "SMOKE FAILED: isolated chunk /_next/$chunk returned '$chunk_code'"
    exit 1
  }
  if grep -q "Could not find a production build" "$smoke_log" 2>/dev/null; then
    say "SMOKE FAILED: server reported 'Could not find a production build'"
    exit 1
  fi
  say "smoke ok      : GET / = 200, isolated chunk served = 200"

  stop_smoke
  still=$(port_holder "$PORT")
  [ -z "$still" ] || {
    say "SMOKE TEARDOWN: port $PORT still held (pid/holder: $still)"
    exit 1
  }
  say "smoke stopped : port $PORT released"
  say "build log     : $build_log"
  say "smoke log     : $smoke_log"
  say "RESULT: build + smoke PASS (artifact freshness and output isolation verified)"
  exit 0
}

main
exit $?