"""add paper trading runs and daily metrics

Revision ID: p4p5t6r7n8g9
Revises: l3a4r5n6i7g8
Create Date: 2026-11-06 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p4p5t6r7n8g9'
down_revision: Union[str, None] = 'l3a4r5n6i7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # paper_trading_runs: tracks individual replay/execution runs
    op.create_table(
        'paper_trading_runs',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('portfolio_id', sa.UUID(), nullable=False),
        sa.Column('skill', sa.String(), nullable=False),
        sa.Column('instrument_id', sa.String(), nullable=False),
        sa.Column('interval_minutes', sa.Integer(), nullable=False),
        sa.Column('exit_config', sa.String(), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=False), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='running'),
        sa.Column('initial_capital', sa.Numeric(15, 2), nullable=False),
        sa.Column('final_capital', sa.Numeric(15, 2), nullable=True),
        sa.Column('total_pnl', sa.Numeric(15, 2), nullable=True, server_default='0'),
        sa.Column('total_trades', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('win_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('sharpe_ratio', sa.Numeric(8, 4), nullable=True),
        sa.Column('max_drawdown_pct', sa.Numeric(8, 4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.Column('run_type', sa.String(50), nullable=True, server_default='historical_simulation'),
        sa.ForeignKeyConstraint(['portfolio_id'], ['paper_portfolios.id'], name='paper_trading_runs_portfolio_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='paper_trading_runs_pkey'),
    )
    op.create_index('idx_paper_trading_runs_portfolio', 'paper_trading_runs', ['portfolio_id'])
    op.create_index('idx_paper_trading_runs_status', 'paper_trading_runs', ['status'])

    # paper_trading_daily_metrics: daily performance aggregation
    op.create_table(
        'paper_trading_daily_metrics',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('run_id', sa.UUID(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('trades_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('wins', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('losses', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('daily_pnl', sa.Numeric(15, 2), nullable=True, server_default='0'),
        sa.Column('cumulative_pnl', sa.Numeric(15, 2), nullable=True, server_default='0'),
        sa.Column('drawdown_pct', sa.Numeric(8, 4), nullable=True, server_default='0'),
        sa.Column('sharpe_7d', sa.Numeric(8, 4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['run_id'], ['paper_trading_runs.id'], name='paper_trading_daily_metrics_run_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='paper_trading_daily_metrics_pkey'),
        sa.UniqueConstraint('run_id', 'date', name='paper_trading_daily_metrics_run_id_date_key'),
    )
    op.create_index('idx_paper_trading_daily_run_date', 'paper_trading_daily_metrics', ['run_id', 'date'])


def downgrade() -> None:
    op.drop_index('idx_paper_trading_daily_run_date', table_name='paper_trading_daily_metrics')
    op.drop_table('paper_trading_daily_metrics')
    op.drop_index('idx_paper_trading_runs_status', table_name='paper_trading_runs')
    op.drop_index('idx_paper_trading_runs_portfolio', table_name='paper_trading_runs')
    op.drop_table('paper_trading_runs')
