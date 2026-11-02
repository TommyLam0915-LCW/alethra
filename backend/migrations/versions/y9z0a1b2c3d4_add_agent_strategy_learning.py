"""add agent strategy learning candidates, outcomes and snapshots

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-11-02

Additive only. Three new tables support a rule strategy's own candidate
signals; no existing row is read or rewritten. The only change to an existing
type is adding ``agent`` and ``unknown`` to ``paperdecisionactortype`` so a
paper decision records the verified credential channel instead of being forced
into ``human`` or ``system``. Historical rows keep their stored value.

Downgrade removes the new tables but leaves the enum value in place: PostgreSQL
cannot drop a single enum value without rebuilding the type, and rebuilding a
type used by existing rows is not a safe automatic downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "strategy_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiment_versions.id"),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_kind", sa.String(length=16), nullable=False),
        sa.Column("rule_key", sa.String(length=64), nullable=False),
        sa.Column("rule_parameters_json", JSONB, nullable=False),
        sa.Column("logged_action", sa.String(length=32), nullable=False),
        sa.Column("created_by_actor_type", sa.String(length=16), nullable=False),
        sa.Column("signal_date", sa.String(length=10), nullable=False),
        sa.Column("decision_instant", sa.DateTime(), nullable=False),
        sa.Column("decision_instant_source", sa.String(length=32), nullable=False),
        sa.Column("data_cutoff_at", sa.String(length=10), nullable=False),
        sa.Column("feature_schema_version", sa.String(length=64), nullable=False),
        sa.Column("feature_names_json", JSONB, nullable=False),
        sa.Column("features_json", JSONB, nullable=False),
        sa.Column("feature_bar_date", sa.String(length=10), nullable=True),
        sa.Column("execution_policy_version", sa.String(length=64), nullable=False),
        sa.Column("execution_feasibility", sa.String(length=24), nullable=False),
        sa.Column("rejection_reason", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=220), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint(
            "owner_id",
            "strategy_version_id",
            "instrument_id",
            "dedupe_key",
            name="uq_agent_candidate_identity",
        ),
    )
    op.create_index(
        "ix_agent_candidates_version", "agent_candidates", ["strategy_version_id"]
    )
    op.create_index(
        "ix_agent_candidates_signal_date", "agent_candidates", ["signal_date"]
    )

    op.create_table(
        "agent_candidate_outcomes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_candidates.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("outcome_status", sa.String(length=16), nullable=False),
        sa.Column("label_maturity", sa.String(length=16), nullable=False),
        sa.Column(
            "label_eligible", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("outcome_source", sa.String(length=32), nullable=False),
        sa.Column(
            "counterfactual", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "observed_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("paper_orders.id"),
            nullable=True,
        ),
        sa.Column(
            "observed_fill_id",
            UUID(as_uuid=True),
            sa.ForeignKey("paper_fills.id"),
            nullable=True,
        ),
        sa.Column("entry_execution_date", sa.String(length=10), nullable=True),
        sa.Column("exit_execution_date", sa.String(length=10), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("gross_return_pct", sa.Float(), nullable=True),
        sa.Column("net_return_pct", sa.Float(), nullable=True),
        sa.Column("cost_model_version", sa.String(length=64), nullable=False),
        sa.Column("cost_model_json", JSONB, nullable=False),
        sa.Column("exit_policy", sa.String(length=64), nullable=False),
        sa.Column("exclusion_reason", sa.String(length=64), nullable=True),
        sa.Column("notes_json", JSONB, nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "agent_dataset_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "strategy_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiment_versions.id"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("consent_policy_version", sa.String(length=64), nullable=False),
        sa.Column("consent_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("candidate_ids_json", JSONB, nullable=False),
        sa.Column("rows_json", JSONB, nullable=False),
        sa.Column("feature_names_json", JSONB, nullable=False),
        sa.Column("label_definition_json", JSONB, nullable=False),
        sa.Column("exclusion_breakdown_json", JSONB, nullable=False),
        sa.Column("outcome_distribution_json", JSONB, nullable=False),
        sa.Column("split_manifest_json", JSONB, nullable=False),
        sa.Column(
            "included_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "excluded_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("date_range_start", sa.String(length=10), nullable=True),
        sa.Column("date_range_end", sa.String(length=10), nullable=True),
        sa.Column("data_cutoff_at", sa.String(length=10), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index(
        "ix_agent_snapshots_version",
        "agent_dataset_snapshots",
        ["strategy_version_id"],
    )
    op.create_index(
        "ix_agent_snapshots_status", "agent_dataset_snapshots", ["status"]
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in ("agent", "unknown"):
            bind.execute(
                sa.text(
                    "ALTER TYPE paperdecisionactortype ADD VALUE IF NOT EXISTS "
                    f"'{value}'"
                )
            )


def downgrade() -> None:
    op.drop_index("ix_agent_snapshots_status", table_name="agent_dataset_snapshots")
    op.drop_index("ix_agent_snapshots_version", table_name="agent_dataset_snapshots")
    op.drop_table("agent_dataset_snapshots")
    op.drop_table("agent_candidate_outcomes")
    op.drop_index("ix_agent_candidates_signal_date", table_name="agent_candidates")
    op.drop_index("ix_agent_candidates_version", table_name="agent_candidates")
    op.drop_table("agent_candidates")
