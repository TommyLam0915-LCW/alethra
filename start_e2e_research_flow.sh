#!/bin/bash
# Bring up the disposable research-flow E2E runtime.
#
# TWO MODES, AND ONLY ONE OF THEM CAN DELETE DATA
#
#   ./start_e2e_research_flow.sh             RESUME (default, non-destructive)
#   ./start_e2e_research_flow.sh --reset     FRESH — destroys this stack's data
#   E2E_RESET=1 ./start_e2e_research_flow.sh same as --reset
#
# RESUME never runs `down`, never removes or recreates the database volume,
# never builds an image, never seeds, and never stops a service that is already
# running. Stopped containers are brought back with `docker start` (start does
# not recreate, so the volume it points at is the same volume). It then proves
# it is looking at the runtime the previous run recorded — container ids plus
# the db volume name — and that this volume still holds the seeded data
# (served database name, alembic head, row counts of the seed's tables), all
# written to $LOG_DIR/resume-check.log. Anything it cannot prove — no
# containers, image not in the local store, no volume mounted by the db
# container, a different volume than the recorded one, wrong alembic head, an
# empty seeded table — aborts and names `--reset`. Resume never fills in a
# blank environment silently.
#
# FRESH is the old behavior and the only data-destroying path: it stops the
# previous run of this same task, `down --volumes` the project (the database
# volume is anonymous, so `down` removes it even without `--volumes`), builds
# the image when the disk allows, upgrades an empty database, and writes the
# four deterministic synthetic seeds.
#
# Task-owned resources (all removed by stop_e2e_research_flow.sh):
#   - compose project `$E2E_COMPOSE_PROJECT`: containers <project>-{db,backend}-1 + ephemeral volume
#   - backend image `$E2E_COMPOSE_PROJECT/backend:local`, built here from committed files
#   - host process: `next dev` on 127.0.0.1:$E2E_FRONTEND_PORT (pid recorded in $E2E_LOG_DIR/frontend.pid)
#   - local files: $E2E_ENV_FILE (mode 600), $E2E_LOG_DIR/*.log
#
# Self-contained first run: nothing here assumes an application image already
# exists on the host. The backend image is built from the committed
# docker-compose.e2e.yml definition when needed (Docker layer cache reuse only).
# That first run is a FRESH run: pass --reset, because there is nothing to
# resume yet.
#
# Defaults match the ports and project of the original run; override to run a
# second, fully isolated instance from another checkout:
#   E2E_COMPOSE_PROJECT E2E_FRONTEND_PORT E2E_BACKEND_PORT E2E_POSTGRES_DB
#   E2E_ENV_FILE E2E_LOG_DIR
#
# Never touches the review stack (project alethra-unified-dev, DB alethra_unified_browser).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PROJECT="${E2E_COMPOSE_PROJECT:-alethra-e2e}"
ENV_FILE="${E2E_ENV_FILE:-.local/e2e-credentials.env}"
LOG_DIR="${E2E_LOG_DIR:-.local/e2e}"
FRONTEND_PORT="${E2E_FRONTEND_PORT:-3099}"
BACKEND_PORT="${E2E_BACKEND_PORT:-8099}"
BACKEND_DB="${E2E_POSTGRES_DB:-alethra_e2e}"
IMAGE="$PROJECT/backend:local"
COMPOSE="docker compose -p $PROJECT -f docker-compose.e2e.yml --env-file $ENV_FILE"
FREE_DB_BUILD_MIB=10240   # 10 GiB operational line: below it, no image builds
# The revision a fresh upgrade ends on, and therefore the revision a volume
# carrying the seeded schema must report. Kept next to the fresh path's grep so
# the two cannot drift apart silently.
EXPECTED_ALEMBIC_HEAD="l6m7n8o9p0q1"
IDENTITY_FILE="$LOG_DIR/runtime-identity.env"
RESUME_LOG="$LOG_DIR/resume-check.log"

fail() { echo "E2E SETUP ABORTED: $*" >&2; exit 1; }

RESET=0
[ "${1:-}" = "--reset" ] && RESET=1
[ "${E2E_RESET:-0}" = "1" ] && RESET=1

case "$BACKEND_DB" in
  alethra_e2e*) ;;
  *) fail "E2E_POSTGRES_DB must start with 'alethra_e2e' (the seed script's guard)" ;;
esac

