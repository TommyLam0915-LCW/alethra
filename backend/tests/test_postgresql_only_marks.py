"""Regression proofs for the PostgreSQL-only gating machinery.

Nine modules used to answer "is there a PostgreSQL server?" in nine different
ways: a module-level skip that took offline coverage with it, an import-time
connection probe, a bare truthiness test on an environment variable, or a
RuntimeError raised during collection. This file pins the contract they now share
in ``tests/postgresql_only.py`` and, just as importantly, pins what did NOT get
weakened by moving to node granularity -- a hostile or absent target still refuses
to import, rather than turning into a quiet skip.

Two properties are proven separately, because a gate that only ever skips proves
nothing:

* it skips when there is no PostgreSQL target, and the skip says so; and
* it does NOT skip when a PostgreSQL target is named, even a broken one.

Each node drives a real subprocess, so the answer comes from the code under test
rather than from this file's own import state.
"""

import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
PG_URL_ENV = "ALETHRA_PG_TEST_URL"
TEST_DB_ENV = "ALETHRA_TEST_DB"
MARKERS = ("PG-ONLY [tests/", "PG-SETUP [tests/")


def _defined_node_ids(path):
    """The node ids ``path`` defines, read from its own syntax tree.

    Parameters are not expanded -- a function that takes parameters appears once,
    which is what makes the comparison below about *definitions* rather than about
    a total that has to be re-accepted whenever someone adds a node.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node_ids = []

    def is_test(node):
        return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")

    for top in tree.body:
        if is_test(top):
            node_ids.append(top.name)
        elif isinstance(top, ast.ClassDef) and top.name.startswith("Test"):
            for member in top.body:
                if is_test(member):
                    node_ids.append(f"{top.name}::{member.name}")
    return node_ids


def _run(args, env_overrides=None, unset=(), cwd=None):
    """Run ``args`` in a subprocess that starts from this process's environment."""
    env = dict(os.environ)
    for name in unset:
        env.pop(name, None)
    env.update(env_overrides or {})
    return subprocess.run(
        args,
        cwd=str(cwd or BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _probe_module(tmp_path, body):
    """Write a throwaway pytest module that imports the shared helper."""
    module = tmp_path / "probe_postgresql_only.py"
    module.write_text(
        "import os\n"
        "import pytest\n"
        "from tests.postgresql_only import (\n"
        "    is_postgresql_url,\n"
        "    postgresql_available,\n"
        "    postgresql_test_url,\n"
        "    requires_postgresql,\n"
        "    skip_reason,\n"
        "    unmet_setup,\n"
        ")\n\n"
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return module


def _run_probe(tmp_path, module, env_overrides=None):
    """Collect and run ``module`` with no conftest and no database in the picture.

    tmp_path holds no conftest.py, so the probe exercises the helper alone. The
    database names this process was started with are removed, so the child cannot
    inherit an answer to the question being asked of it.
    """
    return _run(
        [sys.executable, "-m", "pytest", module.name, "-p", "no:cacheprovider", "-rs"],
        env_overrides={"PYTHONPATH": str(BACKEND_DIR), **(env_overrides or {})},
        unset=(TEST_DB_ENV, PG_URL_ENV),
        cwd=tmp_path,
    )


class TestSkipsWhenNoPostgresTarget:
    """No PostgreSQL URL named -> the node does not run, and says why."""

    def test_requires_postgresql_skips_and_explains(self, tmp_path):
        module = _probe_module(
            tmp_path,
            """
            def test_needs_a_server():
                requires_postgresql("holds a real row lock")
                raise AssertionError("ran past the gate")
            """,
        )
        result = _run_probe(tmp_path, module)
        out = result.stdout + result.stderr

        assert result.returncode == 0, out
        assert "1 skipped" in out, f"expected a skip, got:\n{out}"
        assert "ran past the gate" not in out
        # The reason has to be actionable on its own: what was needed, that it did
        # not run, and the command that would run it.
        assert "holds a real row lock" in out, out
        assert "NOT RUN, not passed" in out, out
        assert "run_tests.sh pg" in out, out

    def test_skip_reason_names_the_calling_node_not_the_helper(self, tmp_path):
        """A skip raised through the shared helper still identifies its caller.

        pytest reports the raise site, which is always postgresql_only.py, so
        without the call-site walk every gated node in the suite looks identical in
        the -rs summary and the per-node prerequisite stops being evidence about a
        specific node. The bracket is the evidence.
        """
        module = _probe_module(
            tmp_path,
            """
            def test_first():
                requires_postgresql("first prerequisite")

            def test_second():
                requires_postgresql("second prerequisite")
            """,
        )
        result = _run_probe(tmp_path, module)
        out = result.stdout + result.stderr
        skip_lines = [ln for ln in out.splitlines() if ln.startswith("SKIPPED [1]")]

        assert len(skip_lines) == 2, out
        attributed = re.findall(
            r"PG-ONLY \[([^\]]*probe_postgresql_only\.py):(\d+)\]", out
        )
        assert len(attributed) == 2, f"no per-node attribution in:\n{out}"
        assert len({line for _, line in attributed}) == 2, (
            f"both nodes collapsed onto one attribution: {attributed}"
        )
        assert "first prerequisite" in out and "second prerequisite" in out

    def test_fixture_setup_reports_what_was_missing(self, tmp_path):
        module = _probe_module(
            tmp_path,
            """
            @pytest.fixture
            def needs_a_schema():
                unmet_setup("the migration has not been approved")
                yield "never"

            def test_uses_the_fixture(needs_a_schema):
                raise AssertionError()
            """,
        )
        result = _run_probe(tmp_path, module)
        out = result.stdout + result.stderr

        assert "1 skipped" in out, out
        assert "PG-SETUP" in out, out
        assert "the migration has not been approved" in out, out
        assert "NOT RUN, not passed" in out, out

    def test_postgresql_test_url_returns_the_url_it_was_handed(self, tmp_path):
        """The accessor is a gate and a reader at once, so a node cannot gate on
        one value and then connect with another."""
        probe_url = "postgresql+psycopg2://alethra_test_runtime@localhost:5432/alethra_test_probe_shapes"
        module = _probe_module(
            tmp_path,
            f"""
            def test_returns_named_url():
                got = postgresql_test_url("connects to the named database")
                assert got == {probe_url!r}, got
            """,
        )
        result = _run_probe(tmp_path, module, env_overrides={PG_URL_ENV: probe_url})
        out = result.stdout + result.stderr

        assert "1 passed" in out, (
            f"a named PostgreSQL URL must not skip, and the accessor must hand "
            f"back exactly that value:\n{out}"
        )


class TestDoesNotSkipWhenPostgresIsNamed:
    """A named PostgreSQL target runs the node, even if the server is unreachable.

    This is the half of the contract that keeps skips honest. If availability were
    probed by connecting, a server that is down would silently convert every
    PostgreSQL-dependent node into a skip and a green run would mean nothing.
    """

    BROKEN_URL = "postgresql://nobody@127.0.0.1:59999/alethra_test_unreachable_probe"

    def test_named_but_unreachable_target_still_runs_the_node(self, tmp_path):
        module = _probe_module(
            tmp_path,
            """
            def test_named_target_runs():
                requires_postgresql("holds a real row lock")
                pytest.fail("RAN_PAST_THE_GATE")
            """,
        )
        result = _run_probe(tmp_path, module, env_overrides={PG_URL_ENV: self.BROKEN_URL})
        out = result.stdout + result.stderr

        assert "RAN_PAST_THE_GATE" in out, (
            "an unreachable-but-named PostgreSQL target was treated as absent; "
            f"nodes would skip instead of reporting the real connection failure:\n{out}"
        )
        assert "1 failed" in out and "skipped" not in out, out

    def test_sqlite_url_under_the_postgresql_name_still_skips(self, tmp_path):
        """The predicate reads the driver, not merely whether something is set."""
        module = _probe_module(
            tmp_path,
            """
            def test_sqlite_named():
                assert not postgresql_available()
                assert not is_postgresql_url(os.environ["ALETHRA_PG_TEST_URL"])
                requires_postgresql("needs real row locks")
                raise AssertionError("ran past the gate")
            """,
        )
        result = _run_probe(
            tmp_path,
            module,
            env_overrides={PG_URL_ENV: "sqlite:////tmp/alethra-sqlite-test-dir/probe.db"},
        )
        out = result.stdout + result.stderr

        assert "1 skipped" in out, (
            "a SQLite file named under ALETHRA_PG_TEST_URL must not be treated as "
            f"a PostgreSQL target:\n{out}"
        )
        assert "ran past the gate" not in out

    def test_malformed_url_is_not_treated_as_postgresql(self, tmp_path):
        module = _probe_module(
            tmp_path,
            """
            def test_garbage_named():
                assert not postgresql_available()
                requires_postgresql("needs a real server")
                raise AssertionError("ran past the gate")
            """,
        )
        result = _run_probe(tmp_path, module, env_overrides={PG_URL_ENV: "not-a-url-at-all"})
        out = result.stdout + result.stderr

        assert "1 skipped" in out, out
        assert "ran past the gate" not in out


class TestModuleTargetGuardStillRefuses:
    """Node granularity did not soften the per-module safety guard.

    ``tests/test_ownership_integration.py`` validates its target at import time and
    used to skip the whole module on the SQLite lane. These nodes prove the
    refusals around that skip are intact, from a plain import with no pytest, no
    conftest and no connection involved -- so a bad name cannot be reached, let
    alone written to.
    """

    MODULE = "tests.test_ownership_integration"

    @pytest.mark.parametrize(
        "db_url, expected",
        [
            pytest.param(
                "mysql://u@localhost:3306/alethra_test",
                "require PostgreSQL",
                id="wrong-driver",
            ),
            pytest.param(
                "postgresql://u@db.internal.example.com:5432/alethra_test",
                "require localhost",
                id="remote-host",
            ),
            pytest.param(
                "postgresql://u@localhost:5432/reporting",
                "must contain 'test'",
                id="name-without-test",
            ),
            pytest.param(
                "postgresql://u@localhost:5432/ai_investment_lab",
                "must contain 'test', got 'ai_investment_lab'",
                id="production-name",
            ),
            pytest.param(
                "postgresql://u@localhost:5432/alethra_production",
                "must contain 'test', got 'alethra_production'",
                id="production-name-2",
            ),
        ],
    )
    def test_hostile_target_refuses_to_import(self, db_url, expected):
        """Every hostile target is refused, whichever check happens to fire.

        The production-name assertion names the check that actually answers, not
        the one the file advertises: all three names in that branch fail the
        "must contain 'test'" test two checks earlier, so the
        "Refusing to use production database" branch is unreachable for exactly
        the databases it exists to protect. Recorded rather than reordered here --
        reordering a safety check is the owner's call, and the refusal is intact
        either way.
        """
        result = _run(
            [sys.executable, "-c", f"import {self.MODULE}"],
            env_overrides={TEST_DB_ENV: db_url, PG_URL_ENV: db_url},
        )
        out = result.stdout + result.stderr

        assert result.returncode != 0, (
            f"{db_url!r} must refuse the import, it succeeded:\n{out}"
        )
        assert "SAFETY" in out, out
        assert expected in out, f"expected {expected!r} in:\n{out}"
        assert "RuntimeError" in out, out

    def test_missing_target_refuses_rather_than_skipping(self):
        """Forgetting to name a database stays loud.

        Without ALETHRA_TEST_DB the shared conftest falls back to a SQLite file, so
        a silent skip is what a misconfigured run would otherwise look like. This
        module raises instead.
        """
        result = _run(
            [sys.executable, "-c", f"import {self.MODULE}"],
            unset=(TEST_DB_ENV, PG_URL_ENV),
        )
        out = result.stdout + result.stderr

        assert result.returncode != 0, out
        assert "environment variable is required" in out, out

    def test_disposable_postgresql_target_imports_without_connecting(self):
        """The positive control for the refusals above.

        A guard that always raised would satisfy every node in this class while
        taking the offline coverage with it. A disposable-looking PostgreSQL name
        has to pass the guard -- and passing it must not open a connection, because
        no server is running and this node has to be able to say that.
        """
        url = "postgresql://alethra_test_runtime@localhost:5432/alethra_test_guard_probe"
        result = _run(
            [sys.executable, "-c", f"import {self.MODULE}"],
            env_overrides={TEST_DB_ENV: url, PG_URL_ENV: url},
        )
        out = result.stdout + result.stderr

        assert result.returncode == 0, out
        for leak in ("could not connect", "Connection refused", "OperationalError"):
            assert leak not in out, f"import reached the network: {out}"

    def test_sqlite_lane_still_collects_every_node_of_the_module(self):
        """The offline nodes only exist if the module is collectable.

        A module-level skip, or an import-time raise on the SQLite target, would
        take them away and the lane would report fewer nodes rather than a failure.

        The expectation is read from the module's own syntax tree instead of a
        pinned total. A hard-coded "N tests collected" has to be re-accepted every
        time someone adds a node, which is exactly how a ceiling stops meaning
        anything; what this node refuses is the drift that matters -- a test the
        file defines and the SQLite lane cannot see.
        """
        source = BACKEND_DIR / "tests" / "test_ownership_integration.py"
        expected = [f"tests/test_ownership_integration.py::{node_id}"
                    for node_id in _defined_node_ids(source)]
        assert len(expected) >= 4, (
            f"the guard class documents offline coverage it no longer has: {expected}"
        )

        result = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_ownership_integration.py",
                "-p",
                "no:cacheprovider",
                "--collect-only",
                "-q",
            ],
        )
        out = result.stdout + result.stderr

        assert result.returncode == 0, out
        collected = {
            line.strip() for line in out.splitlines()
            if line.strip().startswith("tests/test_ownership_integration.py::")
        }
        missing = [node_id for node_id in expected
                   if not any(c == node_id or c.startswith(f"{node_id}[") for c in collected)]
        assert not missing, (
            f"{len(missing)} of the {len(expected)} nodes this module defines are not "
            f"collectable on the SQLite lane (module-level skip?): {missing}\n{out}"
        )


class TestSkipAttributionSurvivesTheWholeLane:
    """Every skip the lane reports has to identify its own node."""

    def test_every_skip_line_names_its_module_and_carries_the_verdict(self, tmp_path):
        db_file = tmp_path / "attribution-check.db"
        result = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_target_guard.py",
                "-p",
                "no:cacheprovider",
                "-rs",
                "-q",
            ],
            env_overrides={TEST_DB_ENV: f"sqlite:///{db_file}"},
            unset=(PG_URL_ENV,),
        )
        out = result.stdout + result.stderr
        skip_lines = [ln for ln in out.splitlines() if ln.startswith("SKIPPED [1]")]

        assert skip_lines, f"no skips were reported:\n{out[-3000:]}"
        for line in skip_lines:
            assert any(marker in line for marker in MARKERS), line
            assert "NOT RUN, not passed" in line, line
