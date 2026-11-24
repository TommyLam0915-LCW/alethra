"""record which data source a study was asked to use, beside which it used

Revision ID: a9b0c1d2e3f4
Revises: d6e7f8a9b0c1
Create Date: 2026-11-25

``strategy_study_runs.dataset_source`` stores where the bars actually came
from, and nothing stores what the caller asked for. ``POST
/strategy-lab/research/runs`` takes ``data_source`` as ``venue`` or ``stored``,
so the two coincide today and the request looks self-evident — but a stored
series that was asked for and a stored series that happened to be available are
the same row, and the pair is what a reader needs in order to tell a source
policy from a source outcome.

One nullable ``VARCHAR(40)`` holds the requested value per run. ``NULL`` means
the run was written before this field existed, which is the same reading its
sibling ``dataset_source`` already carries; it is not a claim that nothing was
requested.

Part of the source-contract item: the backtest path had both halves in its
result and this path had one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'a9b0c1d2e3f4'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists('strategy_study_runs', 'dataset_source_requested'):
        op.add_column(
            'strategy_study_runs',
            sa.Column('dataset_source_requested', sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    if _column_exists('strategy_study_runs', 'dataset_source_requested'):
        op.drop_column('strategy_study_runs', 'dataset_source_requested')
