"""The legacy journey scripts must refuse before their first side effect.

Four scripts carried a hardcoded ``postgresql://test:test@localhost:5434/...``
target and the acceptance signing key next to it. Port 5434 has no listener and
the PG gate only permits 127.0.0.1:5432, so running them with no environment did
not fail — it fired HTTP calls, killed and restarted backends, and printed
"OPERATIONAL" about a database it never touched. They now take their target as a
required input through ``scripts/require_acceptance_inputs.sh``.

Every run here happens in a sandbox whose PATH holds only recording stubs plus
``/usr/bin`` and ``/bin``, whose BACKEND_DIR points inside the sandbox, and whose
log redirections are rewritten into the sandbox: nothing reaches the network, no
process is signalled, and the retired default is never contacted. The stub log is
the side-effect witness — a refusal must leave it empty, which means no ``curl``,
no ``nohup``, no ``kill``.

The stubs never record a value they were handed, only whether it matched what the
test expected, so this suite cannot itself become a place a credential is stored.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "scripts" / "require_acceptance_inputs.sh"

SCRIPTS = (
    "execute_journey_b_worker.sh",
    "verify_data_persistence.sh",
    "verify_quota_persistence.sh",
    "verify_milestone3_external.sh",
)

#: Values that must never appear in stdout, stderr or a recorded argv.
DB_PASSWORD = "SENTINEL-DB-PASSWORD-DO-NOT-ECHO"
JWT_SECRET = "SENTINEL-JWT-SECRET-0123456789abcdef-32-chars"
VALID_URL = f"postgresql://alethra_runner:{DB_PASSWORD}@127.0.0.1:5432/alethra_owner_test"

#: Strings left over from the retired stack. Only executable lines are searched:
#: the scripts keep prose comments naming the port they no longer use, and those
#: comments are the reference inventory the directive asked to keep.
RETIRED_TOKENS = (":5434", ":5433", "test:test@", "alethra-e2e-db", "docker exec")

#: Inputs that must be refused even though the script would otherwise run.
MALFORMED = (
    ("no credentials", "postgresql://127.0.0.1:5432/alethra_owner_test"),
    ("wrong scheme", "mysql://u:p@127.0.0.1:5432/alethra_owner_test"),
    ("port not a number", "postgresql://u:p@127.0.0.1:notp/alethra_owner_test"),
    ("port out of range", "postgresql://u:p@127.0.0.1:0/alethra_owner_test"),
    ("no database", "postgresql://u:p@127.0.0.1:5432"),
    ("empty database", "postgresql://u:p@127.0.0.1:5432/"),
    ("unencoded @ in password", "postgresql://u:p@@127.0.0.1:5432/db"),
    ("empty host", "postgresql://u:p@:5432/db"),
    # Same URL shape as VALID_URL but rejected, and carrying the sentinel password:
    # this is what proves a refusal message does not echo the value back.
    ("port above range with sentinel password", f"postgresql://u:{DB_PASSWORD}@127.0.0.1:99999/alethra_owner_test"),
)

_STUB = """#!/bin/sh
name="$(basename "$0")"
printf '%s %s\\n' "$name" "$*" >> "$ALETHRA_STUB_LOG"
if [ -n "${ALETHRA_STUB_ENV_LOG:-}" ]; then
  db=missing
  if [ -n "${DATABASE_URL:-}" ]; then
    if [ "$DATABASE_URL" = "${ALETHRA_EXPECT_DB:-}" ]; then db=match; else db=different; fi
  fi
  jwt=missing
  if [ -n "${JWT_SECRET_KEY:-}" ]; then
    if [ "$JWT_SECRET_KEY" = "${ALETHRA_EXPECT_JWT:-}" ]; then jwt=match; else jwt=different; fi
  fi
  printf '%s|db=%s|jwt=%s\\n' "$name" "$db" "$jwt" >> "$ALETHRA_STUB_ENV_LOG"
fi
case "$name" in
  pgrep)
    if [ -n "${ALETHRA_PGREP_PID:-}" ]; then printf '%s\\n' "$ALETHRA_PGREP_PID"; fi
    exit 0 ;;
  lsof)
    printf 'COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\\n'
    printf 'uvicorn 4242 root  123u  IPv4 12345678      0t0  TCP *:8001 (LISTEN)\\n'
    exit 0 ;;
