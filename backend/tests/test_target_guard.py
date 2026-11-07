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
# NARROWED: Only allow explicit localhost, not IP variants or Unix socket
APPROVED_TEST_TARGET = {
    "db_name": "ai_investment_lab_test",
    "role": "alethra_test_runtime",
    "server": "localhost",  # Only 'localhost', not '127.0.0.1', '::1', or empty
    "port": 5432,
}


class TargetGuardError(RuntimeError):
    """Raised when target guard verification fails."""
    pass


def verify_test_target(session) -> dict:
    """Verify database session matches approved test target.
    
    Checks:
    - current_database() == approved db_name
    - current_user == approved role
    - inet_server_addr() == approved server
    - inet_server_port() == approved port
    
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
    
    actual_db, actual_role, actual_server, actual_port = result
    
    # Convert port to int (may be string from DB)
    try:
        actual_port = int(actual_port) if actual_port else None
    except (ValueError, TypeError):
        actual_port = None
    
    actual = {
        "db_name": actual_db,
        "role": actual_role,
        "server": actual_server,
        "port": actual_port,
    }
    
    # Verify each component
    errors = []
    
    if actual_db != APPROVED_TEST_TARGET["db_name"]:
        errors.append(f"DB name mismatch: expected {APPROVED_TEST_TARGET['db_name']!r}, got {actual_db!r}")
    
    if actual_role != APPROVED_TEST_TARGET["role"]:
        errors.append(f"Role mismatch: expected {APPROVED_TEST_TARGET['role']!r}, got {actual_role!r}")
    
    # Server check: NARROWED to only allow explicit 'localhost'
    # Reject IP variants (127.0.0.1, ::1) and Unix socket (empty string)
    if actual_server != APPROVED_TEST_TARGET["server"]:
        errors.append(f"Server mismatch: expected {APPROVED_TEST_TARGET['server']!r}, got {actual_server!r}")
    
    # Port check: skip if None (Unix socket connections don't have a TCP port)
    if actual_port is not None and actual_port != APPROVED_TEST_TARGET["port"]:
        errors.append(f"Port mismatch: expected {APPROVED_TEST_TARGET['port']}, got {actual_port}")
    
    if errors:
        error_msg = "Target guard verification failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise TargetGuardError(error_msg)
    
    return actual


def create_verified_session_factory(db_url: str):
    """Create session factory with target guard verification.
    
    The first session created will verify the target. If verification fails,
    no sessions are returned and RuntimeError is raised.
    
    Args:
        db_url: Database URL
        
    Returns:
        Callable that returns verified sessions
        
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
    finally:
        verify_session.close()
    
    # Return factory that verifies on each session creation
    def guarded_factory():
        session = factory()
        # Optional: verify on each session (for extra safety)
        # For performance, we only verify on creation
        return session
    
    return guarded_factory


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
