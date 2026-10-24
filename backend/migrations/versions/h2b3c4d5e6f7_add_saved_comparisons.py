"""add saved_comparisons

Persisted backtest run comparisons: an author saves a purpose (and optional
notes) against a pair of completed research runs within one experiment, so a
comparison can be reopened later from the experiment page.

Revision ID: h2b3c4d5e6f7
Revises: a1b2c3d4e5f7
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "h2b3c4d5e6f7"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Existence guard keeps this idempotent: databases bootstrapped via
    # Base.metadata.create_all (app startup / tests) already carry the table.
    if not _table_exists("saved_comparisons"):
        op.create_table(
            "saved_comparisons",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("run_a_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("run_b_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
            sa.ForeignKeyConstraint(["run_a_id"], ["research_runs.id"]),
            sa.ForeignKeyConstraint(["run_b_id"], ["research_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_saved_comparisons_author_id", "saved_comparisons", ["author_id"]
        )
        op.create_index(
            "ix_saved_comparisons_experiment_id", "saved_comparisons", ["experiment_id"]
        )


def downgrade() -> None:
    if _table_exists("saved_comparisons"):
        op.drop_index("ix_saved_comparisons_experiment_id", table_name="saved_comparisons")
        op.drop_index("ix_saved_comparisons_author_id", table_name="saved_comparisons")
        op.drop_table("saved_comparisons")
