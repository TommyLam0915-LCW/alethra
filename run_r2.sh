#!/bin/bash
# R2 — PostgreSQL / restart / account-isolation close-loop for the research-run lane.
#
# Authored 2026-11-15. Only `guard` has been executed (2026-11-16, exit 0,
# $ROOT/.r2/evidence/guard.log): it installs nothing, starts nothing and makes
# no connection. Every stage that writes (install, provision, migrate, tests,
# api-loop) still needs its own approval.
#
# What the 2026-11-16 guard run changed about this package: the pgserver
# binaries are already on disk ($REPO/.local/pgvenv, PostgreSQL 16.2), so the
# install stage resolves them instead of pip-installing, and no install
# approval is needed. The cluster those binaries also served in 2026-11-08
# ($REPO/.local/pgdata-16, which holds ai_investment_lab) is not reused: the
# provision stage refuses any PGDATA under a .local/pgdata path.
#
# Preservation rules baked in (owner instruction, 2026-11-15):
#   * nothing here deletes PGDATA or the evidence directory, ever;
#   * no retry, no recreate-on-failure, no re-run loop: the first failure is the result;
#   * shutdown is `pg_ctl stop -m fast`, never `-m immediate`;
#   * no GRANT beyond this package's own role in this package's own cluster, and no
#     signal is sent to a process whose PID this script did not record;
#   * cleanup of PGDATA / evidence / role is a separate package.
#
# The target is not free. backend/test_safety_gate.py whitelists database names
# (:25-30), blacklists production names (:33-38), accepts only port 5432 on loopback
# (:41-45) and refuses superuser/CREATEDB/CREATEROLE/BYPASSRLS roles (:125-145), while
# are alethra_test and alethra_owner_test. The prefix rule is not one line: the pg lane
# exports ALETHRA_TEST_REQUIRED_DB_PREFIX=alethra_ (scripts/run_tests.sh:130), hands it to
# check_db_env.analyse (:111, :160) which rejects a name without it (scripts/check_db_env.py:116-119).
# `guard` proves all of that before anything installs.
set -euo pipefail

# Derived from where this script actually sits, because the file now lives in
# the repo: a hardcoded home directory made every stage describe a machine
# nobody else is on. REPO is the script's own directory, ROOT its parent, so
# the working set stays outside the checkout exactly as §21.5(a) requires.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$REPO")"
BACKEND="$REPO/backend"
[ -f "$BACKEND/app/main.py" ] || { echo "FAIL: $REPO has no backend/app/main.py; run this script from the alethra repo root" >&2; exit 1; }
BACKEND_PY="$BACKEND/.venv/bin/python"          # app / alembic / uvicorn; this package never installs into it
PKG="$ROOT/.r2-package"                          # this script plus the values it measured
WORK="$ROOT/.r2"                                 # runtime working set; repo-external, created here
PGDATA="$WORK/pgdata"
EVIDENCE="$WORK/evidence"
VENV="$WORK/venv"                                # pgserver's own interpreter

PG_BOOTSTRAP_USER="r2_bootstrap"                 # initdb superuser: never used to connect
PG_HOST="127.0.0.1"
PG_PORT="5432"                                   # ALLOWED_INSTANCES accepts nothing else
PG_DB="alethra_test"                             # whitelist ∩ `alethra_` prefix
PG_ROLE="alethra_r2"                             # LOGIN, non-superuser: what the gate accepts
API_HOST="127.0.0.1"
API_PORT="8010"
PGSERVER_PIN="${R2_PGSERVER_PIN:-}"              # empty = version UNKNOWN until resolved; never guessed

RUN_URL="postgresql://$PG_ROLE@$PG_HOST:$PG_PORT/$PG_DB"
BOOT_URL="postgresql://$PG_BOOTSTRAP_USER@$PG_HOST:$PG_PORT/postgres"
PGBIN_FILE="$PKG/pgbin"

stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
say() { printf '%s %s\n' "$(stamp)" "$1"; }
fail() { say "FAIL: $1" >&2; say "first evidence retained: $EVIDENCE" >&2; exit 1; }

require_absent() {
  # Refuse to write over anything that already exists.
  if [ -e "$1" ]; then fail "$1 already exists; this package will not overwrite or reuse it"; fi
}