esac
exit "${ALETHRA_STUB_EXIT:-0}"
"""


def _executable_text(path: Path) -> str:
    """Script text with whole-line comments removed."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def _sandbox(base: Path) -> dict:
    """A repo-shaped directory whose only externally visible tools are stubs."""
    root = base / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "backend" / "venv" / "bin").mkdir(parents=True)
    (root / "backend" / "venv" / "bin" / "activate").write_text("# stub activate\n", encoding="utf-8")
    (root / "scratch").mkdir()

    helper = root / "scripts" / "require_acceptance_inputs.sh"
    helper.write_text(HELPER.read_text(encoding="utf-8"), encoding="utf-8")
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)

    for name in SCRIPTS:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        # The sandbox must own every absolute path the script can reach.
        text = text.replace(
            'BACKEND_DIR="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend"',
            f'BACKEND_DIR="{root}/backend"',
        ).replace(
            'WORKTREE="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto"',
            f'WORKTREE="{root}"',
        ).replace("/tmp/alethra-", f"{root}/scratch/alethra-")
        (root / name).write_text(text, encoding="utf-8")

    stubs = base / "stubs"
    stubs.mkdir()
    for name in ("curl", "nohup", "pgrep", "sleep", "kill", "python", "python3", "lsof"):
        path = stubs / name
        path.write_text(_STUB, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return {
        "root": root,
        "log": base / "stub.log",
        "env_log": base / "stub_env.log",
        "path": f"{stubs}:/usr/bin:/bin",
    }


def _run(box: dict, script: str, *, db_url: str | None,
         jwt: str | None = JWT_SECRET, stub_exit: str = "0",
         pgrep_pid: str = "") -> subprocess.CompletedProcess:
    env = {
        "PATH": box["path"],
        "HOME": str(box["root"]),
        "ALETHRA_STUB_LOG": str(box["log"]),
        "ALETHRA_STUB_ENV_LOG": str(box["env_log"]),
        "ALETHRA_STUB_EXIT": stub_exit,
        "ALETHRA_PGREP_PID": pgrep_pid,
        "ALETHRA_EXPECT_DB": db_url if db_url is not None else "",
        "ALETHRA_EXPECT_JWT": jwt if jwt is not None else "",
    }
    if db_url is not None:
        env["DATABASE_URL"] = db_url
    if jwt is not None:
        env["JWT_SECRET_KEY"] = jwt
    # `kill` resolves to the bash builtin before PATH, so without disabling it the
    # real builtin would signal a fabricated PID instead of the recording stub.
    # Sourcing (not exec'ing) keeps that setting in force for the script body and
    # still lets its `exit` set the subprocess return code. The EXIT trap exists
    # because these scripts background their worker/backend with `nohup ... &`:
    # without `wait` that child can outlive the shell and its log line would be
    # missing from the witness, which is a race, not a pass.
    inner = f'enable -n kill\ntrap "wait" EXIT\n. "{box["root"] / script}"'
    return subprocess.run(
        ["/bin/bash", "-c", inner],
        capture_output=True, text=True, env=env, timeout=120,
    )


def _calls(box: dict) -> list[str]:
    if not box["log"].exists():
        return []
    return [line.split(" ", 1)[0] for line in box["log"].read_text(encoding="utf-8").splitlines()]


def _argv(box: dict) -> str:
    return box["log"].read_text(encoding="utf-8") if box["log"].exists() else ""


def _handoffs(box: dict) -> list[str]:
    if not box["env_log"].exists():
        return []
    return box["env_log"].read_text(encoding="utf-8").splitlines()


def _tail_copy(box: dict, script: str, marker: str) -> str:
    """Enter a script at `marker` instead of at its first line.

    Used where an earlier business check exits on the empty stubbed HTTP bodies
    before the restart path runs. The copy keeps `set -e` so a failing command
    inside the retained region still aborts exactly as it would in the script.
    """
    text = (box["root"] / script).read_text(encoding="utf-8")
    start = text.index(marker)
    tail = f"tail_{script}"
    (box["root"] / tail).write_text("set -e\n" + text[start:], encoding="utf-8")
    return tail


@pytest.fixture()
def box(tmp_path):
    return _sandbox(tmp_path)


def test_helper_exists_and_every_script_routes_through_it():
    assert HELPER.is_file()
    for name in SCRIPTS:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "require_acceptance_inputs.sh" in text, f"{name} does not source the shared checks"
        assert "require_db_target DATABASE_URL" in text, f"{name} does not require DATABASE_URL"
        assert 'DATABASE_URL="postgresql' not in text, f"{name} still assigns a credential literal"
        assert 'JWT_SECRET_KEY="acceptance-test-secret' not in text, f"{name} still ships the test key"


def test_retired_default_is_gone_from_the_executable_lines():
    offenders = {}
    for name in SCRIPTS:
        hits = [
            token
            for token in RETIRED_TOKENS
            if token in _executable_text(REPO_ROOT / name)
        ]
        if hits:
            offenders[name] = hits
    assert offenders == {}, f"a retired target is still reachable from code: {offenders}"


def test_comments_still_record_what_was_retired():
    """The scan above ignores comments on purpose, so this checks the prose stayed."""
    documented = [
        name for name in SCRIPTS
        if ":5434" in (REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    assert len(documented) >= 3, f"the retirement record has been edited away: {documented}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_missing_target_refuses_with_zero_side_effects(box, script):
    result = _run(box, script, db_url=None)
    assert result.returncode == 2, f"{script}: out={result.stdout!r} err={result.stderr!r}"
    assert _calls(box) == [], f"{script} side-effected before refusing: {_calls(box)}"
    assert "REFUSING" in result.stderr and "DATABASE_URL" in result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_empty_target_is_treated_as_missing(box, script):
    result = _run(box, script, db_url="")
    assert result.returncode == 2
    assert _calls(box) == []
    assert "not set, or set to an empty string" in result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("label,url", MALFORMED, ids=[label for label, _ in MALFORMED])
def test_malformed_targets_refuse_before_any_call(box, script, label, url):
    result = _run(box, script, db_url=url)
    assert result.returncode == 2, f"{script} accepted {label} ({url!r}): {result.stderr!r}"
    assert _calls(box) == [], f"{script} acted before refusing {label}: {_calls(box)}"
    assert "REFUSING" in result.stderr
    assert DB_PASSWORD not in result.stderr


def test_valid_target_reaches_the_worker_start_it_is_meant_for(box):
    """The required input is not only accepted — it is what the child process gets."""
    result = _run(box, "execute_journey_b_worker.sh", db_url=VALID_URL)
    # rc 0 is not expected here: every curl returns an empty stub body, so the
    # script's own business checks fail after the worker starts. What must not
    # happen is a refusal (rc 2) once the input is valid.
    assert result.returncode != 2, f"refused a valid target: err={result.stderr[-800:]!r}"
    assert "REFUSING" not in result.stderr, result.stderr[-400:]
    assert "nohup" in _calls(box), _calls(box)
    handoffs = _handoffs(box)
    assert "nohup|db=match|jwt=match" in handoffs, handoffs
    # Nothing that ran inherited a different target than the required input.
    assert all("|db=match" in line for line in handoffs), handoffs


@pytest.mark.parametrize("script,entry", [
    ("verify_data_persistence.sh", None),
    # Quota persistence exits at its step-7 quota-delta check before it can reach
    # its own restart, because every stubbed HTTP body is empty. Its restart path
    # is therefore entered at step 8 rather than dropped.
    ("verify_quota_persistence.sh", "# Step 8: Record backend PID and restart"),
], ids=["data-persistence-from-first-line", "quota-persistence-from-step-8"])
def test_restart_scripts_hand_the_same_target_to_the_backend(tmp_path, script, entry):
    one = _sandbox(tmp_path / script)
    if entry is None:
        target = script
    else:
        target = _tail_copy(one, script, entry)
        tail_text = (one["root"] / target).read_text(encoding="utf-8")
        assert "export DATABASE_URL" in tail_text, f"{script} tail lost the export it is meant to prove"
    # These scripts only restart a backend they can find, so pgrep must answer.
    result = _run(one, target, db_url=VALID_URL, pgrep_pid="4242")
    assert result.returncode != 2, f"{script} refused a valid target: {result.stderr[-800:]!r}"
    calls = _calls(one)
    assert calls, f"{script} made no call at all with a valid target"
    if entry is None:
        assert calls[0] == "curl", f"{script} side-effected before HTTP: {calls[:3]}"
    assert "nohup" in calls, f"{script} never restarted the backend: {calls}"
    handoffs = _handoffs(one)
    assert "nohup|db=match|jwt=match" in handoffs, f"{script}: {handoffs}"


def test_the_target_is_exported_before_the_child_is_started():
    """The scripts export rather than interpolate: the child inherits, argv stays clean."""
    for name in ("execute_journey_b_worker.sh", "verify_data_persistence.sh",
                 "verify_quota_persistence.sh"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        export_at = text.index("export DATABASE_URL")
        nohup_at = text.index("nohup ")
        assert export_at < nohup_at, f"{name} starts its child before exporting the target"
        between = text[export_at:nohup_at]
        assert "$DATABASE_URL" not in between, f"{name} expands the credential into a command: {between!r}"


def test_secrets_stay_out_of_stdout_stderr_and_argv(box):
    result = _run(box, "verify_milestone3_external.sh", db_url=VALID_URL)
    assert result.returncode == 0, f"rc={result.returncode} err={result.stderr[-800:]!r}"
    for secret in (DB_PASSWORD, JWT_SECRET):
        assert secret not in result.stdout, "the target description echoed a secret"
        assert secret not in result.stderr
        assert secret not in _argv(box), "a secret reached a stub argv"
        assert secret not in "\n".join(_handoffs(box)), "the witness log stored the secret itself"
    # No full URL anywhere either: only scheme/host/port/database are printable.
    assert "postgresql://" not in result.stdout
    assert "alethra_runner:" not in result.stdout + _argv(box)


def test_milestone3_reports_the_target_as_declared_not_operational(box):
    result = _run(box, "verify_milestone3_external.sh", db_url=VALID_URL)
    assert result.returncode == 0, f"rc={result.returncode} err={result.stderr[-800:]!r}"
    section = result.stdout.split("Service 3")[1].split("Service 4")[0]
    assert "NOT PROBED" in section, section
    assert "OPERATIONAL" not in section, section
    assert "127.0.0.1" in section and "5432" in section and "alethra_owner_test" in section


def test_data_persistence_no_longer_stops_at_the_container_step(tmp_path):
    """The SQL block used to shell into a fixed container, which aborted the run
    before the restart the milestone exists to perform."""
    one = _sandbox(tmp_path / "persist")
    text = (one["root"] / "verify_data_persistence.sh").read_text(encoding="utf-8")
    assert "psql -U test" not in text
    result = _run(one, "verify_data_persistence.sh", db_url=VALID_URL, pgrep_pid="4242")
    assert "SQL counts: NOT CAPTURED" in result.stdout
    assert "nohup" in _calls(one), f"still never restarted: {_calls(one)}"


def test_refusal_output_names_the_variable_without_naming_a_substitute(box):
    """The owner's rule: no hardcoded 5432 and no other fallback replacing 5434."""
    result = _run(box, "verify_quota_persistence.sh", db_url=None)
    assert "localhost:5434" not in result.stderr
    assert "please set" in result.stderr.lower() or "expected" in result.stderr.lower()
    assert "alethra_owner_test" not in result.stderr, "the refusal must not suggest a target DB"


def test_restoring_the_old_default_would_be_caught_not_tolerated(tmp_path):
    """Negative control: put a fallback back and show both witnesses react — the run
    side-effects instead of refusing, and the static scan names the file."""
    mutated_box = _sandbox(tmp_path)
    victim = mutated_box["root"] / "verify_milestone3_external.sh"
    text = victim.read_text(encoding="utf-8")
    mutated = text.replace(
        "require_db_target DATABASE_URL",
        'DATABASE_URL="${DATABASE_URL:-postgresql://test:test@localhost:5434/alethra_acceptance}"\n'
        "export DATABASE_URL",
    )
    assert mutated != text, "the mutation anchor is no longer in the script"
    victim.write_text(mutated, encoding="utf-8")

    result = _run(mutated_box, "verify_milestone3_external.sh", db_url=None)
    assert result.returncode != 2, "the copy kept refusing, so it does not model the old defect"
    assert _calls(mutated_box), "a fallback copy that still makes no call proves nothing"

    offenders = [
        name for name in SCRIPTS
        if any(token in _executable_text(mutated_box["root"] / name) for token in RETIRED_TOKENS)
    ]
    assert offenders == ["verify_milestone3_external.sh"], offenders
