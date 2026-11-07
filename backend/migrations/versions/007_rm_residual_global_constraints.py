"""Remove residual global unique constraints from broken migration 006.

Revision ID: rm_residual_global_constraints
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


def upgrade() -> None:
    """Drop residual global unique constraints from broken 006."""
    conn = op.get_bind()
    
    # Drop residual global constraints from decision_records (if they exist)
    result = conn.execute(text("""
        SELECT constraint_name 
        FROM information_schema.table_constraints 
        WHERE table_name = 'decision_records' 
          AND constraint_type = 'UNIQUE'
          AND constraint_name = 'uq_decision_records_source_trade_type'
    """))
    
    if result.fetchone():
        op.drop_constraint('uq_decision_records_source_trade_type', 'decision_records', type_='unique')
    
    # Drop residual global constraints from paper_positions (if they exist)
    result = conn.execute(text("""
        SELECT constraint_name 
        FROM information_schema.table_constraints 
        WHERE table_name = 'paper_positions' 
          AND constraint_type = 'UNIQUE'
          AND constraint_name = 'uq_paper_positions_source_trade'
    """))
    
    if result.fetchone():
        op.drop_constraint('uq_paper_positions_source_trade', 'paper_positions', type_='unique')


def downgrade() -> None:
    """Re-add the global constraints (not recommended — use fixed 006 instead)."""
    # This is a no-op because the fixed 006 adds portfolio-scoped constraints.
    # Re-adding global constraints would be incorrect.
    pass
