"""Persistent candidate identity: resume must never depend on today's date.

These tests use fake probes and command stubs only. They do not start, stop or
restart the live candidate stack, and they do not touch PostgreSQL.
"""
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    # Read-only means no runtime state either, not just no database writes.
    assert not (tmp_path / "state" / "runtime-identity.env").exists()


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


def test_shell_resume_refusal_invalidates_a_previous_ready_identity(tmp_path):
    # 2026-10-31 shape: the last run left STATUS=ready, then the database went
    # away. A refused resume must not leave that ready claim standing.
    profile, _ = _profile(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    identity_file = state / "runtime-identity.env"
    identity_file.write_text(
        "STATUS=ready\nMODE=resume\nBACKEND_PID=11\nWORKER_PID=22\n",
        encoding="utf-8",
    )

    result = _run_sourced('''
require_credentials() { :; }
readonly_preflight() { fail_code 2 "candidate database is not reachable"; }
resume_mode
''', {
        "CANDIDATE_STATE_DIR": str(state),
        "CANDIDATE_PROFILE": str(profile),
    })

    content = identity_file.read_text(encoding="utf-8")
    assert result.returncode == 2, result.stdout + result.stderr
    assert content.startswith("STATUS=pending\n")
    assert "STATUS=ready" not in content
    assert "RUN_ID=" in content
    # Pending means nothing is proven, so no process may be recorded as bound.
    assert "BACKEND_PID=\n" in content
    assert "WORKER_PID=\n" in content


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


def _stop_env(tmp_path, profile, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({
        "CANDIDATE_PYTHON": sys.executable,
        "CANDIDATE_PROFILE": str(profile),
        # Never let a test rewrite this checkout's real runtime identity.
        "CANDIDATE_STATE_DIR": str(tmp_path / "state"),
    })
    env.update(extra or {})
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


def test_stop_marks_a_previous_ready_identity_stopped(tmp_path):
    profile, _ = _profile(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    identity_file = state / "runtime-identity.env"
    identity_file.write_text(
        "STATUS=ready\nMODE=resume\nRUN_ID=20261030T101212+0800-1111\nBACKEND_PID=11\n",
        encoding="utf-8",
    )
    identity_file.chmod(0o600)

    result = subprocess.run(
        ["bash", str(STOP)], cwd=str(REPO_ROOT), env=_stop_env(tmp_path, profile),
        text=True, capture_output=True, timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    content = identity_file.read_text(encoding="utf-8")
    assert content.startswith("STATUS=stopped\n")
    assert "STATUS=ready" not in content
    # Everything the record already held must survive the status change.
    assert "MODE=resume" in content
    assert "RUN_ID=20261030T101212+0800-1111" in content
    assert "BACKEND_PID=11" in content
    assert "STOPPED_AT=" in content
    assert (identity_file.stat().st_mode & 0o777) == 0o600


def test_stop_does_not_claim_stopped_when_a_recorded_pid_is_left_alone(tmp_path):
    profile, _ = _profile(tmp_path, LOG_DIR=str(tmp_path / "logs"))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    identity_file = state / "runtime-identity.env"
    identity_file.write_text("STATUS=ready\nMODE=resume\nBACKEND_PID=\n", encoding="utf-8")
    url = "postgresql+psycopg2://postgres:testpass@127.0.0.1:54321/other_candidate_db"
    process = _fake_process(tmp_path, "python -m app.workers.training_worker 2", url)
    (log_dir / "worker.pid").write_text(str(process.pid), encoding="utf-8")
    try:
        result = subprocess.run(
            ["bash", str(STOP)], cwd=str(REPO_ROOT), env=_stop_env(tmp_path, profile),
            text=True, capture_output=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        content = identity_file.read_text(encoding="utf-8")
        assert content.startswith("STATUS=stop-incomplete\n")
        assert "STATUS=ready" not in content
        assert "STOPPED_AT=" in content
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


# --- unit: layered readiness -------------------------------------------------

def _heartbeat_file(tmp_path, state="idle", pid=321, age_seconds=0.0, extra=None):
    path = tmp_path / "worker-heartbeat.json"
    payload = {"pid": pid, "state": state, "beat_at": "2026-10-31T02:12:58.616407"}
    payload.update(extra or {})
    path.write_text(json.dumps(payload), encoding="utf-8")
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


@pytest.mark.parametrize("state", ["idle", "working"])
def test_worker_health_accepts_a_fresh_healthy_heartbeat(tmp_path, state):
    path = _heartbeat_file(tmp_path, state=state)
    assert ci.evaluate_worker_health(str(path), 321) == {
        "WORKER": "ready", "STATE": state, "REASON": "",
    }


def test_worker_health_rejects_the_outage_heartbeat_and_redacts_details(tmp_path):
    # The 2026-10-31 shape: a fresh heartbeat whose state is "error".
    path = _heartbeat_file(
        tmp_path,
        state="error",
        extra={"error": 'connection to server at "127.0.0.1", port 54321 failed: Connection refused'},
    )
    result = ci.evaluate_worker_health(str(path), 321)
    assert result["WORKER"] == "unavailable"
    assert result["STATE"] == "error"
    assert "127.0.0.1" not in result["REASON"]
    assert "54321" not in result["REASON"]
    assert "connection" not in result["REASON"].lower()


def test_worker_health_rejects_stopped_and_stale_heartbeats(tmp_path):
    stopped = _heartbeat_file(tmp_path, state="stopped")
    assert ci.evaluate_worker_health(str(stopped), 321)["WORKER"] == "unavailable"
    stale = _heartbeat_file(tmp_path, state="idle", age_seconds=600)
    result = ci.evaluate_worker_health(str(stale), 321)
    assert result["WORKER"] == "unavailable"
    assert "old" in result["REASON"]


def test_worker_health_rejects_a_foreign_pid(tmp_path):
    path = _heartbeat_file(tmp_path)
    assert ci.evaluate_worker_health(str(path), 999)["WORKER"] == "unavailable"


def test_worker_health_cli_exits_three_when_the_worker_is_in_error(tmp_path):
    path = _heartbeat_file(tmp_path, state="error")
    result = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "candidate_identity.py"),
         "check-heartbeat", "--heartbeat", str(path), "--worker-pid", "321"],
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 3, result.stderr
    assert "WORKER=unavailable" in result.stdout
    assert "STATE=error" in result.stdout


class _StubReadiness(BaseHTTPRequestHandler):
    status_code = 200
    body = b"{}"

    def do_GET(self):  # noqa: N802 - http.server API
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        return


def _serve(payload, status_code):
    handler = type(
        "Handler",
        (_StubReadiness,),
        {"body": json.dumps(payload).encode(), "status_code": status_code},
    )
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/health/ready"


def test_readiness_probe_accepts_a_ready_database():
    server, url = _serve(
        {"status": "ready", "checks": {"database": {"status": "connected"}, "redis": {"status": "not_configured"}}},
        200,
    )
    try:
        assert ci.check_ready(url, timeout=5) == {"READY": "yes", "STATUS": "ready", "FAILED": "none"}
    finally:
        server.shutdown()
        server.server_close()


def test_readiness_probe_reports_the_failed_check_without_sql_details():
    payload = {
        "status": "not_ready",
        "checks": {
            "database": {"status": "unavailable", "error": "connection failed: password=secret host=127.0.0.1:54321"},
            "redis": {"status": "not_configured"},
        },
    }
    server, url = _serve(payload, 503)
    try:
        result = ci.check_ready(url, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
    assert result == {"READY": "no", "STATUS": "not_ready", "FAILED": "database"}
    assert "secret" not in json.dumps(result)
    assert "54321" not in json.dumps(result)


def test_readiness_probe_cli_exits_three_when_not_ready():
    server, url = _serve({"status": "not_ready", "checks": {"database": {"status": "unavailable"}}}, 503)
    try:
        result = subprocess.run(
            [sys.executable, str(BACKEND_DIR / "scripts" / "candidate_identity.py"),
             "check-ready", "--url", url, "--timeout", "5"],
            text=True, capture_output=True, timeout=60,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 3, result.stderr
    assert "READY=no" in result.stdout
    assert "FAILED=database" in result.stdout


def test_readiness_probe_reports_an_unreachable_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _StubReadiness)
    port = server.server_port
    server.server_close()
    result = ci.check_ready(f"http://127.0.0.1:{port}/health/ready", timeout=2)
    assert result == {"READY": "no", "STATUS": "unreachable", "FAILED": "endpoint"}


# --- integration: the launcher's own gates, not just the helper --------------
# These source the real start script (CANDIDATE_SOURCE_ONLY keeps it from
# running a mode) and call its real functions. Nothing is started or stopped.

def _run_sourced(body, extra_env=None, timeout=90):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CANDIDATE_")}
    env.update({"CANDIDATE_PYTHON": sys.executable, "CANDIDATE_SOURCE_ONLY": "1"})
    env.update(extra_env or {})
    script = "source " + shlex.quote(str(START)) + "\n" + body
    return subprocess.run(
        ["bash", "-c", script], cwd=str(REPO_ROOT), env=env,
        text=True, capture_output=True, timeout=timeout,
    )


def test_shell_readiness_gate_exits_three_and_never_prints_ready(tmp_path):
    payload = {
        "status": "not_ready",
        "checks": {"database": {"status": "unavailable", "error": "password=secret host=127.0.0.1:54321"}},
    }
    server, url = _serve(payload, 503)
    logs = tmp_path / "logs"
    try:
        result = _run_sourced(f'''
LOG_DIR={shlex.quote(str(logs))}
mkdir -p "$LOG_DIR"
BACKEND_ORIGIN={shlex.quote(url.rsplit("/health/ready", 1)[0])}
wait_backend_ready 1
''')
    finally:
        server.shutdown()
        server.server_close()
    combined = result.stdout + result.stderr
    assert result.returncode == 3, combined
    assert "database is not ready" in result.stderr
    assert "failed checks: database" in result.stderr
    assert "secret" not in combined
    assert "54321" not in combined
    assert "Candidate stack ready" not in combined


def test_shell_worker_gate_exits_four_for_the_outage_heartbeat(tmp_path):
    heartbeat = _heartbeat_file(
        tmp_path,
        state="error",
        extra={"error": 'connection to server at "127.0.0.1", port 54321 failed: Connection refused'},
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    result = _run_sourced(f'''
HEARTBEAT_FILE={shlex.quote(str(heartbeat))}
LOG_DIR={shlex.quote(str(logs))}
wait_worker_heartbeat 321 1
''')
    combined = result.stdout + result.stderr
    assert result.returncode == 4, combined
    assert "worker pid 321 is alive but not healthy" in result.stderr
    assert "127.0.0.1" not in combined
    assert "54321" not in combined
    assert "Candidate stack ready" not in combined


def test_shell_frontend_gate_exits_five_when_nothing_answers(tmp_path):
    probe = HTTPServer(("127.0.0.1", 0), _StubReadiness)
    port = probe.server_port
    probe.server_close()
    result = _run_sourced(
        f'wait_http {shlex.quote(f"http://127.0.0.1:{port}/login")} frontend 1 5'
    )
    assert result.returncode == 5, result.stdout + result.stderr
    assert "frontend did not answer" in result.stderr


def test_shell_partial_start_failure_leaves_a_pending_identity(tmp_path):
    # The 2026-10-31 shape: the backend process is up but the database is not,
    # so a previous run's complete-looking runtime-identity.env must be replaced.
    server, url = _serve({"status": "not_ready", "checks": {"database": {"status": "unavailable"}}}, 503)
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    try:
        result = _run_sourced(f'''
MODE=resume
STATE_DIR={shlex.quote(str(state))}
LOG_DIR={shlex.quote(str(logs))}
BACKEND_ORIGIN={shlex.quote(url.rsplit("/health/ready", 1)[0])}
CANDIDATE_READY_TRIES=1
export_process_env() {{ :; }}
start_backend() {{ echo 4242 > "$BACKEND_PID_FILE"; }}
start_stack
''')
    finally:
        server.shutdown()
        server.server_close()
    identity_file = state / "runtime-identity.env"
    assert identity_file.exists()
    content = identity_file.read_text(encoding="utf-8")
    assert content.startswith("STATUS=pending\n")
    # Pending means "nothing confirmed yet": no process may be recorded as bound.
    assert "BACKEND_PID=\n" in content
    assert "STATUS=ready" not in content
    assert "RUN_ID=" in content
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Candidate stack ready" not in result.stdout + result.stderr


def test_shell_identity_file_reads_ready_only_after_the_final_gate(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    result = _run_sourced(f'''
MODE=resume
STATE_DIR={shlex.quote(str(state))}
BACKEND_PID=11 WORKER_PID=22 FRONTEND_PID=33
HEARTBEAT_FILE=/tmp/unused-heartbeat.json
WORKER_STATE=idle
write_runtime_identity ready
''')
    assert result.returncode == 0, result.stdout + result.stderr
    content = (state / "runtime-identity.env").read_text(encoding="utf-8")
    assert content.startswith("STATUS=ready\n")
    assert "WORKER_STATE=idle" in content
    assert "STATUS=pending" not in content


def test_shell_identity_mismatch_keeps_its_documented_exit_code(tmp_path):
    result = _run_sourced('''
identity() { echo "CANDIDATE IDENTITY ERROR: worker pid is not this role" >&2; return 2; }
verify_runtime_identity
''')
    assert result.returncode == 2, result.stdout + result.stderr
    assert "identity verification failed" in result.stderr