export E2E_BACKEND_IMAGE="$IMAGE"
export E2E_BACKEND_PORT="$BACKEND_PORT"
export E2E_FRONTEND_PORT="$FRONTEND_PORT"

mkdir -p "$(dirname "$ENV_FILE")" "$LOG_DIR"

# --- credentials: generated once, 600, never echoed and never in argv ---
# Only the FRESH path may create them: a generated file that does not match the
# stack on disk would make `docker compose` interpolate the wrong values and
# the resume checks would then be describing a runtime nobody started.
ensure_env_file() {
  if [ -f "$ENV_FILE" ]; then
    echo "reusing existing $ENV_FILE"
    return
  fi
  echo "generating synthetic E2E credentials into $ENV_FILE"
  umask 077
  {
    printf 'E2E_POSTGRES_USER=alethra_e2e\n'
    printf 'E2E_POSTGRES_DB=%s\n' "$BACKEND_DB"
    printf 'E2E_POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'E2E_JWT_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
    printf 'E2E_EMAIL=e2e.research-flow@alethra.io\n'
    printf 'E2E_PASSWORD=%s\n' "$(openssl rand -hex 16)"
  } >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

if [ "$RESET" = "1" ]; then
  ensure_env_file
else
  [ -f "$ENV_FILE" ] || fail "RESUME needs $ENV_FILE to interpolate the existing stack's compose definition, and it will not generate a new one over a running runtime - use --reset for an explicit (data-destroying) first build"
fi
[ "$(stat -f %Lp "$ENV_FILE")" = "600" ] || fail "$ENV_FILE must be mode 600"

# --- read-only probes ---------------------------------------------------------

# Query this project's own db container. User and database come from the
# container's own environment, so no credential is read on the host, put in a
# command line, or written to a log.
db_sql() {
  docker exec -i -e ALETHRA_SQL="$2" "$1" \
    sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$ALETHRA_SQL"'
}

container_state() { docker inspect --format '{{.State.Status}}' "$1" 2>/dev/null || echo missing; }

# The anonymous volume holding the data is identified by the container that
# mounts it, not by a label: `docker run`-style anonymous volumes carry only
# `com.docker.volume.anonymous`.
db_volume_of() {
  docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{" "}}{{end}}{{end}}' "$1" \
    | tr ' ' '\n' | grep -v '^$' | head -1 || true
}

wait_for_db_health() {
  local id="$1" health=""
  for _ in $(seq 1 60); do
    health=$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || true)
    [ "$health" = "healthy" ] && return 0
    sleep 2
  done
  fail "db did not become healthy (last status: ${health:-unknown})"
}

wait_for_backend() {
  local ready=""
  for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then ready=yes; break; fi
    sleep 2
  done
  [ -n "$ready" ] || fail "backend did not answer /health within 180s"
}

# --- identity: record what a successful run is actually using -----------------
write_identity() {
  local db_id="$1" backend_id="$2" db_volume="$3"
  {
    printf 'UPDATED_AT=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
    printf 'COMPOSE_PROJECT=%s\n' "$PROJECT"
    printf 'BACKEND_DB=%s\n' "$BACKEND_DB"
    printf 'DB_CONTAINER=%s\n' "$db_id"
    printf 'BACKEND_CONTAINER=%s\n' "$backend_id"
    printf 'DB_VOLUME=%s\n' "$db_volume"
  } >"$IDENTITY_FILE"
  chmod 600 "$IDENTITY_FILE"
}

recorded_value() {
  [ -f "$IDENTITY_FILE" ] || return 0
  sed -n "s/^$1=//p" "$IDENTITY_FILE" | head -1
}

# A recreated container on the same volume is still the same data, so a
# container-id change is disclosed rather than fatal. A volume change is fatal:
# it means the data this run would serve is not the data the last run seeded.
compare_recorded_identity() {
  local db_id="$1" backend_id="$2" db_volume="$3"
  local r_volume r_db r_backend
  r_volume="$(recorded_value DB_VOLUME)"
  r_db="$(recorded_value DB_CONTAINER)"
  r_backend="$(recorded_value BACKEND_CONTAINER)"
  if [ -z "$r_volume" ]; then
    echo "note: no recorded runtime identity in $IDENTITY_FILE; data identity will be established from the volume's contents only"
    return 0
  fi
  if [ "$r_volume" != "$db_volume" ]; then
    fail "database volume identity changed: recorded $r_volume, this stack mounts $db_volume - refusing to treat a different volume as the seeded one (use --reset to build a deliberate new one)"
  fi
  [ -z "$r_db" ] || [ "$r_db" = "$db_id" ] || \
    echo "note: db container was recreated since the last recorded run (recorded ${r_db:0:12}, now ${db_id:0:12}); volume $db_volume is unchanged"
  [ -z "$r_backend" ] || [ "$r_backend" = "$backend_id" ] || \
    echo "note: backend container was recreated since the last recorded run (recorded ${r_backend:0:12}, now ${backend_id:0:12})"
  echo "volume identity confirmed against the recorded run: $db_volume"
}

