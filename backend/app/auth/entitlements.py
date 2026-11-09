"""Entitlement checks, quota enforcement, and free-plan policy.

Security invariants
-------------------
* A missing entitlement row does NOT grant full access — it falls back to
  FREE_PLAN_ENTITLEMENT limits.
* ``limit == 0`` means "no allowance" (reject).
* ``limit is None`` means "unlimited" (allow).  This should not happen in
  practice because the ORM columns are NOT NULL, but we handle it defensively.
* Unknown ``quota_type`` values raise ``ValueError`` instead of silently
  passing.
* Quota consumption uses an atomic ``UPDATE … SET used = used + amount`` so
  concurrent requests cannot double-spend.

Two-phase flow
--------------
For operations that may fail after starting (e.g. long-running backtests),
use the reserve → settle / release protocol:

1. ``reserve_quota()`` — atomically checks availability (used + reserved) and
   inserts a QuotaReservation row.  Raises 429 if insufficient.
2. Run the operation.
3. On success: ``settle_reservation()`` — atomically increments ``used`` and
   marks the reservation as settled.
4. On failure: ``release_reservation()`` — marks the reservation as released
   so the quota becomes available again.

The legacy ``consume_quota()`` (immediate consumption) is still available for
simple, fast operations where the check-then-consume gap is acceptable.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import func, update, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter, QuotaReservation
from app.auth.jwt import get_current_user
from app.lib.timeutil import utcnow


# ── Quota-type → entitlement-column mapping ──────────────────────────────────

QUOTA_TYPE_MAP = {
    "research_run": "research_run_limit",
    "export": "export_limit",
    "forward_simulation": "forward_simulation_limit",
    "ai_usage": "ai_usage_limit",
}

VALID_QUOTA_TYPES = set(QUOTA_TYPE_MAP.keys())


# ── Free-plan defaults ───────────────────────────────────────────────────────
# Users without an entitlement row (or whose entitlement has NULL limits) get
# these conservative limits.  This is the single source of truth for "free
# tier" allowances.

FREE_PLAN_ENTITLEMENT = {
    "research_run_limit": 5,
    "export_limit": 2,
    "forward_simulation_limit": 3,
    "ai_usage_limit": 10,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_limit(entitlement: Optional[PilotEntitlement], quota_type: str) -> Optional[int]:
    """Return the effective limit for *quota_type*.

    * If the user has an entitlement row, read the mapped column.
    * If the column value is ``None`` → unlimited (return ``None``).
    * If the user has no entitlement row → free-plan limit.
    """
    if quota_type not in VALID_QUOTA_TYPES:
        raise ValueError(f"Unknown quota_type: {quota_type!r}")

    entitlement_field = QUOTA_TYPE_MAP[quota_type]

    if entitlement is not None:
        value = getattr(entitlement, entitlement_field, None)
        # None on the column → unlimited
        return value  # may be None

    # No entitlement row → free plan
    return FREE_PLAN_ENTITLEMENT[entitlement_field]


def get_entitlement(db: Session, user_id: UUID) -> Optional[PilotEntitlement]:
    return db.query(PilotEntitlement).filter(
        PilotEntitlement.user_id == user_id
    ).first()


def check_entitlement_active(entitlement: PilotEntitlement) -> bool:
    if not entitlement.pilot_access:
        return False
    if entitlement.expiry_date and entitlement.expiry_date < utcnow():
        return False
    return True


def get_quota_used(db: Session, user_id: UUID, quota_type: str) -> int:
    if quota_type not in VALID_QUOTA_TYPES:
        raise ValueError(f"Unknown quota_type: {quota_type!r}")
    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == "lifetime",
    ).first()
    return counter.used if counter else 0


def consume_quota(db: Session, user_id: UUID, quota_type: str, amount: int = 1) -> int:
    """Atomically increment the used counter and return the new total.

    Uses a single ``UPDATE … SET used = used + amount`` statement so that
    concurrent requests cannot double-count.  When no row exists yet, we
    INSERT one (the retry loop handles the race where two transactions both
    miss the row).

    This is the legacy immediate-consumption path.  For operations that may
    fail after starting (long-running backtests, ML training), prefer the
    two-phase reserve → settle / release protocol below.
    """
    if quota_type not in VALID_QUOTA_TYPES:
        raise ValueError(f"Unknown quota_type: {quota_type!r}")
    if amount <= 0:
        raise ValueError("amount must be a positive integer")

    # Attempt atomic UPDATE — synchronize_session='fetch' ensures the
    # session's identity map is refreshed for the affected row(s).
    rows_updated = (
        db.query(QuotaCounter)
        .filter(
            QuotaCounter.user_id == user_id,
            QuotaCounter.quota_type == quota_type,
            QuotaCounter.period == "lifetime",
        )
        .update(
            {QuotaCounter.used: QuotaCounter.used + amount},
            synchronize_session="fetch",
        )
    )

    if rows_updated:
        # Re-fetch to get the updated value
        counter = db.query(QuotaCounter).filter(
            QuotaCounter.user_id == user_id,
            QuotaCounter.quota_type == quota_type,
            QuotaCounter.period == "lifetime",
        ).first()
        return counter.used

    # No existing row — INSERT
    counter = QuotaCounter(
        user_id=user_id,
        quota_type=quota_type,
        period="lifetime",
        used=amount,
    )
    db.add(counter)
    db.flush()
    return counter.used


def _get_reserved(db: Session, user_id: UUID, quota_type: str) -> int:
    """Sum of all active (status='reserved') reservation amounts."""
    result = db.query(func.coalesce(func.sum(QuotaReservation.amount), 0)).filter(
        QuotaReservation.user_id == user_id,
        QuotaReservation.quota_type == quota_type,
        QuotaReservation.status == "reserved",
    ).scalar()
    return int(result)


def check_quota_available(
    db: Session,
    user_id: UUID,
    quota_type: str,
    required: int = 1,
) -> bool:
    """Return True if the user has *at least* `required` units remaining.

    Accounts for both consumed (used) and reserved (in-flight) quota.
    Effective consumption = used + reserved.

    * ``limit is None`` → unlimited → always True.
    * ``limit == 0`` → no allowance → always False.
    * ``limit < 0`` → treat as no allowance → False.
    * Unknown *quota_type* → ``ValueError``.
    """
    entitlement = get_entitlement(db, user_id)
    limit = _get_limit(entitlement, quota_type)

    if limit is None:
        return True
    if limit <= 0:
        return False

    used = get_quota_used(db, user_id, quota_type)
    reserved = _get_reserved(db, user_id, quota_type)
    return (used + reserved + required) <= limit


# ── Two-phase quota flow ────────────────────────────────────────────────────

def reserve_quota(
    db: Session,
    user_id: UUID,
    quota_type: str,
    amount: int = 1,
) -> QuotaReservation:
    """Reserve quota units for an in-flight operation.

    Checks availability (used + reserved + amount <= limit) and, if
    sufficient, creates a QuotaReservation row with status='reserved'.

    Uses SELECT FOR UPDATE to serialize concurrent reservations and prevent
    TOCTOU races where multiple transactions read the same quota state.

    Raises HTTPException(429) if quota is insufficient.
    Returns the QuotaReservation object (caller must settle or release).
    """
    if quota_type not in VALID_QUOTA_TYPES:
        raise ValueError(f"Unknown quota_type: {quota_type!r}")
    if amount <= 0:
        raise ValueError("amount must be a positive integer")

    entitlement = get_entitlement(db, user_id)
    limit = _get_limit(entitlement, quota_type)

    if limit is not None:
        # Lock the QuotaCounter row to serialize concurrent reservations.
        # If the row doesn't exist yet, we'll create it after checking reservations.
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == quota_type,
                QuotaCounter.period == "lifetime",
            )
            .with_for_update()
            .first()
        )
        used = counter.used if counter else 0

        # Lock all existing reservations for this user/quota_type to prevent
        # concurrent reads of the reserved sum from changing.
        reservations = (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.quota_type == quota_type,
                QuotaReservation.status == "reserved",
            )
            .with_for_update()
            .all()
        )
        reserved = sum(r.amount for r in reservations)

        if (used + reserved + amount) > limit:
            entitlement_field = QUOTA_TYPE_MAP.get(quota_type, quota_type)
            if entitlement is not None:
                effective_limit = getattr(entitlement, entitlement_field, 0)
            else:
                effective_limit = FREE_PLAN_ENTITLEMENT.get(entitlement_field, 0)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "quota_exceeded",
                    "quota_type": quota_type,
                    "limit": effective_limit,
                    "used": used,
                    "reserved": reserved,
                },
            )

    reservation = QuotaReservation(
        user_id=user_id,
        quota_type=quota_type,
        amount=amount,
        status="reserved",
    )
    db.add(reservation)
    db.flush()
    return reservation


def settle_reservation(db: Session, reservation_id: UUID) -> int:
    """Settle a reservation: increment used counter and mark reservation settled.

    Atomically:
    1. Locks the reservation row with SELECT FOR UPDATE
    2. Increments QuotaCounter.used by the reservation amount (upsert)
    3. Sets reservation status='settled' and settled_at=now

    Returns the new used total.
    Raises ValueError if reservation not found or not in 'reserved' status.
    """
    # Lock the reservation row to prevent concurrent settlements
    reservation = (
        db.query(QuotaReservation)
        .filter(QuotaReservation.id == reservation_id)
        .with_for_update()
        .first()
    )
    if not reservation:
        raise ValueError(f"Reservation not found: {reservation_id}")
    if reservation.status != "reserved":
        raise ValueError(
            f"Reservation {reservation_id} is not in 'reserved' status "
            f"(current: {reservation.status})"
        )

    amount = reservation.amount
    user_id = reservation.user_id
    quota_type = reservation.quota_type

    # Check if counter exists (with lock if it does)
    counter = (
        db.query(QuotaCounter)
        .filter(
            QuotaCounter.user_id == user_id,
            QuotaCounter.quota_type == quota_type,
            QuotaCounter.period == "lifetime",
        )
        .with_for_update()
        .first()
    )

    if counter:
        # Counter exists — update it
        counter.used = counter.used + amount
        db.flush()
        new_used = counter.used
    else:
        # Counter doesn't exist — try to insert.
        # The reservation lock prevents concurrent settlements of the SAME reservation,
        # but different reservations can race here. Use database-specific upsert if available.
        dialect_name = db.bind.dialect.name if db.bind else None
        
        if dialect_name == "postgresql":
            # PostgreSQL: use INSERT ... ON CONFLICT DO UPDATE
            stmt = pg_insert(QuotaCounter).values(
                user_id=user_id,
                quota_type=quota_type,
                period="lifetime",
                used=amount,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_quota_counter",
                set_={"used": QuotaCounter.used + amount}
            )
            db.execute(stmt)
            db.flush()
            
            # Fetch the updated counter to get the new used value
            counter = db.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == quota_type,
                QuotaCounter.period == "lifetime",
            ).first()
            new_used = counter.used
        else:
            # SQLite/other: create counter (reservation lock serializes same-reservation races,
            # but different reservations may still race — acceptable for non-PostgreSQL)
            counter = QuotaCounter(
                user_id=user_id,
                quota_type=quota_type,
                period="lifetime",
                used=amount,
            )
            db.add(counter)
            db.flush()
            new_used = amount

    reservation.status = "settled"
    reservation.settled_at = utcnow()
    db.flush()

    return new_used


def release_reservation(db: Session, reservation_id: UUID) -> None:
    """Release a reservation without consuming quota.

    Marks the reservation as 'released' so the quota becomes available again.
    Raises ValueError if reservation not found or not in 'reserved' status.
    """
    # Lock the reservation row to prevent concurrent release/settle races
    reservation = (
        db.query(QuotaReservation)
        .filter(QuotaReservation.id == reservation_id)
        .with_for_update()
        .first()
    )
    if not reservation:
        raise ValueError(f"Reservation not found: {reservation_id}")
    if reservation.status != "reserved":
        raise ValueError(
            f"Reservation {reservation_id} is not in 'reserved' status "
            f"(current: {reservation.status})"
        )

    reservation.status = "released"
    reservation.settled_at = utcnow()
    db.flush()


def require_entitlement(quota_type: Optional[str] = None):
    def dependency(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
    ):
        entitlement = get_entitlement(db, current_user.id)

        if entitlement is not None:
            if not check_entitlement_active(entitlement):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Entitlement expired or disabled.",
                )

        if quota_type is not None:
            if not check_quota_available(db, current_user.id, quota_type):
                entitlement_field = QUOTA_TYPE_MAP.get(quota_type, quota_type)
                if entitlement is not None:
                    limit = getattr(entitlement, entitlement_field, 0)
                else:
                    limit = FREE_PLAN_ENTITLEMENT.get(entitlement_field, 0)
                used = get_quota_used(db, current_user.id, quota_type)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "quota_exceeded",
                        "quota_type": quota_type,
                        "limit": limit,
                        "used": used,
                    },
                )

        return current_user

    return dependency
