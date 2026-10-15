from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter
from app.auth.jwt import get_current_user


QUOTA_TYPE_MAP = {
    "research_run": "research_run_limit",
    "export": "export_limit",
    "forward_simulation": "forward_simulation_limit",
    "ai_usage": "ai_usage_limit",
}


def get_entitlement(db: Session, user_id: UUID) -> Optional[PilotEntitlement]:
    return db.query(PilotEntitlement).filter(
        PilotEntitlement.user_id == user_id
    ).first()


def check_entitlement_active(entitlement: PilotEntitlement) -> bool:
    if not entitlement.pilot_access:
        return False
    if entitlement.expiry_date and entitlement.expiry_date < datetime.utcnow():
        return False
    return True


def get_quota_used(db: Session, user_id: UUID, quota_type: str) -> int:
    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == "lifetime",
    ).first()
    return counter.used if counter else 0


def consume_quota(db: Session, user_id: UUID, quota_type: str, amount: int = 1) -> int:
    counter = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == quota_type,
        QuotaCounter.period == "lifetime",
    ).first()

    if counter is None:
        counter = QuotaCounter(
            user_id=user_id,
            quota_type=quota_type,
            period="lifetime",
            used=amount,
        )
        db.add(counter)
    else:
        counter.used += amount

    db.flush()
    return counter.used


def check_quota_available(
    db: Session,
    user_id: UUID,
    quota_type: str,
    required: int = 1,
) -> bool:
    entitlement = get_entitlement(db, user_id)
    if entitlement is None:
        return True

    entitlement_field = QUOTA_TYPE_MAP.get(quota_type)
    if entitlement_field is None:
        return True

    limit = getattr(entitlement, entitlement_field, None)
    if limit is None or limit <= 0:
        return True

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
                limit = getattr(entitlement, entitlement_field, 0) if entitlement else 0
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
