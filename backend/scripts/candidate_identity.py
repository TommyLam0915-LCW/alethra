#!/usr/bin/env python3
"""Persistent candidate identity decisions for start_candidate_stack.sh.

The shell script owns process lifecycle; this module owns the choices that must
never depend on today's date: which database a candidate means, whether a
resume may proceed, and whether running processes belong to that candidate.

Only the ``probe`` subcommand touches PostgreSQL, and it runs inside a
READ ONLY transaction.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

CANDIDATE_DB_PREFIX = "alethra_personal_e2e_candidate"
CANDIDATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")

PROFILE_KEYS = (
    "CANDIDATE_ID",
    "CHECKOUT_DIR",
    "DB_NAME",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "CREDENTIALS_FILE",
    "ARTIFACT_DIR",
    "LOG_DIR",
    "BACKEND_ORIGIN",
    "FRONTEND_ORIGIN",
    "BACKEND_PORT",
    "FRONTEND_PORT",
    "EXPECTED_SCHEMA_HEAD",
    "FIXTURE_OWNER_EMAIL",
    "FIXTURE_THIN_EMAIL",
    "FIXTURE_EMPTY_EMAIL",
    "CREATED_AT",
)

ROLE_MARKERS = {
    "backend": ("uvicorn", "app.main:app"),
    "worker": ("app.workers.training_worker",),
    "frontend": ("next",),
}


class CandidateIdentityError(RuntimeError):
    pass


def read_kv(path):
    values = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def load_profile(path):
    if not os.path.exists(path):
        raise CandidateIdentityError(
            f"no persistent candidate profile at {path}; "
            "run --new-candidate for an explicit new candidate, or restore the profile"
        )
    values = read_kv(path)
    missing = [key for key in PROFILE_KEYS if not values.get(key)]
    if missing:
        raise CandidateIdentityError(f"candidate profile {path} is missing: {', '.join(missing)}")
    if not values["DB_NAME"].startswith(CANDIDATE_DB_PREFIX):
        raise CandidateIdentityError(
            f"candidate profile DB_NAME must start with {CANDIDATE_DB_PREFIX}"
        )
    for origin_key, port_key in (
        ("BACKEND_ORIGIN", "BACKEND_PORT"),
        ("FRONTEND_ORIGIN", "FRONTEND_PORT"),
    ):
        parsed = urlsplit(values[origin_key])
        if parsed.scheme not in ("http", "https") or parsed.port is None:
            raise CandidateIdentityError(f"candidate profile {origin_key} is not a full http(s) origin")
        if str(parsed.port) != values[port_key]:
            raise CandidateIdentityError(
                f"candidate profile {origin_key} port does not match {port_key}"
            )
    return values


def _norm_path(value):
    return os.path.normpath(value) if value else value


def _conflicts(profile, overrides):
    checks = (
        ("CANDIDATE_DB_NAME", "DB_NAME", "database name", False),
        ("CANDIDATE_DB_HOST", "DB_HOST", "database host", False),
        ("CANDIDATE_DB_PORT", "DB_PORT", "database port", False),
        ("CANDIDATE_DB_USER", "DB_USER", "database user", False),
        ("CANDIDATE_BACKEND_PORT", "BACKEND_PORT", "backend port", False),
        ("CANDIDATE_FRONTEND_PORT", "FRONTEND_PORT", "frontend port", False),
        ("CANDIDATE_ENV_FILE", "CREDENTIALS_FILE", "credentials file", True),
        ("CANDIDATE_ARTIFACT_DIR", "ARTIFACT_DIR", "artifact directory", True),
        ("CANDIDATE_LOG_DIR", "LOG_DIR", "log directory", True),
    )
    problems = []
    for override_key, profile_key, label, is_path in checks:
        value = overrides.get(override_key) or ""
        if not value:
            continue
        expected = profile[profile_key]
        actual = _norm_path(value) if is_path else value
        wanted = _norm_path(expected) if is_path else expected
        if actual != wanted:
            problems.append(
                f"{override_key}={value!r} conflicts with the saved candidate {label} {expected!r}"
            )
    return problems


def evaluate_resume(profile, probe, overrides=None):
    problems = _conflicts(profile, overrides or {})
    if problems:
        raise CandidateIdentityError("; ".join(problems))
    if not isinstance(probe, dict):
        raise CandidateIdentityError("database probe did not return a JSON object")
    if probe.get("exists") is not True:
        raise CandidateIdentityError(
            f"candidate database {profile['DB_NAME']} does not exist; "
            "resume never creates a replacement database (use --new-candidate or --reset explicitly)"
        )
    if probe.get("db") != profile["DB_NAME"]:
        raise CandidateIdentityError(
            f"probe reached database {probe.get('db')!r}, expected {profile['DB_NAME']!r}"
        )
    if probe.get("head") != profile["EXPECTED_SCHEMA_HEAD"]:
        raise CandidateIdentityError(
            f"database schema head {probe.get('head')!r} does not match the saved candidate "
            f"head {profile['EXPECTED_SCHEMA_HEAD']!r}"
        )
    fixtures = probe.get("fixtures") or {}
    missing = [email for email in
               (profile["FIXTURE_OWNER_EMAIL"], profile["FIXTURE_THIN_EMAIL"], profile["FIXTURE_EMPTY_EMAIL"])
               if fixtures.get(email) is not True]
    if missing:
        raise CandidateIdentityError(
            "candidate database is missing expected fixture accounts: " + ", ".join(missing)
        )
    return {
        "MODE": "resume",
        "CANDIDATE_ID": profile["CANDIDATE_ID"],
        "DB_NAME": profile["DB_NAME"],
        "DB_HOST": profile["DB_HOST"],
        "DB_PORT": profile["DB_PORT"],
        "BACKEND_ORIGIN": profile["BACKEND_ORIGIN"],
        "FRONTEND_ORIGIN": profile["FRONTEND_ORIGIN"],
        "SCHEMA_HEAD": probe.get("head"),
        "SEED": "no",
    }


def database_url_dbname(url):
    if not url:
        return None
    cleaned = re.sub(r"\+\w+", "", url, count=1)
    parsed = urlsplit(cleaned)
    name = parsed.path.lstrip("/")
    return name or None


def database_url_host_port(url):
    cleaned = re.sub(r"\+\w+", "", url or "", count=1)
    parsed = urlsplit(cleaned)
    return parsed.hostname, (str(parsed.port) if parsed.port else None)


def _verify_entry(profile, entry):
    role = entry.get("role")
    pid = entry.get("pid")
    if not entry.get("alive"):
        return [f"{role} pid {pid} is not running"]
    cmdline = entry.get("cmdline") or ""
    markers = ROLE_MARKERS.get(role, ())
    if markers and not all(marker in cmdline for marker in markers):
        return [f"{role} pid {pid} does not look like a candidate {role} ({cmdline[:80]!r})"]
    problems = []
    if role in ("backend", "worker"):
        url = entry.get("database_url")
        if not url:
            return [f"{role} pid {pid} has no visible DATABASE_URL"]
        if database_url_dbname(url) != profile["DB_NAME"]:
            return [
                f"{role} pid {pid} points at database {database_url_dbname(url)!r}, "
                f"not {profile['DB_NAME']!r}"
            ]
        host, port = database_url_host_port(url)
        if host != profile["DB_HOST"] or port != profile["DB_PORT"]:
            problems.append(
                f"{role} pid {pid} points at {host}:{port}, "
                f"not {profile['DB_HOST']}:{profile['DB_PORT']}"
            )
    return problems


def verify_runtime_binding(profile, entries):
    problems = []
    seen = set()
    for entry in entries:
        role = entry.get("role")
        if not role or role in seen:
            problems.append(f"runtime entry for {role!r} is missing or duplicated")
            continue
        seen.add(role)
        problems.extend(_verify_entry(profile, entry))
    for required in ("backend", "worker"):
        if required not in seen:
            problems.append(f"no runtime identity supplied for {required}")
    if problems:
        raise CandidateIdentityError("; ".join(problems))
    return {"RUNTIME_BINDING": "verified", "ROLES": ",".join(sorted(seen))}


def check_process(profile, role, pid, ps_bin):
    if role not in ROLE_MARKERS:
        raise CandidateIdentityError(f"unknown process role {role!r}")
    entry = _process_identity(pid, ps_bin)
    entry.update({"role": role, "pid": pid})
    if not entry["alive"]:
        return {"VERIFIED": "no", "REASON": "not-running"}
    markers = ROLE_MARKERS[role]
    cmdline = entry.get("cmdline") or ""
    if not all(marker in cmdline for marker in markers):
        return {"VERIFIED": "no", "REASON": "not-this-role", "CMDLINE": cmdline[:80]}
    problems = _verify_entry(profile, entry)
    if problems:
        return {"VERIFIED": "no", "REASON": "identity-mismatch", "DETAIL": "; ".join(problems)}
    return {"VERIFIED": "yes", "ROLE": role, "PID": str(pid)}


def new_candidate_plan(candidate_id, profile_path, credentials_path, log_dir, db_name, artifact_dir):
    problems = []
    if not CANDIDATE_ID_RE.match(candidate_id or ""):
        problems.append("candidate id must be 3-41 chars of lowercase letters, digits or '-'")
    if not db_name.startswith(CANDIDATE_DB_PREFIX):
        problems.append(f"new candidate DB name must start with {CANDIDATE_DB_PREFIX}")
    for label, path in (
        ("profile", profile_path),
        ("credentials", credentials_path),
        ("log directory", log_dir),
    ):
        if os.path.exists(path):
            problems.append(f"new candidate would overwrite an existing {label}: {path}")
    if os.path.isdir(artifact_dir) and os.listdir(artifact_dir):
        problems.append(
            f"artifact directory {artifact_dir} is not empty; "
            "a new candidate needs its own checkout or an empty artifact directory"
        )
    if problems:
        raise CandidateIdentityError("; ".join(problems))
    return {"CANDIDATE_ID": candidate_id, "DB_NAME": db_name}


def write_profile(path, values):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        for key in PROFILE_KEYS:
            handle.write(f"{key}={values.get(key, '')}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def probe_database(host, port, user, password, dbname, fixture_emails):
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname=dbname, connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        if getattr(exc, "pgcode", None) == "3D000":
            return {"exists": False, "db": dbname}
        raise
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("select current_database()")
        reached = cur.fetchone()[0]
        cur.execute("select version_num from alembic_version")
        row = cur.fetchone()
        head = row[0] if row else None
        fixtures = {}
        for email in fixture_emails:
            cur.execute("select 1 from users where email = %s", (email,))
            fixtures[email] = cur.fetchone() is not None
        conn.rollback()
        return {"exists": True, "db": reached, "head": head, "fixtures": fixtures}
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed probe, never a partial success
        try:
            conn.rollback()
        finally:
            conn.close()
        return {"exists": None, "db": dbname, "error": type(exc).__name__}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _process_identity(pid, ps_bin):
    alive = False
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True
    cmdline = ""
    env_blob = ""
    if alive:
        try:
            cmdline = subprocess.check_output(
                [ps_bin, "-p", str(pid), "-o", "command="], text=True
            )
            env_blob = subprocess.check_output(
                [ps_bin, "eww", "-p", str(pid), "-o", "command="], text=True
            )
        except subprocess.CalledProcessError:
            alive = False
    database_url = None
    for token in env_blob.split():
        if token.startswith("DATABASE_URL="):
            database_url = token.split("=", 1)[1]
            break
    return {"alive": alive, "cmdline": cmdline.strip(), "database_url": database_url}


def _heartbeat_ok(path, worker_pid, max_age_seconds=60):
    if not os.path.exists(path):
        return False, "worker heartbeat file is missing"
    try:
        with open(path, encoding="utf-8") as handle:
            beat = json.load(handle)
    except (OSError, ValueError) as exc:
        return False, f"worker heartbeat is unreadable ({type(exc).__name__})"
    if str(beat.get("pid")) != str(worker_pid):
        return False, f"heartbeat pid {beat.get('pid')} does not match worker pid {worker_pid}"
    age = time.time() - os.path.getmtime(path)
    if age > max_age_seconds:
        return False, f"worker heartbeat is {age:.0f}s old"
    return True, ""


def _cmd_plan(args):
    profile = load_profile(args.profile)
    overrides = {
        "CANDIDATE_DB_NAME": args.db_name,
        "CANDIDATE_DB_HOST": args.db_host,
        "CANDIDATE_DB_PORT": args.db_port,
        "CANDIDATE_DB_USER": args.db_user,
        "CANDIDATE_BACKEND_PORT": args.backend_port,
        "CANDIDATE_FRONTEND_PORT": args.frontend_port,
        "CANDIDATE_ENV_FILE": args.credentials_file,
        "CANDIDATE_ARTIFACT_DIR": args.artifact_dir,
        "CANDIDATE_LOG_DIR": args.log_dir,
    }
    payload = sys.stdin.read() if args.probe_json == "-" else open(args.probe_json, encoding="utf-8").read()
    try:
        probe = json.loads(payload or "{}")
    except ValueError as exc:
        raise CandidateIdentityError(f"database probe output is not JSON: {exc}")
    plan = evaluate_resume(profile, probe, overrides)
    for key, value in plan.items():
        print(f"{key}={value}")


def _cmd_probe(args):
    emails = [item for item in args.fixture_email]
    result = probe_database(
        args.host, args.port, args.user, os.environ.get("CANDIDATE_DB_PASSWORD", ""),
        args.dbname, emails,
    )
    print(json.dumps(result))
    if result.get("exists") is None:
        raise SystemExit(2)


def _cmd_verify_runtime(args):
    profile = load_profile(args.profile)
    ps_bin = os.environ.get("CANDIDATE_PS_BIN", "/bin/ps")
    entries = []
    for role, pid in (("backend", args.backend_pid), ("worker", args.worker_pid)):
        if not pid:
            raise CandidateIdentityError(f"{role} pid is empty")
        entry = _process_identity(int(pid), ps_bin)
        entry.update({"role": role, "pid": int(pid)})
        entries.append(entry)
    if args.frontend_pid:
        entry = _process_identity(int(args.frontend_pid), ps_bin)
        entry.update({"role": "frontend", "pid": int(args.frontend_pid)})
        entries.append(entry)
    result = verify_runtime_binding(profile, entries)
    ok, reason = _heartbeat_ok(args.heartbeat, args.worker_pid)
    if not ok:
        raise CandidateIdentityError(reason)
    for key, value in result.items():
        print(f"{key}={value}")


def _cmd_check_process(args):
    profile = load_profile(args.profile)
    ps_bin = os.environ.get("CANDIDATE_PS_BIN", "/bin/ps")
    result = check_process(profile, args.role, int(args.pid), ps_bin)
    for key, value in result.items():
        print(f"{key}={value}")


def _cmd_new_check(args):
    if os.path.exists(args.profile):
        raise CandidateIdentityError(f"new candidate would overwrite an existing profile: {args.profile}")
    result = new_candidate_plan(
        args.candidate_id, args.profile, args.credentials, args.log_dir,
        args.db_name, args.artifact_dir,
    )
    for key, value in result.items():
        print(f"{key}={value}")


def _cmd_write_profile(args):
    values = read_kv(args.values_file)
    write_profile(args.path, values)
    print(f"PROFILE={args.path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--profile", required=True)
    plan.add_argument("--probe-json", required=True)
    for flag in ("db-name", "db-host", "db-port", "db-user", "backend-port",
                 "frontend-port", "credentials-file", "artifact-dir", "log-dir"):
        plan.add_argument(f"--{flag}", default="")
    plan.set_defaults(func=_cmd_plan)

    probe = sub.add_parser("probe")
    probe.add_argument("--host", required=True)
    probe.add_argument("--port", required=True)
    probe.add_argument("--user", required=True)
    probe.add_argument("--dbname", required=True)
    probe.add_argument("--fixture-email", action="append", default=[])
    probe.set_defaults(func=_cmd_probe)

    runtime = sub.add_parser("verify-runtime")
    runtime.add_argument("--profile", required=True)
    runtime.add_argument("--backend-pid", required=True)
    runtime.add_argument("--worker-pid", required=True)
    runtime.add_argument("--frontend-pid", default="")
    runtime.add_argument("--heartbeat", required=True)
    runtime.set_defaults(func=_cmd_verify_runtime)

    check = sub.add_parser("check-process")
    check.add_argument("--profile", required=True)
    check.add_argument("--role", required=True, choices=("backend", "worker", "frontend"))
    check.add_argument("--pid", required=True)
    check.set_defaults(func=_cmd_check_process)

    new_check = sub.add_parser("new-check")
    new_check.add_argument("--candidate-id", required=True)
    new_check.add_argument("--profile", required=True)
    new_check.add_argument("--credentials", required=True)
    new_check.add_argument("--log-dir", required=True)
    new_check.add_argument("--db-name", required=True)
    new_check.add_argument("--artifact-dir", required=True)
    new_check.set_defaults(func=_cmd_new_check)

    write = sub.add_parser("write-profile")
    write.add_argument("--path", required=True)
    write.add_argument("--values-file", required=True)
    write.set_defaults(func=_cmd_write_profile)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except CandidateIdentityError as exc:
        print(f"CANDIDATE IDENTITY ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
