"""Add market data storage tables for OHLCV bars and metadata.

Enables persistence of market data fetched from providers (demo, LSEG, etc.)
to avoid repeated API calls and enable historical analysis. Tracks data
quality, provenance, and staleness per instrument.

Revision ID: m1a2b3c4d5e6
Revises: 3f9c2d81b7a4
Create Date: 2026-10-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "m1a2b3c4d5e6"
down_revision: Union[str, None] = "3f9c2d81b7a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_data_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.String(50), nullable=False),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("adjustment_factor", sa.Float(), nullable=True),
        sa.Column("is_adjusted", sa.String(20), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "bar_timestamp", "provider_id", name="uq_instrument_timestamp_provider"),
    )
    op.create_index("ix_market_data_instrument_date", "market_data_bars", ["instrument_id", "bar_date"])
    op.create_index("ix_market_data_bars_instrument_id", "market_data_bars", ["instrument_id"])

    op.create_table(
        "market_data_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.String(50), nullable=False),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("data_nature", sa.String(20), nullable=False),
        sa.Column("adjustment_status", sa.String(50), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("provider_note", sa.String(500), nullable=True),
        sa.Column("provider_note_zh", sa.String(500), nullable=True),
        sa.Column("first_bar_date", sa.Date(), nullable=True),
        sa.Column("last_bar_date", sa.Date(), nullable=True),
        sa.Column("total_bars", sa.Integer(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("consecutive_errors", sa.Integer(), nullable=True),
        sa.Column("health_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_id", "provider_id", name="uq_instrument_provider_metadata"),
    )
    op.create_index("ix_market_data_metadata_instrument_id", "market_data_metadata", ["instrument_id"])


def downgrade() -> None:
    op.drop_index("ix_market_data_metadata_instrument_id", table_name="market_data_metadata")
    op.drop_table("market_data_metadata")
    op.drop_index("ix_market_data_bars_instrument_id", table_name="market_data_bars")
    op.drop_index("ix_market_data_instrument_date", table_name="market_data_bars")
    op.drop_table("market_data_bars")
