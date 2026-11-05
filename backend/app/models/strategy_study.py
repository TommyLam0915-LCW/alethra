"""Durable records for the cost-aware strategy research lane.

The engine is pure: it takes bars and returns a study. These tables are the
review surface the customer asked for — every completed study keeps the exact
bars it used, and every out-of-sample buy/sell keeps its prices, costs, exit
rule and a mechanical earn/loss explanation. Nothing here is a broker order or
a real-money fill; the lane is simulated research.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow_aware


class StrategyStudyRun(Base):
    __tablename__ = "strategy_study_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lane = Column(String(50), nullable=False, default="strategy_research_simulated")
    research_version = Column(String(20), nullable=False)
    instrument_id = Column(String(64), nullable=False, index=True)
    interval_minutes = Column(Integer, nullable=False)
    provider_id = Column(String(32), nullable=False)
    data_nature = Column(String(20), nullable=False)

    bar_count = Column(Integer, nullable=False)
    first_open_time = Column(DateTime(timezone=True), nullable=False)
    last_open_time = Column(DateTime(timezone=True), nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    dataset_fingerprint = Column(String(64), nullable=True)
    dataset_integrity_ok = Column(Boolean, nullable=True)
    dataset_gap_count = Column(Integer, nullable=True)
    dataset_missing_intervals = Column(Integer, nullable=True)

    settings_json = Column(JSONB, nullable=False)
    walk_forward_json = Column(JSONB, nullable=False)
    highlights_json = Column(JSONB, nullable=True)
    disclosures_json = Column(JSONB, nullable=True)
    # The exact completed bars the study read. Storing them makes an old run
    # reproducible even after the venue's rolling window has moved past it.
    bars_json = Column(JSONB, nullable=False)
    trade_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow_aware)

    trades = relationship(
        "StrategyStudyTrade",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StrategyStudyTrade.trade_index",
    )


class StrategyStudyTrade(Base):
    __tablename__ = "strategy_study_trades"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "trade_index", name="uq_strategy_study_trades_run_index"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("strategy_study_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trade_index = Column(Integer, nullable=False)
    fold = Column(Integer, nullable=True)
    rule_id = Column(String(48), nullable=False)
    rule_label_zh = Column(String(80), nullable=True)
    exit_id = Column(String(48), nullable=False)
    exit_label_zh = Column(String(80), nullable=True)
    direction = Column(String(5), nullable=False)
    exit_reason = Column(String(24), nullable=False)
    outcome = Column(String(8), nullable=False)

    signal_time = Column(DateTime(timezone=True), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=False)
    entry_price = Column(Numeric(24, 8), nullable=False)
    exit_price = Column(Numeric(24, 8), nullable=False)
    quantity = Column(Numeric(24, 8), nullable=False)
    gross_pnl = Column(Numeric(24, 8), nullable=False)
    commission = Column(Numeric(24, 8), nullable=False)
    slippage_cost = Column(Numeric(24, 8), nullable=False)
    total_cost = Column(Numeric(24, 8), nullable=False)
    net_pnl = Column(Numeric(24, 8), nullable=False)
    cash_after = Column(Numeric(24, 8), nullable=False)
    bars_held = Column(Integer, nullable=False)
    mfe_pct = Column(Numeric(12, 4), nullable=True)
    mae_pct = Column(Numeric(12, 4), nullable=True)
    cost_ratio_pct = Column(Numeric(12, 4), nullable=True)
    pnl_reason_zh = Column(Text, nullable=False)
    pnl_reason_en = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow_aware)

    run = relationship("StrategyStudyRun", back_populates="trades")
