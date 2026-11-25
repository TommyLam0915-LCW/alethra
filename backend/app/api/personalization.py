"""Personalization consent, dataset eligibility and the daily estimate service.

Capability C (a model trained on the owner's confirmed decisions) is not built
in this round. These endpoints expose the honest gate: the owner can grant a
scoped, revocable training consent for specific strategy versions, and the
eligibility endpoint reports measured counts and explicit refusal reasons.

The daily-estimate routes are the third consent, and it is a different one: a
training consent does not turn the daily clock on, and turning the clock off
does not touch the training consent. Every estimate those routes produce goes
through the same inference gate and the same billing contract as ``POST
…/predict``; nothing in this file runs a model by another door.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api_keys import check_api_key_scope
from app.auth.jwt import get_current_user
from app.auth.provenance import HUMAN, request_actor_provenance
from app.auth.safety import check_kill_switch
from app.database import get_db
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.kill_switch import KillSwitchScope
from app.models.paper_portfolio import PaperPortfolio
from app.models.user import User
from app.services import emit_event
from app.services.daily_estimates import (
    EstimateRefused,
    billed_estimates_by_skill,
    consent_payload,
    daily_estimate_status,
    grant_daily_estimate,
    owned_skill_unit,
    trigger_daily_estimate,
    withdraw_daily_estimate,
)
from app.services.personalization import (
    CONSENT_VERSION,
    capabilities,
    evaluate_eligibility,
    get_active_consent,
    grant_consent,
    revoke_consent,
    training_policy,
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
    return {
        "capabilities": capabilities(db, current_user.id),
        "training_policy": training_policy(db, current_user.id),
    }


@router.put("/personalization/consent")
def put_personalization_consent(
    body: PersonalizationConsentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="需要明確確認才會記錄個人化訓練同意",
        )

    # Training consent is an owner legal act. An API credential (agent) or a
    # credential whose authentication method is unknown cannot consent on the
    # owner's behalf, so the request is refused rather than mislabelled human.
    actor_type = request_actor_provenance(request)
    if actor_type != HUMAN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "personalization training consent requires a password-authenticated "
                f"owner session (provenance: {actor_type})"
            ),
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
        actor_type=actor_type,
        payload={
            "consent_version": CONSENT_VERSION,
            "strategy_version_ids": [str(v) for v in unique_ids],
            "actor_type": actor_type,
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
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    # Revocation is a safety action and stays reachable from any owner
    # credential; the audit event records which one it actually was.
    actor_type = request_actor_provenance(request)
    active = get_active_consent(db, current_user.id)
    revoked = revoke_consent(db, current_user.id)
    if active is not None:
        emit_event(
            db,
            entity_type="personalization_consent",
            entity_id=str(active.id),
            event_type="personalization_consent_revoked",
            actor_id=current_user.id,
            actor_type=actor_type,
            payload={
                "consent_version": active.consent_version,
                "revoked": revoked,
                "actor_type": actor_type,
            },
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


# ---------------------------------------------------------------------------
# the daily estimate service: the third consent, its read-back, and one run
# ---------------------------------------------------------------------------


class DailyEstimateSkill(BaseModel):
    skill_unit_id: uuid.UUID
    instrument_ids: List[str] = Field(min_length=1)
    max_data_age_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="Refuse the estimate when the bar behind it is older than "
        "this many days instead of spending an ai_usage unit on it. Unset means "
        "the owner declared no ceiling — the platform does not invent one.",
    )


class DailyEstimateConsentRequest(BaseModel):
    skill_units: List[DailyEstimateSkill] = Field(min_length=1)
    confirm: bool = Field(
        description="Explicit acknowledgement that this platform will run one "
        "model estimate for the named skill accounts on its daily clock, "
        "consuming one ai_usage unit each time it does."
    )


class DailyEstimateRunRequest(BaseModel):
    skill_unit_id: uuid.UUID


_DAILY_ESTIMATE_MESSAGES_ZH = {
    "empty_scope": "請至少指定一個技能帳號。",
    "scope_too_large": "每個業主最多可申報 10 個技能帳號。",
    "instrument_list_too_long": "每個技能帳號最多可申報 8 個標的。",
    "malformed_skill_unit_id": "技能帳號識別碼格式不正確。",
    "skill_unit_not_found": "找不到屬於你的技能帳號，未記錄任何每日估計範圍。",
    "no_instruments_declared": "每個技能帳號都要申報至少一個標的。",
    "skill_paused": "此技能帳號已暫停，每日估計不會替它執行。",
    "skill_retired": "此技能帳號已退休，每日估計不會替它執行。",
    "skill_completed": "此技能帳號已完成，每日估計不會替它執行。",
    "skill_status_unreadable": "無法辨識此技能帳號的狀態，每日估計不會替它執行。",
    "strategy_scope_missing": "此技能帳號尚未選擇模型版本，無法產生每日估計。",
    "model_version_not_found": "所選模型版本已不存在，請重新選擇版本。",
    "no_skills_enabled": "此技能帳號未申報每日估計範圍，未執行任何推論。",
    # The codes below come from the shared inference gate and are recorded
    # unchanged, so the panel names the same reason the owner would have been
    # told at ``POST …/predict``.
    "stale_market_data": "市場資料已超過你申報的新鮮度上限，本次未執行，也未消耗額度。",
    "missing_market_data": "此標的缺乏足夠的歷史資料，未執行推論。",
    "missing_features": "此標的或日期缺少決策時點可用的市場資料，未執行推論。",
    "invalid_context": "無法辨識此次推論的時點脈絡，未執行推論。",
    "future_leakage": "請求的資料時間點與可用資料不相符，未執行推論。",
    "feature_schema_mismatch": "目前特徵欄位與模型版本不一致，未執行推論。",
    "artifact_missing": "此版本沒有可用的模型 artifact，未執行推論。",
    "prediction_failed": "推論失敗，已釋放本次額度。",
    "quota_exceeded": "額度已用完，未執行推論。",
    "inference_not_authorized": "此技能帳號未通過授權檢查，無法執行推論。",
    "no_training_job": "此模型版本沒有對應的個人訓練記錄，未執行推論。",
    "invalid_training_job_id": "訓練記錄識別碼不正確，未執行推論。",
    "version_not_found": "找不到此模型版本，未執行推論。",
    "no_skill_unit_selection": "此技能帳號尚未選擇模型版本，未執行推論。",
    "superseded_skill_unit_selection": "此模型版本已被新的選擇取代，未執行推論。",
    "training_job_not_found": "找不到訓練記錄，未執行推論。",
    "training_job_not_completed": "訓練尚未完成，未執行推論。",
    "artifact_mismatch": "模型 artifact 與訓練記錄不符，未執行推論。",
    "already_estimated_this_window": "此技能帳號今日的估計已存在，未重複執行。",
    "consent_withdrawn": "你在這次批次走到此帳號之前撤回了它的每日估計，未對它執行推論。",
    "kill_switch_active": "推論服務的緊急停止開關生效中，本次未執行。",
    "kill_switch_unreadable": "無法讀取緊急停止開關，本次未執行。",
    "already_ran_today": "今日的每日估計已經執行過，未重複執行。",
    "no_accounts_served": "沒有已申報每日估計的技能帳號，未執行任何推論。",
    "tick_error": "每日估計批次在寫入任何記錄前失敗，未執行任何推論。",
    # Not a refusal: the estimate was started and its worker raised. The row
    # keeps the exception text in ``errors`` and the log; the sentence below is
    # the whole of what the owner is told, because the internal exception is
    # the platform's failure to fix, not a condition the owner can act on.
    "engine_failure": "這個標的的每日估計在推論時失敗，這次沒有留下估計結果。",
}


def _refuse_daily_estimate(exc: EstimateRefused) -> HTTPException:
    code = exc.code
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT
        if code in {"skill_unit_not_found", "model_version_not_found"}
        else status.HTTP_400_BAD_REQUEST,
        detail={
            "code": code,
            "message_zh": _DAILY_ESTIMATE_MESSAGES_ZH.get(
                code, "每日估計範圍無法被服務，未記錄任何變更。"
            ),
            "detail": str(exc.detail) if exc.detail is not None else None,
        },
    )


def _daily_estimate_event(
    db: Session, request: Request, current_user: User, event_type: str, payload: dict
) -> None:
    emit_event(
        db,
        entity_type="daily_estimate_consent",
        entity_id=str(current_user.id),
        event_type=event_type,
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload=payload,
    )


@router.put("/personalization/daily-estimate/consent")
def put_daily_estimate_consent(
    body: DailyEstimateConsentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    """Declare which skill accounts the daily clock may estimate, and for what.

    This is the opt-in, so it is an owner legal act on the same footing as the
    training consent: an API credential (an agent) is refused rather than
    recorded as the owner's instruction. Granting replaces the whole scope; the
    clock stays off for every account this request does not name.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="需要明確確認才會記錄每日估計同意",
        )
    actor_type = request_actor_provenance(request)
    if actor_type != HUMAN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "daily estimate consent requires a password-authenticated owner "
                f"session (provenance: {actor_type})"
            ),
        )

    targets = [
        {
            "skill_unit_id": str(entry.skill_unit_id),
            "instrument_ids": [
                value.strip() for value in entry.instrument_ids if value.strip()
            ],
            "max_data_age_days": entry.max_data_age_days,
        }
        for entry in body.skill_units
    ]
    try:
        consent = grant_daily_estimate(db, current_user, targets)
    except EstimateRefused as exc:
        raise _refuse_daily_estimate(exc)

    _daily_estimate_event(
        db,
        request,
        current_user,
        "daily_estimate_consent_granted",
        {
            "consent_version": consent["consent_version"],
            "skill_unit_ids": [item["skill_unit_id"] for item in consent["skill_units"]],
            "actor_type": actor_type,
        },
    )
    db.commit()
    return {"consent": consent, "skills": _scope_statuses(db, current_user, consent)}


