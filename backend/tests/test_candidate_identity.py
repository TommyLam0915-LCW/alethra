"""Persistent candidate identity: resume must never depend on today's date.

These tests use fake probes and command stubs only. They do not start, stop or
restart the live candidate stack, and they do not touch PostgreSQL.
"""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "scripts"))

import candidate_identity as ci  # noqa: E402

START = REPO_ROOT / "start_candidate_stack.sh"
STOP = REPO_ROOT / "stop_candidate_stack.sh"

DB_NAME = "alethra_personal_e2e_candidate_20261029"
HEAD = "r2s3t4u5v6w7"
OWNER = "candidate.owner@example.com"
THIN = "candidate.thin@example.com"
EMPTY = "candidate.empty@example.com"


def _profile(tmp_path, **overrides):
    values = {
        "CANDIDATE_ID": "first-use-20261029",
        "CHECKOUT_DIR": str(REPO_ROOT),
        "DB_NAME": DB_NAME,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "54321",
        "DB_USER": "postgres",
        "CREDENTIALS_FILE": str(tmp_path / "credentials.env"),
        "ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "LOG_DIR": str(tmp_path / "logs"),
        "BACKEND_ORIGIN": "http://127.0.0.1:18100",
        "FRONTEND_ORIGIN": "http://127.0.0.1:13100",
        "BACKEND_PORT": "18100",
        "FRONTEND_PORT": "13100",
        "EXPECTED_SCHEMA_HEAD": HEAD,
        "FIXTURE_OWNER_EMAIL": OWNER,
        "FIXTURE_THIN_EMAIL": THIN,
        "FIXTURE_EMPTY_EMAIL": EMPTY,
        "CREATED_AT": "2026-10-29 18:00:00 +0800",
    }
    values.update(overrides)
    path = tmp_path / "candidate-profile.env"
    ci.write_profile(str(path), values)
    return path, ci.load_profile(str(path))


def _probe(**overrides):
    payload = {
        "exists": True,
        "db": DB_NAME,
        "head": HEAD,
        "fixtures": {OWNER: True, THIN: True, EMPTY: True},
    }
    payload.update(overrides)
    return payload


# --- unit: resume decisions --------------------------------------------------

def test_resume_uses_profile_database_and_never_seeds(tmp_path):
    _, profile = _profile(tmp_path)
    plan = ci.evaluate_resume(profile, _probe())
    assert plan["DB_NAME"] == DB_NAME
    assert plan["SEED"] == "no"
    assert plan["MODE"] == "resume"


def test_missing_database_is_rejected_not_created(tmp_path):
    _, profile = _profile(tmp_path)
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.evaluate_resume(profile, {"exists": False, "db": DB_NAME})
    message = str(excinfo.value)
    assert "does not exist" in message
    assert "never creates a replacement database" in message


def test_schema_head_mismatch_is_rejected(tmp_path):
    _, profile = _profile(tmp_path)
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.evaluate_resume(profile, _probe(head="oldhead00000"))
    assert "schema head" in str(excinfo.value)


def test_missing_fixture_account_is_rejected(tmp_path):
    _, profile = _profile(tmp_path)
    fixtures = {OWNER: True, THIN: False, EMPTY: True}
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.evaluate_resume(profile, _probe(fixtures=fixtures))
    assert THIN in str(excinfo.value)


@pytest.mark.parametrize(
    "override",
    [
        {"CANDIDATE_DB_NAME": "alethra_personal_e2e_candidate_20990101"},
        {"CANDIDATE_DB_HOST": "127.0.0.2"},
        {"CANDIDATE_DB_PORT": "59999"},
        {"CANDIDATE_DB_USER": "someone_else"},
        {"CANDIDATE_BACKEND_PORT": "18200"},
        {"CANDIDATE_FRONTEND_PORT": "13200"},
    ],
)
def test_explicit_override_conflict_is_rejected(tmp_path, override):
    _, profile = _profile(tmp_path)
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.evaluate_resume(profile, _probe(), override)
    assert "conflicts with the saved candidate" in str(excinfo.value)


def test_profile_origin_port_must_match_profile_port(tmp_path):
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        _profile(tmp_path, BACKEND_ORIGIN="http://127.0.0.1:19999")
    assert "port does not match" in str(excinfo.value)


def test_profile_database_prefix_is_enforced(tmp_path):
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        _profile(tmp_path, DB_NAME="some_other_database")
    assert "must start with" in str(excinfo.value)


# --- unit: runtime binding ---------------------------------------------------

def _entry(role, url, cmdline, alive=True, pid=1234):
    return {"role": role, "pid": pid, "alive": alive, "cmdline": cmdline, "database_url": url}


def test_runtime_binding_accepts_matching_processes(tmp_path):
    _, profile = _profile(tmp_path)
    url = f"postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/{DB_NAME}"
    entries = [
        _entry("backend", url, "python -m uvicorn app.main:app --port 18100"),
        _entry("worker", url, "python -m app.workers.training_worker 2"),
    ]
    assert ci.verify_runtime_binding(profile, entries)["RUNTIME_BINDING"] == "verified"


