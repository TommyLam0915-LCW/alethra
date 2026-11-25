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
# The sqlite layer also enforces two ceilings. Every run writes its own
# directory (`alethra-runs/<layer>-<utc>-<pid>`) holding identity.txt,
# collect.txt (the node ids this tree collects), pytest.log, junit.xml (the
# outcome records, which are the only artifact that distinguishes an
# item-level skip from a collection-level one) and exit-code.txt -- the tested
# process's own status, never the logger's. Against those artifacts,
# `scripts/skip_budget.py` censuses the printed skip reasons by category and
# `scripts/run_evidence.py` reconciles records against collected ids and
# refuses a selection that no longer matches `tests/collected_nodes.txt`.
# Over budget, vanished nodes, or an unacknowledged selection diff, the lane
# exits non-zero even though every selected test passed.
#
# Environment:
#   ALETHRA_PYTHON                   interpreter override; must be executable.
#                                    Unset, this script uses this checkout's own
#                                    venv (backend/.venv, then backend/venv) and
#                                    refuses if neither exists. It never falls
#                                    back to a system python3: a host interpreter
#                                    that happens to carry pytest/sqlalchemy is a
#                                    second dependency set, not this lane.
#   ALETHRA_PG_TEST_URL              required for `pg`; never printed
#   ALETHRA_TEST_REQUIRED_DB_PREFIX  default alethra_ (disposable boundary)
#   ALETHRA_SQLITE_DB                optional database file for `sqlite`
#   TMPDIR                           where that file is created when
#                                    ALETHRA_SQLITE_DB is unset (default /tmp).
#                                    The directory must exist and be writable:
#                                    the run is refused here rather than failing
#                                    in every test fixture below.
#   ALETHRA_RUN_DIR                  the artifact directory for this run. It is
#                                    created exclusively: an existing directory
#                                    is refused rather than reused or renamed,
#                                    because a shared path is how one run's log
#                                    ended up inside another run's file.
#   ALETHRA_LANE_LOG                 optional copy destination for pytest.log,
#                                    written by copy after the run, never by the
#                                    tee that the tested process writes through.
#   JWT_SECRET_KEY                   generated per run when unset
#
# Exit: pytest's status, 2 refused precondition, 3 preflight rejected the env.
#
# Invocation (the complete list of what this script itself parses):
#   $0 sqlite [pytest args…]        $0 pg [pytest args…]        $0 -h | --help
# One layer word, then arguments handed to pytest verbatim. There is no
# --digest-only, no --tree-digest and no `collect` sub-command, and the runner
# takes no flags of its own after the layer name. The dry run is pytest's own
# --collect-only, which the lane recognises (also as --co) when it classifies
# the selection and when it decides not to judge the whole-suite ceiling.
# A digest is printed by every run -- tree-digest-before/after in identity.txt
# -- so no separate flag is needed for it.
# Verbs that change the collector's shape (-q family, -v family, --quiet,
# --verbose, and any short cluster carrying v or q) are dropped for the
# --collect-only step only and recorded as collector-verbosity-normalised; the
# pytest run itself receives "$@" untouched. The whole-suite ceiling is read
# from backend/tests/skip_budget.json, whose "collected" number is part of the
# pin: a suite that collects a different count is refused rather than judged
# against a ceiling measured over another selection.
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKEND="$ROOT/backend"

usage() {
  awk 'NR == 1 { next } /^set -u/ { exit } { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
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
  -*)
    # Two runs were lost to this shape: a runner flag that does not exist was
    # typed where the layer belongs, and the lane reported a pytest usage error
    # instead of the actual problem -- that the layer word is what comes first.
    echo "refusing: '$1' is not a layer. The layer comes first, and the only" >&2
    echo "        layers are: sqlite, pg (see '$0 --help')." >&2
    echo "        Everything after the layer goes to pytest unchanged; the dry" >&2
    echo "        run is pytest's own --collect-only." >&2
    exit 2
    ;;
  *)
    echo "unknown layer: $1 (expected one of: sqlite, pg; or -h for the full" >&2
    echo "              invocation, which is '$0 <sqlite|pg> [pytest args…]')" >&2
    usage >&2
    exit 2
    ;;
esac

if [ -n "${ALETHRA_PYTHON:-}" ]; then
  PYTHON=$ALETHRA_PYTHON
  [ -f "$PYTHON" ] && [ -x "$PYTHON" ] || {
    echo "refusing: ALETHRA_PYTHON='$PYTHON' is not an executable file" >&2
    exit 2
  }
