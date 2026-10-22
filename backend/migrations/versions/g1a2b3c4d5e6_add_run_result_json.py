"""Add result_json to research_runs.

Stores the full backtest result (transactions, daily_accounting,
daily_equity_curve, data provenance) for later retrieval.

Revision ID: g1a2b3c4d5e6
Revises: f6a7b8c9d0e1
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g1a2b3c4d5e6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str]] = None
depends_on: Union[str, Sequence[str]] = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("result_json", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("research_runs", "result_json")
