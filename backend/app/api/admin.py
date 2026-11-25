"""Admin API routes for system management.

Includes auto-collection scheduler control and monitoring. Every response here
describes something that has happened or a refusal that stopped it happening:
no endpoint reports a service running that the configuration declined, and none
reports counts the tick did not record.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth.jwt import require_admin
from app.lib.timeutil import utcnow_aware
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


class CollectionStatusResponse(BaseModel):
    """What the interval service is configured to do, and what this process does.

    ``enabled`` is the deployment's opt-in and ``running`` is this worker's
    reality; the panel needs both, because "configured on, not running here"
    and "running here, not configured" are different incidents.
    """

    enabled: bool
    running: bool
    interval_hours: int
    timezone: str
    job_type: str
    next_run: Optional[str]


class CollectionJobResponse(BaseModel):
    """One account's recorded tick inside a manually requested cycle."""

    job_id: str
    user_id: str
    status: str


class CollectionTriggerResponse(BaseModel):
    """What the cycle this request asked for actually did.

    ``skipped`` names why nothing ran — the kill switch, an unreadable switch,
    a tick that broke before any row was written, or no account to serve — and
    the accounts list is empty then. Counts are not reported here: they are on
    each recorded row, read back from the ledger.
    """

    skipped: Optional[str]
    accounts: int
    jobs: list[CollectionJobResponse]
    completed_at: str


class CollectionHistoryResponse(BaseModel):
    """One recorded collection tick, with the counts the tick wrote."""

    id: str
    user_id: str
    status: str
    scope: Optional[str]
    window: Optional[str]
    total_combos: Optional[int]
    studied: Optional[int]
    skipped: Optional[int]
    failed: Optional[int]
    errors: list[str]
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]


@router.get("/collection/status", response_model=CollectionStatusResponse)
def collection_status(
    current_user: User = Depends(require_admin),
):
    """Get auto-collection scheduler status."""
    return get_scheduler_status()


@router.post("/collection/trigger", response_model=CollectionTriggerResponse)
def collection_trigger(
    current_user: User = Depends(require_admin),
):
    """Run one collection cycle now and report what it did.

    Synchronous on purpose: the response is the tick's own outcome. When the
    switch refused it, that is what comes back — not an empty success.
    """
    outcome = trigger_manual_collection()
    return {
        "skipped": outcome["skipped"],
        "accounts": len(outcome["jobs"]),
        "jobs": outcome["jobs"],
        "completed_at": utcnow_aware().isoformat(),
    }


@router.get(
    "/collection/history",
    response_model=list[CollectionHistoryResponse],
)
def collection_history(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(require_admin),
):
    """Get the recorded collection ticks, newest first."""
    return get_collection_history(limit=limit)


@router.post("/collection/start", response_model=CollectionStatusResponse)
def collection_start(
    current_user: User = Depends(require_admin),
):
    """Start the interval service in this process.

    Refused when the configuration did not opt in. The flag used to be read
    off the environment here and passed straight through as ``enabled=True``,
    so an operator's POST started background research the deployment had
    declined to spend venue quota on — an opt-in belongs to the deployment,
    and this endpoint cannot grant it.
    """
    if init_scheduler() is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "auto_collection_disabled",
                "message": (
                    "AUTO_COLLECTION_ENABLED is false in this process's "
                    "configuration, so the interval service was not started. "
                    "Set it in the environment and restart; a POST cannot "
                    "override a deployment's opt-in."
                ),
                "status": get_scheduler_status(),
            },
        )
    return get_scheduler_status()


@router.post("/collection/stop", response_model=CollectionStatusResponse)
def collection_stop(
    current_user: User = Depends(require_admin),
):
    """Stop this process's interval service and report the state after.

    An in-flight tick is not cancelled by this: it finishes and records its
    own outcome. What stops is the next scheduled one, which is why the
    response is the status rather than a bare "stopped".
    """
    stop_scheduler()
    return get_scheduler_status()