record_pipbin() {
  if [ ! -f "$PGBIN_FILE" ]; then
    fail "no resolved postgres bin dir ($PGBIN_FILE): run the install stage first"
  fi
  PGBIN="$(cat "$PGBIN_FILE")"
  if [ ! -x "$PGBIN/initdb" ] || [ ! -x "$PGBIN/pg_ctl" ] || [ ! -x "$PGBIN/psql" ]; then
    fail "resolved bin dir has no initdb/pg_ctl/psql: $PGBIN"
  fi
}

# pgserver's PostgreSQL binaries may already be on disk: an earlier lane
# installed the wheel into $REPO/.local/pgvenv and its cluster data went to
# $REPO/.local/pgdata-16 (see ENVIRONMENT_VERIFICATION_REPORT.md, 2026-11-07).
# Reusing the *binaries* is read-only and needs no network, no new interpreter
# and no approval to install. Reusing that *data directory* is a different
# thing entirely and never happens here: stage_provision refuses it.
ON_DISK_PGBIN_SEARCH="$REPO/.local/pgvenv $ROOT/.local/pgvenv $VENV $BACKEND/.venv"

resolve_existing_initdb() {
  local dir found
  for dir in $ON_DISK_PGBIN_SEARCH; do
    [ -d "$dir" ] || continue
    found="$(find "$dir" -type f -name initdb -path '*/bin/*' 2>/dev/null | head -1 || true)"
    if [ -n "$found" ] && [ -x "$found" ]; then
      printf '%s\n' "$found"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------- guard ----
stage_guard() {
  # Read-only: proves the intended target is admissible before anything is installed,
  # and prints the environment that would otherwise surprise us later. No connection,
  # no install, no start.
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/guard.log"; : > "$OUT"
  {
    say "repo=$REPO"
    say "backend interpreter: $BACKEND_PY"
    say "postgres on PATH: $(command -v postgres || echo none)"
    say "postgres processes: $(pgrep -f '[p]ostgres' | wc -l | tr -d ' ')"
    say "port $PG_PORT listeners: $(lsof -nP -iTCP:$PG_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
    say "port $API_PORT listeners: $(lsof -nP -iTCP:$API_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
    say "PGDATA present: $([ -e "$PGDATA" ] && echo yes || echo no)"
    say "pgserver pin: ${PGSERVER_PIN:-UNKNOWN}"
    # Whether an install is needed at all is decided by what is already on disk,
    # so the guard prints that before anyone approves an install stage.
    say "initdb already on disk: $(resolve_existing_initdb || echo none)"
    say "2026-11-08 cluster data dir (this package never initialises in it): $([ -e "$REPO/.local/pgdata-16" ] && echo present || echo absent)"
  } 2>&1 | tee -a "$OUT"
  if [ ! -x "$BACKEND_PY" ]; then fail "backend/.venv interpreter missing"; fi
  if [ "$(lsof -nP -iTCP:$PG_PORT -sTCP:LISTEN 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')" != "0" ]; then
    fail "something already listens on 127.0.0.1:$PG_PORT — this package does not take over a foreign cluster"
  fi

  # The runner's own coherence check, dry: the four DB variables it derives from one URL.
  ( cd "$BACKEND" && "$BACKEND_PY" - "$RUN_URL" <<'R2PY' 2>&1 | tee -a "$OUT"
import sys
from scripts.check_db_env import analyse
url = sys.argv[1]
env = {n: url for n in ("DATABASE_URL", "ALETHRA_TEST_DB",
                       "ALETHRA_PG_DATABASE_URL", "ALETHRA_TEST_POSTGRES_URL")}
a = analyse(env, required_prefix="alethra_")
print("check_db_env.analyse ->", a.mode, "|", a.summary, "| errors:", a.errors or "none")
sys.exit(1 if a.errors else 0)
R2PY
  ) || fail "the intended target is not a coherent PG test environment"

  # The static half of the safety gate, applied to the URL this package will use.
  ( cd "$BACKEND" && "$BACKEND_PY" - "$RUN_URL" <<'R2PY' 2>&1 | tee -a "$OUT"
import sys
from urllib.parse import urlparse
from test_safety_gate import (ALLOWED_INSTANCES, ALLOWED_TEST_DATABASES,
                              FORBIDDEN_DATABASES)
parsed = urlparse(sys.argv[1])
db = parsed.path.lstrip("/")
print("whitelist        ", sorted(ALLOWED_TEST_DATABASES))
print("blacklist        ", sorted(FORBIDDEN_DATABASES))
print("allowed instances", sorted(ALLOWED_INSTANCES))
print("target           ", f"{parsed.hostname}:{parsed.port}/{db} as {parsed.username}")
assert db in ALLOWED_TEST_DATABASES, "database not whitelisted"
assert db not in FORBIDDEN_DATABASES, "database forbidden"
assert (parsed.hostname, parsed.port) in ALLOWED_INSTANCES, "host/port not allowed"
print("static gate: PASS")
R2PY
  ) || fail "target rejected by the static part of test_safety_gate"

  # The .env hazard: app.config reads env_file=".env" (config.py:430) relative to the
  # backend directory, and that file currently names a blacklisted database and turns on
  # live trading. Command-line variables win — measured 2026-11-15, not assumed.
  ( cd "$BACKEND" && "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
from app.config import get_settings
s = get_settings()
print(".effective settings with .env in force: database=%s provider=%s live_trading=%s "
      "learning_scheduler=%s auto_collection=%s auth_bypass=%s"
      % (s.database_url.split("@")[-1], s.data_provider, s.live_trading_enabled,
         s.enable_learning_scheduler, s.auto_collection_enabled,
         getattr(s, "research_auth_bypass_enabled", "n/a")))
R2PY
  )
  say "guard stage complete: no install, no start, no connection made. See $OUT"
}

# -------------------------------------------------------------- install ----
stage_install() {
  mkdir -p "$EVIDENCE" "$PKG"
  OUT="$EVIDENCE/install.log"; : > "$OUT"
  found="$(resolve_existing_initdb || true)"
  if [ -n "$found" ]; then
    say "resolved PostgreSQL binaries already on disk — no install, no venv, no network"
    say "  initdb: $found" | tee -a "$OUT"
    "$found" --version 2>&1 | tee -a "$OUT" || fail "the resolved initdb would not report a version"
  else
    # Nothing on disk: this is the only branch that may touch the network, and
    # it needs the approved pin. An empty pin stays an honest UNKNOWN.
    if [ -z "$PGSERVER_PIN" ]; then
      fail "no pgserver binaries on disk and R2_PGSERVER_PIN is empty: the version is UNKNOWN. Resolve it and approve that exact pin before any install."
    fi
    require_absent "$VENV"
    "$BACKEND_PY" -m venv "$VENV" 2>&1 | tee -a "$OUT" || fail "venv creation failed (backend/.venv untouched)"
    "$VENV/bin/python" -m pip install --disable-pip-version-check "pgserver==$PGSERVER_PIN" 2>&1 | tee -a "$OUT" \
      || fail "pip install pgserver==$PGSERVER_PIN failed"
    {
      "$VENV/bin/python" -m pip freeze 2>&1 | grep -i -E "pgserver|psycopg" || true
      "$VENV/bin/python" -c "import inspect, os, pgserver; print('module', os.path.dirname(inspect.getfile(pgserver)))"
    } >>"$OUT" 2>&1
    found="$(find "$VENV" -type f -name initdb -path '*/bin/*' 2>/dev/null | head -1 || true)"
    if [ -z "$found" ]; then
      say "initdb not found inside the wheel; the installed layout is recorded in $OUT"
      fail "postgres bin dir UNKNOWN — do not proceed on a guess"
    fi
    "$found" --version 2>&1 | tee -a "$OUT"
  fi
  dirname "$found" > "$PGBIN_FILE"
  record_pipbin
  say "resolved PGBIN=$(cat "$PGBIN_FILE")"
}

# ------------------------------------------------------------ provision ----
stage_provision() {
  record_pipbin
  # The binaries may be shared with the 2026-11-08 cluster; the data directory
  # may not. That cluster holds ai_investment_lab, which this package is
  # forbidden to touch, so any PGDATA under a .local/pgdata path is refused
  # before require_absent could even be asked about it.
  case "$PGDATA" in
    */.local/pgdata*) fail "PGDATA=$PGDATA is inside a .local/pgdata path — that is the 2026-11-08 cluster's location and this package never initialises over it" ;;
  esac
  require_absent "$PGDATA"
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/provision.log"; : > "$OUT"
  # The bootstrap superuser owns the cluster; the application never connects as it
  # (the gate refuses superuser connections, test_safety_gate.py:125-130).
  "$PGBIN/initdb" -D "$PGDATA" -U "$PG_BOOTSTRAP_USER" --auth=trust --no-sync 2>&1 | tee -a "$OUT" \
    || fail "initdb failed; PGDATA left in place for inspection"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$EVIDENCE/postgres.log" \
    -o "-p $PG_PORT -c listen_addresses=$PG_HOST -c unix_socket_directories=/tmp" \
    start 2>&1 | tee -a "$OUT" || fail "pg_ctl start failed; see $EVIDENCE/postgres.log"
  say "postmaster pid $(head -1 "$PGDATA/postmaster.pid") datadir $(sed -n 4p "$PGDATA/postmaster.pid")"
  "$PGBIN/psql" "$BOOT_URL" -v ON_ERROR_STOP=1 2>&1 <<SQL | tee -a "$OUT" \
    || fail "role/database creation failed (no retry, no drop)"
CREATE ROLE $PG_ROLE LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE DATABASE $PG_DB OWNER $PG_ROLE;
SQL
  "$PGBIN/psql" "$BOOT_URL" -v ON_ERROR_STOP=1 -d "$PG_DB" \
    -c "GRANT ALL ON SCHEMA public TO $PG_ROLE;" 2>&1 | tee -a "$OUT" \
    || fail "schema grant failed"
  # Loopback-only, and the auth method is recorded rather than assumed.
  "$PGBIN/psql" "$BOOT_URL" -d "$PG_DB" -Atx \
    -c "SELECT current_database(), current_user, rolsuper FROM pg_roles WHERE rolname=current_user" \
    2>&1 | tee -a "$OUT"
  HBAF="$("$PGBIN/psql" "$BOOT_URL" -tAc 'SHOW hba_file')"
  grep -v '^[[:space:]]*#' "$HBAF" | grep -v '^[[:space:]]*$' | tee -a "$OUT"
  say "provisioned $PG_DB owned by $PG_ROLE (trust on loopback: no password exists)"
}

