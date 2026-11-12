"""``test_quota_concurrency.py`` must never fall back to an implicit database.

The module used to be the only test that wiped a PostgreSQL schema: its
``pg_engine`` fixture teardown called ``Base.metadata.drop_all(engine)``, and it
resolved its target with ``os.environ.get("ALETHRA_TEST_POSTGRES_URL",
"<literal URL>")``, so running it *without* the variable did not skip — it
connected to whichever database the literal named and left the DROP armed. It
now issues no DDL of any kind (``test_quota_concurrency_safety_guard.py`` holds
that line), so naming a target is a precondition for connecting at all rather
than for reaching a schema-wide statement. What this file guards is the half of
that contract that has to survive any later rewrite: an unset variable produces
an explicit module-level skip, never a guessed host.

Nothing here connects to a database: the behavioural check imports only the
guard branch, which raises ``Skipped`` before it can build an engine, and the
rest read the module as text and as an AST so they hold under the SQLite layer.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
TARGET = BACKEND / "tests" / "test_quota_concurrency.py"
RUNNER = BACKEND / "scripts" / "run_tests.sh"
VARIABLE = "ALETHRA_TEST_POSTGRES_URL"


def _postgres_url_assignment(source: str) -> ast.Call:
    """Return the ``os.environ.get(...)`` call that binds POSTGRES_URL."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "POSTGRES_URL" for t in node.targets
        ):
            assert isinstance(node.value, ast.Call), "POSTGRES_URL must be bound by a call"
            return node.value
    raise AssertionError("POSTGRES_URL is no longer bound from the environment")


def _module_level_skip_guard(source: str) -> ast.Call:
    """Return the ``pytest.skip(...)`` call guarded on ``if not POSTGRES_URL``."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == "POSTGRES_URL"
        ):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "skip"
            ):
                return child
    raise AssertionError("no `if not POSTGRES_URL: pytest.skip(...)` guard at module level")


def _string_constants(source: str) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_postgres_url_is_read_without_a_fallback_value():
    call = _postgres_url_assignment(TARGET.read_text(encoding="utf-8"))
    assert len(call.args) == 1, (
        f"POSTGRES_URL was given a fallback argument; an unset {VARIABLE} must "
        f"skip, not connect to a literal target"
    )
    assert isinstance(call.args[0], ast.Constant)
    assert call.args[0].value == VARIABLE


def test_skip_guard_is_module_level_and_names_the_missing_variable():
    skip_call = _module_level_skip_guard(TARGET.read_text(encoding="utf-8"))
    reasons = [a.value for a in skip_call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    assert reasons, "the skip carries no reason text"
    assert VARIABLE in reasons[0]
    keywords = {k.arg: k.value for k in skip_call.keywords}
    allow = keywords.get("allow_module_level")
    assert isinstance(allow, ast.Constant) and allow.value is True, (
        "skipping a whole module needs allow_module_level=True, otherwise collection errors"
    )


def test_module_carries_no_embedded_connection_string():
    offenders = [
        value
        for value in _string_constants(TARGET.read_text(encoding="utf-8"))
        if "://" in value or ":5434" in value
    ]
    assert offenders == [], f"connection literals still embedded in the module: {offenders}"


def test_official_pg_layer_still_exports_the_variable_before_pytest_runs():
    """Removing the default must not silently disable the intended PG run."""
    script = RUNNER.read_text(encoding="utf-8")
    exported = script.index(f"export {VARIABLE}=")
    invoked = script.index('-m pytest')
    assert exported < invoked, "the pg layer no longer supplies the variable before pytest starts"


def test_the_old_fallback_would_be_caught_not_tolerated():
    """Negative control: put the removed default back and prove the guard fires."""
    mutated = textwrap.dedent(
        f"""
        POSTGRES_URL = os.environ.get(
            "{VARIABLE}",
            "postgresql://user:pw@localhost:5434/alethra_test"
        )
        """
    )
    call = _postgres_url_assignment(mutated)
    assert len(call.args) == 2, "the argument-count check must flag a supplied fallback"
    assert ":5434" in call.args[1].value
    with pytest.raises(AssertionError):
        _module_level_skip_guard(mutated)


def test_importing_without_the_variable_skips_instead_of_connecting(tmp_path):
    """Behaviour, not text: the guard branch must skip before any engine exists."""
    lines = TARGET.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("POSTGRES_URL = "))
    end = next(i for i, line in enumerate(lines) if line.startswith("OPT_IN = "))
    assert "pytest.skip" in "\n".join(lines[start:end]), "the slice no longer holds the guard"
    (tmp_path / "guard_branch.py").write_text(
        "import os\nimport pytest\n\n" + "\n".join(lines[start:end]) + "\n",
        encoding="utf-8",
    )

    env = {key: value for key, value in os.environ.items() if key != VARIABLE}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "guard_branch.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 5, f"expected a module skip with no tests run:\n{output}"
    assert VARIABLE in output, f"the skip reason does not name the variable:\n{output}"
    assert "Traceback" not in output, f"the import raised instead of skipping:\n{output}"
