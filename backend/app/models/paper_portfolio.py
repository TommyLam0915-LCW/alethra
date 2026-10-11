import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Float, ForeignKey, Enum as SAEnum, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class PortfolioStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    retired = "retired"
    completed = "completed"


class PositionStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"


class OrderType(str, enum.Enum):
    market = "market"
    limit = "limit"


class RiskCheckStatus(str, enum.Enum):
    passed = "passed"
    failed = "failed"
    override_requested = "override_requested"


class PaperDecisionActorType(str, enum.Enum):
    human = "human"
    ai_draft = "ai_draft"
    human_approved_ai_draft = "human_approved_ai_draft"
    system = "system"


class PaperDecisionType(str, enum.Enum):
    no_action = "no_action"
    entry = "entry"
    exit = "exit"
    pause = "pause"
    retire = "retire"
    override_request = "override_request"


class ConfidenceLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    not_provided = "not_provided"


class OutcomeStatus(str, enum.Enum):
    pending = "pending"
    evaluated = "evaluated"
    expired = "expired"


class HypothesisStatus(str, enum.Enum):
    supported = "supported"
    partially_supported = "partially_supported"
    evidence_insufficient = "evidence_insufficient"
    contradicted = "contradicted"


class PaperPortfolio(Base):
    __tablename__ = "paper_portfolios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    simulation_session_id = Column(UUID(as_uuid=True), ForeignKey("simulation_sessions.id"), nullable=True)
    currency = Column(String(3), nullable=False, default="USD")
    initial_virtual_cash = Column(Float, nullable=False)
    current_virtual_cash = Column(Float, nullable=False)
    status = Column(SAEnum(PortfolioStatus), default=PortfolioStatus.active)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    positions = relationship("PaperPosition", back_populates="portfolio", order_by="PaperPosition.opened_at")
    orders = relationship("PaperOrder", back_populates="portfolio", order_by="PaperOrder.created_at")

    @property
    def total_realized_pnl(self):
        return sum(p.realized_pnl or 0 for p in self.positions if p.status == PositionStatus.closed)

    @property
    def total_unrealized_pnl(self):
        return sum(p.unrealized_pnl or 0 for p in self.positions if p.status == PositionStatus.open)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id = Column(UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False)
    asset_id = Column(String(50), nullable=False)
    quantity = Column(Float, nullable=False)
    average_entry_price = Column(Float, nullable=False)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, nullable=True)
    status = Column(SAEnum(PositionStatus), default=PositionStatus.open)

    portfolio = relationship("PaperPortfolio", back_populates="positions")


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id = Column(UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False)
    decision_id = Column(UUID(as_uuid=True), ForeignKey("decision_records.id"), nullable=True)
    asset_id = Column(String(50), nullable=False)
    side = Column(SAEnum(OrderSide), nullable=False)
    quantity = Column(Float, nullable=False)
    order_type = Column(SAEnum(OrderType), default=OrderType.market)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(SAEnum(OrderStatus), default=OrderStatus.pending)
    model_or_strategy_version_id = Column(UUID(as_uuid=True), nullable=True)
    data_cutoff_at = Column(String(10), nullable=True)
    rationale = Column(Text, nullable=True)
    risk_check_status = Column(SAEnum(RiskCheckStatus), default=RiskCheckStatus.passed)

    portfolio = relationship("PaperPortfolio", back_populates="orders")
    fills = relationship("PaperFill", back_populates="order", order_by="PaperFill.execution_date")


class PaperFill(Base):
    __tablename__ = "paper_fills"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_order_id = Column(UUID(as_uuid=True), ForeignKey("paper_orders.id"), nullable=False)
    execution_date = Column(String(10), nullable=False)
    observed_price = Column(Float, nullable=False)
    execution_price_before_costs = Column(Float, nullable=False)
    slippage_amount = Column(Float, nullable=False)
    commission_amount = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    gross_amount = Column(Float, nullable=False)
    net_cash_change = Column(Float, nullable=False)
    simulated_fill_reason = Column(String(100), nullable=True)

    order = relationship("PaperOrder", back_populates="fills")


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    simulation_session_id = Column(UUID(as_uuid=True), ForeignKey("simulation_sessions.id"), nullable=True)
    actor_type = Column(SAEnum(PaperDecisionActorType), nullable=False)
    decision_type = Column(SAEnum(PaperDecisionType), nullable=False)
    decision_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    data_cutoff_at = Column(String(10), nullable=True)
    linked_experiment_version_id = Column(UUID(as_uuid=True), nullable=True)
    linked_model_research_record_id = Column(UUID(as_uuid=True), nullable=True)
    rationale = Column(Text, nullable=True)
    expected_outcome = Column(Text, nullable=True)
    expected_timeframe_days = Column(Integer, nullable=True)
    self_reported_confidence = Column(SAEnum(ConfidenceLevel), default=ConfidenceLevel.not_provided)
    falsification_condition = Column(Text, nullable=True)
    risk_flags = Column(JSONB, nullable=True)
    outcome_status = Column(SAEnum(OutcomeStatus), default=OutcomeStatus.pending)

    outcome_evaluation = relationship("OutcomeEvaluation", back_populates="decision", uselist=False)


class OutcomeEvaluation(Base):
    __tablename__ = "outcome_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id = Column(UUID(as_uuid=True), ForeignKey("decision_records.id"), nullable=False)
    evaluated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actual_outcome_summary = Column(Text, nullable=True)
    net_simulated_return_pct = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    hypothesis_status = Column(SAEnum(HypothesisStatus), nullable=True)
    evaluation_method = Column(String(100), nullable=True)
    user_reflection = Column(Text, nullable=True)
    data_quality_flags = Column(JSONB, nullable=True)

    decision = relationship("DecisionRecord", back_populates="outcome_evaluation")
