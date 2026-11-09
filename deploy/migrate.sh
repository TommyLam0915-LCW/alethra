#!/usr/bin/env bash
# ──────────────────────────────────────────────
# deploy/migrate.sh -- Run Alembic database migrations
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"

cd "$PROJECT_ROOT"

echo "==> Running database migrations..."
docker compose -f "$COMPOSE_FILE" exec -T backend alembic upgrade head

echo "==> Migrations complete."
echo "  Current revision:"
docker compose -f "$COMPOSE_FILE" exec -T backend alembic current