@router.delete("/personalization/daily-estimate/consent")
def delete_daily_estimate_consent(
    request: Request,
    skill_unit_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    """Withdraw the daily clock — one named skill account, or every one of them.

    Withdrawal stays reachable from any owner credential, including an agent
    key: it is the safety action, and a control that only the owner's own
    session could reach could not be reached at all by the automation the owner
    is telling to stop. Only this purpose's row is touched; the training
    consent is a separate row and keeps its own state.
    """
    actor_type = request_actor_provenance(request)
    before = consent_payload(db, current_user.id)
    try:
        consent = withdraw_daily_estimate(
            db, current_user, None if skill_unit_id is None else [str(skill_unit_id)]
        )
    except EstimateRefused as exc:
        raise _refuse_daily_estimate(exc)

    if before["consent_id"]:
        _daily_estimate_event(
            db,
            request,
            current_user,
            "daily_estimate_consent_revoked",
            {
                "previous_consent_id": before["consent_id"],
                "skill_unit_id": str(skill_unit_id) if skill_unit_id else None,
                "remaining_skill_unit_ids": [
                    item["skill_unit_id"] for item in consent["skill_units"]
                ],
                "actor_type": actor_type,
            },
        )
    db.commit()
    return {
        "consent": consent,
        "skills": _scope_statuses(db, current_user, consent),
        "training_consent_untouched": get_active_consent(db, current_user.id) is not None,
    }


@router.get("/personalization/daily-estimate/consent")
def get_daily_estimate_consent_state(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    """The declared scope as the owner wrote it. No row means the clock is off."""
    return consent_payload(db, current_user.id)


@router.get("/personalization/daily-estimate/status")
def get_daily_estimate_status(
    skill_unit_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
    db: Session = Depends(get_db),
):
    """Last estimate, its pinned version, its data time point, and why not.

    Without ``skill_unit_id`` this answers for every skill account the owner
    holds, because the honest reading of "is the daily service running for me?"
    is one list, not a page per account.
    """
    if skill_unit_id is not None:
        portfolio = owned_skill_unit(db, current_user.id, str(skill_unit_id))
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Skill unit not found")
        return _daily_estimate_status(db, current_user, portfolio)
    portfolios = (
        db.query(PaperPortfolio)
        .filter(PaperPortfolio.owner_id == current_user.id)
        .order_by(PaperPortfolio.created_at.desc())
        .all()
    )
    billed = billed_estimates_by_skill(db, current_user.id)
    return {
        "skills": [
            _daily_estimate_status(db, current_user, row, billed=billed)
            for row in portfolios
        ]
    }


@router.post("/personalization/daily-estimate/run", status_code=status.HTTP_201_CREATED)
def run_daily_estimate_now(
    body: DailyEstimateRunRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write", "inference")),
    _kill_switch=Depends(check_kill_switch(KillSwitchScope.inference)),
    db: Session = Depends(get_db),
):
    """Estimate one declared skill account now, through the service's own tick.

    The response is the ledger row the clock would have written, and it is
    written by the same function: ``scope="manual"`` is the only difference, and
    it says that a person asked rather than the schedule firing. Refusing here
    for a skill the owner never declared is the point — the button cannot widen
    the consent.
    """
    try:
        outcome = trigger_daily_estimate(db, current_user, body.skill_unit_id)
    except EstimateRefused as exc:
        raise _refuse_daily_estimate(exc)
    # The tick commits on its own session, so this request's identity map is
    # one transaction behind the row it just wrote.
    db.expire_all()
    portfolio = owned_skill_unit(db, current_user.id, str(body.skill_unit_id))
    return {
        "job": outcome,
        "status": _daily_estimate_status(db, current_user, portfolio)
        if portfolio
        else None,
    }


def _daily_estimate_status(
    db: Session, current_user: User, portfolio: PaperPortfolio, *, billed=None
) -> dict:
    """The service's status read with each refusal and failure said in Chinese.

    The ledger stores the gate's machine code; a panel that printed
    ``superseded_skill_unit_selection`` would be accurate and useless. The code
    stays in ``reason`` next to the sentence, so a reader can tell which
    platform build said it.

    ``billed`` is the owner's billing rows grouped by skill, read once for
    whoever asks about several accounts at a time: the ledger fallback each card
    needs is the same scan whichever skill is being described.
    """
    state = daily_estimate_status(db, current_user, portfolio, billed=billed)
    refusal = state.get("last_refusal")
    if isinstance(refusal, dict):
        refusal["message_zh"] = _DAILY_ESTIMATE_MESSAGES_ZH.get(
            str(refusal.get("reason")), "每日估計本次未執行。"
        )
    failure = state.get("last_failure")
    if isinstance(failure, dict):
        failure["message_zh"] = _DAILY_ESTIMATE_MESSAGES_ZH.get(
            str(failure.get("reason")), "每日估計本次失敗，未留下估計結果。"
        )
    return state


def _scope_statuses(
    db: Session, current_user: User, consent: dict
) -> list:
    """Per-skill read-back for the two writes, so a grant answers in use.

    Read after the write on the same session, and named per skill unit: the
    owner needs to see which account is enabled and which the platform refused,
    not just that the request was accepted. The billing rows are read once here
    and handed to every account, because the ledger fallback each card needs is
    the same scan whichever skill is being described.
    """
    billed = billed_estimates_by_skill(db, current_user.id)
    out = []
    for item in consent["skill_units"]:
        portfolio = owned_skill_unit(db, current_user.id, item["skill_unit_id"])
        if portfolio is not None:
            out.append(
                _daily_estimate_status(db, current_user, portfolio, billed=billed)
            )
    return out