def test_worker_pointing_at_another_database_is_not_ready(tmp_path):
    _, profile = _profile(tmp_path)
    backend_url = f"postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/{DB_NAME}"
    worker_url = "postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/alethra_personal_e2e_candidate_20261030"
    entries = [
        _entry("backend", backend_url, "python -m uvicorn app.main:app"),
        _entry("worker", worker_url, "python -m app.workers.training_worker 2"),
    ]
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.verify_runtime_binding(profile, entries)
    assert "points at database" in str(excinfo.value)


def test_process_without_visible_database_url_is_not_ready(tmp_path):
    _, profile = _profile(tmp_path)
    entries = [
        _entry("backend", f"postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/{DB_NAME}",
               "python -m uvicorn app.main:app"),
        _entry("worker", None, "python -m app.workers.training_worker 2"),
    ]
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.verify_runtime_binding(profile, entries)
    assert "no visible DATABASE_URL" in str(excinfo.value)


def test_check_process_marks_a_recycled_pid_stale(tmp_path, monkeypatch):
    _, profile = _profile(tmp_path)
    monkeypatch.setattr(
        ci, "_process_identity",
        lambda pid, ps_bin: {"alive": True, "cmdline": "/bin/zsh -l", "database_url": None},
    )
    result = ci.check_process(profile, "backend", 4242, "/bin/ps")
    assert result == {"VERIFIED": "no", "REASON": "not-this-role", "CMDLINE": "/bin/zsh -l"}


def test_check_process_reports_identity_mismatch(tmp_path, monkeypatch):
    _, profile = _profile(tmp_path)
    monkeypatch.setattr(
        ci, "_process_identity",
        lambda pid, ps_bin: {
            "alive": True,
            "cmdline": "python -m app.workers.training_worker 2",
            "database_url": "postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/other_candidate",
        },
    )
    result = ci.check_process(profile, "worker", 4242, "/bin/ps")
    assert result["VERIFIED"] == "no"
    assert result["REASON"] == "identity-mismatch"


# --- unit: explicit new candidate -------------------------------------------

def test_new_candidate_refuses_to_overwrite_existing_candidate(tmp_path):
    profile, _ = _profile(tmp_path)
    credentials = tmp_path / "credentials.env"
    credentials.write_text("x", encoding="utf-8")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "model").write_text("x", encoding="utf-8")

    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.new_candidate_plan(
            "second-trial", str(profile), str(credentials), str(log_dir),
            "alethra_personal_e2e_candidate_second_trial", str(artifacts),
        )
    message = str(excinfo.value)
    assert "would overwrite an existing profile" in message
    assert "would overwrite an existing credentials" in message
    assert "would overwrite an existing log directory" in message
    assert "artifact directory" in message


def test_new_candidate_accepts_empty_paths(tmp_path):
    profile = tmp_path / "fresh-profile.env"
    credentials = tmp_path / "fresh-credentials.env"
    log_dir = tmp_path / "fresh-logs"
    artifacts = tmp_path / "fresh-artifacts"
    artifacts.mkdir()
    result = ci.new_candidate_plan(
        "second-trial", str(profile), str(credentials), str(log_dir),
        "alethra_personal_e2e_candidate_second_trial", str(artifacts),
    )
    assert result == {
        "CANDIDATE_ID": "second-trial",
        "DB_NAME": "alethra_personal_e2e_candidate_second_trial",
    }


