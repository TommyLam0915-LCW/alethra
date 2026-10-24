"""add C2 safety columns to simulation tables

Revision ID: c2safety001
Revises: h8i9j0k1l2m3
Create Date: 2026-10-23

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2safety001'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add state_version, provider_id, provider_confirmed to simulation_sessions
    op.add_column('simulation_sessions', sa.Column('state_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('simulation_sessions', sa.Column('provider_id', sa.String(length=50), nullable=True))
    op.add_column('simulation_sessions', sa.Column('provider_confirmed', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    
    # Add idempotency_key to simulation_decisions
    op.add_column('simulation_decisions', sa.Column('idempotency_key', sa.String(length=100), nullable=True))
    
    # Create unique index on idempotency_key
    op.create_index('ix_simulation_decisions_idempotency_key', 'simulation_decisions', ['idempotency_key'], unique=True)


def downgrade() -> None:
    # Drop index first
    op.drop_index('ix_simulation_decisions_idempotency_key', table_name='simulation_decisions')
    
    # Drop columns
    op.drop_column('simulation_decisions', 'idempotency_key')
    op.drop_column('simulation_sessions', 'provider_confirmed')
    op.drop_column('simulation_sessions', 'provider_id')
    op.drop_column('simulation_sessions', 'state_version')
