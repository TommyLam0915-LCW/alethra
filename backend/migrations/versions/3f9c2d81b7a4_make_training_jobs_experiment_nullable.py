"""Make training_jobs.experiment_id nullable for ML training jobs.

ML training jobs (training_type=ml_model) are created from the training
page without an experiment. The previous NOT NULL forced the API to insert
a random experiment UUID, which violates the FK on PostgreSQL (500) and
creates orphan job rows wherever the FK is not enforced.

Revision ID: 3f9c2d81b7a4
Revises: 55a8d07a7f40
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f9c2d81b7a4"
down_revision: Union[str, None] = "55a8d07a7f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "training_jobs",
        "experiment_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM training_jobs WHERE experiment_id IS NULL")
    op.alter_column(
        "training_jobs",
        "experiment_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
