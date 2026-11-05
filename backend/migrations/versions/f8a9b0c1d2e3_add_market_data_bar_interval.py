"""record the bar grain on market_data_bars so two intervals can coexist

Revision ID: f8a9b0c1d2e3
Revises: d5f6a7b8c9d0
Create Date: 2026-11-04

``market_data_bars`` keyed rows on (instrument, timestamp, provider), which is
ambiguous the moment a venue lends more than one interval: a daily bar stamped
00:00 and a four-hour bar stamped 00:00 are the same key. A live check of the
accumulating warehouse showed the deeper grain silently overwriting nothing and
the shallower read silently returning both — 1,304 "four-hour" rows for a 700-row
fetch, half of them daily candles.

Additive-only and non-destructive:
* ``interval_minutes`` (NULL = grain unknown, written before this revision) is
  added; no existing row is rewritten or backfilled, because nothing here can
  prove what grain a legacy row had.
* the unique key becomes (instrument, provider, interval_minutes, timestamp) so
  each grain owns its rows. ``interval_minutes`` is NULL for legacy rows, and
  Postgres treats NULLs as distinct, which leaves those rows exactly as
  permissive as before.

Downgrade restores the three-column key, which only works if no two grains were
ever stored for the same timestamp.
"""
from alembic import op
import sqlalchemy as sa


revision = "f8a9b0c1d2e3"
down_revision = "d5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("market_data_bars") as batch:
        batch.add_column(sa.Column("interval_minutes", sa.Integer(), nullable=True))
        batch.drop_constraint("uq_instrument_timestamp_provider", type_="unique")
        batch.create_unique_constraint(
            "uq_instrument_timestamp_provider",
            ["instrument_id", "provider_id", "interval_minutes", "bar_timestamp"],
        )


def downgrade() -> None:
    with op.batch_alter_table("market_data_bars") as batch:
        batch.drop_constraint("uq_instrument_timestamp_provider", type_="unique")
        batch.create_unique_constraint(
            "uq_instrument_timestamp_provider",
            ["instrument_id", "bar_timestamp", "provider_id"],
        )
        batch.drop_column("interval_minutes")