# ----------------------------------------------------- live gate check ----
stage_gate() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/gate_live.log"; : > "$OUT"
  ( cd "$BACKEND" && TEST_MODE=true DATABASE_URL="$RUN_URL" "$BACKEND_PY" -c \
      'from test_safety_gate import verify_test_database; print(verify_test_database())' ) 2>&1 | tee -a "$OUT" \
    || fail "verify_test_database() rejected the running cluster — stop here, do not migrate"
  say "the gate accepts this target; migration is a separate approval"
}

# ------------------------------------------------------------- migrate ----
stage_migrate() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/migrate.log"; : > "$OUT"
  # First structural write, against alethra_test only, using the existing revisions
  # (backend/migrations, 52 files measured; no new revision authored here).
  ( cd "$BACKEND" && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$RUN_URL" \
      "$BACKEND_PY" -m alembic upgrade head ) 2>&1 | tee -a "$OUT" \
    || fail "alembic upgrade head failed; PGDATA and $OUT retained (no recreate, no retry)"
  ( cd "$BACKEND" && PYTHONDONTWRITEBYTECODE=1 DATABASE_URL="$RUN_URL" \
      "$BACKEND_PY" -m alembic current ) 2>&1 | tee -a "$OUT"
}

# --------------------------------------------------------------- tests ----
stage_tests() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/tests.log"; : > "$OUT"
  # run_tests.sh pg derives all four DB variables from ALETHRA_PG_TEST_URL (:85-90) and
  # runs the safety gate (:98-111) plus the connection probe (:112-129) before pytest.
  # Files 1-2 are the research lane's own judgment; file 3 is the paper-trading
  # isolation suite and is not research-lane evidence — the two must not be merged.
  ( cd "$REPO" && DATA_PROVIDER=demo ALETHRA_PYTHON="$BACKEND_PY" ALETHRA_PG_TEST_URL="$RUN_URL" \
      ./backend/scripts/run_tests.sh pg \
        tests/test_research_run_recovery.py \
        tests/test_two_phase_quota.py \
        tests/test_ownership_integration.py \
        -p no:warnings -q -rf ) 2>&1 | tee -a "$OUT" \
    || fail "PG layer failed; first trace is in $OUT (no re-run, no test edits to go green)"
}

