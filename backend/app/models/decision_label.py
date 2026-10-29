"""Owner review labels for recorded human decisions.

A label is the owner's explicit statement about a decision they already
recorded: it was really their choice (confirmed), it was something else
(corrected), or it must not be used as training data (excluded). Labels are
append-only revisions — the recorded decision event itself is never modified,
and every revision names the revision it replaces so the review history stays
auditable.

This table is deliberately separate from model predictions. A synthetic or
rule signal can never become a label unless the owner records the decision and
then confirms it here.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow
import enum


class DecisionLabelState(str, enum.Enum):
    confirmed = "confirmed"
    excluded = "excluded"


class DecisionExclusionReason(str, enum.Enum):
    data_entry_error = "data_entry_error"
    test_trade = "test_trade"
    outside_strategy = "outside_strategy"
    other = "other"


class DecisionLabel(Base):
    __tablename__ = "decision_labels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decision_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("human_decision_events.id"),
        nullable=False,
        index=True,
    )
    revision_number = Column(Integer, nullable=False)

    state = Column(SAEnum(DecisionLabelState), nullable=False)
    # The action used as the training target. For a confirm it is copied from
    # the recorded event; for a correction the owner states the real action.
    action_label = Column(String(8), nullable=True)
    note = Column(Text, nullable=True)
    exclusion_reason = Column(SAEnum(DecisionExclusionReason), nullable=True)

    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    supersedes_label_id = Column(
        UUID(as_uuid=True), ForeignKey("decision_labels.id"), nullable=True
    )
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "decision_event_id",
            "revision_number",
            name="uq_decision_label_event_revision",
        ),
    )

    decision_event = relationship("HumanDecisionEvent", foreign_keys=[decision_event_id])
    supersedes_label = relationship(
        "DecisionLabel", remote_side=[id], foreign_keys=[supersedes_label_id]
    )
