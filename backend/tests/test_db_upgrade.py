"""Tests for legacy database upgrade service.

Covers: schema detection, preflight checks, backup/restore,
upgrade execution, rollback, idempotency, data preservation,
error handling, and edge cases.
"""

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.db_upgrade import (
    UpgradeError,
    UpgradeResult,
    create_backup,
    detect_column_types,
    detect_schema_version,
    get_upgrade_status,
    is_upgrade_needed,
    preflight_check,
    restore_backup,
    run_rollback,
    run_upgrade,
    SchemaVersion,
    UnsupportedLegacyVersionError,
    PreflightError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def legacy_v0_db(tmp_path):
    """Create a legacy v0 database (core tables only)."""
    db_path = str(tmp_path / "legacy_v0.db")
    conn = sqlite3.connect(db_path)
    try:
        # users table with TEXT UUIDs (legacy style)
        conn.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                user_type TEXT DEFAULT 'individual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                is_admin INTEGER DEFAULT 0
            )
        """)
        # datasets table
        conn.execute("""
            CREATE TABLE datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                source_type TEXT DEFAULT 'demo',
                license_status TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        # experiments table with TEXT JSON
        conn.execute("""
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                workspace_id TEXT,
                title TEXT NOT NULL,
                original_idea TEXT,
                research_type TEXT DEFAULT 'quantitative',
                hypothesis TEXT,
                falsification_conditions TEXT,
                benchmark TEXT,
                scope TEXT,
                status TEXT DEFAULT 'draft',
                visibility TEXT DEFAULT 'private',
                current_version_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)
        # Insert test data
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "test@example.com", "hashed_password", "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        )
        dataset_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO datasets (id, name, provider, created_at) VALUES (?, ?, ?, ?)",
            (dataset_id, "Test Dataset", "demo", "2026-01-01T00:00:00")
        )
        exp_id = str(uuid.uuid4())
        scope_json = json.dumps({"asset_universe": ["AAPL", "GOOG"], "benchmark": "SPY"})
        conn.execute(
            "INSERT INTO experiments (id, owner_id, title, scope, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (exp_id, user_id, "Test Experiment", scope_json, "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def legacy_v1_db(tmp_path):
    """Create a legacy v1 database (extended tables)."""
    db_path = str(tmp_path / "legacy_v1.db")
    conn = sqlite3.connect(db_path)
    try:
        # Same as v0 plus workspaces and training_jobs
        conn.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                user_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                is_admin INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE datasets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                source_type TEXT,
                license_status TEXT,
                description TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                workspace_id TEXT,
                title TEXT NOT NULL,
                original_idea TEXT,
                research_type TEXT,
                hypothesis TEXT,
                falsification_conditions TEXT,
                benchmark TEXT,
                scope TEXT,
                status TEXT,
                visibility TEXT,
                current_version_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE training_jobs (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                experiment_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                config_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Insert test data
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, "v1@example.com", "hashed", "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        )
        workspace_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO workspaces (id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
            (workspace_id, user_id, "Test Workspace", "2026-01-01T00:00:00")
        )
        job_id = str(uuid.uuid4())
        config_json = json.dumps({"learning_rate": 0.001, "epochs": 100})
        conn.execute(
            "INSERT INTO training_jobs (id, owner_id, title, status, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, user_id, "ML Training", "completed", config_json,
             "2026-01-01T00:00:00", "2026-01-02T00:00:00")
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def current_db(tmp_path):
    """Create a database with current schema (alembic_version table)."""
    from app.services.db_upgrade import CURRENT_SCHEMA_REVISION
    db_path = str(tmp_path / "current.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE alembic_version (
                version_num TEXT NOT NULL PRIMARY KEY
            )
        """)
        conn.execute("INSERT INTO alembic_version VALUES (?)", (CURRENT_SCHEMA_REVISION,))
        conn.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Add all current tables
        for table in ["datasets", "experiments", "workspaces", "training_jobs",
                       "experiment_versions", "model_research_records",
                       "market_data_bars", "market_data_metadata"]:
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    """Create an empty database."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.close()
    return db_path


@pytest.fixture
def backup_dir(tmp_path):
    """Create a temporary backup directory."""
    d = str(tmp_path / "backups")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Test: Schema Detection
# ---------------------------------------------------------------------------

class TestSchemaDetection:
    """Tests for schema version detection."""

    def test_detect_legacy_v0(self, legacy_v0_db):
        """Detect legacy v0 schema (core tables only)."""
        version = detect_schema_version(legacy_v0_db)
        assert version == SchemaVersion.LEGACY_V0

    def test_detect_legacy_v1(self, legacy_v1_db):
        """Detect legacy v1 schema (extended tables)."""
        version = detect_schema_version(legacy_v1_db)
        assert version == SchemaVersion.LEGACY_V1

    def test_detect_current_schema(self, current_db):
        """Detect current schema with alembic_version."""
        version = detect_schema_version(current_db)
        assert version == SchemaVersion.CURRENT

    def test_detect_empty_db_unknown(self, empty_db):
        """Empty database returns UNKNOWN."""
        version = detect_schema_version(empty_db)
        assert version == SchemaVersion.UNKNOWN

    def test_alembic_table_alone_is_not_current(self, tmp_path):
        """Regression: alembic_version table existing does NOT mean CURRENT.

        The directive requires checking the stamped revision AND the actual
        schema — not just table existence.
        """
        db_path = str(tmp_path / "alembic_only.db")
        from app.services.db_upgrade import CURRENT_SCHEMA_REVISION
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL PRIMARY KEY)")
            conn.execute(
                "INSERT INTO alembic_version VALUES (?)", (CURRENT_SCHEMA_REVISION,)
            )
            conn.commit()
        finally:
            conn.close()

        assert detect_schema_version(db_path) == SchemaVersion.UNKNOWN

    def test_wrong_revision_is_not_current(self, current_db):
        """Current tables with an unexpected stamped revision are UNKNOWN."""
        conn = sqlite3.connect(current_db)
        try:
            conn.execute("UPDATE alembic_version SET version_num = 'a1b2c3d4e5f6'")
            conn.commit()
        finally:
            conn.close()
        assert detect_schema_version(current_db) == SchemaVersion.UNKNOWN

    def test_preflight_reports_why_alembic_db_is_unknown(self, current_db):
        """Preflight surfaces revision mismatch / missing tables detail."""
        conn = sqlite3.connect(current_db)
        try:
            conn.execute("DROP TABLE market_data_bars")
            conn.execute("UPDATE alembic_version SET version_num = 'deadbeef0000'")
            conn.commit()
        finally:
            conn.close()

        report = preflight_check(current_db)
        assert report["schema_version"] == "unknown"
        joined = "; ".join(report["errors"])
        assert "revision='deadbeef0000'" in joined
        assert "market_data_bars" in joined

    def test_detect_nonexistent_db_raises(self, tmp_db):
        """Non-existent database raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            detect_schema_version(tmp_db)

    def test_detect_column_types(self, legacy_v0_db):
        """Column type inspection returns correct types."""
        col_types = detect_column_types(legacy_v0_db)
        assert "users" in col_types
        assert "id" in col_types["users"]
        assert col_types["users"]["id"] == "TEXT"
        assert col_types["users"]["email"] == "TEXT"


