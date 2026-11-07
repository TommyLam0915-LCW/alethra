#!/usr/bin/env python3
"""
Fail-closed safety gate for test database connections.

ALL test scripts MUST call verify_test_database() before any write operation.
This prevents accidental writes to production database.

Safety checks:
1. DATABASE_URL must be explicitly set (no fallback to app config)
2. Database name must be in allowed list (ai_investment_lab_test, etc.)
3. Role must not have production permissions
4. Connection must verify actual database identity after connecting
"""

import os
import sys
from urllib.parse import urlparse
import psycopg2


# Allowed test databases (whitelist) - must use alethra_ prefix for disposable tests
ALLOWED_TEST_DATABASES = {
    'ai_investment_lab_test',
    'alethra_test',
    'alethra_owner_test',
    'test_ai_investment_lab',
}

# Allowed test database prefixes (for disposable test databases)
ALLOWED_TEST_PREFIXES = (
    'alethra_',  # All disposable test databases must use this prefix
)

# Forbidden production databases (blacklist) - explicit block
FORBIDDEN_DATABASES = {
    'ai_investment_lab',  # Production
    'postgres',  # Default maintenance
}

# Forbidden roles - must not have production permissions
FORBIDDEN_ROLE_PATTERNS = {
    'superuser',  # Cannot be superuser
}

# Required environment variable for test mode
REQUIRED_TEST_MODE_VAR = 'TEST_MODE'


class TestDatabaseGateError(Exception):
    """Raised when test database safety gate fails."""
    pass


def parse_database_url(url: str) -> dict:
    """Parse DATABASE_URL into components."""
    if not url:
        raise TestDatabaseGateError("DATABASE_URL is not set")
    
    parsed = urlparse(url)
    
    if parsed.scheme not in ('postgresql', 'postgres'):
        raise TestDatabaseGateError(f"Invalid scheme: {parsed.scheme}")
    
    return {
        'scheme': parsed.scheme,
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'username': parsed.username,
        'password': parsed.password,
    }


def verify_database_identity(conn, expected_db: str):
    """Verify actual database identity after connecting.
    
    Fail-closed: rejects superuser and elevated permission roles.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port()")
        actual_db, actual_user, server_addr, server_port = cur.fetchone()
        
        if actual_db != expected_db:
            raise TestDatabaseGateError(
                f"Database mismatch: connected to '{actual_db}', expected '{expected_db}'"
            )
        
        # Check if user has production-level permissions - FAIL CLOSED
        cur.execute("""
            SELECT rolsuper, rolcreatedb, rolcreaterole
            FROM pg_roles
            WHERE rolname = %s
        """, (actual_user,))
        
        result = cur.fetchone()
        if result:
            is_super, can_create_db, can_create_role = result
            if is_super:
                raise TestDatabaseGateError(
                    f"FORBIDDEN: Connected as superuser '{actual_user}'. "
                    f"Test databases must not use superuser roles."
                )
            if can_create_db:
                raise TestDatabaseGateError(
                    f"FORBIDDEN: User '{actual_user}' has CREATEDB permission. "
                    f"Test roles must not have database creation privileges."
                )
            if can_create_role:
                raise TestDatabaseGateError(
                    f"FORBIDDEN: User '{actual_user}' has CREATEROLE permission. "
                    f"Test roles must not have role creation privileges."
                )
        
        return {
            'database': actual_db,
            'user': actual_user,
            'server_addr': server_addr,
            'server_port': server_port,
        }


def verify_test_database(database_url: str = None) -> dict:
    """
    Verify that the database connection is safe for testing.
    
    Fail-closed: ALL checks must pass or exception is raised.
    
    Returns connection info dict on success.
    Raises TestDatabaseGateError on any failure.
    
    Usage:
        from test_safety_gate import verify_test_database
        conn_info = verify_test_database()
        # Now safe to proceed with test operations
    """
    # Step 0: Check TEST_MODE - required for all test operations
    if os.environ.get(REQUIRED_TEST_MODE_VAR) != 'true':
        raise TestDatabaseGateError(
            f"{REQUIRED_TEST_MODE_VAR} not set to 'true'. "
            f"All test operations require explicit TEST_MODE=true."
        )
    
    # Step 1: Get DATABASE_URL
    url = database_url or os.environ.get('DATABASE_URL')
    if not url:
        raise TestDatabaseGateError(
            "DATABASE_URL not set. Test scripts must explicitly set database URL. "
            "Do not fall back to application config."
        )
    
    # Step 2: Parse URL
    try:
        parsed = parse_database_url(url)
    except Exception as e:
        raise TestDatabaseGateError(f"Failed to parse DATABASE_URL: {e}")
    
    db_name = parsed['database']
    
    # Step 3: Check against blacklist - explicit block
    if db_name in FORBIDDEN_DATABASES:
        raise TestDatabaseGateError(
            f"FORBIDDEN: Database '{db_name}' is production. "
            f"Test scripts must use test database only."
        )
    
    # Step 4: Check against whitelist or allowed prefix - FAIL CLOSED
    is_whitelisted = db_name in ALLOWED_TEST_DATABASES
    has_allowed_prefix = any(db_name.startswith(prefix) for prefix in ALLOWED_TEST_PREFIXES)
    
    if not (is_whitelisted or has_allowed_prefix):
        raise TestDatabaseGateError(
            f"FORBIDDEN: Database '{db_name}' not in whitelist and does not use allowed prefix. "
            f"Allowed: {ALLOWED_TEST_DATABASES} or prefix: {ALLOWED_TEST_PREFIXES}. "
            f"Test databases must be explicitly approved."
        )
    
    # Step 5: Connect and verify actual identity
    try:
        conn = psycopg2.connect(url)
        try:
            conn_info = verify_database_identity(conn, db_name)
            conn_info['url_parsed'] = parsed
            return conn_info
        finally:
            conn.close()
    except psycopg2.Error as e:
        raise TestDatabaseGateError(f"Failed to connect to database: {e}")


def main():
    """Command-line entry point for manual verification."""
    print("Verifying test database safety gate...")
    
    try:
        # TEST_MODE is now checked inside verify_test_database()
        conn_info = verify_test_database()
        
        print("✓ Safety gate passed")
        print(f"  Database: {conn_info['database']}")
        print(f"  User: {conn_info['user']}")
        print(f"  Server: {conn_info['server_addr']}:{conn_info['server_port']}")
        
        return 0
    
    except TestDatabaseGateError as e:
        print(f"✗ Safety gate FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
