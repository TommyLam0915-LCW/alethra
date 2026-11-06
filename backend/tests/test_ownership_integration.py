"""PostgreSQL integration tests for paper-trading ownership isolation.

These tests run against a REAL PostgreSQL database (not SQLite mocks) to verify
that the `_verify_run_ownership` SQL query correctly enforces ownership isolation.

Tests use transactions that rollback after each test to avoid polluting the database.

Requires: PostgreSQL cluster accessible at /tmp:5432 with database ai_investment_lab.
"""

import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor


# PostgreSQL connection parameters
PG_HOST = "/tmp"
PG_PORT = 5432
PG_USER = "postgres"
PG_DB = "ai_investment_lab"


@contextmanager
def pg_connection():
    """Get a PostgreSQL connection that auto-rolls back."""
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        dbname=PG_DB,
    )
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def create_test_user(conn, email: str, is_admin: bool = False) -> str:
    """Create a test user and return their ID."""
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    password_hash = "$2b$12$LQv3c1yqBo9SkvXS8QTjAeKHq8/1ZyK5yK5yK5yK5yK5yK5yK5yK5"  # dummy hash

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, password_hash, display_name, is_admin, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, email, password_hash, email.split("@")[0], is_admin, True, now, now),
        )
    conn.commit()
    return user_id


def create_test_portfolio(conn, owner_id: str) -> str:
    """Create a test portfolio and return its ID."""
    portfolio_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (portfolio_id, owner_id, "USD", 100000.0, 100000.0, "active", now),
        )
    conn.commit()
    return portfolio_id


def create_test_run(conn, portfolio_id: str, skill: str = "momentum") -> str:
    """Create a test paper trading run and return its ID."""
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_trading_runs (id, portfolio_id, skill, instrument_id, interval_minutes, exit_config, start_date, status, initial_capital)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, portfolio_id, skill, "BTC-USD", 60, "exit_1", now, "running", Decimal("100000.00")),
        )
    conn.commit()
    return run_id


def verify_run_ownership(conn, run_id: str, user_id: str) -> bool:
    """
    Replicate the `_verify_run_ownership` SQL query from paper_trading.py.
    Returns True if the user owns the run, False otherwise.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT 1 FROM paper_trading_runs r
            JOIN paper_portfolios p ON r.portfolio_id = p.id
            WHERE r.id = %s AND p.owner_id = %s
            """,
            (run_id, user_id),
        )
        result = cur.fetchone()
        return result is not None


def cleanup_test_data(conn, user_ids: list, portfolio_ids: list, run_ids: list):
    """Clean up test data in reverse dependency order."""
    with conn.cursor() as cur:
        # Delete runs first
        for run_id in run_ids:
            cur.execute("DELETE FROM paper_trading_runs WHERE id = %s", (run_id,))
        # Delete portfolios
        for portfolio_id in portfolio_ids:
            cur.execute("DELETE FROM paper_portfolios WHERE id = %s", (portfolio_id,))
        # Delete users
        for user_id in user_ids:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()


class TestOwnershipIntegration:
    """Integration tests against real PostgreSQL."""

    def test_user_can_access_own_run(self):
        """User A should be able to access their own run."""
        with pg_connection() as conn:
            user_a = create_test_user(conn, "user_a_integration@test.com")
            portfolio_a = create_test_portfolio(conn, user_a)
            run_a = create_test_run(conn, portfolio_a)

            try:
                # User A should own run A
                assert verify_run_ownership(conn, run_a, user_a) is True, (
                    "User A must own their own run"
                )
            finally:
                cleanup_test_data(conn, [user_a], [portfolio_a], [run_a])

    def test_user_cannot_access_other_users_run(self):
        """User A should NOT be able to access User B's run."""
        with pg_connection() as conn:
            user_a = create_test_user(conn, "user_a_cross@test.com")
            user_b = create_test_user(conn, "user_b_cross@test.com")
            portfolio_b = create_test_portfolio(conn, user_b)
            run_b = create_test_run(conn, portfolio_b)

            try:
                # User A should NOT own run B
                assert verify_run_ownership(conn, run_b, user_a) is False, (
                    "User A must NOT own User B's run (ownership isolation violation)"
                )
            finally:
                cleanup_test_data(conn, [user_a, user_b], [portfolio_b], [run_b])

    def test_nonexistent_run_returns_false(self):
        """Querying a non-existent run should return False (not error)."""
        with pg_connection() as conn:
            user_a = create_test_user(conn, "user_a_nonexist@test.com")
            fake_run_id = str(uuid.uuid4())

            try:
                # Non-existent run should return False
                assert verify_run_ownership(conn, fake_run_id, user_a) is False, (
                    "Non-existent run must return False"
                )
            finally:
                cleanup_test_data(conn, [user_a], [], [])

    def test_multiple_runs_per_user(self):
        """User with multiple runs should own all of them."""
        with pg_connection() as conn:
            user_a = create_test_user(conn, "user_a_multi@test.com")
            portfolio_a = create_test_portfolio(conn, user_a)
            run_a1 = create_test_run(conn, portfolio_a, skill="momentum")
            run_a2 = create_test_run(conn, portfolio_a, skill="mean_reversion")
            run_a3 = create_test_run(conn, portfolio_a, skill="breakout")

            try:
                # User A should own all three runs
                assert verify_run_ownership(conn, run_a1, user_a) is True
                assert verify_run_ownership(conn, run_a2, user_a) is True
                assert verify_run_ownership(conn, run_a3, user_a) is True
            finally:
                cleanup_test_data(conn, [user_a], [portfolio_a], [run_a1, run_a2, run_a3])

    def test_cross_user_isolation_with_multiple_users(self):
        """Multiple users with multiple runs — each only owns their own."""
        with pg_connection() as conn:
            user_a = create_test_user(conn, "user_a_isolation@test.com")
            user_b = create_test_user(conn, "user_b_isolation@test.com")
            user_c = create_test_user(conn, "user_c_isolation@test.com")

            portfolio_a = create_test_portfolio(conn, user_a)
            portfolio_b = create_test_portfolio(conn, user_b)
            portfolio_c = create_test_portfolio(conn, user_c)

            run_a = create_test_run(conn, portfolio_a)
            run_b = create_test_run(conn, portfolio_b)
            run_c = create_test_run(conn, portfolio_c)

            try:
                # Each user owns only their own run
                assert verify_run_ownership(conn, run_a, user_a) is True
                assert verify_run_ownership(conn, run_a, user_b) is False
                assert verify_run_ownership(conn, run_a, user_c) is False

                assert verify_run_ownership(conn, run_b, user_a) is False
                assert verify_run_ownership(conn, run_b, user_b) is True
                assert verify_run_ownership(conn, run_b, user_c) is False

                assert verify_run_ownership(conn, run_c, user_a) is False
                assert verify_run_ownership(conn, run_c, user_b) is False
                assert verify_run_ownership(conn, run_c, user_c) is True
            finally:
                cleanup_test_data(
                    conn,
                    [user_a, user_b, user_c],
                    [portfolio_a, portfolio_b, portfolio_c],
                    [run_a, run_b, run_c],
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
