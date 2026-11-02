"""Durable registry for one customer write batch.

A batch names a bounded set of writes that one designated executor performs
for one owner. The registry exists so a second agent does not repeat a batch
that already happened, and so a deployment can refuse to start while a batch
is in flight. It is intentionally a single small table, not a scheduler: claim,
complete, abandon and read back.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base
from app.lib.timeutil import utcnow

BATCH_STATUSES = ("claimed", "completed", "abandoned")


class OperationBatch(Base):
    __tablename__ = "operation_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    batch_key = Column(String(120), nullable=False)
    executor = Column(String(160), nullable=False)
    status = Column(String(16), nullable=False, default="claimed")

    intended_operations = Column(JSONB, nullable=True)
    caps = Column(JSONB, nullable=True)
    # What the executor read back before acting (existing object ids and caps
    # already consumed). Recorded at claim time, never rewritten.
    precheck = Column(JSONB, nullable=True)

    claimed_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    abandoned_at = Column(DateTime, nullable=True)
    abandon_reason = Column(Text, nullable=True)

    # Read-back evidence written at completion: created object ids and the caps
    # actually consumed. Never overwritten once set.
    result_json = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("owner_id", "batch_key", name="uq_operation_batch_owner_key"),
        CheckConstraint(
            "status IN ('claimed', 'completed', 'abandoned')",
            name="ck_operation_batch_status",
        ),
        Index("ix_operation_batches_status", "status"),
    )
