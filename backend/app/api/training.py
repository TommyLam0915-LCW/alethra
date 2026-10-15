"""Module F: Training pipeline API router."""
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.training import TrainingJob, TrainingJobStatus
from app.auth.jwt import get_current_user
from app.services.training import run_parameter_sweep, _validate_grid

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
    experiment_id: UUID
    status: str
    parameter_grid: dict
    results: Optional[List[dict]]
    best_params: Optional[dict]
    error_details: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Routes ─────────────────────────────────────────────────────────────


@router.post("/jobs", response_model=TrainingJobResponse, status_code=status.HTTP_201_CREATED)
def create_training_job(
    payload: TrainingJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create and execute a training job (parameter sweep)."""
    experiment = db.query(Experiment).filter(
        Experiment.id == payload.experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    job = TrainingJob(
        user_id=current_user.id,
        experiment_id=payload.experiment_id,
        parameter_grid=payload.parameter_grid,
        status=TrainingJobStatus.pending,
    )
    db.add(job)
    db.flush()

    # Execute the sweep synchronously
    run_parameter_sweep(db, job.id)

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
    """Cancel a pending training job."""
    job = db.query(TrainingJob).filter(
        TrainingJob.id == job_id,
        TrainingJob.user_id == current_user.id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")

    if job.status != TrainingJobStatus.pending:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status '{job.status.value}'. Only pending jobs can be cancelled.",
        )

    job.status = TrainingJobStatus.cancelled
    job.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job
