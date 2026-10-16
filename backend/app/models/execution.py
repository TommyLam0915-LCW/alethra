import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, Text, DateTime, Integer, Float, ForeignKey, Enum as SAEnum, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class IntentSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class IntentOrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class IntentStatus(str, enum.Enum):
    pending = "pending"
    authorized = "authorized"
    submitted = "submitted"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"
    uncertain = "uncertain"  # Used for ambiguous broker submissions (a.k.a. "unknown")


class FillSource(str, enum.Enum):
    """Distinguishes simulated fills from broker-confirmed fills."""
    simulated = "simulated"
    broker = "broker"


class OrderIntent(Base):
    __tablename__ = "order_intents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    portfolio_id = Column(UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False)
    intent_id = Column(String(36), unique=True, nullable=False, index=True)
    symbol = Column(String(50), nullable=False)
    side = Column(SAEnum(IntentSide), nullable=False)
    quantity = Column(Integer, nullable=False)
    order_type = Column(SAEnum(IntentOrderType), nullable=False, default=IntentOrderType.MARKET)
    limit_price = Column(Float, nullable=True)
    status = Column(SAEnum(IntentStatus), default=IntentStatus.pending, nullable=False)
    authorization_token = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    broker_ref = Column(String(255), nullable=True)

    # Idempotency: client-supplied key to prevent duplicate submissions.
    # If a request arrives with a duplicate idempotency_key, the existing intent is returned.
    idempotency_key = Column(String(64), unique=True, nullable=True, index=True)

    # Fill tracking: distinguishes simulated fills from broker-confirmed fills
    fill_source = Column(SAEnum(FillSource), nullable=True)
    broker_confirmed_at = Column(DateTime, nullable=True)

    fill_reconciliations = relationship("FillReconciliation", back_populates="order_intent")
    fill_records = relationship("FillRecord", back_populates="order_intent", cascade="all, delete-orphan")


class ExecutionAgent(Base):
    __tablename__ = "execution_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    agent_type = Column(String(100), nullable=False)
    config_encrypted = Column(JSONB, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class FillRecord(Base):
    """Tracks individual fills for an order intent.

    Each fill record represents a discrete execution event (partial or full).
    Reconciliation compares the sum of fill records against the expected quantity.
    """
    __tablename__ = "fill_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_intent_id = Column(UUID(as_uuid=True), ForeignKey("order_intents.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=True)
    fill_source = Column(SAEnum(FillSource), nullable=False, default=FillSource.simulated)
    broker_ref = Column(String(255), nullable=True)
    broker_confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    order_intent = relationship("OrderIntent", back_populates="fill_records")


class FillReconciliation(Base):
    __tablename__ = "fill_reconciliations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_intent_id = Column(UUID(as_uuid=True), ForeignKey("order_intents.id"), nullable=False)
    expected_quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, nullable=False)
    discrepancy = Column(Boolean, nullable=False, default=False)
    reconciled_at = Column(DateTime, default=utcnow, nullable=False)

    order_intent = relationship("OrderIntent", back_populates="fill_reconciliations")
