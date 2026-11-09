#!/usr/bin/env bash
# ──────────────────────────────────────────────
# deploy/down.sh -- Stop all Alethra production services
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"

cd "$PROJECT_ROOT"

REMOVE_VOLUMES="${1:-}"

echo "==> Stopping Alethra production stack..."
if [ "$REMOVE_VOLUMES" = "--volumes" ] || [ "$REMOVE_VOLUMES" = "-v" ]; then
    echo "WARNING: This will DELETE persistent data volumes (postgres + redis)."
    read -rp "Are you sure? (yes/N) " confirm
    if [ "$confirm" = "yes" ]; then
        docker compose -f "$COMPOSE_FILE" down --volumes
        echo "==> Stack stopped and volumes removed."
    else
        echo "Aborted."
        exit 0
    fi
else
    docker compose -f "$COMPOSE_FILE" down
    echo "==> Stack stopped. Data volumes preserved."
    echo "  To also remove volumes:  ./deploy/down.sh --volumes"
fi
