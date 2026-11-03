"""add durable strategy study records

Two additive tables for the strategy research lane: one study row per
completed walk-forward run (settings, folds, disclosures and the exact
completed bars it read) and one row per out-of-sample buy/sell (prices, costs,
exit rule and a mechanical earn/loss explanation). No existing table is read or
rewritten, and nothing here is a broker order or a real-money fill.

Revision ID: c4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "c4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "strategy_study_runs"):
        op.create_table(
            "strategy_study_runs",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("lane", sa.String(50), nullable=False),
            sa.Column("research_version", sa.String(20), nullable=False),
            sa.Column("instrument_id", sa.String(64), nullable=False),
            sa.Column("interval_minutes", sa.Integer(), nullable=False),
            sa.Column("provider_id", sa.String(32), nullable=False),
            sa.Column("data_nature", sa.String(20), nullable=False),
            sa.Column("bar_count", sa.Integer(), nullable=False),
            sa.Column("first_open_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_open_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dataset_fingerprint", sa.String(64), nullable=True),
            sa.Column("dataset_integrity_ok", sa.Boolean(), nullable=True),
            sa.Column("dataset_gap_count", sa.Integer(), nullable=True),
            sa.Column("dataset_missing_intervals", sa.Integer(), nullable=True),
            sa.Column("settings_json", JSONB(), nullable=False),
            sa.Column("walk_forward_json", JSONB(), nullable=False),
            sa.Column("highlights_json", JSONB(), nullable=True),
            sa.Column("disclosures_json", JSONB(), nullable=True),
            sa.Column("bars_json", JSONB(), nullable=False),
            sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_strategy_study_runs_user_id", "strategy_study_runs", ["user_id"]
        )
        op.create_index(
            "ix_strategy_study_runs_instrument_id",
            "strategy_study_runs",
            ["instrument_id"],
        )

    if not _table_exists(bind, "strategy_study_trades"):
        op.create_table(
            "strategy_study_trades",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("strategy_study_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("trade_index", sa.Integer(), nullable=False),
            sa.Column("fold", sa.Integer(), nullable=True),
            sa.Column("rule_id", sa.String(48), nullable=False),
            sa.Column("rule_label_zh", sa.String(80), nullable=True),
            sa.Column("exit_id", sa.String(48), nullable=False),
            sa.Column("exit_label_zh", sa.String(80), nullable=True),
            sa.Column("direction", sa.String(5), nullable=False),
            sa.Column("exit_reason", sa.String(24), nullable=False),
            sa.Column("outcome", sa.String(8), nullable=False),
            sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("exit_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("entry_price", sa.Numeric(24, 8), nullable=False),
            sa.Column("exit_price", sa.Numeric(24, 8), nullable=False),
            sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
            sa.Column("gross_pnl", sa.Numeric(24, 8), nullable=False),
            sa.Column("commission", sa.Numeric(24, 8), nullable=False),
            sa.Column("slippage_cost", sa.Numeric(24, 8), nullable=False),
            sa.Column("total_cost", sa.Numeric(24, 8), nullable=False),
            sa.Column("net_pnl", sa.Numeric(24, 8), nullable=False),
            sa.Column("cash_after", sa.Numeric(24, 8), nullable=False),
            sa.Column("bars_held", sa.Integer(), nullable=False),
            sa.Column("mfe_pct", sa.Numeric(12, 4), nullable=True),
            sa.Column("mae_pct", sa.Numeric(12, 4), nullable=True),
            sa.Column("cost_ratio_pct", sa.Numeric(12, 4), nullable=True),
            sa.Column("pnl_reason_zh", sa.Text(), nullable=False),
            sa.Column("pnl_reason_en", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint(
                "run_id",
                "trade_index",
                name="uq_strategy_study_trades_run_index",
            ),
        )
        op.create_index(
            "ix_strategy_study_trades_run_id", "strategy_study_trades", ["run_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "strategy_study_trades"):
        op.drop_index("ix_strategy_study_trades_run_id", table_name="strategy_study_trades")
        op.drop_table("strategy_study_trades")
    if _table_exists(bind, "strategy_study_runs"):
        op.drop_index(
            "ix_strategy_study_runs_instrument_id", table_name="strategy_study_runs"
        )
        op.drop_index("ix_strategy_study_runs_user_id", table_name="strategy_study_runs")
        op.drop_table("strategy_study_runs")
