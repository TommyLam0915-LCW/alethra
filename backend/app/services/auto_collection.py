"""Interval research collection: the daily grid, on its own clock.

This module used to be a second, parallel implementation of the controlled
research service, with its own copy of everything the daily lane had already
been fixed on: work attributed to one account id hardcoded from a development
database, a database engine built per cycle and never disposed, no kill switch,
no ledger row at all (the counts went only to whoever called it), a cost model
that contradicted the one the research lane declares, and a history query
written in PostgreSQL JSON syntax that filtered on a marker no writer ever
produced.

What is left here is the clock and the operator surface. The work is
:func:`app.workers.learning_scheduler.run_research_cycle`, filed under its own
job type so the dedup window and the history of the interval service stay
separate from the daily cron's. Nothing in this module places an order, touches
a portfolio, or starts a service the deployment did not opt into.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.database import SessionLocal
from app.models.learning_job import LearningJob
from app.workers.learning_scheduler import (
    MANUAL_SCOPE,
    SERVICE_TIMEZONE,
    run_research_cycle,
)

logger = logging.getLogger(__name__)

# The ledger type this service files under. Dedup, history and any later audit
# read this value; nothing else writes it.
COLLECTION_JOB_TYPE = "auto_collection"
# What asked for the tick, recorded in ``config_json["scope"]``.
COLLECTION_SCOPE = "interval"

_scheduler: Optional[BackgroundScheduler] = None
_job_id = "auto_collection_job"


def collection_window(interval_hours: int, *, now: Optional[datetime] = None) -> str:
    """The dedup key for the window ``now`` falls in.

    Buckets are aligned to the UTC clock the service is scheduled on, so a tick
    that fires at 04:07 and a retry at 07:59 name the same window and only the
    first of them studies. The key is readable without decoding: a 4-hour
    interval starting 2026-11-17 is ``2026-11-17T04:00Z/4h``.
    """
    if interval_hours < 1:
        raise ValueError(f"interval_hours must be at least 1, got {interval_hours}")
    now = now or datetime.now(timezone.utc)
    hour = (now.hour // interval_hours) * interval_hours
    return f"{now.date().isoformat()}T{hour:02d}:00Z/{interval_hours}h"


def run_collection_cycle(
    user_id: Optional[UUID] = None,
    *,
    window: Optional[str] = None,
    job_id: Optional[UUID] = None,
) -> dict:
    """One interval tick: the study grid for every account this host serves.

    ``window`` is the dedup key; by default it is the window the clock is in
    now, which is what makes a restarted process, a duplicated trigger and a
    second API worker all land on the same window instead of studying again.
    The kill switch, the owner fan-out and its ceiling, the freshness check and
    the terminal status all live in the shared tick.
    """
    if window is None:
        window = collection_window(get_settings().auto_collection_interval_hours)
    return run_research_cycle(
        scope=COLLECTION_SCOPE,
        job_type=COLLECTION_JOB_TYPE,
        scheduled_for=window,
        user_id=user_id,
        job_id=job_id,
    )


def trigger_manual_collection(user_id: Optional[UUID] = None) -> dict:
    """Run the grid now, for this request, and return what the tick did.

    A person asking is not a duplicate of the clock, so this path carries no
    window and is never suppressed by one — what stops it repeating work is
    the freshness check inside the tick, which skips a window the venue has
    not updated. It runs on the request thread rather than being handed to a
    background scheduler, so the answer is an outcome that already happened,
    not a promise about one that may.
    """
    return run_research_cycle(
        scope=MANUAL_SCOPE,
        job_type=COLLECTION_JOB_TYPE,
        scheduled_for=None,
        user_id=user_id,
    )


def _build_trigger(interval_hours: int):
    """The clock this service fires on, aligned to the grid it dedups on.

    ``hour="*/N"`` puts every worker's fire exactly at the start of the N-hour
    bucket its window key names, so a second API worker joining the host cannot
    study a window another worker already studied. A bare
    ``IntervalTrigger(hours=N)`` counts from each process's own start time, and
    two workers started three hours apart then straddle the bucket boundary
    within minutes of each other — the dedup would miss and the venue quota
    would be spent twice for one window.

    An interval that does not divide 24 cannot be written that way (cron's step
    restarts at midnight), so it keeps the process-relative wait; what still
    holds there is the rest of the dedup — restart, duplicated trigger,
    same-bucket overlap — and the freshness check inside the tick, which
    declines to re-study a window the venue has not updated.
    """
    if 24 % interval_hours == 0:
        return CronTrigger(
            hour=f"*/{interval_hours}", minute=0, timezone=SERVICE_TIMEZONE
        )
    return IntervalTrigger(hours=interval_hours, timezone=SERVICE_TIMEZONE)


def create_scheduler() -> BackgroundScheduler:
    """Build this process's interval scheduler without starting it.

    The trigger is pinned to :data:`SERVICE_TIMEZONE` like the cron triggers
    beside it: an interval measured off the host clock drifts by the host's
    whole offset whenever the two disagree.
    """
    interval_hours = get_settings().auto_collection_interval_hours
    scheduler = BackgroundScheduler(timezone=SERVICE_TIMEZONE)
    scheduler.add_job(
        run_collection_cycle,
        trigger=_build_trigger(interval_hours),
        id=_job_id,
        name="Auto data collection",
        replace_existing=True,
    )
    return scheduler


def init_scheduler() -> Optional[BackgroundScheduler]:
    """Start the interval service in this process, if the deployment opted in.

    The opt-in is the configuration's, not a parameter: ``/collection/start``
    used to pass ``enabled=True`` straight through, which turned a host that
    had declined background research into one spending the venue quota on it.
    Returns ``None`` when disabled, so the caller can say so instead of
    reporting a start that did not happen.
    """
    global _scheduler

    settings = get_settings()
    if not settings.auto_collection_enabled:
        logger.info(
            "Auto-collection is disabled by configuration "
            "(AUTO_COLLECTION_ENABLED=false); not started"
        )
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = create_scheduler()
    _scheduler.start()
    logger.info(
        "Auto-collection scheduler started (every %s hours on %s)",
        settings.auto_collection_interval_hours,
        SERVICE_TIMEZONE,
    )
    return _scheduler


def stop_scheduler() -> bool:
    """Stop this process's interval service. ``False`` if none was running."""
    global _scheduler

    if _scheduler is None:
        return False
    was_running = _scheduler.running
    if was_running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Auto-collection scheduler stopped")
    return was_running


