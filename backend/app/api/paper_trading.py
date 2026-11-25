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

from app.auth.jwt import get_current_user, require_admin
from app.config import get_settings
from app.database import get_db
from app.lib.timeutil import iso_from_stored
from app.lib.uuid_bind import as_id_text, to_uuid, uuid_binds
from app.models.user import User
from app.services.paper_trading import get_engine
from app.services.paper_trading_exceptions import (
    AuthorizationError,
    InvalidStateError,
    ResourceNotFoundError,
)
from app.services.skill_units import SkillUnitLimitReached, assert_slot_available
from app.services.strategy_versions import validate_strategy_version

router = APIRouter(prefix="/paper-trading", tags=["paper-trading"])
logger = logging.getLogger(__name__)

# An unexpected exception is not a diagnostic for the person making the request.
# Its ``str()`` names the attribute, module or file that broke -- the 500 this
# lane produced for a status read carried a Python AttributeError -- while
# ``logger.exception`` in each handler already records the full traceback
# server-side. The response therefore says which operation failed and nothing
# about how it failed internally.
_INTERNAL_ERROR_MESSAGE = (
    "Internal error while processing this request. "
    "The failure has been logged; please retry or contact support."
)


def _verify_run_ownership(db: Session, run_id: str, user_id: str) -> None:
    """Verify that the run belongs to the given user.

    Raises ValueError if the run does not exist or does not belong to the user.
    Uses the same 404 for both cases to avoid information leakage.
    """
    from sqlalchemy import text

    # ``paper_trading_runs.id``, ``.portfolio_id`` and ``paper_portfolios.id``/
    # ``.owner_id`` are mapped ``UUID(as_uuid=True)``. Binding these keys with
    # the column's own type and a parsed value is what lets one predicate find
    # an account whichever create route opened it; an untyped string here only
    # ever matched one spelling on the text-UUID lane.
    result = db.execute(
        text("""
            SELECT 1 FROM paper_trading_runs r
            JOIN paper_portfolios p ON r.portfolio_id = p.id
            WHERE r.id = :run_id AND p.owner_id = :user_id
        """).bindparams(*uuid_binds("run_id", "user_id")),
        {"run_id": to_uuid(run_id), "user_id": to_uuid(user_id)},
    ).fetchone()
    if not result:
        raise ValueError("not_found")


class CreatePortfolioRequest(BaseModel):
    """Request to create a new paper trading portfolio."""
    skill: str
    instrument_id: str
    interval_minutes: int
    exit_config: str
    initial_capital: Optional[float] = 100000.0
    # Unified skill model: optional lineage fields for tracing which strategy
    # version and dataset this run uses. When provided, they are validated for
    # existence, ownership, frozen state, and compatibility.
    strategy_version_id: Optional[str] = None
    dataset_id: Optional[str] = None


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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new paper trading portfolio."""
    # The same ceiling as POST /paper-portfolio, because this lane writes the
    # same paper_portfolios row: a cap enforced at one entry point and not the
    # other just moves the door. Checked before the engine opens anything, and
    # outside the catch-all below so a full account is not reported as a 500.
    try:
        assert_slot_available(db, current_user.id)
    except SkillUnitLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail())

    # Validate strategy_version_id and dataset_id if provided — not just
    # existence, but ownership, frozen state, and compatibility.
    validated_strategy_version_id = None
    validated_dataset_id = None
    if request.strategy_version_id is not None:
        from uuid import UUID
        try:
            version_uuid = UUID(request.strategy_version_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "invalid_uuid",
                    "message": f"strategy_version_id must be a valid UUID, got: {request.strategy_version_id}",
                },
            )
        # Ownership lives on the parent experiment and usability on the freeze
        # stamp: the version row has neither an owner nor a status column, so a
        # check that reads one raises instead of answering. These are the same
        # refusals a decision naming this version gets, from one function.
        version = validate_strategy_version(
            db, version_id=version_uuid, owner_id=current_user.id
        )
        validated_strategy_version_id = version_uuid
        # If dataset_id is also provided, verify compatibility
        if request.dataset_id is not None:
            try:
                dataset_uuid = UUID(request.dataset_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason": "invalid_uuid",
                        "message": f"dataset_id must be a valid UUID, got: {request.dataset_id}",
                    },
                )
            if version.dataset_id != dataset_uuid:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason": "dataset_mismatch",
                        "version_dataset": str(version.dataset_id) if version.dataset_id else None,
                        "request_dataset": request.dataset_id,
                        "message": "The provided dataset_id does not match the experiment version's dataset.",
                    },
                )
            validated_dataset_id = dataset_uuid
        else:
            # Use the version's dataset if not explicitly provided
            validated_dataset_id = version.dataset_id
    elif request.dataset_id is not None:
        # dataset_id provided without strategy_version_id — validate existence
        from uuid import UUID
        try:
            validated_dataset_id = UUID(request.dataset_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "invalid_uuid",
                    "message": f"dataset_id must be a valid UUID, got: {request.dataset_id}",
                },
            )
        # Note: we don't validate dataset existence here because datasets table
        # may not exist or may not have ownership checks. The engine will handle
        # the actual data loading and fail if the dataset is invalid.

    database_url = get_settings().database_url
    engine = get_engine(database_url)

    try:
        result = engine.create_portfolio(
            user_id=str(current_user.id),
            skill=request.skill,
            instrument_id=request.instrument_id,
            interval_minutes=request.interval_minutes,
            exit_config=request.exit_config,
            initial_capital=Decimal(str(request.initial_capital)),
            session=db,
            strategy_version_id=validated_strategy_version_id,
            dataset_id=validated_dataset_id,
        )
        db.commit()
        return result
    except Exception:
        logger.exception("Failed to create portfolio")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "creation_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
        )


@router.get("/status/{run_id}", response_model=StatusResponse)
def get_status(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get current status of a paper trading run."""
    database_url = get_settings().database_url
    engine = get_engine(database_url)

    try:
        _verify_run_ownership(db, run_id, str(current_user.id))
        return engine.get_status(run_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except AuthorizationError:
        # Return 404 to avoid information leakage about resource existence/ownership
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": "Run not found"},
        )
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "invalid_state", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception:
        logger.exception("Failed to get status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "status_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
        )