# ------------------------------------------------------------ api loop ----
# Every variable is explicit because backend/.env carries DATA_PROVIDER=twelvedata,
# LIVE_TRADING_ENABLED=true and a DATABASE_URL naming the blacklisted ai_investment_lab.
# Nothing external is reachable from these stages: the demo provider is in-process
# synthetic data, schedulers stay off, payments/notifications/live trading off, and both
# test-only auth conveniences off, so the two accounts carry real JWTs.
API_ENV=(
  PYTHONDONTWRITEBYTECODE=1
  DATABASE_URL="$RUN_URL"
  DATA_PROVIDER=demo
  ENABLE_LEARNING_SCHEDULER=false
  AUTO_COLLECTION_ENABLED=false
  ENABLE_LIVE_PAYMENTS=false
  ENABLE_EXTERNAL_NOTIFICATIONS=false
  LIVE_TRADING_ENABLED=false
  RESEARCH_AUTH_BYPASS_ENABLED=false
  CANDIDATE_PASSWORDLESS_MODE=false
  RESEARCH_RUN_RECOVERY_ENABLED=true
  # The window is a production setting (config.py:125 default 900). Lowering it to 5s
  # here is what makes the post-kill scan observable in seconds instead of a quarter of
  # an hour; it is configuration, not a code change, and it is printed with the result.
  RESEARCH_RUN_STALE_AFTER_SECONDS=5
)

