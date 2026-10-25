import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Float, ForeignKey,
    UniqueConstraint, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class HumanDecisionAction(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    hold = "hold"


class HumanDecisionExecutionStatus(str, enum.Enum):
    record_only = "record_only"


class HumanDecisionEvent(Base):
    __tablename__ = "human_decision_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    simulation_session_id = Column(
        UUID(as_uuid=True), ForeignKey("simulation_sessions.id"), nullable=False
    )

    idempotency_key = Column(String(128), nullable=False)

    action = Column(SAEnum(HumanDecisionAction), nullable=False)
    quantity = Column(Float, nullable=True)
    rationale = Column(Text, nullable=True)

    server_strategy_version_id = Column(UUID(as_uuid=True), nullable=False)
    server_instrument_id = Column(String(64), nullable=False)
    server_step_number = Column(Integer, nullable=False)
    server_snapshot_date = Column(String(10), nullable=False)
    server_data_cutoff_at = Column(String(10), nullable=False)

    execution_status = Column(
        SAEnum(HumanDecisionExecutionStatus),
        nullable=False,
        default=HumanDecisionExecutionStatus.record_only,
    )

    supersedes_event_id = Column(
        UUID(as_uuid=True), ForeignKey("human_decision_events.id"), nullable=True
    )

    metadata_json = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "simulation_session_id", "idempotency_key",
            name="uq_human_decision_owner_session_key",
        ),
    )

    session = relationship("SimulationSession", foreign_keys=[simulation_session_id])
    supersedes_event = relationship(
        "HumanDecisionEvent", remote_side=[id], foreign_keys=[supersedes_event_id]
    )
