"""Process state written at module import leaks for the rest of the pytest session.

Measured on 2026-11-16, in this lane, before the fix:

* ``test_explicit_provider_selection.py`` assigned ``app.dependency_overrides[get_db]``
  and built its in-memory engine at module import. It sorts first in collection
  order, so every later ``TestClient(app)`` resolved ``get_db`` against that
  module's ``:memory:`` database instead of the lane database. Running only
  ``[that file, tests/test_ml_pipeline.py]`` produced **18 failed, 41 passed**;
  the same 54 ml_pipeline items run alone produced **54 passed**.
* ``test_provider_isolation.py`` put seven empty ``sklearn.*`` modules into
  ``sys.modules`` at import ("mock sklearn to avoid import errors", although
  scikit-learn 1.9.1 is installed). Later modules then failed with
  ``ImportError: cannot import name 'StandardScaler' from 'sklearn.preprocessing'
  (unknown location)`` - the ``(unknown location)`` is the stub's fingerprint.
  Adding that file to the prefix made it **19 failed**; it also explains
  ``tests/test_journey_b_model_training.py:343``
  (``LogisticRegression ... (unknown location)``).

Neither file could be blamed from a single-file run, and neither failure was a
product defect: they were one process sharing two global mutable objects.

These tests pin the three properties that make the class detectable rather than
re-discoverable: the victim set passes in polluter-first order, passes in the
reverse order, and the teardown restores the override dict even when the body
raises. The AST gate then refuses the *pattern* anywhere in the tree, because a
leak found by bisecting 114 files costs far more than a rule that cannot be
broken quietly.
"""

import ast
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPT = BACKEND / "scripts" / "run_tests.sh"

POLLUTER_OVERRIDES = "test_explicit_provider_selection.py"
POLLUTER_SKLEARN = "test_provider_isolation.py"

# The victim nodes are selected by class, not by whole file: the 18 override
# victims all live in these three classes, and the sklearn victim is one node.
VICTIM_ENDPOINTS = "tests/test_ml_pipeline.py::TestMLAPIEndpoints"
VICTIM_OWNERSHIP = "tests/test_ml_pipeline.py::TestModelOwnership"
VICTIM_USAGE = "tests/test_ml_pipeline.py::TestInferenceUsageTracking"
VICTIM_SCALER = (
    "tests/test_ml_pipeline.py::TestModelTraining::test_scaler_fit_only_on_training_data"
)
VICTIM_JOURNEY_B = (
    "tests/test_journey_b_model_training.py::TestJourneyBTrainableModelAndAPI"
    "::test_trained_model_deletion"
)


def lane_env(tmp_path):
    env = dict(os.environ)
    # A parent pytest leaks these into the child, and the child then reports on
    # the parent's session instead of its own.
    for name in [k for k in env if k.startswith(("PYTEST_", "PYTHONHOME"))] + ["PYTEST_ADDOPTS"]:
        env.pop(name, None)
    for name in ("ALETHRA_PG_TEST_URL", "ALETHRA_SQLITE_DB", "ALETHRA_TEST_DB", "DATABASE_URL"):
        env.pop(name, None)
    env["TMPDIR"] = str(tmp_path)
    env["DATA_PROVIDER"] = "demo"
    return env


def run_lane(args, tmp_path):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900, env=lane_env(tmp_path),
    )


def bad_nodes(result):
    return [
        line for line in result.stdout.splitlines()
        if line.startswith(("FAILED ", "ERROR "))
    ]


def assert_lane_clean(result, label):
    # exit 0 alone is not the property: the selection could have matched nothing
    # and the lane would still be green for a caller that ignores its refusals.
    bad = bad_nodes(result)
    assert not bad, f"{label} produced failures:\n" + "\n".join(bad)
    assert result.returncode == 0, f"{label} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"


def test_polluter_first_order_leaves_the_api_victims_passing(tmp_path):
    """The 18 override victims, run immediately after the override polluter.

    This is the minimal prefix that reproduced them: one file, no conftest
    changes and no ordering tricks.
    """
    result = run_lane(
        ["sqlite", "-q", "-p", "no:warnings", "--tb=line",
         POLLUTER_OVERRIDES, VICTIM_ENDPOINTS, VICTIM_OWNERSHIP, VICTIM_USAGE],
        tmp_path,
    )
    assert_lane_clean(result, "provider-selection module before the ml API victims")


