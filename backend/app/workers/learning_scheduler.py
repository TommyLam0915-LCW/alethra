"""Auto-learning queue: one owner per row, one executor, one row per request.

Two producers file work and one executor runs it:

- ``POST /learning/jobs/trigger`` files a ``pending`` row owned by the
  authenticated actor (``origin="user"``).
- A cron tick files a ``pending`` row owned by the explicitly configured system
  owner (``origin="schedule"``). ``LEARNING_SYSTEM_OWNER_EMAIL`` unset means the
  tick is refused; nothing here picks an owner by reading ``select(User).limit(1)``.

``drain_learning_queue`` is the only executor. It atomically claims a ``pending``
row and settles *that same row* — the request record and the run record are one
row, so a successful submission cannot be left pending forever. While it is
registered and running it also expires stale claims: a row still ``running``
past its lease is recorded as ``failed``, because an executor that stopped
talking is not evidence the work finished, and re-running a research batch
unasked would spend quota the owner never approved.

Jobs are opt-in via ``settings.enable_learning_scheduler``; the endpoint refuses
a submission when no executor exists rather than filing work nobody will run.
APScheduler's in-process BackgroundScheduler is used — no Redis or Celery.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.lib.error_safety import safe_error_message
from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun
from app.services.job_control import (
    IdempotencyConflict,
    lock_user_for_idempotency,
    request_fingerprint,
)
from app.services.pattern_analysis import (
    analyze_skill_patterns,
    propose_parameter_adjustment,
    should_deploy,
    validate_proposal,
)
from app.services.strategy_research import (
    EXIT_BY_ID,
    ExitConfig,
    ResearchSettings,
    run_study,
)
from app.services.strategy_lab import LabBar
from app.services.strategy_study_store import persist_study

logger = logging.getLogger(__name__)

# The interval job that claims and runs queued work. Admission asks whether this
# job is live, so the name is part of the contract, not an implementation detail.
DRAIN_JOB_ID = "learning_queue_drain"

# Weekly pattern analysis default; a request row stores its own value so the
# executor never has to guess what the submitter asked for.
DEFAULT_LOOKBACK_RUNS = 50

# Default instruments and intervals for scheduled research
SCHEDULED_INSTRUMENTS = ["CRYPTO:KRAKEN:BTC-USD", "CRYPTO:KRAKEN:ETH-USD", "CRYPTO:KRAKEN:SOL-USD"]
SCHEDULED_INTERVALS = [1440, 240, 60, 15]

# Legacy exit IDs that may appear in stored trades but are not in the current
# EXIT_BY_ID registry.  Map them to approximate ExitConfig objects so the
# proposal pipeline can still operate on older data.
_LEGACY_EXIT_CONFIGS: dict[str, ExitConfig] = {
    "exit_event_catalyst_1": ExitConfig("exit_event_catalyst_1", "事件催化 (緊)", Decimal("1.0"), Decimal("1.5"), 6, "Legacy"),
    "exit_event_catalyst_2": ExitConfig("exit_event_catalyst_2", "事件催化 (中)", Decimal("1.5"), Decimal("2.0"), 8, "Legacy"),
    "exit_event_catalyst_3": ExitConfig("exit_event_catalyst_3", "事件催化 (寬)", Decimal("2.0"), Decimal("2.5"), 10, "Legacy"),
    "exit_mean_reversion_1": ExitConfig("exit_mean_reversion_1", "均值回歸 (緊)", Decimal("0.8"), Decimal("1.2"), 4, "Legacy"),
    "exit_mean_reversion_2": ExitConfig("exit_mean_reversion_2", "均值回歸 (中)", Decimal("1.2"), Decimal("1.8"), 6, "Legacy"),
    "exit_mean_reversion_3": ExitConfig("exit_mean_reversion_3", "均值回歸 (寬)", Decimal("1.8"), Decimal("2.5"), 8, "Legacy"),
    "exit_trend_following_1": ExitConfig("exit_trend_following_1", "趨勢跟隨 (緊)", Decimal("1.0"), Decimal("1.5"), 8, "Legacy"),
    "exit_trend_following_2": ExitConfig("exit_trend_following_2", "趨勢跟隨 (中)", Decimal("1.5"), Decimal("2.0"), 12, "Legacy"),
    "exit_trend_following_3": ExitConfig("exit_trend_following_3", "趨勢跟隨 (寬)", Decimal("2.0"), Decimal("3.0"), 16, "Legacy"),
    "exit_intraday_momentum_1": ExitConfig("exit_intraday_momentum_1", "日內動量 (緊)", Decimal("0.6"), Decimal("1.0"), 3, "Legacy"),
    "exit_intraday_momentum_2": ExitConfig("exit_intraday_momentum_2", "日內動量 (中)", Decimal("1.0"), Decimal("1.5"), 5, "Legacy"),
    "exit_intraday_momentum_3": ExitConfig("exit_intraday_momentum_3", "日內動量 (寬)", Decimal("1.5"), Decimal("2.0"), 8, "Legacy"),
}


def _resolve_exit_config(exit_id: str) -> Optional[ExitConfig]:
    """Look up an ExitConfig by ID, falling back to legacy configs."""
    return EXIT_BY_ID.get(exit_id) or _LEGACY_EXIT_CONFIGS.get(exit_id)


def create_learning_scheduler() -> BackgroundScheduler:
    """Create the scheduler: two producers on a cron and one executor on a tick.

    The cron jobs file work for the owner named in
    ``LEARNING_SYSTEM_OWNER_EMAIL``; ``learning_queue_drain`` is what actually
    runs it. Registering the drain is what makes ``POST /learning/jobs/trigger``
    acceptable at all — see :func:`learning_executor_status`.
    """
    scheduler = BackgroundScheduler()

    # Daily: research across all instrument x interval combos
    scheduler.add_job(
        run_scheduled_research,
        CronTrigger(hour=2, minute=0),
        id="daily_research",
        kwargs={"scope": "daily"},
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    # Weekly: pattern analysis + parameter proposals
    scheduler.add_job(
        run_pattern_analysis_job,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="weekly_analysis",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    poll_seconds = get_settings().learning_queue_poll_seconds
    if poll_seconds < 1:
        logger.warning(
            "LEARNING_QUEUE_POLL_SECONDS=%s is not a usable interval; draining every 1s",
            poll_seconds,
        )
        poll_seconds = 1
    scheduler.add_job(
        drain_learning_queue,
        IntervalTrigger(seconds=poll_seconds),
        id=DRAIN_JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    logger.info(
        "Learning scheduler created: daily_research@02:00, weekly_analysis@Sun 04:00, "
        "%s@%ss",
        DRAIN_JOB_ID,
        poll_seconds,
    )
    return scheduler


# The process that holds the running scheduler is the process that owns the
# executor, because APScheduler's BackgroundScheduler lives in the API process.
# Admission asks this object rather than reading a flag, so "the scheduler is
# configured" cannot stand in for "the scheduler is running".
_active_scheduler: Optional[BackgroundScheduler] = None


def register_learning_scheduler(scheduler: Optional[BackgroundScheduler]) -> None:
    """Publish the process's scheduler as the learning executor, or remove it."""
    global _active_scheduler
    _active_scheduler = scheduler


