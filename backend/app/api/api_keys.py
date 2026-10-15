from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User
from app.auth.api_keys import generate_api_key

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


# --- Schemas ---

class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class ApiKeyCreateResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    raw_key: str  # shown only once
    scopes: list
    is_active: bool
    expires_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApiKeyListItem(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list
    is_active: bool
    last_used_at: Optional[datetime]
    created_at: datetime
    expires_at: Optional[datetime]

    model_config = {"from_attributes": True}


# --- Endpoints ---

@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new API key. The raw key is returned only in this response."""
    key_prefix, raw_key = generate_api_key(
        db=db,
        user_id=current_user.id,
        name=body.name,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    # Fetch the just-created record to build the response
    api_key = (
        db.query(ApiKey)
        .filter(ApiKey.key_prefix == key_prefix, ApiKey.user_id == current_user.id)
        .first()
    )
    return ApiKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        raw_key=raw_key,
        scopes=api_key.scopes or [],
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
    )


@router.get("", response_model=list[ApiKeyListItem])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's API keys (never exposes the full key)."""
    keys = (
        db.query(ApiKey)
        .filter(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return keys


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke (deactivate) an API key. Only the owner can revoke their own keys."""
    api_key = (
        db.query(ApiKey)
        .filter(ApiKey.id == key_id, ApiKey.user_id == current_user.id)
        .first()
    )
    if api_key is None:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.is_active = False
    db.commit()
    return None
