"""Legacy database upgrade service.

Provides a safe upgrade path from legacy SQLite/PostgreSQL schema to the
current PostgreSQL schema. Features:
- Schema preflight checks (detects legacy vs current)
- Automated backup before upgrade
- Data preservation during migration
- Rollback capability on failure
- Idempotent execution (safe to run multiple times)

Supported legacy versions:
- v0: Core tables only (users, datasets, experiments)
- v1: Extended tables (adds workspaces, training_jobs)
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class SchemaVersion(str, Enum):
    """Known schema versions."""
    LEGACY_V0 = "legacy_v0"
    LEGACY_V1 = "legacy_v1"
    CURRENT = "current"
    UNKNOWN = "unknown"


SUPPORTED_LEGACY_VERSIONS = {SchemaVersion.LEGACY_V0, SchemaVersion.LEGACY_V1}

LEGACY_V0_TABLES = {"users", "datasets", "experiments"}
LEGACY_V1_TABLES = LEGACY_V0_TABLES | {"workspaces", "training_jobs"}

CURRENT_MIN_TABLES = {"users", "datasets", "experiments", "workspaces",
                      "training_jobs", "experiment_versions", "model_research_records",
                      "market_data_bars", "market_data_metadata"}

# Alembic revision this tool stamps after upgrading a legacy database.
# Must match the branch's migration head (a8d9e0f1b2c3 add_ai_conversations
# — the linearized head on this stack).
CURRENT_SCHEMA_REVISION = "a8d9e0f1b2c3"

# Column signatures that identify legacy schema
LEGACY_UUID_COLUMNS = {"id", "user_id", "owner_id", "experiment_id",
                       "workspace_id", "dataset_id"}


class UpgradeError(Exception):
    """Raised when upgrade fails with a recoverable state."""
    pass


class UnsupportedLegacyVersionError(UpgradeError):
    """Raised when the legacy schema is not a supported version."""
    pass


class PreflightError(UpgradeError):
    """Raised when preflight checks fail."""
    pass


# ---------------------------------------------------------------------------
# Schema detection
# ---------------------------------------------------------------------------

def detect_schema_version(db_path: str) -> SchemaVersion:
    """Detect the schema version of a SQLite database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        SchemaVersion indicating the detected version.

    Raises:
        FileNotFoundError: If database file doesn't exist.
        sqlite3.Error: If database cannot be opened.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()

    # Remove SQLite internal tables
    tables -= {"sqlite_sequence", "sqlite_stat1"}

    # An alembic_version table alone does NOT prove the schema is current:
    # verify the stamped revision matches the expected head AND the actual
    # table set covers the current minimum tables.
    if "alembic_version" in tables:
        revision = _read_alembic_revision(db_path)
        if revision == CURRENT_SCHEMA_REVISION and CURRENT_MIN_TABLES.issubset(tables):
            return SchemaVersion.CURRENT
        return SchemaVersion.UNKNOWN

    # Check legacy v1 first (superset of v0: has workspaces + training_jobs)
    if LEGACY_V1_TABLES.issubset(tables) and len(tables) <= 15:
        return SchemaVersion.LEGACY_V1

    # Check legacy v0 (core tables only, no workspaces/training_jobs)
    if LEGACY_V0_TABLES.issubset(tables) and not LEGACY_V1_TABLES.issubset(tables):
        return SchemaVersion.LEGACY_V0

    # Unknown - maybe very old or corrupted
    if len(tables) == 0:
        return SchemaVersion.UNKNOWN

    # If tables exist but don't match any known pattern
    return SchemaVersion.UNKNOWN


