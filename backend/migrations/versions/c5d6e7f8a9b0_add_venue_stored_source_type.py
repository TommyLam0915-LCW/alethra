"""give the stored-series registry a source type it can honestly write

``app.services.dataset_registry`` records a ``datasets`` row for the exact
``market_data_bars`` window a stored-only backtest priced off. The three
existing labels cannot describe that row: ``demo`` would mark real Kraken bars
as synthetic, ``licensed`` would assert licence terms nobody verified, and
``user_uploaded`` would credit a person with supplying them. ``venue_stored``
means only what the registry actually knows — the platform fetched these bars
from a venue and still holds them.

The label is not added to the admin dataset form, so nobody can pick it by
hand; a row carrying it is written by the registry or not at all.

PostgreSQL enum values are added outside the row transaction and SQLite has no
enum type at all (``Base.metadata.create_all`` writes a CHECK over the labels,
and ``SourceType`` in Python already carries the new one), so this migration is
PG-only and leaves nothing to undo: ``DROP VALUE`` does not exist.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-11-24

"""
from alembic import op
import sqlalchemy as sa


revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(
        sa.text("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'venue_stored'")
    )


def downgrade() -> None:
    # An enum label cannot be removed while any row may carry it, and dropping
    # the type would rewrite the column. The label stays; nothing reads it
    # except the registry.
    pass
