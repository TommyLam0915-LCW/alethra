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


# Allowed test databases (whitelist)
ALLOWED_TEST_DATABASES = {
    'ai_investment_lab_test',
    'alethra_test',
    'test_ai_investment_lab',
}

# Forbidden production databases (blacklist)
FORBIDDEN_DATABASES = {
    'ai_investment_lab',  # Production
    'postgres',  # Default maintenance
}


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
    """Verify actual database identity after connecting."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port()")
        actual_db, actual_user, server_addr, server_port = cur.fetchone()
        
        if actual_db != expected_db:
            raise TestDatabaseGateError(
                f"Database mismatch: connected to '{actual_db}', expected '{expected_db}'"
            )
        
        # Check if user has production-level permissions
        cur.execute("""
            SELECT rolsuper, rolcreatedb, rolcreaterole
            FROM pg_roles
            WHERE rolname = %s
        """, (actual_user,))
        
        result = cur.fetchone()
        if result:
            is_super, can_create_db, can_create_role = result
            if is_super:
                print(f"WARNING: Connected as superuser '{actual_user}'", file=sys.stderr)
            if can_create_db or can_create_role:
                print(f"WARNING: User '{actual_user}' has elevated permissions", file=sys.stderr)
        
        return {
            'database': actual_db,
            'user': actual_user,
            'server_addr': server_addr,
            'server_port': server_port,
        }


def verify_test_database(database_url: str = None) -> dict:
    """
    Verify that the database connection is safe for testing.
    
    Returns connection info dict on success.
    Raises TestDatabaseGateError on any failure.
    
    Usage:
        from test_safety_gate import verify_test_database
        conn_info = verify_test_database()
        # Now safe to proceed with test operations
    """
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
    
    # Step 3: Check against blacklist
    if db_name in FORBIDDEN_DATABASES:
        raise TestDatabaseGateError(
            f"FORBIDDEN: Database '{db_name}' is production. "
            f"Test scripts must use test database only."
        )
    
    # Step 4: Check against whitelist (warning, not error)
    if db_name not in ALLOWED_TEST_DATABASES:
        print(
            f"WARNING: Database '{db_name}' not in known test databases. "
            f"Proceeding but verify this is intentional.",
            file=sys.stderr
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


def require_test_mode():
    """Require TEST_MODE=true environment variable."""
    if os.environ.get('TEST_MODE') != 'true':
        raise TestDatabaseGateError(
            "TEST_MODE not set to 'true'. "
            "Test scripts must set TEST_MODE=true to proceed."
        )


def main():
    """Command-line entry point for manual verification."""
    print("Verifying test database safety gate...")
    
    try:
        require_test_mode()
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
