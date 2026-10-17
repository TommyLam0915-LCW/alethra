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
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
# Runs after the legacy-schema conversion: the quota_reservations FK points at
# users.id, which on a legacy database is still VARCHAR(36) until b7c8d9e0f1a2
# converts it — creating this table before that conversion fails on PostgreSQL.
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return index in {i["name"] for i in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    if _table_exists("quota_reservations"):
        # Already created (e.g. legacy bootstrap created the full current
        # schema from the model metadata before this migration ran).
        return
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
    # Model-default name (ix_<table>_<column>) so alembic-created and
    # create_all-created databases carry identical indexes.
    op.create_index("ix_quota_reservations_user_id", "quota_reservations", ["user_id"])
    op.create_index(
        "ix_quota_reservation_user_type_status",
        "quota_reservations",
        ["user_id", "quota_type", "status"],
    )


def downgrade() -> None:
    if not _table_exists("quota_reservations"):
        return
    if _index_exists("quota_reservations", "ix_quota_reservation_user_type_status"):
        op.drop_index("ix_quota_reservation_user_type_status", table_name="quota_reservations")
    for name in ("ix_quota_reservations_user_id", "ix_quota_reservation_user_id"):
        if _index_exists("quota_reservations", name):
            op.drop_index(name, table_name="quota_reservations")
    op.drop_table("quota_reservations")
