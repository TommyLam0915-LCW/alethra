"""Agent candidate screening API — isolated research slice, development only.

These routes expose the screening workflow for an existing locked rule
strategy: collect candidate signals, build an outcome per candidate, report
eligibility and exclusions, preview a dataset with a chronological split, and
validate the training/inference contract lineage.

What they deliberately do not do: train a model, activate one, run inference,
accept a research consent, or modify any paper order, fill or decision. A
snapshot request without the research consent is recorded as refused and
returns 409 — the audit record is the honest result, not a training set.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api_keys import check_api_key_scope
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services.agent_strategy import (
    AgentStrategyRefused,
    build_outcomes,
    candidate_outcome_history,
    capability,
    collect_candidates,
    consent_policy,
    consent_status,
    create_snapshot,
    evaluate_eligibility,
    existing_trade_eligibility,
    get_snapshot,
    list_candidates,
    preview_snapshot,
    snapshot_authorization,
    training_contract,
    validate_lineage,
)

router = APIRouter(
    prefix="/agent-strategy", tags=["agent-strategy-screening"]
)


class CollectCandidatesRequest(BaseModel):
    strategy_version_id: uuid.UUID
    instrument_id: Optional[str] = Field(default=None, max_length=64)
    provider_id: Optional[str] = Field(default=None, max_length=64)


class BuildOutcomesRequest(BaseModel):
    strategy_version_id: uuid.UUID
    instrument_id: Optional[str] = Field(default=None, max_length=64)
    explicit_functional_test_order_ids: Optional[List[str]] = Field(
        default=None,
        description="Order ids the owner states are functional tests. "
        "Used only to classify, never to edit those orders.",
    )


class SnapshotRequest(BaseModel):
    strategy_version_id: uuid.UUID
    instrument_id: Optional[str] = Field(default=None, max_length=64)


class LineageValidateRequest(BaseModel):
    lineage: dict = Field(
        description="Candidate/model lineage claims to check against the "
        "training contract's required fields."
    )


def require_screening_enabled() -> None:
    """Fail closed for C-slice writes outside an explicitly enabled dev stack."""
    if not get_settings().agent_strategy_screening_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_strategy_screening_disabled",
                "message_zh": (
                    "代理候選訊號篩選只在明確啟用的隔離開發環境可用；"
                    "原客戶環境未開放此用途。"
                ),
                "enabled": False,
            },
        )


def _refusal_http(exc: AgentStrategyRefused) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": exc.code,
            "message_zh": exc.message_zh,
            "details": exc.details,
        },
    )


@router.get("/capabilities")
def get_capability(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    settings = get_settings()
    authorization = snapshot_authorization(settings)
    return {
        "capability": capability(),
        "enabled_in_this_process": settings.agent_strategy_screening_enabled,
        "consent_policy": consent_policy(),
        # Four separate judgments the caller can read apart: the write path is
        # implemented, this process is allowed, this purpose is authorized by
        # the test fixture, and a snapshot is still never a training set.
        "snapshot_creation": {
            "implemented": True,
            "environment_allowed": authorization["environment_allowed"],
            "purpose_authorized": authorization["granted"],
            "authorization_mode": authorization["mode"],
            "real_owner_consent": False,
            "movable_to_customer_environment": False,
            "usable_for_training": False,
        },
        "training": {
            "real_training": "NOT_APPROVED",
            "model_activation": "NOT_APPROVED",
            "inference": "NOT_APPROVED",
        },
    }


@router.get("/consent-policy")
def get_consent_policy(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return {"policy": consent_policy(), "status": consent_status(db, current_user.id)}


@router.put("/consent")
def put_consent(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    # No consent table or acceptance path exists this round. Accepting a
    # request here would create a permission that no policy check reads.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "agent_strategy_consent_not_configured",
            "message_zh": "本輪未提供代理策略研究同意介面，亦不接受代 owner 同意。",
            "persisted": False,
        },
    )


@router.post(
    "/candidates/collect", dependencies=[Depends(require_screening_enabled)]
)
def post_collect_candidates(
    body: CollectCandidatesRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return collect_candidates(
            db,
            current_user.id,
            body.strategy_version_id,
            instrument_id=body.instrument_id,
            provider_id=body.provider_id,
            actor_type=request_actor_provenance(request),
        )
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.get("/candidates")
def get_candidates(
    strategy_version_id: uuid.UUID,
    instrument_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return list_candidates(
        db, current_user.id, strategy_version_id, instrument_id=instrument_id
    )


@router.get("/candidates/{candidate_id}/outcomes")
def get_candidate_outcome_history(
    candidate_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    try:
        return candidate_outcome_history(db, current_user.id, candidate_id)
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.post(
    "/candidates/outcomes", dependencies=[Depends(require_screening_enabled)]
)
def post_build_outcomes(
    body: BuildOutcomesRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return build_outcomes(
            db,
            current_user.id,
            body.strategy_version_id,
            instrument_id=body.instrument_id,
            explicit_functional_test_ids=body.explicit_functional_test_order_ids,
        )
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.get("/eligibility")
def get_eligibility(
    strategy_version_id: uuid.UUID,
    instrument_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return evaluate_eligibility(
        db, current_user.id, strategy_version_id, instrument_id=instrument_id
    )


@router.post(
    "/snapshots/preview", dependencies=[Depends(require_screening_enabled)]
)
def post_preview_snapshot(
    body: SnapshotRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return preview_snapshot(
            db, current_user.id, body.strategy_version_id,
            instrument_id=body.instrument_id,
        )
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.post("/snapshots", dependencies=[Depends(require_screening_enabled)])
def post_create_snapshot(
    body: SnapshotRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return create_snapshot(
            db, current_user.id, body.strategy_version_id,
            instrument_id=body.instrument_id,
        )
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.get("/snapshots/{snapshot_id}")
def get_snapshot_by_id(
    snapshot_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    try:
        return get_snapshot(db, current_user.id, snapshot_id)
    except AgentStrategyRefused as exc:
        if exc.code == "snapshot_not_found":
            # A snapshot owned by someone else is reported as absent, not as
            # forbidden, so the route does not confirm that it exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "message_zh": exc.message_zh},
            )
        raise _refusal_http(exc)


@router.get("/contract")
def get_contract(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    return training_contract()


@router.post("/contract/validate")
def post_validate_lineage(
    body: LineageValidateRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    return validate_lineage(body.lineage)


@router.get("/existing-trades/eligibility")
def get_existing_trade_eligibility(
    portfolio_id: Optional[uuid.UUID] = None,
    explicit_functional_test_order_ids: Optional[List[uuid.UUID]] = Query(default=None),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    # An operator can declare a trade a functional test; the declaration only
    # narrows eligibility and writes nothing.
    return existing_trade_eligibility(
        db,
        current_user.id,
        portfolio_id=portfolio_id,
        explicit_functional_test_ids=explicit_functional_test_order_ids,
    )