def _read_alembic_revision(db_path: str) -> Optional[str]:
    """Read the stamped revision from alembic_version, or None if unreadable."""
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def detect_column_types(db_path: str) -> dict[str, dict[str, str]]:
    """Inspect column types in a SQLite database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Dict mapping table_name -> {column_name: column_type}.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cursor.fetchall()]

        result = {}
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            result[table] = {col[1]: col[2] for col in cols}
        return result
    finally:
        conn.close()


def preflight_check(db_path: str) -> dict[str, Any]:
    """Run preflight checks before upgrade.

    Returns a report dict with:
    - schema_version: detected version
    - is_supported: whether upgrade is supported
    - table_count: number of tables
    - column_types: detected column types
    - warnings: list of warning messages
    - errors: list of error messages
    """
    report = {
        "schema_version": None,
        "is_supported": False,
        "table_count": 0,
        "column_types": {},
        "warnings": [],
        "errors": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        version = detect_schema_version(db_path)
        report["schema_version"] = version.value
        report["is_supported"] = version in SUPPORTED_LEGACY_VERSIONS

        col_types = detect_column_types(db_path)
        report["column_types"] = col_types
        report["table_count"] = len(col_types)

        if version == SchemaVersion.UNKNOWN:
            report["errors"].append(
                "Unknown schema version. Supported legacy versions: v0, v1. "
                "Please contact support for manual migration."
            )
            if "alembic_version" in col_types:
                missing = CURRENT_MIN_TABLES - set(col_types.keys())
                revision = _read_alembic_revision(db_path)
                report["errors"].append(
                    f"alembic_version present but schema is not current "
                    f"(revision={revision!r}, expected={CURRENT_SCHEMA_REVISION!r}, "
                    f"missing tables={sorted(missing)})"
                )
        elif version == SchemaVersion.CURRENT:
            report["warnings"].append("Schema is already current. No upgrade needed.")

        # Check for legacy UUID patterns (VARCHAR stored as TEXT)
        for table, cols in col_types.items():
            for col_name, col_type in cols.items():
                if col_name in LEGACY_UUID_COLUMNS and col_type == "TEXT":
                    report["warnings"].append(
                        f"Table '{table}' column '{col_name}' uses TEXT for UUID storage"
                    )

    except FileNotFoundError as e:
        report["errors"].append(str(e))
    except sqlite3.Error as e:
        report["errors"].append(f"Database error: {e}")

    return report


# ---------------------------------------------------------------------------
# Backup and restore
# ---------------------------------------------------------------------------

def create_backup(db_path: str, backup_dir: Optional[str] = None) -> str:
    """Create a full backup of the database before upgrade.

    Args:
        db_path: Path to the source database.
        backup_dir: Directory for backup files. Uses temp dir if None.

    Returns:
        Path to the backup file.

    Raises:
        FileNotFoundError: If source database doesn't exist.
        OSError: If backup directory cannot be created.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    if backup_dir is None:
        backup_dir = tempfile.mkdtemp(prefix="alethra_backup_")
    else:
        os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    checksum = _file_checksum(db_path)[:8]
    backup_name = f"alethra_backup_{timestamp}_{checksum}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    shutil.copy2(db_path, backup_path)

    # Verify backup integrity
    _verify_backup(backup_path)

    # Write metadata file
    meta_path = backup_path + ".meta.json"
    metadata = {
        "source": db_path,
        "timestamp": timestamp,
        "checksum": checksum,
        "schema_version": detect_schema_version(db_path).value,
        "backup_path": backup_path,
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("Backup created: %s", backup_path)
    return backup_path


def restore_backup(backup_path: str, target_path: str) -> None:
    """Restore a database from backup.

    Args:
        backup_path: Path to the backup file.
        target_path: Path where the database should be restored.

    Raises:
        FileNotFoundError: If backup file doesn't exist.
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    # Verify backup before restoring
    _verify_backup(backup_path)

    shutil.copy2(backup_path, target_path)
    logger.info("Restored from backup: %s -> %s", backup_path, target_path)


def _verify_backup(backup_path: str) -> None:
    """Verify backup file integrity using SQLite integrity check."""
    conn = sqlite3.connect(backup_path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise UpgradeError(f"Backup integrity check failed: {result[0]}")
    finally:
        conn.close()


def _file_checksum(path: str) -> str:
    """Calculate SHA256 checksum of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# Upgrade execution
# ---------------------------------------------------------------------------

class UpgradeResult:
    """Result of an upgrade operation."""

    def __init__(self, success: bool, message: str, backup_path: Optional[str] = None,
                 tables_migrated: Optional[list] = None, errors: Optional[list] = None):
        self.success = success
        self.message = message
        self.backup_path = backup_path
        self.tables_migrated = tables_migrated or []
        self.errors = errors or []
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "backup_path": self.backup_path,
            "tables_migrated": self.tables_migrated,
            "errors": self.errors,
            "timestamp": self.timestamp,
        }


