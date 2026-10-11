import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class VersionStatus(str, enum.Enum):
    draft = "draft"
    locked = "locked"


class ExperimentVersion(Base):
    __tablename__ = "experiment_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    strategy_spec = Column(JSONB, nullable=False)
    assumptions = Column(JSONB, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_approved_at = Column(DateTime, nullable=True)
    config_hash = Column(String(64), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    lock_reason = Column(Text, nullable=True)
    strategy_config_hash = Column(String(64), nullable=True)
    dataset_cutoff_at = Column(String(10), nullable=True)
    engine_version = Column(String(50), nullable=True)

    experiment = relationship("Experiment", back_populates="versions", foreign_keys=[experiment_id])
    runs = relationship("ResearchRun", back_populates="experiment_version")
