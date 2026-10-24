from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.saved_comparison import SavedComparison
from app.auth.jwt import get_current_user


router = APIRouter(tags=["saved-comparisons"])


def _non_blank_purpose(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("purpose must not be blank")
    return value


class SavedComparisonCreate(BaseModel):
    experiment_id: UUID
    run_a_id: UUID
    run_b_id: UUID
    purpose: str = Field(..., min_length=1, max_length=500)
    notes: Optional[str] = Field(None, max_length=5000)

    @field_validator("purpose")
    @classmethod
    def _purpose_not_blank(cls, v: str) -> str:
        return _non_blank_purpose(v)


class SavedComparisonUpdate(BaseModel):
    purpose: str = Field(..., min_length=1, max_length=500)
    notes: Optional[str] = Field(None, max_length=5000)

    @field_validator("purpose")
    @classmethod
    def _purpose_not_blank(cls, v: str) -> str:
        return _non_blank_purpose(v)


class SavedComparisonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    author_id: UUID
    experiment_id: UUID
    run_a_id: UUID
    run_b_id: UUID
    purpose: str
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime


def _get_owned_experiment(db: Session, experiment_id: UUID, user_id: UUID) -> Experiment:
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == user_id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found or not authorized")
    return experiment


def _assert_run_referenceable(db: Session, run_id: UUID, experiment_id: UUID, label: str) -> ResearchRun:
    """Confirm a run exists, belongs to this experiment, and is completed.

    Re-checked on read and update so a saved comparison never silently points
    at a run that has moved to another experiment or is no longer completed.
    Returns 404 (never 500) when the reference is unavailable.
    """
    run = db.query(ResearchRun).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).filter(
        ResearchRun.id == run_id,
        ExperimentVersion.experiment_id == experiment_id,
        ResearchRun.status == RunStatus.completed,
    ).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail=f"{label} not found, not authorized, or not completed",
        )
    return run


@router.post("/saved-comparisons", response_model=SavedComparisonResponse, status_code=status.HTTP_201_CREATED)
def create_saved_comparison(
    data: SavedComparisonCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Save a comparison with purpose and notes."""
    _get_owned_experiment(db, data.experiment_id, current_user.id)

    if data.run_a_id == data.run_b_id:
        raise HTTPException(status_code=400, detail="Cannot compare a run with itself")

    _assert_run_referenceable(db, data.run_a_id, data.experiment_id, "Run A")
    _assert_run_referenceable(db, data.run_b_id, data.experiment_id, "Run B")

    saved = SavedComparison(
        author_id=current_user.id,
        experiment_id=data.experiment_id,
        run_a_id=data.run_a_id,
        run_b_id=data.run_b_id,
        purpose=data.purpose,
        notes=data.notes,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    return saved


@router.get("/experiments/{experiment_id}/saved-comparisons", response_model=List[SavedComparisonResponse])
def list_saved_comparisons(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List saved comparisons authored by the current user for an experiment."""
    _get_owned_experiment(db, experiment_id, current_user.id)

    saved_comparisons = db.query(SavedComparison).filter(
        SavedComparison.experiment_id == experiment_id,
        SavedComparison.author_id == current_user.id,
    ).order_by(SavedComparison.updated_at.desc()).all()

    return saved_comparisons


@router.get("/saved-comparisons/{comparison_id}", response_model=SavedComparisonResponse)
def get_saved_comparison(
    comparison_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve a saved comparison after re-verifying all referenced resources."""
    saved = _get_owned_saved_comparison(db, comparison_id, current_user.id)
    _get_owned_experiment(db, saved.experiment_id, current_user.id)
    _assert_run_referenceable(db, saved.run_a_id, saved.experiment_id, "Run A")
    _assert_run_referenceable(db, saved.run_b_id, saved.experiment_id, "Run B")
    return saved


@router.patch("/saved-comparisons/{comparison_id}", response_model=SavedComparisonResponse)
def update_saved_comparison(
    comparison_id: UUID,
    data: SavedComparisonUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update purpose and notes. Run references and identity are immutable."""
    saved = _get_owned_saved_comparison(db, comparison_id, current_user.id)
    _get_owned_experiment(db, saved.experiment_id, current_user.id)
    _assert_run_referenceable(db, saved.run_a_id, saved.experiment_id, "Run A")
    _assert_run_referenceable(db, saved.run_b_id, saved.experiment_id, "Run B")

    saved.purpose = data.purpose
    # Omitting notes leaves the stored note unchanged; sending notes=null clears it.
    if "notes" in data.model_fields_set:
        saved.notes = data.notes
    saved.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(saved)

    return saved


def _get_owned_saved_comparison(db: Session, comparison_id: UUID, user_id: UUID) -> SavedComparison:
    saved = db.query(SavedComparison).filter(
        SavedComparison.id == comparison_id,
        SavedComparison.author_id == user_id,
    ).first()
    if not saved:
        raise HTTPException(status_code=404, detail="Saved comparison not found or not authorized")
    return saved
