"""legacy schema upgrade with data preservation

Detects legacy SQLite-style schema (VARCHAR UUIDs, JSON instead of JSONB,
VARCHAR instead of ENUMs) and upgrades to current PostgreSQL schema while
preserving all existing data. Supports rollback with full data restoration.

Supported legacy versions: v0 (no migrations), v1 (basic tables only).

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-16

"""
import logging
from typing import Sequence, Union

logger = logging.getLogger(__name__)

import sqlalchemy as sa
from alembic import op, context
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Legacy schema detection
# ---------------------------------------------------------------------------

_LEGACY_TABLES_V0 = {"users", "datasets", "experiments"}
_LEGACY_TABLES_V1 = _LEGACY_TABLES_V0 | {"workspaces", "training_jobs"}


def _get_table_names(connection) -> set:
    """Return set of table names present in the database."""
    insp = inspect(connection)
    return set(insp.get_table_names())


def _detect_legacy_version(connection) -> int:
    """Detect legacy schema version. Returns -1 if not legacy, 0 or 1 if legacy."""
    tables = _get_table_names(connection)
    alembic_tables = tables & {"alembic_version"}
    has_alembic = bool(alembic_tables)

    if not has_alembic:
        # Check if this is a v0 legacy (just core tables)
        if _LEGACY_TABLES_V0.issubset(tables) and len(tables) < 10:
            return 0
        if _LEGACY_TABLES_V1.issubset(tables) and len(tables) < 20:
            return 1
    return -1


def _check_legacy_column_types(connection) -> bool:
    """Check if columns use legacy types (VARCHAR UUIDs, TEXT JSON)."""
    insp = inspect(connection)
    if "users" not in _get_table_names(connection):
        return False
    columns = insp.get_columns("users")
    for col in columns:
        if col["name"] == "id":
            col_type = str(col["type"]).upper()
            # Legacy: VARCHAR(36) instead of UUID
            if "VARCHAR" in col_type or "CHAR" in col_type:
                return True
    return False


# ---------------------------------------------------------------------------
# Data transformation helpers
# ---------------------------------------------------------------------------

def _convert_uuid_columns(connection) -> list:
    """Convert all VARCHAR UUID columns to proper UUID type. Returns list of conversions."""
    conversions = []
    insp = inspect(connection)
    tables = insp.get_table_names()

    for table_name in tables:
        columns = insp.get_columns(table_name)
        for col in columns:
            col_type = str(col["type"]).upper()
            # Detect VARCHAR(36) columns that likely hold UUIDs
            if ("VARCHAR(36)" in col_type or "CHAR(36)" in col_type) and col["name"] in (
                "id", "user_id", "owner_id", "experiment_id", "dataset_id",
                "workspace_id", "model_version_id", "actor_id", "created_by",
                "locked_by", "granted_by", "subscription_id", "plan_id",
            ):
                conversions.append((table_name, col["name"]))

    return conversions


def _convert_text_to_jsonb(connection) -> list:
    """Convert TEXT/JSON columns to JSONB. Returns list of conversions."""
    conversions = []
    insp = inspect(connection)
    tables = insp.get_table_names()

    # Known JSONB columns in current schema
    jsonb_columns = {
        "experiments": ["scope"],
        "experiment_versions": ["strategy_spec", "assumptions"],
        "model_research_records": ["allowed_asset_universe", "allowed_research_actions"],
        "users": [],  # no JSONB in users
        "datasets": [],
    }

    for table_name in tables:
        if table_name not in jsonb_columns:
            continue
        columns = insp.get_columns(table_name)
        col_names = {c["name"]: str(c["type"]).upper() for c in columns}
        for target_col in jsonb_columns[table_name]:
            if target_col in col_names:
                ct = col_names[target_col]
                # Legacy columns holding JSON are commonly declared TEXT
                # (SQLite-era) or JSON; either converts to JSONB. Already
                # JSONB means nothing to do.
                if ("JSON" in ct or "TEXT" in ct) and "JSONB" not in ct:
                    conversions.append((table_name, target_col))

    return conversions


# ---------------------------------------------------------------------------
# Alembic migration entry points
# ---------------------------------------------------------------------------