def test_sklearn_users_import_the_installed_package_after_the_stub_module(tmp_path):
    """Both sklearn victims, run immediately after the module that stubbed it."""
    result = run_lane(
        ["sqlite", "-q", "-p", "no:warnings", "--tb=line",
         POLLUTER_SKLEARN, VICTIM_SCALER, VICTIM_JOURNEY_B],
        tmp_path,
    )
    assert_lane_clean(result, "provider-isolation module before the sklearn victims")
    assert "unknown location" not in result.stdout, result.stdout


def test_reverse_order_also_passes(tmp_path):
    """Victim first, then polluter: the order that hides a leak instead of showing it."""
    result = run_lane(
        ["sqlite", "-q", "-p", "no:warnings", "--tb=line",
         VICTIM_ENDPOINTS, VICTIM_OWNERSHIP, VICTIM_USAGE, VICTIM_SCALER,
         POLLUTER_OVERRIDES, POLLUTER_SKLEARN],
        tmp_path,
    )
    assert_lane_clean(result, "victims before polluters")


def test_the_stack_restores_the_override_dict_even_when_the_body_raises():
    """Teardown in ``finally``, restoring the prior dict rather than clearing it.

    ``clear()`` alone would destroy overrides another module installed
    legitimately; a teardown after ``yield`` would not run at all when the test
    fails, which is exactly when the suite is being read.
    """
    import test_explicit_provider_selection as provider_module
    from app.database import get_db
    from app.main import app

    before = dict(app.dependency_overrides)
    with pytest.raises(RuntimeError, match="body failed"):
        with provider_module._provider_selection_stack():
            assert get_db in app.dependency_overrides, "the stack must install its override"
            raise RuntimeError("body failed")
    assert app.dependency_overrides == before, (
        "a failing consumer must not leave the override behind: "
        f"{app.dependency_overrides} != {before}"
    )


def test_no_test_module_writes_shared_state_at_import():
    """Module-level ``app.dependency_overrides[...] =``/``sys.modules[...] =``.

    An assignment at module scope executes during *collection*, before any test
    in the file runs and regardless of whether that file's tests pass, so it is
    not a fixture and no teardown can reach it. This gate refuses the pattern;
    the two files above are the reason it exists.
    """
    offenders = [
        f"{path.relative_to(BACKEND)} line {node.lineno}: {detail}"
        for path, node, detail in _module_level_shared_state_writes()
    ]
    assert not offenders, "test modules leaking process state at import:\n" + "\n".join(offenders)


def _module_level_shared_state_writes():
    """Every module-level statement that mutates a process-global container."""
    for module_path in sorted(BACKEND.rglob("test_*.py")):
        if "__pycache__" in module_path.parts or ".venv" in module_path.parts:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            for target in targets:
                if not isinstance(target, ast.Subscript):
                    continue
                base = target.value
                if not isinstance(base, ast.Attribute):
                    continue
                # app.dependency_overrides[...] , sys.modules[...] , os.environ[...]
                if base.attr in ("dependency_overrides", "modules", "environ"):
                    root = base.value
                    name = root.id if isinstance(root, ast.Name) else getattr(root, "attr", "?")
                    yield module_path, node, f"module-level assignment to {name}.{base.attr}"


def _qualified(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_no_test_module_leaves_a_directory_on_sys_path():
    """``sys.path.insert(...)`` at module level has to be undone in the same scope.

    Three files import from ``backend/scripts``, which is not a package, so they
    need the directory on the path — but only while the import runs. Left there,
    every later plain ``import X`` in the session resolves against ``scripts/``
    first, and the shadowing shows up in whichever file happens to sort next.
    """
    offenders = []
    for module_path in sorted(BACKEND.rglob("test_*.py")):
        if "__pycache__" in module_path.parts or ".venv" in module_path.parts:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
        mutated = []
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ("insert", "append", "remove")
            ):
                container = node.value.func.value
                if isinstance(container, ast.Attribute) and container.attr == "path" \
                        and _qualified(container.value).startswith("sys"):
                    mutated.append((node.value.func.attr, node.lineno))
        inserts = [line for op, line in mutated if op in ("insert", "append")]
        removes = [line for op, line in mutated if op == "remove"]
        if len(inserts) > len(removes):
            offenders.append(
                f"{module_path.relative_to(BACKEND)}: {len(inserts)} module-level "
                f"sys.path insert/append on lines {inserts} against {len(removes)} "
                "removes — the directory outlives the import"
            )
    assert not offenders, (
        "test modules changing sys.path for the whole session:\n" + "\n".join(offenders)
    )


DOTENV_REACHABLE = (
    "data_provider",
    "live_trading_enabled",
    "enable_learning_scheduler",
    "auto_collection_enabled",
)


