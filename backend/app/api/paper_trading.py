"""Paper trading API routes.

Provides endpoints for managing paper trading portfolios and monitoring performance.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.paper_trading import get_engine

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])
logger = logging.getLogger(__name__)


class CreatePortfolioRequest(BaseModel):
    """Request to create a new paper trading portfolio."""
    skill: str
    instrument_id: str
    interval_minutes: int
    exit_config: str
    initial_capital: Optional[float] = 100000.0


class PortfolioResponse(BaseModel):
    """Response from portfolio creation."""
    portfolio_id: str
    run_id: str
    skill: str
    instrument_id: str
    interval_minutes: int
    initial_capital: float
    status: str


class StatusResponse(BaseModel):
    """Current status of a paper trading run."""
    run_id: str
    status: str
    skill: str
    instrument_id: str
    initial_capital: float
    current_capital: float
    total_pnl: float
    total_trades: int
    win_rate: float
    start_date: Optional[str]
    end_date: Optional[str]


class DailyReportResponse(BaseModel):
    """Daily performance report."""
    date: str
    run_id: str
    skill: str
    instrument_id: str
    portfolio: dict
    trades: dict
    risk: dict


class ReplayRequest(BaseModel):
    """Request to execute historical replay."""
    days_back: Optional[int] = 30
    position_size_pct: Optional[float] = 0.10
    max_positions: Optional[int] = 3


class ReplayResponse(BaseModel):
    """Response from historical replay."""
    run_id: str
    status: str
    total_trades: int
    total_pnl: float
    win_rate: float
    final_capital: float


@router.post("/create", response_model=PortfolioResponse)
def create_portfolio(
    request: CreatePortfolioRequest,
    current_user: User = Depends(get_current_user),
):
    """Create a new paper trading portfolio."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    engine = get_engine(database_url)

    try:
        result = engine.create_portfolio(
            user_id=str(current_user.id),
            skill=request.skill,
            instrument_id=request.instrument_id,
            interval_minutes=request.interval_minutes,
            exit_config=request.exit_config,
            initial_capital=Decimal(str(request.initial_capital)),
        )
        return result
    except Exception as exc:
        logger.exception("Failed to create portfolio")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "creation_failed", "message": str(exc)},
        )


@router.get("/status/{run_id}", response_model=StatusResponse)
def get_status(
    run_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get current status of a paper trading run."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    engine = get_engine(database_url)

    try:
        return engine.get_status(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Failed to get status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "status_failed", "message": str(exc)},
        )


@router.get("/daily-report/{run_id}", response_model=DailyReportResponse)
def get_daily_report(
    run_id: str,
    report_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format"),
    current_user: User = Depends(get_current_user),
):
    """Get daily performance report for a run."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    engine = get_engine(database_url)

    try:
        parsed_date = None
        if report_date:
            parsed_date = datetime.strptime(report_date, "%Y-%m-%d").date()

        return engine.generate_daily_report(run_id, parsed_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Failed to generate report")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "report_failed", "message": str(exc)},
        )


@router.post("/replay/{run_id}", response_model=ReplayResponse)
def execute_replay(
    run_id: str,
    request: ReplayRequest,
    current_user: User = Depends(get_current_user),
):
    """Execute historical replay for a paper trading run."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    engine = get_engine(database_url)

    try:
        result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=str(current_user.id),
            days_back=request.days_back,
            position_size_pct=request.position_size_pct,
            max_positions=request.max_positions,
        )
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Replay failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "replay_failed", "message": str(exc)},
        )


