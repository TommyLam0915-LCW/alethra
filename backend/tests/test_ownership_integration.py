"""PostgreSQL integration tests for paper-trading ownership isolation.

Tests the FULL HTTP chain: create users → create portfolios → create runs →
verify ownership via API endpoints (status, daily-report, replay, stop).

Calls actual _verify_run_ownership() through the API — no SQL copies.
Uses the disposable test database (ai_investment_lab_test), NOT production.

Run with:
    ALETHRA_TEST_DB=postgresql://postgres:postgres@localhost:5432/ai_investment_lab_test \
    pytest tests/test_ownership_integration.py -v
"""

import os
import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

# Safety: refuse to run against production database
_test_db_url = os.environ.get("ALETHRA_TEST_DB", "")
if "ai_investment_lab_test" not in _test_db_url and _test_db_url.startswith("postgresql"):
    raise RuntimeError(
        f"SAFETY: Integration tests require ALETHRA_TEST_DB pointing to "
        f"ai_investment_lab_test, got: {_test_db_url}"
    )


@pytest.fixture(autouse=True)
def ensure_paper_trading_tables():
    """Create raw-SQL tables that conftest's setup_db doesn't know about.

    paper_trading_runs and paper_trading_daily_metrics are created by raw SQL
    in the service layer, not by SQLAlchemy models, so they're not in
    Base.metadata and don't get recreated by conftest's create_all().
    """
    from tests.conftest import TEST_ENGINE
    from sqlalchemy import text

    with TEST_ENGINE.connect() as conn:
        # paper_trading_runs depends on paper_portfolios (FK), which IS in Base.metadata
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS paper_trading_runs (
                id uuid NOT NULL DEFAULT gen_random_uuid(),
                portfolio_id uuid NOT NULL,
                skill character varying NOT NULL,
                instrument_id character varying NOT NULL,
                interval_minutes integer NOT NULL,
                exit_config character varying NOT NULL,
                start_date timestamp without time zone NOT NULL,
                end_date date,
                status character varying DEFAULT 'running',
                initial_capital numeric(15,2) NOT NULL,
                final_capital numeric(15,2),
                total_pnl numeric(15,2) DEFAULT 0,
                total_trades integer DEFAULT 0,
                win_rate numeric(5,2),
                sharpe_ratio numeric(8,4),
                max_drawdown_pct numeric(8,4),
                created_at timestamp with time zone DEFAULT now(),
                updated_at timestamp with time zone DEFAULT now(),
                run_type character varying(50) DEFAULT 'historical_simulation',
                CONSTRAINT paper_trading_runs_pkey PRIMARY KEY (id),
                CONSTRAINT paper_trading_runs_portfolio_id_fkey FOREIGN KEY (portfolio_id) REFERENCES paper_portfolios(id)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_paper_trading_runs_portfolio
            ON paper_trading_runs(portfolio_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_paper_trading_runs_status
            ON paper_trading_runs(status)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS paper_trading_daily_metrics (
                id uuid NOT NULL DEFAULT gen_random_uuid(),
                run_id uuid NOT NULL,
                date date NOT NULL,
                trades_count integer DEFAULT 0,
                wins integer DEFAULT 0,
                losses integer DEFAULT 0,
                daily_pnl numeric(15,2) DEFAULT 0,
                cumulative_pnl numeric(15,2) DEFAULT 0,
                drawdown_pct numeric(8,4) DEFAULT 0,
                sharpe_7d numeric(8,4),
                created_at timestamp with time zone DEFAULT now(),
                CONSTRAINT paper_trading_daily_metrics_pkey PRIMARY KEY (id),
                CONSTRAINT paper_trading_daily_metrics_run_id_date_key UNIQUE (run_id, date),
                CONSTRAINT paper_trading_daily_metrics_run_id_fkey FOREIGN KEY (run_id) REFERENCES paper_trading_runs(id)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_paper_trading_daily_run_date
            ON paper_trading_daily_metrics(run_id, date)
        """))
        conn.commit()

    yield

    # Cleanup: drop in reverse dependency order
    with TEST_ENGINE.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS paper_trading_daily_metrics CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS paper_trading_runs CASCADE"))
        conn.commit()


def _make_user(client, email: str, is_admin: bool = False):
    """Register a user and return (auth_headers, user_id)."""
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User
    from tests.conftest import TestSession

    db = TestSession()
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


def _create_portfolio_via_api(client, headers, skill="momentum", instrument_id="BTC-USD"):
    """Create a portfolio via the formal API and return (portfolio_id, run_id)."""
    resp = client.post(
        "/api/v1/paper-trading/create",
        headers=headers,
        json={
            "skill": skill,
            "instrument_id": f"CRYPTO:KRAKEN:{instrument_id}",
            "interval_minutes": 60,
            "exit_config": "exit_1",
            "initial_capital": 100000.0,
        },
    )
    assert resp.status_code == 200, f"Portfolio creation failed: {resp.json()}"
    data = resp.json()
    return data["portfolio_id"], data["run_id"]


class TestHTTPOwnershipIsolation:
    """Test ownership isolation through the actual HTTP API."""

    def test_user_can_read_own_run_status(self, client):
        """User A can read status of their own run."""
        headers_a, user_id_a = _make_user(client, "owner_a@test.com")
        portfolio_id, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_a)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        assert resp.json()["run_id"] == run_id

    def test_user_cannot_read_other_users_run_status(self, client):
        """User B gets 404 when reading User A's run status."""
        headers_a, _ = _make_user(client, "owner_a2@test.com")
        headers_b, _ = _make_user(client, "owner_b2@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 for User A's run, got {resp.status_code}"
        )

    def test_user_cannot_read_other_users_daily_report(self, client):
        """User B gets 404 when reading User A's daily report."""
        headers_a, _ = _make_user(client, "owner_a3@test.com")
        headers_b, _ = _make_user(client, "owner_b3@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/daily-report/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 for User A's daily report, got {resp.status_code}"
        )

    def test_user_cannot_stop_other_users_run(self, client):
        """User B gets 404 when trying to stop User A's run."""
        headers_a, _ = _make_user(client, "owner_a4@test.com")
        headers_b, _ = _make_user(client, "owner_b4@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.post(f"/api/v1/paper-trading/stop/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 when stopping User A's run, got {resp.status_code}"
        )

    def test_nonexistent_run_returns_404_not_500(self, client):
        """Non-existent run returns 404, not 500."""
        headers_a, _ = _make_user(client, "owner_a5@test.com")
        fake_run_id = str(uuid.uuid4())

        resp = client.get(f"/api/v1/paper-trading/status/{fake_run_id}", headers=headers_a)
        assert resp.status_code == 404, (
            f"Non-existent run must return 404, got {resp.status_code}"
        )

    def test_rejection_has_no_side_effects(self, client):
        """After User B's rejected access attempt, User A's run is unchanged."""
        headers_a, user_id_a = _make_user(client, "owner_a6@test.com")
        headers_b, _ = _make_user(client, "owner_b6@test.com")
        portfolio_id, run_id = _create_portfolio_via_api(client, headers_a)

        # User B tries to access User A's run (should fail)
        resp_b = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_b)
        assert resp_b.status_code == 404

        # User A's run should still be accessible and unchanged
        resp_a = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_a)
        assert resp_a.status_code == 200
        assert resp_a.json()["run_id"] == run_id

        # User B should have no portfolios or runs
        resp_b_history = client.get("/api/v1/paper-trading/history", headers=headers_b)
        assert resp_b_history.status_code == 200
        b_runs = resp_b_history.json()
        assert len(b_runs) == 0, (
            f"User B must have 0 runs after rejected access, got {len(b_runs)}"
        )

    def test_multiple_runs_per_user_isolated(self, client):
        """User with multiple runs can access all; other user can access none."""
        headers_a, _ = _make_user(client, "owner_a7@test.com")
        headers_b, _ = _make_user(client, "owner_b7@test.com")

        _, run_a1 = _create_portfolio_via_api(client, headers_a, skill="momentum")
        _, run_a2 = _create_portfolio_via_api(client, headers_a, skill="mean_reversion")

        # User A can access both
        assert client.get(f"/api/v1/paper-trading/status/{run_a1}", headers=headers_a).status_code == 200
        assert client.get(f"/api/v1/paper-trading/status/{run_a2}", headers=headers_a).status_code == 200

        # User B cannot access either
        assert client.get(f"/api/v1/paper-trading/status/{run_a1}", headers=headers_b).status_code == 404
        assert client.get(f"/api/v1/paper-trading/status/{run_a2}", headers=headers_b).status_code == 404


class TestVerifyRunOwnershipDirect:
    """Test the actual _verify_run_ownership() function (not a SQL copy)."""

    def test_verify_run_ownership_succeeds_for_owner(self, client):
        """_verify_run_ownership() returns None (success) for the owner."""
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        headers_a, user_id_a = _make_user(client, "direct_a@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        db = TestSession()
        try:
            # Should not raise
            _verify_run_ownership(db, run_id, user_id_a)
        finally:
            db.close()

    def test_verify_run_ownership_raises_for_non_owner(self, client):
        """_verify_run_ownership() raises ValueError for non-owner."""
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        headers_a, _ = _make_user(client, "direct_a2@test.com")
        _, user_id_b = _make_user(client, "direct_b2@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        db = TestSession()
        try:
            with pytest.raises(ValueError, match="not_found"):
                _verify_run_ownership(db, run_id, user_id_b)
        finally:
            db.close()

    def test_verify_run_ownership_raises_for_nonexistent(self, client):
        """_verify_run_ownership() raises ValueError for non-existent run."""
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        headers_a, user_id_a = _make_user(client, "direct_a3@test.com")
        fake_run_id = str(uuid.uuid4())

        db = TestSession()
        try:
            with pytest.raises(ValueError, match="not_found"):
                _verify_run_ownership(db, fake_run_id, user_id_a)
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