def learning_executor_status() -> Dict[str, Any]:
    """Whether queued learning work will actually be run here, and by what.

    A deployment that runs the API in more than one process needs the drain to
    live in one named executor process; this call can only speak for the process
    that answers it, and it refuses a submission it cannot honour rather than
    filing a request nobody claims.
    """
    scheduler = _active_scheduler
    if scheduler is None:
        return {
            "available": False,
            "reason": "no_learning_scheduler_in_this_process",
            "remedy": (
                "start the backend with ENABLE_LEARNING_SCHEDULER=true, which "
                "registers the queue drain in this process"
            ),
        }
    if not getattr(scheduler, "running", False):
        return {
            "available": False,
            "reason": "learning_scheduler_not_running",
            "remedy": "the scheduler was created but never started, or has shut down",
        }
    try:
        drain_job = scheduler.get_job(DRAIN_JOB_ID)
    except Exception as exc:  # a scheduler that cannot be read is not an executor
        logger.warning("Learning scheduler could not be queried: %s", exc)
        return {"available": False, "reason": "learning_scheduler_unreadable"}
    if drain_job is None:
        return {
            "available": False,
            "reason": "learning_queue_drain_missing",
            "remedy": "the queue executor is not registered, so pending rows would stay pending",
        }
    next_claim_at = getattr(drain_job, "next_run_time", None)
    if next_claim_at is None:
        return {
            "available": False,
            "reason": "learning_queue_drain_paused",
            "remedy": "resume the drain job; a paused executor never claims work",
        }
    return {
        "available": True,
        "reason": "learning_queue_drain_running",
        "next_claim_at": next_claim_at.isoformat(),
        "lease_seconds": get_settings().learning_lease_seconds,
    }


def executor_identity() -> str:
    """The name this process claims rows under, recorded in ``claimed_by``."""
    return f"{socket.gethostname()}:{os.getpid()}"[:64]


def _tick_operation_key(prefix: str) -> str:
    """Identify one cron slot so a double-fired tick replays instead of duplicating.

    The slot is the current UTC hour: it separates two nights of work and joins
    two firings of the same one. A tick delayed past the hour boundary mints a
    different key, so this deduplicates a same-slot double fire, not a late run.
    """
    slot = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return f"cron:{prefix}:{slot.strftime('%Y-%m-%dT%H')}"