# --- existing-data checks: the volume must prove it holds the seeded schema ---
verify_existing_data() {
  local id="$1" db_volume="$2" backend_id="$3"
  local served head table rows
  served="$(db_sql "$id" "select current_database()")" || fail "could not query the db container's served database"
  [ "$served" = "$BACKEND_DB" ] || fail "db container $id serves database '$served', this run is configured for '$BACKEND_DB' - not the same runtime"
  head="$(db_sql "$id" "select version_num from alembic_version")" \
    || fail "$BACKEND_DB has no alembic_version row - the schema is not the one this branch upgrades; use --reset"
  [ "$head" = "$EXPECTED_ALEMBIC_HEAD" ] || fail "alembic head in '$BACKEND_DB' is '$head', expected '$EXPECTED_ALEMBIC_HEAD' - refusing to run specs against a schema this work does not define (use --reset for an explicit rebuild)"

  : >"$RESUME_LOG"
  {
    echo "# read-only resume verification - no container stopped, no volume recreated, no seed written"
    echo "# ${PROJECT} / ${BACKEND_DB}"
    echo "db_container=$id"
    echo "backend_container=$backend_id"
    echo "db_volume=$db_volume"
    echo "served_database=$served"
    echo "alembic_head=$head"
  } >>"$RESUME_LOG"

  for table in users experiments simulation_sessions paper_portfolios market_data_metadata market_data_bars; do
    rows="$(db_sql "$id" "select count(*) from $table")"
    echo "rows_$table=$rows" >>"$RESUME_LOG"
    if [ "${rows:-0}" -le 0 ]; then
      fail "$BACKEND_DB is reachable but table '$table' has 0 rows - this volume is not the seeded research-flow dataset, and resume will not reseed over it (use --reset)"
    fi
  done
  echo "existing data verified against $BACKEND_DB (identifiers + row counts: $RESUME_LOG)"
}

# --- frontend: reuse, adopt, or start - never clobber someone else's port -----
frontend_listener() { lsof -nP -iTCP:"$FRONTEND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

frontend_answers() { curl -sf -o /dev/null "http://127.0.0.1:$FRONTEND_PORT/login" 2>/dev/null; }

start_dev_server() {
  [ -d frontend/node_modules ] || fail "frontend/node_modules missing - run npm ci in frontend first"
  LOG_DIR_ABS="$(cd "$LOG_DIR" && pwd)"
  (
    cd frontend
    # Build output is per-port: two `next dev` servers on this directory would
    # otherwise share .next, and whichever compiled last would decide the
    # NEXT_PUBLIC_API_URL every browser is served.
    NEXT_DEV_DIST_DIR=".next/dev-$FRONTEND_PORT" \
    NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT" \
      nohup npx next dev -p "$FRONTEND_PORT" >"$LOG_DIR_ABS/frontend-dev.log" 2>&1 &
    echo $! >"$LOG_DIR_ABS/frontend.pid"
  )
  echo "waiting for frontend on 127.0.0.1:$FRONTEND_PORT..."
  local ready=""
  for _ in $(seq 1 90); do
    if frontend_answers; then ready=yes; break; fi
    sleep 2
  done
  [ -n "$ready" ] || fail "frontend dev server did not answer /login within 180s"
}

ensure_frontend() {
  local pid="" listener cmd cwd
  [ -f "$LOG_DIR/frontend.pid" ] && pid="$(tr -d '[:space:]' <"$LOG_DIR/frontend.pid")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && frontend_answers; then
    echo "reusing recorded E2E frontend dev server (pid $pid)"
    return
  fi
  listener="$(frontend_listener)"
  if [ -n "$listener" ]; then
    # `npx next dev` records the wrapper pid; the server that actually listens
    # is a child. Adopt it only when it is provably a next server of THIS
    # checkout's frontend directory, so resume never reuses another project's UI.
    cmd="$(ps -p "$listener" -o command= 2>/dev/null || true)"
    cwd="$(lsof -a -d cwd -p "$listener" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    case "$cmd" in
      *next*)
        if [ "$cwd" = "$ROOT/frontend" ] && frontend_answers; then
          echo "$listener" >"$LOG_DIR/frontend.pid"
          echo "adopting existing E2E frontend dev server (pid $listener, cwd $cwd); its NEXT_PUBLIC_API_URL was set by whoever started it"
          return
        fi
        ;;
    esac
    fail "loopback port $FRONTEND_PORT is served by pid $listener ($cmd, cwd ${cwd:-unknown}), which is not a next server of $ROOT/frontend - free it or override E2E_FRONTEND_PORT"
  fi
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "recorded frontend pid $pid is alive but not answering /login yet; waiting on it"
    local ready=""
    for _ in $(seq 1 90); do
      if frontend_answers; then ready=yes; break; fi
      sleep 2
    done
    [ -n "$ready" ] || fail "recorded frontend pid $pid did not start answering /login within 180s"
    return
  fi
  start_dev_server
}

