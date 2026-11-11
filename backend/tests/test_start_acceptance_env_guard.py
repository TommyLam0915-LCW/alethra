"""``start_acceptance_env.sh`` must validate every input before its first side effect.

The script used to start a PostgreSQL container on line 8 and only then require
``POSTGRES_PASSWORD`` on line 22, so a missing password still left a running
container behind, and it carried a hardcoded default password and a hardcoded
acceptance JWT signing key. These tests pin the replacement contract: a missing
or empty required variable exits non-zero with the *stub* log still empty, which
means ``docker`` was never reached — and one test proves the stub log would have
recorded the call had it happened.

Nothing here touches Docker Desktop, the daemon, or an image: either the script
refuses during its input checks, or it stops at a stub ``docker`` that records
argv and fails. The real CLI is unreachable because ``PATH`` in these tests
holds only the stub directory plus ``/usr/bin`` and ``/bin``.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "start_acceptance_env.sh"

#: Values that must never reach stdout, stderr, or a recorded argv.
PASSWORD = "SENTINEL-ACCEPTANCE-PASSWORD-DO-NOT-ECHO"
JWT = "SENTINEL-JWT-KEY-DO-NOT-ECHO-0123456789abcdef"  # 44 chars, over the 32 minimum

_STUB = """#!/bin/sh
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$ALETHRA_STUB_LOG"
exit "${ALETHRA_STUB_EXIT:-0}"
"""


def _sandbox(tmp_path: Path, *, omit: tuple[str, ...] = (), docker_exit: str = "1") -> dict:
    """A repo-shaped directory whose only externally visible tools are stubs."""
    root = tmp_path / "repo"
    (root / "backend" / "venv" / "bin").mkdir(parents=True)
    (root / "backend" / "venv" / "bin" / "activate").write_text("# stub activate\n")
    (root / "frontend").mkdir()
    (root / "start_acceptance_env.sh").write_text(SCRIPT.read_text(encoding="utf-8"))

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in ("docker", "sleep", "python", "npm", "nohup"):
        if name in omit:
            continue
        path = stubs / name
        path.write_text(_STUB)
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log = tmp_path / "stub.log"
    return {"root": root, "log": log, "docker_exit": docker_exit,
            "base_path": f"{stubs}:/usr/bin:/bin"}


def _run(box: dict, **vars_: str | None) -> subprocess.CompletedProcess:
    """Run the script with exactly the named variables — no ambient credentials."""
    env = {
        "PATH": box["base_path"],
        "ALETHRA_STUB_LOG": str(box["log"]),
        "ALETHRA_STUB_EXIT": box["docker_exit"],
        "HOME": str(box["root"].parent),
    }
    for name, value in vars_.items():
        if value is not None:
            env[name] = value
    return subprocess.run(
        ["bash", "start_acceptance_env.sh"],
        cwd=box["root"], env=env, capture_output=True, text=True, timeout=60,
    )


def _calls(box: dict) -> list[str]:
    if not box["log"].exists():
        return []
    return [line for line in box["log"].read_text().splitlines() if line.strip()]


def test_missing_password_refuses_with_no_side_effect(tmp_path):
    box = _sandbox(tmp_path)
    result = _run(box, JWT_SECRET_KEY=JWT)
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr
    assert _calls(box) == [], f"side effects happened before the input check: {_calls(box)}"


def test_empty_password_is_treated_as_missing(tmp_path):
    box = _sandbox(tmp_path)
    result = _run(box, POSTGRES_PASSWORD="", JWT_SECRET_KEY=JWT)
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr
    assert _calls(box) == []


def test_missing_jwt_key_refuses_before_docker(tmp_path):
    box = _sandbox(tmp_path)
    result = _run(box, POSTGRES_PASSWORD=PASSWORD)
    assert result.returncode != 0
    assert "JWT_SECRET_KEY" in result.stderr
    assert _calls(box) == []


def test_short_jwt_key_refuses_and_does_not_echo_its_length_value(tmp_path):
    box = _sandbox(tmp_path)
    short = "too-short"
    result = _run(box, POSTGRES_PASSWORD=PASSWORD, JWT_SECRET_KEY=short)
    assert result.returncode != 0
    assert "at least 32 characters" in result.stderr
    assert "9" in result.stderr  # the length is reported, which is the point
    assert short not in result.stderr + result.stdout
    assert _calls(box) == []


def test_absent_docker_cli_refuses_without_starting_anything(tmp_path):
    box = _sandbox(tmp_path, omit=("docker",))
    result = _run(box, POSTGRES_PASSWORD=PASSWORD, JWT_SECRET_KEY=JWT)
    assert result.returncode != 0
    assert "docker is not on PATH" in result.stderr
    assert _calls(box) == []


def test_valid_inputs_do_reach_docker_and_never_put_the_password_in_argv(tmp_path):
    """Positive control: the stub log is only meaningful because it records here."""
    box = _sandbox(tmp_path, docker_exit="1")  # a failing `docker run` stops the script
    result = _run(box, POSTGRES_PASSWORD=PASSWORD, JWT_SECRET_KEY=JWT)
    calls = _calls(box)

    assert result.returncode != 0  # set -e must stop at the failed container start
    assert calls and calls[0].startswith("docker run"), calls
    assert "-e POSTGRES_PASSWORD" in calls[0], "password must be passed by name, not value"
    assert PASSWORD not in " ".join(calls), "the password reached a recorded argv"
    assert PASSWORD not in result.stdout + result.stderr
    assert calls == [calls[0]], f"a later stage ran after docker failed: {calls}"


def test_no_hardcoded_default_credentials_remain_in_the_script():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=postgres" not in text
    assert 'JWT_SECRET_KEY="' not in text
    assert "postgres:postgres@" not in text
    assert "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be exported" not in text, (
        "the guard moved back after `docker run` instead of before it"
    )


def test_every_input_check_precedes_the_first_side_effect():
    """Ordering, not just presence: the checks must sit above `docker run`."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    docker_run = next(i for i, line in enumerate(lines) if "docker run" in line)
    for needle in ("require_var POSTGRES_PASSWORD", "require_var JWT_SECRET_KEY",
                   "at least 32 characters", "command -v docker"):
        index = next((i for i, line in enumerate(lines) if needle in line), None)
        assert index is not None, f"missing check: {needle}"
        assert index < docker_run, f"{needle} runs after `docker run` (line {docker_run + 1})"


@pytest.mark.parametrize("missing", ["POSTGRES_PASSWORD", "JWT_SECRET_KEY"])
def test_refusal_message_names_the_variable_and_denies_side_effects(tmp_path, missing):
    box = _sandbox(tmp_path)
    kwargs = {"POSTGRES_PASSWORD": PASSWORD, "JWT_SECRET_KEY": JWT}
    kwargs[missing] = None
    result = _run(box, **kwargs)
    assert result.returncode != 0
    assert missing in result.stderr
    assert "No container, migration, build, or server was started" in result.stderr
