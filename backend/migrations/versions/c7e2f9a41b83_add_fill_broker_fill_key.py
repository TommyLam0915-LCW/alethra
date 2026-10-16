"""add fill_records.broker_fill_key for idempotent broker fill reports

Revision ID: c7e2f9a41b83
Revises: 55a8d07a7f40
Create Date: 2026-10-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7e2f9a41b83"
down_revision: Union[str, None] = "55a8d07a7f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.add_column(
            sa.Column("broker_fill_key", sa.String(length=255), nullable=True)
        )
        batch_op.create_index(
            "ix_fill_records_broker_fill_key", ["broker_fill_key"]
        )
        batch_op.create_unique_constraint(
            "uq_fill_records_intent_fill_key",
            ["order_intent_id", "broker_fill_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("fill_records") as batch_op:
        batch_op.drop_constraint(
            "uq_fill_records_intent_fill_key", type_="unique"
        )
        batch_op.drop_index("ix_fill_records_broker_fill_key")
        batch_op.drop_column("broker_fill_key")
