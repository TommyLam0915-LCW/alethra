import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=False)
    event_type = Column(String(50), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_type = Column(String(10), nullable=False, default="human")
    payload_json = Column(JSONB, nullable=True)
    recorded_before_outcome = Column(Boolean, nullable=False, default=True)
    rules_locked = Column(Boolean, nullable=False, default=False)
    data_cutoff_at = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