api_start() {
  if [ -f "$PKG/uvicorn.pid" ] && ps -p "$(cat "$PKG/uvicorn.pid")" >/dev/null 2>&1; then
    say "uvicorn already running as pid $(cat "$PKG/uvicorn.pid")"
    return 0
  fi
  ( cd "$BACKEND" && env "${API_ENV[@]}" "$BACKEND_PY" -m uvicorn app.main:app \
      --host "$API_HOST" --port "$API_PORT" --log-level info \
      >>"$EVIDENCE/uvicorn.log" 2>&1 & echo $! > "$PKG/uvicorn.pid" )
  say "uvicorn pid $(cat "$PKG/uvicorn.pid") (the only pid this package may signal)"
}

api_stop_recorded() {
  if [ -f "$PKG/uvicorn.pid" ]; then
    pid="$(cat "$PKG/uvicorn.pid")"
    if ps -p "$pid" -o command= 2>/dev/null | grep -q "uvicorn app.main:app"; then
      kill -TERM "$pid" || true
      say "stopped the API process this script started (pid $pid)"
    else
      say "pid $pid is not this package's uvicorn — left alone"
    fi
    rm -f "$PKG/uvicorn.pid"
  fi
}

stage_timeprobe() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/timeprobe.log"; : > "$OUT"
  if [ ! -f "$PKG/uvicorn.pid" ]; then api_start; fi
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
# Feasibility gate for a real SIGKILL: is a run row observable in `running` from a
# second connection at all? Demo data is 3 assets x 30 trade dates, so the honest
# answer has to be measured rather than assumed.
import json
import os
import time
import urllib.request

import psycopg2

from app.data.demo_data import TRADE_DATES

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["DEMO-ALPHA"],
    "frequency": "1d",
    "start_date": str(min(TRADE_DATES)),
    "end_date": str(max(TRADE_DATES)),
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}


