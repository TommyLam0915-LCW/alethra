"""record the owner's own model-activation approvals

Revision ID: a7c1e4f9b2d5
Revises: f8a9b0c1d2e3
Create Date: 2026-11-04

``agent_strategy_training_jobs.activation_status`` was a constant: nothing in the
codebase could ever read it as anything but ``NOT_APPROVED``, so the training
path could not be audited for the one judgment that matters — whether the owner
switched this exact artifact on. This table makes that judgment a recorded fact
the gate reads, and it is additive only: no existing row, column or constraint
is touched, and a fresh database with zero approvals behaves exactly as before
(``NOT_APPROVED``).

The row binds one approval to one artifact: ``artifact_checksum``,
``dataset_checksum`` and ``training_contract_version`` all have to match the job
for the gate to honour it, so approving a model never carries over to the next
training run. ``owner_id`` and ``approved_by_user_id`` are stored apart for the
same reason consent is: the route passes the authenticated user, and the gate
refuses an approval recorded by anyone but the owner. ``channel`` is kept even
when it does not qualify, so a row written by a seed script or a CLI stays
visible as the non-owner provenance it is.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c1e4f9b2d5"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_model_activation_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=False),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("training_contract_version", sa.String(length=64), nullable=False),
        sa.Column("consent_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_policy_version", sa.String(length=64), nullable=False),
        sa.Column("acknowledgement_text_version", sa.String(length=64), nullable=False),
        sa.Column("acknowledgement", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("gate_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["agent_strategy_training_jobs.id"]),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["experiment_versions.id"]),
        sa.ForeignKeyConstraint(["consent_record_id"], ["agent_strategy_consents.id"]),
    )
    op.create_index(
        "uq_agent_activation_approval_live_job",
        "agent_model_activation_approvals",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("decision = 'approved'"),
    )
    op.create_index(
        "ix_agent_activation_approval_owner",
        "agent_model_activation_approvals",
        ["owner_id"],
    )
    op.create_index(
        "ix_agent_activation_approval_job",
        "agent_model_activation_approvals",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_activation_approval_job",
                  table_name="agent_model_activation_approvals")
    op.drop_index("ix_agent_activation_approval_owner",
                  table_name="agent_model_activation_approvals")
    op.drop_index("uq_agent_activation_approval_live_job",
                  table_name="agent_model_activation_approvals")
    op.drop_table("agent_model_activation_approvals")
