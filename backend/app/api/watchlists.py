"""Watchlist API for the shared workspace shell.

Per-user ordered lists of instrument symbols ("US:AAPL", "HK:0700").
Item positions are 0-based and compacted on removal.
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.data.real_instruments import get_instrument_by_id
from app.database import get_db
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem

router = APIRouter(tags=["watchlists"])

MAX_NAME_LENGTH = 100


class WatchlistCreate(BaseModel):
    name: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        name = value.strip()
        if not name:
            raise ValueError("Watchlist name cannot be empty")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"Watchlist name must be at most {MAX_NAME_LENGTH} characters")
        return name


class WatchlistItemAdd(BaseModel):
    symbol: str


class WatchlistReorder(BaseModel):
    symbols: List[str]


def _serialize_item(item: WatchlistItem) -> dict:
    return {
        "symbol": item.symbol,
        "position": item.position,
        "added_at": item.added_at.isoformat() if item.added_at else None,
    }


def _serialize_watchlist(watchlist: Watchlist) -> dict:
    items = sorted(watchlist.items, key=lambda i: i.position)
    return {
        "id": str(watchlist.id),
        "name": watchlist.name,
        "created_at": watchlist.created_at.isoformat() if watchlist.created_at else None,
        "updated_at": watchlist.updated_at.isoformat() if watchlist.updated_at else None,
        "items": [_serialize_item(i) for i in items],
    }


def _get_owned_watchlist(db: Session, watchlist_id: UUID, user: User) -> Watchlist:
    watchlist = (
        db.query(Watchlist)
        .filter(Watchlist.id == watchlist_id, Watchlist.user_id == user.id)
        .first()
    )
    if not watchlist:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return watchlist


@router.get("/watchlists")
def list_watchlists(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's watchlists, ordered by creation time.

    Items within each watchlist are ordered by position.
    """
    watchlists = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == current_user.id)
        .order_by(Watchlist.created_at.asc(), Watchlist.id.asc())
        .all()
    )
    return {"watchlists": [_serialize_watchlist(w) for w in watchlists]}


@router.post("/watchlists", status_code=status.HTTP_201_CREATED)
def create_watchlist(
    data: Optional[WatchlistCreate] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a watchlist. `name` defaults to "Default" (max 100 chars)."""
    name = data.name if (data and data.name) else "Default"

    existing = (
        db.query(Watchlist)
        .filter(Watchlist.user_id == current_user.id, Watchlist.name == name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Watchlist name already exists")

    watchlist = Watchlist(user_id=current_user.id, name=name)
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    return {"watchlist": _serialize_watchlist(watchlist)}


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(
    watchlist_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a watchlist and (cascade) its items."""
    watchlist = _get_owned_watchlist(db, watchlist_id, current_user)
    db.query(WatchlistItem).filter(WatchlistItem.watchlist_id == watchlist.id).delete()
    db.delete(watchlist)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/watchlists/{watchlist_id}/items")
def add_watchlist_item(
    watchlist_id: UUID,
    data: WatchlistItemAdd,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add an instrument to a watchlist (idempotent).

    Appends at the next position. Adding a symbol already present
    returns the current items unchanged with 200.
    """
    watchlist = _get_owned_watchlist(db, watchlist_id, current_user)

    if get_instrument_by_id(data.symbol) is None:
        raise HTTPException(status_code=404, detail=f"Unknown instrument: {data.symbol}")

    existing = (
        db.query(WatchlistItem)
        .filter(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.symbol == data.symbol,
        )
        .first()
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        return {"watchlist": _serialize_watchlist(watchlist)}

    next_position = (
        db.query(func.max(WatchlistItem.position))
        .filter(WatchlistItem.watchlist_id == watchlist.id)
        .scalar()
    )
    position = (next_position + 1) if next_position is not None else 0

    db.add(
        WatchlistItem(
            watchlist_id=watchlist.id,
            symbol=data.symbol,
            position=position,
        )
    )
    db.commit()
    db.refresh(watchlist)
    response.status_code = status.HTTP_201_CREATED
    return {"watchlist": _serialize_watchlist(watchlist)}


@router.delete("/watchlists/{watchlist_id}/items/{symbol}")
def remove_watchlist_item(
    watchlist_id: UUID,
    symbol: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a symbol from a watchlist; remaining positions compact to 0..n-1."""
    watchlist = _get_owned_watchlist(db, watchlist_id, current_user)

    item = (
        db.query(WatchlistItem)
        .filter(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.symbol == symbol,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Symbol not in watchlist")

    db.delete(item)
    db.flush()
    _compact_positions(db, watchlist)
    db.commit()
    db.refresh(watchlist)
    return {"watchlist": _serialize_watchlist(watchlist)}


@router.post("/watchlists/{watchlist_id}/reorder")
def reorder_watchlist(
    watchlist_id: UUID,
    data: WatchlistReorder,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the item order to exactly `symbols` (must match current items)."""
    watchlist = _get_owned_watchlist(db, watchlist_id, current_user)

    current_symbols = [i.symbol for i in watchlist.items]
    if (
        len(data.symbols) != len(current_symbols)
        or len(set(data.symbols)) != len(data.symbols)
        or set(data.symbols) != set(current_symbols)
    ):
        raise HTTPException(
            status_code=400,
            detail="Symbols must exactly match the current watchlist items",
        )

    items_by_symbol = {i.symbol: i for i in watchlist.items}
    for position, symbol in enumerate(data.symbols):
        items_by_symbol[symbol].position = position
    db.commit()
    db.refresh(watchlist)
    return {"watchlist": _serialize_watchlist(watchlist)}


def _compact_positions(db: Session, watchlist: Watchlist) -> None:
    """Renumber remaining items 0..n-1 in current position order."""
    remaining = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.watchlist_id == watchlist.id)
        .order_by(WatchlistItem.position.asc())
        .all()
    )
    for position, item in enumerate(remaining):
        item.position = position
