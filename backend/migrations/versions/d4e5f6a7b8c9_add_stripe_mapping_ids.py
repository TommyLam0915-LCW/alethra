"""add stripe mapping ids for webhook resolution

Adds subscriptions.stripe_subscription_id (maps Stripe sub_xxx ids to local
subscriptions so customer.subscription.* / invoice.* events resolve) and
users.stripe_customer_id (persists the Stripe customer per user so portal and
checkout reuse it instead of creating duplicates).

Revision ID: d4e5f6a7b8c9
Revises: b7d8e9f1a2c3
Create Date: 2026-10-17 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'b7d8e9f1a2c3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'subscriptions',
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
    )
    op.create_index(
        'ix_subscriptions_stripe_subscription_id',
        'subscriptions',
        ['stripe_subscription_id'],
    )
    op.add_column(
        'users',
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
    )
    op.create_index(
        'ix_users_stripe_customer_id',
        'users',
        ['stripe_customer_id'],
    )


def downgrade():
    op.drop_index('ix_users_stripe_customer_id', table_name='users')
    op.drop_column('users', 'stripe_customer_id')
    op.drop_index('ix_subscriptions_stripe_subscription_id', table_name='subscriptions')
    op.drop_column('subscriptions', 'stripe_subscription_id')
