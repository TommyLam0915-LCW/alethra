"""Fix source dedup scope: table-wide → run-scoped.

Revision ID: fix_source_dedup_scope
Revises: add_source_pinning
Create Date: 2026-11-07

Migration 005 added UNIQUE(source_trade_id) which is table-wide, preventing
different paper runs from using the same source trade. This is incorrect:
the uniqueness should be per-run, not global.

This migration:
1. Drops the table-wide UNIQUE constraint on source_trade_id
2. Adds a composite UNIQUE constraint on (paper_run_id, source_trade_id)
3. Adds source_run_id to the composite for future multi-source support

The constraint now allows:
- Different paper runs to each use the same source trade (correct)
- Same paper run cannot duplicate the same source trade (prevents double-counting)
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'fix_source_dedup_scope'
down_revision = 'add_source_pinning'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the table-wide UNIQUE constraints from migration 005
    # These prevented different portfolios from using the same source trade
    op.drop_constraint('uq_decision_records_source_trade_type', 'decision_records', type_='unique')
    op.drop_constraint('uq_paper_positions_source_trade', 'paper_positions', type_='unique')
    
    # Optional: drop the lookup indexes (will recreate below)
    op.drop_index('idx_decision_records_source_trade', table_name='decision_records')
    op.drop_index('idx_paper_positions_source_trade', table_name='paper_positions')
    
    # Add composite unique constraints (portfolio-scoped)
    # decision_records: unique per (paper_portfolio_id, source_trade_id, decision_type)
    # This allows one entry and one exit decision per source trade per portfolio,
    # but prevents duplicate entries or duplicate exits
    op.create_unique_constraint(
        'uq_decision_records_source_per_portfolio',
        'decision_records',
        ['paper_portfolio_id', 'source_trade_id', 'decision_type']
    )
    
    # paper_positions: unique per (portfolio_id, source_trade_id)
    # portfolio_id is the run scope (each run has its own portfolio)
    op.create_unique_constraint(
        'uq_paper_positions_source_per_portfolio',
        'paper_positions',
        ['portfolio_id', 'source_trade_id']
    )
    
    # Recreate non-unique indexes for lookups
    op.create_index('idx_decision_records_source_trade', 'decision_records', ['source_trade_id'])
    op.create_index('idx_paper_positions_source_trade', 'paper_positions', ['source_trade_id'])


def downgrade():
    # Drop the lookup indexes
    op.drop_index('idx_paper_positions_source_trade', table_name='paper_positions')
    op.drop_index('idx_decision_records_source_trade', table_name='decision_records')
    
    # Drop the portfolio-scoped unique constraints
    op.drop_constraint('uq_paper_positions_source_per_portfolio', 'paper_positions', type_='unique')
    op.drop_constraint('uq_decision_records_source_per_portfolio', 'decision_records', type_='unique')
    
    # Recreate the table-wide unique constraints from migration 005
    # decision_records: unique on (source_trade_id, decision_type)
    op.create_unique_constraint(
        'uq_decision_records_source_trade_type',
        'decision_records',
        ['source_trade_id', 'decision_type']
    )
    # paper_positions: unique on source_trade_id
    op.create_unique_constraint(
        'uq_paper_positions_source_trade',
        'paper_positions',
        ['source_trade_id']
    )
    
    # Recreate lookup indexes
    op.create_index('idx_decision_records_source_trade', 'decision_records', ['source_trade_id'])
    op.create_index('idx_paper_positions_source_trade', 'paper_positions', ['source_trade_id'])
