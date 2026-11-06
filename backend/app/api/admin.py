"""Admin API routes for system management.

Includes auto-collection scheduler control and monitoring.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.auto_collection import (
    get_collection_history,
    get_scheduler_status,
    init_scheduler,
    stop_scheduler,
    trigger_manual_collection,
)

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


class CollectionTriggerResponse(BaseModel):
    """Response from manual collection trigger."""
    runs: int
    trades: int
    errors: list[str]
    completed_at: str


class SchedulerStatusResponse(BaseModel):
    """Scheduler status."""
    enabled: bool
    running: bool
    interval_hours: int
    next_run: Optional[str]


class CollectionRunResponse(BaseModel):
    """Single collection run record."""
    id: str
    instrument_id: str
    interval_minutes: int
    trade_count: int
    created_at: Optional[str]


@router.get("/collection/status", response_model=SchedulerStatusResponse)
def collection_status(
    current_user: User = Depends(get_current_user),
):
    """Get auto-collection scheduler status."""
    return get_scheduler_status()


@router.post("/collection/trigger", response_model=CollectionTriggerResponse)
def collection_trigger(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger a collection cycle."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    
    try:
        result = trigger_manual_collection(database_url)
        return result
    except Exception as exc:
        logger.exception("Manual collection failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "reason": "collection_failed",
                "message": str(exc),
            },
        )


@router.get("/collection/history", response_model=list[CollectionRunResponse])
def collection_history(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get history of collection runs."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    
    return get_collection_history(database_url, limit=limit)


@router.post("/collection/start")
def collection_start(
    current_user: User = Depends(get_current_user),
):
    """Start the auto-collection scheduler."""
    database_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/ai_investment_lab')
    enabled = os.environ.get('AUTO_COLLECTION_ENABLED', 'false').lower() == 'true'
    interval_hours = int(os.environ.get('AUTO_COLLECTION_INTERVAL_HOURS', '4'))
    
    init_scheduler(database_url, enabled=True, interval_hours=interval_hours)
    
    return {
        "status": "started",
        "interval_hours": interval_hours,
    }


@router.post("/collection/stop")
def collection_stop(
    current_user: User = Depends(get_current_user),
):
    """Stop the auto-collection scheduler."""
    stop_scheduler()
    
    return {
        "status": "stopped",
    }
