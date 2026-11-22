"""Learning API routes.

Exposes the auto-learning loop to the owner: list jobs, queue research, review
proposals, approve a proposal that passed validation, and deploy an approved one.

The trigger route is a queue write, not a run: it files one ``pending`` row owned
by the authenticated caller, and ``drain_learning_queue`` in the scheduler process
is what claims and settles it. A submission is refused when no such executor is
live, because accepting work that cannot run leaves a request the owner has to
notice and clean up by hand.

NEVER auto-approves and NEVER auto-deploys. Both decisions are separate
explicit POSTs from the owner.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.database import get_db
from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.user import User
from app.services import emit_event
from app.services.job_control import IdempotencyConflict
from app.services.pattern_analysis import analyze_skill_patterns

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger(__name__)

# A caller-supplied key is stored under this prefix so it cannot be mistaken
# for, or collide with, the ``cron:`` keys the scheduler mints for its own slots.
# The column is String(128), so the key itself is capped at 120 to leave room.
USER_KEY_PREFIX = "user:"
USER_KEY_MAX = 120
USER_KEY_MIN = 8


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
    origin: Optional[str]
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
        # ``user`` / ``schedule`` — and ``None`` for a row written before the
        # field existed. Guessing "user" there would attribute an old batch to
        # somebody who never asked for it, so the absence is reported as such.
        "origin": job.origin,
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
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """One page of this user's learning jobs, with the total it was drawn from.

    ``total`` counts every row that matches the owner and the status filter, not
    the rows in this response — it used to be ``len(jobs)``, so 20 rows out of
    2,000 read back as "20 total" and the page looked like the whole history.
    ``created_at`` alone is not a stable sort key (a batch of rows can share it
    to the microsecond), so ``id`` breaks ties and the same request returns the
    same page twice.
    """
    conditions = [LearningJob.user_id == current_user.id]
    if status_filter:
        conditions.append(LearningJob.status == status_filter)

    total = db.scalar(select(func.count(LearningJob.id)).where(*conditions))

    jobs = list(
        db.scalars(
            select(LearningJob)
            .where(*conditions)
            .order_by(LearningJob.created_at.desc(), LearningJob.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return {
        "jobs": [_serialize_job(j) for j in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(jobs) < (total or 0),
    }


@router.post("/jobs/trigger", status_code=status.HTTP_201_CREATED)
def trigger_learning_job(
    payload: TriggerLearningRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Queue one learning request owned by the caller who sent it.

    The row this creates *is* the run record: the queue executor claims it, sets
    ``started_at``, and writes ``status``/``completed_at``/result onto the same
    id. There is no second row, and nothing here runs the work inline — an
    endpoint-spawned thread used to produce its own batch row while this one sat
    ``pending`` forever under the owner's name.

    ``Idempotency-Key`` (8-120 characters) makes a retry of the same request
    return the row the first one filed. Same key with different content is a
    409, not a rewrite: the second call is a different operation and silently
    re-labelling the first would make the retry mean something its author never
    asked for. Keys are scoped to this owner, and an omitted key means every
    call is a new operation — deliberate repeat runs stay possible.
    """
    from app.workers.learning_scheduler import (
        enqueue_pattern_analysis,
        enqueue_scheduled_research,
        learning_executor_status,
    )

    # `None` means "not specified" and takes the scheduled default grid. `[]`
    # means the caller asked for no instruments (or no grains), which used to be
    # silently rewritten into the full 3 x 4 workload — a bigger run than the
    # request, charged to the owner's compute.
    if payload.instruments is not None and not payload.instruments:
        raise HTTPException(
            status_code=400,
            detail="instruments must not be empty; omit the field to use the scheduled default set",
        )
    if payload.intervals is not None and not payload.intervals:
        raise HTTPException(
            status_code=400,
            detail="intervals must not be empty; omit the field to use the scheduled default set",
        )
    # Pattern analysis reads the owner's stored trades; the grid it reports on is
    # decided by that data, not by these fields. Accepting them would let a
    # request look like it chose a scope it never had.
    if payload.job_type == "pattern_analysis" and (
        payload.instruments is not None or payload.intervals is not None
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "instruments/intervals are not part of a pattern_analysis request; "
                "omit them, or use job_type=scheduled_study to choose a grid"
            ),
        )

    operation_key = None
    if idempotency_key is not None:
        key = idempotency_key.strip()
        if not USER_KEY_MIN <= len(key) <= USER_KEY_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Idempotency-Key must be {USER_KEY_MIN}-{USER_KEY_MAX} characters",
            )
        operation_key = f"{USER_KEY_PREFIX}{key}"

    # Admission first: a request nobody can claim is not a favour to the owner,
    # and this route has no deadline that would let it expire on its own.
    executor = learning_executor_status()
    if not executor["available"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "learning_executor_unavailable",
                "executor_status": executor["reason"],
                "remedy": executor.get(
                    "remedy",
                    "start the learning executor: set ENABLE_LEARNING_SCHEDULER=true "
                    "for the API process, or run `python -m app.workers.learning_scheduler`",
                ),
                "message": (
                    "學習工作目前不會被執行，所以這個要求沒有被排入佇列。"
                    "請先啟動學習執行器，或請管理員開啟排程服務。"
                ),
            },
        )

    try:
        if payload.job_type == "scheduled_study":
            job, replayed = enqueue_scheduled_research(
                db,
                user_id=current_user.id,
                scope="manual",
                instruments=payload.instruments,
                intervals=payload.intervals,
                origin="user",
                operation_key=operation_key,
            )
        else:
            job, replayed = enqueue_pattern_analysis(
                db,
                user_id=current_user.id,
                origin="user",
                operation_key=operation_key,
            )
    except IdempotencyConflict as exc:
        db.rollback()
        logger.info(
            "Learning trigger for user %s refused: %s", current_user.id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "idempotency_conflict",
                "message": (
                    "這個 Idempotency-Key 已經用於一個不同的學習要求。"
                    "換一個金鑰再送，或重送原本完全相同的要求。"
                ),
            },
        )

    if not replayed:
        emit_event(
            db,
            entity_type="learning_job",
            entity_id=str(job.id),
            event_type="learning_job_queued",
            actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            payload={
                "job_type": job.job_type,
                "origin": "user",
                "operation_key": operation_key,
                "config_json": job.config_json,
            },
        )
        db.commit()

    logger.info(
        "Learning job %s queued by user %s (type=%s, replayed=%s)",
        job.id,
        current_user.id,
        payload.job_type,
        replayed,
    )

    return {
        "job": _serialize_job(job),
        "replayed": replayed,
        "next_claim_at": executor.get("next_claim_at"),
        "message": (
            "Already queued — this is the request your key filed the first time."
            if replayed
            else "Queued. The learning executor claims it on its next tick and "
            "updates this same record with the result."
        ),
    }


