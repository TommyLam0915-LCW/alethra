"""Admin endpoint security tests.

Verifies that admin endpoints:
1. Reject unauthenticated requests (401) -- all 5 endpoints
2. Reject non-admin users (403) -- all 5 endpoints
3. Accept authenticated admin users (200) -- all 5 endpoints (service mocked)
4. Reject bypass authentication even when user is admin (403)
5. Service functions are NOT called when request is rejected

The bypass rejection is critical: when research_auth_bypass_enabled is True
and a loopback request arrives, get_current_user() returns the first active
user without checking credentials. If that user is an admin, the bypass could
grant admin access. require_admin() must explicitly reject bypass authentication.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import Request


COLLECTION_ENDPOINTS = [
    ("GET", "/api/v1/admin/collection/status"),
    ("POST", "/api/v1/admin/collection/trigger"),
    ("GET", "/api/v1/admin/collection/history"),
    ("POST", "/api/v1/admin/collection/start"),
    ("POST", "/api/v1/admin/collection/stop"),
]


def _mock_scheduler_status(*args, **kwargs):
    return {"enabled": False, "running": False, "interval_hours": 4, "next_run": None}


def _mock_collection_result(*args, **kwargs):
    return {"runs": 0, "trades": 0, "errors": [], "completed_at": "2026-11-06T00:00:00Z"}


def _mock_collection_history(*args, **kwargs):
    return []


class TestAllEndpointsRejectUnauthenticated:
    """Every admin collection endpoint must return 401 without credentials."""

    @pytest.mark.parametrize("method,path", COLLECTION_ENDPOINTS)
    def test_unauthenticated_rejected(self, client, method, path):
        response = client.request(method, path)
        assert response.status_code == 401, f"{method} {path} must reject unauthenticated"


class TestAllEndpointsRejectNonAdmin:
    """Every admin collection endpoint must return 403 for non-admin users."""

    @pytest.mark.parametrize("method,path", COLLECTION_ENDPOINTS)
    def test_regular_user_rejected(self, client, auth_headers, method, path):
        response = client.request(method, path, headers=auth_headers)
        assert response.status_code == 403, f"{method} {path} must reject non-admin"
        assert "Admin access required" in response.json()["detail"]


class TestAllEndpointsAcceptAdmin:
    """Every admin collection endpoint must return 200 for authenticated admin.

    Service functions are mocked to avoid SQLite JSONB incompatibility.
    """

    @patch("app.api.admin.get_scheduler_status", side_effect=_mock_scheduler_status)
    def test_status_admin_accepted(self, mock_svc, client, admin_headers):
        response = client.get("/api/v1/admin/collection/status", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["running"] is False

    @patch("app.api.admin.trigger_manual_collection", side_effect=_mock_collection_result)
    def test_trigger_admin_accepted(self, mock_svc, client, admin_headers):
        response = client.post("/api/v1/admin/collection/trigger", headers=admin_headers)
        assert response.status_code == 200
        assert mock_svc.called

    @patch("app.api.admin.get_collection_history", side_effect=_mock_collection_history)
    def test_history_admin_accepted(self, mock_svc, client, admin_headers):
        response = client.get("/api/v1/admin/collection/history", headers=admin_headers)
        assert response.status_code == 200
        assert response.json() == []

    @patch("app.api.admin.init_scheduler")
    def test_start_admin_accepted(self, mock_svc, client, admin_headers):
        response = client.post("/api/v1/admin/collection/start", headers=admin_headers)
        assert response.status_code == 200
        assert mock_svc.called

    @patch("app.api.admin.stop_scheduler")
    def test_stop_admin_accepted(self, mock_svc, client, admin_headers):
        response = client.post("/api/v1/admin/collection/stop", headers=admin_headers)
        assert response.status_code == 200
        assert mock_svc.called


class TestServiceNotCalledOnRejection:
    """When a request is rejected, the underlying service function must NOT be called."""

    @patch("app.api.admin.get_scheduler_status", side_effect=_mock_scheduler_status)
    def test_status_service_not_called_for_unauth(self, mock_svc, client):
        client.get("/api/v1/admin/collection/status")
        mock_svc.assert_not_called()

    @patch("app.api.admin.get_scheduler_status", side_effect=_mock_scheduler_status)
    def test_status_service_not_called_for_non_admin(self, mock_svc, client, auth_headers):
        client.get("/api/v1/admin/collection/status", headers=auth_headers)
        mock_svc.assert_not_called()

    @patch("app.api.admin.trigger_manual_collection", side_effect=_mock_collection_result)
    def test_trigger_service_not_called_for_non_admin(self, mock_svc, client, auth_headers):
        client.post("/api/v1/admin/collection/trigger", headers=auth_headers)
        mock_svc.assert_not_called()

    @patch("app.api.admin.init_scheduler")
    def test_start_service_not_called_for_non_admin(self, mock_svc, client, auth_headers):
        client.post("/api/v1/admin/collection/start", headers=auth_headers)
        mock_svc.assert_not_called()

    @patch("app.api.admin.stop_scheduler")
    def test_stop_service_not_called_for_non_admin(self, mock_svc, client, auth_headers):
        client.post("/api/v1/admin/collection/stop", headers=auth_headers)
        mock_svc.assert_not_called()

    @patch("app.api.admin.stop_scheduler")
    def test_stop_service_not_called_for_unauth(self, mock_svc, client):
        client.post("/api/v1/admin/collection/stop")
        mock_svc.assert_not_called()


class TestBypassRejection:
    """require_admin must reject bypass authentication through the full chain."""

    def test_require_admin_unit_rejects_bypass(self):
        """Unit test: require_admin() rejects when auth_amr contains 'bypass'."""
        from app.auth.jwt import require_admin
        from app.models.user import User
        from fastapi import HTTPException

        mock_user = MagicMock(spec=User)
        mock_user.is_admin = True

        mock_request = MagicMock(spec=Request)
        mock_request.state.auth_amr = ["bypass"]

        with pytest.raises(HTTPException) as exc_info:
            require_admin(request=mock_request, current_user=mock_user)
        assert exc_info.value.status_code == 403
        assert "bypass" in exc_info.value.detail.lower()

    def test_require_admin_unit_accepts_real_admin(self):
        """Unit test: require_admin() accepts real auth for admin user."""
        from app.auth.jwt import require_admin
        from app.models.user import User

        mock_user = MagicMock(spec=User)
        mock_user.is_admin = True

        mock_request = MagicMock(spec=Request)
        mock_request.state.auth_amr = ["pwd"]

        result = require_admin(request=mock_request, current_user=mock_user)
        assert result is mock_user

    def test_require_admin_unit_rejects_non_admin(self):
        """Unit test: require_admin() rejects non-admin with real auth."""
        from app.auth.jwt import require_admin
        from app.models.user import User
        from fastapi import HTTPException

        mock_user = MagicMock(spec=User)
        mock_user.is_admin = False

        mock_request = MagicMock(spec=Request)
        mock_request.state.auth_amr = ["pwd"]

        with pytest.raises(HTTPException) as exc_info:
            require_admin(request=mock_request, current_user=mock_user)
        assert exc_info.value.status_code == 403

    @patch("app.api.admin.get_scheduler_status", side_effect=_mock_scheduler_status)
    def test_bypass_admin_rejected_via_http(self, mock_svc, client):
        """Full HTTP test: bypass-authenticated admin must be rejected at admin endpoint.

        Uses FastAPI dependency override to simulate the bypass path:
        get_current_user returns an admin user with auth_amr=['bypass'] set
        on request.state. This tests the full chain:
        get_current_user -> request.state.auth_amr -> require_admin -> 403.
        """
        from app.main import app
        from app.auth.jwt import get_current_user, get_password_hash, security
        from app.models.user import User
        from app.database import get_db
        from fastapi import Depends
        from fastapi.security import HTTPAuthorizationCredentials
        from sqlalchemy.orm import Session
        from starlette.requests import Request

        db = next(get_db())
        try:
            admin = User(
                email="bypass-http-admin@alethra.io",
                password_hash=get_password_hash("temppass123"),
                display_name="Bypass HTTP Admin",
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

            def bypass_get_current_user(
                request: Request,
                credentials: HTTPAuthorizationCredentials = Depends(security),
                db: Session = Depends(get_db),
            ):
                request.state.auth_amr = ["bypass"]
                request.state.api_key_scopes = None
                request.state.api_key_id = None
                return admin

            app.dependency_overrides[get_current_user] = bypass_get_current_user
            try:
                response = client.get("/api/v1/admin/collection/status")
            finally:
                app.dependency_overrides.pop(get_current_user, None)

            assert response.status_code == 403
            assert "bypass" in response.json()["detail"].lower()
            mock_svc.assert_not_called()
        finally:
            db.rollback()
            db.query(User).filter(User.email == "bypass-http-admin@alethra.io").delete()
            db.commit()
            db.close()
