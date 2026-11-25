"""add linked_inference_id to decision_records for model-execution traceability

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-11-17

Decision lineage requires linking each decision to the specific inference that
produced it. The inference is recorded in usage_records (billing.py:82) with a
stable UUID primary key. This migration adds the reference column that makes
that link queryable: a column holding another table's primary key, not a schema
foreign key. The distinction is the whole point of the note below.

Why NULLABLE:
1. This column is added to an existing table with rows that predate the feature.
2. Existing decisions cannot be retroactively linked to their source inference
   because the linkage was not captured at write time.
3. Hiding those rows (by requiring NOT NULL) would make recorded history
   disappear, violating the provenance invariant.
4. The column is indexed to support efficient lookup when filtering decisions by
   inference.

No FK constraint at DB level: usage_records is in the billing schema and may
have different lifecycle/retention policies. Application-level validation
(see app/api/paper_portfolio.py POST decisions) enforces existence and
ownership checks without creating cross-schema coupling.

Additive-only. Every precondition is checked before anything is created, and any
partial state is refused by name with nothing changed. It does not repair and
it does not drop and recreate.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def _table_exists(conn, table: str) -> bool:
    """Check if a table exists."""
    inspector = inspect(conn)
    return table in inspector.get_table_names()


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    inspector = inspect(conn)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def _index_exists(conn, table: str, index: str) -> bool:
    """Check if an index exists on a table."""
    inspector = inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes(table)]
    return index in indexes


def upgrade() -> None:
    conn = op.get_bind()

    # Phase 1: Verify preconditions
    if not _table_exists(conn, "decision_records"):
        raise RuntimeError(
            "Precondition failed: decision_records table does not exist. "
            "This migration requires the decision_records table to be present."
        )

    if _column_exists(conn, "decision_records", "linked_inference_id"):
        raise RuntimeError(
            "Precondition failed: decision_records.linked_inference_id already exists. "
            "This migration is additive-only and will not overwrite existing state."
        )

    if _index_exists(conn, "decision_records", "ix_decision_records_linked_inference_id"):
        raise RuntimeError(
            "Precondition failed: index ix_decision_records_linked_inference_id already exists. "
            "This migration is additive-only and will not overwrite existing state."
        )

    # Phase 2: Add column and index
    op.add_column(
        'decision_records',
        sa.Column('linked_inference_id', sa.UUID(), nullable=True)
    )
    op.create_index(
        'ix_decision_records_linked_inference_id',
        'decision_records',
        ['linked_inference_id']
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Verify preconditions for downgrade (reverse of upgrade)
    if not _index_exists(conn, "decision_records", "ix_decision_records_linked_inference_id"):
        raise RuntimeError(
            "Downgrade precondition: index ix_decision_records_linked_inference_id does not exist. "
            "Cannot drop an index that is not present."
        )

    if not _column_exists(conn, "decision_records", "linked_inference_id"):
        raise RuntimeError(
            "Downgrade precondition: decision_records.linked_inference_id does not exist. "
            "Cannot drop a column that is not present."
        )

    op.drop_index('ix_decision_records_linked_inference_id', table_name='decision_records')
    op.drop_column('decision_records', 'linked_inference_id')
