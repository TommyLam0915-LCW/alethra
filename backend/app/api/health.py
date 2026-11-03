"""Health check endpoints for deployment monitoring.

Provides:
  GET /health       -- liveness probe (basic, no dependency checks)
  GET /health/ready -- readiness probe (verifies DB, schema and Redis, and
                       reports the running API contract identity)
"""

import hashlib
import json
import logging
import subprocess
import time
import weakref
from pathlib import Path

from fastapi import APIRouter, Request
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


_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_IDENTITY_CACHE: "weakref.WeakKeyDictionary[object, dict]" = weakref.WeakKeyDictionary()
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _code_identity() -> dict:
    """The code this process runs, because a fingerprint is not a build id.

    Two builds can share an OpenAPI document while returning different field
    values, so the served hash alone cannot say which one is live. ``null``
    means the checkout is not a git worktree (or git is unavailable), not that
    the tree is clean.
    """
    try:
        commit = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            [
                "git",
                "-C",
                str(_REPO_ROOT),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"code_commit": None, "code_dirty": None}
    commit_sha = commit.stdout.strip()
    if commit.returncode != 0 or not commit_sha:
        return {"code_commit": None, "code_dirty": None}
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    return {"code_commit": commit_sha, "code_dirty": dirty}


def _api_identity(app) -> dict:
    """The served OpenAPI document's identity, computed once per process.

    ``info.version`` stays 1.0.0 because changing it changes the document the
    customer anchors on, so the fingerprint itself is the version marker. The
    raw hash is over the exact bytes ``/openapi.json`` serves (compact,
    ``ensure_ascii=False``); the canonical hash sorts keys and uses compact
    separators so it is stable across serializers.
    """
    cached = _IDENTITY_CACHE.get(app)
    if cached is None:
        spec = app.openapi()
        served = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        paths = spec.get("paths", {})
        cached = {
            "info_version": spec.get("info", {}).get("version"),
            "openapi_sha256": hashlib.sha256(served.encode()).hexdigest(),
            "openapi_canonical_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "paths": len(paths),
            "operations": sum(
                1 for item in paths.values() for method in item if method in _HTTP_METHODS
            ),
            **_code_identity(),
        }
        _IDENTITY_CACHE[app] = cached
    return cached


@router.get("/health")
def health():
    """Liveness probe -- always returns healthy if the process is running."""
    return {"status": "healthy"}


@router.get("/health/ready")
def readiness(request: Request):
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
        "identity": {
            **_api_identity(request.app),
            "alembic_revision": schema_check.get("alembic_revision"),
        },
    }
    return JSONResponse(content=body, status_code=200 if overall_ok else 503)
