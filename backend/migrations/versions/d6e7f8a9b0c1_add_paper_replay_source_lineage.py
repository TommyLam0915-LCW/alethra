"""record the exact upstream population a paper replay consumed

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-11-25

``execute_historical_replay`` selects its source trades from
``strategy_study_trades`` by instrument, interval, rule, owner and a
wall-clock window, and keeps the result only in memory. Nothing on the run row
says which upstream study runs, which trades, which research version or how
many rows were priced, so a run's ledger cannot be re-derived after the fact,
and a study run inserted or repaired later silently changes what the same
``POST /replay/{run_id}`` would match on its next attempt.

One nullable ``JSONB`` column holds the capture, written once by the attempt
that reads the rows: a completed, cancelled or failed run keeps describing the
population it actually traded, and a later upstream change can be detected
against the recorded digest instead of rewriting history. ``NULL`` means no
attempt has read anything yet — it is not a claim of zero upstream rows, so
the absence is readable as absence.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'd6e7f8a9b0c1'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists('paper_trading_runs', 'source_lineage'):
        op.add_column(
            'paper_trading_runs',
            sa.Column('source_lineage', postgresql.JSONB(astext_type=sa.Text()),
                      nullable=True),
        )


def downgrade() -> None:
    if _column_exists('paper_trading_runs', 'source_lineage'):
        op.drop_column('paper_trading_runs', 'source_lineage')
