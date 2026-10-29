"""add the server-recorded decision instant to human decision events

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-10-29

Additive-only: ``decision_instant`` is the instant at which the decision was
taken on the simulation's market clock, written by the server (never by the
client). The personal dataset builder uses it to include only daily bars that
had already closed at that instant.

Legacy rows keep NULL. They are read conservatively as if the decision happened
at the start of its snapshot date, so the same-day daily bar is not used.
"""
from alembic import op
import sqlalchemy as sa


revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "human_decision_events",
        sa.Column("decision_instant", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("human_decision_events", "decision_instant")
