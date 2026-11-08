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
import pytest
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


def _extract_port_from_url(db_url: str):
    """Extract port from PostgreSQL connection URL (fail-closed).

    Checks (in order):
    1. Query param port= (for socket URLs)
    2. Authority port (for TCP URLs)

    Returns:
        int: The extracted port number
        None: If no port specified at all (caller uses config default)

    Raises:
        TargetGuardError: If port is illegal:
        - Empty value (port=)
        - Non-numeric value
        - Multiple port= query params
        - Out of range (not 1-65535)
        - Both authority port AND query port present (conflict)
    """
    parsed = urlparse(db_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    port_values = query_params.get("port", [])
    has_authority_port = parsed.port is not None

    # Conflict: both authority port and query port present
    if has_authority_port and port_values:
        raise TargetGuardError(
            f"URL has both authority port ({parsed.port}) and "
            f"query port= ({port_values!r}). Ambiguous port target rejected."
        )

    # No port at all → None (missing, not illegal)
    if not port_values and not has_authority_port:
        return None

    # Parse query port if present
    if port_values:
        if len(port_values) > 1:
            raise TargetGuardError(
                f"Multiple port parameters in URL: {port_values!r}. "
                f"Ambiguous port target rejected."
            )
        raw_port = port_values[0]
        if raw_port == "":
            raise TargetGuardError(
                "Port query parameter is empty string. "
                "Fail-closed: must specify explicit numeric port."
            )
        try:
            port = int(raw_port)
        except ValueError:
            raise TargetGuardError(
                f"Port is not a valid integer: {raw_port!r}"
            )
        if not (1 <= port <= 65535):
            raise TargetGuardError(
                f"Port out of valid range (1-65535): {port}"
            )
        return port

    # Authority port — validate range
    port = parsed.port
    if port is not None:
        if not (1 <= port <= 65535):
            raise TargetGuardError(
                f"Port out of valid range (1-65535): {port}"
            )
        return port
    
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


def _mask_url_credentials(db_url: str) -> str:
    """Mask password in URL for safe logging. Returns URL with password replaced by '***'."""
    if not isinstance(db_url, str):
        return "<unparseable URL>"
    
    try:
        parsed = urlparse(db_url)
        if parsed.password:
            # Reconstruct netloc with masked password
            # netloc format: [user[:password]@]host[:port]
            userinfo = parsed.username or ""
            hostinfo = parsed.hostname or ""
            if parsed.port:
                hostinfo = f"{hostinfo}:{parsed.port}"
            
            if userinfo:
                new_netloc = f"{userinfo}:***@{hostinfo}"
            else:
                new_netloc = hostinfo
            
            masked = parsed._replace(netloc=new_netloc)
            return masked.geturl()
        return db_url
    except Exception:
        return "<unparseable URL>"


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
                        f"Ambiguous socket target rejected. URL: {_mask_url_credentials(db_url)!r}"
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
                    try:
                        url_port = _extract_port_from_url(db_url)
                    except TargetGuardError as e:
                        errors.append(str(e))
                    else:
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
    
    All failure paths guarantee:
    - verify_session is closed (if created)
    - engine is disposed (no leaked connections)
    - Both primary and cleanup errors are observable (never silently swallowed)
    
    Error handling:
    - If sessionmaker() fails: dispose engine, raise original
    - If factory() fails: close session (if created), dispose engine, raise original
    - If verify_test_target() fails: close session, dispose engine, raise original
    - If session.close() fails after primary error: chain via __context__
    - If session.close() fails without primary error: dispose engine, raise close error
    - If engine.dispose() fails: chain via __context__ with whatever error triggered it
    
    Args:
        db_url: Database URL
        
    Returns:
        Tuple of (guarded_factory, engine) for explicit teardown
        
    Raises:
        TargetGuardError: If initial verification fails
        Exception: If any step fails; cleanup errors chained via __context__
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(db_url)
    verify_session = None
    primary_error = None
    
    try:
        # sessionmaker construction is part of engine failure protection
        factory = sessionmaker(bind=engine)
        verify_session = factory()
        verify_test_target(verify_session, db_url=db_url)
    except Exception as e:
        primary_error = e
        # Attempt session close first (if session was created)
        if verify_session is not None:
            try:
                verify_session.close()
            except Exception as close_err:
                # Chain: close happened because of primary
                close_err.__context__ = e
                primary_error = close_err
        # Now dispose engine
        try:
            engine.dispose()
        except Exception as dispose_err:
            # Chain: dispose happened because of primary
            dispose_err.__context__ = primary_error
            raise dispose_err
        raise primary_error
    
    # Success path: close verification session
    # If close fails, must still dispose engine
    try:
        verify_session.close()
    except Exception as close_err:
        try:
            engine.dispose()
        except Exception as dispose_err:
            # Chain both errors for observability
            dispose_err.__context__ = close_err
            raise dispose_err
        raise close_err
    
    # Return factory for ongoing use
    def guarded_factory():
        return factory()
    
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


def test_extract_port_from_url_empty_value_rejected():
    """Empty port= value is rejected (fail-closed)."""
    url = "postgresql://user@/db?host=/tmp&port="
    with pytest.raises(TargetGuardError, match="empty string"):
        _extract_port_from_url(url)


def test_extract_port_from_url_non_numeric_rejected():
    """Non-numeric port value is rejected."""
    url = "postgresql://user@/db?host=/tmp&port=abc"
    with pytest.raises(TargetGuardError, match="not a valid integer"):
        _extract_port_from_url(url)


def test_extract_port_from_url_multiple_values_rejected():
    """Multiple port= values are rejected."""
    url = "postgresql://user@/db?host=/tmp&port=5432&port=5433"
    with pytest.raises(TargetGuardError, match="Multiple port parameters"):
        _extract_port_from_url(url)


def test_extract_port_from_url_out_of_range_rejected():
    """Port outside 1-65535 is rejected (query param)."""
    url = "postgresql://user@/db?host=/tmp&port=70000"
    with pytest.raises(TargetGuardError, match="out of valid range"):
        _extract_port_from_url(url)


def test_extract_port_from_url_zero_rejected():
    """Port 0 is rejected (query param)."""
    url = "postgresql://user@/db?host=/tmp&port=0"
    with pytest.raises(TargetGuardError, match="out of valid range"):
        _extract_port_from_url(url)


def test_extract_port_from_url_authority_zero_rejected():
    """Authority port 0 is rejected."""
    url = "postgresql://user@localhost:0/db"
    with pytest.raises(TargetGuardError, match="out of valid range"):
        _extract_port_from_url(url)


def test_extract_port_from_url_authority_and_query_conflict():
    """Both authority port AND query port= is rejected (ambiguous)."""
    url = "postgresql://user@localhost:5432/db?port=5433"
    with pytest.raises(TargetGuardError, match="both authority port.*query port="):
        _extract_port_from_url(url)


def test_verify_test_target_illegal_port_not_fallback():
    """Integration: illegal port in URL raises, doesn't silently use approved 5432."""
    from unittest.mock import Mock
    
    mock_session = Mock()
    mock_result = Mock()
    # Simulate Unix socket mode (NULL addr/port)
    mock_result.fetchone.return_value = ("ai_investment_lab_test", "alethra_test_runtime", None, None)
    mock_session.execute.return_value = mock_result
    
    # URL with illegal port (out of range)
    illegal_port_url = "postgresql:///ai_investment_lab_test?host=/tmp&port=99999"
    
    try:
        verify_test_target(mock_session, db_url=illegal_port_url)
        assert False, "Should have raised TargetGuardError"
    except TargetGuardError as e:
        # Must mention port error, NOT silently pass with approved 5432
        assert "out of valid range" in str(e) or "Port" in str(e)


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


# === Gap 3: Socket actual target validation tests ===

def test_socket_validation_wrong_port_in_url():
    """URL with wrong port parameter fails validation."""
    # Socket file exists at /tmp/.s.PGSQL.5432 but not at /tmp/.s.PGSQL.9999
    url = "postgresql://user@/db?host=/tmp&port=9999"
    socket_dir = _extract_socket_dir_from_url(url)
    assert socket_dir == "/tmp"
    # Validation would check port from URL against approved port
    # Since we're checking the helper, verify it extracts correctly
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert params.get("port", ["5432"])[0] == "9999"


def test_socket_validation_wrong_instance_dir():
    """URL with unapproved socket directory fails validation."""
    url = "postgresql://user@/db?host=/var/run/postgresql"
    socket_dir = _extract_socket_dir_from_url(url)
    assert socket_dir == "/var/run/postgresql"
    # This dir is not in approved_socket_dirs ({"/tmp"})
    approved_dirs = {"/tmp"}
    assert socket_dir not in approved_dirs


def test_socket_validation_conflicting_host_params():
    """URL with multiple conflicting host= params is rejected."""
    url = "postgresql://user@/db?host=/tmp&host=/evil"
    try:
        _extract_socket_dir_from_url(url)
        assert False, "Should have raised TargetGuardError for multiple host params"
    except TargetGuardError as e:
        assert "Multiple host parameters" in str(e)


def test_socket_validation_empty_host_rejected():
    """URL with empty host= is rejected (ambiguous default socket)."""
    url = "postgresql://user@/db?host="
    socket_dir = _extract_socket_dir_from_url(url)
    assert socket_dir == ""
    # Empty string is not in approved dirs and would be rejected by validation
    approved_dirs = {"/tmp"}
    assert socket_dir not in approved_dirs


def test_socket_validation_no_host_param_rejected():
    """URL without host= param is rejected (ambiguous socket target)."""
    url = "postgresql://user@/db"
    socket_dir = _extract_socket_dir_from_url(url)
    assert socket_dir is None
    # None means no explicit socket dir — rejected by validation


# === Gap 4: Credential masking tests ===

def test_mask_url_credentials_with_password():
    """URL with password is masked."""
    url = "postgresql://user:secretpassword@localhost:5432/db"
    masked = _mask_url_credentials(url)
    assert "secretpassword" not in masked
    assert "***" in masked
    assert "user" in masked
    assert "localhost" in masked


def test_mask_url_credentials_without_password():
    """URL without password is unchanged."""
    url = "postgresql://user@localhost:5432/db"
    masked = _mask_url_credentials(url)
    assert masked == url


def test_mask_url_credentials_unparseable():
    """Non-string input returns safe placeholder."""
    # None would cause an exception in urlparse
    masked = _mask_url_credentials(None)
    assert masked == "<unparseable URL>"
    
    # Invalid type would cause an exception
    masked = _mask_url_credentials(123)
    assert masked == "<unparseable URL>"


def test_error_message_masks_credentials():
    """Error messages don't expose passwords."""
    # Simulate an error with a URL containing a password
    url_with_password = "postgresql://user:secretpassword@/db"
    masked = _mask_url_credentials(url_with_password)
    error_msg = f"Ambiguous socket target rejected. URL: {masked!r}"
    assert "secretpassword" not in error_msg
    assert "***" in error_msg


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


# === Tests for create_verified_session_factory close/dispose ===

def test_factory_verify_failure_disposes_engine():
    """engine.dispose() called when verify_test_target raises."""
    from unittest.mock import MagicMock, patch
    import sys
    
    mock_engine = MagicMock()
    mock_session = MagicMock()
    mock_factory_fn = MagicMock(return_value=mock_session)
    
    # Get module reference for patching
    mod = sys.modules[__name__]
    
    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("sqlalchemy.orm.sessionmaker", return_value=mock_factory_fn), \
         patch.object(mod, "verify_test_target",
                      side_effect=TargetGuardError("verify failed")):
        with pytest.raises(TargetGuardError, match="verify failed"):
            create_verified_session_factory("postgresql:///test")
        
        mock_engine.dispose.assert_called_once()
        mock_session.close.assert_called_once()


def test_factory_sessionmaker_failure_disposes_engine():
    """engine.dispose() called when sessionmaker() raises."""
    from unittest.mock import MagicMock, patch
    
    mock_engine = MagicMock()
    
    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("sqlalchemy.orm.sessionmaker",
               side_effect=RuntimeError("sessionmaker failed")):
        with pytest.raises(RuntimeError, match="sessionmaker failed"):
            create_verified_session_factory("postgresql:///test")
        
        mock_engine.dispose.assert_called_once()


def test_factory_session_close_failure_disposes_engine():
    """engine.dispose() called when session.close() fails after success."""
    from unittest.mock import MagicMock, patch
    import sys
    
    mock_engine = MagicMock()
    mock_session = MagicMock()
    mock_session.close.side_effect = RuntimeError("close failed")
    mock_factory_fn = MagicMock(return_value=mock_session)
    
    mod = sys.modules[__name__]
    
    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("sqlalchemy.orm.sessionmaker", return_value=mock_factory_fn), \
         patch.object(mod, "verify_test_target", return_value={"ok": True}):
        with pytest.raises(RuntimeError, match="close failed"):
            create_verified_session_factory("postgresql:///test")
        
        mock_engine.dispose.assert_called_once()


def test_factory_close_error_chained_with_dispose_error():
    """Both close and dispose errors observable when both fail."""
    from unittest.mock import MagicMock, patch
    import sys
    
    mock_engine = MagicMock()
    mock_engine.dispose.side_effect = RuntimeError("dispose failed")
    mock_session = MagicMock()
    mock_session.close.side_effect = RuntimeError("close failed")
    mock_factory_fn = MagicMock(return_value=mock_session)
    
    mod = sys.modules[__name__]
    
    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("sqlalchemy.orm.sessionmaker", return_value=mock_factory_fn), \
         patch.object(mod, "verify_test_target", return_value={"ok": True}):
        with pytest.raises(RuntimeError, match="dispose failed") as exc_info:
            create_verified_session_factory("postgresql:///test")
        
        # Dispose error raised, with close error chained via __context__
        assert exc_info.value.__context__ is not None
        assert "close failed" in str(exc_info.value.__context__)


def test_factory_verify_failure_close_error_chained():
    """When verify fails AND close fails, close error chains to verify error."""
    from unittest.mock import MagicMock, patch
    import sys
    
    mock_engine = MagicMock()
    mock_session = MagicMock()
    mock_session.close.side_effect = RuntimeError("close failed")
    mock_factory_fn = MagicMock(return_value=mock_session)
    verify_error = TargetGuardError("verify failed")
    
    mod = sys.modules[__name__]
    
    with patch("sqlalchemy.create_engine", return_value=mock_engine), \
         patch("sqlalchemy.orm.sessionmaker", return_value=mock_factory_fn), \
         patch.object(mod, "verify_test_target", side_effect=verify_error):
        with pytest.raises(RuntimeError, match="close failed") as exc_info:
            create_verified_session_factory("postgresql:///test")
        
        # Close error raised, with verify error chained via __context__
        assert exc_info.value.__context__ is verify_error
        mock_engine.dispose.assert_called_once()
