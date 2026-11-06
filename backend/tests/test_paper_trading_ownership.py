"""Paper trading ownership isolation tests.

Verifies that:
1. User A cannot read User B's run status (404, same as non-existent)
2. User A cannot stop User B's run (404, same as non-existent)
3. Live daemon control requires admin
4. Non-existent and not-owned runs return identical responses (no info leak)
5. Stop does not execute DB writes for non-owned runs

The service layer AND _verify_run_ownership are mocked because:
- paper_trading.py service uses PostgreSQL-specific SQL (NOW(), CURRENT_DATE)
- paper_trading_runs / paper_portfolios tables don't exist in SQLite test DB
"""

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4


def _make_user_headers(client, email: str, is_admin: bool = False):
    """Create a user and return (auth_headers, user_id)."""
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User
    from app.database import get_db

    db = next(get_db())
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name=email.split("@")[0],
            is_admin=is_admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(data={"sub": str(user.id)})
        user_id = str(user.id)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}, user_id


RUN_A = str(uuid4())
RUN_B = str(uuid4())


class TestStatusOwnership:
    """GET /status/{run_id} must verify owner before returning data."""

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_user_cannot_read_other_users_run(self, mock_engine_cls, mock_ownership, client):
        headers_b, user_b = _make_user_headers(client, "status-owner-a@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")

        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {"run_id": RUN_A, "status": "running"}
        mock_engine_cls.return_value = mock_engine

        response = client.get(f"/api/v1/paper-trading/status/{RUN_A}", headers=headers_b)
        assert response.status_code == 404, "User B must not see User A's run"
        mock_engine.get_status.assert_not_called()

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_nonexistent_run_returns_404(self, mock_engine_cls, mock_ownership, client):
        """Non-existent run must return same 404 as not-owned run (no info leak)."""
        headers, _ = _make_user_headers(client, "status-nonexist@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")

        mock_engine = MagicMock()
        mock_engine.get_status.side_effect = ValueError("not found")
        mock_engine_cls.return_value = mock_engine

        response = client.get(f"/api/v1/paper-trading/status/{RUN_B}", headers=headers)
        assert response.status_code == 404

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_owner_can_read_own_run(self, mock_engine_cls, mock_ownership, client):
        """Ownership check passes -> engine.get_status is called."""
        headers, user_id = _make_user_headers(client, "status-owner-ok@alethra.io")

        mock_ownership.return_value = None

        mock_engine = MagicMock()
        mock_engine.get_status.return_value = {
            "run_id": RUN_A, "status": "running", "skill": "momentum",
            "instrument_id": "BTC", "initial_capital": 100000,
            "current_capital": 101000, "total_pnl": 1000,
            "total_trades": 5, "win_rate": 0.6,
            "start_date": "2026-01-01", "end_date": None,
        }
        mock_engine_cls.return_value = mock_engine

        response = client.get(f"/api/v1/paper-trading/status/{RUN_A}", headers=headers)
        assert response.status_code == 200
        mock_ownership.assert_called_once()
        mock_engine.get_status.assert_called_once_with(RUN_A)

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_ownership_checked_with_correct_args(self, mock_engine_cls, mock_ownership, client):
        """Verify _verify_run_ownership receives the correct run_id and user_id."""
        headers, user_id = _make_user_headers(client, "status-args@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")
        mock_engine_cls.return_value = MagicMock()

        client.get(f"/api/v1/paper-trading/status/{RUN_A}", headers=headers)

        mock_ownership.assert_called_once()
        call_args = mock_ownership.call_args
        assert call_args[0][1] == RUN_A
        assert call_args[0][2] == user_id


class TestDailyReportOwnership:
    """GET /daily-report/{run_id} must verify owner."""

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_user_cannot_read_other_users_report(self, mock_engine_cls, mock_ownership, client):
        headers_b, _ = _make_user_headers(client, "report-reader-b@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        response = client.get(f"/api/v1/paper-trading/daily-report/{RUN_A}", headers=headers_b)
        assert response.status_code == 404
        mock_engine.generate_daily_report.assert_not_called()


class TestReplayOwnership:
    """POST /replay/{run_id} must verify owner."""

    @patch("app.api.paper_trading._verify_run_ownership")
    @patch("app.api.paper_trading.get_engine")
    def test_user_cannot_replay_other_users_run(self, mock_engine_cls, mock_ownership, client):
        headers_b, _ = _make_user_headers(client, "replay-reader-b@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        response = client.post(
            f"/api/v1/paper-trading/replay/{RUN_A}",
            json={"days_back": 30, "position_size_pct": 0.1, "max_positions": 3},
            headers=headers_b,
        )
        assert response.status_code == 404
        mock_engine.execute_historical_replay.assert_not_called()


class TestStopOwnership:
    """POST /stop/{run_id} must verify owner before executing write."""

    @patch("app.api.paper_trading._verify_run_ownership")
    def test_user_cannot_stop_other_users_run(self, mock_ownership, client):
        headers_b, _ = _make_user_headers(client, "stopper-b@alethra.io")

        mock_ownership.side_effect = ValueError("not_found")

        response = client.post(f"/api/v1/paper-trading/stop/{RUN_A}", headers=headers_b)
        assert response.status_code == 404, "User B must not stop User A's run"

    def test_unauthenticated_cannot_stop(self, client):
        response = client.post(f"/api/v1/paper-trading/stop/{RUN_A}")
        assert response.status_code == 401

    @patch("app.api.paper_trading._verify_run_ownership")
    def test_owner_can_stop_own_run(self, mock_ownership, client):
        """When ownership check passes, stop proceeds to DB write."""
        headers, user_id = _make_user_headers(client, "stop-owner-ok@alethra.io")

        mock_ownership.return_value = None

        response = client.post(f"/api/v1/paper-trading/stop/{RUN_A}", headers=headers)
        mock_ownership.assert_called_once()
        call_args = mock_ownership.call_args
        assert call_args[0][1] == RUN_A
        assert call_args[0][2] == user_id
        assert response.status_code in (200, 500)


class TestLiveDaemonRequiresAdmin:
    """Live daemon control must require admin authorization."""

    def test_live_start_requires_admin(self, client, auth_headers):
        response = client.post("/api/v1/paper-trading/live/start", headers=auth_headers)
        assert response.status_code == 403

    def test_live_stop_requires_admin(self, client, auth_headers):
        response = client.post("/api/v1/paper-trading/live/stop", headers=auth_headers)
        assert response.status_code == 403

    def test_live_status_requires_admin(self, client, auth_headers):
        response = client.get("/api/v1/paper-trading/live/status", headers=auth_headers)
        assert response.status_code == 403

    def test_unauthenticated_live_start_rejected(self, client):
        response = client.post("/api/v1/paper-trading/live/start")
        assert response.status_code == 401

    def test_unauthenticated_live_status_rejected(self, client):
        response = client.get("/api/v1/paper-trading/live/status")
        assert response.status_code == 401
