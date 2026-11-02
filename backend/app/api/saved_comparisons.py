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
    # Optional for cross-experiment comparisons: when omitted, the anchor is
    # run A's experiment. When supplied, it must be one of the two runs'
    # experiments so a saved row is listed under a real participant.
    experiment_id: Optional[UUID] = None
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
    # The anchor experiment this comparison is listed under. For a
    # cross-experiment comparison it equals one of the two side experiments.
    experiment_id: UUID
    run_a_id: UUID
    run_b_id: UUID
    run_a_experiment_id: UUID
    run_b_experiment_id: UUID
    run_a_experiment_version_id: UUID
    run_b_experiment_version_id: UUID
    scope: str  # "same_experiment" | "cross_experiment"
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


def _assert_run_referenceable(
    db: Session, run_id: UUID, owner_id: UUID, label: str
) -> tuple:
    """Confirm a run exists, belongs to this owner, and is completed.

    Ownership is checked per run (run → version → experiment → owner), not by a
    single enclosing experiment, so an owner may compare two of their own
    experiments without exposing anything about another account. Every failure
    is the same 404: a crafted reference cannot reveal whether another user's
    run exists.
    """
    row = db.query(ResearchRun, ExperimentVersion, Experiment).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).join(
        Experiment, ExperimentVersion.experiment_id == Experiment.id
    ).filter(
        ResearchRun.id == run_id,
        Experiment.owner_id == owner_id,
        ResearchRun.status == RunStatus.completed,
    ).first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"{label} not found, not authorized, or not completed",
        )
    return row


def _saved_response(
    saved: SavedComparison,
    run_a_row: tuple,
    run_b_row: tuple,
) -> SavedComparisonResponse:
    _, version_a, experiment_a = run_a_row
    _, version_b, experiment_b = run_b_row
    return SavedComparisonResponse(
        id=saved.id,
        author_id=saved.author_id,
        experiment_id=saved.experiment_id,
        run_a_id=saved.run_a_id,
        run_b_id=saved.run_b_id,
        run_a_experiment_id=experiment_a.id,
        run_b_experiment_id=experiment_b.id,
        run_a_experiment_version_id=version_a.id,
        run_b_experiment_version_id=version_b.id,
        scope=(
            "same_experiment"
            if experiment_a.id == experiment_b.id
            else "cross_experiment"
        ),
        purpose=saved.purpose,
        notes=saved.notes,
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


@router.post("/saved-comparisons", response_model=SavedComparisonResponse, status_code=status.HTTP_201_CREATED)
def create_saved_comparison(
    data: SavedComparisonCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Save a same-experiment or cross-experiment comparison with a purpose."""
    if data.run_a_id == data.run_b_id:
        raise HTTPException(status_code=400, detail="Cannot compare a run with itself")

    run_a_row = _assert_run_referenceable(db, data.run_a_id, current_user.id, "Run A")
    run_b_row = _assert_run_referenceable(db, data.run_b_id, current_user.id, "Run B")
    experiment_a = run_a_row[2]
    experiment_b = run_b_row[2]

    if data.experiment_id is not None:
        anchor = _get_owned_experiment(db, data.experiment_id, current_user.id)
        if anchor.id not in {experiment_a.id, experiment_b.id}:
            raise HTTPException(
                status_code=400,
                detail=(
                    "experiment_id must be the experiment of one of the compared "
                    "runs; it is the list anchor, not a third experiment."
                ),
            )
    else:
        anchor = experiment_a

    saved = SavedComparison(
        author_id=current_user.id,
        experiment_id=anchor.id,
        run_a_id=data.run_a_id,
        run_b_id=data.run_b_id,
        purpose=data.purpose,
        notes=data.notes,
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    return _saved_response(saved, run_a_row, run_b_row)


@router.get("/experiments/{experiment_id}/saved-comparisons", response_model=List[SavedComparisonResponse])
def list_saved_comparisons(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List saved comparisons authored by the current user for an experiment.

    The experiment is the anchor; a cross-experiment comparison appears under
    whichever participant experiment it was saved under.
    """
    _get_owned_experiment(db, experiment_id, current_user.id)

    saved_comparisons = db.query(SavedComparison).filter(
        SavedComparison.experiment_id == experiment_id,
        SavedComparison.author_id == current_user.id,
    ).order_by(SavedComparison.updated_at.desc()).all()

    responses = []
    for saved in saved_comparisons:
        run_a_row = _assert_run_referenceable(db, saved.run_a_id, current_user.id, "Run A")
        run_b_row = _assert_run_referenceable(db, saved.run_b_id, current_user.id, "Run B")
        responses.append(_saved_response(saved, run_a_row, run_b_row))
    return responses


@router.get("/saved-comparisons/{comparison_id}", response_model=SavedComparisonResponse)
def get_saved_comparison(
    comparison_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve a saved comparison after re-verifying all referenced resources."""
    saved = _get_owned_saved_comparison(db, comparison_id, current_user.id)
    _get_owned_experiment(db, saved.experiment_id, current_user.id)
    run_a_row = _assert_run_referenceable(db, saved.run_a_id, current_user.id, "Run A")
    run_b_row = _assert_run_referenceable(db, saved.run_b_id, current_user.id, "Run B")
    return _saved_response(saved, run_a_row, run_b_row)


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
    run_a_row = _assert_run_referenceable(db, saved.run_a_id, current_user.id, "Run A")
    run_b_row = _assert_run_referenceable(db, saved.run_b_id, current_user.id, "Run B")

    saved.purpose = data.purpose
    # Omitting notes leaves the stored note unchanged; sending notes=null clears it.
    if "notes" in data.model_fields_set:
        saved.notes = data.notes
    saved.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(saved)

    return _saved_response(saved, run_a_row, run_b_row)


def _get_owned_saved_comparison(db: Session, comparison_id: UUID, user_id: UUID) -> SavedComparison:
    saved = db.query(SavedComparison).filter(
        SavedComparison.id == comparison_id,
        SavedComparison.author_id == user_id,
    ).first()
    if not saved:
        raise HTTPException(status_code=404, detail="Saved comparison not found or not authorized")
    return saved