def get_scheduler_status() -> dict:
    """Deployment intent and this process's reality, as two separate fields.

    ``enabled`` answers "did the configuration opt in?", read from settings; it
    used to be a module global that ``/collection/start`` set to true, so the
    panel reported a service the deployment had declined. ``running`` answers
    "is a scheduler firing in this process?" — necessarily a per-process
    answer, because every API worker starts its own and cannot see another's.
    """
    settings = get_settings()
    running = _scheduler is not None and _scheduler.running
    next_run = None
    if running:
        job = _scheduler.get_job(_job_id)
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

    return {
        "enabled": settings.auto_collection_enabled,
        "running": running,
        "interval_hours": settings.auto_collection_interval_hours,
        "timezone": SERVICE_TIMEZONE,
        "job_type": COLLECTION_JOB_TYPE,
        "next_run": next_run,
    }


def get_collection_history(limit: int = 20) -> List[dict]:
    """The collection ticks that were recorded, newest first.

    Reads the ledger the ticks write. The version before this one issued
    ``settings_json->>'source' = 'auto_collection'`` — PostgreSQL-only JSON
    syntax, against a column
    :func:`app.services.strategy_study_store.persist_study` does not write,
    filtered on a marker no writer ever produced. It could only ever have
    returned an empty list, on either database.
    """
    db = SessionLocal()
    try:
        jobs = (
            db.query(LearningJob)
            .filter(LearningJob.job_type == COLLECTION_JOB_TYPE)
            .order_by(LearningJob.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_history_row(job) for job in jobs]
    finally:
        db.close()


def _history_row(job: LearningJob) -> dict:
    """One recorded tick, flattened for the operator table.

    The counts come from the row the tick wrote, so a ``failed`` tick shows its
    error list instead of an empty row that reads like a quiet period.
    """
    config = job.config_json or {}
    result = job.result_json or {}
    return {
        "id": str(job.id),
        "user_id": str(job.user_id),
        "status": job.status,
        "scope": config.get("scope"),
        "window": config.get("scheduled_for"),
        "total_combos": result.get("total_combos"),
        "studied": result.get("studied"),
        "skipped": result.get("skipped"),
        "failed": result.get("failed"),
        "errors": list(result.get("errors") or []),
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "completed_at": _iso(job.completed_at),
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None
