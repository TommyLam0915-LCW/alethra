"""Learning API routes.

Exposes the auto-learning loop to the owner: list jobs, trigger research,
review proposals, and deploy validated parameter adjustments.

NEVER auto-deploys. Every deploy requires an explicit POST from the owner.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.user import User
from app.services.pattern_analysis import analyze_skill_patterns

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger(__name__)


class TriggerLearningRequest(BaseModel):
    """Request body for manually triggering a learning cycle."""

    job_type: str = Field(
        default="scheduled_study",
        pattern="^(scheduled_study|pattern_analysis)$",
    )
    instruments: Optional[list[str]] = None
    intervals: Optional[list[int]] = None


class LearningJobResponse(BaseModel):
    """Serialized learning job."""

    id: str
    user_id: str
    status: str
    job_type: str
    config_json: dict
    result_json: Optional[dict]
    error_details: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]


class LearningProposalResponse(BaseModel):
    """Serialized learning proposal."""

    id: str
    user_id: str
    learning_job_id: str
    rule_id: str
    exit_id: str
    instrument_id: str
    interval_minutes: int
    baseline_exit_config: dict
    proposed_exit_config: dict
    rationale_json: dict
    validation_result: Optional[dict]
    status: str
    created_at: str
    validated_at: Optional[str]
    deployed_at: Optional[str]


def _serialize_job(job: LearningJob) -> dict:
    return {
        "id": str(job.id),
        "user_id": str(job.user_id),
        "status": job.status,
        "job_type": job.job_type,
        "config_json": job.config_json,
        "result_json": job.result_json,
        "error_details": job.error_details,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _serialize_proposal(proposal: LearningProposal) -> dict:
    return {
        "id": str(proposal.id),
        "user_id": str(proposal.user_id),
        "learning_job_id": str(proposal.learning_job_id),
        "rule_id": proposal.rule_id,
        "exit_id": proposal.exit_id,
        "instrument_id": proposal.instrument_id,
        "interval_minutes": proposal.interval_minutes,
        "baseline_exit_config": proposal.baseline_exit_config,
        "proposed_exit_config": proposal.proposed_exit_config,
        "rationale_json": proposal.rationale_json,
        "validation_result": proposal.validation_result,
        "status": proposal.status,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "validated_at": proposal.validated_at.isoformat() if proposal.validated_at else None,
        "deployed_at": proposal.deployed_at.isoformat() if proposal.deployed_at else None,
    }


@router.get("/jobs")
def list_learning_jobs(
    limit: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List learning jobs for the current user."""
    stmt = (
        select(LearningJob)
        .where(LearningJob.user_id == current_user.id)
        .order_by(LearningJob.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(LearningJob.status == status_filter)

    jobs = list(db.scalars(stmt).all())
    return {
        "jobs": [_serialize_job(j) for j in jobs],
        "total": len(jobs),
    }


@router.post("/jobs/trigger", status_code=status.HTTP_201_CREATED)
def trigger_learning_job(
    payload: TriggerLearningRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Manually trigger a research + analysis cycle.

    Creates a LearningJob record and dispatches the work to the scheduler
    worker. If the scheduler is not running, the job stays in ``pending``
    until the next scheduler tick or a manual run.
    """
    config = {
        "job_type": payload.job_type,
        "instruments": payload.instruments or [
            "CRYPTO:KRAKEN:BTC-USD",
            "CRYPTO:KRAKEN:ETH-USD",
            "CRYPTO:KRAKEN:SOL-USD",
        ],
        "intervals": payload.intervals or [1440, 240, 60, 15],
    }

    job = LearningJob(
        user_id=current_user.id,
        status="pending",
        job_type=payload.job_type,
        config_json=config,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    logger.info(
        "Learning job %s triggered by user %s (type=%s)",
        job.id,
        current_user.id,
        payload.job_type,
    )

    # Try to run immediately if scheduler is available
    try:
        from app.main import _learning_scheduler

        if _learning_scheduler is not None and _learning_scheduler.running:
            from app.workers.learning_scheduler import (
                run_scheduled_research,
                run_pattern_analysis_job,
            )
            import threading

            if payload.job_type == "scheduled_study":
                thread = threading.Thread(
                    target=run_scheduled_research,
                    kwargs={"scope": "manual"},
                    daemon=True,
                )
                thread.start()
            elif payload.job_type == "pattern_analysis":
                thread = threading.Thread(
                    target=run_pattern_analysis_job,
                    daemon=True,
                )
                thread.start()
    except Exception as exc:
        logger.warning("Could not dispatch job %s immediately: %s", job.id, exc)

    return {
        "job": _serialize_job(job),
        "message": "Job created. It will run on the next scheduler tick or immediately if the scheduler is active.",
    }


@router.get("/proposals")
def list_proposals(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List parameter proposals with validation status."""
    stmt = (
        select(LearningProposal)
        .where(LearningProposal.user_id == current_user.id)
        .order_by(LearningProposal.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(LearningProposal.status == status_filter)

    proposals = list(db.scalars(stmt).all())
    return {
        "proposals": [_serialize_proposal(p) for p in proposals],
        "total": len(proposals),
    }


@router.post("/proposals/{proposal_id}/deploy")
def deploy_proposal(
    proposal_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Owner decision: deploy a validated proposal.

    Updates the proposal status to ``deployed`` and records the deployment
    timestamp. The proposed exit config is stored in the rationale for
    future reference.

    NEVER auto-deploys. Always requires owner authorization.
    """
    proposal = db.scalars(
        select(LearningProposal).where(
            LearningProposal.id == proposal_id,
            LearningProposal.user_id == current_user.id,
        )
    ).first()

    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": "proposal_not_found", "proposal_id": str(proposal_id)},
        )

    # Check status: only 'validated' can be deployed
    if proposal.status != "validated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "proposal_not_validated",
                "current_status": proposal.status,
                "message": (
                    f"Only proposals with status 'validated' can be deployed. "
                    f"Current status: '{proposal.status}'. "
                    f"Pending proposals require validation first."
                ),
            },
        )

    # Check evidence status: only validated_candidate can be deployed
    # Explicit allow-list: reject all other statuses (unknown, descriptive, hypothesis, insufficient, unsupported)
    evidence_status = proposal.rationale_json.get("evidence_status", "unknown")
    if evidence_status != "validated_candidate":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "proposal_not_validated",
                "evidence_status": evidence_status,
                "allowed_status": "validated_candidate",
                "message": (
                    f"This proposal has evidence_status='{evidence_status}'. "
                    f"Only 'validated_candidate' proposals can be deployed. "
                    f"Status meanings: "
                    f"'unknown' (not assessed), "
                    f"'descriptive' (historical description only), "
                    f"'hypothesis' (parameter exploration), "
                    f"'insufficient' (not enough data), "
                    f"'unsupported' (evidence contradicts). "
                    f"Requires formal validation to become validated_candidate."
                ),
            },
        )

    # Check validation result: must have passed
    validation_result = proposal.validation_result or {}
    if not validation_result.get("passed", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "validation_not_passed",
                "validation_result": validation_result,
                "message": (
                    "This proposal has not passed validation. "
                    "Cannot deploy unvalidated proposals."
                ),
            },
        )

    proposal.status = "deployed"
    proposal.deployed_at = utcnow_aware()
    db.commit()
    db.refresh(proposal)

    logger.info(
        "Proposal %s deployed by user %s: %s/%s %s@%dm",
        proposal.id,
        current_user.id,
        proposal.rule_id,
        proposal.exit_id,
        proposal.instrument_id,
        proposal.interval_minutes,
    )

    return {
        "proposal": _serialize_proposal(proposal),
        "message": (
            "Proposal deployed. The proposed exit config will be used in "
            "future research runs for this instrument/interval."
        ),
    }


@router.get("/analysis")
def get_pattern_analysis(
    lookback_runs: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Run pattern analysis on-demand and return results."""
    analysis = analyze_skill_patterns(
        db, user_id=current_user.id, lookback_runs=lookback_runs
    )
    return {
        "analysis": analysis,
        "disclaimer": (
            "Pattern analysis is diagnostic, not predictive. Past performance "
            "of a parameter configuration does not guarantee future results."
        ),
    }
