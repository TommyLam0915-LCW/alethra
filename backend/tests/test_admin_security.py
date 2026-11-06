"""Admin endpoint security tests.

Verifies that admin endpoints:
1. Reject unauthenticated requests (401)
2. Reject non-admin users (403)
3. Accept authenticated admin users (200)
4. Reject bypass authentication even when user is admin (403)

The bypass rejection is critical: when research_auth_bypass_enabled is True
and a loopback request arrives, get_current_user() returns the first active
user without checking credentials. If that user is an admin, the bypass could
grant admin access. require_admin() must explicitly reject bypass authentication.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi import Request
from starlette.datastructures import Headers


class TestAdminEndpointSecurity:
    """Admin collection endpoints must enforce admin authorization."""

    def test_unauthenticated_request_rejected(self, client):
        """Unauthenticated requests must receive 401."""
        response = client.get("/api/v1/admin/collection/status")
        assert response.status_code == 401

    def test_regular_user_rejected(self, client, auth_headers):
        """Non-admin users must receive 403."""
        response = client.get(
            "/api/v1/admin/collection/status",
            headers=auth_headers,
        )
        assert response.status_code == 403
        assert "Admin access required" in response.json()["detail"]

    def test_admin_user_accepted(self, client, admin_headers):
        """Authenticated admin users must receive 200."""
        response = client.get(
            "/api/v1/admin/collection/status",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "running" in data
        assert "interval_hours" in data

    def test_require_admin_rejects_bypass_auth_method(self):
        """require_admin() must reject requests authenticated via bypass.

        Directly unit-test the require_admin dependency: when auth_amr
        contains 'bypass', the function must raise 403 regardless of
        whether the user is an admin.
        """
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

    def test_require_admin_accepts_real_auth_for_admin(self):
        """require_admin() must accept properly authenticated admin users."""
        from app.auth.jwt import require_admin
        from app.models.user import User

        mock_user = MagicMock(spec=User)
        mock_user.is_admin = True

        mock_request = MagicMock(spec=Request)
        mock_request.state.auth_amr = ["pwd"]

        result = require_admin(request=mock_request, current_user=mock_user)
        assert result is mock_user

    def test_require_admin_rejects_non_admin_with_real_auth(self):
        """require_admin() must reject non-admin users even with real auth."""
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

    def test_collection_status_endpoints_enforce_admin(self, client, admin_headers, auth_headers):
        """Collection status/start/stop endpoints must enforce admin authorization.

        Note: /collection/history and /collection/trigger are excluded because
        they use PostgreSQL-specific JSONB operators that fail on the SQLite
        test database. This is a pre-existing issue in auto_collection.py,
        not related to the authorization fix.
        """
        read_endpoints = [
            ("GET", "/api/v1/admin/collection/status"),
        ]
        write_endpoints = [
            ("POST", "/api/v1/admin/collection/start"),
            ("POST", "/api/v1/admin/collection/stop"),
        ]

        for method, path in read_endpoints + write_endpoints:
            unauth_response = client.request(method, path)
            assert unauth_response.status_code == 401, f"{method} {path} should reject unauthenticated"

            user_response = client.request(method, path, headers=auth_headers)
            assert user_response.status_code == 403, f"{method} {path} should reject non-admin"

            admin_response = client.request(method, path, headers=admin_headers)
            assert admin_response.status_code in [200, 201], f"{method} {path} should accept admin (got {admin_response.status_code})"
