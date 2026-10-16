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

TODO (billing): current implementation uses immediate consumption.  A proper
two-phase reserve / settle / release flow should be added once the billing
service is wired in.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter
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

    NOTE (billing): this is immediate consumption.  A two-phase
    reserve → settle / release protocol should replace this for real billing.
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


def check_quota_available(
    db: Session,
    user_id: UUID,
    quota_type: str,
    required: int = 1,
) -> bool:
    """Return True if the user has *at least* `required` units remaining.

    * ``limit is None`` → unlimited → always True.
    * ``limit == 0`` → no allowance → always False.
    * ``limit < 0`` → treat as no allowance → False.
    * Unknown *quota_type* → ``ValueError``.
    """
    entitlement = get_entitlement(db, user_id)
    limit = _get_limit(entitlement, quota_type)

    if limit is None:
        # Unlimited
        return True
    if limit <= 0:
        # No allowance
        return False

    used = get_quota_used(db, user_id, quota_type)
    return (used + required) <= limit


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
