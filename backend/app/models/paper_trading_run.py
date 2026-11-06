"""Paper trading run and daily metrics models.

These models represent the paper_trading_runs and paper_trading_daily_metrics tables,
which track individual replay/execution runs and their daily performance aggregation.
"""
import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Integer, DateTime, Date, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
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
    drawdown_pct = Column(Numeric(8, 4), default=0)
    sharpe_7d = Column(Numeric(8, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_paper_trading_daily_run_date", "run_id", "date"),
    )