def _pinned_dotenv_fields():
    """Fields this process cannot use to measure a dotenv because env already sets them.

    pydantic-settings is case-insensitive and an environment variable outranks a
    dotenv value, so an exported ``DATA_PROVIDER=demo`` would make "the dotenv was
    not read" indistinguishable from "the dotenv was overridden".
    """
    names = {key.upper() for key in os.environ}
    return [field for field in DOTENV_REACHABLE if field.upper() in names]


def _bare_settings_constructions():
    """Every ``Settings(...)`` in a test module that names no dotenv source."""
    offenders = []
    for module_path in sorted(BACKEND.rglob("test_*.py")):
        if "__pycache__" in module_path.parts or ".venv" in module_path.parts:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if named != "Settings":
                continue
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            if "_env_file" in kwargs:
                continue
            offenders.append(
                f"{module_path.relative_to(BACKEND)}:{node.lineno}: Settings(...) "
                "without _env_file=None"
            )
    return offenders


def test_the_environment_the_suite_measures_is_the_environment_it_documents(
    tmp_path, monkeypatch
):
    """A Settings built in a test must mean what the test says it means.

    ``Settings.model_config`` names ``.env`` (app/config.py:560), a *relative*
    path the lane's cwd resolves to ``backend/.env``. That was measured twice
    over on 2026-11-17: with ``CANDIDATE_PASSWORDLESS_MODE=TRUE`` in that
    gitignored file, two ``tests/test_trial_passwordless.py`` cases failed
    because the *candidate* validator — which runs first — answered for the trial
    gate, and ``tests/test_candidate_passwordless.py``'s "disabled by default"
    POST returned 403 (non-loopback peer) instead of the 404 (entry closed) it
    asserted, while its sibling GET passed vacuously on the same address check.

    This repository has two answers to that, and the second one postdates this
    node: a call site can pass ``_env_file=None`` (tests/test_config.py:14), or
    ``tests/conftest.py:98-101`` can remove the source for the whole process.
    Once the source is gone, a bare ``Settings(...)`` is not an ambient read --
    and ``tests/test_conftest_sqlite_default.py:175`` has to write one bare,
    because a construction that names ``_env_file=None`` cannot prove the
    implicit source is dead. Refusing the *spelling* therefore stopped measuring
    the environment and started refusing a true statement, which is a gate whose
    own green cannot be read as one.

    So the check is the behaviour, measured with a sentinel file, and it now
    fails in a case the old textual rule passed: if the neutralisation is
    deleted, this node is red whether or not any test module constructs
    ``Settings`` bare. The call-site census is kept and printed in that failure,
    because a leak found by bisecting the tree is worth more than a rule that
    only says "the source came back".
    """
    from app.config import Settings

    census = _bare_settings_constructions()
    pinned = _pinned_dotenv_fields()
    measurable = [field for field in DOTENV_REACHABLE if field not in pinned]
    assert measurable, (
        "every dotenv field this node measures is already pinned by an exported "
        f"environment variable ({', '.join(pinned)}), so 'the dotenv was not read' "
        "cannot be told from 'the dotenv was overridden' — unset them for this run"
    )

    (tmp_path / ".env").write_text(
        "DATA_PROVIDER=twelvedata\n"
        "LIVE_TRADING_ENABLED=true\n"
        "ENABLE_LEARNING_SCHEDULER=true\n"
        "AUTO_COLLECTION_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    baseline = Settings(_env_file=None)
    named = Settings(_env_file=str(tmp_path / ".env"))
    inert = [
        f"{field}: {getattr(baseline, field)!r} from code, "
        f"{getattr(named, field)!r} from the sentinel"
        for field in measurable
        if getattr(named, field) == getattr(baseline, field)
    ]
    assert not inert, (
        "the sentinel dotenv did not reach these fields, so the ambient check "
        f"below would pass whether or not the lane reads a dotenv: {'; '.join(inert)}"
    )

    ambient = Settings()
    drifted = [
        f"{field}: {getattr(baseline, field)!r} wanted, {getattr(ambient, field)!r} read"
        for field in measurable
        if getattr(ambient, field) != getattr(baseline, field)
    ]
    assert not drifted, (
        "a bare Settings(...) reads the developer's backend/.env again — every "
        "test module is measuring this machine instead of the product "
        f"(conftest.py's dotenv neutralisation is off): {'; '.join(drifted)}\n"
        "bare Settings(...) constructions in the tree (this node's own probe is "
        "one of them, so the list is a census of spellings, not of offenders):\n"
        + ("\n".join(census) if census else "  (none)")
    )
