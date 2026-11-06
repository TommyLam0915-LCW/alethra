"""add learning jobs and proposals

Two additive tables for the auto-learning loop: one row per scheduled or
manually triggered learning batch (instruments, intervals, skills, results)
and one row per parameter adjustment proposal produced by pattern analysis.
A proposal records the baseline exit config, the proposed config, the
rationale, and the walk-forward validation result. Nothing here auto-deploys.

Revision ID: l3a4r5n6i7g8
Revises: a7c1e4f9b2d5
Create Date: 2026-11-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "l3a4r5n6i7g8"
down_revision = "a7c1e4f9b2d5"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "learning_jobs"):
        op.create_table(
            "learning_jobs",
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
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("job_type", sa.String(32), nullable=False),
            sa.Column("config_json", JSONB(), nullable=False),
            sa.Column("result_json", JSONB(), nullable=True),
            sa.Column("error_details", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_learning_jobs_user_id", "learning_jobs", ["user_id"])
        op.create_index("ix_learning_jobs_status", "learning_jobs", ["status"])

    if not _table_exists(bind, "learning_proposals"):
        op.create_table(
            "learning_proposals",
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
            sa.Column(
                "learning_job_id",
                UUID(as_uuid=True),
                sa.ForeignKey("learning_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("rule_id", sa.String(48), nullable=False),
            sa.Column("exit_id", sa.String(48), nullable=False),
            sa.Column("instrument_id", sa.String(64), nullable=False),
            sa.Column("interval_minutes", sa.Integer(), nullable=False),
            sa.Column("baseline_exit_config", JSONB(), nullable=False),
            sa.Column("proposed_exit_config", JSONB(), nullable=False),
            sa.Column("rationale_json", JSONB(), nullable=False),
            sa.Column("validation_result", JSONB(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_learning_proposals_user_id", "learning_proposals", ["user_id"]
        )
        op.create_index(
            "ix_learning_proposals_learning_job_id",
            "learning_proposals",
            ["learning_job_id"],
        )
        op.create_index(
            "ix_learning_proposals_status", "learning_proposals", ["status"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "learning_proposals"):
        op.drop_index(
            "ix_learning_proposals_status", table_name="learning_proposals"
        )
        op.drop_index(
            "ix_learning_proposals_learning_job_id",
            table_name="learning_proposals",
        )
        op.drop_index(
            "ix_learning_proposals_user_id", table_name="learning_proposals"
        )
        op.drop_table("learning_proposals")
    if _table_exists(bind, "learning_jobs"):
        op.drop_index("ix_learning_jobs_status", table_name="learning_jobs")
        op.drop_index("ix_learning_jobs_user_id", table_name="learning_jobs")
        op.drop_table("learning_jobs")
