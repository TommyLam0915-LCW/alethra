"""Personalization consent and dataset eligibility endpoints.

Capability C (a model trained on the owner's confirmed decisions) is not built
in this round. These endpoints expose the honest gate: the owner can grant a
scoped, revocable training consent for specific strategy versions, and the
eligibility endpoint reports measured counts and explicit refusal reasons.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api_keys import check_api_key_scope
from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.user import User
from app.services import emit_event
from app.services.personalization import (
    CONSENT_VERSION,
    capabilities,
    evaluate_eligibility,
    get_active_consent,
    grant_consent,
    revoke_consent,
)

router = APIRouter(prefix="/ml", tags=["personalization"])


class PersonalizationConsentRequest(BaseModel):
    strategy_version_ids: List[uuid.UUID] = Field(min_length=1)
    confirm: bool = Field(
        description="Explicit acknowledgement that recorded decisions will be "
        "used to train a model for this owner."
    )


def _owned_version_ids(
    db: Session, owner_id: uuid.UUID, version_ids: List[uuid.UUID]
) -> List[uuid.UUID]:
    rows = (
        db.query(ExperimentVersion.id)
        .join(Experiment, ExperimentVersion.experiment_id == Experiment.id)
        .filter(
            ExperimentVersion.id.in_(version_ids),
            Experiment.owner_id == owner_id,
        )
        .all()
    )
    return [row[0] for row in rows]


@router.get("/capabilities")
def list_capabilities(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return {"capabilities": capabilities(db, current_user.id)}


@router.put("/personalization/consent")
def put_personalization_consent(
    body: PersonalizationConsentRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="需要明確確認才會記錄個人化訓練同意",
        )

    unique_ids = list(dict.fromkeys(body.strategy_version_ids))
    owned = set(_owned_version_ids(db, current_user.id, unique_ids))
    missing = [str(version_id) for version_id in unique_ids if version_id not in owned]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy version not found: {', '.join(missing)}",
        )

    consent = grant_consent(db, current_user.id, unique_ids)
    emit_event(
        db,
        entity_type="personalization_consent",
        entity_id=str(consent.id),
        event_type="personalization_consent_granted",
        actor_id=current_user.id,
        actor_type="human",
        payload={
            "consent_version": CONSENT_VERSION,
            "strategy_version_ids": [str(v) for v in unique_ids],
        },
    )
    db.commit()
    db.refresh(consent)

    return {
        "consent": {
            "id": str(consent.id),
            "purpose": consent.purpose,
            "consent_version": consent.consent_version,
            "scope": consent.scope_json,
            "granted_at": consent.granted_at.isoformat() if consent.granted_at else None,
        },
        "eligibility": evaluate_eligibility(db, current_user.id),
    }


@router.delete("/personalization/consent")
def delete_personalization_consent(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    active = get_active_consent(db, current_user.id)
    revoked = revoke_consent(db, current_user.id)
    if active is not None:
        emit_event(
            db,
            entity_type="personalization_consent",
            entity_id=str(active.id),
            event_type="personalization_consent_revoked",
            actor_id=current_user.id,
            actor_type="human",
            payload={"consent_version": active.consent_version, "revoked": revoked},
        )
    db.commit()
    return {
        "revoked": revoked,
        "eligibility": evaluate_eligibility(db, current_user.id),
    }


@router.get("/personalization/eligibility")
def get_personalization_eligibility(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return evaluate_eligibility(db, current_user.id)