def test_new_candidate_rejects_invalid_identity(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with pytest.raises(ci.CandidateIdentityError) as excinfo:
        ci.new_candidate_plan(
            "Bad ID!", str(tmp_path / "p.env"), str(tmp_path / "c.env"),
            str(tmp_path / "l"), "not_a_candidate_db", str(artifacts),
        )
    message = str(excinfo.value)
    assert "lowercase letters, digits" in message
    assert "must start with" in message


# --- shell: read-only plan mode ---------------------------------------------

def _shell_env(tmp_path, probe, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({
        "CANDIDATE_PYTHON": sys.executable,
        "CANDIDATE_STATE_DIR": str(tmp_path / "state"),
        "CANDIDATE_PROFILE": str(tmp_path / "candidate-profile.env"),
        "CANDIDATE_PROBE_CMD": f"printf '%s' {shlex.quote(json.dumps(probe))}",
    })
    env.update(extra or {})
    return env


def _run_plan(tmp_path, probe, extra=None):
    return subprocess.run(
        ["bash", str(START), "--plan"],
        cwd=str(REPO_ROOT),
        env=_shell_env(tmp_path, probe, extra),
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_shell_plan_uses_saved_database_on_another_date(tmp_path):
    _profile(tmp_path)
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    fake_date = fakebin / "date"
    fake_date.write_text("#!/bin/sh\necho 2099-12-31\n", encoding="utf-8")
    fake_date.chmod(0o755)
    env_extra = {"PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"}

    result = _run_plan(tmp_path, _probe(), env_extra)

    assert result.returncode == 0, result.stderr
    assert f"DB_NAME={DB_NAME}" in result.stdout
    assert "SEED=no" in result.stdout
    assert "2099" not in result.stdout


def test_shell_plan_rejects_conflicting_database_override(tmp_path):
    _profile(tmp_path)
    result = _run_plan(
        tmp_path, _probe(),
        {"CANDIDATE_DB_NAME": "alethra_personal_e2e_candidate_20990101"},
    )
    assert result.returncode == 2
    assert "conflicts with the saved candidate database name" in result.stderr


def test_shell_plan_does_not_create_a_missing_database(tmp_path):
    _profile(tmp_path)
    result = _run_plan(
        tmp_path,
        {"exists": False, "db": DB_NAME},
    )
    assert result.returncode == 2
    assert "never creates a replacement database" in result.stderr
    assert "creating database" not in (result.stdout + result.stderr).lower()
    assert not (tmp_path / "state" / "runtime-identity.env").exists()


def test_shell_plan_refuses_a_profile_from_another_checkout(tmp_path):
    _profile(tmp_path, CHECKOUT_DIR="/tmp/some-other-checkout")
    result = _run_plan(tmp_path, _probe())
    assert result.returncode == 1
    assert "belongs to" in result.stderr


def test_shell_stop_refuses_without_profile(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env["CANDIDATE_PROFILE"] = str(tmp_path / "missing-profile.env")
    result = subprocess.run(
        ["bash", str(STOP)], cwd=str(REPO_ROOT), env=env,
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 1
    assert "refusing to stop processes by pid file alone" in result.stderr


def test_shell_resume_without_a_profile_refuses_instead_of_guessing(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({
        "CANDIDATE_PYTHON": sys.executable,
        "CANDIDATE_PROFILE": str(tmp_path / "missing-profile.env"),
    })
    result = subprocess.run(
        ["bash", str(START)], cwd=str(REPO_ROOT), env=env,
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 1
    assert "no persistent candidate profile" in result.stderr
    assert "Resume refuses to guess a database" in result.stderr


def test_shell_new_candidate_refuses_to_overwrite_an_existing_profile(tmp_path):
    profile, _ = _profile(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({
        "CANDIDATE_PYTHON": sys.executable,
        "CANDIDATE_STATE_DIR": str(tmp_path / "state"),
        "CANDIDATE_PROFILE": str(profile),
    })
    result = subprocess.run(
        ["bash", str(START), "--new-candidate", "--candidate-id", "second-trial"],
        cwd=str(REPO_ROOT), env=env, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 2
    assert "would overwrite an existing profile" in result.stderr


def _fake_process(tmp_path, argument, database_url):
    script = tmp_path / "fake_process.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env["DATABASE_URL"] = database_url
    return subprocess.Popen(
        [sys.executable, str(script), argument],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_env(tmp_path, profile):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({
        "CANDIDATE_PYTHON": sys.executable,
        "CANDIDATE_PROFILE": str(profile),
    })
    return env


def test_stop_only_kills_a_verified_worker(tmp_path):
    profile, _ = _profile(tmp_path, LOG_DIR=str(tmp_path / "logs"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    url = f"postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/{DB_NAME}"
    process = _fake_process(tmp_path, "python -m app.workers.training_worker 2", url)
    (log_dir / "worker.pid").write_text(str(process.pid), encoding="utf-8")
    try:
        result = subprocess.run(
            ["bash", str(STOP)], cwd=str(REPO_ROOT), env=_stop_env(tmp_path, profile),
            text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert f"stopping worker pid {process.pid}" in result.stdout
        process.wait(timeout=30)
        assert process.returncode is not None
        assert not (log_dir / "worker.pid").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def test_stop_leaves_a_worker_bound_to_another_database(tmp_path):
    profile, _ = _profile(tmp_path, LOG_DIR=str(tmp_path / "logs"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    url = "postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/other_candidate_db"
    process = _fake_process(tmp_path, "python -m app.workers.training_worker 2", url)
    (log_dir / "worker.pid").write_text(str(process.pid), encoding="utf-8")
    try:
        result = subprocess.run(
            ["bash", str(STOP)], cwd=str(REPO_ROOT), env=_stop_env(tmp_path, profile),
            text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "does not match the saved candidate identity" in result.stdout
        assert process.poll() is None
        assert (log_dir / "worker.pid").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def test_stop_leaves_a_recycled_pid(tmp_path):
    profile, _ = _profile(tmp_path, LOG_DIR=str(tmp_path / "logs"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    process = _fake_process(tmp_path, "python -m http.server 6553", "unused")
    (log_dir / "worker.pid").write_text(str(process.pid), encoding="utf-8")
    try:
        result = subprocess.run(
            ["bash", str(STOP)], cwd=str(REPO_ROOT), env=_stop_env(tmp_path, profile),
            text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "is not this candidate's process" in result.stdout
        assert process.poll() is None
        assert not (log_dir / "worker.pid").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
