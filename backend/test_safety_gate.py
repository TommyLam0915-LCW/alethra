#!/usr/bin/env python3
"""
Fail-closed safety gate for test database connections.

ALL test scripts MUST call verify_test_database() before any write operation.
This prevents accidental writes to production database.

SAFETY PRINCIPLES:
1. Default DENY — unknown targets are rejected, not warned
2. TEST_MODE must be checked in verify_test_database(), not just CLI
3. Superuser and elevated permissions are rejected
4. Host/port must match allowed instances
5. Passwords are never returned or logged
6. Actual database identity is verified after connecting
"""

import os
import sys
from urllib.parse import urlparse
from typing import Optional, Dict, Any
import psycopg2


# Allowed test databases (whitelist) — all others are rejected
ALLOWED_TEST_DATABASES = {
    'ai_investment_lab_test',
    'alethra_test',
    'test_ai_investment_lab',
    'alethra_owner_test',  # Added for owner verification tests
}

# Forbidden production databases (blacklist) — always rejected
FORBIDDEN_DATABASES = {
    'ai_investment_lab',  # Production
    'postgres',  # Default maintenance
    'template0',
    'template1',
}

# Allowed hosts/ports for test instances
ALLOWED_INSTANCES = {
    ('localhost', 5432),
    ('127.0.0.1', 5432),
    ('::1', 5432),
}


class TestDatabaseGateError(Exception):
    """Raised when test database safety gate fails.
    
    This is a HARD FAIL — callers must not catch and continue.
    """
    pass


def parse_database_url(url: str) -> Dict[str, Any]:
    """Parse DATABASE_URL into components.
    
    Returns dict with host, port, database, username.
    NEVER includes password in return value.
    """
    if not url:
        raise TestDatabaseGateError("DATABASE_URL is not set")
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise TestDatabaseGateError(f"Failed to parse DATABASE_URL: {e}")
    
    if parsed.scheme not in ('postgresql', 'postgres'):
        raise TestDatabaseGateError(f"Invalid scheme: {parsed.scheme}")
    
    return {
        'scheme': parsed.scheme,
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'username': parsed.username,
        # password intentionally excluded
    }


def verify_database_identity(conn, expected_db: str) -> Dict[str, Any]:
    """Verify actual database identity after connecting.
    
    Returns connection info (without password).
    Raises TestDatabaseGateError on any mismatch.
    """
    with conn.cursor() as cur:
        # Get actual connection info
        cur.execute("""
            SELECT 
                current_database(), 
                current_user, 
                inet_server_addr(), 
                inet_server_port()
        """)
        actual_db, actual_user, server_addr, server_port = cur.fetchone()
        
        # Verify database name matches
        if actual_db != expected_db:
            raise TestDatabaseGateError(
                f"Database mismatch: connected to '{actual_db}', "
                f"expected '{expected_db}'. "
                f"Rejecting to prevent cross-database writes."
            )
        
        # Check role permissions — reject superuser and elevated roles
        cur.execute("""
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
            FROM pg_roles
            WHERE rolname = %s
        """, (actual_user,))
        
        result = cur.fetchone()
        if not result:
            raise TestDatabaseGateError(
                f"Role '{actual_user}' not found in pg_roles. "
                f"Cannot verify permissions."
            )
        
        is_super, can_create_db, can_create_role, bypass_rls = result
        
        # HARD FAIL on dangerous permissions
        if is_super:
            raise TestDatabaseGateError(
                f"Connected as superuser '{actual_user}'. "
                f"Test operations must use a non-superuser role. "
                f"Rejecting to prevent privilege escalation."
            )
        
        if can_create_db or can_create_role:
            raise TestDatabaseGateError(
                f"Role '{actual_user}' has elevated permissions "
                f"(CREATEDB: {can_create_db}, CREATEROLE: {can_create_role}). "
                f"Test operations must use a role without these permissions. "
                f"Rejecting to prevent unauthorized database/role creation."
            )
        
        if bypass_rls:
            raise TestDatabaseGateError(
                f"Role '{actual_user}' has BYPASSRLS permission. "
                f"Test operations must not bypass row-level security. "
                f"Rejecting to prevent security policy bypass."
            )
        
        return {
            'database': actual_db,
            'user': actual_user,
            'server_addr': server_addr,
            'server_port': server_port,
            # permissions intentionally excluded from return
        }


