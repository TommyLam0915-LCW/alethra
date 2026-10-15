"""Module E: Inference API router."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.inference import InferenceRun, InferenceRunStatus
from app.auth.jwt import get_current_user
from app.services.inference import run_inference

router = APIRouter(prefix="/inference", tags=["inference"])


# ── Schemas ────────────────────────────────────────────────────────────


class InferenceInputSpec(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    parameters: Optional[dict] = None


class InferenceRunCreate(BaseModel):
    model_version_id: UUID
    input_spec: InferenceInputSpec


class InferenceRunResponse(BaseModel):
    id: UUID
    model_version_id: UUID
    user_id: UUID
    input_spec: dict
    output_signals: Optional[List[dict]]
    status: str
    error_details: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ── Routes ─────────────────────────────────────────────────────────────


@router.post("/runs", response_model=InferenceRunResponse, status_code=status.HTTP_201_CREATED)
def create_inference_run(
    payload: InferenceRunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create and execute an inference run."""
    input_spec_dict = payload.input_spec.model_dump()
    run = run_inference(db, payload.model_version_id, current_user.id, input_spec_dict)
    db.commit()
    db.refresh(run)
    return run


@router.get("/runs/{run_id}", response_model=InferenceRunResponse)
def get_inference_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific inference run result."""
    run = db.query(InferenceRun).filter(
        InferenceRun.id == run_id,
        InferenceRun.user_id == current_user.id,
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail="Inference run not found")
    return run


@router.get("/runs", response_model=List[InferenceRunResponse])
def list_inference_runs(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's inference runs."""
    query = db.query(InferenceRun).filter(InferenceRun.user_id == current_user.id)
    if status_filter:
        query = query.filter(InferenceRun.status == status_filter)
    return query.order_by(InferenceRun.created_at.desc()).all()
