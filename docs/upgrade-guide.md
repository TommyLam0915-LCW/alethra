# Legacy Database Upgrade Guide

This guide describes the upgrade path from legacy SQLite schema to the current
PostgreSQL schema used by Alethra. The upgrade preserves all existing data and
supports rollback if needed.

## Supported Legacy Versions

| Version | Tables | Description |
|---------|--------|-------------|
| **v0** | `users`, `datasets`, `experiments` | Core tables only |
| **v1** | v0 + `workspaces`, `training_jobs` | Extended tables |

Any other schema will be reported as **unsupported**. Contact support for manual
migration of unknown schemas.

## Quick Start

### 1. Run a Preflight Check

Before upgrading, inspect your database to confirm the schema version:

```python
from app.services.db_upgrade import preflight_check

report = preflight_check("path/to/your/database.db")
print(report["schema_version"])   # "legacy_v0", "legacy_v1", "current", "unknown"
print(report["is_supported"])     # True if upgrade is supported
print(report["warnings"])         # List of potential issues
print(report["errors"])           # List of blocking errors
```

### 2. Dry Run

Verify what the upgrade would do without modifying anything:

```python
from app.services.db_upgrade import run_upgrade

result = run_upgrade("path/to/database.db", dry_run=True)
print(result.message)
```

### 3. Execute the Upgrade

The upgrade creates an automatic backup before making any changes:

```python
from app.services.db_upgrade import run_upgrade

result = run_upgrade(
    db_path="path/to/database.db",
    backup_dir="/safe/backup/location",  # optional, uses temp dir if omitted
)

if result.success:
    print("Upgrade completed!")
    print(f"Backup: {result.backup_path}")
    print(f"Tables migrated: {result.tables_migrated}")
else:
    print(f"Upgrade failed: {result.message}")
    for err in result.errors:
        print(f"  - {err}")
```

### 4. Verify the Upgrade

```python
from app.services.db_upgrade import detect_schema_version, get_upgrade_status

status = get_upgrade_status("path/to/database.db")
print(status)
# {'schema_version': 'current', 'needs_upgrade': False, 'is_supported': True}
```

## What the Upgrade Does

### Phase 1: Backup
- Creates a full copy of the database with integrity verification
- Stores metadata (timestamp, checksum, schema version) alongside the backup
- Backup is automatically restored if the upgrade fails

### Phase 2: Column Type Conversion
- Detects `TEXT` columns that store UUIDs (36-character strings)
- On PostgreSQL: converts `VARCHAR(36)` to native `UUID` type
- On SQLite: validates UUID format but keeps `TEXT` (SQLite has no UUID type)
- Detects `TEXT`/`JSON` columns that should be `JSONB`
- On PostgreSQL: converts to `JSONB` for better query performance
- All conversions preserve existing data via safe casting

### Phase 3: Missing Table Creation
- For **v0** databases: creates `workspaces` and `training_jobs` tables
- Creates `alembic_version` table to mark schema as current
- Does not overwrite existing tables

### Data Preservation Guarantees
- All rows in all tables are preserved
- Foreign key relationships are maintained
- JSON data stored as TEXT is correctly cast to JSON/JSONB
- UUID strings are validated before conversion

## Rollback

If you need to revert the upgrade:

```python
from app.services.db_upgrade import run_rollback

result = run_rollback(
    backup_path="/path/to/backup_file.db",
    target_path="path/to/database.db",
)

if result.success:
    print("Rollback completed!")
else:
    print(f"Rollback failed: {result.message}")
```

The backup path is returned by `run_upgrade()` in `result.backup_path`.

## Idempotency

Running the upgrade multiple times is safe:

- **First run**: detects legacy schema, creates backup, performs upgrade
- **Subsequent runs**: detects current schema, returns no-op result
- No data is lost or duplicated on repeated runs

```python
result1 = run_upgrade("database.db")  # Performs upgrade
result2 = run_upgrade("database.db")  # "Schema is already current. No upgrade needed."
```

## Error Handling

### Unsupported Legacy Version
```
UnsupportedLegacyVersionError: Unsupported legacy version: unknown.
Supported: ['legacy_v0', 'legacy_v1']
```
**Action**: The database schema is not recognized. Export your data manually
and import into a fresh current-schema database.

### Preflight Check Failed
```
PreflightError: Preflight checks failed: <reason>
```
**Action**: Review the errors in the preflight report and fix them before
retrying. Common causes: corrupted database, missing tables, file permissions.

### Upgrade Failure with Automatic Rollback
If the upgrade encounters an error, it automatically restores from backup:
```
UpgradeResult(success=False, message="Upgrade failed and was rolled back: ...")
```
**Action**: Review the error message, fix the underlying issue, and retry.

## Migration via Alembic

For PostgreSQL databases managed by Alembic, the migration script
`b7c8d9e0f1a2_legacy_schema_upgrade.py` handles the upgrade automatically:

```bash
cd backend
alembic upgrade head
```

This migration:
1. Detects if the schema is legacy (checks column types, table presence)
2. Records original column types in `_legacy_schema_backup` for rollback
3. Converts column types using safe PostgreSQL casts
4. Creates any missing tables

### Downgrade via Alembic
```bash
alembic downgrade -1  # Rollback one migration
```

The downgrade converts PostgreSQL-specific types back to generic types:
- `UUID` -> `VARCHAR(36)`
- `JSONB` -> `TEXT`

## Architecture

```
backend/
  app/services/
    db_upgrade.py          # Core upgrade service
  migrations/versions/
    b7c8d9e0f1a2_legacy_schema_upgrade.py  # Alembic migration
  tests/
    test_db_upgrade.py     # 15+ tests covering all scenarios
```

## Testing

Run the test suite:
```bash
cd backend
python3 -m pytest tests/test_db_upgrade.py -v
```

The tests use SQLite databases to simulate legacy schemas and verify:
- Schema detection (v0, v1, current, unknown, empty)
- Preflight checks (supported/unsupported versions, warnings)
- Backup creation and integrity verification
- Upgrade execution with data preservation
- Rollback to original state
- Idempotent behavior (safe repeated runs)
- Error handling (missing files, invalid UUIDs, unsupported versions)
- Migration script structure and revision chain
