"""merge integration heads

Revision ID: 3c79f3c5cd0b
Revises: b2c3d4e5f6a7, b7c8d9e0f1a2, d4e5f6a7b8c9, e5f6a7b8c9d0
Create Date: 2026-10-17 12:15:58.090870

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c79f3c5cd0b'
down_revision: Union[str, None] = ('b2c3d4e5f6a7', 'b7c8d9e0f1a2', 'd4e5f6a7b8c9', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
