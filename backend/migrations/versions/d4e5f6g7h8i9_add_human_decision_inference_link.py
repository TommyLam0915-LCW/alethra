"""add linked_inference_id to human_decision_events for decision traceability

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-11-19

``POST /simulations/{id}/manual-decision`` records one act in two books: a paper
``decision_records`` row, which has carried ``linked_inference_id`` since
c3d4e5f6g7h8, and a ``human_decision_events`` row — the ledger the personal
learning lane counts owner decisions from (services/agent_strategy.py,
api/decision_labels.py). Before this revision the second row could not name the
inference the owner said they were reacting to: the request validated it, the
decision row stored it, and the human event dropped it. One press therefore left
two descriptions of the same act with different lineage, and the book that
answers "what did this decision come from?" had no column to answer with.

Why NULLABLE:
1. The table already has rows that predate the feature, and no later request can
   honestly say which inference those owners were reacting to.
2. A decision that named no source is the ordinary case and must stay
   distinguishable from a decision whose source was never recorded — the same
   reason decision_records keeps the column nullable.
3. Making it NOT NULL would either refuse valid human decisions or invite the
   writer to invent an identity, and neither is a record.

No FK constraint at DB level, for the reason c3d4e5f6g7h8 states: the value is
``usage_records.id``, a billing-schema row with its own retention, so existence
and ownership are checked by app/services/decision_lineage.py at write time
rather than coupled here. The index matches the one the model's ``index=True``
creates, so ``create_all`` and this upgrade describe the same shape.

Additive-only. Every precondition is checked before anything is created, and any
partial state is refused by name with nothing changed. It does not repair, and it
does not drop and recreate.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'd4e5f6g7h8i9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None

INDEX = 'ix_human_decision_events_linked_inference_id'


def _table_exists(conn, table: str) -> bool:
    return table in inspect(conn).get_table_names()


def _column_exists(conn, table: str, column: str) -> bool:
    return column in [c["name"] for c in inspect(conn).get_columns(table)]


def _index_exists(conn, table: str, index: str) -> bool:
    return index in [i["name"] for i in inspect(conn).get_indexes(table)]


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "human_decision_events"):
        raise RuntimeError(
            "Precondition failed: human_decision_events table does not exist. "
            "This migration requires the table created by h8i9j0k1l2m3."
        )

    if _column_exists(conn, "human_decision_events", "linked_inference_id"):
        raise RuntimeError(
            "Precondition failed: human_decision_events.linked_inference_id "
            "already exists. This migration is additive-only and will not "
            "overwrite existing state."
        )

    if _index_exists(conn, "human_decision_events", INDEX):
        raise RuntimeError(
            f"Precondition failed: index {INDEX} already exists but the column "
            "it would index does not. This migration is additive-only and will "
            "not repair a half-applied schema."
        )

    op.add_column(
        'human_decision_events',
        sa.Column('linked_inference_id', sa.UUID(), nullable=True)
    )
    op.create_index(INDEX, 'human_decision_events', ['linked_inference_id'])


def downgrade() -> None:
    conn = op.get_bind()

    if not _index_exists(conn, "human_decision_events", INDEX):
        raise RuntimeError(
            f"Downgrade precondition: index {INDEX} does not exist. "
            "Cannot drop an index that is not present."
        )

    if not _column_exists(conn, "human_decision_events", "linked_inference_id"):
        raise RuntimeError(
            "Downgrade precondition: human_decision_events.linked_inference_id "
            "does not exist. Cannot drop a column that is not present."
        )

    op.drop_index(INDEX, table_name='human_decision_events')
    op.drop_column('human_decision_events', 'linked_inference_id')
