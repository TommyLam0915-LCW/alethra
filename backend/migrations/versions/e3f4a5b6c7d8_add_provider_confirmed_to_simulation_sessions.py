"""add provider_confirmed to simulation sessions

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3f4a5b6c7d8'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add provider_confirmed column to simulation_sessions
    op.add_column('simulation_sessions', 
        sa.Column('provider_confirmed', sa.Boolean(), nullable=False, server_default='0'))
    
    # Create index for faster lookups
    op.create_index(
        'idx_simulation_sessions_provider_confirmed',
        'simulation_sessions',
        ['provider_confirmed']
    )


def downgrade() -> None:
    # Drop the index
    op.drop_index('idx_simulation_sessions_provider_confirmed', table_name='simulation_sessions')
    
    # Drop the column
    op.drop_column('simulation_sessions', 'provider_confirmed')
