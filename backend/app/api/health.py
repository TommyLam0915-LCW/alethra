"""Health check endpoints for deployment monitoring.

Provides:
  GET /health       -- liveness probe (basic, no dependency checks)
  GET /health/ready -- readiness probe (verifies DB, schema and Redis)
"""

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import engine
from app.config import get_settings
from app.services.schema_compat import check_schema_compatibility, summarise

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _check_database() -> dict:
    """Verify database connectivity via a lightweight query."""
    try:
        start = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {"status": "connected", "latency_ms": latency_ms}
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return {"status": "unavailable", "error": "connection failed"}


def _check_schema() -> dict:
    """Read-only: every column the running code maps must exist in the DB.

    Reachability is not compatibility. The 2026-11-01 ``volume_decimal``
    incident had a reachable database stamped at an earlier revision than the
    running code, so readiness said ready while every market-data read failed.
    """
    try:
        with engine.connect() as conn:
            result = check_schema_compatibility(conn)
    except Exception as exc:
        logger.warning("Schema compatibility check failed: %s", exc)
        return {"status": "unknown", "error": "schema check could not run"}
    if result["status"] == "not_applicable":
        return {"status": "not_applicable"}
    if result["status"] == "incompatible":
        logger.error("Readiness blocked: %s", summarise(result))
        return {
            "status": "unavailable",
            "reason": "schema_incompatible",
            "alembic_revision": result.get("alembic_revision"),
            "missing_columns": result.get("missing_columns"),
            "missing_tables": result.get("missing_tables"),
        }
    return {"status": "compatible", "alembic_revision": result.get("alembic_revision")}


def _check_redis() -> dict:
    """Verify Redis connectivity via PING."""
    settings = get_settings()
    if not settings.redis_url:
        return {"status": "not_configured"}

    try:
        import redis as redis_lib

        start = time.monotonic()
        client = redis_lib.from_url(settings.redis_url, socket_timeout=3)
        client.ping()
        client.close()
        latency_ms = round((time.monotonic() - start) * 1000, 2)
        return {"status": "connected", "latency_ms": latency_ms}
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return {"status": "unavailable", "error": "connection failed"}


@router.get("/health")
def health():
    """Liveness probe -- always returns healthy if the process is running."""
    return {"status": "healthy"}


@router.get("/health/ready")
def readiness():
    """Readiness probe -- verifies downstream dependency connectivity.

    Returns 200 only when all critical dependencies are reachable and the
    database schema matches the running code. Returns 503 otherwise.
    """
    db_check = _check_database()
    schema_check = _check_schema() if db_check["status"] == "connected" else {
        "status": "unknown",
        "reason": "database_unreachable",
    }
    redis_check = _check_redis()

    # Database and a compatible schema are critical; Redis is optional
    # (not_configured is acceptable).
    db_ok = db_check["status"] == "connected"
    schema_ok = schema_check["status"] in ("compatible", "not_applicable")
    redis_ok = redis_check["status"] in ("connected", "not_configured")

    overall_ok = db_ok and schema_ok and redis_ok

    body = {
        "status": "ready" if overall_ok else "not_ready",
        "checks": {
            "database": db_check,
            "schema": schema_check,
            "redis": redis_check,
        },
    }
    return JSONResponse(content=body, status_code=200 if overall_ok else 503)