@router.get("/proposals")
def list_proposals(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """One page of this user's proposals, with the total it was drawn from.

    ``total`` is every matching proposal, not this page: the caller decides
    whether what it can see is all there is, which ``limit`` alone cannot say.
    """
    conditions = [LearningProposal.user_id == current_user.id]
    if status_filter:
        conditions.append(LearningProposal.status == status_filter)

    total = db.scalar(select(func.count(LearningProposal.id)).where(*conditions))

    proposals = list(
        db.scalars(
            select(LearningProposal)
            .where(*conditions)
            .order_by(
                LearningProposal.created_at.desc(), LearningProposal.id.desc()
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return {
        "proposals": [_serialize_proposal(p) for p in proposals],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(proposals) < (total or 0),
    }


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Owner decision: approve a proposal that passed validation.

    This is the only rung that writes ``evidence_status="validated_candidate"``,
    which is the value ``deploy_proposal`` requires. Without it the ladder had
    no producer at all: pattern analysis emits only ``hypothesis`` /
    ``descriptive`` / ``insufficient``, the scheduler stores every proposal as
    ``status="pending"``, and deploy refused everything else — so nothing the
    product created could ever legally reach deploy.

    NEVER auto-approves. Always requires an explicit POST from the owner.
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

    validation_result = proposal.validation_result or {}
    if not validation_result.get("passed", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "validation_not_passed",
                "current_status": proposal.status,
                "validation_result": validation_result,
                "message": (
                    "Only a proposal whose validation_result.passed is true can be "
                    "approved. Run the backtest validation first."
                ),
            },
        )

    new_rationale = dict(proposal.rationale_json or {})
    evidence_status_before = new_rationale.get("evidence_status", "unknown")
    new_rationale["evidence_status"] = "validated_candidate"

    # The status predicate is what makes a second concurrent approve lose, so
    # an approval can never be counted twice; the whole rationale is reassigned
    # because a JSON column is not tracked on in-place mutation.
    approved = db.execute(
        update(LearningProposal)
        .where(
            LearningProposal.id == proposal_id,
            LearningProposal.user_id == current_user.id,
            LearningProposal.status == "pending",
        )
        .values(
            status="validated",
            validated_at=utcnow_aware(),
            rationale_json=new_rationale,
        )
    ).rowcount

    if approved != 1:
        db.rollback()
        # Re-read with the same owner predicate the write had. Dropping it to
        # "find the row by id" would report another tenant's status back to the
        # caller, which is the leak the write path was careful not to have.
        current = db.scalars(
            select(LearningProposal).where(
                LearningProposal.id == proposal_id,
                LearningProposal.user_id == current_user.id,
            )
        ).first()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "proposal_not_approvable",
                "current_status": current.status if current else None,
                "message": (
                    "Only proposals with status 'pending' can be approved. "
                    "An already validated, deployed, or rejected proposal keeps "
                    "its recorded decision instead of being re-approved."
                ),
            },
        )

    emit_event(
        db,
        entity_type="learning_proposal",
        entity_id=str(proposal_id),
        event_type="proposal_approved",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "rule_id": proposal.rule_id,
            "exit_id": proposal.exit_id,
            "instrument_id": proposal.instrument_id,
            "interval_minutes": proposal.interval_minutes,
            "status_before": "pending",
            "status_after": "validated",
            "evidence_status_before": evidence_status_before,
            "evidence_status_after": "validated_candidate",
        },
    )
    db.commit()
    db.refresh(proposal)

    logger.info(
        "Proposal %s approved by user %s: %s/%s %s@%dm",
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
            "Proposal approved. It is now deployable through "
            "POST /learning/proposals/{id}/deploy."
        ),
    }


