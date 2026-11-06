"""Auto-learning loop models.

LearningJob tracks scheduled and manual research batches. LearningProposal
records parameter adjustment suggestions produced by pattern analysis, each
validated by a walk-forward backtest before the owner may deploy them.

Nothing here auto-deploys. A proposal reaching ``validated`` status still
requires an explicit POST /learning/proposals/{id}/deploy from the owner.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.lib.timeutil import utcnow_aware


class LearningJob(Base):
    """Scheduled auto-learning runs."""

    __tablename__ = "learning_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(16), nullable=False, default="pending")
    # pending | running | completed | failed | cancelled

    job_type = Column(String(32), nullable=False)
    # "scheduled_study" | "pattern_analysis" | "parameter_proposal" | "validation_backtest"

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
