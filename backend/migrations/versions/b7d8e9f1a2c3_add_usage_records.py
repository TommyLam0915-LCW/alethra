"""add usage records table

Revision ID: b7d8e9f1a2c3
Revises: a1b2c3d4e5f6
Create Date: 2026-10-16 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = 'b7d8e9f1a2c3'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Create usage_records table
    op.create_table(
        'usage_records',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('subscription_id', UUID(as_uuid=True), sa.ForeignKey('subscriptions.id'), nullable=True),
        sa.Column('endpoint', sa.String(255), nullable=False),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer(), default=0, nullable=False),
        sa.Column('cost', sa.Integer(), default=0, nullable=False),
        sa.Column('metadata_json', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, index=True),
    )

    # Add composite indexes for efficient querying
    op.create_index('ix_usage_user_date', 'usage_records', ['user_id', 'created_at'])
    op.create_index('ix_usage_subscription', 'usage_records', ['subscription_id', 'created_at'])


def downgrade():
    op.drop_index('ix_usage_subscription', table_name='usage_records')
    op.drop_index('ix_usage_user_date', table_name='usage_records')
    op.drop_table('usage_records')
