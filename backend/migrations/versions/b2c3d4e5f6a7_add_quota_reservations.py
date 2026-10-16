"""Add quota_reservations table for two-phase quota flow.

Enables reserve/settle/release protocol for quota management. Tracks
in-flight operations to prevent double-spending when operations may fail
after starting (e.g., long-running backtests).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quota_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quota_type", sa.String(100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quota_reservation_user_id", "quota_reservations", ["user_id"])
    op.create_index(
        "ix_quota_reservation_user_type_status",
        "quota_reservations",
        ["user_id", "quota_type", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_quota_reservation_user_type_status", table_name="quota_reservations")
    op.drop_index("ix_quota_reservation_user_id", table_name="quota_reservations")
    op.drop_table("quota_reservations")
