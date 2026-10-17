"""add fill_records.broker_fill_key for idempotent broker fill reports

Revision ID: c7e2f9a41b83
Revises: 55a8d07a7f40
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "c7e2f9a41b83"
# Linearized chain: originally branched off 55a8d07a7f40, but a stamp below
# the branchpoint (the legacy upgrade path) plus a multi-parent merge trips
# an alembic 1.14 bookkeeping KeyError in MigrationContext heads tracking,
# so the branch was folded into the main line after d4e5f6a7b8c9.
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return index in {i["name"] for i in inspect(bind).get_indexes(table)}


def _unique_constraint_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return name in {
        c["name"] for c in inspect(bind).get_unique_constraints(table)
    }


def upgrade() -> None:
    # Legacy databases upgraded through b7c8d9e0f1a2 never ran the initial
    # schema; fill_records either does not exist yet (skip — the legacy
    # bootstrap creates it with this column) or was created from the current
    # model which already includes broker_fill_key.
    if "fill_records" not in inspect(op.get_bind()).get_table_names():
        return
    if not _column_exists("fill_records", "broker_fill_key"):
        with op.batch_alter_table("fill_records") as batch_op:
            batch_op.add_column(
                sa.Column("broker_fill_key", sa.String(length=255), nullable=True)
            )
    if not _index_exists("fill_records", "ix_fill_records_broker_fill_key"):
        op.create_index(
            "ix_fill_records_broker_fill_key", "fill_records", ["broker_fill_key"]
        )
    if not _unique_constraint_exists("fill_records", "uq_fill_records_intent_fill_key"):
        with op.batch_alter_table("fill_records") as batch_op:
            batch_op.create_unique_constraint(
                "uq_fill_records_intent_fill_key",
                ["order_intent_id", "broker_fill_key"],
            )


def downgrade() -> None:
    if _unique_constraint_exists("fill_records", "uq_fill_records_intent_fill_key"):
        with op.batch_alter_table("fill_records") as batch_op:
            batch_op.drop_constraint(
                "uq_fill_records_intent_fill_key", type_="unique"
            )
    if _index_exists("fill_records", "ix_fill_records_broker_fill_key"):
        op.drop_index("ix_fill_records_broker_fill_key", table_name="fill_records")
    if _column_exists("fill_records", "broker_fill_key"):
        with op.batch_alter_table("fill_records") as batch_op:
            batch_op.drop_column("broker_fill_key")
