"""add failure_reason to paper_trading_runs

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-21 00:00:00.000000

A replay that dies is recorded by ``paper_trading.py``'s terminal-state CASE
statement as ``status='failed'`` and nothing else, so "why" reaches only the
server log. This adds the column that claim is stored in.

``Text``, not the ``String(64)`` the ``failure_reason`` columns on
``agent_strategy``/``personal_dataset`` use: those carry a platform-authored
code, while this one carries ``safe_error_message(exc)``, which is bounded at
200 characters by ``app/lib/error_safety.py:49`` and would be silently
truncated by a 64-character column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists('paper_trading_runs', 'failure_reason'):
        op.add_column(
            'paper_trading_runs',
            sa.Column('failure_reason', sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists('paper_trading_runs', 'failure_reason'):
        op.drop_column('paper_trading_runs', 'failure_reason')