else
  PYTHON=""
  for candidate in "$BACKEND/.venv/bin/python" "$BACKEND/venv/bin/python"; do
    if [ -f "$candidate" ] && [ -x "$candidate" ]; then
      PYTHON=$candidate
      break
    fi
  done
  [ -n "$PYTHON" ] || {
    echo "refusing: this checkout has no venv interpreter. Probed:" >&2
    echo "  $BACKEND/.venv/bin/python" >&2
    echo "  $BACKEND/venv/bin/python" >&2
    echo "Set ALETHRA_PYTHON to the interpreter this lane was built with." >&2
    echo "A system python3 is not used silently: it carries its own pytest and" >&2
    echo "SQLAlchemy, so its counts would be read as this lane's." >&2
    exit 2
  }
fi

"$PYTHON" -c 'import pytest, sqlalchemy' 2>/dev/null ||
  { echo "refusing: $PYTHON cannot import pytest/sqlalchemy; set ALETHRA_PYTHON" >&2; exit 2; }

# The counts below are only comparable to another run if the dependency set was
# the same one, so the versions are printed instead of inferred from the path.
print_lane_fingerprint() {
  "$PYTHON" -c 'import platform, pytest, sqlalchemy; print("lane fingerprint: python %s | pytest %s | sqlalchemy %s" % (platform.python_version(), pytest.__version__, sqlalchemy.__version__))'
}

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
  print_lane_fingerprint
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  # Integrate safety gate before any DB writes
  "$PYTHON" -c '
import sys
from test_safety_gate import verify_test_database, TestDatabaseGateError

try:
    gate_info = verify_test_database()
    print("Safety gate passed:", gate_info["database"], "on", str(gate_info["server_addr"]) + ":" + str(gate_info["server_port"]))
