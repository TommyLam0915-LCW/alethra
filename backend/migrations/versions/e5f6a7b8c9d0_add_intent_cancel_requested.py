"""add cancel_requested to order intents

Owner cancels of submitted intents now set cancel_requested instead of
marking the intent rejected: the execution agent cancels the live broker
order and reports back, so fills that landed before the cancel are recorded
rather than lost.

Revision ID: e5f6a7b8c9d0
Revises: c7e2f9a41b83
Create Date: 2026-10-17 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'c7e2f9a41b83'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade():
    # Legacy databases stamped mid-chain may not have order_intents yet (the
    # legacy bootstrap creates it with this column), and databases bootstrapped
    # from the current model already have it.
    if "order_intents" not in inspect(op.get_bind()).get_table_names():
        return
    if not _column_exists('order_intents', 'cancel_requested'):
        op.add_column(
            'order_intents',
            sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade():
    if _column_exists('order_intents', 'cancel_requested'):
        op.drop_column('order_intents', 'cancel_requested')
