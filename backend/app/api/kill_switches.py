"""Kill switch management API — emergency shutdown controls."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.kill_switch import KillSwitch, KillSwitchScope
from app.services.kill_switch import (
    activate_kill_switch,
    deactivate_kill_switch,
    get_kill_switch,
    list_kill_switches,
)
from app.auth.jwt import get_current_user

router = APIRouter(prefix="/kill-switches", tags=["kill-switches"])


# ── Schemas ────────────────────────────────────────────────────────────


class KillSwitchResponse(BaseModel):
    id: UUID
    scope: str
    is_active: bool
    activated_at: Optional[datetime]
    activated_by: Optional[str]
    reason: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KillSwitchActivate(BaseModel):
    scope: str
    reason: Optional[str] = None


class KillSwitchDeactivate(BaseModel):
    scope: str


# ── Helpers ────────────────────────────────────────────────────────────


def _validate_scope(scope_str: str) -> KillSwitchScope:
    """Validate scope string and return enum."""
    try:
        return KillSwitchScope(scope_str)
    except ValueError:
        valid_scopes = [s.value for s in KillSwitchScope]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scope '{scope_str}'. Valid scopes: {valid_scopes}",
        )


def _require_admin(current_user: User):
    """Require admin user for kill switch operations."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kill switch operations require admin privileges",
        )


# ── Routes ─────────────────────────────────────────────────────────────


@router.get("", response_model=List[KillSwitchResponse])
def list_all_kill_switches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all kill switches.
    
    Requires admin privileges.
    """
    _require_admin(current_user)
    return list_kill_switches(db)


@router.get("/{scope}", response_model=KillSwitchResponse)
def get_kill_switch_status(
    scope: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get kill switch status for a specific scope.
    
    Requires admin privileges.
    """
    _require_admin(current_user)
    scope_enum = _validate_scope(scope)
    switch = get_kill_switch(db, scope_enum)
    
    if not switch:
        raise HTTPException(
            status_code=404,
            detail=f"Kill switch for scope '{scope}' not found",
        )
    
    return switch


@router.post("/activate", response_model=KillSwitchResponse, status_code=status.HTTP_201_CREATED)
def activate(
    payload: KillSwitchActivate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activate kill switch for a scope.
    
    Requires admin privileges.
    
    When activated, blocks new submissions for the specified scope.
    Does NOT cancel already-submitted broker orders or running jobs.
    """
    _require_admin(current_user)
    scope_enum = _validate_scope(payload.scope)
    
    switch = activate_kill_switch(
        db,
        scope_enum,
        activated_by=str(current_user.id),
        reason=payload.reason,
    )
    
    return switch


@router.post("/deactivate", response_model=KillSwitchResponse)
def deactivate(
    payload: KillSwitchDeactivate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deactivate kill switch for a scope.
    
    Requires admin privileges.
    """
    _require_admin(current_user)
    scope_enum = _validate_scope(payload.scope)
    
    switch = deactivate_kill_switch(db, scope_enum)
    
    if not switch:
        raise HTTPException(
            status_code=404,
            detail=f"Kill switch for scope '{scope}' not found",
        )
    
    return switch