def call(method, path, body=None, token=None, boot_wait=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    for _ in range(200 if boot_wait else 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("API never answered")


boot = time.perf_counter()
status, _ = call("GET", "/", boot_wait=True)
print("api reachable in %.3fs (%s)" % (time.perf_counter() - boot, status))

call("POST", "/api/auth/register", {
    "risk_acknowledged": True, "email": "r2-timeprobe@alethra.test",
    "password": "r2-loop-pass-123", "display_name": "R2 timeprobe",
    "user_type": "individual",
}, boot_wait=True)
status, body = call("POST", "/api/auth/login",
                    {"email": "r2-timeprobe@alethra.test",
                     "password": "r2-loop-pass-123"})
assert status == 200, body
token_a = json.loads(body)["access_token"]

status, body = call("POST", "/api/v1/experiments/", {
    "title": "R2 timeprobe", "hypothesis": "Is a run observable mid-flight?",
    "strategy_spec": SPEC,
}, token=token_a)
assert status == 201, body
version_id = json.loads(body)["current_version_id"]

durations, polls = [], []
for i in range(3):
    conn = psycopg2.connect(RUN_URL)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    start = time.perf_counter()
    status, body = call("POST", "/api/v1/versions/%s/runs?data_source=provider&provider_id=demo"
                        % version_id, None, token_a)
    elapsed = time.perf_counter() - start
    assert status == 201, "%s %s" % (status, body[:400])
    run_id = json.loads(body)["id"]
    observed = 0
    final = None
    while True:
        cur.execute("SELECT status FROM research_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        if row is None or row[0] in ("completed", "failed"):
            final = row[0] if row else "missing"
            break
        observed += 1
        time.sleep(0.001)
    conn.close()
    durations.append(elapsed)
    polls.append(observed)
    print("run %d: request=%.4fs final=%s poll-saw-running=%d"
          % (i, elapsed, final, observed))

median = sorted(durations)[len(durations) // 2]
print("median request %.4fs; runs observed mid-flight: %d/%d"
      % (median, sum(1 for p in polls if p > 0), len(polls)))
observable = all(p > 0 for p in polls)
print("verdict:", "KILL_WINDOW_OBSERVABLE" if observable else
      "INSUFFICIENT_EVIDENCE: no in-flight window on this workload; the real-kill layer "
      "needs B1 (dataset import through the formal route) or B3 (worker/heartbeat), "
      "not a hand-seeded row")
raise SystemExit(0 if observable else 3)
R2PY
}

stage_api_loop() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/api_loop.log"; : > "$OUT"
  if [ ! -f "$PKG/uvicorn.pid" ]; then api_start; fi
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
  PIDFILE="$PKG/uvicorn.pid" KILLED_FILE="$PKG/killed_run_id" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
# Run this only after timeprobe exited 0; it also refuses to kill when the row is never
# observed running. The pid it signals is the one api_start recorded.
import json
import os
import signal
import threading
import time
import urllib.request

import psycopg2

from app.data.demo_data import TRADE_DATES

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
PID = int(open(os.environ["PIDFILE"]).read().strip())
KILLED_FILE = os.environ["KILLED_FILE"]


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except Exception as exc:
        return 0, type(exc).__name__


def register(email, name):
    call("POST", "/api/auth/register", {
        "risk_acknowledged": True, "email": email, "password": "r2-loop-pass-123",
        "display_name": name, "user_type": "individual",
    })
    status, body = call("POST", "/api/auth/login",
                        {"email": email, "password": "r2-loop-pass-123"})
    assert status == 200, body
    return json.loads(body)["access_token"]


token_a = register("r2-owner@alethra.test", "Owner A")
token_b = register("r2-other@alethra.test", "Other B")

spec = {
    "template_type": "buy_and_hold", "asset_ids": ["DEMO-ALPHA"], "frequency": "1d",
    "start_date": str(min(TRADE_DATES)), "end_date": str(max(TRADE_DATES)),
    "initial_cash": 100000.0, "commission_rate": 0.001, "slippage_rate": 0.0,
}
status, body = call("POST", "/api/v1/experiments/", {
    "title": "R2 kill loop", "hypothesis": "Recovery after a real kill.",
    "strategy_spec": spec,
}, token=token_a)
assert status == 201, body
version_id = json.loads(body)["current_version_id"]
run_path = ("/api/v1/versions/%s/runs?data_source=provider&provider_id=demo" % version_id)

conn = psycopg2.connect(RUN_URL)
conn.set_session(autocommit=True)
cur = conn.cursor()

# Scoped to the owner of this experiment: an unscoped scalar subquery over
# quota_counters raises "more than one row returned" the moment a second account
# ever gets a counter, and an unscoped reservation sum would report B's holds as
# if they were A's.
cur.execute("""
    SELECT e.owner_id FROM experiment_versions v
      JOIN experiments e ON e.id = v.experiment_id
     WHERE v.id = %s
""", (version_id,))
owner_id = str(cur.fetchone()[0])

QUOTA_SQL = """
    SELECT COALESCE((SELECT used FROM quota_counters
                     WHERE quota_type='research_run' AND period='lifetime'
                       AND user_id = %s), 0),
           COALESCE((SELECT sum(amount) FROM quota_reservations
                     WHERE quota_type='research_run' AND status='reserved'
                       AND user_id = %s), 0),
           (SELECT count(*) FROM research_runs WHERE experiment_version_id = %s),
           (SELECT count(*) FROM audit_events
             WHERE entity_type='run' AND event_type='research_run_failed'
               AND actor_id = %s)
"""


def quota():
    cur.execute(QUOTA_SQL, (owner_id, owner_id, version_id, owner_id))
    used, reserved, rows, failed_events = cur.fetchone()
    return dict(used=used, reserved=reserved or 0, run_rows=rows,
                failed_events=failed_events)


print("before:", quota())

# A finished run first, so there is a real run id for the cross-account read to target.
status, body = call("POST", run_path, None, token_a)
assert status == 201, body
baseline_run_id = json.loads(body)["id"]
print("baseline run (A, completed):", baseline_run_id)
print("after baseline:", quota())

# Isolation over HTTP on the real routes, with the codes the code actually produces:
# submitting to another account's version is refused by the ownership check
# (api/runs.py:73) with 403, and reading another account's run is answered 404
# (api/runs.py:705-706). 403 and 404 are different answers; this stage records both.
status_read, _ = call("GET", "/api/v1/runs/%s" % baseline_run_id, None, token_b)
status_submit, body_submit = call("POST", run_path, None, token_b)
print("B GET on A's run ->", status_read, "| B POST on A's version ->", status_submit,
      "| A's run id leaked:", baseline_run_id in body_submit)
assert status_read == 404, "a cross-account read must not answer 200"
assert status_submit == 403, body_submit[:300]

# Kill while a row is observably running. The request runs in its own thread so the
# polling connection is not blocked behind its own statement.
outcome = {}


def submit():
    outcome["result"] = call("POST", run_path, None, token_a)


thread = threading.Thread(target=submit)
thread.start()
killed = None
deadline = time.perf_counter() + 30
while time.perf_counter() < deadline and thread.is_alive():
    cur.execute("""
        SELECT id, status FROM research_runs
         WHERE experiment_version_id = %s AND status IN ('pending','running')
         ORDER BY started_at DESC NULLS LAST LIMIT 1
    """, (version_id,))
    row = cur.fetchone()
    if row:
        os.kill(PID, signal.SIGKILL)
        killed = str(row[0])
        open(KILLED_FILE, "w").write(killed)
        print("SIGKILL -> pid %s while run %s was %s" % (PID, killed, row[1]))
        break
    time.sleep(0.001)
if killed is None:
    thread.join(timeout=60)
    print("verdict: INSUFFICIENT_EVIDENCE - never observed a running row; nothing killed")
    print("request outcome:", outcome.get("result", ("no response", "")))
    raise SystemExit(3)
thread.join(timeout=60)
print("request ended with:", outcome.get("result", ("connection dropped by the kill", "")))
print("after kill (API down):", quota())
conn.close()
R2PY
  say "killed run id recorded in $PKG/killed_run_id; next approved step: $0 restart-verify"
}

stage_restart_verify() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/restart_verify.log"; : > "$OUT"
  if [ ! -f "$PKG/killed_run_id" ]; then fail "no $PKG/killed_run_id: run api-loop first"; fi
  STALE="$("$BACKEND_PY" -c 'import sys; sys.path.insert(0, "'"$BACKEND"'"); from app.config import get_settings; print(get_settings().research_run_stale_after_seconds)' 2>/dev/null || echo UNKNOWN)"
  say "relaunching the API so the startup scan (main.py:78-108) runs; stale window ${STALE}s"
  rm -f "$PKG/uvicorn.pid"
  api_start
  RUN_URL="$RUN_URL" API_BASE="http://$API_HOST:$API_PORT" \
  KILLED="$(cat "$PKG/killed_run_id")" STALE="$STALE" \
    "$BACKEND_PY" - <<'R2PY' 2>&1 | tee -a "$OUT"
import json
import os
import time
import urllib.request

import psycopg2

BASE = os.environ["API_BASE"]
RUN_URL = os.environ["RUN_URL"]
KILLED = os.environ["KILLED"]
STALE = os.environ["STALE"]


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    for _ in range(200):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("API never answered")


status, _ = call("GET", "/")
print("api up:", status)

conn = psycopg2.connect(RUN_URL)
conn.set_session(autocommit=True)
cur = conn.cursor()
# The startup scan only closes rows older than the stale window, so a freshly killed
# row is still `running` here. Both readings are printed: immediately after restart,
# and after the POST that has to wait out the window (or a second restart once it is
# past it). Nothing is asserted that the scan did not do.
# Every count is scoped to the owner of the killed row: counting across all accounts
# would let account B's open reservation answer for account A's, and an unscoped
# counter read can match several rows and silently report one of them.
cur.execute("""
    SELECT r.status, r.started_at, r.completed_at, r.error_details,
           e.owner_id,
           (SELECT count(*) FROM quota_reservations q
             WHERE q.quota_type='research_run' AND q.status='reserved'
               AND q.user_id = e.owner_id),
           (SELECT count(*) FROM audit_events a
             WHERE a.entity_type='run' AND a.entity_id = r.id::text)
      FROM research_runs r
      JOIN experiment_versions v ON v.id = r.experiment_version_id
      JOIN experiments e ON e.id = v.experiment_id
     WHERE r.id = %s
""", (KILLED,))
row = cur.fetchone()
if row is None:
    raise SystemExit("killed run %s is not in research_runs: the write never committed")
cur.execute("""
    SELECT used FROM quota_counters
     WHERE quota_type='research_run' AND period='lifetime' AND user_id = %s
""", (str(row[4]),))
counters = cur.fetchall()
cur.execute("""
    SELECT count(*) FROM audit_events
     WHERE entity_type='run' AND event_type='research_run_failed'
""")
failed_events = cur.fetchone()[0]
print(json.dumps(dict(
    killed_run=KILLED, status=row[0], started_at=str(row[1]),
    completed_at=str(row[2]), error_head=(row[3] or "")[:60],
    owner=str(row[4]), open_reservations_same_owner=row[5],
    audit_events_for_this_run=row[6],
    owner_lifetime_used=counters[0][0] if len(counters) == 1 else "ROWS=%d" % len(counters),
    research_run_failed_events_all_accounts=failed_events,
    stale_after_seconds=STALE,
), ensure_ascii=False, indent=2))
conn.close()
R2PY
  say "acceptance is the printed row; the terminal state appears once the row is past"
  say "${STALE}s and a scan runs (startup, or the next POST to that version)"
}

# ---------------------------------------------------------------- stop ----
stage_stop() {
  mkdir -p "$EVIDENCE"
  OUT="$EVIDENCE/stop.log"; : > "$OUT"
  api_stop_recorded
  if [ -f "$PGDATA/postmaster.pid" ]; then
    record_pipbin
    "$PGBIN/pg_ctl" -D "$PGDATA" stop -m fast 2>&1 | tee -a "$OUT" \
      || fail "pg_ctl stop -m fast failed (never -m immediate; no signal to foreign backends)"
  fi
  say "PGDATA and evidence retained: $PGDATA / $EVIDENCE — cleanup is a separate package"
}

stage_status() {
  printf 'working set   %s\n' "$WORK"
  printf 'pgdata        %s (%s)\n' "$PGDATA" "$([ -d "$PGDATA" ] && echo exists || echo absent)"
  printf 'evidence      %s\n' "$(ls -1 "$EVIDENCE" 2>/dev/null | tr '\n' ' ')"
  printf 'resolved bins %s\n' "$(cat "$PGBIN_FILE" 2>/dev/null || echo 'UNKNOWN (install not run)')"
  printf 'uvicorn pid   %s\n' "$(cat "$PKG/uvicorn.pid" 2>/dev/null || echo 'not started')"
  printf 'postmaster    %s\n' "$(head -1 "$PGDATA/postmaster.pid" 2>/dev/null || echo 'not running')"
}

case "${1:-}" in
  guard)            stage_guard ;;
  install)          stage_install ;;
  provision)        stage_provision ;;
  gate)             stage_gate ;;
  migrate)          stage_migrate ;;
  tests)            stage_tests ;;
  timeprobe)        stage_timeprobe ;;
  api-loop)         stage_api_loop ;;
  restart-verify)   stage_restart_verify ;;
  stop)             stage_stop ;;
  status)           stage_status ;;
  *) echo "usage: $0 {guard|install|provision|gate|migrate|tests|timeprobe|api-loop|restart-verify|stop|status}" >&2
     echo "only the read-only guard stage has ever run (2026-11-16); every writing stage still needs its own approval." >&2
     exit 2 ;;
esac
