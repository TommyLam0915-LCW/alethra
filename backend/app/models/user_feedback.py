import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow


class UserFeedback(Base):
    """One feedback report submitted from inside the product.

    The row is the receipt: the UI only claims a submission after this record
    is read back from the server. Nothing here is collected automatically —
    every text column is what the user typed.
    """

    __tablename__ = "user_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    category = Column(String(40), nullable=False)
    page = Column(String(200), nullable=True)
    steps = Column(Text, nullable=False)
    expected = Column(Text, nullable=True)
    actual = Column(Text, nullable=False)
    error_reference = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User")

    __table_args__ = (
        Index("ix_user_feedback_user_created", "user_id", "created_at"),
    )
