import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class TrainingJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=True)
    status = Column(SAEnum(TrainingJobStatus), default=TrainingJobStatus.pending, nullable=False)
    parameter_grid = Column(JSONB, nullable=False)
    strategy_spec_snapshot = Column(JSONB, nullable=True)
    results = Column(JSONB, nullable=True)
    best_params = Column(JSONB, nullable=True)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    experiment = relationship("Experiment")
