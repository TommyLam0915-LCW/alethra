"""Target guard for test database verification.

This module provides a shared guard function that verifies the test database
target (DB name, role, server, port) before any write operations.

Safety guarantees:
- Fail closed: if verification fails, raise RuntimeError
- No writes happen before guard passes
- Guard is shared between fixtures and tests
"""
import os
from sqlalchemy import text


# Approved test target configuration
# Two connection modes supported:
#   TCP: inet_server_addr() returns IP, inet_server_port() returns port
#   Unix socket: both return NULL (only local socket connections)
APPROVED_TEST_TARGET = {
    "db_name": "ai_investment_lab_test",
    "role": "alethra_test_runtime",
    "tcp": {
        "server_ip": "127.0.0.1",
        "port": 5432,
    },
    "unix_socket": {
        "approved_servers": {"localhost", "127.0.0.1", "::1", "", None},
        "approved_ports": {None},
    },
}


class TargetGuardError(RuntimeError):
    """Raised when target guard verification fails."""
    pass


def verify_test_target(session) -> dict:
    """Verify database session matches approved test target.

    Supports two connection modes:
    - TCP: inet_server_addr() returns IP, inet_server_port() returns port
    - Unix socket: both return NULL (local socket only)

    Checks (both modes):
    - current_database() == approved db_name
    - current_user() == approved role

    TCP mode checks:
    - inet_server_addr() == approved server_ip
    - inet_server_port() == approved port

    Unix socket mode:
    - NULL addr and NULL port are accepted (local-only)

    Args:
        session: SQLAlchemy session to verify

    Returns:
        dict with actual target info if verification passes

    Raises:
        TargetGuardError: If any check fails
    """
    result = session.execute(text("""
        SELECT
            current_database(),
            current_user,
            inet_server_addr(),
            inet_server_port()
    """)).fetchone()

    if result is None:
        raise TargetGuardError("Failed to query database target information")

    actual_db, actual_role, actual_server_addr, actual_port = result

    try:
        actual_port = int(actual_port) if actual_port is not None else None
    except (ValueError, TypeError):
        actual_port = None

    is_unix_socket = actual_server_addr is None and actual_port is None

    actual = {
        "db_name": actual_db,
        "role": actual_role,
        "server_addr": actual_server_addr,
        "port": actual_port,
        "mode": "unix_socket" if is_unix_socket else "tcp",
    }

    errors = []

    if actual_db != APPROVED_TEST_TARGET["db_name"]:
        errors.append(
            f"DB name mismatch: expected {APPROVED_TEST_TARGET['db_name']!r}, "
            f"got {actual_db!r}"
        )

    if actual_role != APPROVED_TEST_TARGET["role"]:
        errors.append(
            f"Role mismatch: expected {APPROVED_TEST_TARGET['role']!r}, "
            f"got {actual_role!r}"
        )

    if is_unix_socket:
        unix_policy = APPROVED_TEST_TARGET["unix_socket"]
        if actual_server_addr not in unix_policy["approved_servers"]:
            errors.append(
                f"Unix socket server mismatch: expected one of "
                f"{unix_policy['approved_servers']}, got {actual_server_addr!r}"
            )
        if actual_port not in unix_policy["approved_ports"]:
            errors.append(
                f"Unix socket port mismatch: expected one of "
                f"{unix_policy['approved_ports']}, got {actual_port!r}"
            )
    else:
        tcp_policy = APPROVED_TEST_TARGET["tcp"]
        if actual_server_addr != tcp_policy["server_ip"]:
            errors.append(
                f"Server IP mismatch: expected {tcp_policy['server_ip']!r}, "
                f"got {actual_server_addr!r}"
            )
        if actual_port != tcp_policy["port"]:
            errors.append(
                f"Port mismatch: expected {tcp_policy['port']}, got {actual_port}"
            )

    if errors:
        error_msg = "Target guard verification failed:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        raise TargetGuardError(error_msg)

    return actual


def create_verified_session_factory(db_url: str):
    """Create session factory with target guard verification.
    
    P1 C5: Extracted shared verified-factory creation flow.
    Both fixtures and tests use this same entry point.
    
    The first session created will verify the target. If verification fails,
    no sessions are returned and RuntimeError is raised.
    
    Args:
        db_url: Database URL
        
    Returns:
        Tuple of (guarded_factory, engine) for explicit teardown
        
    Raises:
        TargetGuardError: If initial verification fails
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(db_url)
    factory = sessionmaker(bind=engine)
    
    # Verify target on first session using context manager
    verify_session = factory()
    try:
        actual_target = verify_test_target(verify_session)
    except TargetGuardError:
        # P1 C8: On rejection, verify session is closed and engine is disposed
        engine.dispose()
        raise
    finally:
        # P1 Commit 1.1: Use standard finally close, not is_closed check
        verify_session.close()
    
    # P1 C9: Return factory that can create sessions
    def guarded_factory():
        session = factory()
        return session
    
    # P1 C9: Return both factory and engine for explicit teardown
    return guarded_factory, engine


def get_test_db_url() -> str:
    """Get test database URL from environment.
    
    Fail closed: if ALETHRA_PG_TEST_URL not set, raise RuntimeError.
    
    Returns:
        Database URL string
        
    Raises:
        RuntimeError: If environment variable not set
    """
    db_url = os.environ.get("ALETHRA_PG_TEST_URL")
    if not db_url:
        raise RuntimeError(
            "ALETHRA_PG_TEST_URL environment variable not set. "
            "Cannot proceed without explicit test database configuration. "
            "This is a safety feature to prevent accidental writes to wrong database."
        )
    return db_url
