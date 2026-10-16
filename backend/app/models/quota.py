import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base
from app.lib.timeutil import utcnow


class QuotaCounter(Base):
    __tablename__ = "quota_counters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    quota_type = Column(String(100), nullable=False)
    period = Column(String(20), nullable=False, default="lifetime")
    used = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "quota_type", "period", name="uq_quota_counter"),
        Index("ix_quota_user_type", "user_id", "quota_type"),
    )