def run_upgrade(db_path: str, backup_dir: Optional[str] = None,
                dry_run: bool = False) -> UpgradeResult:
    """Execute the database upgrade.

    This is the main entry point for upgrading a legacy database.

    Args:
        db_path: Path to the database to upgrade.
        backup_dir: Directory for backup files.
        dry_run: If True, only check and report, don't modify.

    Returns:
        UpgradeResult with details of the operation.

    Raises:
        UnsupportedLegacyVersionError: If schema version is not supported.
        PreflightError: If preflight checks fail.
    """
    # Step 1: Preflight check
    report = preflight_check(db_path)

    if report["errors"]:
        raise PreflightError(f"Preflight checks failed: {'; '.join(report['errors'])}")

    version = SchemaVersion(report["schema_version"])

    if version == SchemaVersion.CURRENT:
        return UpgradeResult(
            success=True,
            message="Schema is already current. No upgrade needed.",
            tables_migrated=[],
        )

    if version not in SUPPORTED_LEGACY_VERSIONS:
        raise UnsupportedLegacyVersionError(
            f"Unsupported legacy version: {version.value}. "
            f"Supported: {[v.value for v in SUPPORTED_LEGACY_VERSIONS]}"
        )

    if dry_run:
        return UpgradeResult(
            success=True,
            message=f"Dry run: would upgrade from {version.value} to current.",
            tables_migrated=[],
        )

    # Step 2: Create backup
    backup_path = create_backup(db_path, backup_dir)

    # Step 3: Execute upgrade
    conn = sqlite3.connect(db_path)
    tables_migrated = []
    errors = []

    try:
        # Enable WAL mode for safer writes
        conn.execute("PRAGMA journal_mode=WAL")

        # Phase 1: Validate and record legacy UUID columns (read-only)
        col_types = detect_column_types(db_path)
        for table, cols in col_types.items():
            for col_name, col_type in cols.items():
                if col_name in LEGACY_UUID_COLUMNS and col_type == "TEXT":
                    # In SQLite, TEXT can hold UUIDs directly - no conversion needed
                    # but we validate they're valid UUIDs
                    try:
                        rows = conn.execute(f"SELECT {col_name} FROM {table} WHERE {col_name} IS NOT NULL").fetchall()
                        for row in rows:
                            if row[0]:
                                try:
                                    uuid.UUID(str(row[0]))
                                except ValueError:
                                    errors.append(
                                        f"Table '{table}' column '{col_name}' has "
                                        f"non-UUID value: '{row[0]}'"
                                    )
                    except sqlite3.Error as e:
                        logger.warning("Could not validate UUIDs in %s.%s: %s", table, col_name, e)
                    tables_migrated.append(f"{table}.{col_name}")

        # Non-UUID values in key columns are BLOCKING: the upgrade would
        # produce a database that violates UUID constraints downstream.
        if errors:
            conn.rollback()
            logger.error("Upgrade aborted: %d blocking data validation errors", len(errors))
            # Phase 1 performed no writes; restore anyway so the database
            # file is byte-identical to the pre-upgrade state (incl. WAL).
            restore_backup(backup_path, db_path)
            return UpgradeResult(
                success=False,
                message=(
                    f"Upgrade aborted: {len(errors)} blocking data validation "
                    f"errors. Database restored from backup."
                ),
                backup_path=backup_path,
                tables_migrated=tables_migrated,
                errors=errors,
            )

        # Phase 2: Ensure the current minimum table set exists (v0 and v1)
        if version in SUPPORTED_LEGACY_VERSIONS:
            _ensure_current_tables(conn)
            tables_migrated.append("ensured_current_tables")

        # Phase 3: Add alembic_version marker
        _mark_schema_version(conn, version)

        conn.commit()
        logger.info("Upgrade completed: %d tables migrated", len(tables_migrated))

    except Exception as e:
        conn.rollback()
        logger.error("Upgrade failed: %s", e)
        # Attempt to restore backup
        try:
            restore_backup(backup_path, db_path)
            return UpgradeResult(
                success=False,
                message=f"Upgrade failed and was rolled back: {e}",
                backup_path=backup_path,
                tables_migrated=tables_migrated,
                errors=[str(e)],
            )
        except Exception as restore_err:
            return UpgradeResult(
                success=False,
                message=f"Upgrade failed AND rollback failed: {e}; rollback error: {restore_err}",
                backup_path=backup_path,
                tables_migrated=tables_migrated,
                errors=[str(e), f"Rollback failed: {restore_err}"],
            )
    finally:
        conn.close()

    return UpgradeResult(
        success=True,
        message=f"Successfully upgraded from {version.value} to current.",
        backup_path=backup_path,
        tables_migrated=tables_migrated,
        errors=errors,
    )