def resolve_system_owner_id(db: Session) -> Optional[UUID]:
    """The account scheduled work belongs to, or None when that is not configured.

    Deliberately narrower than "find somebody": the email must be set, match an
    active account exactly, and resolve to one row. A tick that cannot name its
    owner files nothing, because a row owned by a bystander would read in that
    person's journal as work they never asked for.
    """
    email = (get_settings().learning_system_owner_email or "").strip().lower()
    if not email:
        return None

    from app.models.user import User

    return db.scalars(
        select(User.id).where(
            func.lower(User.email) == email,
            User.is_active.is_(True),
        )
    ).first()


def _settle_on_fresh_session(
    job_id: Optional[UUID], *, executor: Optional[str], outcome: "_Outcome"
) -> bool:
    """Write an outcome on a transaction the worker's own session cannot undo.

    Both jobs ran inside a single session whose exception handler calls
    ``db.rollback()``. Before the job row was committed, that rollback discarded
    it, so a batch that died mid-flight left no record that it had ever started —
    the UI just showed the previous night's job again. Restricted to
    ``status='running'`` so this can never overwrite a state another writer has
    already settled.

    The same fallback has to cover the success path: when the terminal write
    itself raises (a disconnected or poisoned session), the outcome is known but
    unrecorded, and a row left at ``running`` is only honest until its lease
    expires. Writing it here from a fresh session keeps the outcome the handler
    actually produced.
    """
    if job_id is None:
        return False
    db = SessionLocal()
    try:
        values: Dict[str, Any] = {"status": outcome.status, "completed_at": utcnow_aware()}
        if outcome.result_json is not None:
            values["result_json"] = outcome.result_json
        if outcome.error_details is not None:
            values["error_details"] = outcome.error_details

        statement = update(LearningJob).where(
            LearningJob.id == job_id, LearningJob.status == "running"
        )
        if executor is not None:
            # An executor whose lease already expired lost the right to settle
            # the row it came back for; the reclaim owns it now.
            statement = statement.where(LearningJob.claimed_by == executor)
        result = db.execute(statement.values(**values))
        db.commit()
        if result.rowcount == 0:
            logger.warning(
                "Learning job %s was not 'running' at recovery time; terminal state left alone",
                job_id,
            )
        return result.rowcount == 1
    except Exception:
        db.rollback()
        logger.exception("Could not record terminal state for learning job %s", job_id)
        return False
    finally:
        db.close()


def _record_terminal_failure(
    job_id: Optional[UUID], exc: BaseException, *, executor: Optional[str] = None
) -> None:
    """Record a ``failed`` outcome the worker's session could not write."""
    _settle_on_fresh_session(
        job_id,
        executor=executor,
        outcome=_Outcome("failed", error_details=safe_error_message(exc)),
    )


