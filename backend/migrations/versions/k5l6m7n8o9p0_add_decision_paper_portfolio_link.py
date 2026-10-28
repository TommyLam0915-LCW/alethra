"""link decision records to the paper portfolio they belong to

GET /api/v1/paper-portfolio/{portfolio_id}/decisions could only filter by owner,
so a user with two portfolios saw both portfolios' decisions in each journal and
the per-portfolio decision count was wrong. Adds a nullable link column and
backfills it from the order that references each decision; decisions that predate
the link stay NULL and remain visible rather than disappearing from the journal.

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-10-25

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = 'k5l6m7n8o9p0'
down_revision = 'j4k5l6m7n8o9'
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    return table in inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    if not _table_exists('decision_records'):
        return
    if 'paper_portfolio_id' not in _columns('decision_records'):
        op.add_column(
            'decision_records',
            sa.Column(
                'paper_portfolio_id',
                UUID(as_uuid=True),
                sa.ForeignKey('paper_portfolios.id'),
                nullable=True,
            ),
        )
    if 'ix_decision_records_paper_portfolio_id' not in _indexes('decision_records'):
        op.create_index(
            'ix_decision_records_paper_portfolio_id',
            'decision_records',
            ['paper_portfolio_id'],
        )
    if _table_exists('paper_orders'):
        op.execute(
            sa.text(
                """
                UPDATE decision_records
                   SET paper_portfolio_id = (
                        SELECT o.portfolio_id
                          FROM paper_orders o
                         WHERE o.decision_id = decision_records.id
                         LIMIT 1
                   )
                 WHERE paper_portfolio_id IS NULL
                   AND EXISTS (
                        SELECT 1 FROM paper_orders o WHERE o.decision_id = decision_records.id
                   )
                """
            )
        )


def downgrade():
    if not _table_exists('decision_records'):
        return
    if 'ix_decision_records_paper_portfolio_id' in _indexes('decision_records'):
        op.drop_index('ix_decision_records_paper_portfolio_id', table_name='decision_records')
    if 'paper_portfolio_id' in _columns('decision_records'):
        op.drop_column('decision_records', 'paper_portfolio_id')
