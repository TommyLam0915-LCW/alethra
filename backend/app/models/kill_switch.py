"""System-wide kill switch model for emergency shutdown."""
from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Column, String, DateTime, Boolean, Text, Enum
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class KillSwitchScope(str, PyEnum):
    """Scope of kill switch activation."""
    global_ = "global"  # Entire system
    training = "training"  # Training jobs only
    execution = "execution"  # Execution intents only
    inference = "inference"  # Inference runs only


class KillSwitch(Base):
    """System-wide kill switch for emergency shutdown.
    
    When activated, blocks new submissions for the specified scope.
    Does NOT cancel already-submitted broker orders or running jobs.
    
    This is a durable, cross-process control stored in PostgreSQL.
    All API and worker processes check this before accepting new work.
    """
    __tablename__ = "kill_switches"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=lambda: __import__("uuid").uuid4())
    scope = Column(
        Enum(KillSwitchScope, values_callable=lambda e: [m.value for m in e]),
        nullable=False, unique=True, index=True,
    )
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    activated_at = Column(DateTime, nullable=True)
    activated_by = Column(String(255), nullable=True)  # User ID or "system"
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<KillSwitch scope={self.scope} active={self.is_active}>"
