"""Target guard for test database verification.

This module provides a shared guard function that verifies the test database
target (DB name, role, server, port) before any write operations.

Safety guarantees:
- Fail closed: if verification fails, raise RuntimeError
- No writes happen before guard passes
- Guard is shared between fixtures and tests
"""
import os
import stat
from urllib.parse import urlparse, parse_qs
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
        # Approved Unix socket directory paths (from URL query host parameter)
        # Empty string is NOT approved — ambiguous default socket is rejected
        "approved_socket_dirs": {"/tmp"},
        # PostgreSQL default port for socket file name
        "socket_port": 5432,
    },
}


class TargetGuardError(RuntimeError):
    """Raised when target guard verification fails."""
    pass


def _extract_socket_dir_from_url(db_url: str):
    """Extract socket directory from PostgreSQL connection URL.

    Handles formats:
    - postgresql://user@/db?host=/tmp  → '/tmp'
    - postgresql://user@/db?host=/var/run/postgresql  → '/var/run/postgresql'
    - postgresql://user@/db  → None (no host specified)
    - postgresql://user@localhost:5432/db  → None (TCP, not socket)

    Returns None if no socket host parameter found.
    Returns empty string if host= is explicitly empty (will be rejected).
    Raises TargetGuardError if:
    - Multiple conflicting host values found
    - Both authority hostname AND query host= present (ambiguous)
    """
    parsed = urlparse(db_url)

    # Parse query parameters for host=
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    host_values = query_params.get("host", [])

    # SECURITY: Reject if both authority hostname and query host= are present
    # This is ambiguous — is it TCP to hostname or socket at host=?
    if parsed.hostname and host_values:
        raise TargetGuardError(
            f"URL has both authority hostname ({parsed.hostname!r}) and "
            f"query host= ({host_values!r}). Ambiguous connection target rejected."
        )

    # If hostname is present (without query host), this is TCP mode, not Unix socket
    if parsed.hostname:
        return None

    if not host_values:
        return None

    if len(host_values) > 1:
        raise TargetGuardError(
            f"Multiple host parameters in URL: {host_values!r}. "
            f"Ambiguous socket target rejected."
        )

    return host_values[0]


def _extract_port_from_url(db_url: str) -> int:
    """Extract port from PostgreSQL connection URL.

    Returns the port from:
    - URL authority: postgresql://user@host:PORT/db
    - Query param: postgresql://user@/db?host=/tmp&port=5433
    - Config default if neither specified

    Returns None if no port specified (caller should use config default).
    """
    parsed = urlparse(db_url)

    # Check query param first (explicit port= overrides authority port for socket)
    query_params = parse_qs(parsed.query)
    port_values = query_params.get("port", [])
    if port_values:
        try:
            return int(port_values[0])
        except (ValueError, IndexError):
            pass

    # Fallback to authority port
    if parsed.port:
        return parsed.port

    return None


def _verify_socket_file_exists(socket_dir: str, port: int = 5432) -> bool:
    """Verify that a PostgreSQL socket file exists at the given directory.

    Checks for .s.PGSQL.{port} file and verifies it's a socket type.
    """
    socket_path = os.path.join(socket_dir, f".s.PGSQL.{port}")
    if not os.path.exists(socket_path):
        return False
    try:
        mode = os.stat(socket_path).st_mode
        return stat.S_ISSOCK(mode)
    except OSError:
        return False


