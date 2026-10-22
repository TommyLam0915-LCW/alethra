"""add idempotency key to simulation decisions

Revision ID: c1d2e3f4a5b6
Revises: b7d8e9f1a2c3
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'b7d8e9f1a2c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add idempotency_key column to simulation_decisions
    op.add_column('simulation_decisions', sa.Column('idempotency_key', sa.String(100), nullable=True))
    
    # Create unique index on idempotency_key (only for non-null values)
    # Note: SQLite supports partial indexes with WHERE clause
    op.create_index(
        'idx_simulation_decisions_idempotency_key',
        'simulation_decisions',
        ['idempotency_key'],
        unique=True,
        sqlite_where=sa.text('idempotency_key IS NOT NULL')
    )


def downgrade() -> None:
    # Drop the unique index
    op.drop_index('idx_simulation_decisions_idempotency_key', table_name='simulation_decisions')
    
    # Drop the column
    op.drop_column('simulation_decisions', 'idempotency_key')
