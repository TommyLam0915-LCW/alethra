import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow


STUDY_KINDS = (
    "cost_scenarios",
    "period_slices",
    "parameter_sensitivity",
    "walk_forward",
)

STUDY_STATUSES = ("planned", "running", "completed", "partial", "failed", "cancelled")

ATTEMPT_STATUSES = ("pending", "completed", "failed", "skipped")


class RobustnessStudy(Base):
    """One bounded robustness experiment over a single strategy version.

    The study records the whole plan (estimated attempts, cap, refusals) before
    any backtest runs, and every attempt is kept — including failures — so the
    result can never collapse into "the best run we happened to keep".
    """

    __tablename__ = "robustness_studies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    version_id = Column(UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="planned")
    request_id = Column(String(64), nullable=True)
    config = Column(JSONB, nullable=False)
    plan = Column(JSONB, nullable=False)
    aggregate = Column(JSONB, nullable=True)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    attempts = relationship(
        "RobustnessAttempt",
        back_populates="study",
        order_by="RobustnessAttempt.created_at",
    )

    __table_args__ = (
        Index("ix_robustness_study_experiment", "experiment_id", "created_at"),
        Index("ix_robustness_study_user_request", "user_id", "request_id"),
    )


class RobustnessAttempt(Base):
    """One cell of a study, backed by one real research run.

    ``cell_key`` is unique per study: a retry reuses the row instead of adding
    a second run for the same cell, so resuming can never duplicate operations.
    """

    __tablename__ = "robustness_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    study_id = Column(
        UUID(as_uuid=True),
        ForeignKey("robustness_studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    cell_key = Column(String(200), nullable=False)
    label = Column(String(300), nullable=False)
    role = Column(String(32), nullable=False)
    spec = Column(JSONB, nullable=False)
    overrides = Column(JSONB, nullable=True)
    period_start = Column(String(10), nullable=True)
    period_end = Column(String(10), nullable=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status = Column(String(32), nullable=False, default="pending")
    metrics = Column(JSONB, nullable=True)
    dataset_checksum = Column(String(64), nullable=True)
    data_identity = Column(JSONB, nullable=True)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    study = relationship("RobustnessStudy", back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("study_id", "cell_key", name="uq_robustness_attempt_cell"),
        Index("ix_robustness_attempt_study", "study_id"),
    )
