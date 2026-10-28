"""add idempotency key and actor type to paper orders

Paper orders had no way to recognise a retry, so a double-clicked submit or a
re-sent API request silently created a second order and a second cash movement.
Adds the client idempotency key with a partial unique index (only rows carrying
a key are deduplicated), plus the actor type so an API-key-driven order is
distinguishable from an interactive one.

Revision ID: j4k5l6m7n8o9
Revises: h8i9j0k1l2m3
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'j4k5l6m7n8o9'
down_revision = 'h8i9j0k1l2m3'
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    return table in inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    if not _table_exists('paper_orders'):
        return
    cols = _columns('paper_orders')
    if 'idempotency_key' not in cols:
        op.add_column('paper_orders', sa.Column('idempotency_key', sa.String(128), nullable=True))
    if 'actor_type' not in cols:
        op.add_column(
            'paper_orders',
            sa.Column('actor_type', sa.String(20), nullable=False, server_default='human'),
        )
    if 'uq_paper_order_portfolio_idempotency' not in _indexes('paper_orders'):
        op.create_index(
            'uq_paper_order_portfolio_idempotency',
            'paper_orders',
            ['portfolio_id', 'idempotency_key'],
            unique=True,
            postgresql_where=sa.text('idempotency_key IS NOT NULL'),
            sqlite_where=sa.text('idempotency_key IS NOT NULL'),
        )


def downgrade():
    if not _table_exists('paper_orders'):
        return
    if 'uq_paper_order_portfolio_idempotency' in _indexes('paper_orders'):
        op.drop_index('uq_paper_order_portfolio_idempotency', table_name='paper_orders')
    cols = _columns('paper_orders')
    if 'actor_type' in cols:
        op.drop_column('paper_orders', 'actor_type')
    if 'idempotency_key' in cols:
        op.drop_column('paper_orders', 'idempotency_key')
