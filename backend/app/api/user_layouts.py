"""Per-user UI layout persistence for the workspace shell.

GET/PUT /layouts/workspace — layout is a JSON object (dict) per user,
capped at 65536 serialized bytes.
"""
import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.lib.timeutil import utcnow
from app.models.user import User
from app.models.user_layout import UserLayout

router = APIRouter(tags=["user_layouts"])

WORKSPACE_LAYOUT_KEY = "workspace"
WORKSPACE_PREFERENCES_KEY = "workspace_preferences"
MAX_LAYOUT_BYTES = 65536


class LayoutUpdate(BaseModel):
    layout: Dict[str, Any]


def _get_layout_row(db: Session, user: User) -> UserLayout:
    return (
        db.query(UserLayout)
        .filter(
            UserLayout.user_id == user.id,
            UserLayout.layout_key == WORKSPACE_LAYOUT_KEY,
        )
        .first()
    )


@router.get("/layouts/workspace")
def get_workspace_layout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch the saved workspace layout (null when never saved)."""
    row = _get_layout_row(db, current_user)
    if row is None:
        return {"layout": None, "updated_at": None}
    return {
        "layout": row.layout_data,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/layouts/workspace")
def put_workspace_layout(
    data: LayoutUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert the workspace layout (JSON object, <= 65536 serialized bytes)."""
    serialized = json.dumps(data.layout)
    if len(serialized.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise HTTPException(status_code=413, detail="Layout too large")

    row = _get_layout_row(db, current_user)
    if row is None:
        row = UserLayout(
            user_id=current_user.id,
            layout_key=WORKSPACE_LAYOUT_KEY,
            layout_data=data.layout,
        )
        db.add(row)
    else:
        row.layout_data = data.layout
        row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return {
        "layout": row.layout_data,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/layouts/workspace_preferences")
def get_workspace_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch the saved workspace preferences (null when never saved)."""
    row = (
        db.query(UserLayout)
        .filter(
            UserLayout.user_id == current_user.id,
            UserLayout.layout_key == WORKSPACE_PREFERENCES_KEY,
        )
        .first()
    )
    if row is None:
        return {"preferences": None, "updated_at": None}
    return {
        "preferences": row.layout_data,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.put("/layouts/workspace_preferences")
def put_workspace_preferences(
    data: LayoutUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upsert the workspace preferences (JSON object, <= 65536 serialized bytes)."""
    serialized = json.dumps(data.layout)
    if len(serialized.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise HTTPException(status_code=413, detail="Preferences too large")

    row = (
        db.query(UserLayout)
        .filter(
            UserLayout.user_id == current_user.id,
            UserLayout.layout_key == WORKSPACE_PREFERENCES_KEY,
        )
        .first()
    )
    if row is None:
        row = UserLayout(
            user_id=current_user.id,
            layout_key=WORKSPACE_PREFERENCES_KEY,
            layout_data=data.layout,
        )
        db.add(row)
    else:
        row.layout_data = data.layout
        row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return {
        "preferences": row.layout_data,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