def _ensure_current_tables(conn: sqlite3.Connection) -> None:
    """Create every table in CURRENT_MIN_TABLES that is missing.

    The upgrade stamps the database with the current revision; the actual
    table set must back that claim, otherwise post-upgrade detection
    (revision + schema) would classify the database as unknown. The
    workspaces/training_jobs tables get their full legacy-shape columns;
    remaining current tables are created with a minimal id column since
    legacy data does not reference them.
    """
    existing = set()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    for row in cursor.fetchall():
        existing.add(row[0])

    if "workspaces" not in existing:
        conn.execute("""
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)
        existing.add("workspaces")

    if "training_jobs" not in existing:
        conn.execute("""
            CREATE TABLE training_jobs (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                experiment_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                config_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)
        existing.add("training_jobs")

    for table in sorted(CURRENT_MIN_TABLES):
        if table not in existing:
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
            existing.add(table)


def _mark_schema_version(conn: sqlite3.Connection, version: SchemaVersion) -> None:
    """Mark the schema as upgraded in alembic_version table."""
    # Create alembic_version if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alembic_version (
            version_num TEXT NOT NULL PRIMARY KEY
        )
    """)

    # Set the current migration revision
    conn.execute("DELETE FROM alembic_version")
    conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)",
                 (CURRENT_SCHEMA_REVISION,))


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def run_rollback(backup_path: str, target_path: str) -> UpgradeResult:
    """Roll back to a previous database state from backup.

    Args:
        backup_path: Path to the backup to restore from.
        target_path: Path of the database to overwrite.

    Returns:
        UpgradeResult with rollback details.
    """
    try:
        restore_backup(backup_path, target_path)
        return UpgradeResult(
            success=True,
            message=f"Successfully rolled back to backup: {backup_path}",
            backup_path=backup_path,
        )
    except Exception as e:
        return UpgradeResult(
            success=False,
            message=f"Rollback failed: {e}",
            backup_path=backup_path,
            errors=[str(e)],
        )


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------

def is_upgrade_needed(db_path: str) -> bool:
    """Check if an upgrade is needed.

    Returns True if the database is a supported legacy version,
    False if already current or unknown.
    """
    try:
        version = detect_schema_version(db_path)
        return version in SUPPORTED_LEGACY_VERSIONS
    except (FileNotFoundError, sqlite3.Error):
        return False


def get_upgrade_status(db_path: str) -> dict:
    """Get current upgrade status for monitoring.

    Returns dict with schema_version, needs_upgrade, and last_upgrade info.
    """
    try:
        version = detect_schema_version(db_path)
        return {
            "schema_version": version.value,
            "needs_upgrade": version in SUPPORTED_LEGACY_VERSIONS,
            "is_supported": version in SUPPORTED_LEGACY_VERSIONS or version == SchemaVersion.CURRENT,
        }
    except FileNotFoundError:
        return {
            "schema_version": "not_found",
            "needs_upgrade": False,
            "is_supported": False,
            "error": "Database file not found",
        }
    except sqlite3.Error as e:
        return {
            "schema_version": "error",
            "needs_upgrade": False,
            "is_supported": False,
            "error": str(e),
        }
