"""add agent strategy consent records and training jobs

Revision ID: e7f8a9b0c1d2
Revises: z0a1b2c3d4e5
Create Date: 2026-11-02

Additive only. Two new tables:

* ``agent_strategy_consents`` — who accepted which policy version, for
  themselves. A partial unique index allows at most one active row per owner
  and purpose, so accepting twice supersedes in one place instead of leaving
  two live consents.
* ``agent_strategy_training_jobs`` — one bounded run over a frozen dataset
  snapshot, with the dataset checksum, artifact checksum and lineage needed to
  prove which content produced which model. ``activation_status`` is stored so
  an artifact can never be mistaken for an activated model.

No existing table is altered and no existing row is read or rewritten.
Downgrade drops both tables; the rows are development-only artifacts and the
downgrade is explicit about losing them.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "e7f8a9b0c1d2"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None

_CONSENTS = "agent_strategy_consents"
_TRAINING_JOBS = "agent_strategy_training_jobs"


def upgrade() -> None:
    op.create_table(
        _CONSENTS,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "accepted_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("acceptance_text_version", sa.String(length=64), nullable=False),
        sa.Column("acknowledgement_json", JSONB, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_agent_strategy_consent_active",
        _CONSENTS,
        ["owner_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_agent_strategy_consent_owner", _CONSENTS, ["owner_id"]
    )

    op.create_table(
        _TRAINING_JOBS,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "owner_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "snapshot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_dataset_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "strategy_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiment_versions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("training_contract_version", sa.String(length=64), nullable=False),
        sa.Column("derived_label_version", sa.String(length=64), nullable=False),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("hyperparameters_json", JSONB, nullable=False),
        sa.Column("hyperparameters_hash", sa.String(length=64), nullable=False),
        sa.Column("row_counts_json", JSONB, nullable=False),
        sa.Column("metrics_json", JSONB, nullable=True),
        sa.Column("lineage_json", JSONB, nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=True),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "artifact_metadata_checksum", sa.String(length=64), nullable=True
        ),
        sa.Column("activation_status", sa.String(length=32), nullable=False),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "uq_agent_training_job_completed",
        _TRAINING_JOBS,
        ["owner_id", "snapshot_id", "hyperparameters_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'completed'"),
        sqlite_where=sa.text("status = 'completed'"),
    )
    op.create_index(
        "ix_agent_training_jobs_owner", _TRAINING_JOBS, ["owner_id"]
    )
    op.create_index(
        "ix_agent_training_jobs_snapshot", _TRAINING_JOBS, ["snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_training_jobs_snapshot", table_name=_TRAINING_JOBS)
    op.drop_index("ix_agent_training_jobs_owner", table_name=_TRAINING_JOBS)
    op.drop_index("uq_agent_training_job_completed", table_name=_TRAINING_JOBS)
    op.drop_table(_TRAINING_JOBS)

    op.drop_index("ix_agent_strategy_consent_owner", table_name=_CONSENTS)
    op.drop_index("uq_agent_strategy_consent_active", table_name=_CONSENTS)
    op.drop_table(_CONSENTS)
