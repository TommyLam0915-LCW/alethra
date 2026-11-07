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
# P0 FIX: inet_server_addr() returns IP, not hostname
# Separate URL hostname from actual server IP verification
APPROVED_TEST_TARGET = {
    "db_name": "ai_investment_lab_test",
    "role": "alethra_test_runtime",
    "server_ip": "127.0.0.1",  # inet_server_addr() returns IP, not hostname
    "port": 5432,  # TCP port, must not be NULL
}


class TargetGuardError(RuntimeError):
    """Raised when target guard verification fails."""
    pass


def verify_test_target(session) -> dict:
    """Verify database session matches approved test target.
    
    Checks:
    - current_database() == approved db_name
    - current_user == approved role
    - inet_server_addr() == approved server_ip (IP address, not hostname)
    - inet_server_port() == approved port (TCP mode, must not be NULL)
    
    Args:
        session: SQLAlchemy session to verify
        
    Returns:
        dict with actual target info if verification passes
        
    Raises:
        TargetGuardError: If any check fails
    """
    # Query actual target
    result = session.execute(text("""
        SELECT 
            current_database(),
            current_user,
            inet_server_addr(),
            inet_server_port()
    """)).fetchone()
    
    if result is None:
        raise TargetGuardError("Failed to query database target information")
    
    actual_db, actual_role, actual_server_ip, actual_port = result
    
    # Convert port to int (may be string from DB)
    # P0 FIX: TCP mode requires non-NULL port
    try:
        actual_port = int(actual_port) if actual_port is not None else None
    except (ValueError, TypeError):
        actual_port = None
    
    # P0 FIX: Reject NULL port in TCP mode
    if actual_port is None:
        raise TargetGuardError(
            f"Port is NULL: TCP mode requires explicit port. "
            f"Expected {APPROVED_TEST_TARGET['port']}, got NULL. "
            f"This may indicate a Unix socket connection, which is not approved."
        )
    
    actual = {
        "db_name": actual_db,
        "role": actual_role,
        "server_ip": actual_server_ip,  # IP address, not hostname
        "port": actual_port,
    }
    
    # Verify each component
    errors = []
    
    if actual_db != APPROVED_TEST_TARGET["db_name"]:
        errors.append(f"DB name mismatch: expected {APPROVED_TEST_TARGET['db_name']!r}, got {actual_db!r}")
    
    if actual_role != APPROVED_TEST_TARGET["role"]:
        errors.append(f"Role mismatch: expected {APPROVED_TEST_TARGET['role']!r}, got {actual_role!r}")
    
    # P0 FIX: Server IP check (inet_server_addr returns IP, not hostname)
    if actual_server_ip != APPROVED_TEST_TARGET["server_ip"]:
        errors.append(f"Server IP mismatch: expected {APPROVED_TEST_TARGET['server_ip']!r}, got {actual_server_ip!r}")
    
    # Port check: strict, no skip for None (already checked above)
    if actual_port != APPROVED_TEST_TARGET["port"]:
        errors.append(f"Port mismatch: expected {APPROVED_TEST_TARGET['port']}, got {actual_port}")
    
    if errors:
        error_msg = "Target guard verification failed:\n" + "\n".join(f"  - {e}" for e in errors)
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
    
    # Verify target on first session
    verify_session = factory()
    try:
        actual_target = verify_test_target(verify_session)
    except TargetGuardError:
        # P1 C8: On rejection, verify session is closed and engine is disposed
        verify_session.close()
        engine.dispose()
        raise
    finally:
        # Ensure session is closed after verification
        if not verify_session.is_closed:
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
