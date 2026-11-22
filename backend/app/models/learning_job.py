"""Auto-learning loop models.

LearningJob tracks scheduled and manual research batches. LearningProposal
records parameter adjustment suggestions produced by pattern analysis, each
validated by a walk-forward backtest before the owner may deploy them.

Nothing here auto-deploys. A proposal reaching ``validated`` status still
requires an explicit POST /learning/proposals/{id}/deploy from the owner.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.lib.timeutil import utcnow_aware

# The five states a learning job may hold, enforced in the database as well as
# in code: a row outside this vocabulary cannot be rendered truthfully by the
# UI, which maps every status to a label.
LEARNING_JOB_STATUSES = ("pending", "running", "completed", "failed", "cancelled")

# Who put the row here. ``user`` rows are created by an authenticated POST and
# must belong to that actor; ``schedule`` rows are created by a cron tick and
# belong to the explicitly configured system owner. Nothing in between: the
# executor that runs a job never gets to choose its owner.
LEARNING_JOB_ORIGINS = ("user", "schedule")


class LearningJob(Base):
    """One unit of learning work: requested, claimed, and settled on this row."""

    __tablename__ = "learning_jobs"

    __table_args__ = (
        # A resubmission is recognised by (owner, operation key) in the database,
        # not by a disabled button. The namespace is per owner, so two tenants
        # minting the same key cannot see or block each other.
        Index(
            "uq_learning_job_owner_operation",
            "user_id",
            "operation_key",
            unique=True,
            postgresql_where=text("operation_key IS NOT NULL"),
            sqlite_where=text("operation_key IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_learning_jobs_status",
        ),
        CheckConstraint(
            "origin IN ('user', 'schedule')",
            name="ck_learning_jobs_origin",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(16), nullable=False, default="pending")
    # pending | running | completed | failed | cancelled

    # "user" | "schedule" — see LEARNING_JOB_ORIGINS. Nullable only so that a
    # row written before the field existed can stay "requester not recorded"
    # instead of being back-filled with a guess; every write path sets it.
    origin = Column(String(16), nullable=True, default="user")

    job_type = Column(String(32), nullable=False)
    # "scheduled_study" | "pattern_analysis" | "parameter_proposal" | "validation_backtest"

    operation_key = Column(String(128), nullable=True)
    # Client-minted once per logical submit; a retry of the same submit resends
    # the same key and gets this same row back.

    request_fingerprint = Column(String(64), nullable=True)
    # sha256 over the canonical request. Same key + same fingerprint replays;
    # same key + different fingerprint is refused as a conflict.

    claimed_by = Column(String(64), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    # Which executor won the atomic claim on this row, and when. A lease that
    # expires is settled as ``failed``, never silently re-run.

    config_json = Column(JSONB, nullable=False)
    # { instruments: [...], intervals: [...], skills: [...] }

    result_json = Column(JSONB, nullable=True)
    # Analysis results, proposed parameters, validation metrics

    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow_aware)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class LearningProposal(Base):
    """Parameter adjustment proposals from pattern analysis."""

    __tablename__ = "learning_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    learning_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("learning_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rule_id = Column(String(48), nullable=False)
    exit_id = Column(String(48), nullable=False)
    instrument_id = Column(String(64), nullable=False)
    interval_minutes = Column(Integer, nullable=False)

    # Current parameters
    baseline_exit_config = Column(JSONB, nullable=False)
    # { target_atr: "5.0", stop_atr: "2.0", time_stop_bars: 24 }

    # Proposed parameters
    proposed_exit_config = Column(JSONB, nullable=False)
    # { target_atr: "6.0", stop_atr: "2.0", time_stop_bars: 24 }

    rationale_json = Column(JSONB, nullable=False)
    # { pattern: "...", baseline_metrics: {...}, expected_improvement: {...} }

    validation_result = Column(JSONB, nullable=True)
    # { sharpe_before, sharpe_after, max_dd_before, max_dd_after, passed: bool }

    status = Column(String(16), nullable=False, default="pending")
    # pending | validated | rejected | deployed

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow_aware)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
