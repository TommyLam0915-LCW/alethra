"""add provider_id to simulation sessions

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2e3f4a5b6c7'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add provider_id column to simulation_sessions
    op.add_column('simulation_sessions', sa.Column('provider_id', sa.String(50), nullable=True))
    
    # Create index on provider_id for faster lookups
    op.create_index(
        'idx_simulation_sessions_provider_id',
        'simulation_sessions',
        ['provider_id']
    )


def downgrade() -> None:
    # Drop the index
    op.drop_index('idx_simulation_sessions_provider_id', table_name='simulation_sessions')
    
    # Drop the column
    op.drop_column('simulation_sessions', 'provider_id')
