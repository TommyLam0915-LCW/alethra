"""Cross-process cancellation for training jobs.

The cancel endpoint records ``cancelled_at`` on the job row. Executors run in
the worker process, so a process-local flag set by an HTTP worker is never
visible to them. Every executor therefore reads the column from the database,
and marks the job cancelled itself.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.models.training import TrainingJob, TrainingJobStatus


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was reused with a different request."""


def request_fingerprint(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lock_user_for_idempotency(db: Session, user_id: uuid.UUID) -> None:
    """Serialize idempotent creations for one user.

    Two concurrent requests with the same key would otherwise both miss the
    existing job and create a second one plus a second quota reservation.
    Locking the user row makes the second request wait until the first
    commits, then find it.
    """
    from app.models.user import User

    db.query(User).filter(User.id == user_id).with_for_update().first()


def find_idempotent_job(
    db: Session,
    user_id: uuid.UUID,
    key: str,
    fingerprint: str,
    *,
    training_type: Optional[str] = None,
) -> Optional[TrainingJob]:
    recent = (
        db.query(TrainingJob)
        .filter(TrainingJob.user_id == user_id)
        .order_by(TrainingJob.created_at.desc())
        .limit(200)
        .all()
    )
    for job in recent:
        spec = job.strategy_spec_snapshot or {}
        if spec.get("idempotency_key") != key:
            continue
        if training_type is not None and spec.get("training_type") != training_type:
            continue
        if spec.get("request_fingerprint") != fingerprint:
            raise IdempotencyConflict(
                "Idempotency-Key was already used with a different request"
            )
        return job
    return None


def stamp_idempotency(
    job: TrainingJob, key: str, fingerprint: str
) -> Dict[str, Any]:
    spec = dict(job.strategy_spec_snapshot or {})
    spec["idempotency_key"] = key
    spec["request_fingerprint"] = fingerprint
    job.strategy_spec_snapshot = spec
    return spec


def cancel_requested_at(db: Session, job_id: uuid.UUID) -> Optional[datetime]:
    """Return the stored cancellation timestamp, if any.

    Reads the column directly: a ``TrainingJob`` already loaded in this
    session would otherwise answer from the identity map and miss a
    cancellation committed by another process after the row was loaded.
    """
    return (
        db.query(TrainingJob.cancelled_at)
        .filter(TrainingJob.id == job_id)
        .scalar()
    )


def mark_cancelled(
    job: TrainingJob,
    requested_at: Optional[datetime] = None,
    *,
    reason: str = "Job cancelled by user",
) -> TrainingJob:
    job.status = TrainingJobStatus.cancelled
    job.cancelled_at = requested_at or job.cancelled_at or utcnow()
    job.completed_at = utcnow()
    job.error_details = reason
    return job


def stop_if_cancelled(
    db: Session,
    job: TrainingJob,
    *,
    reason: str = "Job cancelled by user",
) -> bool:
    """Mark the job cancelled when a cancellation was requested.

    Returns True when the caller must stop without doing the work.
    """
    requested_at = cancel_requested_at(db, job.id)
    if requested_at is None:
        return False
    mark_cancelled(job, requested_at, reason=reason)
    db.flush()
    return True
