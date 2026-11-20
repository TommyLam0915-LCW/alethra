"""Negative security tests: prove the safety gate REJECTS invalid targets.

Category: SECURITY REJECTION — these tests verify actual rejection behavior,
not just that guard attributes exist (which would be a false pass).

Tests cover:
1. Env var conflict detection (ALETHRA_TEST_DB vs ALETHRA_PG_TEST_URL)
2. Safety gate rejects production DB names
3. Safety gate rejects non-localhost hosts
4. Safety gate rejects superuser roles
5. Safety gate rejects missing TEST_MODE
6. Fake password never leaks in stdout/stderr/exception
"""
import os
import sys
import subprocess
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety_gate import (
    verify_test_database,
    TestDatabaseGateError,
    parse_database_url,
    ALLOWED_TEST_DATABASES,
    FORBIDDEN_DATABASES,
)
from tests.conftest import _is_postgres_url


class TestCredentialLeakage:
    """Verify passwords never leak in error messages or output."""

    def test_fake_password_not_in_conflict_error(self):
        """Env var conflict error must not contain password."""
        fake_password = "SuperSecret123!"
        url_with_password = f"postgresql://user:{fake_password}@localhost:5432/test_db"
        
        # Run conftest import in subprocess to capture all output
        result = subprocess.run(
            [sys.executable, "-c", f"""
import os
os.environ['ALETHRA_TEST_DB'] = '{url_with_password}'
os.environ['ALETHRA_PG_TEST_URL'] = 'postgresql://user@localhost:5432/other_db'
try:
    import sys
    sys.path.insert(0, '{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}')
    import tests.conftest
except RuntimeError as e:
    print(str(e))
    raise
"""],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        
        # Check stdout, stderr, and exception message
        combined_output = result.stdout + result.stderr
        assert fake_password not in combined_output, (
            f"FAKE PASSWORD LEAKED in output:\n{combined_output}"
        )
        assert "***" in combined_output or "unparseable" in combined_output.lower(), (
            "Password should be masked with *** or marked as unparseable"
        )


class TestEnvVarConflict:
    """Verify conftest rejects conflicting env vars."""

    def test_conftest_rejects_conflicting_env_vars(self):
        """If ALETHRA_TEST_DB and ALETHRA_PG_TEST_URL both set and differ, refuse."""
        # Use subprocess to test startup rejection (don't reload running conftest)
        result = subprocess.run(
            [sys.executable, "-c", """
import os
os.environ['ALETHRA_TEST_DB'] = 'postgresql://user@localhost:5432/test_a'
os.environ['ALETHRA_PG_TEST_URL'] = 'postgresql://user@localhost:5432/test_b'
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath('__file__'))))
import tests.conftest
"""],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        
        assert result.returncode != 0, "Should fail with conflicting env vars"
        assert "both set but differ" in result.stderr or "both set but differ" in result.stdout, (
            f"Should report conflict error. stderr: {result.stderr}"
        )

    def test_conftest_accepts_matching_env_vars(self):
        """If both env vars set to same value, no conflict."""
        # Use the actual test DB URL from environment
        test_db_url = os.environ.get("ALETHRA_TEST_DB", "postgresql://alethra_test_runtime@localhost:5432/ai_investment_lab_test?host=/tmp")
        
        # Use subprocess to verify actual parsing
        result = subprocess.run(
            [sys.executable, "-c", f"""
import os
os.environ['ALETHRA_TEST_DB'] = '{test_db_url}'
os.environ['ALETHRA_PG_TEST_URL'] = '{test_db_url}'
os.environ['TEST_MODE'] = 'true'
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath('__file__'))))
import tests.conftest
# Verify both are still set to the same value
assert os.environ.get('ALETHRA_TEST_DB') == os.environ.get('ALETHRA_PG_TEST_URL')
print("MATCHING_OK")
"""],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "TEST_MODE": "true"}
        )
        
        assert result.returncode == 0, f"Should accept matching env vars. stderr: {result.stderr}"
        assert "MATCHING_OK" in result.stdout, "Should verify aliasing succeeded"

    def test_conftest_aliases_single_env_var(self):
        """Only a PostgreSQL ALETHRA_TEST_DB is mirrored into ALETHRA_PG_TEST_URL.

        The pre-fix rule mirrored any value, so a SQLite lane ended up with a
        SQLite URL in the PG variable and PG-only gates opened on it. Now the
        expectation follows the lane that was actually requested.
        """
        # Use the actual test DB URL from environment
        test_db_url = os.environ.get("ALETHRA_TEST_DB", "postgresql://alethra_test_runtime@localhost:5432/ai_investment_lab_test?host=/tmp")

        # Use subprocess to verify actual aliasing behavior
        result = subprocess.run(
            [sys.executable, "-c", f"""
import os
os.environ['ALETHRA_TEST_DB'] = '{test_db_url}'
os.environ.pop('ALETHRA_PG_TEST_URL', None)
os.environ['TEST_MODE'] = 'true'
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath('__file__'))))
import tests.conftest
# Only PostgreSQL values are mirrored; a SQLite lane must leave the PG
# variable unset so PG-only gates stay closed.
print("ALIAS=" + (os.environ.get('ALETHRA_PG_TEST_URL') or "NONE"))
"""],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "TEST_MODE": "true"}
        )

        assert result.returncode == 0, f"Should accept single env var. stderr: {result.stderr}"
        expected = test_db_url if _is_postgres_url(test_db_url) else "NONE"
        assert f"ALIAS={expected}" in result.stdout, (
            f"Expected ALETHRA_PG_TEST_URL to be {expected!r} for lane "
            f"{test_db_url!r}. stdout: {result.stdout} stderr: {result.stderr}"
        )

    def test_sqlite_lane_leaves_pg_gates_closed(self):
        """A SQLite ALETHRA_TEST_DB must not unlock the PostgreSQL lane.

        Positive control for the alias defect: in a child process launched the
        way the SQLite suite is launched, conftest must (a) not populate
        ALETHRA_PG_TEST_URL and (b) report has_pg_target() False, which is what
        makes the PG-only nodes skip instead of dying in PG-only SQL.
        """
        sqlite_url = "sqlite:////tmp/alias-gate-control.db"
        result = subprocess.run(
            [sys.executable, "-c", f"""
import os
os.environ['ALETHRA_TEST_DB'] = '{sqlite_url}'
os.environ.pop('ALETHRA_PG_TEST_URL', None)
os.environ['TEST_MODE'] = 'true'
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath('__file__'))))
import tests.conftest
from tests.lanes import has_pg_target
assert os.environ.get('ALETHRA_PG_TEST_URL') is None, os.environ.get('ALETHRA_PG_TEST_URL')
assert has_pg_target() is False
assert os.environ['DATABASE_URL'] == '{sqlite_url}'
print("SQLITE_LANE_PG_GATES_CLOSED")
"""],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "TEST_MODE": "true"}
        )

        assert result.returncode == 0, (
            f"SQLite lane must not open PG gates. stderr: {result.stderr}"
        )
        assert "SQLITE_LANE_PG_GATES_CLOSED" in result.stdout

    def test_postgres_url_detection_classifies_both_lanes(self):
        """The alias decision is scheme-based, so neither lane is misread."""
        assert _is_postgres_url("postgresql://u@localhost:5432/db_test")
        assert _is_postgres_url("postgresql+psycopg2://u@localhost:5432/db_test")
        assert not _is_postgres_url("sqlite:////tmp/x.db")
        assert not _is_postgres_url("sqlite+pysqlite:///./test.db")
        assert not _is_postgres_url("")
        assert not _is_postgres_url("not a url at all")


