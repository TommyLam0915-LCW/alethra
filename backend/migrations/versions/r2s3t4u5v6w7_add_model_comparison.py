"""add personal model comparison runs and explicit selection history

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-10-29

Additive-only:
* ``model_comparison`` — a new ``evaluationtype`` value for evaluations that
  compare two immutable model versions on a shared or per-version test slice.
* ``personal_model_comparison_runs`` — the durable record of one comparison the
  owner ran, keyed by ``(owner_id, operation_key)`` so a retry is idempotent.
* ``personal_model_selections`` — append-only history of explicit active-version
  switches and rollbacks with the previous version and the owner's rationale.

No existing model version, artifact, evaluation or inference record is changed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL enums gain values outside the row transaction.
        bind.execute(
            sa.text(
                "ALTER TYPE evaluationtype ADD VALUE IF NOT EXISTS "
                "'model_comparison'"
            )
        )

    op.create_table(
        "personal_model_comparison_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("operation_key", sa.String(length=128), nullable=True),
        sa.Column("request_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["record_id"], ["model_research_records.id"]
        ),
        sa.ForeignKeyConstraint(["version_a_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(["version_b_id"], ["model_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "operation_key",
            name="uq_personal_comparison_operation",
        ),
    )
    op.create_index(
        "ix_personal_comparison_owner_created",
        "personal_model_comparison_runs",
        ["owner_id", "created_at"],
    )

    op.create_table(
        "personal_model_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("comparison_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "select",
                "rollback",
                name="personalmodelselectionaction",
            ),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["record_id"], ["model_research_records.id"]
        ),
        sa.ForeignKeyConstraint(["version_id"], ["model_versions.id"]),
        sa.ForeignKeyConstraint(
            ["previous_version_id"], ["model_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["comparison_run_id"], ["personal_model_comparison_runs.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_personal_selection_record_created",
        "personal_model_selections",
        ["record_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_selection_record_created",
        table_name="personal_model_selections",
    )
    op.drop_table("personal_model_selections")
    op.drop_index(
        "ix_personal_comparison_owner_created",
        table_name="personal_model_comparison_runs",
    )
    op.drop_table("personal_model_comparison_runs")
    if op.get_bind().dialect.name == "postgresql":
        postgresql.ENUM(
            name="personalmodelselectionaction"
        ).drop(op.get_bind(), checkfirst=True)
    # PostgreSQL cannot remove an enum value; the added value is inert once no
    # row uses it, so the downgrade leaves it in place.
