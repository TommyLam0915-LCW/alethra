import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class InferenceRunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class InferenceRun(Base):
    __tablename__ = "inference_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    input_spec = Column(JSONB, nullable=False)
    output_signals = Column(JSONB, nullable=True)
    status = Column(SAEnum(InferenceRunStatus), default=InferenceRunStatus.pending, nullable=False)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    model_version = relationship("ModelVersion")
