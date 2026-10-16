#!/usr/bin/env bash
# ──────────────────────────────────────────────
# deploy/backup.sh -- Backup the PostgreSQL database
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"

cd "$PROJECT_ROOT"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/alethra_db_$TIMESTAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "==> Backing up database to $BACKUP_FILE ..."
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_dump -U "${POSTGRES_USER:-alethra}" "${POSTGRES_DB:-alethra}" \
    | gzip > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file is empty — pg_dump may have failed" >&2
    rm -f "$BACKUP_FILE"
    exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "==> Backup complete: $BACKUP_FILE ($SIZE)"
echo ""
echo "  Restore with:"
echo "    gunzip -c $BACKUP_FILE | docker compose -f $COMPOSE_FILE exec -T db \\"
echo "      psql -U \${POSTGRES_USER} \${POSTGRES_DB}"

# ── Retention: keep last 7 backups ────────────────────────────
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/alethra_db_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt 7 ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - 7))
    echo "==> Pruning $REMOVE_COUNT old backup(s) (keeping last 7)..."
    ls -1t "$BACKUP_DIR"/alethra_db_*.sql.gz | tail -n "$REMOVE_COUNT" | xargs rm -f
fi
