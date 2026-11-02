"""register delegated write batches so a batch cannot run twice

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-11-02

Additive-only. One row per ``(owner_id, batch_key)``; the unique constraint is
the coordination primitive. A row moves ``claimed -> completed`` or
``claimed -> abandoned``; a completed result is never overwritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("batch_key", sa.String(length=120), nullable=False),
        sa.Column("executor", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="claimed",
        ),
        sa.Column("intended_operations", JSONB, nullable=True),
        sa.Column("caps", JSONB, nullable=True),
        sa.Column("precheck", JSONB, nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("abandoned_at", sa.DateTime(), nullable=True),
        sa.Column("abandon_reason", sa.Text(), nullable=True),
        sa.Column("result_json", JSONB, nullable=True),
        sa.UniqueConstraint(
            "owner_id", "batch_key", name="uq_operation_batch_owner_key"
        ),
        sa.CheckConstraint(
            "status IN ('claimed', 'completed', 'abandoned')",
            name="ck_operation_batch_status",
        ),
    )
    op.create_index(
        "ix_operation_batches_status", "operation_batches", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_operation_batches_status", table_name="operation_batches")
    op.drop_table("operation_batches")
