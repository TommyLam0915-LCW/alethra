"""merge market-data and daily-equity branches

Joins the two migration branches that were split at the initial schema.
This merge migration has no operations; it only establishes the graph
relationship so Alembic sees a single head.

Revision ID: merge001
Revises: c2safety001, m1a2b3c4d5e6
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "merge001"
down_revision: Union[str, Sequence[str]] = ("c2safety001", "m1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge migration — no schema changes, only joins branches
    pass


def downgrade() -> None:
    # Merge migration — no schema changes to reverse
    pass
