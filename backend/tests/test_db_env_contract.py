"""The DB environment must describe one database, or none of the PG-only ones.

The branches below are exercised on literal mappings, so this test proves the
decision logic itself rather than restating it: a run whose PostgreSQL
variables disagree is rejected before any module gets the chance to skip, and a
password can never reach an error message.
"""

import os

from scripts.check_db_env import DB_VARS, PG_ONLY, analyse

PASSWORD = "Th1s-is-a-password-marker"
HOST = "disposable-db.internal"
NAME = "alethra_contract_test"


def _pg_env(**overrides):
    url = f"postgresql+psycopg2://u:{PASSWORD}@{HOST}:5432/{NAME}"
    env = {var: url for var in DB_VARS}
    env.update(overrides)
    return env


def _sqlite_env():
    return {
        "DATABASE_URL": "sqlite:////tmp/alethra-contract test.db",
        "ALETHRA_TEST_DB": "sqlite:////tmp/alethra-contract test.db",
    }


class TestCoherentEnvironments:
    def test_sqlite_without_pg_variables_is_accepted(self):
        result = analyse(_sqlite_env())
        assert result.errors == ()
        assert result.mode == "sqlite"

    def test_postgres_variables_agreeing_on_one_database_are_accepted(self):
        result = analyse(_pg_env(), required_prefix="alethra_")
        assert result.errors == ()
        assert result.mode == "postgresql"
        assert result.database == NAME


class TestRejectedEnvironments:
    def test_one_pg_variable_without_the_others_is_rejected(self):
        env = _sqlite_env()
        env["ALETHRA_PG_DATABASE_URL"] = "postgresql://u:p@h:5432/alethra_x"
        result = analyse(env)
        assert result.errors
        assert any("ALETHRA_PG_DATABASE_URL" in e for e in result.errors)

    def test_missing_variable_in_a_pg_run_is_named(self):
        env = _pg_env()
        del env["ALETHRA_TEST_POSTGRES_URL"]
        result = analyse(env)
        assert result.errors
        assert any("ALETHRA_TEST_POSTGRES_URL" in e for e in result.errors)

    def test_variables_pointing_at_different_databases_are_rejected(self):
        env = _pg_env(
            ALETHRA_TEST_POSTGRES_URL=(
                f"postgresql://u:{PASSWORD}@other-host:5432/other_database"
            )
        )
        result = analyse(env)
        assert result.errors
        assert any("disagree" in e for e in result.errors)

    def test_sqlite_url_inside_a_pg_run_is_rejected_as_mixed_backends(self):
        env = _pg_env(ALETHRA_TEST_DB="sqlite:////tmp/x.db")
        result = analyse(env)
        assert result.errors
        assert any("mixed backends" in e for e in result.errors)

    def test_database_outside_the_required_prefix_is_rejected(self):
        env = _pg_env(
            **{var: f"postgresql://u:{PASSWORD}@h:5432/shared_runtime" for var in DB_VARS}
        )
        result = analyse(env, required_prefix="alethra_")
        assert result.errors
        assert any("prefix" in e for e in result.errors)

    def test_unparseable_value_is_rejected_by_name(self):
        env = _sqlite_env()
        env["ALETHRA_TEST_DB"] = "not a url"
        result = analyse(env)
        assert result.errors
        assert any("ALETHRA_TEST_DB" in e for e in result.errors)


class TestMessages:
    def test_no_password_or_full_url_reaches_any_message(self):
        env = _pg_env(
            ALETHRA_TEST_POSTGRES_URL=f"postgresql://u:{PASSWORD}@other-host:5432/other_db"
        )
        result = analyse(env, required_prefix="alethra_")
        text = result.summary + " " + " ".join(result.errors)
        assert PASSWORD not in text
        assert "postgresql://" not in text
        assert "postgresql+psycopg2://" not in text


class TestLiveEnvironment:
    """The environment this run actually uses must itself be coherent."""

    def test_live_db_environment_is_coherent(self):
        result = analyse(
            os.environ,
            required_prefix=os.environ.get("ALETHRA_TEST_REQUIRED_DB_PREFIX") or None,
        )
        assert result.errors == ()
        # Every PostgreSQL-only module decides whether to run from these two
        # variables, so a sqlite run must not leave them half-set.
        if result.mode == "sqlite":
            assert not any(var in os.environ for var in PG_ONLY)