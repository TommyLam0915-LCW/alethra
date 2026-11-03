"""Tests for health check endpoints."""

import hashlib

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


class TestHealthLiveness:
    """GET /health -- liveness probe."""

    def test_returns_healthy(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"


class TestHealthReadiness:
    """GET /health/ready -- readiness probe."""

    def test_ready_when_all_ok(self, client):
        """DB connected + Redis not_configured => ready (200)."""
        with patch("app.api.health._check_database", return_value={"status": "connected", "latency_ms": 1.0}), \
             patch("app.api.health._check_redis", return_value={"status": "not_configured"}):
            resp = client.get("/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
            assert data["checks"]["database"]["status"] == "connected"
            assert data["checks"]["redis"]["status"] == "not_configured"

    def test_ready_includes_latency(self, client):
        with patch("app.api.health._check_database", return_value={"status": "connected", "latency_ms": 2.5}), \
             patch("app.api.health._check_redis", return_value={"status": "not_configured"}):
            resp = client.get("/health/ready")
            data = resp.json()
            assert "latency_ms" in data["checks"]["database"]
            assert data["checks"]["database"]["latency_ms"] == 2.5

    def test_not_ready_when_db_down(self, client):
        """DB unavailable => not_ready (503)."""
        with patch("app.api.health._check_database", return_value={"status": "unavailable", "error": "connection refused"}), \
             patch("app.api.health._check_redis", return_value={"status": "not_configured"}):
            resp = client.get("/health/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["checks"]["database"]["status"] == "unavailable"

    def test_redis_connected(self, client):
        """DB + Redis both connected => ready (200)."""
        with patch("app.api.health._check_database", return_value={"status": "connected", "latency_ms": 1.0}), \
             patch("app.api.health._check_redis", return_value={"status": "connected", "latency_ms": 0.5}):
            resp = client.get("/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["checks"]["redis"]["status"] == "connected"

    def test_redis_unavailable_is_critical_when_configured(self, client):
        """Redis configured but broken => not_ready (503)."""
        with patch("app.api.health._check_database", return_value={"status": "connected", "latency_ms": 1.0}), \
             patch("app.api.health._check_redis", return_value={"status": "unavailable", "error": "connection refused"}):
            resp = client.get("/health/ready")
            assert resp.status_code == 503
            data = resp.json()
            assert data["status"] == "not_ready"
            assert data["checks"]["redis"]["status"] == "unavailable"


class TestReadinessIdentity:
    """The readiness payload carries the identity of the served contract."""

    def test_identity_hashes_the_exact_served_openapi_bytes(self, client):
        with patch("app.api.health._check_database", return_value={"status": "connected", "latency_ms": 1.0}), \
             patch("app.api.health._check_redis", return_value={"status": "not_configured"}):
            ready = client.get("/health/ready")
        identity = ready.json()["identity"]
        served = client.get("/openapi.json").content
        assert identity["openapi_sha256"] == hashlib.sha256(served).hexdigest()
        assert len(identity["openapi_canonical_sha256"]) == 64
        assert identity["openapi_canonical_sha256"] != identity["openapi_sha256"]
        assert identity["info_version"] == "1.0.0"
        assert identity["paths"] > 0
        assert identity["operations"] >= identity["paths"]
        assert "alembic_revision" in identity
        # A fingerprint is not a build id: two builds can share the document
        # while returning different values. The code commit says which one ran.
        commit = identity["code_commit"]
        assert commit is None or (len(commit) == 40 and all(c in "0123456789abcdef" for c in commit))
        assert identity["code_dirty"] in (True, False, None)
        if commit is not None:
            assert identity["code_dirty"] is not None