def verify_test_target(session, db_url: str = None) -> dict:
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
    - NULL addr and NULL port are necessary but NOT sufficient
    - db_url host parameter must match approved socket path
    - Socket file must exist on disk

    Args:
        session: SQLAlchemy session to verify
        db_url: Connection URL string (required for Unix socket validation)

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
        # Unix socket mode: strict validation required
        if db_url is None:
            errors.append(
                "Unix socket detected (NULL addr/port) but db_url not provided. "
                "Cannot validate socket instance without connection URL."
            )
        else:
            unix_policy = APPROVED_TEST_TARGET["unix_socket"]
            approved_dirs = unix_policy["approved_socket_dirs"]
            socket_port = unix_policy["socket_port"]

            try:
                socket_dir = _extract_socket_dir_from_url(db_url)
            except TargetGuardError as e:
                errors.append(str(e))
            else:
                if socket_dir is None:
                    errors.append(
                        f"Unix socket mode but no host= query parameter in URL. "
                        f"Ambiguous socket target rejected. URL: {db_url!r}"
                    )
                elif socket_dir == "":
                    errors.append(
                        "Unix socket host= is empty string. "
                        "Default/ambiguous socket path rejected."
                    )
                elif socket_dir not in approved_dirs:
                    errors.append(
                        f"Unix socket directory not approved: {socket_dir!r}. "
                        f"Approved: {approved_dirs}"
                    )
                else:
                    # Check URL port vs socket file port consistency
                    url_port = _extract_port_from_url(db_url)
                    if url_port is not None and url_port != socket_port:
                        errors.append(
                            f"Socket URL port mismatch: URL specifies port {url_port} "
                            f"but approved socket port is {socket_port}. "
                            f"Socket file would be at {socket_dir}/.s.PGSQL.{socket_port}, "
                            f"not {socket_dir}/.s.PGSQL.{url_port}."
                        )
                    elif not _verify_socket_file_exists(socket_dir, socket_port):
                        errors.append(
                            f"PostgreSQL socket file not found or not a socket at "
                            f"{socket_dir}/.s.PGSQL.{socket_port}"
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
    
    All failure paths guarantee:
    - verify_session is closed
    - engine is disposed (no leaked connections)
    
    Args:
        db_url: Database URL
        
    Returns:
        Tuple of (guarded_factory, engine) for explicit teardown
        
    Raises:
        TargetGuardError: If initial verification fails
        Exception: If session creation or verification fails for any reason
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(db_url)
    factory = sessionmaker(bind=engine)
    
    # Verify target on first session
    # All failure paths must close session and dispose engine
    verify_session = None
    try:
        verify_session = factory()
        actual_target = verify_test_target(verify_session, db_url=db_url)
    except Exception:
        # ANY failure: dispose engine (closes all pooled connections)
        engine.dispose()
        raise
    finally:
        # Always close the verification session
        if verify_session is not None:
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


# === Unit tests for socket validation helpers ===

def test_extract_socket_dir_valid():
    """Valid Unix socket URL extracts directory correctly."""
    url = "postgresql://user@/db?host=/tmp"
    assert _extract_socket_dir_from_url(url) == "/tmp"


def test_extract_socket_dir_tcp_returns_none():
    """TCP URL (with hostname) returns None."""
    url = "postgresql://user@localhost:5432/db"
    assert _extract_socket_dir_from_url(url) is None


def test_extract_socket_dir_no_host_param_returns_none():
    """URL without host= query param returns None."""
    url = "postgresql://user@/db"
    assert _extract_socket_dir_from_url(url) is None


def test_extract_socket_dir_empty_host_returns_empty_string():
    """URL with host= (empty value) returns empty string."""
    url = "postgresql://user@/db?host="
    assert _extract_socket_dir_from_url(url) == ""


def test_extract_socket_dir_multiple_hosts_rejected():
    """URL with multiple host= params raises TargetGuardError."""
    url = "postgresql://user@/db?host=/tmp&host=/var/run/postgresql"
    try:
        _extract_socket_dir_from_url(url)
        assert False, "Should have raised TargetGuardError"
    except TargetGuardError as e:
        assert "Multiple host parameters" in str(e)


def test_extract_socket_dir_authority_and_query_host_conflict():
    """URL with both authority hostname AND query host= is rejected (ambiguous)."""
    url = "postgresql://user@localhost/db?host=/tmp"
    try:
        _extract_socket_dir_from_url(url)
        assert False, "Should have raised TargetGuardError"
    except TargetGuardError as e:
        assert "both authority hostname" in str(e) and "query host=" in str(e)


def test_extract_port_from_url_query_param():
    """Port extracted from query param takes precedence."""
    url = "postgresql://user@/db?host=/tmp&port=5433"
    assert _extract_port_from_url(url) == 5433


def test_extract_port_from_url_authority():
    """Port extracted from authority when no query param."""
    url = "postgresql://user@localhost:5433/db"
    assert _extract_port_from_url(url) == 5433


def test_extract_port_from_url_none():
    """No port returns None."""
    url = "postgresql://user@/db?host=/tmp"
    assert _extract_port_from_url(url) is None


def test_verify_socket_file_exists_mocked_socket():
    """Socket file exists — mocked to not depend on real PostgreSQL."""
    from unittest.mock import patch, MagicMock
    import stat as stat_module
    
    mock_stat_result = MagicMock()
    mock_stat_result.st_mode = stat_module.S_IFSOCK | 0o755
    
    with patch("os.path.exists", return_value=True), \
         patch("os.stat", return_value=mock_stat_result):
        assert _verify_socket_file_exists("/tmp", 5432) is True


def test_verify_socket_file_exists_mocked_not_socket():
    """File exists but is not a socket type — mocked."""
    from unittest.mock import patch, MagicMock
    import stat as stat_module
    
    mock_stat_result = MagicMock()
    mock_stat_result.st_mode = stat_module.S_IFREG | 0o644  # Regular file
    
    with patch("os.path.exists", return_value=True), \
         patch("os.stat", return_value=mock_stat_result):
        assert _verify_socket_file_exists("/tmp", 5432) is False


def test_verify_socket_file_exists_mocked_not_found():
    """Socket file doesn't exist — mocked."""
    from unittest.mock import patch
    
    with patch("os.path.exists", return_value=False):
        assert _verify_socket_file_exists("/evil", 5432) is False


def test_verify_socket_file_exists_mocked_wrong_port():
    """Socket file doesn't exist for wrong port — mocked."""
    from unittest.mock import patch
    
    with patch("os.path.exists", return_value=False):
        assert _verify_socket_file_exists("/tmp", 9999) is False


# === Tests for is_local_database_url (Gap 二) ===

def test_is_local_database_url_sqlite():
    """SQLite URLs are local."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("sqlite:///./test.db") is True
    assert is_local_database_url("sqlite:///:memory:") is True


def test_is_local_database_url_loopback_tcp():
    """Loopback TCP URLs are local."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("postgresql://user@127.0.0.1:5432/db") is True
    assert is_local_database_url("postgresql://user@localhost:5432/db") is True
    assert is_local_database_url("postgresql://user@[::1]:5432/db") is True


def test_is_local_database_url_remote_tcp():
    """Remote TCP URLs are NOT local."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("postgresql://user@remotehost:5432/db") is False
    assert is_local_database_url("postgresql://user@192.168.1.100:5432/db") is False


def test_is_local_database_url_unix_socket_with_path():
    """Unix socket URLs with explicit path are local."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("postgresql://user@/db?host=/tmp") is True
    assert is_local_database_url("postgresql://user@/db?host=/var/run/postgresql") is True


def test_is_local_database_url_unix_socket_no_host_param():
    """Unix socket URLs without host= param are local (default socket)."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("postgresql://user@/db") is True


def test_is_local_database_url_unix_socket_empty_host():
    """Unix socket URLs with empty host= are local (default socket)."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("postgresql://user@/db?host=") is True


def test_is_local_database_url_hostname_takes_precedence():
    """When hostname is present, query host= is ignored (TCP mode)."""
    from app.auth.candidate_access import is_local_database_url
    # TCP to loopback with query host= → local (TCP wins)
    assert is_local_database_url("postgresql://user@127.0.0.1/db?host=/tmp") is True
    # TCP to remote with query host= → NOT local (TCP wins)
    assert is_local_database_url("postgresql://user@remotehost/db?host=/tmp") is False


def test_is_local_database_url_empty_string():
    """Empty URL is not local."""
    from app.auth.candidate_access import is_local_database_url
    assert is_local_database_url("") is False
    assert is_local_database_url(None) is False
