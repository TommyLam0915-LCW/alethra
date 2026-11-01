"""Read-only check that the database holds every column the ORM expects.

A matching ``alembic_version`` string is not proof: the 2026-11-01
``volume_decimal`` incident had a database stamped at one revision while the
running code had already added a mapped column, so every market-data read
failed with ``UndefinedColumn`` while the launcher still reported ready.

This module never writes. It compares ``Base.metadata`` (what the running code
will actually SELECT/INSERT) against ``information_schema.columns`` (what the
database has) and reports the difference.
"""
from __future__ import annotations

import logging
from typing import Dict

from sqlalchemy import text

import app.models  # noqa: F401  - registers every mapped table on Base.metadata
from app.database import Base

logger = logging.getLogger(__name__)


def _schema_targets(conn) -> Dict[str, list]:
    """Return {table_name: [column names]} for tables in the current schema."""
    if conn.dialect.name != "postgresql":
        return {}
    schema = conn.execute(text("SELECT current_schema()")).scalar() or "public"
    rows = conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema"
        ),
        {"schema": schema},
    ).fetchall()
    columns: Dict[str, set] = {}
    for table_name, column_name in rows:
        columns.setdefault(table_name, set()).add(column_name)
    return columns


def check_schema_compatibility(conn) -> dict:
    """Compare mapped columns with the live database, read-only.

    Returns ``status`` in:
      * ``compatible``     - every mapped table/column exists;
      * ``incompatible``   - the running code needs columns the DB lacks;
      * ``not_applicable`` - non-PostgreSQL dialect (SQLite test runs).
    """
    if conn.dialect.name != "postgresql":
        return {"status": "not_applicable", "dialect": conn.dialect.name}

    existing = _schema_targets(conn)
    missing_tables = []
    missing_columns: Dict[str, list] = {}
    for table_name, table in Base.metadata.tables.items():
        if table.schema not in (None, "public"):
            continue
        columns = existing.get(table_name)
        if columns is None:
            missing_tables.append(table_name)
            continue
        absent = sorted(c.name for c in table.columns if c.name not in columns)
        if absent:
            missing_columns[table_name] = absent

    try:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - a missing version row is not the point of this check
        revision = None

    compatible = not missing_tables and not missing_columns
    return {
        "status": "compatible" if compatible else "incompatible",
        "alembic_revision": revision,
        "missing_tables": sorted(missing_tables),
        "missing_columns": {name: cols for name, cols in sorted(missing_columns.items())},
    }


def summarise(result: dict, max_items: int = 5) -> str:
    """One log-safe sentence naming a few missing columns, never SQL text."""
    if result.get("status") != "incompatible":
        return f"schema {result.get('status')}"
    parts = [
        f"{table}: {', '.join(columns[:max_items])}"
        for table, columns in result.get("missing_columns", {}).items()
    ]
    if result.get("missing_tables"):
        parts.append("missing tables: " + ", ".join(result["missing_tables"][:max_items]))
    return "schema incompatible with running code (" + "; ".join(parts) + ")"
