"""Environment entries (plan section 二F): every entry refuses before it acts.

These tests cover the entry points an operator is told to use — the documented
backend suite entry, the candidate start/stop/status trio, and the docker-backed
acceptance start/stop pair. They are subprocess tests with scratch trees and
fake processes: they do not start, stop or restart the live candidate stack, they
do not touch PostgreSQL, and they never create a container.

What is pinned here is the part that was measurably wrong before this round:
  * an entry could silently run a second dependency set (system python3) and let
    its counts be read as this lane's;
  * entries carried one machine's absolute paths and an embedded DB password and
    signing key, and the acceptance pair contradicted itself about the password;
  * there was no read-only status entry, so "is it up?" could only be answered
    by starting something, and the start script had no stop counterpart.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import sqlalchemy

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
# `scripts/` is not a package, so the import below needs it on the path — but
# only for the duration of this import. Leaving a directory at sys.path[0] for
# the rest of the session makes every later plain `import X` resolve there
# first, which is process-global state this file has no business changing.
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
import candidate_identity as ci  # noqa: E402
sys.path.remove(str(BACKEND_DIR / "scripts"))

BASH = "/bin/bash"
RUN_TESTS = BACKEND_DIR / "scripts" / "run_tests.sh"
START = REPO_ROOT / "start_candidate_stack.sh"
STOP = REPO_ROOT / "stop_candidate_stack.sh"
STATUS = REPO_ROOT / "candidate_status.sh"
ACCEPT_START = REPO_ROOT / "start_acceptance_env.sh"
ACCEPT_STOP = REPO_ROOT / "stop_acceptance_env.sh"

DB_NAME = "alethra_personal_e2e_candidate_20261029"
DB_URL = "postgresql+psycopg2://postgres@127.0.0.1:54321/" + DB_NAME
HEAD = "r2s3t4u5v6w7"
OWNER = "candidate.owner@example.com"
THIN = "candidate.thin@example.com"
EMPTY = "candidate.empty@example.com"

HOME_PATH = re.compile(r"/Users/[^/\s\"']+")
INFRA_SECRET = re.compile(
    r"://[^/\s\"'@]*:[^/\s\"'@$]+@"                              # literal pw in a URL
    r"|(?:POSTGRES_PASSWORD|PGPASSWORD|DB_PASSWORD|DATABASE_PASSWORD"
    r"|JWT_SECRET_KEY|SECRET_KEY|SIGNING_KEY|API_KEY|ACCESS_TOKEN)=['\"]?[A-Za-z0-9]"
)


def _env(**overrides):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    # conftest aliases ALETHRA_TEST_DB into ALETHRA_PG_TEST_URL inside this very
    # process, so a nested lane run inherits a PG alias that contradicts the
    # sqlite file it is about to pick. The nested entry starts from its own
    # layer, never from this process's leftover DB environment.
    for key in ("ALETHRA_PG_TEST_URL", "ALETHRA_PG_DATABASE_URL",
                "ALETHRA_TEST_POSTGRES_URL", "ALETHRA_TEST_DB", "ALETHRA_SQLITE_DB",
                "ALETHRA_TEST_REQUIRED_DB_PREFIX", "DATABASE_URL"):
        env.pop(key, None)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


def _run(script, *args, env=None, cwd=None):
    return subprocess.run(
        [BASH, str(script), *args],
        capture_output=True, text=True, timeout=300,
        cwd=str(cwd or REPO_ROOT), env=env,
    )


def _lines(result):
    """The report as {label: text}; labels are what the entry prints."""
    out = {}
    for line in result.stdout.splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def _scratch_tree(tmp_path, script):
    """A tree holding only this script, so its ROOT resolves to the scratch dir."""
    target = tmp_path / script.relative_to(REPO_ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(script, target)
    return target


def _profile(tmp_path, backend_port=18100, frontend_port=13100, **overrides):
    """load_profile refuses a profile whose origins disagree with its ports, so
    the two are derived here instead of being held as unrelated constants."""
    values = {
        "CANDIDATE_ID": "env-entries-selftest",
        "CHECKOUT_DIR": str(REPO_ROOT),
        "DB_NAME": DB_NAME,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "54321",
        "DB_USER": "postgres",
        "CREDENTIALS_FILE": str(tmp_path / "credentials.env"),
        "ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "LOG_DIR": str(tmp_path / "logs"),
        "BACKEND_ORIGIN": f"http://127.0.0.1:{backend_port}",
        "FRONTEND_ORIGIN": f"http://127.0.0.1:{frontend_port}",
        "BACKEND_PORT": str(backend_port),
        "FRONTEND_PORT": str(frontend_port),
        "EXPECTED_SCHEMA_HEAD": HEAD,
        "FIXTURE_OWNER_EMAIL": OWNER,
        "FIXTURE_THIN_EMAIL": THIN,
        "FIXTURE_EMPTY_EMAIL": EMPTY,
        "CREATED_AT": "2026-11-16 12:00:00 +0800",
    }
    values.update(overrides)
    path = tmp_path / "candidate-profile.env"
    ci.write_profile(str(path), values)
    (tmp_path / "logs").mkdir(exist_ok=True)
    return path


def _status_env(tmp_path, profile, **overrides):
    base = {
        "CANDIDATE_PROFILE": str(profile),
        "CANDIDATE_STATE_DIR": str(tmp_path / "state"),
        "CANDIDATE_DB_PASSWORD": None,
        "TMPDIR": tmp_path,
    }
    base.update(overrides)
    return _env(**base)


def _spawn(args, env=None):
    """A direct python exec, so ps eww shows its command line and environment."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ready", "checks": {
            "database": {"status": "connected"},
            "schema": {"status": "compatible"},
            "redis": {"status": "not_configured"},
        }}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def ready_server():
    server = HTTPServer(("127.0.0.1", 0), _ReadyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


# --- the documented suite entry and its interpreter --------------------------

def test_run_tests_refuses_a_checkout_without_a_venv(tmp_path):
    script = _scratch_tree(tmp_path, RUN_TESTS)
    result = _run(script, "sqlite", "-q", "--collect-only",
                  env=_env(ALETHRA_PYTHON=None, TMPDIR=tmp_path))
    assert result.returncode == 2, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "no venv interpreter" in combined
    assert ".venv/bin/python" in combined and "venv/bin/python" in combined
    assert "system python3 is not used silently" in combined
    assert "collected" not in result.stdout


def test_run_tests_refuses_an_explicit_interpreter_that_is_not_executable(tmp_path):
    result = _run(RUN_TESTS, "sqlite", "--collect-only",
                  env=_env(ALETHRA_PYTHON=tmp_path, TMPDIR=tmp_path))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not an executable file" in result.stderr


def test_run_tests_names_the_interpreter_that_actually_runs_the_suite(tmp_path):
    """The printed lane fingerprint must describe the interpreter that ran pytest.

    This host's system python3 also carries pytest and SQLAlchemy, so the entry
    used to be able to run a different dependency set than the pinned venv while
    printing nothing about it. Both reported lines are compared against this test
    process, which is the interpreter the documented lane command resolves to.
    """
    result = _run(RUN_TESTS, "sqlite", "-q", "--collect-only",
                  "-p", "no:cacheprovider", "tests/test_candidate_identity.py",
                  env=_env(ALETHRA_PYTHON=None, TMPDIR=tmp_path,
                           DATA_PROVIDER="demo"))
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    interpreter = re.search(r"^interpreter: (.*)$", result.stdout, re.M).group(1)
    assert Path(interpreter).resolve() == Path(sys.executable).resolve()
    match = re.search(
        r"lane fingerprint: python ([\w.]+) \| pytest ([\w.]+) \| sqlalchemy ([\w.]+)",
        result.stdout)
    assert match, result.stdout[:1500]
    assert match.group(1) == platform.python_version()
    assert match.group(2) == pytest.__version__
    assert match.group(3) == sqlalchemy.__version__


def test_run_tests_refuses_an_absent_scratch_directory_before_setup(tmp_path):
    """A missing database directory is refused, not reported as 16 sqlite errors.

    Before this gate the entry exported `sqlite:///<TMPDIR>/x.db` without looking
    at TMPDIR, and every test failed in the database fixture with "unable to open
    database file" - a count that reads as a product regression.
    """
    absent = tmp_path / "not-created-yet"
    result = _run(RUN_TESTS, "sqlite", "-q", "--collect-only",
                  env=_env(ALETHRA_PYTHON=None, TMPDIR=absent,
                           DATA_PROVIDER="demo"))
    combined = result.stdout + result.stderr
    assert result.returncode == 2, combined
    assert "the directory for the sqlite test database does not exist" in combined
    assert str(absent) in combined
    assert "TMPDIR=" in combined
    assert "unable to open database file" not in combined
    assert "ERROR" not in combined and "collected" not in combined


def test_run_tests_says_which_variable_named_the_absent_directory(tmp_path):
    absent = tmp_path / "also-absent" / "lane.db"
    result = _run(RUN_TESTS, "sqlite", "-q", "--collect-only",
                  env=_env(ALETHRA_PYTHON=None, ALETHRA_SQLITE_DB=absent,
                           DATA_PROVIDER="demo"))
    combined = result.stdout + result.stderr
    assert result.returncode == 2, combined
    assert "ALETHRA_SQLITE_DB=" in combined
    assert str(absent.parent) in combined


def test_run_tests_refuses_a_scratch_directory_it_cannot_write(tmp_path):
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    os.chmod(read_only, 0o500)
    try:
        result = _run(RUN_TESTS, "sqlite", "-q", "--collect-only",
                      env=_env(ALETHRA_PYTHON=None, TMPDIR=read_only,
                               DATA_PROVIDER="demo"))
        combined = result.stdout + result.stderr
        assert result.returncode == 2, combined
        assert "is not writable" in combined
    finally:
        os.chmod(read_only, 0o700)


# --- start / stop refuse instead of guessing ---------------------------------

def test_start_refuses_without_a_virtualenv_before_doing_anything(tmp_path):
    script = _scratch_tree(tmp_path, START)
    result = _run(script, env=_env(CANDIDATE_PYTHON=None, TMPDIR=tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "CANDIDATE SETUP ABORTED" in combined
    assert ".venv/bin/python" in combined and "venv/bin/python" in combined
    assert not (tmp_path / ".local").exists()


def test_stop_refuses_to_work_from_pid_files_alone(tmp_path):
    script = _scratch_tree(tmp_path, STOP)
    venv_bin = tmp_path / "backend" / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python").symlink_to(sys.executable)
    result = _run(script, env=_env(CANDIDATE_PYTHON=None, TMPDIR=tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refusing to stop processes by pid file alone" in result.stderr


# --- the status entry reports, and only reports ------------------------------

def test_status_refuses_without_a_profile(tmp_path):
    missing = tmp_path / "nope" / "candidate-profile.env"
    result = _run(STATUS, env=_status_env(tmp_path, missing))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no persistent candidate profile" in result.stderr


def test_status_refuses_a_profile_from_another_checkout(tmp_path):
    profile = _profile(tmp_path, 18100, CHECKOUT_DIR=str(tmp_path / "elsewhere"))
    result = _run(STATUS, env=_status_env(tmp_path, profile))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "belongs to" in result.stderr and "not" in result.stderr


def test_status_reports_unmatched_pids_and_leaves_both_running(tmp_path):
    """Two ways a pid file can point at somebody else, and neither is signalled.

    A process that does not look like the role at all, and a process that looks
    like the role but is attached to a different database, are the two stale-pid
    shapes a shared machine produces. Reporting either as "stopped" would let a
    later start treat that slot as free; signalling either would stop somebody
    else's server.
    """
    profile = _profile(tmp_path, 18100)
    unrelated = _spawn([])
    other_database = _spawn(
        ["-m", "app.workers.training_worker"],
        env=_env(DATABASE_URL="postgresql+psycopg2://postgres@127.0.0.1:54321/somebody_elses_db"),
    )
    left_running = ()
    try:
        (tmp_path / "logs" / "backend.pid").write_text(f"{unrelated.pid}\n")
        (tmp_path / "logs" / "worker.pid").write_text(f"{other_database.pid}\n")
        result = _run(STATUS, env=_status_env(tmp_path, profile))
        assert result.returncode == 1, result.stdout
        lines = _lines(result)
        assert f"pid {unrelated.pid} UNMATCHED" in lines["backend"], result.stdout
        assert "not-this-role" in lines["backend"], lines["backend"]
        assert f"pid {other_database.pid} UNMATCHED" in lines["worker"], result.stdout
        assert "identity-mismatch" in lines["worker"], lines["worker"]
        assert "somebody_elses_db" in lines["worker"], lines["worker"]
        assert "not as stopped" in result.stdout
        left_running = (unrelated.poll() is None, other_database.poll() is None)
    finally:
        for proc in (unrelated, other_database):
            proc.terminate()
            proc.wait(timeout=15)
    assert left_running == (True, True), "the status entry left both foreign processes running"


def test_status_reports_a_verified_backend_and_a_ready_endpoint(tmp_path, ready_server):
    profile = _profile(tmp_path, backend_port=ready_server)
    proc = _spawn(["uvicorn", "app.main:app"], env=_env(DATABASE_URL=DB_URL))
    try:
        (tmp_path / "logs" / "backend.pid").write_text(f"{proc.pid}\n")
        result = _run(STATUS, env=_status_env(tmp_path, profile))
        lines = _lines(result)
        assert f"pid {proc.pid} matches the profile" in lines["backend"], result.stdout
        assert "OK" in lines["readiness"], result.stdout
        assert "SKIPPED" in lines["schema"], result.stdout
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_status_does_not_report_a_foreign_listener_as_this_stack_being_up(tmp_path, ready_server):
    """A verified backend pid plus a port held by somebody else is not "up".

    Here the port the profile names is served by this test process, while the pid
    file names the spawned (correctly marked) process, so the two cannot be the
    same. Readiness then answers the listener, not the recorded process.
    """
    profile = _profile(tmp_path, backend_port=ready_server)
    proc = _spawn(["uvicorn", "app.main:app"], env=_env(DATABASE_URL=DB_URL))
    try:
        (tmp_path / "logs" / "backend.pid").write_text(f"{proc.pid}\n")
        result = _run(STATUS, env=_status_env(tmp_path, profile))
        lines = _lines(result)
        assert f"pid {proc.pid} matches the profile" in lines["backend"], result.stdout
        assert "not what the profile recorded" in lines[f"backend port {ready_server}"], result.stdout
        assert result.returncode == 1, result.stdout
        assert "STATUS: not fully up" in result.stdout
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_status_heartbeat_verdict_does_not_echo_the_worker_error(tmp_path, ready_server):
    profile = _profile(tmp_path, backend_port=ready_server)
    proc = _spawn(["-m", "app.workers.training_worker"], env=_env(DATABASE_URL=DB_URL))
    heartbeat = tmp_path / "logs" / "worker-heartbeat.json"
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "runtime-identity.env").write_text(
        "STATUS=ready\nRUN_ID=selftest\nUPDATED_AT=now\n"
        f"CANDIDATE_PROFILE={profile}\nHEARTBEAT_FILE={heartbeat}\n"
    )
    try:
        (tmp_path / "logs" / "worker.pid").write_text(f"{proc.pid}\n")
        heartbeat.write_text(json.dumps({
            "pid": proc.pid, "state": "idle",
            "beat_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "poll_interval_seconds": 5,
        }))
        ok = _run(STATUS, env=_status_env(tmp_path, profile))
        assert "OK" in ok.stdout and "STATE=idle" in ok.stdout, ok.stdout

        heartbeat.write_text(json.dumps({
            "pid": proc.pid, "state": "error",
            "beat_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "error": "password=hunter2 for host hidden-venue.internal",
            "poll_interval_seconds": 5,
        }))
        bad = _run(STATUS, env=_status_env(tmp_path, profile))
        assert "NOT READY" in bad.stdout and "state 'error'" in bad.stdout, bad.stdout
        assert "hunter2" not in bad.stdout and "hidden-venue" not in bad.stdout
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_status_reports_a_stale_or_unusable_record_instead_of_assuming(tmp_path):
    profile = _profile(tmp_path, 18100)
    dead = _dead_pid()
    (tmp_path / "logs" / "backend.pid").write_text(f"{dead}\n")
    stopped = _run(STATUS, env=_status_env(tmp_path, profile))
    assert f"pid {dead} is not running" in stopped.stdout

    (tmp_path / "logs" / "worker.pid").write_text("not-a-pid\n")
    junk = _run(STATUS, env=_status_env(tmp_path, profile))
    assert "does not hold a pid" in junk.stdout


def test_status_entry_contains_no_state_changing_verb():
    """The read-only claim is checked against the entry text, not asserted.

    'kill' is the exact gate: the entry asks whether a pid exists through
    candidate_identity.check_process, which uses os.kill(pid, 0) in its own
    process, and never signals, removes, creates or chmods anything itself.
    """
    source = STATUS.read_text(encoding="utf-8")
    for verb in (r"\bkill\b", r"\bpkill\b", r"\brm\b", r"\bmkdir\b", r"\btouch\b",
                 r"\bchmod\b", r"\bdocker\b", r"\balembic\b", r"\buvicorn\b",
                 r"\bSIGKILL\b", r"\bgit \b"):
        assert not re.search(verb, source), f"status entry contains {verb}"


# --- gates that can actually fail --------------------------------------------

PRE_FIX_JOURNEY = (
    'WORKTREE="/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-int"\n'
    'export DATABASE_URL="postgresql://test:test@localhost:5434/alethra_acceptance"\n'
    'export JWT_SECRET_KEY="acceptance-test-secret-minimum-32-chars-long"\n'
    'source venv/bin/activate\n'
)
PRE_FIX_STATUS = (
    'kill -9 "$pid"\n'
    'rm -f "$LOG_DIR/backend.pid"\n'
    'docker stop alethra-acceptance-db\n'
)
# What the fixed entries legitimately contain: interpolated credentials only.
ALLOWED = (
    'DB_URL="postgresql+psycopg2://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"\n'
    'export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1/x"\n'
    'export JWT_SECRET_KEY="$CANDIDATE_JWT_SECRET_KEY"\n'
    'PASSWORD="testpass123"\n'  # synthetic fixture account, not infrastructure
)


def test_the_refusal_gates_are_not_vacuous():
    assert HOME_PATH.search(PRE_FIX_JOURNEY)
    assert len(INFRA_SECRET.findall(PRE_FIX_JOURNEY)) == 2, INFRA_SECRET.findall(PRE_FIX_JOURNEY)
    for verb in (r"\bkill\b", r"\brm\b", r"\bdocker\b"):
        assert re.search(verb, PRE_FIX_STATUS), verb
    assert not HOME_PATH.search(PRE_FIX_STATUS)
    assert not INFRA_SECRET.search(ALLOWED), INFRA_SECRET.findall(ALLOWED)


def test_acceptance_start_reads_one_operator_password_and_embeds_no_secret():
    source = ACCEPT_START.read_text(encoding="utf-8")
    assert not re.search(r"\$\{POSTGRES_PASSWORD:-[^}]", source), "a default password was invented"
    assert "POSTGRES_PASSWORD is not exported" in source
    assert "JWT_SECRET_KEY is not exported" in source
    assert len(re.findall(r'-e POSTGRES_PASSWORD="\$POSTGRES_PASSWORD"', source)) == 1
    assert re.search(r'export DATABASE_URL="postgresql://postgres:\$\{POSTGRES_PASSWORD\}',
                     source)


def test_documented_entry_points_cover_check_start_status_verify_stop():
    entries = {
        "backend suite (check)": RUN_TESTS,
        "candidate start": START,
        "candidate stop": STOP,
        "candidate status": STATUS,
        "acceptance start": ACCEPT_START,
        "acceptance stop": ACCEPT_STOP,
        "journey/verify entry": REPO_ROOT / "verify_data_persistence.sh",
    }
    for label, path in entries.items():
        assert path.is_file(), f"{label} is missing: {path}"
        assert os.access(path, os.X_OK), f"{label} is not executable: {path}"
    assert "layer: sqlite" in RUN_TESTS.read_text(encoding="utf-8")


def test_no_shell_entry_carries_a_machine_path_or_a_literal_infrastructure_secret():
    """Every .sh in the checkout is scanned, .venv and node_modules aside.

    The claim is exactly: no home-directory path, no literal password inside a
    connection URL, no literal assignment to an infrastructure credential
    variable. It does not claim more than that: a ${VAR:-default} fallback is not
    matched, and start_candidate_stack.sh:255 keeps the local pgserver default
    password, which is named in the handoff instead of quietly passing here.
    """
    pruned = {".venv", "venv", "node_modules", ".next", ".git", "__pycache__",
              "dist", "build", ".pytest_cache", ".local", "postgres"}
    scanned = 0
    offenders = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in pruned]
        for name in filenames:
            if not name.endswith(".sh"):
                continue
            path = Path(dirpath) / name
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            offenders += [f"{path.relative_to(REPO_ROOT)}: path {m.group(0)}"
                          for m in HOME_PATH.finditer(text)]
            offenders += [f"{path.relative_to(REPO_ROOT)}: secret {m.group(0)[:48]}"
                          for m in INFRA_SECRET.finditer(text)]
    assert scanned >= 25, scanned
    assert offenders == [], "\n".join(offenders)