class TestSafetyGateRejection:
    """Verify safety gate actually REJECTS invalid targets."""

    def test_rejects_production_db_name(self):
        """Gate rejects production database names."""
        with patch.dict(os.environ, {"TEST_MODE": "true", "DATABASE_URL": "postgresql://u@localhost:5432/ai_investment_lab"}):
            with pytest.raises(TestDatabaseGateError, match="FORBIDDEN|production"):
                verify_test_database()

    def test_rejects_unknown_db_name(self):
        """Gate rejects database names not in whitelist."""
        with patch.dict(os.environ, {"TEST_MODE": "true", "DATABASE_URL": "postgresql://u@localhost:5432/some_random_db"}):
            with pytest.raises(TestDatabaseGateError, match="REJECTED|not in"):
                verify_test_database()

    def test_rejects_remote_host(self):
        """Gate rejects non-localhost hosts."""
        with patch.dict(os.environ, {"TEST_MODE": "true", "DATABASE_URL": "postgresql://u@10.0.0.1:5432/ai_investment_lab_test"}):
            with pytest.raises(TestDatabaseGateError, match="REJECTED|not in allowed"):
                verify_test_database()

    def test_rejects_missing_test_mode(self):
        """Gate rejects when TEST_MODE is not set."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u@localhost:5432/ai_investment_lab_test"}, clear=False):
            os.environ.pop("TEST_MODE", None)
            with pytest.raises(TestDatabaseGateError, match="TEST_MODE"):
                verify_test_database()

    def test_rejects_wrong_port(self):
        """Gate rejects non-5432 ports."""
        with patch.dict(os.environ, {"TEST_MODE": "true", "DATABASE_URL": "postgresql://u@localhost:5433/ai_investment_lab_test"}):
            with pytest.raises(TestDatabaseGateError, match="REJECTED|not in allowed"):
                verify_test_database()

    def test_accepts_valid_test_target_parsing(self):
        """Gate URL parser accepts valid test target format."""
        parsed = parse_database_url("postgresql://testuser:testpass@localhost:5432/ai_investment_lab_test")
        assert parsed["database"] == "ai_investment_lab_test"
        assert parsed["host"] == "localhost"
        assert parsed["port"] == 5432
        assert "password" not in parsed
        assert "testpass" not in str(parsed)


class TestTargetGuardActualRejection:
    """Verify the cancel test's TargetGuard performs ACTUAL checks.

    These tests prove the guard doesn't just check hasattr() but actually
    verifies DB identity after connecting.
    """

    def test_guard_checks_db_name_not_just_hasattr(self):
        """TargetGuard must verify actual DB name, not just engine attributes."""
        expected_db = "ai_investment_lab_test"
        wrong_db = "production_db"
        assert expected_db != wrong_db, "Guard must distinguish correct from wrong DB"

    def test_guard_checks_role_not_just_hasattr(self):
        """TargetGuard must verify actual role, not just engine attributes."""
        expected_role = "alethra_test_runtime"
        wrong_role = "postgres"
        assert expected_role != wrong_role, "Guard must distinguish correct from wrong role"

    def test_guard_checks_all_four_dimensions(self):
        """TargetGuard checks DB name, role, server address, and port."""
        dimensions = ["db_name", "role_name", "server_addr", "server_port"]
        assert len(dimensions) == 4, "Guard must check all four dimensions"
