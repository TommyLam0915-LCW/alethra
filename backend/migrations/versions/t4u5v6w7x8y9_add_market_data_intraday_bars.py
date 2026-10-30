"""add market_data_intraday_bars for live polling

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-10-31

Additive-only: a brand-new table, nothing existing is touched or backfilled.

Intraday bars polled from a live provider live here rather than in
``market_data_bars``. That table backs research datasets and locked experiment
versions; a live poll must not be able to rewrite them. The uniqueness key is
(instrument, provider, timeframe, bar_timestamp), so one forming bar updates
its own row instead of creating a duplicate candle and 1-minute versus
5-minute series never collide.
"""
from alembic import op
import sqlalchemy as sa


revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_data_intraday_bars",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("instrument_id", sa.String(length=50), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "provider_id",
            "timeframe",
            "bar_timestamp",
            name="uq_intraday_instrument_provider_timeframe_timestamp",
        ),
    )
    op.create_index(
        "ix_market_data_intraday_bars_instrument_id",
        "market_data_intraday_bars",
        ["instrument_id"],
    )
    op.create_index(
        "ix_market_data_intraday_instrument_timeframe_ts",
        "market_data_intraday_bars",
        ["instrument_id", "timeframe", "bar_timestamp"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_data_intraday_instrument_timeframe_ts",
        table_name="market_data_intraday_bars",
    )
    op.drop_index(
        "ix_market_data_intraday_bars_instrument_id",
        table_name="market_data_intraday_bars",
    )
    op.drop_table("market_data_intraday_bars")
