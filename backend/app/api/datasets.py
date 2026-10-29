"""Dataset management API endpoints."""
import os
from datetime import datetime, date
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.user import User
from app.models.dataset import Dataset, DatasetSnapshot, SourceType
from app.auth.jwt import get_current_user, require_admin
from app.services import emit_event

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _is_server_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized.startswith("/")
        or normalized.startswith("~")
        or (len(normalized) > 1 and normalized[1] == ":")
        or ".." in normalized.split("/")
    )


def _validate_snapshot_path(path: str) -> str:
    """Catalog snapshots reference relative paths only.

    The client must not be able to point the catalog at arbitrary server
    files, so absolute paths, home-relative paths, drive letters and parent
    traversal are rejected before anything is stored.
    """
    candidate = path.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="data_file_path must not be empty")
    if candidate.startswith("/") or candidate.startswith("~"):
        raise HTTPException(
            status_code=400, detail="data_file_path must be a relative path"
        )
    normalized = candidate.replace("\\", "/")
    if len(normalized) > 1 and normalized[1] == ":":
        raise HTTPException(
            status_code=400, detail="data_file_path must not include a drive letter"
        )
    if ".." in normalized.split("/"):
        raise HTTPException(
            status_code=400,
            detail="data_file_path must not traverse parent directories",
        )
    return candidate


def _visible_data_file_path(path: str, viewer: User) -> str:
    """Ordinary users never receive internal server paths."""
    if viewer.is_admin or not _is_server_path(path):
        return path
    return os.path.basename(path.replace("\\", "/").rstrip("/")) or "restricted"


class DatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(default="demo", pattern="^(demo|licensed|user_uploaded)$")
    license_status: Optional[str] = None
    description: Optional[str] = None


class DatasetSnapshotCreate(BaseModel):
    snapshot_date: date
    data_file_path: str = Field(..., min_length=1, max_length=500)
    checksum: Optional[str] = Field(None, max_length=64)
    corporate_actions_adjusted: bool = False
    missing_data_notes: Optional[str] = None


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    provider: str
    source_type: str
    license_status: Optional[str]
    description: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class DatasetSnapshotResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    snapshot_date: date
    data_file_path: str
    checksum: Optional[str]
    corporate_actions_adjusted: bool
    missing_data_notes: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class DatasetWithSnapshotsResponse(DatasetResponse):
    snapshots: List[DatasetSnapshotResponse] = []


@router.post("", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
def create_dataset(
    body: DatasetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Create a dataset in the managed shared catalog (admin only)."""
    if body.source_type == SourceType.user_uploaded.value:
        raise HTTPException(
            status_code=400,
            detail="user_uploaded datasets are not supported",
        )

    dataset = Dataset(
        name=body.name,
        provider=body.provider,
        source_type=SourceType(body.source_type),
        license_status=body.license_status,
        description=body.description,
    )
    db.add(dataset)
    db.flush()
    
    emit_event(
        db,
        entity_type="dataset",
        entity_id=str(dataset.id),
        event_type="dataset_created",
        actor_id=current_user.id,
        actor_type="human",
        payload={
            "name": body.name,
            "provider": body.provider,
            "source_type": body.source_type,
        },
    )
    
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("", response_model=List[DatasetResponse])
def list_datasets(
    provider: Optional[str] = None,
    source_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all datasets with optional filters."""
    query = db.query(Dataset)
    
    if provider:
        query = query.filter(Dataset.provider == provider)
    if source_type:
        query = query.filter(Dataset.source_type == SourceType(source_type))
    
    return query.order_by(Dataset.created_at.desc()).all()


@router.get("/{dataset_id}", response_model=DatasetWithSnapshotsResponse)
def get_dataset(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a dataset with all its snapshots."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    return DatasetWithSnapshotsResponse(
        id=dataset.id,
        name=dataset.name,
        provider=dataset.provider,
        source_type=dataset.source_type,
        license_status=dataset.license_status,
        description=dataset.description,
        created_at=dataset.created_at,
        snapshots=[
            DatasetSnapshotResponse(
                id=snapshot.id,
                dataset_id=snapshot.dataset_id,
                snapshot_date=snapshot.snapshot_date,
                data_file_path=_visible_data_file_path(
                    snapshot.data_file_path, current_user
                ),
                checksum=snapshot.checksum,
                corporate_actions_adjusted=bool(snapshot.corporate_actions_adjusted),
                missing_data_notes=snapshot.missing_data_notes,
                created_at=snapshot.created_at,
            )
            for snapshot in dataset.snapshots
        ],
    )


@router.post("/{dataset_id}/snapshots", response_model=DatasetSnapshotResponse, status_code=status.HTTP_201_CREATED)
def create_dataset_snapshot(
    dataset_id: UUID,
    body: DatasetSnapshotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Create a new snapshot for a dataset (admin only)."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    data_file_path = _validate_snapshot_path(body.data_file_path)

    snapshot = DatasetSnapshot(
        dataset_id=dataset_id,
        snapshot_date=body.snapshot_date,
        data_file_path=data_file_path,
        checksum=body.checksum,
        corporate_actions_adjusted=body.corporate_actions_adjusted,
        missing_data_notes=body.missing_data_notes,
    )
    db.add(snapshot)
    db.flush()

    emit_event(
        db,
        entity_type="dataset",
        entity_id=str(dataset_id),
        event_type="dataset_snapshot_created",
        actor_id=current_user.id,
        actor_type="human",
        payload={
            "snapshot_date": body.snapshot_date.isoformat(),
            "data_file_path": data_file_path,
        },
    )
    
    db.commit()
    db.refresh(snapshot)
    return snapshot