@router.get("/daily-report/{run_id}", response_model=DailyReportResponse)
def get_daily_report(
    run_id: str,
    report_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get daily performance report for a run."""
    database_url = get_settings().database_url
    engine = get_engine(database_url)

    try:
        _verify_run_ownership(db, run_id, str(current_user.id))
        parsed_date = None
        if report_date:
            parsed_date = datetime.strptime(report_date, "%Y-%m-%d").date()

        return engine.generate_daily_report(run_id, parsed_date)
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except AuthorizationError:
        # Return 404 to avoid information leakage about resource existence/ownership
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": "Run not found"},
        )
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "invalid_state", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception:
        logger.exception("Failed to generate report")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "report_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
        )


@router.post("/replay/{run_id}", response_model=ReplayResponse)
def execute_replay(
    run_id: str,
    request: ReplayRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Execute historical replay for a paper trading run."""
    database_url = get_settings().database_url
    engine = get_engine(database_url)

    try:
        _verify_run_ownership(db, run_id, str(current_user.id))
        result = engine.execute_historical_replay(
            run_id=run_id,
            user_id=str(current_user.id),
            days_back=request.days_back,
            position_size_pct=request.position_size_pct,
            max_positions=request.max_positions,
        )
        return result
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except AuthorizationError:
        # Return 404 to avoid information leakage about resource existence/ownership
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": "Run not found"},
        )
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "invalid_state", "message": str(exc)},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except Exception:
        logger.exception("Replay failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "replay_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
        )


@router.post("/stop/{run_id}")
def stop_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stop a running paper trading run."""
    database_url = get_settings().database_url
    engine = get_engine(database_url)

    try:
        result = engine.cancel_run(run_id, str(current_user.id))
        return result
    except ResourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": str(exc)},
        )
    except AuthorizationError:
        # Return 404 to avoid information leakage about resource existence/ownership
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": "Run not found"},
        )
    except InvalidStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": "invalid_state", "message": str(exc)},
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "not_found", "message": "Run not found"},
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to stop run")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "stop_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
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
        """).bindparams(*uuid_binds("user_id")),
            {"user_id": to_uuid(current_user.id), "limit": limit},
        ).fetchall()

        return [
            {
                "run_id": as_id_text(r.id),
                "skill": r.skill,
                "instrument_id": r.instrument_id,
                "interval_minutes": r.interval_minutes,
                "status": r.status,
                "initial_capital": float(r.initial_capital),
                "final_capital": float(r.final_capital) if r.final_capital else None,
                "total_pnl": float(r.total_pnl) if r.total_pnl else 0,
                "total_trades": r.total_trades,
                "win_rate": float(r.win_rate) if r.win_rate else 0,
                "start_date": iso_from_stored(r.start_date),
                "end_date": iso_from_stored(r.end_date),
                "run_type": r.run_type,
            }
            for r in results
        ]
    except Exception:
        logger.exception("Failed to get history")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "history_failed",
                "message": _INTERNAL_ERROR_MESSAGE,
            },
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
    current_user: User = Depends(require_admin),
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
    current_user: User = Depends(require_admin),
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
    from sqlalchemy import text
    from app.database import SessionLocal
    
    db = SessionLocal()
    try:
        results = db.execute(
            text("""
            SELECT 
                t.rule_id,
                t.rule_label_zh as skill,
                r.instrument_id,
                r.interval_minutes,
                t.exit_id,
                r.user_id
            FROM strategy_study_trades t
            JOIN strategy_study_runs r ON t.run_id = r.id
            GROUP BY t.rule_id, t.rule_label_zh, r.instrument_id, r.interval_minutes, t.exit_id, r.user_id
            HAVING SUM(t.net_pnl) > 0
            ORDER BY SUM(t.net_pnl) DESC
            LIMIT 5
        """)
        ).fetchall()

        skills = [
            {
                "rule_id": r[0],
                "skill": r[1],
                "instrument_id": r[2],
                "interval_minutes": r[3],
                "exit_config": r[4],
                "user_id": str(r[5]),
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
    current_user: User = Depends(require_admin),
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