# ---------------------------------------------------------------------------
# Test: Preflight Checks
# ---------------------------------------------------------------------------

class TestPreflightCheck:
    """Tests for preflight check functionality."""

    def test_preflight_legacy_v0_supported(self, legacy_v0_db):
        """Preflight identifies legacy v0 as supported."""
        report = preflight_check(legacy_v0_db)
        assert report["schema_version"] == "legacy_v0"
        assert report["is_supported"] is True
        assert len(report["errors"]) == 0

    def test_preflight_legacy_v1_supported(self, legacy_v1_db):
        """Preflight identifies legacy v1 as supported."""
        report = preflight_check(legacy_v1_db)
        assert report["schema_version"] == "legacy_v1"
        assert report["is_supported"] is True

    def test_preflight_current_no_upgrade_needed(self, current_db):
        """Preflight on current schema reports no upgrade needed."""
        report = preflight_check(current_db)
        assert report["schema_version"] == "current"
        assert report["is_supported"] is False  # not a legacy version
        assert any("already current" in w for w in report["warnings"])

    def test_preflight_unknown_has_errors(self, empty_db):
        """Preflight on unknown schema produces errors."""
        report = preflight_check(empty_db)
        assert report["schema_version"] == "unknown"
        assert report["is_supported"] is False
        assert len(report["errors"]) > 0

    def test_preflight_nonexistent_db_has_errors(self, tmp_db):
        """Preflight on missing database reports error."""
        report = preflight_check(tmp_db)
        assert report["errors"]
        assert "not found" in report["errors"][0].lower() or "No such file" in report["errors"][0]

    def test_preflight_reports_legacy_uuid_warnings(self, legacy_v0_db):
        """Preflight warns about TEXT UUID columns."""
        report = preflight_check(legacy_v0_db)
        uuid_warnings = [w for w in report["warnings"] if "UUID" in w]
        assert len(uuid_warnings) > 0

    def test_preflight_report_has_timestamp(self, legacy_v0_db):
        """Preflight report includes timestamp."""
        report = preflight_check(legacy_v0_db)
        assert "timestamp" in report
        # Verify it's valid ISO format
        datetime.fromisoformat(report["timestamp"])


