"""add commission to fill records

Fill reports from the execution agent include commission, but the field was
not stored in the database. This migration adds the commission column to
fill_records so that execution costs are durably tracked.

Revision ID: g7h8i9j0k1l2
Revises: a8d9e0f1b2c3
Create Date: 2026-10-20 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'g7h8i9j0k1l2'
down_revision = 'a8d9e0f1b2c3'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade():
    if "fill_records" not in inspect(op.get_bind()).get_table_names():
        return
    if not _column_exists('fill_records', 'commission'):
        op.add_column(
            'fill_records',
            sa.Column('commission', sa.Float(), nullable=True),
        )


def downgrade():
    if _column_exists('fill_records', 'commission'):
        op.drop_column('fill_records', 'commission')
