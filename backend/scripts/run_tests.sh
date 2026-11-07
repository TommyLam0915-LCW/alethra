#!/bin/bash
# Backend suite entry point. Two layers, never merged:
#
#   ./backend/scripts/run_tests.sh sqlite [pytest args…]
#       Default suite on a run-specific SQLite file. The PostgreSQL-only
#       variables are cleared on purpose, so PostgreSQL modules skip for the
#       stated reason (printed by -rs) instead of tripping over a half-set env.
#
#   ./backend/scripts/run_tests.sh pg [pytest args…]
#       PostgreSQL-required suites on a disposable database. Requires
#       ALETHRA_PG_TEST_URL; all four DB variables are derived from it, and a
#       read-only preflight proves they agree — and that the database answers —
#       before pytest starts.
#
# A sqlite result is not a PostgreSQL result and vice versa. Each run prints the
# layer it executed, so a count can never be read across layers.
#
# Environment:
#   ALETHRA_PYTHON                   interpreter (default python3)
#   ALETHRA_PG_TEST_URL              required for `pg`; never printed
#   ALETHRA_TEST_REQUIRED_DB_PREFIX  default alethra_ (disposable boundary)
#   ALETHRA_SQLITE_DB                optional database file for `sqlite`
#   JWT_SECRET_KEY                   generated per run when unset
#
# Exit: pytest's status, 2 refused precondition, 3 preflight rejected the env.
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKEND="$ROOT/backend"
PYTHON=${ALETHRA_PYTHON:-python3}

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
  "") MODE="sqlite" ;;
  sqlite | pg)
    MODE=$1
    shift
    ;;
  *)
    echo "unknown layer: $1 (expected sqlite or pg)" >&2
    usage >&2
    exit 2
    ;;
esac

"$PYTHON" -c 'import pytest, sqlalchemy' 2>/dev/null ||
  { echo "refusing: $PYTHON cannot import pytest/sqlalchemy; set ALETHRA_PYTHON" >&2; exit 2; }

if [ -z "${JWT_SECRET_KEY:-}" ]; then
  JWT_SECRET_KEY=$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')
  export JWT_SECRET_KEY
fi

export PYTHONPATH="$BACKEND${PYTHONPATH:+:$PYTHONPATH}"

preflight() {
  "$PYTHON" -c '
import os
import sys

from scripts.check_db_env import analyse

prefix = os.environ.get("ALETHRA_TEST_REQUIRED_DB_PREFIX") or None
analysis = analyse(os.environ, required_prefix=prefix)
for problem in analysis.errors:
    print("preflight: " + problem, file=sys.stderr)
if analysis.errors:
    sys.exit(1)
print("preflight ok: " + analysis.summary)
'
}

if [ "$MODE" = "pg" ]; then
  if [ -z "${ALETHRA_PG_TEST_URL:-}" ]; then
    echo "refusing: pg layer needs ALETHRA_PG_TEST_URL (a disposable PostgreSQL database)" >&2
    exit 2
  fi
  export DATABASE_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_DB="$ALETHRA_PG_TEST_URL"
  export ALETHRA_PG_DATABASE_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_POSTGRES_URL="$ALETHRA_PG_TEST_URL"
  export ALETHRA_TEST_REQUIRED_DB_PREFIX=${ALETHRA_TEST_REQUIRED_DB_PREFIX:-alethra_}
  export TEST_MODE=true
  echo "backend suite — layer: pg (PostgreSQL; pass the PostgreSQL-required test paths)"
  echo "interpreter: $PYTHON"
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  # Integrate safety gate before any DB writes
  "$PYTHON" -c '
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety_gate import verify_test_database, TestDatabaseGateError

try:
    gate_info = verify_test_database()
    print(f"✓ Safety gate passed: {gate_info[\"database\"]} on {gate_info[\"server_addr\"]}:{gate_info[\"server_port\"]}")
except TestDatabaseGateError as e:
    print(f"SAFETY GATE FAILED: {e}", file=sys.stderr)
    sys.exit(1)
' || {
    echo "safety gate rejected the database connection; pytest was not started." >&2
    exit 3
  }
  "$PYTHON" -c '
import os
import sys

from scripts.check_db_env import analyse, connection_problem

analysis = analyse(os.environ, required_prefix=os.environ.get("ALETHRA_TEST_REQUIRED_DB_PREFIX"))
if analysis.errors:
    sys.exit(1)
problem = connection_problem(os.environ["DATABASE_URL"], analysis.database)
if problem:
    print("preflight: " + problem, file=sys.stderr)
    sys.exit(1)
print("preflight ok: database answered as " + analysis.database)
' || {
    echo "preflight could not reach the database; pytest was not started." >&2
    exit 3
  }
else
  DB_FILE=${ALETHRA_SQLITE_DB:-${TMPDIR:-/tmp}/alethra-sqlite-test-$$.db}
  case "$DB_FILE" in
    /*) ;;
    *)
      echo "refusing: ALETHRA_SQLITE_DB must be an absolute path, got '$DB_FILE'" >&2
      exit 2
      ;;
  esac
  unset ALETHRA_PG_DATABASE_URL ALETHRA_TEST_POSTGRES_URL ALETHRA_TEST_REQUIRED_DB_PREFIX
  export DATABASE_URL="sqlite:///$DB_FILE"
  export ALETHRA_TEST_DB="$DATABASE_URL"
  echo "backend suite — layer: sqlite (default suite)"
  echo "interpreter: $PYTHON"
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  echo "note: PostgreSQL-only modules will skip here; -rs prints the reason."
  echo "      this run is not a PostgreSQL result."
fi

cd "$BACKEND" || exit 2
exec "$PYTHON" -m pytest -rs "$@"
