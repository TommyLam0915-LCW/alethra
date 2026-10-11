import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


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
    error_details = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    experiment_version = relationship("ExperimentVersion", back_populates="runs")
    dataset_snapshot = relationship("DatasetSnapshot")
