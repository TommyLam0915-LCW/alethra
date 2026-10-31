#!/usr/bin/env bash
# ──────────────────────────────────────────────
# deploy/backup.sh -- Logical backup of the compose PostgreSQL database
# ──────────────────────────────────────────────
#
# Exit codes:
#   0  backup written, integrity-checked, retention applied
#   1  configuration problem (missing compose file, DB name/user, bad limits)
#   2  target unavailable or not the requested database
#   3  pg_dump failed or timed out (no backup file is left behind)
#   4  the dump did not pass the integrity checks (no backup file is published)
#
# Parameters (environment):
#   BACKUP_DIR             destination directory (default <project>/backups)
#   BACKUP_SERVICE         compose service name (default: db)
#   BACKUP_DB_NAME         database to dump (default: POSTGRES_DB, else refuse)
#   BACKUP_DB_USER         role used by pg_dump (default: POSTGRES_USER, else refuse)
#   BACKUP_KEEP            backups retained after a successful run (default 7)
#   BACKUP_TIMEOUT_SECONDS per-command limit (default 900)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
DB_SERVICE="${BACKUP_SERVICE:-db}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"
BACKUP_TIMEOUT_SECONDS="${BACKUP_TIMEOUT_SECONDS:-900}"

fail() {
    local code="$1"; shift
    echo "BACKUP FAILED (exit $code): $*" >&2
    exit "$code"
}

# Never echo a connection string or password from a target's error output.
redact() {
    sed -E \
        -e 's#(postgres(ql)?(\+[A-Za-z0-9]+)?://)[^[:space:]]+#\1***#g' \
        -e 's#((PG)?PASSWORD=)[^[:space:]]+#\1***#g'
}

report_error_log() {
    local file="$1"
    [ -s "$file" ] || return 0
    echo "  target said (redacted):" >&2
    redact <"$file" | tail -n 5 | sed 's/^/    /' >&2
}

# Runs a command with a hard limit and no reliance on GNU timeout being present.
run_with_timeout() {
    local seconds="$1"; shift
    local ticks=0 limit=$((seconds * 5)) state
    "$@" &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
        case "$state" in *Z*) break ;; esac
        if [ "$ticks" -ge "$limit" ]; then
            echo "timed out after ${seconds}s" >&2
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 0.2
        ticks=$((ticks + 1))
    done
    wait "$pid"
}

cd "$PROJECT_ROOT"

[ -f "$COMPOSE_FILE" ] || fail 1 "compose file not found: $COMPOSE_FILE"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

DB_NAME="${BACKUP_DB_NAME:-${POSTGRES_DB:-}}"
DB_USER="${BACKUP_DB_USER:-${POSTGRES_USER:-}}"
[ -n "$DB_NAME" ] || fail 1 "database name is not set; set BACKUP_DB_NAME or POSTGRES_DB"
[ -n "$DB_USER" ] || fail 1 "database user is not set; set BACKUP_DB_USER or POSTGRES_USER"
case "$BACKUP_KEEP" in
    ''|*[!0-9]*) fail 1 "BACKUP_KEEP must be a positive integer, got '$BACKUP_KEEP'" ;;
esac
[ "$BACKUP_KEEP" -ge 1 ] || fail 1 "BACKUP_KEEP must be at least 1"
case "$BACKUP_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) fail 1 "BACKUP_TIMEOUT_SECONDS must be a positive integer, got '$BACKUP_TIMEOUT_SECONDS'" ;;
esac
[ "$BACKUP_TIMEOUT_SECONDS" -ge 1 ] || fail 1 "BACKUP_TIMEOUT_SECONDS must be at least 1"
command -v docker >/dev/null 2>&1 || fail 1 "docker CLI is not available on PATH"
command -v gzip >/dev/null 2>&1 || fail 1 "gzip is not available on PATH"

mkdir -p "$BACKUP_DIR"
WORK_DIR="$(mktemp -d "$BACKUP_DIR/.backup-work.XXXXXX")" || fail 1 "cannot create a work directory under $BACKUP_DIR"
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

