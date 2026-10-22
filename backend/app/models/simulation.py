import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Float, ForeignKey, Enum as SAEnum, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class SimulationStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    paused = "paused"
    completed = "completed"
    retired = "retired"


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_version_id = Column(UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    initial_virtual_cash = Column(Float, nullable=False)
    started_at = Column(DateTime, nullable=True)
    data_cutoff_at = Column(String(10), nullable=False)
    current_snapshot_date = Column(String(10), nullable=True)
    status = Column(SAEnum(SimulationStatus), default=SimulationStatus.pending)
    simulation_policy_version = Column(String(20), nullable=False, default="1.0.0")
    provider_id = Column(String(50), nullable=True)
    provider_confirmed = Column(Boolean, default=False, nullable=False)
    paper_equity = Column(Float, nullable=True)
    current_position_json = Column(JSONB, nullable=True)
    warnings = Column(JSONB, nullable=True)
    total_decisions = Column(Integer, default=0)
    observed_days = Column(Integer, default=0)
    state_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    experiment_version = relationship("ExperimentVersion", foreign_keys=[experiment_version_id])
    decisions = relationship("SimulationDecision", back_populates="session", order_by="SimulationDecision.step_number")


class DecisionType(str, enum.Enum):
    no_action = "no_action"
    simulated_entry = "simulated_entry"
    simulated_exit = "simulated_exit"
    forced_exit = "forced_exit"
    blocked_action = "blocked_action"


class SimulationDecision(Base):
    __tablename__ = "simulation_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("simulation_sessions.id"), nullable=False)
    step_number = Column(Integer, nullable=False)
    snapshot_date = Column(String(10), nullable=False)
    data_cutoff_at = Column(String(10), nullable=False)
    engine_version = Column(String(50), nullable=False)
    decision_type = Column(SAEnum(DecisionType), nullable=False)
    decision_detail = Column(JSONB, nullable=True)
    idempotency_key = Column(String(100), unique=True, nullable=True)
    paper_cash = Column(Float, nullable=False)
    paper_position = Column(JSONB, nullable=True)
    paper_equity = Column(Float, nullable=False)
    warnings = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    session = relationship("SimulationSession", back_populates="decisions")
