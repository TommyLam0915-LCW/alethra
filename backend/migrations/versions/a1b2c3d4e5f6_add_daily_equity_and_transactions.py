"""add daily equity curve and transactions to research runs

Revision ID: a1b2c3d4e5f6
Revises: 8616c0c9aa3e
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = 'a1b2c3d4e5f6'
down_revision = '8616c0c9aa3e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('research_runs', sa.Column('daily_equity_curve', JSONB, nullable=True))
    op.add_column('research_runs', sa.Column('transactions', JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column('research_runs', 'transactions')
    op.drop_column('research_runs', 'daily_equity_curve')
