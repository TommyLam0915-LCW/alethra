"""add user_feedback

In-product feedback reports. One row per submission, owned by the logged-in
user; the row is the receipt the UI reads back before claiming success. No
column is filled by the server from the request context — nothing about the
environment, cookies or credentials is collected.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-15 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
# Both this revision and d0e1f2a3b4c5 (quota_reservations.research_run_id) were
# written against c9d0e1f2a3b4, which makes two heads and an `upgrade head` that
# refuses with MultipleHeads. This file is the one that can move: re-parenting it
# keeps d0e1f2a3b4c5 byte-identical to what was published. A database already at
# c9d0e1f2a3b4 now walks one chain, not a fork.
down_revision: Union[str, None] = "d0e1f2a3b4c5"
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
    # Existence guards keep this idempotent for databases bootstrapped via
    # Base.metadata.create_all, which already carry the table.
    if not _table_exists("user_feedback"):
        op.create_table(
            "user_feedback",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("category", sa.String(40), nullable=False),
            sa.Column("page", sa.String(200), nullable=True),
            sa.Column("steps", sa.Text(), nullable=False),
            sa.Column("expected", sa.Text(), nullable=True),
            sa.Column("actual", sa.Text(), nullable=False),
            sa.Column("error_reference", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists("user_feedback", "ix_user_feedback_user_created"):
        op.create_index(
            "ix_user_feedback_user_created", "user_feedback", ["user_id", "created_at"]
        )


def downgrade() -> None:
    if _index_exists("user_feedback", "ix_user_feedback_user_created"):
        op.drop_index("ix_user_feedback_user_created", table_name="user_feedback")
    if _table_exists("user_feedback"):
        op.drop_table("user_feedback")