@router.post("/proposals/{proposal_id}/deploy")
def deploy_proposal(
    proposal_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Owner decision: deploy a validated proposal.

    Records the decision on the proposal row (status, timestamp, audit event).
    The proposed exit config is kept for reference; nothing in the research or
    trading paths reads it yet, and the response says so rather than promising
    that the next run will use it.

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

    # Deploy is the last rung, so it is written the way the rungs above it are:
    # one conditional UPDATE whose predicate is the state this call requires. A
    # plain ORM assignment let two concurrent deploys both "succeed", and left no
    # audit trail at all of who decided to deploy what.
    deployed = db.execute(
        update(LearningProposal)
        .where(
            LearningProposal.id == proposal_id,
            LearningProposal.user_id == current_user.id,
            LearningProposal.status == "validated",
        )
        .values(status="deployed", deployed_at=utcnow_aware())
    ).rowcount

    if deployed != 1:
        db.rollback()
        current = db.scalars(
            select(LearningProposal).where(
                LearningProposal.id == proposal_id,
                LearningProposal.user_id == current_user.id,
            )
        ).first()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "proposal_not_deployable",
                "current_status": current.status if current else None,
                "message": (
                    "Only proposals with status 'validated' can be deployed. "
                    "A proposal that was already deployed, rejected, or reverted "
                    "keeps its recorded decision instead of being deployed again."
                ),
            },
        )

    emit_event(
        db,
        entity_type="learning_proposal",
        entity_id=str(proposal_id),
        event_type="proposal_deployed",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "status_before": "validated",
            "status_after": "deployed",
            "rule_id": proposal.rule_id,
            "exit_id": proposal.exit_id,
            "instrument_id": proposal.instrument_id,
            "interval_minutes": proposal.interval_minutes,
            "proposed_exit_config": proposal.proposed_exit_config,
            "evidence_status": evidence_status,
        },
    )
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
            "Proposal deployed: your decision is recorded and the proposal is "
            "marked deployed. No research, paper-trading or live flow reads the "
            "proposed exit config yet, so this changes nothing about how the "
            "next run is executed."
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
