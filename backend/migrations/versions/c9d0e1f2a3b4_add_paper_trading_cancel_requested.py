"""add cancel_requested to paper_trading_runs

Revision ID: c9d0e1f2a3b4
Revises: add_sacrificial_test_table
Create Date: 2026-11-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'add_sacrificial_test_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Add cancel_requested flag to paper_trading_runs for atomic cancel operations
    if not _column_exists('paper_trading_runs', 'cancel_requested'):
        op.add_column(
            'paper_trading_runs',
            sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if _column_exists('paper_trading_runs', 'cancel_requested'):
        op.drop_column('paper_trading_runs', 'cancel_requested')
