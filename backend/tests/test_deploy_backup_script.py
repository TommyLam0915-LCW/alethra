"""deploy/backup.sh correctness with a fake docker CLI.

These tests never contact Docker or PostgreSQL: a stub on PATH answers the
compose service list, the target identity query and pg_dump. They pin the
failure modes that used to be silent (partial file left behind, wrong target,
truncated dump, timeout).
"""
import gzip
import os
import shutil
import subprocess
from pathlib import Path

BACKUP = Path(__file__).resolve().parents[2] / "deploy" / "backup.sh"

TRAILER = "-- PostgreSQL database dump complete\n"

FAKE_DOCKER = """#!/usr/bin/env bash
set -u
[ "${1:-}" = "compose" ] || { echo "fake docker: unexpected command: $*" >&2; exit 90; }
shift 3   # compose -f <file>
cmd="${1:-}"; shift || true
[ -n "${FAKE_LOG:-}" ] && echo "$cmd $*" >> "$FAKE_LOG"
case "$cmd" in
  ps)
    printf '%s\\n' ${FAKE_SERVICES:-db}
    ;;
  exec)
    [ "${1:-}" = "-T" ] && shift
    shift   # service name
    tool="${1:-}"; shift || true
    case "$tool" in
      psql)
        if [ -n "${FAKE_PSQL_ERR:-}" ]; then printf '%s\\n' "$FAKE_PSQL_ERR" >&2; fi
        printf '%s\\n' "${FAKE_IDENTITY:-alethra|160004}"
        exit "${FAKE_PSQL_EXIT:-0}"
        ;;
      pg_dump)
        if [ -n "${FAKE_PGDUMP_SLEEP:-}" ]; then exec sleep "$FAKE_PGDUMP_SLEEP"; fi
        if [ -n "${FAKE_PGDUMP_ERR:-}" ]; then printf '%s\\n' "$FAKE_PGDUMP_ERR" >&2; fi
        [ -n "${FAKE_PGDUMP_FILE:-}" ] && cat "$FAKE_PGDUMP_FILE"
        exit "${FAKE_PGDUMP_EXIT:-0}"
        ;;
      *)
        echo "fake docker: unknown tool $tool" >&2
        exit 91
        ;;
    esac
    ;;
  *)
    echo "fake docker: unknown compose command $cmd" >&2
    exit 92
    ;;
esac
"""


def _project(tmp_path, dump_text="-- header\n" + TRAILER):
    root = tmp_path / "project"
    (root / "deploy").mkdir(parents=True)
    shutil.copy2(BACKUP, root / "deploy" / "backup.sh")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    docker = fakebin / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    dump = tmp_path / "dump.sql"
    dump.write_text(dump_text, encoding="utf-8")
    log = tmp_path / "docker.log"
    log.write_text("", encoding="utf-8")
    return root, fakebin, dump, log


def _run(root, fakebin, dump, log, extra=None):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("BACKUP_", "POSTGRES_", "FAKE_"))
    }
    env.update({
        "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
        "BACKUP_DIR": str(root / "backups"),
        "BACKUP_DB_NAME": "alethra",
        "BACKUP_DB_USER": "alethra",
        "BACKUP_TIMEOUT_SECONDS": "10",
        "FAKE_PGDUMP_FILE": str(dump),
        "FAKE_LOG": str(log),
    })
    env.update(extra or {})
    return subprocess.run(
        ["bash", str(root / "deploy" / "backup.sh")],
        cwd=str(root), env=env, text=True, capture_output=True, timeout=120,
    )


def _backups(root):
    return sorted((root / "backups").glob("alethra_db_*.sql.gz"))


def _leftovers(root):
    return sorted((root / "backups").glob(".backup-work.*")) + sorted(
        (root / "backups").glob("*.partial")
    )


def test_backup_publishes_only_a_verified_archive(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log)

    assert result.returncode == 0, result.stdout + result.stderr
    backups = _backups(root)
    assert len(backups) == 1, result.stdout
    assert gzip.decompress(backups[0].read_bytes()).decode() == dump.read_text(encoding="utf-8")
    assert (backups[0].stat().st_mode & 0o777) == 0o600
    assert _leftovers(root) == []
    assert "Target verified: database alethra, PostgreSQL 16" in result.stdout
    assert "psql -v ON_ERROR_STOP=1 -U alethra alethra" in result.stdout
    assert "pruning disabled" in result.stdout


