import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint, Index, text
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

    ``research_run_id`` says which research run a unit was held for. It is
    written by the admission transaction and read at settlement, so ownership
    is looked up instead of inferred from timestamps. Rows predating the column
    keep it NULL: that is an honest UNKNOWN, never backfilled and never
    auto-released. Deleting a run is refused while its ledger row exists, so no
    run removal can take the accounting evidence with it.
    """
    __tablename__ = "quota_reservations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    quota_type = Column(String(100), nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="reserved")
    research_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = Column(DateTime, default=utcnow, nullable=False)
    settled_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_quota_reservation_user_type_status", "user_id", "quota_type", "status"),
        Index(
            "ix_quota_reservation_research_run",
            "research_run_id",
            postgresql_where=text("research_run_id IS NOT NULL"),
            sqlite_where=text("research_run_id IS NOT NULL"),
        ),
        # The billing contract: one research_run unit per run, so a run can
        # never be settled or released twice through two linked rows.
        Index(
            "uq_quota_reservation_run_unit",
            "research_run_id",
            unique=True,
            postgresql_where=text("quota_type = 'research_run' AND research_run_id IS NOT NULL"),
            sqlite_where=text("quota_type = 'research_run' AND research_run_id IS NOT NULL"),
        ),
    )
