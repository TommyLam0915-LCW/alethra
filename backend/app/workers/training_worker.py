#!/usr/bin/env python3
"""Training job worker — polls for pending jobs and executes them.

This is a simple database-backed worker that provides durable execution
for training jobs. It can be run as a separate process alongside the
FastAPI application.

Usage:
    python -m app.workers.training_worker

The worker will:
1. Poll for pending training jobs
2. Claim jobs exclusively (SELECT FOR UPDATE)
3. Execute the parameter sweep
4. Handle failures and cancellation
5. Repeat until stopped (Ctrl+C)

Multiple workers can run concurrently; each job is claimed by exactly
one worker via row-level locking.
"""
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.kill_switch import KillSwitchScope
from app.services.kill_switch import is_kill_switch_active
from app.services.training import run_parameter_sweep
from app.services.ml_training import execute_ml_training
from app.auth.entitlements import settle_reservation, release_reservation
from app.lib.timeutil import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Graceful shutdown flag
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _shutdown_requested
    logger.info("Shutdown signal received, finishing current job...")
    _shutdown_requested = True


def claim_pending_job(db) -> Optional[TrainingJob]:
    """Claim a single pending job using SELECT FOR UPDATE.

    Returns the claimed job, or None if no pending jobs exist.
    Uses row-level locking to ensure exclusive claiming.

    Checks kill switch before claiming: if training or global kill switch
    is active, does not claim any new jobs. Pending jobs remain pending
    and will be claimed when the kill switch is deactivated.
    """
    # Check kill switch before claiming
    try:
        if is_kill_switch_active(db, KillSwitchScope.training):
            logger.info("Kill switch active, not claiming new jobs")
            return None
    except Exception as e:
        # Fail-closed: if we can't read the switch, don't claim jobs
        logger.error(f"Unable to check kill switch: {e}, not claiming jobs")
        return None

    job = (
        db.query(TrainingJob)
        .filter(TrainingJob.status == TrainingJobStatus.pending)
        .order_by(TrainingJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )

    if job:
        job.status = TrainingJobStatus.running
        spec = dict(job.strategy_spec_snapshot or {})
        spec["claimed_at"] = utcnow().isoformat()
        job.strategy_spec_snapshot = spec
        db.commit()
        logger.info(f"Claimed job {job.id}")

    return job


def reclaim_stale_running_jobs(db, stale_after_seconds: Optional[float] = None) -> int:
    """Reset running jobs back to pending when their claim looks abandoned.

    A worker crash between claim and completion leaves a job stuck in
    `running` forever. The claim timestamp recorded in
    strategy_spec_snapshot['claimed_at'] is compared against
    stale_after_seconds (default from TRAINING_STALE_AFTER_SECONDS env,
    900s) — anything older is considered orphaned and reset to pending so
    a restarted worker can claim it again.

    Only resets jobs that carry a claimed_at marker; jobs claimed by
    older worker versions (no marker) are left untouched rather than
    guessed at. Call this from the worker loop before claiming; with
    multiple workers, set stale_after_seconds comfortably above the
    longest expected job runtime.
    """
    if stale_after_seconds is None:
        try:
            stale_after_seconds = float(
                os.environ.get("TRAINING_STALE_AFTER_SECONDS", "900")
            )
        except ValueError:
            stale_after_seconds = 900.0

    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    reclaimed = 0

    running = (
        db.query(TrainingJob)
        .filter(
            TrainingJob.status == TrainingJobStatus.running,
            TrainingJob.completed_at == None,  # noqa: E711
        )
        .all()
    )

    for job in running:
        spec = dict(job.strategy_spec_snapshot or {})
        claimed_at_raw = spec.get("claimed_at")
        if not claimed_at_raw:
            continue
        try:
            claimed_at = datetime.fromisoformat(str(claimed_at_raw))
        except ValueError:
            continue
        if claimed_at < cutoff:
            job.status = TrainingJobStatus.pending
            spec["reclaimed_at"] = utcnow().isoformat()
            spec.pop("claimed_at", None)
            job.strategy_spec_snapshot = spec
            db.commit()
            reclaimed += 1
            logger.warning(
                f"Reclaimed stale running job {job.id} "
                f"(claimed at {claimed_at_raw}, stale after {stale_after_seconds}s)"
            )

    return reclaimed


def _is_ml_job(job: TrainingJob) -> bool:
    return (job.strategy_spec_snapshot or {}).get("training_type") == "ml_model"


def _quota_reservation_id(job: TrainingJob) -> Optional[uuid.UUID]:
    raw = (job.strategy_spec_snapshot or {}).get("quota_reservation_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _settle_ml_quota(db, job: TrainingJob) -> None:
    reservation_id = _quota_reservation_id(job)
    if reservation_id is None:
        return
    try:
        settle_reservation(db, reservation_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            f"Failed to settle quota reservation {reservation_id} for job {job.id}: {exc}"
        )


def _release_ml_quota(db, job: TrainingJob) -> None:
    reservation_id = _quota_reservation_id(job)
    if reservation_id is None:
        return
    try:
        release_reservation(db, reservation_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            f"Failed to release quota reservation {reservation_id} for job {job.id}: {exc}"
        )


def run_claimed_job(db, job_id) -> None:
    """Execute a claimed job on the given session (routes by job type).

    ML jobs go to execute_ml_training; everything else to the parameter
    sweep. On ML completion the job's quota reservation is settled; on
    failure it is released so the quota is not consumed.
    """
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        logger.error(f"Job {job_id} not found")
        return

    logger.info(f"Executing job {job_id} (experiment {job.experiment_id})")

    if _is_ml_job(job):
        execute_ml_training(db, job.id)
        db.commit()
        if job.status == TrainingJobStatus.completed:
            _settle_ml_quota(db, job)
        else:
            _release_ml_quota(db, job)
    else:
        run_parameter_sweep(db, job.id)
        db.commit()

    logger.info(f"Job {job_id} finished with status {job.status.value}")


def execute_job(job_id: int) -> None:
    """Execute a training job with error handling."""
    db = SessionLocal()
    try:
        run_claimed_job(db, job_id)
    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}", exc_info=True)
        db.rollback()

        # Mark job as failed
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if job:
            job.status = TrainingJobStatus.failed
            job.error_details = str(exc)
            job.completed_at = utcnow()
            db.commit()
            if _is_ml_job(job):
                _release_ml_quota(db, job)

    finally:
        db.close()


def worker_loop(poll_interval: float = 5.0) -> None:
    """Main worker loop — poll for jobs and execute them."""
    logger.info("Training worker started")
    logger.info(f"Poll interval: {poll_interval}s")

    while not _shutdown_requested:
        db = SessionLocal()
        try:
            reclaim_stale_running_jobs(db)
            job = claim_pending_job(db)

            if job:
                job_id = job.id
                db.close()
                execute_job(job_id)
            else:
                db.close()
                time.sleep(poll_interval)

        except Exception as exc:
            logger.error(f"Worker loop error: {exc}", exc_info=True)
            try:
                db.close()
            except:
                pass
            time.sleep(poll_interval)

    logger.info("Training worker stopped")


def main():
    """Entry point — set up signal handlers and run worker loop."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    poll_interval = 5.0
    if len(sys.argv) > 1:
        try:
            poll_interval = float(sys.argv[1])
        except ValueError:
            logger.warning(f"Invalid poll interval: {sys.argv[1]}, using default 5.0s")

    worker_loop(poll_interval=poll_interval)


if __name__ == "__main__":
    main()