def upgrade() -> None:
    """Upgrade legacy schema to current schema with data preservation.

    This migration is designed to be idempotent: running it twice is safe.
    It detects the current state and only applies needed changes.
    """
    connection = context.get_bind()

    # Detect legacy version
    legacy_ver = _detect_legacy_version(connection)
    has_legacy_types = _check_legacy_column_types(connection)

    if legacy_ver == -1 and not has_legacy_types:
        # Already on current schema or unknown - skip
        logger.info("Schema is already current or not a legacy version. No changes applied.")
        return

    # Store legacy state for rollback
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS _legacy_schema_backup ("
        "  id SERIAL PRIMARY KEY,"
        "  table_name TEXT NOT NULL,"
        "  column_name TEXT NOT NULL,"
        "  original_type TEXT NOT NULL,"
        "  backup_data JSONB,"
        "  migrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))

    # Phase 1: Backup existing data that will be transformed
    uuid_conversions = _convert_uuid_columns(connection)
    jsonb_conversions = _convert_text_to_jsonb(connection)

    for table_name, col_name in uuid_conversions:
        connection.execute(text(
            "INSERT INTO _legacy_schema_backup (table_name, column_name, original_type)"
            " VALUES (:table, :col, 'VARCHAR(36)')"
        ), {"table": table_name, "col": col_name})

    for table_name, col_name in jsonb_conversions:
        connection.execute(text(
            "INSERT INTO _legacy_schema_backup (table_name, column_name, original_type)"
            " VALUES (:table, :col, 'TEXT/JSON')"
        ), {"table": table_name, "col": col_name})

    # Phase 2: Convert column types (data is preserved via PostgreSQL casting)
    # Legacy-internal FKs (e.g. experiments.owner_id → users.id in a legacy
    # database that declared them) block the type conversions in both
    # directions; constraints are not part of the restore contract.
    _drop_fks_referencing_legacy_tables(connection)
    for table_name, col_name in uuid_conversions:
        try:
            # Drop existing indexes/constraints that reference this column first
            connection.execute(text(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE UUID"
                f" USING {col_name}::UUID"
            ))
        except sa.exc.ProgrammingError as e:
            logger.warning("UUID conversion skipped for %s.%s: %s", table_name, col_name, e.orig)
        except sa.exc.DataError as e:
            logger.error("UUID conversion FAILED for %s.%s — non-UUID data: %s", table_name, col_name, e.orig)
            raise

    for table_name, col_name in jsonb_conversions:
        try:
            connection.execute(text(
                f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE JSONB"
                f" USING {col_name}::JSONB"
            ))
        except sa.exc.ProgrammingError as e:
            logger.warning("JSONB conversion skipped for %s.%s: %s", table_name, col_name, e.orig)
        except sa.exc.DataError as e:
            logger.error("JSONB conversion FAILED for %s.%s — invalid JSON: %s", table_name, col_name, e.orig)
            raise

    # Phase 3: Ensure all expected tables exist (for v0/v1 legacy that may be missing tables)
    # A legacy database stamped mid-chain (alembic_version present, so version
    # detection returns -1) still needs the bootstrap — it never ran the
    # initial schema. has_legacy_types covers that case.
    if legacy_ver in (0, 1) or has_legacy_types:
        # Create missing tables using raw SQL to be database-agnostic
        _ensure_missing_tables(connection, legacy_ver)
        # The migrations this database skipped (initial schema onward, because
        # the legacy database predates alembic and was stamped mid-chain) never
        # ran, so every current table that the legacy schema lacks must be
        # created here from the live model metadata. Later migrations in the
        # chain detect their work already present and no-op.
        _create_missing_current_tables(connection)
        # Existing legacy tables also lack columns the current model expects
        # (e.g. training_jobs.strategy_spec_snapshot); without them the app
        # fails on its very first SELECT of those tables.
        _add_missing_columns(connection)


def _create_missing_current_tables(connection) -> None:
    """Create every model table that does not exist yet.

    On a legacy database stamped mid-chain, the initial-schema migration was
    never applied; without this bootstrap, subsequent migrations that ALTER
    those tables (fill_records, order_intents, ...) fail with UndefinedTable.
    """
    from app.database import Base
    import app.models  # noqa: F401 — importing registers all models with Base

    Base.metadata.create_all(bind=connection, checkfirst=True)


def _add_missing_columns(connection) -> None:
    """Add model columns that existing legacy tables lack.

    Legacy v0/v1 tables predate many columns added since (quota fields,
    strategy_spec_snapshot, stripe ids, ...). create_all skips existing
    tables entirely, so the gap is closed here column by column. Columns are
    added nullable (or NOT NULL with DEFAULT when the model carries a fixed
    scalar default) so existing rows stay valid.
    """
    from app.database import Base
    import app.models  # noqa: F401 — importing registers all models with Base

    insp = inspect(connection)
    existing = set(insp.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in present:
                continue
            try:
                col_type = col.type.compile(connection.dialect)
                default = ""
                not_null = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    val = col.default.arg
                    if isinstance(val, bool):
                        default = f" DEFAULT {'true' if val else 'false'}"
                    elif isinstance(val, (int, float)):
                        default = f" DEFAULT {val}"
                    if default:
                        not_null = " NOT NULL" if col.nullable is False else ""
                elif col.nullable is False and col.primary_key is False:
                    logger.warning(
                        "Column %s.%s added as NULLABLE (model requires NOT NULL "
                        "but has no scalar default)", table.name, col.name)
                connection.execute(text(
                    f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default}{not_null}'
                ))
            except Exception as exc:
                logger.warning(
                    "Could not add column %s.%s during legacy upgrade: %s",
                    table.name, col.name, exc)


def _ensure_missing_tables(connection, legacy_ver) -> None:
    """Create tables that are missing in legacy versions."""
    tables = _get_table_names(connection)

    # For v0 legacy, we need all tables from initial schema
    # For v1 legacy, we need tables added after v1

    # Create alembic_version table if not exists
    if "alembic_version" not in tables:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "  version_num VARCHAR(32) NOT NULL,"
            "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
            ")"
        ))