def verify_test_database(database_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Verify that the database connection is safe for testing.
    
    This is the PUBLIC entry point — all test scripts must call this.
    
    Returns connection info dict (without password) on success.
    Raises TestDatabaseGateError on ANY failure — no warnings, no fallback.
    
    Safety checks (all must pass):
    1. TEST_MODE=true environment variable is set
    2. DATABASE_URL is explicitly provided (no app config fallback)
    3. Database name is in ALLOWED_TEST_DATABASES whitelist
    4. Database name is NOT in FORBIDDEN_DATABASES blacklist
    5. Host/port matches ALLOWED_INSTANCES
    6. Connection succeeds and actual identity matches
    7. Role is NOT superuser, CREATEDB, CREATEROLE, or BYPASSRLS
    
    Usage:
        from test_safety_gate import verify_test_database
        conn_info = verify_test_database()  # Raises on any failure
        # Only reaches here if ALL checks passed
    """
    # Step 1: Verify TEST_MODE (HARD REQUIREMENT)
    test_mode = os.environ.get('TEST_MODE')
    if test_mode != 'true':
        raise TestDatabaseGateError(
            f"TEST_MODE must be 'true' for test operations. "
            f"Current value: {test_mode!r}. "
            f"This is a hard requirement, not a warning."
        )
    
    # Step 2: Get DATABASE_URL
    url = database_url or os.environ.get('DATABASE_URL')
    if not url:
        raise TestDatabaseGateError(
            "DATABASE_URL not set. Test scripts must explicitly set database URL. "
            "Do not fall back to application config."
        )
    
    # Step 3: Parse URL
    try:
        parsed = parse_database_url(url)
    except Exception as e:
        raise TestDatabaseGateError(f"Failed to parse DATABASE_URL: {e}")
    
    db_name = parsed['database']
    host = parsed['host']
    port = parsed['port']
    
    # Step 4: Check against blacklist (HARD FAIL)
    if db_name in FORBIDDEN_DATABASES:
        raise TestDatabaseGateError(
            f"FORBIDDEN: Database '{db_name}' is in the production blacklist. "
            f"Test scripts must use test database only. "
            f"Rejecting to prevent production writes."
        )
    
    # Step 5: Check against whitelist (HARD FAIL — not warning)
    if db_name not in ALLOWED_TEST_DATABASES:
        raise TestDatabaseGateError(
            f"REJECTED: Database '{db_name}' is not in the test whitelist. "
            f"Allowed test databases: {sorted(ALLOWED_TEST_DATABASES)}. "
            f"Rejecting to prevent writes to unknown databases."
        )
    
    # Step 6: Check host/port (HARD FAIL)
    if (host, port) not in ALLOWED_INSTANCES:
        raise TestDatabaseGateError(
            f"REJECTED: Host/port {host}:{port} not in allowed instances. "
            f"Allowed: {sorted(ALLOWED_INSTANCES)}. "
            f"Rejecting to prevent writes to external databases."
        )
    
    # Step 7: Connect and verify actual identity
    try:
        conn = psycopg2.connect(url)
        try:
            conn_info = verify_database_identity(conn, db_name)
            conn_info['url_parsed'] = {
                'host': host,
                'port': port,
                'database': db_name,
                'username': parsed['username'],
                # password excluded
            }
            return conn_info
        finally:
            conn.close()
    except psycopg2.Error as e:
        raise TestDatabaseGateError(f"Failed to connect to database: {e}")


def main():
    """Command-line entry point for manual verification."""
    print("Verifying test database safety gate...")
    print()
    
    try:
        conn_info = verify_test_database()
        
        print("✓ Safety gate PASSED — all checks succeeded")
        print(f"  Database: {conn_info['database']}")
        print(f"  User: {conn_info['user']}")
        print(f"  Server: {conn_info['server_addr']}:{conn_info['server_port']}")
        print()
        print("Safe to proceed with test operations.")
        
        return 0
    
    except TestDatabaseGateError as e:
        print(f"✗ Safety gate FAILED — rejecting test operations")
        print(f"  Error: {e}")
        print()
        print("DO NOT proceed with test operations.")
        print("Fix the configuration and retry.")
        
        return 1


if __name__ == '__main__':
    sys.exit(main())
