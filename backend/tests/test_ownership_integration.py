"""PostgreSQL integration tests for paper-trading ownership isolation.

These tests run against a DEDICATED TEST PostgreSQL database (not production).
Each test is wrapped in a transaction that rolls back, ensuring no data persists.

Environment requirements:
- ALETHRA_TEST_PG_HOST: Test database host (default: localhost)
- ALETHRA_TEST_PG_PORT: Test database port (default: 5432)
- ALETHRA_TEST_PG_USER: Test database user (default: postgres)
- ALETHRA_TEST_PG_PASSWORD: Test database password (default: empty)
- ALETHRA_TEST_PG_DB: Test database name (REQUIRED, no default)

If ALETHRA_TEST_PG_DB is not set, all tests in this module are SKIPPED.
This prevents accidental writes to production or development databases.
"""

import pytest
import uuid
import os
from datetime import datetime, timezone
from decimal import Decimal
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor


# Test database configuration from environment variables
TEST_PG_HOST = os.environ.get("ALETHRA_TEST_PG_HOST", "localhost")
TEST_PG_PORT = int(os.environ.get("ALETHRA_TEST_PG_PORT", "5432"))
TEST_PG_USER = os.environ.get("ALETHRA_TEST_PG_USER", "postgres")
TEST_PG_PASSWORD = os.environ.get("ALETHRA_TEST_PG_PASSWORD", "")
TEST_PG_DB = os.environ.get("ALETHRA_TEST_PG_DB")  # REQUIRED, no default

# Known production database names that must NEVER be used for testing
PRODUCTION_DB_NAMES = {
    "ai_investment_lab",
    "alethra_production",
    "alethra_prod",
    "production",
    "prod",
}


def is_test_db_configured() -> bool:
    """Check if test database is explicitly configured."""
    return TEST_PG_DB is not None and TEST_PG_DB != ""


def validate_test_db_target() -> tuple[bool, str]:
    """Validate that the configured test database is actually a test environment.
    
    Returns:
        (is_valid, error_message)
    
    Checks:
    1. DB name must contain 'test' (case-insensitive)
    2. DB name must not be in known production list
    3. DB name must not be a common production pattern
    
    This prevents accidental writes to production databases even if
    the environment variable is set incorrectly.
    """
    if not is_test_db_configured():
        return False, "ALETHRA_TEST_PG_DB not set"
    
    db_name_lower = TEST_PG_DB.lower()
    
    # Check against known production names
    if TEST_PG_DB in PRODUCTION_DB_NAMES or db_name_lower in {n.lower() for n in PRODUCTION_DB_NAMES}:
        return False, f"Database name '{TEST_PG_DB}' is a known production database. Refusing to use for testing."
    
    # Check that name contains 'test' (safety convention)
    if "test" not in db_name_lower:
        return False, (
            f"Database name '{TEST_PG_DB}' does not contain 'test'. "
            f"Test database names must contain 'test' to prevent accidental production use. "
            f"Example: alethra_test, test_db, pg_test_01"
        )
    
    return True, ""


# Skip all tests if test database is not configured or not validated
_test_db_valid, _test_db_error = validate_test_db_target()
pytestmark = pytest.mark.skipif(
    not _test_db_valid,
    reason=f"Test database not configured or invalid: {_test_db_error}"
)


@contextmanager
def test_db_connection():
    """Get a connection to the TEST database.
    
    This context manager:
    1. Validates the test database target (not production)
    2. Connects to the dedicated test database
    3. Yields the connection
    4. ALWAYS rolls back (never commits)
    5. Closes the connection
    
    This ensures no test data persists, even if tests fail.
    """
    is_valid, error_msg = validate_test_db_target()
    if not is_valid:
        raise RuntimeError(
            f"Test database target validation failed: {error_msg}. "
            f"Refusing to connect."
        )
    
    conn = psycopg2.connect(
        host=TEST_PG_HOST,
        port=TEST_PG_PORT,
        user=TEST_PG_USER,
        password=TEST_PG_PASSWORD,
        dbname=TEST_PG_DB,
    )
    try:
        yield conn
    finally:
        # ALWAYS rollback, never commit
        conn.rollback()
        conn.close()


@contextmanager
def test_transaction(conn):
    """Wrap test code in a transaction that always rolls back.
    
    Usage:
        with test_db_connection() as conn:
            with test_transaction(conn):
                # All writes here will be rolled back
                create_test_user(conn, ...)
                assert verify_run_ownership(...)
    
    This ensures that even if setup succeeds but the test fails,
    no data persists.
    """
    # Disable autocommit so we can control the transaction
    old_autocommit = conn.autocommit
    conn.autocommit = False
    
    try:
        yield conn
    finally:
        # ALWAYS rollback, never commit
        conn.rollback()
        conn.autocommit = old_autocommit