except TestDatabaseGateError as e:
    print("SAFETY GATE FAILED:", e, file=sys.stderr)
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
  DB_DIR=${DB_FILE%/*}
  [ -n "$DB_DIR" ] || DB_DIR=/
  if [ -n "${ALETHRA_SQLITE_DB:-}" ]; then
    DB_SOURCE="ALETHRA_SQLITE_DB=$ALETHRA_SQLITE_DB"
  elif [ -n "${TMPDIR:-}" ]; then
    DB_SOURCE="TMPDIR=$TMPDIR"
  else
    DB_SOURCE="the default /tmp"
  fi
  # An unusable directory has to be said here: sqlite opens no file silently and
  # every test then fails at setup with "unable to open database file", which
  # reads as a product regression rather than as a missing scratch directory.
  [ -d "$DB_DIR" ] || {
    echo "refusing: the directory for the sqlite test database does not exist: $DB_DIR" >&2
    echo "that path came from $DB_SOURCE" >&2
    echo "create it, or point TMPDIR (or ALETHRA_SQLITE_DB) at a directory that exists" >&2
    exit 2
  }
  [ -w "$DB_DIR" ] || {
    echo "refusing: the sqlite test database directory $DB_DIR ($DB_SOURCE) is not writable" >&2
    exit 2
  }
  unset ALETHRA_PG_DATABASE_URL ALETHRA_TEST_POSTGRES_URL ALETHRA_TEST_REQUIRED_DB_PREFIX
  export DATABASE_URL="sqlite:///$DB_FILE"
  export ALETHRA_TEST_DB="$DATABASE_URL"
  echo "backend suite — layer: sqlite (default suite)"
  echo "interpreter: $PYTHON"
  print_lane_fingerprint
  preflight || {
    echo "preflight rejected the environment; pytest was not started." >&2
    exit 3
  }
  echo "note: PostgreSQL-only modules will skip here; the lane prints each reason."
  echo "      this run is not a PostgreSQL result."
fi

cd "$BACKEND" || exit 2

# The identity of a measurement is its command, its tree and its selection, and
# a log that carries none of the three cannot be re-run by anybody. The
# authoritative 2026-11-16 run is the reason: its command had to be reconstructed
# afterwards from the shape of the output, because nothing printed it.
# Two things have to be true for a count in a log to be checkable later, and
# neither held before: pytest honours only the LAST -r flag, so a caller asking
# for -rf silently overrode the -rs below and the run lost every skip reason;
# and the verbosity the caller chose changes what the collector prints, so a
# log could not be reconciled against the tree it ran on. The lane therefore
# writes its own identity header (command, cwd, revision, collected count and
# node-id count) into every run directory and echoes it, rather than relying on
# pytest's own header surviving the caller's options, and it normalises -q for
# the collection step (further below).
LANE_ARGS="$*"

# What pytest was actually pointed at, decided from CLI semantics rather than
# from "some argument did not start with a dash": an option's value is not a
# selection ("-p no:warnings" used to convince this lane that specific tests
# were named, which silently switched the whole-suite ceiling off), while a
# -k/-m/--deselect/--ignore expression IS a narrowed selection even though it
# carries no path, and --collect-only executes nothing at all.
SELECTED_TESTS=""
FILTER_ARGS=""
COLLECT_ONLY=""
for arg in "$@"; do
  case "$arg" in
    --co | --collect-only)
      COLLECT_ONLY=YES
      ;;
    -k | -m | --deselect | --ignore | --ignore-glob)
      FILTER_ARGS="$FILTER_ARGS $arg"
      ;;
    -k?* | -m?* | --deselect=* | --ignore=* | --ignore-glob=*)
      FILTER_ARGS="$FILTER_ARGS $arg"
      ;;
    -*) ;;
    *)
      # What pytest can be pointed at is a path that exists here, optionally
      # followed by "::node::id".
      if [ -e "${arg%%::*}" ]; then
        SELECTED_TESTS="$SELECTED_TESTS $arg"
      fi
      ;;
  esac
done
EXPLICIT=""
if [ -n "$SELECTED_TESTS" ] || [ -n "$FILTER_ARGS" ]; then
  EXPLICIT=YES
fi
# What the identity artifact has to say about the selection. The gate's own
# decision reads $EXPLICIT, but a durable evidence file must not carry the
# shell's sentinel: a reader who finds "YES" cannot tell which way it was set.
SELECTION_KIND=${EXPLICIT:+explicit-selection}
SELECTION_KIND=${SELECTION_KIND:-whole-suite}

# One directory per run, created exclusively. Sharing an output path is not a
# convenience here: a focused run given the full-suite run's log path truncated
# it from byte 0 and left a hole where the identity header had been.
RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_DIR=${ALETHRA_RUN_DIR:-${TMPDIR:-/tmp}/alethra-runs/$MODE-$RUN_STAMP-$$}
RUN_PARENT=${RUN_DIR%/*}
[ -n "$RUN_PARENT" ] || RUN_PARENT=/
# The default tree under $TMPDIR/alethra-runs is created with -p because it is
# storage, not a measurement. An explicitly named ALETHRA_RUN_DIR is not: its
# parent has to exist, otherwise a typo in the variable invents a directory and
# the run's evidence lands somewhere nobody will look for it again. The run
# directory itself is always created exclusively, so no two runs can share one.
# mkdir's failure is not a single cause, and conflating them put "the run
# directory already exists" on the screen when the truth was that TMPDIR named a
# directory that had never been created.
if [ -z "${ALETHRA_RUN_DIR:-}" ]; then
  mkdir -p "$RUN_PARENT" 2>/dev/null
elif [ ! -d "$RUN_PARENT" ]; then
  echo "refusing: ALETHRA_RUN_DIR=$RUN_DIR has no parent directory: $RUN_PARENT" >&2
  echo "It is not created for you: an evidence path that has to be invented mid-run" >&2
  echo "is a path a later reader cannot find. Create it, or name a real one." >&2
  exit 2
fi
if ! mkdir "$RUN_DIR" 2>/dev/null; then
  if [ -e "$RUN_DIR" ]; then
    echo "refusing: the run directory already exists: $RUN_DIR" >&2
    echo "nothing is reused, renamed-and-retried, or shared: unset ALETHRA_RUN_DIR" >&2
    echo "and let the lane make a new one." >&2
  else
    echo "refusing: the run directory could not be created: $RUN_DIR" >&2
    echo "its parent $RUN_PARENT is not writable, so nothing was run and nothing saved." >&2
  fi
  exit 2
fi
[ -d "$RUN_DIR" ] || {
  echo "refusing: mkdir reported success but $RUN_DIR is not a directory" >&2
  exit 2
}

# Where this run puts its evidence is this shell's private coordinate, not an
# input to the suite. It has to be deleted from the environment before pytest is
# started, because some of those tests start the lane themselves: an inherited
# ALETHRA_RUN_DIR made every one of those nested calls answer "the run directory
# already exists" and exit 2, and an inherited ALETHRA_LANE_LOG handed a child
# the parent's copy destination. The paths are kept here first so the gates
# below still read what the caller asked for.
LANE_LOG_COPY=${ALETHRA_LANE_LOG:-}
BUDGET_PATH=${ALETHRA_SKIP_BUDGET:-$BACKEND/tests/skip_budget.json}
MANIFEST_PATH=${ALETHRA_COLLECTED_MANIFEST:-$BACKEND/tests/collected_nodes.txt}
unset ALETHRA_RUN_DIR ALETHRA_LANE_LOG ALETHRA_SKIP_BUDGET ALETHRA_COLLECTED_MANIFEST

tree_digest() {
  find . -name '*.py' -type f \
    -not -path './.venv/*' -not -path './venv/*' \
    -not -path '*/__pycache__/*' -not -path './node_modules/*' \
    2>/dev/null | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 |
    cut -d' ' -f1
}

# The collector's output format is part of this lane's contract, so the
# verbosity level is normalised instead of inherited. pytest reads repeated -q
# as a count, and a caller who already passed -q plus the -q appended here
# becomes -qq, which prints per-file totals ("tests/test_x.py: 20") instead of
# node ids and drops the "N tests collected" line -- leaving nothing to
# reconcile junit.xml against and a run that cannot be shown to be the pinned
# selection. -v breaks it the same way from the other side: `--collect-only -v`
# prints the tree ("<Function test_x>"), so 0 node ids survive and every JUnit
# record lands in "record without id", which refused a run in which all 59
# tests had passed (measured 2026-11-20, run dir
# sqlite-20261120T070014Z-79849). Both families are therefore removed from the
# caller's arguments for this step only, and exactly one -q is re-added; the run
# below still uses "$@" untouched, so a caller who wants verbose results keeps
# them in pytest.log -- just not in the file the ledger is read from.
COLLECT_ARGS=()
VERBOSITY_DROPPED=""
for arg in "$@"; do
  drop=""
  case "$arg" in
    --quiet | --verbose) drop=yes ;;
    -[!-]*) case "${arg#-}" in *[vq]*) drop=yes ;; esac ;;
  esac
  if [ -n "$drop" ]; then
    VERBOSITY_DROPPED="${VERBOSITY_DROPPED:+$VERBOSITY_DROPPED }$arg"
  else
    COLLECT_ARGS+=("$arg")
  fi
done

# bash 3.2 expands an empty array as unbound under `set -u`, and the default
# whole-suite call is exactly that case.
if [ "${#COLLECT_ARGS[@]}" -gt 0 ]; then
  "$PYTHON" -m pytest "${COLLECT_ARGS[@]}" --collect-only -q > "$RUN_DIR/collect.txt" 2>&1
else
  "$PYTHON" -m pytest --collect-only -q > "$RUN_DIR/collect.txt" 2>&1
fi
CRC=$?
SELECTION=$(grep -E "collected|deselected" "$RUN_DIR/collect.txt" | tail -n 1)
COLLECTED_IDS=$(grep -cE '^[^[:space:]]+\.py(::.*)+$' "$RUN_DIR/collect.txt")

emit_identity() {
  echo "command: $0 $MODE $LANE_ARGS"
  echo "cwd: $(pwd)"
  echo "git: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo '<git could not answer>')$(
    git -C "$ROOT" status --porcelain 2>/dev/null | grep -q . && echo " (working tree dirty: run 'git status --porcelain' for the diff identity')"
  )"
  echo "porcelain: $(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ') path(s) sha256 $(git -C "$ROOT" status --porcelain 2>/dev/null | shasum -a 256 | cut -d' ' -f1)"
  echo "interpreter: $PYTHON"
  print_lane_fingerprint
  if [ "$MODE" = "pg" ]; then
    echo "target: ALETHRA_PG_TEST_URL (not printed)"
  else
    echo "target: sqlite $DB_FILE"
  fi
  echo "provider: DATA_PROVIDER=${DATA_PROVIDER:-<unset>}"
  echo "layer: $MODE"
  echo "selection-kind: $SELECTION_KIND${FILTER_ARGS:+ with filters:$FILTER_ARGS}${COLLECT_ONLY:+ (collect-only)}"
  if [ -n "$VERBOSITY_DROPPED" ]; then
    echo "collector-verbosity-normalised: $VERBOSITY_DROPPED (dropped for the --collect-only step only; the run itself keeps them)"
  fi
  echo "tree-digest-before: $(tree_digest)"
  echo "collected-node-ids: $COLLECTED_IDS"
  echo "selection on this tree: ${SELECTION:-<the collector printed no selection line>}"
}

emit_identity | tee "$RUN_DIR/identity.txt"
case "$SELECTION" in
  *"no tests collected"*)
    # A run that executes nothing cannot produce a result, so it is refused
    # rather than reported as a green 0/0.
    echo "refusing: the selection matched no tests, so this lane would run nothing." >&2
    echo "collect output: $RUN_DIR/collect.txt" >&2
    exit 2
    ;;
  *[0-9]*" collected"*) ;;
  *)
    # Everything deselected (or no count at all) is the same outcome: nothing
    # would be executed, and a log without a selected count cannot be audited.
    echo "refusing: no test was selected ('$SELECTION'), so this lane would run nothing." >&2
    echo "collect output: $RUN_DIR/collect.txt" >&2
    exit 2
    ;;
esac
if [ "$CRC" -ne 0 ]; then
  echo "note: the collector exited $CRC; a collection error is kept as evidence," >&2
  echo "      not smoothed over -- see $RUN_DIR/collect.txt."
fi

# A skip is self-reported by the very run it excuses, so the lane does not
# finish when pytest returns: the census of the reasons pytest printed is
# checked against the ceiling a real measurement wrote
# (backend/tests/skip_budget.json), and the outcome records in junit.xml are
# reconciled against the node ids in collect.txt. Over budget, a vanished
# node, or a selection that no longer matches the pinned one is reported as a
# failed run -- otherwise "nothing ran" and "nothing needed running" print the
# same green. The pg layer has no such ceiling yet, so it is not judged here.
"$PYTHON" -m pytest "$@" -rfEs --junitxml="$RUN_DIR/junit.xml" 2>&1 |
  tee "$RUN_DIR/pytest.log"
RC=${PIPESTATUS[0]}
echo "$RC" > "$RUN_DIR/exit-code.txt"

{
  echo "tree-digest-after: $(tree_digest)"
  echo "exit-code: $RC"
} | tee -a "$RUN_DIR/identity.txt"
TREE_BEFORE=$(grep '^tree-digest-before: ' "$RUN_DIR/identity.txt" | head -n 1 | cut -d' ' -f2)
TREE_AFTER=$(grep '^tree-digest-after: ' "$RUN_DIR/identity.txt" | head -n 1 | cut -d' ' -f2)
if [ "$TREE_BEFORE" = "$TREE_AFTER" ]; then
  echo "tree changed during run: no (python digest $TREE_BEFORE before and after)"
else
  echo "tree changed during run: YES ($TREE_BEFORE -> $TREE_AFTER)"
  echo "this run measures a moving tree; it is not a fixed-version result."
fi
echo "run dir: $RUN_DIR"
echo "console log: $RUN_DIR/pytest.log"
echo "outcome records: $RUN_DIR/junit.xml"
echo "exit code: $RUN_DIR/exit-code.txt (pytest's own status, recorded before any gate)"

# ALETHRA_LANE_LOG is a copy, not the place the run happens: the run directory
# above is what the gates read. The copy carries the identity header as well as
# the console output, because a log that cannot say which command and which
# revision produced it is only believable beside a transcript.
if [ -n "$LANE_LOG_COPY" ]; then
  LANE_LOG_PARTIAL="${LANE_LOG_COPY%.*}.$$.$RANDOM.partial"
  if cat "$RUN_DIR/identity.txt" "$RUN_DIR/pytest.log" > "$LANE_LOG_PARTIAL" 2>&1 &&
    mv -f "$LANE_LOG_PARTIAL" "$LANE_LOG_COPY" 2>&1; then
    echo "lane log copy: $LANE_LOG_COPY"
  else
    rm -f "$LANE_LOG_PARTIAL"
    echo "note: could not write the lane log copy to ALETHRA_LANE_LOG=$LANE_LOG_COPY;" >&2
    echo "      the run's own artifacts remain under $RUN_DIR." >&2
  fi
fi

if [ "$MODE" = "sqlite" ] && [ -z "$COLLECT_ONLY" ]; then
  CHECK=("$BACKEND/scripts/skip_budget.py" "$RUN_DIR/pytest.log" check --budget "$BUDGET_PATH")
  CHECK+=("--identity" "$RUN_DIR/identity.txt")
  EVIDENCE=("$BACKEND/scripts/run_evidence.py" "$RUN_DIR" check
    --budget "$BUDGET_PATH" --manifest "$MANIFEST_PATH")
  if [ -n "$EXPLICIT" ]; then
    CHECK+=("--explicit-selection")
    EVIDENCE+=("--explicit-selection")
  fi
  "$PYTHON" "${CHECK[@]}"
  BRC=$?
  "$PYTHON" "${EVIDENCE[@]}"
  ERC=$?
  GATE=$BRC
  [ "$ERC" -ne 0 ] && GATE=$ERC
  if [ "$RC" -eq 0 ] && [ "$GATE" -ne 0 ]; then
    echo "not green: pytest passed but the evidence gates refused (skip census $BRC, ledger $ERC)." >&2
    RC=$GATE
  fi
elif [ "$MODE" = "sqlite" ]; then
  echo "collect-only run: nothing executed, so no ceiling is judged and no ledger closes."
fi

exit "$RC"
