import uuid
from datetime import datetime
from sqlalchemy import Column, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.lib.timeutil import utcnow


class SavedComparison(Base):
    __tablename__ = "saved_comparisons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    experiment_id = Column(UUID(as_uuid=True), ForeignKey("experiments.id"), nullable=False, index=True)
    run_a_id = Column(UUID(as_uuid=True), ForeignKey("research_runs.id"), nullable=False)
    run_b_id = Column(UUID(as_uuid=True), ForeignKey("research_runs.id"), nullable=False)
    purpose = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    author = relationship("User")
    experiment = relationship("Experiment")
    run_a = relationship("ResearchRun", foreign_keys=[run_a_id])
    run_b = relationship("ResearchRun", foreign_keys=[run_b_id])
