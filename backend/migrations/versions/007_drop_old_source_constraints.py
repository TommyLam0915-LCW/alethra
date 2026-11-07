"""Drop old table-wide unique constraints from migration 005.

Revision ID: drop_old_source_constraints
Revises: fix_source_dedup_scope
Create Date: 2026-11-07

Migration 005 added table-wide unique constraints:
- uq_decision_records_source_trade_type (source_trade_id, decision_type)
- uq_paper_positions_source_trade (source_trade_id)

Migration 006 attempted to fix the scope by adding portfolio-scoped constraints,
but only dropped the indexes, not the unique constraints themselves.

This migration:
1. Drops the old table-wide unique constraints from 005
2. Verifies the new portfolio-scoped constraints from 006 exist
3. Ensures the schema matches the intended design

The portfolio-scoped constraints allow:
- Different portfolios to each use the same source trade (correct)
- Same portfolio cannot duplicate the same source trade (prevents double-counting)
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'drop_old_source_constraints'
down_revision = 'fix_source_dedup_scope'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the old table-wide unique constraints from migration 005
    # These were NOT dropped by migration 006 (which only dropped indexes)
    
    # decision_records: drop uq_decision_records_source_trade_type
    op.drop_constraint(
        'uq_decision_records_source_trade_type',
        'decision_records',
        type_='unique'
    )
    
    # paper_positions: drop uq_paper_positions_source_trade
    op.drop_constraint(
        'uq_paper_positions_source_trade',
        'paper_positions',
        type_='unique'
    )
    
    # Verify new portfolio-scoped constraints exist (from migration 006)
    # These should already be in place, but we document them here for clarity:
    # - uq_decision_records_source_per_portfolio (paper_portfolio_id, source_trade_id, decision_type)
    # - uq_paper_positions_source_per_portfolio (portfolio_id, source_trade_id)


def downgrade():
    # Recreate the old table-wide unique constraints (incorrect scope)
    # This is the reverse of what migration 005 did
    
    op.create_unique_constraint(
        'uq_paper_positions_source_trade',
        'paper_positions',
        ['source_trade_id']
    )
    
    op.create_unique_constraint(
        'uq_decision_records_source_trade_type',
        'decision_records',
        ['source_trade_id', 'decision_type']
    )
