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


class QuotaReservation(Base):
    """Tracks reserved (pending) quota units in two-phase flow.

    Lifecycle:
    1. reserve_quota() creates a reservation (status='reserved')
    2. settle_reservation() increments used counter and marks status='settled'
    3. release_reservation() marks status='released' (quota not consumed)

    Reservations prevent quota from being double-spent by accounting for
    in-flight operations when checking availability.
    """
    __tablename__ = "quota_reservations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    quota_type = Column(String(100), nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="reserved")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    settled_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_quota_reservation_user_type_status", "user_id", "quota_type", "status"),
    )