echo "==> Target: service $DB_SERVICE, database $DB_NAME, user $DB_USER"

RUNNING="$(docker compose -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null || true)"
printf '%s\n' "$RUNNING" | grep -qx "$DB_SERVICE" \
    || fail 2 "compose service $DB_SERVICE is not running; refusing to guess a target"

# Wrong-target protection: the server must report the database we asked for.
IDENTITY="$(run_with_timeout "$BACKUP_TIMEOUT_SECONDS" \
    docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -Atc \
    "select current_database() || '|' || current_setting('server_version_num')" \
    2>"$WORK_DIR/identity.log")" || {
    report_error_log "$WORK_DIR/identity.log"
    fail 2 "could not query database $DB_NAME inside service $DB_SERVICE"
}
REACHED_DB="${IDENTITY%%|*}"
REACHED_VERSION="${IDENTITY##*|}"
[ "$REACHED_DB" = "$DB_NAME" ] \
    || fail 2 "server reports database '$REACHED_DB', not the requested '$DB_NAME'; refusing to back up an unexpected target"
case "$REACHED_VERSION" in
    ''|*[!0-9]*) fail 2 "could not read the PostgreSQL server version from the target" ;;
esac
echo "==> Target verified: database $REACHED_DB, PostgreSQL $((REACHED_VERSION / 10000))"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/alethra_db_$TIMESTAMP.sql.gz"
PARTIAL_FILE="$BACKUP_DIR/.alethra_db_$TIMESTAMP.sql.gz.partial"
[ ! -e "$BACKUP_FILE" ] || fail 1 "backup file $BACKUP_FILE already exists; refusing to overwrite it"

echo "==> Backing up database to $BACKUP_FILE ..."
if ! run_with_timeout "$BACKUP_TIMEOUT_SECONDS" \
        docker compose -f "$COMPOSE_FILE" exec -T "$DB_SERVICE" pg_dump -U "$DB_USER" -d "$DB_NAME" \
        >"$WORK_DIR/dump.sql" 2>"$WORK_DIR/pg_dump.log"; then
    report_error_log "$WORK_DIR/pg_dump.log"
    fail 3 "pg_dump failed or timed out; no backup file was written"
fi
[ -s "$WORK_DIR/dump.sql" ] || fail 4 "pg_dump produced an empty file"

# pg_dump ends with this line only when the whole dump completed.
tail -n 5 "$WORK_DIR/dump.sql" | grep -q "PostgreSQL database dump complete" \
    || fail 4 "pg_dump output has no completion line; refusing to publish it as a backup"

gzip -c "$WORK_DIR/dump.sql" >"$PARTIAL_FILE" || {
    rm -f "$PARTIAL_FILE"
    fail 4 "gzip failed; no backup file was written"
}
gzip -t "$PARTIAL_FILE" 2>/dev/null || {
    rm -f "$PARTIAL_FILE"
    fail 4 "the compressed backup failed its integrity check"
}
mv "$PARTIAL_FILE" "$BACKUP_FILE" || {
    rm -f "$PARTIAL_FILE"
    fail 4 "could not move the verified backup into place"
}
chmod 600 "$BACKUP_FILE"

SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "==> Backup complete: $BACKUP_FILE ($SIZE)"
echo ""
echo "  Restore with:"
echo "    gunzip -c $BACKUP_FILE | docker compose -f $COMPOSE_FILE exec -T $DB_SERVICE \\"
echo "      psql -U $DB_USER $DB_NAME"

# ── Retention: keep the last N verified backups ───────────────
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/alethra_db_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt "$BACKUP_KEEP" ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - BACKUP_KEEP))
    echo "==> Pruning $REMOVE_COUNT old backup(s) (keeping last $BACKUP_KEEP)..."
    ls -1t "$BACKUP_DIR"/alethra_db_*.sql.gz | tail -n "$REMOVE_COUNT" | xargs rm -f
fi
