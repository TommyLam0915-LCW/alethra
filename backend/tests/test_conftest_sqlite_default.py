"""The default SQLite database must not be a file inside the checkout.

``backend/test.db`` (3,043,328 bytes, written 2026-11-20 03:07:34Z by a bare
``pytest`` that skipped the runner) is the artifact this contract exists to make
unrepeatable: ``conftest.py`` used to fall back to ``sqlite:///./test.db``, a
path relative to the current directory, so any run that named no database wrote
into the shared worktree and collided with every other run there. The old
record of that collision is ``EXECUTION_TABLE_20261117.md`` §六 量測協議 and
§8.8.2 ②; the file itself is kept, unmodified, as the evidence.

Nothing here starts a database server or runs a migration: the resolver is pure,
and the two fixture modules are only imported so their module-level URL can be
read back.

``TestDotenvSourceNeutralised`` covers the second entry point removed from the
lane's reach -- the relative ``env_file=".env"`` in ``app/config.py``, which made
an unreviewed gitignored file reconfigure every test process.
"""

import importlib
import os

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings, get_settings
from tests.conftest import _DOTENV_WOULD_LOAD, _sqlite_default_test_db_url

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_SHARED_DEFAULT = "sqlite:///./test.db"
NAMED_PG = "postgresql+psycopg2://alethra_test_runtime@localhost:5432/ai_investment_lab_test"


def _paths(*envs, pid=4242):
    return [_sqlite_default_test_db_url(env, pid) for env in envs]


class TestResolver:
    def test_unnamed_run_gets_a_per_process_file_outside_the_checkout(self):
        resolved = _sqlite_default_test_db_url({}, os.getpid())
        assert resolved.startswith("sqlite:///"), resolved
        database = make_url(resolved).database
        assert os.path.isabs(database), database
        assert os.path.basename(database) == f"alethra-sqlite-test-{os.getpid()}.db"
        assert not database.startswith(BACKEND + os.sep), database

    def test_two_processes_do_not_share_one_file(self):
        env = {"TMPDIR": "/tmp"}
        assert _sqlite_default_test_db_url(env, 111) != _sqlite_default_test_db_url(
            env, 222
        )

    @pytest.mark.parametrize(
        "env",
        [
            {},
            {"TMPDIR": ""},
            {"TMPDIR": "relative/tmp"},
            {"TMPDIR": "/tmp"},
            {"ALETHRA_TEST_DB": ""},
        ],
    )
    def test_no_input_shape_reproduces_the_shared_default(self, env):
        assert _sqlite_default_test_db_url(env, 7) != OLD_SHARED_DEFAULT

    def test_relative_tmpdir_does_not_move_the_file_back_into_the_tree(self):
        resolved = _sqlite_default_test_db_url({"TMPDIR": "../sibling"}, 7)
        assert resolved == "sqlite:////tmp/alethra-sqlite-test-7.db", resolved

    def test_the_absolute_default_is_not_read_as_a_relative_one(self):
        """``sqlite:///x`` is relative and ``sqlite:////x`` absolute in this
        dialect, so the slash count is the thing being asserted here."""
        url = make_url(_sqlite_default_test_db_url({"TMPDIR": "/tmp"}, 7))
        assert url.database == "/tmp/alethra-sqlite-test-7.db", url.database

    @pytest.mark.parametrize("named", [NAMED_PG, "sqlite:////tmp/named-by-owner.db", OLD_SHARED_DEFAULT])
    def test_a_named_database_is_returned_unchanged(self, named):
        """The fix removes a default, it does not re-point a run that chose."""
        env = {"ALETHRA_TEST_DB": named, "TMPDIR": "/tmp"}
        assert _sqlite_default_test_db_url(env, 7) == named