def create_or_replay_learning_job(
    db: Session,
    *,
    user_id: UUID,
    job_type: str,
    config_json: Dict[str, Any],
    origin: str,
    operation_key: Optional[str] = None,
) -> Tuple[LearningJob, bool]:
    """File one ``pending`` request row, or hand back the row this key filed.

    Returns ``(job, replayed)``. The database is what recognises a resubmission:
    the owner-scoped unique index on ``(user_id, operation_key)`` makes a second
    insert of the same submit lose, and the loser re-reads the winner instead of
    reporting a conflict it did not create. Same key plus same content replays;
    same key with different content is refused, because silently replacing the
    first request would make a retry mean something its author never asked for.

    An unkeyed submit is a new operation every time — that is what a caller who
    deliberately asks for a second run wants — so it cannot collide.
    """
    fingerprint = request_fingerprint({"job_type": job_type, "config": config_json})
    if operation_key:
        lock_user_for_idempotency(db, user_id)

    job = LearningJob(
        user_id=user_id,
        status="pending",
        origin=origin,
        job_type=job_type,
        config_json=config_json,
        operation_key=operation_key,
        request_fingerprint=fingerprint,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not operation_key:
            raise
        existing = db.scalars(
            select(LearningJob).where(
                LearningJob.user_id == user_id,
                LearningJob.operation_key == operation_key,
            )
        ).first()
        if existing is None:
            # Not our key collision — some other constraint refused the row.
            raise exc
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflict(
                "operation_key was already used for a different learning request"
            ) from exc
        logger.info(
            "Learning request %s replayed for key %s (origin=%s)",
            existing.id,
            operation_key,
            origin,
        )
        return existing, True

    db.refresh(job)
    return job, False


def claim_pending_learning_job_ids(
    db: Session, *, executor: str, limit: int = 1
) -> List[UUID]:
    """Atomically move up to ``limit`` pending rows to ``running`` under this executor.

    ``SELECT … FOR UPDATE SKIP LOCKED`` is what makes two executors disagree once
    rather than both doing the work: the loser's read skips the row another
    process is already holding. The claim is committed before any work starts,
    so the row is visibly running even if this process dies mid-batch.
    """
    rows = (
        db.query(LearningJob)
        .filter(LearningJob.status == "pending")
        .order_by(LearningJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    if not rows:
        return []

    now = utcnow_aware()
    claimed: List[UUID] = []
    for job in rows:
        job.status = "running"
        job.started_at = now
        job.claimed_by = executor
        job.claimed_at = now
        claimed.append(job.id)
    db.commit()
    logger.info("Learning queue: %s claimed %d job(s)", executor, len(claimed))
    return claimed


def settle_learning_job(
    db: Session,
    job_id: UUID,
    *,
    executor: str,
    status: str,
    result_json: Optional[Dict[str, Any]] = None,
    error_details: Optional[str] = None,
) -> bool:
    """Write the terminal state on the row this executor claimed, or refuse.

    Matched on ``claimed_by`` as well as ``status``: an executor that comes back
    after its lease expired must not overwrite the settlement the reclaim
    already recorded, or the row would end up claiming a result nobody saw.
    """
    values: Dict[str, Any] = {"status": status, "completed_at": utcnow_aware()}
    if result_json is not None:
        values["result_json"] = result_json
    if error_details is not None:
        values["error_details"] = error_details

    rowcount = db.execute(
        update(LearningJob)
        .where(
            LearningJob.id == job_id,
            LearningJob.status == "running",
            LearningJob.claimed_by == executor,
        )
        .values(**values)
    ).rowcount
    db.commit()

    if rowcount != 1:
        logger.warning(
            "Learning job %s was not settled by %s (rowcount=%d); its recorded outcome stands",
            job_id,
            executor,
            rowcount,
        )
        return False
    return True


def _settle_after_handler_failure(
    db: Session, job_id: UUID, executor: str, exc: BaseException
) -> str:
    """Settle a failed run, falling back to a separate session if this one is poisoned."""
    try:
        if settle_learning_job(
            db, job_id, executor=executor, status="failed",
            error_details=safe_error_message(exc),
        ):
            return "failed"
    except Exception:
        db.rollback()
    # The row is still 'running' and this session could not write it, which is
    # exactly the case _record_terminal_failure exists for.
    _record_terminal_failure(job_id, exc, executor=executor)
    return "failed"


class _Outcome(NamedTuple):
    """What a handler decided happened, so the executor can write it truthfully.

    Handlers return this rather than a bare dict because some batches genuinely
    failed while still running: a study whose every combo errored produced
    nothing, and recording it as ``completed`` would put a claim in the job list
    that nobody can act on.
    """

    status: str
    result_json: Optional[Dict[str, Any]] = None
    error_details: Optional[str] = None


def execute_learning_job(job_id: UUID, *, executor: Optional[str] = None) -> Optional[str]:
    """Run one claimed job and settle the same row it was requested as.

    Returns the terminal status, or None when this executor no longer owns the
    row — a job claimed by somebody else, or already settled, is left alone
    rather than run twice.
    """
    executor = executor or executor_identity()
    db = SessionLocal()
    try:
        job = db.get(LearningJob, job_id)
        if job is None:
            logger.warning("Learning job %s disappeared before execution", job_id)
            return None
        if job.status != "running" or job.claimed_by != executor:
            logger.warning(
                "Learning job %s is not this executor's (status=%s claimed_by=%s); not run",
                job_id,
                job.status,
                job.claimed_by,
            )
            return None

        handler = _JOB_HANDLERS.get(job.job_type)
        if handler is None:
            # Naming the type is more useful than a stack trace here: the row was
            # filed with a request this build cannot serve, and the owner has to
            # be able to see that in the job record.
            reason = f"unsupported_job_type: {job.job_type}"
            settle_learning_job(
                db, job_id, executor=executor, status="failed", error_details=reason
            )
            logger.error("Learning job %s: %s", job_id, reason)
            return "failed"

        try:
            outcome = handler(db, job)
        except Exception as exc:
            db.rollback()
            logger.exception("Learning job %s (%s) failed", job_id, job.job_type)
            return _settle_after_handler_failure(db, job_id, executor, exc)

        # The handler decided the outcome, so an unwritable terminal row is an
        # executor failure, not a missing outcome: retry it on a session the
        # handler's own transaction state cannot poison. A row left at `running`
        # after a batch that actually finished is a lie until its lease expires.
        try:
            wrote = settle_learning_job(
                db,
                job_id,
                executor=executor,
                status=outcome.status,
                result_json=outcome.result_json,
                error_details=outcome.error_details,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Learning job %s could not settle on its own session; retrying fresh", job_id
            )
            wrote = _settle_on_fresh_session(job_id, executor=executor, outcome=outcome)
        if wrote:
            return outcome.status
        return "failed"
    finally:
        db.close()


def reclaim_expired_learning_leases() -> int:
    """Settle rows whose executor stopped reporting. Fail-closed: never re-run.

    A row still ``running`` past ``LEARNING_LEASE_SECONDS`` means the process
    that claimed it wrote no terminal state — it died, or it is still working,
    and the row cannot tell which. Re-queueing would spend provider quota on a
    batch nobody asked for a second time and can duplicate the proposals of one
    that is genuinely still running, so the honest record is ``failed`` with the
    reason, and a new attempt is a new request with a new operation key.

    Rows with no ``claimed_at`` are judged on ``started_at``: that is the shape
    left by the pre-queue scheduler, which wrote ``running`` rows itself.
    """
    lease_seconds = get_settings().learning_lease_seconds
    db = SessionLocal()
    try:
        running = db.execute(
            select(
                LearningJob.id,
                LearningJob.claimed_at,
                LearningJob.started_at,
                LearningJob.created_at,
            )
            .where(LearningJob.status == "running")
        ).all()

        cutoff = utcnow_aware() - timedelta(seconds=lease_seconds)
        settled = 0
        for row in running:
            anchor = row.claimed_at or row.started_at or row.created_at
            if anchor is None:
                continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            if anchor > cutoff:
                continue
            reason = (
                f"lease_expired: claimed at {anchor.isoformat()} and no terminal "
                f"write within {lease_seconds}s. The executor's outcome is unknown; "
                "submit a new request to run it again."
            )
            rowcount = db.execute(
                update(LearningJob)
                .where(LearningJob.id == row.id, LearningJob.status == "running")
                .values(status="failed", error_details=reason, completed_at=utcnow_aware())
            ).rowcount
            if rowcount == 1:
                settled += 1
                logger.warning("Learning job %s settled as failed: expired lease", row.id)
        db.commit()
        return settled
    except Exception:
        db.rollback()
        logger.exception("Learning lease reclaim failed")
        return 0
    finally:
        db.close()


def drain_learning_queue(max_jobs: int = 1) -> int:
    """The queue executor: expire stale claims, then claim and run pending work.

    One job per tick keeps a long research batch from stacking up behind itself;
    the drain runs with ``max_instances=1``, so a tick that arrives while the
    previous one is still working is coalesced rather than run in parallel.
    """
    expired = reclaim_expired_learning_leases()
    executor = executor_identity()

    db = SessionLocal()
    try:
        job_ids = claim_pending_learning_job_ids(db, executor=executor, limit=max_jobs)
    except Exception:
        db.rollback()
        logger.exception("Learning queue claim failed")
        return 0
    finally:
        db.close()

    ran = 0
    for job_id in job_ids:
        if execute_learning_job(job_id, executor=executor) is not None:
            ran += 1
    if expired or ran:
        logger.info(
            "Learning drain: %d job(s) run, %d lease(s) expired", ran, expired
        )
    return ran


def enqueue_scheduled_research(
    db: Session,
    *,
    user_id: UUID,
    scope: str = "daily",
    instruments: Optional[List[str]] = None,
    intervals: Optional[List[int]] = None,
    origin: str = "schedule",
    operation_key: Optional[str] = None,
) -> Tuple[LearningJob, bool]:
    """File one pending study batch owned by ``user_id`` and return the row.

    The grid is frozen into ``config_json`` at submit time so the executor runs
    what was asked for rather than whatever the settings say when it gets there.
    ``instruments``/``intervals`` of ``None`` take the scheduled default; an
    explicitly empty list means "run nothing" and must not silently expand.
    """
    config: Dict[str, Any] = {
        "scope": scope,
        "instruments": list(
            SCHEDULED_INSTRUMENTS if instruments is None else instruments
        ),
        "intervals": list(SCHEDULED_INTERVALS if intervals is None else intervals),
    }
    job, replayed = create_or_replay_learning_job(
        db,
        user_id=user_id,
        job_type="scheduled_study",
        config_json=config,
        origin=origin,
        operation_key=operation_key,
    )
    logger.info(
        "Learning request %s filed (%s research, %s, origin=%s, %d combo(s))",
        job.id,
        scope,
        "replayed" if replayed else "queued",
        origin,
        len(config["instruments"]) * len(config["intervals"]),
    )
    return job, replayed


def run_scheduled_research(
    scope: str = "daily",
    instruments: Optional[List[str]] = None,
    intervals: Optional[List[int]] = None,
    user_id: Optional[UUID] = None,
    operation_key: Optional[str] = None,
) -> Optional[UUID]:
    """Cron entry: queue the daily batch, or refuse when no owner can be named.

    This files work and returns; ``drain_learning_queue`` runs it. Passing
    ``user_id`` is how a caller that already knows whose work this is says so —
    a trigger from the API must be attributed to the authenticated actor, never
    to "the first row in the users table".
    """
    db = SessionLocal()
    try:
        owner_id = user_id if user_id is not None else resolve_system_owner_id(db)
        if owner_id is None:
            # Nothing was asked for, so nothing is recorded. The alternative —
            # filing a row under a bystander's id — would show up in that
            # person's job list as research they never requested.
            logger.error(
                "Scheduled research (%s) refused: LEARNING_SYSTEM_OWNER_EMAIL is "
                "not set to an active account, so this run has no owner",
                scope,
            )
            return None

        job, _ = enqueue_scheduled_research(
            db,
            user_id=owner_id,
            scope=scope,
            instruments=instruments,
            intervals=intervals,
            origin="schedule",
            operation_key=operation_key
            if operation_key is not None
            else _tick_operation_key("daily_research"),
        )
        return job.id
    except Exception:
        db.rollback()
        logger.exception("Could not queue scheduled research (%s)", scope)
        return None
    finally:
        db.close()


def _handle_scheduled_study(db: Session, job: LearningJob) -> _Outcome:
    """Run the queued grid and report what actually happened on this row.

    Each combo that dies is recorded with its reason and the batch continues:
    one instrument's provider outage is not evidence the other five were fine,
    and it is not evidence the whole batch failed either. The status therefore
    distinguishes "produced something, with gaps" (``completed`` plus the
    reasons in ``error_details``) from "produced nothing" (``failed``).
    """
    config = job.config_json or {}
    instruments = list(config.get("instruments") or [])
    intervals = list(config.get("intervals") or [])
    user_id = job.user_id

    results: List[dict] = []
    errors: List[str] = []

    for instrument_id in instruments:
        for interval in intervals:
            try:
                result = _run_single_study(db, user_id, instrument_id, interval)
                trade_count = (
                    result.get("walk_forward", {}).get("out_of_sample", {}).get("trades", 0)
                )
                results.append(
                    {
                        "instrument_id": instrument_id,
                        "interval_minutes": interval,
                        "trade_count": trade_count,
                        "status": "ok",
                    }
                )
                logger.info(
                    "Study completed: %s @ %dm — %d trades",
                    instrument_id,
                    interval,
                    trade_count,
                )
            except Exception as exc:
                # ``GET /learning/jobs`` renders ``error_details`` verbatim, so
                # this string reaches a person: keep the raw exception (paths,
                # DSNs) in the log and store only the bounded summary.
                errors.append(f"{instrument_id} @ {interval}m: {safe_error_message(exc)}")
                logger.warning(
                    "Study failed for %s @ %dm", instrument_id, interval, exc_info=True
                )

    # A batch whose every combo failed produced nothing, so it is not a success;
    # a partial failure did produce the results it claims and stays completed.
    status = "failed" if errors and not results else "completed"
    return _Outcome(
        status=status,
        result_json={
            "scope": config.get("scope"),
            "results": results,
            "errors": errors,
            "total_combos": len(instruments) * len(intervals),
            "successful": len(results),
            "failed": len(errors),
        },
        error_details="\n".join(errors) if errors else None,
    )


def _run_single_study(
    db: Session, user_id: UUID, instrument_id: str, interval_minutes: int
) -> dict:
    """Run a single study for one instrument/interval combo.

    Fetches bars from the most recent stored run (to avoid re-fetching from API),
    then runs the study and persists it.
    """
    # Try to reuse bars from the most recent stored run
    stmt = (
        select(StrategyStudyRun)
        .where(
            StrategyStudyRun.user_id == user_id,
            StrategyStudyRun.instrument_id == instrument_id,
            StrategyStudyRun.interval_minutes == interval_minutes,
        )
        .order_by(StrategyStudyRun.created_at.desc())
        .limit(1)
    )
    existing_run = db.scalars(stmt).first()

    if existing_run and existing_run.bars_json:
        # Reuse stored bars
        bars = _bars_from_json(existing_run.bars_json, interval_minutes)
    else:
        # No stored bars available — fetch from provider
        from app.providers.crypto_registry import get_crypto_provider

        provider = get_crypto_provider()
        series = provider.get_candle_series(
            instrument_id, interval_minutes=interval_minutes, limit=720
        )
        bars = [
            LabBar(
                open_time=candle.open_time,
                interval_minutes=interval_minutes,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for candle in series.completed
        ]

    if len(bars) < 120:
        raise ValueError(f"Insufficient bars: {len(bars)} < 120")

    result = run_study(bars, settings=ResearchSettings(), include_trades=True)

    dataset = {
        "instrument_id": instrument_id,
        "interval_minutes": interval_minutes,
        "provider_id": "kraken",
        "data_nature": "venue_completed_bars",
        "bars": len(bars),
        "first_open_time": bars[0].open_time.isoformat(),
        "last_open_time": bars[-1].open_time.isoformat(),
        "fingerprint": None,
        "integrity_ok": True,
        "gap_count": 0,
        "missing_intervals": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    persist_study(db, user_id=user_id, result=result, bars=bars, dataset=dataset)
    return result


def _bars_from_json(bars_json: list, interval_minutes: int) -> List[LabBar]:
    """Reconstruct LabBar list from stored bars_json."""
    from datetime import datetime, timezone

    bars: List[LabBar] = []
    for row in bars_json:
        open_time = row.get("open_time")
        if isinstance(open_time, str):
            parsed = datetime.fromisoformat(open_time)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            open_time = parsed
        bars.append(
            LabBar(
                open_time=open_time,
                interval_minutes=interval_minutes,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
        )
    return bars


def enqueue_pattern_analysis(
    db: Session,
    *,
    user_id: UUID,
    lookback_runs: int = DEFAULT_LOOKBACK_RUNS,
    origin: str = "schedule",
    operation_key: Optional[str] = None,
) -> Tuple[LearningJob, bool]:
    """File one pending pattern-analysis request owned by ``user_id``."""
    job, replayed = create_or_replay_learning_job(
        db,
        user_id=user_id,
        job_type="pattern_analysis",
        config_json={"lookback_runs": lookback_runs},
        origin=origin,
        operation_key=operation_key,
    )
    logger.info(
        "Learning request %s filed (pattern analysis, %s, origin=%s)",
        job.id,
        "replayed" if replayed else "queued",
        origin,
    )
    return job, replayed


def run_pattern_analysis_job(
    user_id: Optional[UUID] = None,
    operation_key: Optional[str] = None,
) -> Optional[UUID]:
    """Cron entry: queue the weekly analysis for the system owner.

    Filings only. The row is claimed and settled by the same executor that runs
    study batches, so a weekly tick cannot leave a request sitting at
    ``pending`` in an owner's job list forever.
    """
    db = SessionLocal()
    try:
        owner_id = user_id if user_id is not None else resolve_system_owner_id(db)
        if owner_id is None:
            logger.error(
                "Pattern analysis refused: LEARNING_SYSTEM_OWNER_EMAIL is not set "
                "to an active account, so this run has no owner"
            )
            return None

        job, _ = enqueue_pattern_analysis(
            db,
            user_id=owner_id,
            origin="schedule",
            operation_key=operation_key
            if operation_key is not None
            else _tick_operation_key("weekly_analysis"),
        )
        return job.id
    except Exception:
        db.rollback()
        logger.exception("Could not queue pattern analysis")
        return None
    finally:
        db.close()


def _handle_pattern_analysis(db: Session, job: LearningJob) -> _Outcome:
    """Analyse this owner's trades and file the proposals the evidence supports.

    1. Read the last N runs' trades for the row's own owner.
    2. Group by (rule_id, exit_id) and compute metrics.
    3. Identify patterns: best exit configs per skill, trending vs ranging.
    4. Generate LearningProposal records for parameter adjustments.
    5. Validate each proposal against a real baseline run.
    6. Never auto-deploy: validation only sets ``validated`` / ``rejected`` /
       ``pending`` for the owner to decide on.
    """
    user_id = job.user_id
    # The request row carries its own lookback; an executor that re-read a
    # global setting would run a different analysis than the one asked for.
    lookback_runs = (job.config_json or {}).get("lookback_runs", DEFAULT_LOOKBACK_RUNS)

    analysis = analyze_skill_patterns(db, user_id=user_id, lookback_runs=lookback_runs)

    proposals_created = 0
    for exit_id, metrics in analysis.get("per_exit_config", {}).items():
        exit_config = _resolve_exit_config(exit_id)
        if exit_config is None:
            continue

        adjustment = propose_parameter_adjustment(
            current_exit=exit_config,
            performance_data=metrics,
        )
        if adjustment is None:
            continue

        # Find the instrument/interval from the most recent run using this exit
        recent_run = _find_recent_run_for_exit(db, user_id, exit_id)
        if recent_run is None:
            continue

        rule_id = _find_best_rule_for_exit(db, user_id, recent_run.id, exit_id)
        if rule_id is None:
            # No owner-scoped trade names a rule for this exit, so there is
            # nothing to propose against; a guessed rule would be attributed to
            # no measurement, which is exactly what a proposal must not be.
            logger.warning(
                "Proposal for exit %s skipped: run %s has no trades for this owner",
                exit_id,
                recent_run.id,
            )
            continue

        # Create proposal
        # Note: propose_parameter_adjustment now gates on trade_count >= 30
        # and marks all proposals as "hypothesis" (not evidence-based)
        evidence_status = adjustment.get("evidence_status", "hypothesis")

        proposal = LearningProposal(
            user_id=user_id,
            learning_job_id=job.id,
            rule_id=rule_id,
            exit_id=exit_id,
            instrument_id=recent_run.instrument_id,
            interval_minutes=recent_run.interval_minutes,
            baseline_exit_config=adjustment["baseline_exit_config"],
            proposed_exit_config=adjustment["proposed_exit_config"],
            rationale_json={
                "pattern": adjustment["pattern"],
                "baseline_metrics": metrics,
                "rationale": adjustment["rationale"],
                "evidence_status": evidence_status,
            },
            status="pending",
        )
        db.add(proposal)
        db.flush()

        # Validate the proposal
        validation = validate_proposal(
            db,
            user_id=user_id,
            proposal={
                "instrument_id": recent_run.instrument_id,
                "interval_minutes": recent_run.interval_minutes,
                "rule_id": proposal.rule_id,
                "baseline_exit_config": {
                    **adjustment["baseline_exit_config"],
                    "exit_id": exit_id,
                },
                "proposed_exit_config": adjustment["proposed_exit_config"],
            },
        )

        proposal.validation_result = validation

        # Evidence gating: hypothesis proposals cannot be validated/deployed
        # They remain as pending for manual review only
        if evidence_status == "hypothesis":
            proposal.status = "pending"
            logger.info(
                "Proposal %s remains pending (hypothesis, not evidence-based)",
                proposal.id,
            )
        elif validation.get("passed"):
            proposal.status = "validated"
            proposal.validated_at = utcnow_aware()
            logger.info(
                "Proposal %s validated: Sharpe %.2f -> %.2f",
                proposal.id,
                validation.get("sharpe_before", 0),
                validation.get("sharpe_after", 0),
            )
        elif validation.get("error") in ("no_baseline_run", "insufficient_bars", "unknown_rule"):
            # Validation could not run due to missing data or unknown rule — keep as
            # ``pending`` for manual review rather than rejecting.
            proposal.status = "pending"
            logger.info(
                "Proposal %s pending (validation skipped: %s)",
                proposal.id,
                validation.get("error"),
            )
        else:
            proposal.status = "rejected"
            logger.info("Proposal %s rejected by validation", proposal.id)

        proposals_created += 1

    return _Outcome(
        status="completed",
        result_json={"analysis": analysis, "proposals_created": proposals_created},
    )


_JOB_HANDLERS = {
    "scheduled_study": _handle_scheduled_study,
    "pattern_analysis": _handle_pattern_analysis,
}


def _find_recent_run_for_exit(
    db: Session, user_id: UUID, exit_id: str
) -> Optional[StrategyStudyRun]:
    """Find the most recent run that has trades using this exit_id."""
    from app.models.strategy_study import StrategyStudyTrade

    stmt = (
        select(StrategyStudyRun)
        .join(StrategyStudyTrade, StrategyStudyTrade.run_id == StrategyStudyRun.id)
        .where(
            StrategyStudyRun.user_id == user_id,
            StrategyStudyTrade.exit_id == exit_id,
        )
        .order_by(StrategyStudyRun.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _find_best_rule_for_exit(
    db: Session, user_id: UUID, run_id: UUID, exit_id: str
) -> Optional[str]:
    """Find the rule_id with the most trades for a given exit in one owner's run.

    ``user_id`` is part of the predicate, not decoration: without it a run id
    that reached here from elsewhere could attribute another tenant's trades to
    this owner's proposal.

    ``None`` means this owner's run holds no such trades. The caller then files
    no proposal — the alternative was a hard-coded ``"breakout_trend"``, which
    would have written a rule nobody measured into the proposal that a person
    later reads as strategy evidence.
    """
    from app.models.strategy_study import StrategyStudyTrade
    from collections import Counter

    trades = db.scalars(
        select(StrategyStudyTrade)
        .join(StrategyStudyRun, StrategyStudyRun.id == StrategyStudyTrade.run_id)
        .where(
            StrategyStudyTrade.run_id == run_id,
            StrategyStudyTrade.exit_id == exit_id,
            StrategyStudyRun.user_id == user_id,
        )
    ).all()

    if not trades:
        return None

    rule_counts = Counter(t.rule_id for t in trades)
    return rule_counts.most_common(1)[0][0]


def run_learning_worker() -> int:
    """Run the producers and the queue executor as a standalone process.

    ``python -m app.workers.learning_scheduler`` is the remedy
    :func:`learning_executor_status` names for a deployment that keeps
    APScheduler out of the API process. Two processes may run this at once: a
    cron slot both of them file collapses onto one owner-scoped operation key,
    so the later one replays the first row instead of duplicating the batch, and
    the queue claim skips rows another process already holds.

    Returns 0 after a clean signal, 1 if the scheduler died on its own — a
    process that stopped working must not exit as though it were fine.
    """
    scheduler = create_learning_scheduler()
    register_learning_scheduler(scheduler)
    scheduler.start()

    stop_requested = threading.Event()

    def _request_stop(signum, _frame):
        logger.info("Learning worker received signal %s; stopping", signum)
        stop_requested.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    logger.info("Learning worker running as %s", executor_identity())
    died_on_its_own = False
    try:
        while not stop_requested.wait(get_settings().learning_queue_poll_seconds):
            if not scheduler.running:
                logger.error("Learning scheduler stopped unexpectedly; exiting")
                died_on_its_own = True
                break
    finally:
        scheduler.shutdown(wait=False)
        register_learning_scheduler(None)
    return 1 if died_on_its_own else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run_learning_worker())
