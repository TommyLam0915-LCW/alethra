#!/usr/bin/env bash
# ──────────────────────────────────────────────
# deploy/up.sh -- Start all Alethra production services
# ──────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"

cd "$PROJECT_ROOT"

# ── Preflight checks ─────────────────────────────────────────
if [ ! -f .env ]; then
    echo "ERROR: .env file not found."
    echo "  cp .env.example .env  &&  edit .env with real values."
    exit 1
fi

# Verify required secrets are set (non-empty, stripping quotes)
for var in POSTGRES_PASSWORD REDIS_PASSWORD JWT_SECRET_KEY; do
    val=$(grep "^${var}=" .env | cut -d= -f2- | tr -d '"' | tr -d "'")
    if [ -z "$val" ]; then
        echo "ERROR: $var is not set in .env"
        exit 1
    fi
done

echo "==> Building Alethra production images..."
docker compose -f "$COMPOSE_FILE" build

echo "==> Starting infrastructure (db, redis)..."
docker compose -f "$COMPOSE_FILE" up -d db redis

# Wait for db to accept connections before migrating.
for i in $(seq 1 30); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U "$(grep '^POSTGRES_USER=' .env | cut -d= -f2)" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Migrations MUST run before the app starts: the backend auto-creates the
# schema at import time (Base.metadata.create_all), which would leave the
# database without an alembic_version — running the chain afterwards fails
# on the unguarded create_table calls in the initial migration.
echo "==> Running database migrations..."
docker compose -f "$COMPOSE_FILE" run --rm backend alembic upgrade head
docker compose -f "$COMPOSE_FILE" run --rm backend alembic current

echo "==> Starting all services..."
docker compose -f "$COMPOSE_FILE" up -d

echo ""
echo "==> Waiting for services to become healthy..."
for i in $(seq 1 60); do
    unhealthy=$(docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null \
        | grep -c '"unhealthy"\|"starting"' || true)
    if [ "$unhealthy" -eq 0 ]; then
        echo "==> All services are healthy."
        docker compose -f "$COMPOSE_FILE" ps
        exit 0
    fi
    sleep 2
done

echo "WARNING: Some services did not become healthy within 120s."
docker compose -f "$COMPOSE_FILE" ps
echo "Check logs:  docker compose -f $COMPOSE_FILE logs <service>"
exit 1