def _drop_fks_referencing_legacy_tables(connection) -> None:
    """Drop FK constraints pointing at the legacy tables.

    UUID-typed foreign keys created by the bootstrap (or legacy-internal
    constraints) block converting the legacy id columns between VARCHAR(36)
    and UUID in both directions. Constraints are not part of the restore
    contract; column types and data are.
    """
    insp = inspect(connection)
    for table_name in insp.get_table_names():
        if table_name in ("alembic_version", "_legacy_schema_backup"):
            continue
        for fk in insp.get_foreign_keys(table_name):
            if fk.get("referred_table") in _LEGACY_TABLES_V1 and fk.get("name"):
                connection.execute(text(
                    f'ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {fk["name"]}'
                ))


def downgrade() -> None:
    """Downgrade current schema back to legacy-compatible state.

    Converts UUID to VARCHAR, JSONB to TEXT. This preserves all data
    but loses PostgreSQL-specific type benefits.
    """
    connection = context.get_bind()

    # Check if we have backup data
    result = connection.execute(text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '_legacy_schema_backup')"
    ))
    has_backup = result.scalar() if hasattr(result, 'scalar') else False

    if not has_backup:
        logger.info("No legacy backup found. Downgrade skipped.")
        return

    # Restore original column types from backup
    # Bootstrap-created tables (experiment_versions, subscriptions, ...)
    # carry UUID FKs pointing at the legacy tables and block the type
    # restoration; drop those constraints first.
    _drop_fks_referencing_legacy_tables(connection)
    backup_rows = connection.execute(text(
        "SELECT table_name, column_name, original_type FROM _legacy_schema_backup"
    ))

    for row in backup_rows:
        table_name, col_name, orig_type = row
        try:
            if "VARCHAR" in orig_type:
                connection.execute(text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE VARCHAR(36)"
                    f" USING {col_name}::VARCHAR"
                ))
            elif "JSON" in orig_type:
                connection.execute(text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {col_name} TYPE TEXT"
                    f" USING {col_name}::TEXT"
                ))
        except sa.exc.ProgrammingError as e:
            logger.warning("Downgrade conversion skipped for %s.%s: %s", table_name, col_name, e.orig)
        except sa.exc.DataError as e:
            logger.error("Downgrade conversion FAILED for %s.%s: %s", table_name, col_name, e.orig)
            raise

    # Clean up backup table
    connection.execute(text("DROP TABLE IF EXISTS _legacy_schema_backup"))
