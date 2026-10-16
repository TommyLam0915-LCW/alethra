"""Health check endpoints for deployment monitoring.

Provides:
  GET /health       -- liveness probe (basic, no dependency checks)
  GET /health/ready -- readiness probe (verifies DB and Redis connectivity)
"""

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import engine
from app.config import get_settings

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

    Returns 200 only when all critical dependencies are reachable.
    Returns 503 if any critical dependency is unavailable.
    """
    db_check = _check_database()
    redis_check = _check_redis()

    # Database is critical; Redis is optional (not_configured is acceptable)
    db_ok = db_check["status"] == "connected"
    redis_ok = redis_check["status"] in ("connected", "not_configured")

    overall_ok = db_ok and redis_ok

    body = {
        "status": "ready" if overall_ok else "not_ready",
        "checks": {
            "database": db_check,
            "redis": redis_check,
        },
    }
    return JSONResponse(content=body, status_code=200 if overall_ok else 503)
