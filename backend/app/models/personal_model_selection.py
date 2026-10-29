"""Durable comparison runs and explicit model-selection history.

A comparison run stores the result the owner actually saw for one pair of
immutable model versions. Its id can be derived from an ``operation_key`` so a
retried request returns the stored result instead of paying for a second run.

A selection row records every explicit active-version switch (and every
rollback) with the version before it and the owner's rationale. Model versions
and stored evaluations are never mutated by a switch.
"""

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base
from app.lib.timeutil import utcnow


class PersonalModelSelectionAction(str, enum.Enum):
    select = "select"
    rollback = "rollback"


class PersonalModelComparisonRun(Base):
    __tablename__ = "personal_model_comparison_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "operation_key",
            name="uq_personal_comparison_operation",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("model_research_records.id"),
        nullable=False,
    )
    version_a_id = Column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    version_b_id = Column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    mode = Column(String(32), nullable=False)
    operation_key = Column(String(128), nullable=True)
    request_json = Column(JSONB, nullable=False)
    result_json = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class PersonalModelSelection(Base):
    __tablename__ = "personal_model_selections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    record_id = Column(
        UUID(as_uuid=True),
        ForeignKey("model_research_records.id"),
        nullable=False,
    )
    version_id = Column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    previous_version_id = Column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=True
    )
    comparison_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("personal_model_comparison_runs.id"),
        nullable=True,
    )
    action = Column(
        SAEnum(PersonalModelSelectionAction),
        default=PersonalModelSelectionAction.select,
        nullable=False,
    )
    rationale = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
