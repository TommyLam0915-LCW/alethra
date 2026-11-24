"""Paper trading run and daily metrics models.

These models represent the paper_trading_runs and paper_trading_daily_metrics tables,
which track individual replay/execution runs and their daily performance aggregation.
"""
import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Integer, DateTime, Date, Numeric, ForeignKey, Index, UniqueConstraint, Boolean, Text, false
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from app.database import Base


class PaperTradingRun(Base):
    """Tracks individual paper trading replay/execution runs."""
    __tablename__ = "paper_trading_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id = Column(UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False)
    skill = Column(String, nullable=False)
    instrument_id = Column(String, nullable=False)
    interval_minutes = Column(Integer, nullable=False)
    exit_config = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=False), nullable=False)
    end_date = Column(Date, nullable=True)
    status = Column(String, default="running")
    initial_capital = Column(Numeric(15, 2), nullable=False)
    final_capital = Column(Numeric(15, 2), nullable=True)
    total_pnl = Column(Numeric(15, 2), default=0)
    total_trades = Column(Integer, default=0)
    win_rate = Column(Numeric(5, 2), nullable=True)
    sharpe_ratio = Column(Numeric(8, 4), nullable=True)
    max_drawdown_pct = Column(Numeric(8, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    run_type = Column(String(50), default="historical_simulation")
    # Added by migration c9d0e1f2a3b4; declared here so create_all (SQLite) and
    # the migrated schema (PostgreSQL) expose the same column set.
    cancel_requested = Column(Boolean, nullable=False, server_default=false())
    # Added by migration f2a3b4c5d6e7. NULL means "no failure recorded" — a
    # cancelled or completed run is not a failure and keeps this empty rather
    # than storing a zero-length string. Text, not String(64), because the
    # writer stores safe_error_message(exc), which is bounded at 200 characters.
    failure_reason = Column(Text, nullable=True)
    # Added by migration d6e7f8a9b0c1. The upstream study trades this replay
    # priced, captured once by the attempt that read them: run ids, trade ids,
    # research versions, a content digest over the priced fields, and the
    # window. The writer conditions on ``source_lineage IS NULL``, so a later
    # attempt cannot restampe it and a reader can tell "nothing was consumed
    # yet" (NULL) from "the population was empty" (trade_count 0).
    source_lineage = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("idx_paper_trading_runs_portfolio", "portfolio_id"),
        Index("idx_paper_trading_runs_status", "status"),
    )


class PaperTradingDailyMetric(Base):
    """Daily performance aggregation for a paper trading run."""
    __tablename__ = "paper_trading_daily_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("paper_trading_runs.id"), nullable=False)
    date = Column(Date, nullable=False)
    trades_count = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    daily_pnl = Column(Numeric(15, 2), default=0)
    cumulative_pnl = Column(Numeric(15, 2), default=0)
    drawdown_pct = Column(Numeric(8, 4), nullable=True)
    sharpe_7d = Column(Numeric(8, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "date", name="uq_paper_trading_daily_run_date"),
        Index("idx_paper_trading_daily_run_date", "run_id", "date"),
    )
