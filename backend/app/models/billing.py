import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class SubscriptionStatus(str, enum.Enum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    cancelled = "cancelled"
    expired = "expired"


class Plan(Base):
    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    monthly_price = Column(Integer, nullable=False, default=0)
    currency = Column(String(10), default="USD", nullable=False)
    features = Column(JSONB, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    status = Column(String(20), default=SubscriptionStatus.trialing, nullable=False)
    payment_provider_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True, index=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PaymentRecord(Base):
    __tablename__ = "payment_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True)
    amount = Column(Integer, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    status = Column(String(20), nullable=False)
    payment_intent_id = Column(String(255), nullable=True)
    provider = Column(String(50), nullable=False)
    test_mode = Column(Boolean, default=True, nullable=False)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_payment_user_status", "user_id", "status"),
    )


class WebhookEventRecord(Base):
    __tablename__ = "webhook_event_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String(255), nullable=False, unique=True, index=True)
    event_type = Column(String(100), nullable=False)
    payload = Column(JSONB, nullable=False)
    processed = Column(Boolean, default=False, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=True)
    endpoint = Column(String(255), nullable=False)
    model = Column(String(100), nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False)
    cost = Column(Integer, default=0, nullable=False)  # Cost in cents
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_usage_user_date", "user_id", "created_at"),
        Index("ix_usage_subscription", "subscription_id", "created_at"),
    )
