"""add crypto quote bars and exact paper execution receipts

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-10-31

Additive-only: two new tables. No existing table, column or index is changed.

``crypto_quote_bars`` carries the completed-bar quote contract the crypto paper
lane prices from. It exists beside ``market_data_bars`` because that table has
no interval identity, so a stored row cannot prove its own interval.

``crypto_paper_executions`` records one simulated fill at Decimal precision and
names the shared Float ledger row it mirrors. It is a receipt, not a second
ledger: cash and positions still move only through ``paper_execution.book_fill``.
"""
from alembic import op
import sqlalchemy as sa


revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_quote_bars",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("instrument_id", sa.String(length=50), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("open_time", sa.DateTime(), nullable=False),
        sa.Column("close_time", sa.DateTime(), nullable=False),
        sa.Column("open", sa.Numeric(30, 12), nullable=False),
        sa.Column("high", sa.Numeric(30, 12), nullable=False),
        sa.Column("low", sa.Numeric(30, 12), nullable=False),
        sa.Column("close", sa.Numeric(30, 12), nullable=False),
        sa.Column("volume", sa.Numeric(30, 12), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("data_nature", sa.String(length=16), nullable=False),
        sa.Column("price_source", sa.String(length=100), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "provider_id",
            "interval_minutes",
            "open_time",
            name="uq_crypto_quote_instrument_provider_interval_open",
        ),
    )
    op.create_index(
        "ix_crypto_quote_bars_instrument_id",
        "crypto_quote_bars",
        ["instrument_id"],
    )

    op.create_table(
        "crypto_paper_executions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("portfolio_id", sa.UUID(), nullable=False),
        sa.Column("paper_order_id", sa.UUID(), nullable=False),
        sa.Column("paper_fill_id", sa.UUID(), nullable=False),
        sa.Column("quote_bar_id", sa.UUID(), nullable=False),
        sa.Column("venue", sa.String(length=20), nullable=False),
        sa.Column("instrument_id", sa.String(length=50), nullable=False),
        sa.Column("base_asset", sa.String(length=16), nullable=False),
        sa.Column("quote_asset", sa.String(length=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("observed_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("execution_price", sa.Numeric(30, 12), nullable=False),
        sa.Column("gross_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("commission_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("slippage_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("net_cash_change", sa.Numeric(30, 12), nullable=False),
        sa.Column("ledger_gross_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("ledger_commission_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("ledger_slippage_amount", sa.Numeric(30, 12), nullable=False),
        sa.Column("ledger_net_cash_change", sa.Numeric(30, 12), nullable=False),
        sa.Column("commission_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("slippage_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("price_source", sa.String(length=100), nullable=False),
        sa.Column("data_nature", sa.String(length=16), nullable=False),
        sa.Column("data_as_of", sa.DateTime(), nullable=False),
        sa.Column("bar_open_time", sa.DateTime(), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("execution_assumption", sa.String(length=200), nullable=False),
        sa.Column("cost_model", sa.Text(), nullable=False),
        sa.Column("ledger_mirror", sa.String(length=60), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"]),
        sa.ForeignKeyConstraint(["paper_order_id"], ["paper_orders.id"]),
        sa.ForeignKeyConstraint(["paper_fill_id"], ["paper_fills.id"]),
        sa.ForeignKeyConstraint(["quote_bar_id"], ["crypto_quote_bars.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_order_id"),
        sa.UniqueConstraint("paper_fill_id"),
    )
    op.create_index(
        "ix_crypto_paper_executions_portfolio_id",
        "crypto_paper_executions",
        ["portfolio_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crypto_paper_executions_portfolio_id",
        table_name="crypto_paper_executions",
    )
    op.drop_table("crypto_paper_executions")
    op.drop_index(
        "ix_crypto_quote_bars_instrument_id",
        table_name="crypto_quote_bars",
    )
    op.drop_table("crypto_quote_bars")
