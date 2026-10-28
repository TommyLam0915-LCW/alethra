"""add intraday timeframe + bar cursor to simulation sessions/decisions

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-10-26

Additive-only. Every column is nullable and no existing value is rewritten, so
daily sessions keep advancing by current_snapshot_date exactly as before:

  * simulation_sessions.timeframe          — None/'1d' daily; '5m' etc. intraday
  * simulation_sessions.current_bar_timestamp — intraday replay cursor
  * simulation_decisions.bar_timestamp     — exact bar a per-bar decision filled on
  * paper_fills.bar_timestamp              — exact bar time for an intraday fill
    (execution_date is String(10) and cannot hold a 5-minute time; daily fills
    leave this null)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'l6m7n8o9p0q1'
down_revision = 'k5l6m7n8o9p0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'simulation_sessions',
        sa.Column('timeframe', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'simulation_sessions',
        sa.Column('current_bar_timestamp', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'simulation_decisions',
        sa.Column('bar_timestamp', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'paper_fills',
        sa.Column('bar_timestamp', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('paper_fills', 'bar_timestamp')
    op.drop_column('simulation_decisions', 'bar_timestamp')
    op.drop_column('simulation_sessions', 'current_bar_timestamp')
    op.drop_column('simulation_sessions', 'timeframe')
