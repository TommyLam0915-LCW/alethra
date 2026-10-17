"""add usage records table

Revision ID: b7d8e9f1a2c3
Revises: a1b2c3d4e5f6
Create Date: 2026-10-16 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = 'b7d8e9f1a2c3'
# Linearized chain: originally branched off a1b2c3d4e5f6; folded after
# b2c3d4e5f6a7 so the branch has a single head (multi-parent merges trip an
# alembic 1.14 KeyError when upgrading from a legacy stamp below the branchpoint).
# usage_records still runs after the legacy-schema conversion either way.
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def upgrade():
    # Legacy bootstrap already creates usage_records from the current model.
    if _table_exists('usage_records'):
        return
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
    if not _table_exists('usage_records'):
        return
    op.drop_index('ix_usage_subscription', table_name='usage_records')
    op.drop_index('ix_usage_user_date', table_name='usage_records')
    op.drop_table('usage_records')