class TestConftestEnvironment:
    def test_both_names_agree_after_conftest_import(self):
        """``DATABASE_URL`` and ``ALETHRA_TEST_DB`` are one answer, not two."""
        assert os.environ["DATABASE_URL"] == os.environ["ALETHRA_TEST_DB"]
        assert os.environ["ALETHRA_TEST_DB"] != OLD_SHARED_DEFAULT
        assert not os.environ["DATABASE_URL"].startswith("sqlite:///./")

    @pytest.mark.parametrize(
        "module",
        ["tests.test_stored_market_data", "tests.test_fb17_dataset_provenance"],
    )
    def test_fixture_modules_that_re_read_the_name_do_not_get_the_worktree(
        self, module
    ):
        """conftest is imported before any test module, so the name it publishes
        is the one these two module-level ``create_engine`` calls see."""
        resolved = importlib.import_module(module)._TEST_DB_PATH
        assert resolved == os.environ["ALETHRA_TEST_DB"], resolved
        assert resolved != OLD_SHARED_DEFAULT, resolved
        url = make_url(resolved)
        if url.get_backend_name() == "sqlite":
            assert os.path.isabs(url.database), resolved
            assert not str(url.database).startswith(BACKEND + os.sep), resolved


# The keys a local dotenv can flip without any test asking, and the values the
# module declares when no dotenv is read.
DOTENV_KEYS = {
    "DATA_PROVIDER": "twelvedata",
    "LIVE_TRADING_ENABLED": "true",
    "ENABLE_LEARNING_SCHEDULER": "true",
    "AUTO_COLLECTION_ENABLED": "true",
}
CODE_DEFAULTS = {
    "data_provider": "demo",
    "live_trading_enabled": False,
    "enable_learning_scheduler": False,
    "auto_collection_enabled": False,
}


def _write_dotenv(directory):
    path = directory / ".env"
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in DOTENV_KEYS.items()) + "\n",
        encoding="utf-8",
    )
    return path


class TestDotenvSourceNeutralised:
    """A gitignored `backend/.env` must not reconfigure the tested process.

    Same class of entry point as the shared `test.db` above: `app/config.py`
    names its dotenv source ``".env"``, which pydantic-settings resolves against
    the cwd, and the lane's cwd is `backend/`. The three keys below decide
    whether a test may reach a market-data API (`DATA_PROVIDER`) and whether
    importing `app.main` arms the scheduler whose startup reads
    `settings.enable_learning_scheduler`.
    """

    def test_the_pristine_source_was_one_relative_dotenv(self):
        """Without this the neutralisation would be guarding nothing."""
        assert _DOTENV_WOULD_LOAD == ".env", _DOTENV_WOULD_LOAD

    def test_the_app_setting_no_longer_declares_a_dotenv(self):
        assert Settings.model_config.get("env_file") is None, dict(
            Settings.model_config
        )

    def test_a_named_env_file_still_configures_the_app(self, tmp_path):
        """The dotenv mechanism is untouched: only the *implicit* source is gone.

        A reviewer has to be able to see that the test below could have failed,
        otherwise "the file is ignored" is indistinguishable from "pydantic never
        reads dotenv files at all".
        """
        path = _write_dotenv(tmp_path)
        from_dotenv = Settings(_env_file=str(path))
        assert {
            field: getattr(from_dotenv, field) for field in CODE_DEFAULTS
        } == {
            "data_provider": "twelvedata",
            "live_trading_enabled": True,
            "enable_learning_scheduler": True,
            "auto_collection_enabled": True,
        }

    def test_the_same_file_ignored_from_the_lane_cwd(self, tmp_path, monkeypatch):
        _write_dotenv(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert {
            field: getattr(Settings(), field) for field in CODE_DEFAULTS
        } == CODE_DEFAULTS

    def test_the_import_time_snapshot_keeps_the_code_defaults(self):
        """`app/main.py:13` caches `get_settings()` at import; that snapshot is
        what `main.py:52` starts the scheduler from, so it is the value that has
        to be the code default rather than whatever a local file said."""
        snapshot = get_settings()
        assert {field: getattr(snapshot, field) for field in CODE_DEFAULTS} == CODE_DEFAULTS
