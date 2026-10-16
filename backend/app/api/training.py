"""Module F: Training pipeline API router."""
import copy
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.training import TrainingJob, TrainingJobStatus
from app.auth.jwt import get_current_user
from app.auth.safety import safety_control
from app.models.kill_switch import KillSwitchScope
from app.services.training import (
    run_parameter_sweep,
    cancel_running_job,
    _validate_grid,
    _expand_grid,
    MAX_GRID_SIZE,
)
from app.lib.timeutil import utcnow

router = APIRouter(prefix="/training", tags=["training"])


# ── Schemas ────────────────────────────────────────────────────────────


class TrainingJobCreate(BaseModel):
    experiment_id: UUID
    parameter_grid: Dict[str, List[Any]]

    @field_validator("parameter_grid")
    @classmethod
    def validate_parameter_grid(cls, v):
        if not isinstance(v, dict):
            raise ValueError("parameter_grid must be a dict")
        errors = _validate_grid(v)
        if errors:
            raise ValueError("; ".join(errors))
        return v


class TrainingJobResponse(BaseModel):
    id: UUID
    user_id: UUID
    experiment_id: Optional[UUID] = None
    status: str
    parameter_grid: dict
    strategy_spec_snapshot: Optional[dict]
    results: Optional[List[dict]]
    best_params: Optional[dict]
    error_details: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    cancelled_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ── Helpers ────────────────────────────────────────────────────────────


def _resolve_strategy_spec(db: Session, experiment: Experiment) -> Dict[str, Any]:
    """Resolve the strategy_spec for an experiment.

    Looks at the experiment's current version first, then falls back to the
    scope field.  Raises ValueError if no strategy_spec can be found.
    """
    base_strategy_spec: Dict[str, Any] = {}
    if experiment.current_version_id:
        current_version = db.query(ExperimentVersion).filter(
            ExperimentVersion.id == experiment.current_version_id
        ).first()
        if current_version:
            base_strategy_spec = current_version.strategy_spec or {}

    if not base_strategy_spec:
        scope = experiment.scope or {}
        base_strategy_spec = scope.get("strategy_spec", {})

    if not base_strategy_spec:
        raise ValueError("Experiment has no strategy_spec in current version or scope")

    return base_strategy_spec


# ── Routes ─────────────────────────────────────────────────────────────


@router.post("/jobs", response_model=TrainingJobResponse, status_code=status.HTTP_201_CREATED)
def create_training_job(
    payload: TrainingJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(safety_control(KillSwitchScope.training, "training")),
):
    """Create a training job (parameter sweep).

    The job is created with status 'pending' and will be executed by a
    background worker. The API returns immediately with the job details.
    Use GET /training/jobs/{job_id} to poll for completion.
    
    Safety controls:
    - Kill switch: Blocks new training jobs if training/global switch is active
    - Rate limit: 10 training jobs per hour per user/API key
    """
    experiment = db.query(Experiment).filter(
        Experiment.id == payload.experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Resolve strategy_spec at creation time (immutable snapshot)
    try:
        base_strategy_spec = _resolve_strategy_spec(db, experiment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Expand grid and enforce size cap
    combinations = _expand_grid(payload.parameter_grid)
    if len(combinations) > MAX_GRID_SIZE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Parameter grid produces {len(combinations)} combinations, "
                f"which exceeds the maximum of {MAX_GRID_SIZE}"
            ),
        )

    job = TrainingJob(
        user_id=current_user.id,
        experiment_id=payload.experiment_id,
        parameter_grid=payload.parameter_grid,
        strategy_spec_snapshot=copy.deepcopy(base_strategy_spec),
        status=TrainingJobStatus.pending,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs/{job_id}", response_model=TrainingJobResponse)
def get_training_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific training job with results."""
    job = db.query(TrainingJob).filter(
        TrainingJob.id == job_id,
        TrainingJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    return job


@router.get("/jobs", response_model=List[TrainingJobResponse])
def list_training_jobs(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's training jobs."""
    query = db.query(TrainingJob).filter(TrainingJob.user_id == current_user.id)
    if status_filter:
        query = query.filter(TrainingJob.status == status_filter)
    return query.order_by(TrainingJob.created_at.desc()).all()


@router.post("/jobs/{job_id}/cancel", response_model=TrainingJobResponse)
def cancel_training_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a pending or running training job.

    - Pending jobs are cancelled immediately (status -> cancelled).
    - Running jobs have a cancellation flag set; the sweep loop will detect
      it between iterations and transition the status to cancelled.
    """
    job = db.query(TrainingJob).filter(
        TrainingJob.id == job_id,
        TrainingJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")

    if job.status == TrainingJobStatus.pending:
        job.status = TrainingJobStatus.cancelled
        job.cancelled_at = utcnow()
        job.completed_at = utcnow()
        db.commit()
        db.refresh(job)
        return job

    if job.status == TrainingJobStatus.running:
        # Set cancellation flag — the sweep loop checks this between
        # iterations and will transition the status to cancelled.
        cancel_running_job(job.id)
        job.cancelled_at = utcnow()
        db.commit()
        db.refresh(job)
        return job

    raise HTTPException(
        status_code=400,
        detail=(
            f"Cannot cancel job with status '{job.status.value}'. "
            "Only pending or running jobs can be cancelled."
        ),
    )
