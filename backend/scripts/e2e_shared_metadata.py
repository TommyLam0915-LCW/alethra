"""Write policy for the ``market_data_metadata`` row the E2E seeds share.

There is one row per ``(instrument_id, provider_id)``, and several writers point at
it: ``seed_e2e_rule_flow.py``, ``seed_e2e_order_lifecycle.py`` and the product's own
``app.services.market_data_storage``, which is what records ``health_status``
(``ok``/``degraded``), ``adjustment_status`` and the success/error clock.

A seed can honestly restate what it can recompute — the ``COUNT``/``MIN``/``MAX``
span over the bar rows that are actually in the database — so that is all it writes
on an existing row. Provenance and health belong to whoever measured them: silently
stamping ``health_status='ok'`` over a degraded row, or rewriting ``data_nature`` on
a row a real import created, would make a re-seed erase the quality signal the UI
then reads from ``api/instruments.py``.

The create path is different: when the seed is the first writer it owns every field,
including ``health_status``, because there is nothing to preserve.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select

from app.models.market_data import MarketDataBar, MarketDataMetadata

#: Columns a later seed must not touch on a row it did not create.
PRESERVED_ON_SHARED_ROW = (
    "data_nature",
    "adjustment_status",
    "timezone",
    "currency",
    "health_status",
    "last_success_at",
    "last_error_at",
    "consecutive_errors",
)

#: Columns this module writes on an existing row: recomputed span plus the note
#: describing what the stored bars currently cover.
AGGREGATE_FIELDS = ("first_bar_date", "last_bar_date", "total_bars")
NOTE_FIELDS = ("provider_note", "provider_note_zh")


def stored_span(session, instrument_id: str, provider_id: str) -> Tuple[int, Optional[Any], Optional[Any]]:
    """``(bar_count, first_bar_date, last_bar_date)`` over every stored bar."""
    total, first, last = session.execute(
        select(
            func.count(MarketDataBar.id),
            func.min(MarketDataBar.bar_date),
            func.max(MarketDataBar.bar_date),
        ).where(
            MarketDataBar.instrument_id == instrument_id,
            MarketDataBar.provider_id == provider_id,
        )
    ).one()
    return int(total), first, last


def upsert_shared_metadata(
    session,
    *,
    instrument_id: str,
    provider_id: str,
    note: str,
    note_zh: str,
    data_nature: str,
    timezone: str,
    currency: str,
    health_status: str = "ok",
    new_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Refresh the shared row's span, or create it when no writer got there first.

    Returns ``{"created", "total_bars", "first_bar_date", "last_bar_date",
    "preserved"}`` so a caller can print exactly what it did — and what it did not.
    """
    total, first, last = stored_span(session, instrument_id, provider_id)
    metadata = session.execute(
        select(MarketDataMetadata).where(
            MarketDataMetadata.instrument_id == instrument_id,
            MarketDataMetadata.provider_id == provider_id,
        )
    ).scalar_one_or_none()

    if metadata is None:
        row = MarketDataMetadata(
            instrument_id=instrument_id,
            provider_id=provider_id,
            data_nature=data_nature,
            timezone=timezone,
            currency=currency,
            provider_note=note,
            provider_note_zh=note_zh,
            first_bar_date=first,
            last_bar_date=last,
            total_bars=total,
            health_status=health_status,
        )
        if new_id is not None:
            row.id = new_id
        session.add(row)
        return {
            "created": True,
            "total_bars": total,
            "first_bar_date": first,
            "last_bar_date": last,
            "preserved": (),
        }

    metadata.first_bar_date = first
    metadata.last_bar_date = last
    metadata.total_bars = total
    metadata.provider_note = note
    metadata.provider_note_zh = note_zh
    session.flush()
    return {
        "created": False,
        "total_bars": int(metadata.total_bars),
        "first_bar_date": metadata.first_bar_date,
        "last_bar_date": metadata.last_bar_date,
        "preserved": tuple(
            (field, getattr(metadata, field)) for field in PRESERVED_ON_SHARED_ROW
        ),
    }
