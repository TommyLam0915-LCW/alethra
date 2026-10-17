"""Per-user persisted UI layout (workspace shell arrangement).

Layout is stored as a JSON object keyed by layout key (currently only
"workspace", hardcoded by the API layer).
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base
from app.lib.timeutil import utcnow


class UserLayout(Base):
    __tablename__ = "user_layouts"
    __table_args__ = (
        UniqueConstraint("user_id", "layout_key", name="uq_user_layout_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    layout_key = Column(String(64), default="workspace", nullable=False)
    layout_data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<UserLayout key={self.layout_key!r}>"
