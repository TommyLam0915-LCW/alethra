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
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'b7d8e9f1a2c3'
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    return table in inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return index in {i["name"] for i in inspect(bind).get_indexes(table)}


def upgrade():
    # Legacy databases stamped mid-chain may lack subscriptions until the
    # legacy bootstrap creates it (with the column already present).
    if _table_exists('subscriptions'):
        if not _column_exists('subscriptions', 'stripe_subscription_id'):
            op.add_column(
                'subscriptions',
                sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
            )
        if not _index_exists('subscriptions', 'ix_subscriptions_stripe_subscription_id'):
            op.create_index(
                'ix_subscriptions_stripe_subscription_id',
                'subscriptions',
                ['stripe_subscription_id'],
            )
    if not _column_exists('users', 'stripe_customer_id'):
        op.add_column(
            'users',
            sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        )
    if not _index_exists('users', 'ix_users_stripe_customer_id'):
        op.create_index(
            'ix_users_stripe_customer_id',
            'users',
            ['stripe_customer_id'],
        )


def downgrade():
    if _index_exists('users', 'ix_users_stripe_customer_id'):
        op.drop_index('ix_users_stripe_customer_id', table_name='users')
    if _column_exists('users', 'stripe_customer_id'):
        op.drop_column('users', 'stripe_customer_id')
    if _table_exists('subscriptions'):
        if _index_exists('subscriptions', 'ix_subscriptions_stripe_subscription_id'):
            op.drop_index('ix_subscriptions_stripe_subscription_id', table_name='subscriptions')
        if _column_exists('subscriptions', 'stripe_subscription_id'):
            op.drop_column('subscriptions', 'stripe_subscription_id')
