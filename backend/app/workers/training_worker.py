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
import signal
import sys
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.kill_switch import KillSwitchScope
from app.services.kill_switch import is_kill_switch_active
from app.services.training import run_parameter_sweep

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
        db.commit()
        logger.info(f"Claimed job {job.id}")

    return job


def execute_job(job_id: int) -> None:
    """Execute a training job with error handling."""
    db = SessionLocal()
    try:
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return

        logger.info(f"Executing job {job_id} (experiment {job.experiment_id})")

        run_parameter_sweep(db, job.id)
        db.commit()

        logger.info(f"Job {job_id} completed successfully")

    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}", exc_info=True)
        db.rollback()

        # Mark job as failed
        job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
        if job:
            job.status = TrainingJobStatus.failed
            job.error_details = str(exc)
            job.completed_at = datetime.utcnow()
            db.commit()

    finally:
        db.close()


def worker_loop(poll_interval: float = 5.0) -> None:
    """Main worker loop — poll for jobs and execute them."""
    logger.info("Training worker started")
    logger.info(f"Poll interval: {poll_interval}s")

    while not _shutdown_requested:
        db = SessionLocal()
        try:
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
