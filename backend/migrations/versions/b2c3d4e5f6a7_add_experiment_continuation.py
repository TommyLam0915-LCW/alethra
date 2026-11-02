"""add experiment continuation lineage

Revision ID: b2c3d4e5f6a7
Revises: e7f8a9b0c1d2
Create Date: 2026-11-02

Additive only. A locked experiment cannot move its current-version pointer
because replay and simulation bind that pointer, so continuing a version's
research creates a new experiment. These nullable columns record which source
experiment/version it came from; no existing row is read or rewritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "b2c3d4e5f6a7"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("continued_from_experiment_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "experiments",
        sa.Column("continued_from_version_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiments_continued_from_experiment",
        "experiments",
        "experiments",
        ["continued_from_experiment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_experiments_continued_from_version",
        "experiments",
        "experiment_versions",
        ["continued_from_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_experiments_continued_from_version", "experiments", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_experiments_continued_from_experiment", "experiments", type_="foreignkey"
    )
    op.drop_column("experiments", "continued_from_version_id")
    op.drop_column("experiments", "continued_from_experiment_id")