# A `next dev` server can outlive the wrapper pid recorded in frontend.pid, and
# the stop script only signals the recorded pid. Free the port only for a
# listener this task can prove it started: a next server whose cwd is THIS
# checkout's frontend directory. Anything else is left alone and the port
# preflight below fails instead.
free_this_task_frontend_port() {
  local listener cmd cwd
  listener="$(frontend_listener)"
  [ -n "$listener" ] || return 0
  cmd="$(ps -p "$listener" -o command= 2>/dev/null || true)"
  cwd="$(lsof -a -d cwd -p "$listener" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  case "$cmd" in
    *next*) ;;
    *) return 0 ;;
  esac
  [ "$cwd" = "$ROOT/frontend" ] || return 0
  echo "stopping this task's frontend dev server still holding port $FRONTEND_PORT (pid $listener)"
  kill "$listener" 2>/dev/null || true
  for _ in $(seq 1 15); do
    kill -0 "$listener" 2>/dev/null || break
    sleep 1
  done
  kill -0 "$listener" 2>/dev/null && { echo "forcing pid $listener"; kill -9 "$listener" 2>/dev/null || true; }
}

# --- FRESH: the only data-destroying path -------------------------------------
fresh_runtime() {
  echo "mode: FRESH (--reset) - this removes this stack's containers and its anonymous database volume, then reseeds"

  # idempotent re-run: tear down a previous run of this same task
  if { [ -f "$LOG_DIR/frontend.pid" ] || docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" | grep -q .; }; then
    echo "previous E2E runtime detected - stopping it first (task-owned resources only)"
    E2E_COMPOSE_PROJECT="$PROJECT" E2E_ENV_FILE="$ENV_FILE" E2E_LOG_DIR="$LOG_DIR" \
    E2E_FRONTEND_PORT="$FRONTEND_PORT" E2E_BACKEND_PORT="$BACKEND_PORT" \
      ./stop_e2e_research_flow.sh >/dev/null 2>&1 || echo "note: stop script reported an issue; continuing with a fresh start"
  fi
  free_this_task_frontend_port

  # preflight: ports must be unused, nothing shared gets clobbered
  for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      fail "loopback port $port is already in use - free it or override E2E_FRONTEND_PORT / E2E_BACKEND_PORT"
    fi
  done

  # preflight: disk line, then build this project's own backend image
  avail_mib=$(/bin/df -km / | awk 'NR==2 {print $4}')
  echo "free space: ${avail_mib} MiB (image-build stop line: ${FREE_DB_BUILD_MIB} MiB)"
  if [ "$avail_mib" -lt "$FREE_DB_BUILD_MIB" ]; then
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
      echo "below the build line but $IMAGE already present - reusing it, no build"
    else
      fail "$IMAGE absent and free space below the 10 GiB build line"
    fi
  else
    echo "building $IMAGE from committed files (single build, no concurrent builds)"
    $COMPOSE build backend
  fi

  # database + backend (fresh, empty volume every run)
  $COMPOSE down --remove-orphans --volumes >/dev/null 2>&1 || true
  $COMPOSE up -d db
  echo "waiting for db..."
  db_id="$($COMPOSE ps -q db)"
  [ -n "$db_id" ] || fail "compose did not report a db container for project $PROJECT"
  wait_for_db_health "$db_id"

  # `alembic upgrade head` runs as the backend entrypoint against the empty
  # disposable database; the log is kept as fresh-upgrade migration evidence.
  $COMPOSE up -d backend
  echo "waiting for backend on 127.0.0.1:$BACKEND_PORT (alembic upgrade head runs first)..."
  wait_for_backend
  $COMPOSE logs --no-color backend >"$LOG_DIR/backend-upgrade.log" 2>&1
  grep -q "Running upgrade k5l6m7n8o9p0 -> l6m7n8o9p0q1" "$LOG_DIR/backend-upgrade.log" \
    || fail "expected intraday migration step not observed in $LOG_DIR/backend-upgrade.log"
  echo "migrations applied to head $EXPECTED_ALEMBIC_HEAD (log: $LOG_DIR/backend-upgrade.log)"

  # --- deterministic synthetic seed (guarded to the alethra_e2e database) ---
  $COMPOSE exec -T backend python scripts/seed_e2e_research_flow.py | tee "$LOG_DIR/seed.log"
  # Rule-triggered-flow seed: locked rule experiment + its OWN synthetic daily bars
  # (timeframe/provider/timezone/checksum printed), so the rule-flow spec never
  # depends on ad-hoc bars or any pre-existing account/session/portfolio.
  $COMPOSE exec -T backend python scripts/seed_e2e_rule_flow.py | tee "$LOG_DIR/seed-rule-flow.log"
  # Intraday rule-flow seed: a SEPARATE locked experiment whose spec declares
  # timeframe=5m + its OWN synthetic 5-minute bars under provider 'demo-5m' (the
  # timeframe-isolation key), so the intraday spec drives the real per-bar replay
  # without touching the daily seed's experiment or bars.
  $COMPOSE exec -T backend python scripts/seed_e2e_intraday_flow.py | tee "$LOG_DIR/seed-intraday-flow.log"
  # Order-lifecycle seed: a THIRD locked experiment on its OWN disjoint daily bar range
  # (DEMO-ALPHA, provider 'demo', 2024-02-01..2024-02-13) plus a watchlist row carrying
  # that symbol, so the lifecycle spec can reach the workspace chart without the
  # instrument API. Its rule is pinned passive (threshold_pct=0.99) so it never emits an
  # entry signal — every fill this suite asserts must be a manual one.
  $COMPOSE exec -T backend python scripts/seed_e2e_order_lifecycle.py | tee "$LOG_DIR/seed-order-lifecycle.log"

  backend_id="$($COMPOSE ps -q backend)"
  write_identity "$db_id" "$backend_id" "$(db_volume_of "$db_id")"
}

# --- RESUME: start what exists, prove what it holds, touch nothing else -------
resume_runtime() {
  echo "mode: RESUME (default) - no down, no volume recreate, no image build, no seed, no stop of a running service"

  db_id="$($COMPOSE ps -q db)"
  backend_id="$($COMPOSE ps -q backend)"
  [ -n "$db_id" ] || fail "compose project $PROJECT has no db container - there is nothing to resume; run ./start_e2e_research_flow.sh --reset to build one deliberately"
  [ -n "$backend_id" ] || fail "compose project $PROJECT has no backend container - there is nothing to resume; run ./start_e2e_research_flow.sh --reset to build one deliberately"

  # image identity: the container must point at an image the host still has
  backend_image="$(docker inspect --format '{{.Image}}' "$backend_id" 2>/dev/null || true)"
  [ -n "$backend_image" ] || fail "backend container $backend_id cannot be inspected - image identity unknown"
  docker image inspect "$backend_image" >/dev/null 2>&1 \
    || fail "backend container $backend_id references image ${backend_image:0:19}, which is not in the local image store - resume will not build over an unknown image; use --reset"
  image_tags="$(docker image inspect --format '{{range .RepoTags}}{{.}} {{end}}' "$backend_image")"
  echo "backend image identity: ${image_tags:-<untagged: $backend_image>}"

  # volume identity: the db container must mount a volume that still exists
  db_volume="$(db_volume_of "$db_id")"
  [ -n "$db_volume" ] || fail "db container $db_id mounts no volume - its data identity cannot be established, so resume refuses to assume it; use --reset"
  docker volume inspect "$db_volume" >/dev/null 2>&1 \
    || fail "volume $db_volume (mounted by $db_id) is not in the local volume store - data identity unknown; use --reset"

  compare_recorded_identity "$db_id" "$backend_id" "$db_volume"

  # stopped containers start in place; a running one is never restarted
  for pair in "db:$db_id" "backend:$backend_id"; do
    name="${pair%%:*}"; id="${pair#*:}"
    state="$(container_state "$id")"
    case "$state" in
      running) echo "$name ($id) already running - left untouched" ;;
      exited|created|dead)
        echo "starting stopped $name container ${id:0:12} with docker start (no recreate, same volume, same image)"
        docker start "$id" >/dev/null || fail "docker start failed for $name container $id"
        ;;
      *) fail "$name container $id is in state '$state', which resume does not handle - inspect it before continuing (nothing was stopped or removed)" ;;
    esac
  done

  wait_for_db_health "$db_id"
  verify_existing_data "$db_id" "$db_volume" "$backend_id"

  if ! curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      fail "loopback port $BACKEND_PORT is in use by a process outside compose project $PROJECT - resume will not displace it"
    fi
  fi
  wait_for_backend
  echo "backend answering /health on 127.0.0.1:$BACKEND_PORT"
  echo "note: if the backend had to be started, its entrypoint re-ran 'alembic upgrade head' against the EXISTING database (idempotent, no drop/recreate); resume never recreates the volume or reseeds"

  write_identity "$db_id" "$backend_id" "$db_volume"
}

