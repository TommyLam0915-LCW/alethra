"""Remove residual global unique constraints from broken migration 006.

Revision ID: remove_residual_global_constraints
Revises: fix_source_dedup_scope
Create Date: 2026-11-07

Migration 006 was supposed to replace global unique constraints with
portfolio-scoped constraints, but it only dropped lookup indexes, not
the actual unique constraints. This migration fixes that by dropping
the residual global constraints that should have been removed in 006.

This is safe to run on databases that:
1. Already applied the broken 006 (will drop residual constraints)
2. Will apply 006 fresh (constraints already dropped by fixed 006)

The fixed migration 006 now correctly drops these constraints, so
fresh installations won't have them. This migration is for databases
that already applied the broken version.
"""
from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'rm_residual_global_constraints'
down_revision = 'fix_source_dedup_scope'
branch_labels = None
depends_on = None


def check_constraint_exists(constraint_name: str, table_name: str) -> bool:
    """Check if a constraint exists in the database."""
    conn = op.get_bind()
    result = conn.execute(text("""
        SELECT COUNT(*) FROM information_schema.table_constraints
        WHERE constraint_name = :constraint_name
        AND table_name = :table_name
    """), {'constraint_name': constraint_name, 'table_name': table_name})
    count = result.scalar()
    return count > 0


def upgrade():
    # Drop residual global constraints from migration 005
    # These should have been dropped by migration 006, but the broken
    # version only dropped lookup indexes, not the actual constraints
    
    if check_constraint_exists('uq_decision_records_source_trade_type', 'decision_records'):
        op.drop_constraint('uq_decision_records_source_trade_type', 'decision_records', type_='unique')
    
    if check_constraint_exists('uq_paper_positions_source_trade', 'paper_positions'):
        op.drop_constraint('uq_paper_positions_source_trade', 'paper_positions', type_='unique')


def downgrade():
    # This migration is a one-way fix for broken migration 006.
    # Downgrade would re-add the global constraints, but that would
    # violate the portfolio-scoped constraints added by 006.
    # 
    # In practice, downgrade should not be needed. If you must downgrade,
    # you need to:
    # 1. Drop the portfolio-scoped constraints from 006
    # 2. Re-add the global constraints from 005
    # 3. This will fail if there are duplicate source_trade_ids across portfolios
    #
    # For safety, we do nothing in downgrade. To truly reverse, drop the
    # database and re-run migrations from scratch.
    pass
