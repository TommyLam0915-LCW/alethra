"""add state_version to sessions

Revision ID: add_state_version
Revises: 
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_state_version'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on = None

def upgrade():
    # Add state_version column with default 0
    op.add_column('simulation_sessions', sa.Column('state_version', sa.Integer(), nullable=False, server_default='0'))

def downgrade():
    op.drop_column('simulation_sessions', 'state_version')
