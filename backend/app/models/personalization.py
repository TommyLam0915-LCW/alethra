import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.lib.timeutil import utcnow


PERSONALIZATION_TRAINING_PURPOSE = "personalization_training"


class PersonalizationConsent(Base):
    """Owner-scoped, revocable consent to use recorded decisions for training.

    Storing research data or approving a memory for AI context is not training
    consent; only an active row of this table that lists the strategy version
    in ``scope_json["strategy_version_ids"]`` authorises that data.
    """

    __tablename__ = "personalization_consents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    purpose = Column(String(64), nullable=False)
    consent_version = Column(String(64), nullable=False)
    scope_json = Column(JSONB, nullable=False, default=dict)
    granted_at = Column(DateTime, default=utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    owner = relationship("User")

    __table_args__ = (
        Index(
            "ix_personalization_consent_owner_purpose",
            "owner_id",
            "purpose",
        ),
    )