# ---------------------------------------------------------------------------
# Test: Backup and Restore
# ---------------------------------------------------------------------------

class TestBackupRestore:
    """Tests for backup and restore functionality."""

    def test_create_backup(self, legacy_v0_db, backup_dir):
        """Backup creates a valid copy of the database."""
        backup_path = create_backup(legacy_v0_db, backup_dir)
        assert os.path.exists(backup_path)
        assert backup_path.endswith(".db")

    def test_backup_integrity(self, legacy_v0_db, backup_dir):
        """Backup file passes SQLite integrity check."""
        backup_path = create_backup(legacy_v0_db, backup_dir)
        conn = sqlite3.connect(backup_path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            assert result[0] == "ok"
        finally:
            conn.close()

    def test_backup_metadata_file(self, legacy_v0_db, backup_dir):
        """Backup creates a metadata JSON file."""
        backup_path = create_backup(legacy_v0_db, backup_dir)
        meta_path = backup_path + ".meta.json"
        assert os.path.exists(meta_path)
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["source"] == legacy_v0_db
        assert meta["schema_version"] == "legacy_v0"
        assert "timestamp" in meta
        assert "checksum" in meta

    def test_backup_nonexistent_raises(self, tmp_db, backup_dir):
        """Backup of non-existent database raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            create_backup(tmp_db, backup_dir)

    def test_backup_default_dir(self, legacy_v0_db):
        """Backup works with default (temp) directory."""
        backup_path = create_backup(legacy_v0_db)
        assert os.path.exists(backup_path)
        # Cleanup temp dir
        os.unlink(backup_path)
        meta_path = backup_path + ".meta.json"
        if os.path.exists(meta_path):
            os.unlink(meta_path)

    def test_restore_backup(self, legacy_v0_db, backup_dir):
        """Restore recovers database from backup."""
        backup_path = create_backup(legacy_v0_db, backup_dir)

        # Modify the original
        conn = sqlite3.connect(legacy_v0_db)
        conn.execute("DELETE FROM users")
        conn.commit()
        conn.close()

        # Verify data is gone
        conn = sqlite3.connect(legacy_v0_db)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        assert count == 0

        # Restore
        restore_backup(backup_path, legacy_v0_db)

        # Verify data is back
        conn = sqlite3.connect(legacy_v0_db)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        assert count == 1

    def test_restore_nonexistent_backup_raises(self, tmp_db):
        """Restore of non-existent backup raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            restore_backup("/nonexistent/backup.db", tmp_db)

    def test_backup_preserves_all_data(self, legacy_v1_db, backup_dir):
        """Backup preserves all tables and data."""
        backup_path = create_backup(legacy_v1_db, backup_dir)

        # Compare table counts
        orig_conn = sqlite3.connect(legacy_v1_db)
        backup_conn = sqlite3.connect(backup_path)
        try:
            orig_tables = orig_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            backup_tables = backup_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            assert len(orig_tables) == len(backup_tables)

            # Compare row counts
            for table_row in orig_tables:
                table = table_row[0]
                orig_count = orig_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                backup_count = backup_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert orig_count == backup_count, f"Row count mismatch in {table}"
        finally:
            orig_conn.close()
            backup_conn.close()


# ---------------------------------------------------------------------------
# Test: Upgrade Execution
# ---------------------------------------------------------------------------

class TestUpgradeExecution:
    """Tests for the main upgrade process."""

    def test_upgrade_legacy_v0_succeeds(self, legacy_v0_db, backup_dir):
        """Upgrade legacy v0 to current succeeds."""
        result = run_upgrade(legacy_v0_db, backup_dir)
        assert result.success is True
        assert result.backup_path is not None
        assert len(result.tables_migrated) > 0

    def test_upgrade_legacy_v1_succeeds(self, legacy_v1_db, backup_dir):
        """Upgrade legacy v1 to current succeeds."""
        result = run_upgrade(legacy_v1_db, backup_dir)
        assert result.success is True
        assert result.backup_path is not None

    def test_upgrade_current_schema_noop(self, current_db, backup_dir):
        """Upgrade on current schema returns no-op result."""
        result = run_upgrade(current_db, backup_dir)
        assert result.success is True
        assert "already current" in result.message.lower()
        assert result.backup_path is None

    def test_upgrade_dry_run(self, legacy_v0_db, backup_dir):
        """Dry run reports changes without modifying database."""
        result = run_upgrade(legacy_v0_db, backup_dir, dry_run=True)
        assert result.success is True
        assert "dry run" in result.message.lower()
        # Database should not have alembic_version after dry run
        version = detect_schema_version(legacy_v0_db)
        assert version == SchemaVersion.LEGACY_V0

    def test_upgrade_preserves_user_data(self, legacy_v0_db, backup_dir):
        """Upgrade preserves all user data."""
        # Get pre-upgrade data
        conn = sqlite3.connect(legacy_v0_db)
        pre_users = conn.execute("SELECT id, email FROM users").fetchall()
        pre_experiments = conn.execute("SELECT id, title, scope FROM experiments").fetchall()
        conn.close()

        result = run_upgrade(legacy_v0_db, backup_dir)
        assert result.success is True

        # Verify data preserved
        conn = sqlite3.connect(legacy_v0_db)
        post_users = conn.execute("SELECT id, email FROM users").fetchall()
        post_experiments = conn.execute("SELECT id, title, scope FROM experiments").fetchall()
        conn.close()

        assert pre_users == post_users
        assert pre_experiments == post_experiments

    def test_upgrade_preserves_training_data(self, legacy_v1_db, backup_dir):
        """Upgrade preserves training job data."""
        conn = sqlite3.connect(legacy_v1_db)
        pre_jobs = conn.execute("SELECT id, title, status, config_json FROM training_jobs").fetchall()
        conn.close()

        result = run_upgrade(legacy_v1_db, backup_dir)
        assert result.success is True

        conn = sqlite3.connect(legacy_v1_db)
        post_jobs = conn.execute("SELECT id, title, status, config_json FROM training_jobs").fetchall()
        conn.close()

        assert pre_jobs == post_jobs

    def test_upgrade_creates_alembic_version(self, legacy_v0_db, backup_dir):
        """Upgrade creates alembic_version table."""
        run_upgrade(legacy_v0_db, backup_dir)
        version = detect_schema_version(legacy_v0_db)
        assert version == SchemaVersion.CURRENT

    def test_upgrade_v0_adds_missing_tables(self, legacy_v0_db, backup_dir):
        """Upgrade from v0 adds workspaces and training_jobs tables."""
        run_upgrade(legacy_v0_db, backup_dir)

        conn = sqlite3.connect(legacy_v0_db)
        tables = set()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        for row in cursor.fetchall():
            tables.add(row[0])
        conn.close()

        assert "workspaces" in tables
        assert "training_jobs" in tables

    def test_upgrade_to_dict(self, legacy_v0_db, backup_dir):
        """UpgradeResult can be serialized to dict."""
        result = run_upgrade(legacy_v0_db, backup_dir)
        d = result.to_dict()
        assert d["success"] is True
        assert "message" in d
        assert "timestamp" in d
        assert "tables_migrated" in d

    def test_upgrade_ensures_full_current_table_set(self, legacy_v1_db, backup_dir):
        """Upgrade ensures every CURRENT_MIN_TABLES table exists before stamping.

        Regression: the stamp claims the current revision; the actual table
        set must back that claim so post-upgrade detection (revision +
        schema) classifies the database as current, not unknown.
        """
        result = run_upgrade(legacy_v1_db, backup_dir)
        assert result.success is True

        conn = sqlite3.connect(legacy_v1_db)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            revision = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()[0]
        finally:
            conn.close()

        from app.services.db_upgrade import CURRENT_MIN_TABLES, CURRENT_SCHEMA_REVISION
        missing = CURRENT_MIN_TABLES - tables
        assert not missing, f"Upgrade did not create: {sorted(missing)}"
        assert revision == CURRENT_SCHEMA_REVISION
        assert detect_schema_version(legacy_v1_db) == SchemaVersion.CURRENT


# ---------------------------------------------------------------------------
# Test: Fixture-validated upgrade + restore roundtrip (directive item 2)
# ---------------------------------------------------------------------------

class TestUpgradeRestoreRoundtrip:
    """Full upgrade → verify → restore → verify with a representative
    legacy fixture. Uses only temporary fixture files; never touches the
    operational database."""

    @pytest.fixture
    def representative_v1_db(self, tmp_path):
        """Representative legacy v1 fixture with data in all 5 tables."""
        db_path = str(tmp_path / "representative_v1.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, display_name TEXT,
                    user_type TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1, is_admin INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL,
                    source_type TEXT, license_status TEXT, description TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE experiments (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, workspace_id TEXT,
                    title TEXT NOT NULL, original_idea TEXT, research_type TEXT,
                    hypothesis TEXT, falsification_conditions TEXT, benchmark TEXT,
                    scope TEXT, status TEXT, visibility TEXT, current_version_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY (owner_id) REFERENCES users(id)
                )
            """)
            conn.execute("""
                CREATE TABLE workspaces (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (owner_id) REFERENCES users(id)
                )
            """)
            conn.execute("""
                CREATE TABLE training_jobs (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, experiment_id TEXT,
                    title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    config_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY (owner_id) REFERENCES users(id)
                )
            """)
            for i in range(3):
                uid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO users (id, email, password_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (uid, f"user{i}@example.com", "hashed", "2026-01-01T00:00:00",
                     "2026-01-01T00:00:00"),
                )
                wid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO workspaces (id, owner_id, name, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (wid, uid, f"Workspace {i}", "2026-01-01T00:00:00"),
                )
                did = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO datasets (id, name, provider, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (did, f"Dataset {i}", "demo", "2026-01-01T00:00:00"),
                )
                eid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO experiments (id, owner_id, title, scope, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, uid, f"Experiment {i}",
                     json.dumps({"asset_universe": ["AAPL"]}),
                     "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
                )
                jid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO training_jobs (id, owner_id, title, status, config_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (jid, uid, f"Job {i}", "completed",
                     json.dumps({"epochs": 10 + i}),
                     "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
                )
            conn.commit()
        finally:
            conn.close()
        return db_path

    def _snapshot(self, db_path):
        conn = sqlite3.connect(db_path)
        try:
            tables = sorted(
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            )
            rows = {}
            for t in tables:
                rows[t] = sorted(
                    conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr
                )
            return tables, rows
        finally:
            conn.close()

    def test_full_upgrade_then_restore_roundtrip(self, representative_v1_db, backup_dir):
        pre_tables, pre_rows = self._snapshot(representative_v1_db)
        assert pre_tables == sorted(["users", "datasets", "experiments",
                                     "workspaces", "training_jobs"])
        assert "users" in pre_rows and len(pre_rows["users"]) == 3

        # Upgrade
        result = run_upgrade(representative_v1_db, backup_dir)
        assert result.success is True, result.errors

        post_tables, post_rows = self._snapshot(representative_v1_db)
        # Legacy data preserved row-for-row
        for t in pre_tables:
            assert post_rows[t] == pre_rows[t], f"Data changed in {t}"
        # Current tables + marker present; detection now CURRENT
        from app.services.db_upgrade import CURRENT_MIN_TABLES, CURRENT_SCHEMA_REVISION
        assert CURRENT_MIN_TABLES.issubset(set(post_tables))
        conn = sqlite3.connect(representative_v1_db)
        try:
            revision = conn.execute(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).fetchone()[0]
        finally:
            conn.close()
        assert revision == CURRENT_SCHEMA_REVISION
        assert detect_schema_version(representative_v1_db) == SchemaVersion.CURRENT

        # Restore from the backup the upgrade created
        restore_backup(result.backup_path, representative_v1_db)

        restored_tables, restored_rows = self._snapshot(representative_v1_db)
        assert restored_tables == pre_tables
        for t in pre_tables:
            assert restored_rows[t] == pre_rows[t], f"Restore changed data in {t}"
        assert "alembic_version" not in restored_tables
        assert detect_schema_version(representative_v1_db) == SchemaVersion.LEGACY_V1

    def test_invalid_uuid_is_blocking_and_restores(self, representative_v1_db, backup_dir):
        """Non-UUID values in UUID columns must fail the upgrade (not succeed
        with errors), and the database must be restored to its pre-upgrade state."""
        conn = sqlite3.connect(representative_v1_db)
        try:
            conn.execute(
                "UPDATE users SET id = 'not-a-uuid' WHERE email = 'user1@example.com'"
            )
            conn.commit()
        finally:
            conn.close()

        pre_tables, pre_rows = self._snapshot(representative_v1_db)

        result = run_upgrade(representative_v1_db, backup_dir)
        assert result.success is False, "Invalid UUID data must not yield success=True"
        assert any("not-a-uuid" in e for e in result.errors)
        assert result.backup_path is not None

        # Database restored: identical to pre-upgrade state, still legacy v1
        post_tables, post_rows = self._snapshot(representative_v1_db)
        assert post_tables == pre_tables
        for t in pre_tables:
            assert post_rows[t] == pre_rows[t]
        assert detect_schema_version(representative_v1_db) == SchemaVersion.LEGACY_V1

    def test_table_count_counts_tables_not_columns(self, legacy_v1_db):
        """Regression: preflight table_count must count TABLES."""
        report = preflight_check(legacy_v1_db)
        conn = sqlite3.connect(legacy_v1_db)
        try:
            n_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert report["table_count"] == n_tables
        assert report["table_count"] == 5


# ---------------------------------------------------------------------------
# Test: Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Tests for idempotent upgrade behavior."""

    def test_double_upgrade_safe(self, legacy_v0_db, backup_dir):
        """Running upgrade twice is safe."""
        result1 = run_upgrade(legacy_v0_db, backup_dir)
        assert result1.success is True

        result2 = run_upgrade(legacy_v0_db, backup_dir)
        assert result2.success is True
        assert "already current" in result2.message.lower()

    def test_is_upgrade_needed(self, legacy_v0_db):
        """is_upgrade_needed returns correct values."""
        assert is_upgrade_needed(legacy_v0_db) is True

        # After upgrade, should return False
        run_upgrade(legacy_v0_db)
        assert is_upgrade_needed(legacy_v0_db) is False

    def test_is_upgrade_needed_missing_db(self, tmp_db):
        """is_upgrade_needed returns False for missing database."""
        assert is_upgrade_needed(tmp_db) is False

    def test_get_upgrade_status_legacy(self, legacy_v0_db):
        """get_upgrade_status reports legacy version correctly."""
        status = get_upgrade_status(legacy_v0_db)
        assert status["schema_version"] == "legacy_v0"
        assert status["needs_upgrade"] is True
        assert status["is_supported"] is True

    def test_get_upgrade_status_current(self, current_db):
        """get_upgrade_status reports current version correctly."""
        status = get_upgrade_status(current_db)
        assert status["schema_version"] == "current"
        assert status["needs_upgrade"] is False
        assert status["is_supported"] is True

    def test_get_upgrade_status_missing(self, tmp_db):
        """get_upgrade_status handles missing database."""
        status = get_upgrade_status(tmp_db)
        assert status["schema_version"] == "not_found"
        assert status["needs_upgrade"] is False
        assert "error" in status


# ---------------------------------------------------------------------------
# Test: Rollback
# ---------------------------------------------------------------------------

class TestRollback:
    """Tests for rollback functionality."""

    def test_rollback_restores_original(self, legacy_v0_db, backup_dir):
        """Rollback restores the original database state."""
        # Create backup and upgrade
        result = run_upgrade(legacy_v0_db, backup_dir)
        assert result.success is True
        backup_path = result.backup_path

        # Verify upgraded state
        version = detect_schema_version(legacy_v0_db)
        assert version == SchemaVersion.CURRENT

        # Rollback
        rollback_result = run_rollback(backup_path, legacy_v0_db)
        assert rollback_result.success is True

        # Verify restored state
        version = detect_schema_version(legacy_v0_db)
        assert version == SchemaVersion.LEGACY_V0

    def test_rollback_preserves_data(self, legacy_v1_db, backup_dir):
        """Rollback preserves all original data."""
        # Get pre-upgrade data
        conn = sqlite3.connect(legacy_v1_db)
        pre_users = conn.execute("SELECT * FROM users").fetchall()
        pre_jobs = conn.execute("SELECT * FROM training_jobs").fetchall()
        conn.close()

        result = run_upgrade(legacy_v1_db, backup_dir)
        assert result.success is True

        rollback_result = run_rollback(result.backup_path, legacy_v1_db)
        assert rollback_result.success is True

        conn = sqlite3.connect(legacy_v1_db)
        post_users = conn.execute("SELECT * FROM users").fetchall()
        post_jobs = conn.execute("SELECT * FROM training_jobs").fetchall()
        conn.close()

        assert pre_users == post_users
        assert pre_jobs == post_jobs

    def test_rollback_nonexistent_backup_fails(self, tmp_db):
        """Rollback with non-existent backup fails gracefully."""
        result = run_rollback("/nonexistent/backup.db", tmp_db)
        assert result.success is False
        assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Test: Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for error handling and edge cases."""

    def test_unsupported_legacy_version_error(self, empty_db, backup_dir):
        """Unsupported legacy version raises appropriate error."""
        # Empty DB has UNKNOWN version which is unsupported
        with pytest.raises(PreflightError):
            run_upgrade(empty_db, backup_dir)

    def test_upgrade_error_hierarchy(self):
        """UpgradeError hierarchy is correct."""
        assert issubclass(UnsupportedLegacyVersionError, UpgradeError)
        assert issubclass(PreflightError, UpgradeError)

    def test_upgrade_result_serialization(self):
        """UpgradeResult serializes all fields correctly."""
        result = UpgradeResult(
            success=True,
            message="Test upgrade",
            backup_path="/tmp/backup.db",
            tables_migrated=["users", "experiments"],
            errors=[],
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["message"] == "Test upgrade"
        assert d["backup_path"] == "/tmp/backup.db"
        assert d["tables_migrated"] == ["users", "experiments"]
        assert d["errors"] == []

    def test_upgrade_with_invalid_uuid_data(self, tmp_path, backup_dir):
        """Upgrade handles invalid UUID data gracefully."""
        db_path = str(tmp_path / "bad_uuid.db")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE datasets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE experiments (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Insert a user with non-UUID id
            conn.execute(
                "INSERT INTO users (id, email, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("not-a-uuid", "test@example.com", "hash", "2026-01-01", "2026-01-01")
            )
            conn.commit()
        finally:
            conn.close()

        # Non-UUID values in key columns are BLOCKING: the upgrade must fail
        # (not succeed with errors) and restore the pre-upgrade state.
        result = run_upgrade(db_path, backup_dir)
        assert result.success is False
        uuid_errors = [e for e in result.errors if "non-UUID" in e or "UUID" in e]
        assert len(uuid_errors) > 0

        # Database restored: still legacy v0, no alembic_version stamp
        assert detect_schema_version(db_path) == SchemaVersion.LEGACY_V0
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "alembic_version" not in tables
            assert "workspaces" not in tables
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Test: Migration Script
# ---------------------------------------------------------------------------

class TestMigrationScript:
    """Tests for the Alembic migration script."""

    def test_migration_file_exists(self):
        """Migration file exists in versions directory."""
        migration_path = Path(__file__).parent.parent / "migrations" / "versions" / "b7c8d9e0f1a2_legacy_schema_upgrade.py"
        assert migration_path.exists()

    def test_migration_has_upgrade_downgrade(self):
        """Migration defines upgrade() and downgrade() functions."""
        migration_path = Path(__file__).parent.parent / "migrations" / "versions" / "b7c8d9e0f1a2_legacy_schema_upgrade.py"
        content = migration_path.read_text()
        assert "def upgrade()" in content
        assert "def downgrade()" in content

    def test_migration_revision_chain(self):
        """Migration has correct revision chain."""
        migration_path = Path(__file__).parent.parent / "migrations" / "versions" / "b7c8d9e0f1a2_legacy_schema_upgrade.py"
        content = migration_path.read_text()
        assert 'revision: str = "b7c8d9e0f1a2"' in content
        assert 'down_revision: Union[str, None] = "a1b2c3d4e5f6"' in content