def test_backup_refuses_a_database_that_is_not_the_requested_one(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {"FAKE_IDENTITY": "other_db|160004"})

    assert result.returncode == 2, result.stdout + result.stderr
    assert "refusing to back up an unexpected target" in result.stderr
    assert _backups(root) == []
    assert "pg_dump" not in log.read_text(encoding="utf-8")


def test_backup_refuses_when_the_service_is_not_running(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {"FAKE_SERVICES": "backend worker"})

    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not running" in result.stderr
    assert _backups(root) == []


def test_backup_masks_connection_details_from_target_errors(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {
        "FAKE_PSQL_EXIT": "1",
        "FAKE_PSQL_ERR": "could not connect: postgresql://alethra:s3cret@db:5432/alethra PGPASSWORD=s3cret",
    })

    combined = result.stdout + result.stderr
    assert result.returncode == 2, combined
    assert "could not query database alethra" in result.stderr
    assert "s3cret" not in combined
    assert "***" in result.stderr


def test_backup_failure_leaves_no_file_behind(tmp_path):
    root, fakebin, dump, log = _project(tmp_path, dump_text="-- half a dump\n")

    result = _run(root, fakebin, dump, log, {
        "FAKE_PGDUMP_EXIT": "1",
        "FAKE_PGDUMP_ERR": "could not write to output file",
    })

    assert result.returncode == 3, result.stdout + result.stderr
    assert "no backup file was written" in result.stderr
    assert _backups(root) == []
    assert _leftovers(root) == []
    assert "Backup complete" not in result.stdout


def test_backup_refuses_a_dump_without_its_completion_line(tmp_path):
    root, fakebin, dump, log = _project(tmp_path, dump_text="-- header only\n")

    result = _run(root, fakebin, dump, log)

    assert result.returncode == 4, result.stdout + result.stderr
    assert "no completion line" in result.stderr
    assert _backups(root) == []
    assert _leftovers(root) == []


def test_backup_times_out_instead_of_publishing_a_stalled_dump(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {
        "FAKE_PGDUMP_SLEEP": "20",
        "BACKUP_TIMEOUT_SECONDS": "1",
    })

    assert result.returncode == 3, result.stdout + result.stderr
    assert "timed out after 1s" in result.stderr
    assert _backups(root) == []


def test_backup_requires_an_explicit_database_name(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {"BACKUP_DB_NAME": ""})

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BACKUP_DB_NAME is required" in result.stderr
    assert _backups(root) == []


def test_backup_never_falls_back_to_postgres_env_defaults(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {
        "BACKUP_DB_NAME": "",
        "POSTGRES_DB": "alethra",
        "POSTGRES_USER": "alethra",
    })

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BACKUP_DB_NAME is required" in result.stderr
    assert "pg_dump" not in log.read_text(encoding="utf-8")
    assert _backups(root) == []


def test_backup_refuses_an_unrecognised_prune_setting(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)

    result = _run(root, fakebin, dump, log, {"BACKUP_PRUNE": "yes"})

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BACKUP_PRUNE must be 0" in result.stderr
    assert _backups(root) == []


def test_backup_keeps_every_archive_by_default(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)
    backup_dir = root / "backups"
    backup_dir.mkdir()
    old_files = []
    for index in range(8):
        path = backup_dir / f"alethra_db_2026010{index}_000000.sql.gz"
        path.write_bytes(gzip.compress(b"-- old\n" + TRAILER.encode()))
        os.utime(path, (1_000_000 + index, 1_000_000 + index))
        old_files.append(path)

    result = _run(root, fakebin, dump, log)

    assert result.returncode == 0, result.stdout + result.stderr
    remaining = _backups(root)
    assert len(remaining) == 9, [path.name for path in remaining]
    for path in old_files:
        assert path in remaining
    assert "pruning disabled" in result.stdout


def test_backup_prunes_old_rotations_only_when_explicitly_enabled(tmp_path):
    root, fakebin, dump, log = _project(tmp_path)
    backup_dir = root / "backups"
    backup_dir.mkdir()
    old_files = []
    for index in range(8):
        path = backup_dir / f"alethra_db_2026010{index}_000000.sql.gz"
        path.write_bytes(gzip.compress(b"-- old\n" + TRAILER.encode()))
        os.utime(path, (1_000_000 + index, 1_000_000 + index))
        old_files.append(path)

    result = _run(root, fakebin, dump, log, {"BACKUP_PRUNE": "1"})

    assert result.returncode == 0, result.stdout + result.stderr
    remaining = _backups(root)
    assert len(remaining) == 7, [path.name for path in remaining]
    assert old_files[0] not in remaining
    assert old_files[1] not in remaining
    assert old_files[-1] in remaining