def create_test_user(conn, email: str, is_admin: bool = False) -> str:
    """Create a test user and return their ID.
    
    NOTE: This does NOT commit. The caller must manage transactions.
    """
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
    # NO COMMIT - transaction managed by caller
    return user_id


def create_test_portfolio(conn, owner_id: str) -> str:
    """Create a test portfolio and return its ID.
    
    NOTE: This does NOT commit. The caller must manage transactions.
    """
    portfolio_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (portfolio_id, owner_id, "USD", 10000.0, 10000.0, "active", now),
        )
    # NO COMMIT - transaction managed by caller
    return portfolio_id


def create_test_run(conn, portfolio_id: str, skill: str = "momentum") -> str:
    """Create a test paper trading run and return its ID.
    
    NOTE: This does NOT commit. The caller must manage transactions.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper_trading_runs (id, portfolio_id, skill, instrument_id, interval_minutes, exit_config, start_date, status, initial_capital)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, portfolio_id, skill, "BTC-USD", 60, "exit_1", now, "running", Decimal("1000.00")),
        )
    # NO COMMIT - transaction managed by caller
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


class TestOwnershipIntegration:
    """Integration tests against dedicated TEST PostgreSQL database."""

    def test_user_can_access_own_run(self):
        """User A should be able to access their own run."""
        with test_db_connection() as conn:
            with test_transaction(conn):
                user_a = create_test_user(conn, "user_a_integration@test.com")
                portfolio_a = create_test_portfolio(conn, user_a)
                run_a = create_test_run(conn, portfolio_a)

                # User A should own run A
                assert verify_run_ownership(conn, run_a, user_a) is True, (
                    "User A must own their own run"
                )
                # Transaction rolls back here - no cleanup needed

    def test_user_cannot_access_other_users_run(self):
        """User A should NOT be able to access User B's run."""
        with test_db_connection() as conn:
            with test_transaction(conn):
                user_a = create_test_user(conn, "user_a_cross@test.com")
                user_b = create_test_user(conn, "user_b_cross@test.com")
                portfolio_b = create_test_portfolio(conn, user_b)
                run_b = create_test_run(conn, portfolio_b)

                # User A should NOT own run B
                assert verify_run_ownership(conn, run_b, user_a) is False, (
                    "User A must NOT own User B's run (ownership isolation violation)"
                )
                # Transaction rolls back here

    def test_nonexistent_run_returns_false(self):
        """Querying a non-existent run should return False (not error)."""
        with test_db_connection() as conn:
            with test_transaction(conn):
                user_a = create_test_user(conn, "user_a_nonexist@test.com")
                fake_run_id = str(uuid.uuid4())

                # Non-existent run should return False
                assert verify_run_ownership(conn, fake_run_id, user_a) is False, (
                    "Non-existent run must return False"
                )
                # Transaction rolls back here

    def test_multiple_runs_per_user(self):
        """User with multiple runs should own all of them."""
        with test_db_connection() as conn:
            with test_transaction(conn):
                user_a = create_test_user(conn, "user_a_multi@test.com")
                portfolio_a = create_test_portfolio(conn, user_a)
                run_a1 = create_test_run(conn, portfolio_a, skill="momentum")
                run_a2 = create_test_run(conn, portfolio_a, skill="mean_reversion")
                run_a3 = create_test_run(conn, portfolio_a, skill="breakout")

                # User A should own all three runs
                assert verify_run_ownership(conn, run_a1, user_a) is True
                assert verify_run_ownership(conn, run_a2, user_a) is True
                assert verify_run_ownership(conn, run_a3, user_a) is True
                # Transaction rolls back here

    def test_cross_user_isolation_with_multiple_users(self):
        """Multiple users with multiple runs — each only owns their own."""
        with test_db_connection() as conn:
            with test_transaction(conn):
                user_a = create_test_user(conn, "user_a_isolation@test.com")
                user_b = create_test_user(conn, "user_b_isolation@test.com")
                user_c = create_test_user(conn, "user_c_isolation@test.com")

                portfolio_a = create_test_portfolio(conn, user_a)
                portfolio_b = create_test_portfolio(conn, user_b)
                portfolio_c = create_test_portfolio(conn, user_c)

                run_a = create_test_run(conn, portfolio_a)
                run_b = create_test_run(conn, portfolio_b)
                run_c = create_test_run(conn, portfolio_c)

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
                # Transaction rolls back here


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
