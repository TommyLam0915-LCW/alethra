import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    Enum as SAEnum,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    # Closed by an explicit decision at POST /api/v1/runs/{id}/cancel, never by a
    # timeout: the platform sees rows, not processes, so "aged" cannot be
    # published as "failed" without writing an outcome nobody observed.
    cancelled = "cancelled"


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_version_id = Column(UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False)
    dataset_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("dataset_snapshots.id"), nullable=True)
    dataset_checksum = Column(String(64), nullable=True)
    engine_version = Column(String(50), nullable=False)
    config_hash = Column(String(64), nullable=False)
    status = Column(SAEnum(RunStatus), default=RunStatus.pending)
    metrics = Column(JSONB, nullable=True)
    warnings = Column(JSONB, nullable=True)
    methodology_json = Column(JSONB, nullable=True)
    result_json = Column(JSONB, nullable=True)
    error_details = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Persistent request de-duplication for POST /versions/{id}/runs. A key is
    # scoped to the version, because that is the URL the caller retries and
    # because the version already belongs to exactly one owner: a table-wide
    # unique key would let one account's key collide with another's.
    idempotency_key = Column(String(128), nullable=True)
    # What the admitted request actually was, so a key reused for a different
    # request is a conflict rather than a silently ignored second body.
    idempotency_request_hash = Column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "uq_research_run_version_idempotency",
            "experiment_version_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    experiment_version = relationship("ExperimentVersion", back_populates="runs")
    dataset_snapshot = relationship("DatasetSnapshot")
