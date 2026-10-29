"""Materialised personalization training dataset snapshots.

A snapshot is the exact set of (as-of-decision features, owner-confirmed
action) rows the personal trainer consumes. It is not an export: the trainer
reads ``rows_json`` from this row, so re-exporting or editing a CSV cannot
silently change what a model was trained on.

Every snapshot records the owner, the strategy version scope, the decision and
label ids it used, the data cutoff, the feature schema, the label definition,
exclusion reasons, the consent it was built under and a checksum over the
row payload.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base
from app.lib.timeutil import utcnow
import enum


class PersonalizationSnapshotStatus(str, enum.Enum):
    ready = "ready"
    failed = "failed"


class PersonalizationDatasetSnapshot(Base):
    __tablename__ = "personalization_dataset_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    strategy_version_id = Column(
        UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False
    )
    consent_id = Column(
        UUID(as_uuid=True), ForeignKey("personalization_consents.id"), nullable=False
    )
    consent_version = Column(String(64), nullable=False)
    status = Column(
        SAEnum(PersonalizationSnapshotStatus),
        default=PersonalizationSnapshotStatus.ready,
        nullable=False,
    )

    rows_json = Column(JSONB, nullable=False)
    feature_names_json = Column(JSONB, nullable=False)
    label_definition_json = Column(JSONB, nullable=False)
    exclusion_breakdown_json = Column(JSONB, nullable=False)
    class_distribution_json = Column(JSONB, nullable=False)

    included_count = Column(Integer, nullable=False)
    excluded_count = Column(Integer, nullable=False)
    date_range_start = Column(String(10), nullable=True)
    date_range_end = Column(String(10), nullable=True)
    data_cutoff_at = Column(String(10), nullable=True)
    checksum = Column(String(64), nullable=False)
    failure_reason = Column(String(64), nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