@router.post("/stop/{run_id}")
def stop_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop a running paper trading run."""
    from sqlalchemy import text

    try:
        db.execute(
            text("""
            UPDATE paper_trading_runs
            SET status = 'stopped', end_date = CURRENT_DATE, updated_at = NOW()
            WHERE id = :id
        """),
            {"id": run_id},
        )
        db.commit()

        return {"status": "stopped", "run_id": run_id}
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to stop run")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "stop_failed", "message": str(exc)},
        )


@router.get("/history")
def get_history(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get history of paper trading runs."""
    from sqlalchemy import text

    try:
        results = db.execute(
            text("""
            SELECT
                r.id,
                r.skill,
                r.instrument_id,
                r.interval_minutes,
                r.status,
                r.initial_capital,
                r.final_capital,
                r.total_pnl,
                r.total_trades,
                r.win_rate,
                r.start_date,
                r.end_date,
                r.run_type
            FROM paper_trading_runs r
            JOIN paper_portfolios p ON r.portfolio_id = p.id
            WHERE p.owner_id = :user_id
            ORDER BY r.start_date DESC
            LIMIT :limit
        """),
            {"user_id": str(current_user.id), "limit": limit},
        ).fetchall()

        return [
            {
                "run_id": str(r.id),
                "skill": r.skill,
                "instrument_id": r.instrument_id,
                "interval_minutes": r.interval_minutes,
                "status": r.status,
                "initial_capital": float(r.initial_capital),
                "final_capital": float(r.final_capital) if r.final_capital else None,
                "total_pnl": float(r.total_pnl) if r.total_pnl else 0,
                "total_trades": r.total_trades,
                "win_rate": float(r.win_rate) if r.win_rate else 0,
                "start_date": r.start_date.isoformat() if r.start_date else None,
                "end_date": r.end_date.isoformat() if r.end_date else None,
                "run_type": r.run_type,
            }
            for r in results
        ]
    except Exception as exc:
        logger.exception("Failed to get history")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"reason": "history_failed", "message": str(exc)},
        )


# ============================================================================
# Live Trading Daemon Control
# ============================================================================

_live_daemon = None


class LiveStatusResponse(BaseModel):
    """Live trading daemon status."""
    running: bool
    symbols: list[str]
    skills_count: int
    uptime_seconds: int
    total_trades: int
    total_pnl: float


@router.get("/live/status", response_model=LiveStatusResponse)
def live_status(
    current_user: User = Depends(get_current_user),
):
    """Get live trading daemon status."""
    global _live_daemon

    if _live_daemon is None:
        return {
            "running": False,
            "symbols": [],
            "skills_count": 0,
            "uptime_seconds": 0,
            "total_trades": 0,
            "total_pnl": 0.0,
        }

    return {
        "running": _live_daemon.running,
        "symbols": _live_daemon.symbols,
        "skills_count": len(_live_daemon.skills),
        "uptime_seconds": 0,  # TODO: Track start time
        "total_trades": _live_daemon.total_trades,
        "total_pnl": _live_daemon.total_pnl,
    }


@router.post("/live/start")
async def live_start(
    current_user: User = Depends(get_current_user),
):
    """Start the live trading daemon."""
    global _live_daemon

    if _live_daemon and _live_daemon.running:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "already_running", "message": "Live trading daemon is already running"},
        )

    from app.services.live_trading_daemon import LivePaperTradingDaemon
    import asyncio

    # Load configuration
    symbols = os.environ.get("LIVE_TRADING_SYMBOLS", "XBT/USD,ETH/USD,SOL/USD").split(",")
    interval = int(os.environ.get("LIVE_TRADING_INTERVAL", "60"))
    capital = float(os.environ.get("LIVE_TRADING_CAPITAL", "100000"))

    # Get top profitable skills
    db = SessionLocal()
    try:
        results = db.execute(
            text("""
            SELECT 
                t.rule_label_zh as skill,
                r.instrument_id,
                r.interval_minutes,
                t.exit_id,
                r.user_id
            FROM strategy_study_trades t
            JOIN strategy_study_runs r ON t.run_id = r.id
            GROUP BY t.rule_label_zh, r.instrument_id, r.interval_minutes, t.exit_id, r.user_id
            HAVING SUM(t.net_pnl) > 0
            ORDER BY SUM(t.net_pnl) DESC
            LIMIT 5
        """)
        ).fetchall()

        skills = [
            {
                "skill": r[0],
                "instrument_id": r[1],
                "interval_minutes": r[2],
                "exit_config": r[3],
                "user_id": str(r[4]),
            }
            for r in results
        ]

    finally:
        db.close()

    if not skills:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "no_skills", "message": "No profitable skills found"},
        )

    # Create and start daemon in background
    _live_daemon = LivePaperTradingDaemon(
        symbols=symbols,
        skills=skills,
        interval_seconds=interval,
        initial_capital=capital,
    )

    # Start daemon in background task
    asyncio.create_task(_live_daemon.start())

    return {
        "status": "started",
        "symbols": symbols,
        "skills_count": len(skills),
        "interval_seconds": interval,
    }


@router.post("/live/stop")
async def live_stop(
    current_user: User = Depends(get_current_user),
):
    """Stop the live trading daemon."""
    global _live_daemon

    if _live_daemon is None or not _live_daemon.running:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": "not_running", "message": "Live trading daemon is not running"},
        )

    await _live_daemon.stop()
    _live_daemon = None

    return {
        "status": "stopped",
    }
