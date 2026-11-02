"""Agent candidate screening API — isolated research slice, development only.

These routes expose the screening workflow for an existing locked rule
strategy: collect candidate signals, build an outcome per candidate, report
eligibility and exclusions, preview a dataset with a chronological split,
freeze a ready snapshot, record the owner's own consent, and run one bounded
training → artifact → inference chain over a frozen snapshot.

What they deliberately do not do: activate a model, release it to a customer
environment, or use real customer data. Training requires both the development
training fixture and a consent row the authenticated owner recorded for
themselves; the agent cannot accept consent on the owner's behalf.
"""

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api_keys import check_api_key_scope
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.config import get_settings
from app.database import get_db
from app.ml.agent_strategy_trainer import (
    get_training_job,
    list_training_jobs,
    run_inference,
    train_from_snapshot,
)
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
    record_consent,
    revoke_consent,
    snapshot_authorization,
    training_authorization,
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


class ConsentAcceptRequest(BaseModel):
    policy_version: str = Field(min_length=1, max_length=64)
    acceptance_text_version: str = Field(min_length=1, max_length=64)
    acknowledgement: str = Field(
        min_length=1,
        description="The full acceptance text, echoed back by the owner.",
    )


class ConsentRevokeRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=64)


class TrainingJobRequest(BaseModel):
    snapshot_id: uuid.UUID


class InferenceRequest(BaseModel):
    features: Dict[str, float] = Field(
        description="Feature values for one candidate, keyed by feature name."
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
    db: Session = Depends(get_db),
):
    settings = get_settings()
    authorization = snapshot_authorization(settings)
    training_auth = training_authorization(
        db, current_user.id, settings
    )
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
            "usable_for_fixture_training": training_auth["granted"],
            "usable_for_real_training": False,
        },
        "training": {
            "implementation": "development_fixture_only",
            "real_training": "NOT_APPROVED",
            "model_activation": "NOT_APPROVED",
            "inference": "NOT_APPROVED",
            "customer_release": "NOT_APPROVED",
            "activation_available": False,
            "requires_owner_consent_and_fixture": True,
            "authorized_in_this_process": training_auth["granted"],
            "authorization_mode": training_auth["authorization_mode"],
            "consent_status": training_auth["consent_status"],
        },
    }


@router.get("/consent-policy")
def get_consent_policy(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return {"policy": consent_policy(), "status": consent_status(db, current_user.id)}


@router.put("/consent", dependencies=[Depends(require_screening_enabled)])
def put_consent(
    body: ConsentAcceptRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    # ``current_user`` is the only identity that can be recorded; there is no
    # parameter for an accepting user, so one request cannot consent for
    # another account. The full text must be echoed back, so a bare boolean is
    # not enough to create the permission.
    try:
        return record_consent(
            db,
            current_user.id,
            policy_version=body.policy_version,
            acceptance_text_version=body.acceptance_text_version,
            acknowledgement=body.acknowledgement,
        )
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.post("/consent/revoke")
def post_revoke_consent(
    body: ConsentRevokeRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    # Revocation is deliberately not gated on the screening flag: an owner can
    # always withdraw a permission that was previously recorded.
    return revoke_consent(db, current_user.id, reason=body.reason)


@router.post(
    "/training/jobs", dependencies=[Depends(require_screening_enabled)]
)
def post_training_job(
    body: TrainingJobRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return train_from_snapshot(db, current_user.id, body.snapshot_id)
    except AgentStrategyRefused as exc:
        raise _refusal_http(exc)


@router.get("/training/jobs")
def get_training_jobs(
    snapshot_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    return list_training_jobs(db, current_user.id, snapshot_id=snapshot_id)


@router.get("/training/jobs/{job_id}")
def get_training_job_by_id(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    try:
        return get_training_job(db, current_user.id, job_id)
    except AgentStrategyRefused as exc:
        if exc.code == "training_job_not_found":
            # Same as snapshots: another owner's job is reported as absent.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "message_zh": exc.message_zh},
            )
        raise _refusal_http(exc)


@router.post(
    "/training/jobs/{job_id}/inference",
    dependencies=[Depends(require_screening_enabled)],
)
def post_training_inference(
    job_id: uuid.UUID,
    body: InferenceRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    try:
        return run_inference(db, current_user.id, job_id, body.features)
    except AgentStrategyRefused as exc:
        if exc.code == "training_job_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "message_zh": exc.message_zh},
            )
        raise _refusal_http(exc)


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
