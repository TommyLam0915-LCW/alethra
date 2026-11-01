"""Importing the app must not create or alter schema.

Until 2026-11-01 ``app/main.py`` ran ``Base.metadata.create_all`` at import
time. create_all adds a missing table but never alters an existing one, so a
database behind the code ended up half-migrated: the newer tables appeared
while the missing column stayed missing and ``alembic_version`` stayed behind.
Schema is owned by Alembic; startup is read-only.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _import_app_main(database_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["APP_ENV"] = "development"
    env["AI_PROVIDER"] = "mock"
    env["DATA_PROVIDER"] = "demo"
    env["CANDIDATE_PASSWORDLESS_MODE"] = "false"
    env.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [sys.executable, "-c", "import app.main; print('imported')"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _sqlite_tables(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def test_importing_app_main_creates_no_tables(tmp_path):
    db_path = tmp_path / "startup-schema.db"
    result = _import_app_main(f"sqlite:///{db_path}")

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
    assert _sqlite_tables(db_path) == []


def test_importing_app_main_does_not_add_columns_to_existing_tables(tmp_path):
    """A column-less table must stay column-less: no implicit ALTER at import."""
    db_path = tmp_path / "startup-schema-existing.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE market_data_bars (id VARCHAR(36) PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    result = _import_app_main(f"sqlite:///{db_path}")

    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(db_path)
    try:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(market_data_bars)").fetchall()
        ]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert columns == ["id"]
    assert tables == ["market_data_bars"]
