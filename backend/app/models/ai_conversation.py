"""Personal AI conversation models.

A conversation is a per-user chat thread with the personal AI assistant.
User-controlled context (selected research drafts, personal memories,
instrument symbols, experiments) lives on the conversation as JSON and is
fed to the provider on every turn. Every message persists the provider,
model, and token accounting for auditability.

These tables carry research advice only — the AI layer never writes
execution intents, orders, or authorization state.
"""
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow


class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="新對話")
    # User-controlled context references:
    # {"research_draft_ids": [...], "memory_ids": [...],
    #  "instrument_symbols": [...], "experiment_ids": [...]}
    context = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    messages = relationship(
        "AIMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AIMessage.created_at",
    )

    def __repr__(self):
        return f"<AIConversation id={self.id} title={self.title!r}>"


class AIMessage(Base):
    __tablename__ = "ai_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="completed")  # completed | error
    error_detail = Column(Text, nullable=True)
    provider = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    cost_cents = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    conversation = relationship("AIConversation", back_populates="messages")

    def __repr__(self):
        return f"<AIMessage role={self.role} status={self.status}>"
