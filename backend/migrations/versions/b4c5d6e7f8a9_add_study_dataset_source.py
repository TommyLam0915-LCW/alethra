"""record which bars a stored study was priced off

``POST /strategy-lab/research`` can build a study either from the venue's
rolling window or from rows already in ``market_data_bars``, and the run row
recorded only ``provider_id`` and ``data_nature`` — which say the same thing
about both readings of the same Kraken pair. A journal row therefore could not
tell a reader whether its prices came from bytes fetched at request time or
from a series the platform had previously stored and may still hold.

``dataset_source`` is the third family marker: ``venue_completed_bars`` or
``stored_market_data_bars``. It stays NULL on rows written before the field
existed because that history cannot be recovered — a ``venue_completed_bars``
default would relabel every old study as a live read.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

_TABLE = "strategy_study_runs"
_COLUMN = "dataset_source"


def _columns(bind) -> set:
    if _TABLE not in inspect(bind).get_table_names():
        return set()
    return {c["name"] for c in inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in inspect(bind).get_table_names():
        return
    if _COLUMN not in _columns(bind):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(length=40), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        op.drop_column(_TABLE, _COLUMN)