# --- dispatch -----------------------------------------------------------------
if [ "$RESET" = "1" ]; then
  fresh_runtime
  start_dev_server
else
  resume_runtime
  ensure_frontend
fi

[ -f "$ENV_FILE" ] || fail "$ENV_FILE missing"
ENV_FILE_ABS="$ROOT/$ENV_FILE"
LOGIN_EMAIL="$(sed -n 's/^E2E_EMAIL=//p' "$ENV_FILE" | head -1)"

echo
echo "E2E research-flow runtime ready (project $PROJECT, mode: $([ "$RESET" = "1" ] && echo FRESH || echo RESUME)):"
echo "  frontend : http://localhost:$FRONTEND_PORT   (host next dev = development server, not a production build)"
echo "  backend  : http://localhost:$BACKEND_PORT"
echo "  login    : ${LOGIN_EMAIL:-see $ENV_FILE_ABS}   password: $ENV_FILE_ABS (key E2E_PASSWORD, value never printed)"
echo "  run spec : cd frontend && E2E_FRONTEND_BASE=http://localhost:$FRONTEND_PORT \\"
echo "             E2E_CRED_FILE=$ENV_FILE_ABS \\"
echo "             npx playwright test --config playwright.research-flow.config.ts"
echo "  rule spec: cd frontend && E2E_FRONTEND_BASE=http://localhost:$FRONTEND_PORT \\"
echo "             E2E_CRED_FILE=$ENV_FILE_ABS \\"
echo "             npx playwright test --config playwright.rule-flow.config.ts"
echo "  lifecycle spec (提交→掛單→下一 bar 成交→結算, real UI): cd frontend && \\"
echo "             E2E_FRONTEND_BASE=http://localhost:$FRONTEND_PORT \\"
echo "             E2E_CRED_FILE=$ENV_FILE_ABS \\"
echo "             npx playwright test --config playwright.order-lifecycle.config.ts"
echo "  reseed   : the lifecycle spec is serial and consumes its seed. Replay it without touching"
echo "             the running stack (verified repeatable, same checksum, deletes nothing):"
echo "             $COMPOSE exec -T backend python scripts/seed_e2e_order_lifecycle.py"
echo "             ./start_e2e_research_flow.sh --reset is the destructive alternative: it stops the"
echo "             stack, removes its volumes and reseeds from empty - only for a disposable run."
echo "  teardown : E2E_COMPOSE_PROJECT=$PROJECT E2E_ENV_FILE=$ENV_FILE E2E_LOG_DIR=$LOG_DIR \\"
echo "             E2E_FRONTEND_PORT=$FRONTEND_PORT E2E_BACKEND_PORT=$BACKEND_PORT ./stop_e2e_research_flow.sh"
