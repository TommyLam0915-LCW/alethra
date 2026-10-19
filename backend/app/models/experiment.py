import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum as SAEnum, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class ResearchType(str, enum.Enum):
    quantitative = "quantitative"
    fundamental = "fundamental"


class ExperimentStatus(str, enum.Enum):
    draft = "draft"
    exploring = "exploring"
    locked = "locked"
    archived = "archived"


class Visibility(str, enum.Enum):
    private = "private"
    shared = "shared"
    public = "public"


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)
    title = Column(String(500), nullable=False)
    original_idea = Column(Text, nullable=True)
    research_type = Column(SAEnum(ResearchType), default=ResearchType.quantitative)
    hypothesis = Column(Text, nullable=True)
    falsification_conditions = Column(Text, nullable=True)
    benchmark = Column(String(50), nullable=True)
    scope = Column(JSONB, nullable=True)
    status = Column(SAEnum(ExperimentStatus), default=ExperimentStatus.draft)
    visibility = Column(SAEnum(Visibility), default=Visibility.private)
    current_version_id = Column(UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    workspace = relationship("Workspace", back_populates="experiments")
    versions = relationship("ExperimentVersion", back_populates="experiment", foreign_keys="ExperimentVersion.experiment_id")
    current_version = relationship("ExperimentVersion", foreign_keys=[current_version_id], post_update=True)
