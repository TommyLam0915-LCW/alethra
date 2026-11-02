"""Personal strategy learning API.

Chain: reviewed decisions → dataset snapshot → training job → model version
→ immutable-version or resolved-active-alias prediction.

Guards: ownership is checked on every read and write; API-key scopes and the
per-key rate limit apply; inference consumes one ai_usage unit reserved before
the call (released on failure); error bodies never include artifact paths or
another owner's data.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api_keys import check_api_key_scope
from app.auth.entitlements import (
    release_reservation,
    reserve_quota,
    settle_reservation,
)
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.auth.rate_limit import api_key_rate_limit_dependency
from app.config import get_settings
from app.database import get_db
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    PERSONAL_WORDING_ZH,
    PersonalTrainingRefused,
    predict_personal,
)
from app.models.model_research import ModelResearchRecord, ModelType, ModelVersion
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.personal_model_selection import (
    PersonalModelComparisonRun,
    PersonalModelSelection,
)
from app.models.user import User
from app.services import emit_event
from app.services.model_comparison import (
    ComparisonRefused,
    _canonical_request,
    compare_contracts,
    evaluate_pair,
    existing_run,
    list_selections,
    requests_match,
)
from app.services.personal_selection import (
    SelectionRefused,
    rollback as rollback_selection,
    select_version,
)
from app.services.personal_dataset import (
    DatasetRefused,
    create_snapshot,
    features_as_of,
    preview_snapshot,
    snapshot_payload,
)
from app.services.job_control import (
    IdempotencyConflict,
    find_idempotent_job,
    lock_user_for_idempotency,
    request_fingerprint,
    stamp_idempotency,
)
from app.services.personal_journey import JourneyRefused, build_journey
from app.services.personal_training import (
    PERSONAL_TRAINING_TYPE,
    create_personal_training_job,
    get_owned_snapshot,
)
from app.services.personalization import get_active_consent, _scope_strategy_ids

_settings = get_settings()

router = APIRouter(
    prefix="/ml/personal",
    tags=["personal-model"],
    dependencies=[
        Depends(
            api_key_rate_limit_dependency(
                max_requests=_settings.api_key_rate_limit_requests,
                window_seconds=_settings.api_key_rate_limit_window_seconds,
            )
        )
    ],
)

READ_SCOPES = ("read", "inference", "training")
WRITE_SCOPES = ("write", "training")


class SnapshotRequest(BaseModel):
    strategy_version_id: uuid.UUID


class TrainingJobRequest(BaseModel):
    snapshot_id: uuid.UUID
    model_type: str = Field(default="logistic_regression")


class PredictRequest(BaseModel):
    instrument_id: str = Field(min_length=1)
    as_of_date: str = Field(min_length=10, max_length=10)


def _via(request: Request) -> str:
    scopes = getattr(request.state, "api_key_scopes", None)
    return "jwt" if scopes is None else "api_key"


def _refuse(exc: DatasetRefused | PersonalTrainingRefused) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": exc.code,
            "message_zh": exc.message_zh,
            "details": exc.details,
        },
    )


def _owned_record(db: Session, record_id: uuid.UUID, user: User) -> ModelResearchRecord:
    record = db.query(ModelResearchRecord).filter(
        ModelResearchRecord.id == record_id
    ).first()
    if record is None or record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Model record not found")
    return record


def _owned_version(
    db: Session, version_id: uuid.UUID, user: User
) -> tuple:
    version = db.query(ModelVersion).filter(ModelVersion.id == version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Model version not found")
    record = db.query(ModelResearchRecord).filter(
        ModelResearchRecord.id == version.model_research_record_id
    ).first()
    if record is None or record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Model version not found")
    return version, record


def _assert_personal_version(version: ModelVersion) -> dict:
    spec = version.model_specification_json or {}
    if (
        version.model_type is not ModelType.personal_decision_model
        or spec.get("task_type") != PERSONAL_TASK_TYPE
    ):
        # Never load a rule/research version as an ML artifact.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_a_personal_decision_model",
                "message_zh": "此版本不是以個人決策訓練的模型，不能用作個人決策推論。",
            },
        )
    if version.revoked_at is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_version_revoked",
                "message_zh": "此模型版本已撤銷，不能用作推論。",
            },
        )
    return spec


def _evaluation_for(db: Session, version_id: uuid.UUID) -> Optional[dict]:
    from app.models.model_research import ModelEvaluation

    evaluation = (
        db.query(ModelEvaluation)
        .filter(ModelEvaluation.model_version_id == version_id)
        .order_by(ModelEvaluation.evaluation_date.desc())
        .first()
    )
    if evaluation is None:
        return None
    return {
        "evaluation_type": evaluation.evaluation_type.value,
        "result_summary": evaluation.result_summary,
        "metrics": evaluation.metrics_json or {},
        "warnings": evaluation.warning_flags or [],
        "data_cutoff_at": evaluation.data_cutoff_at,
        "engine_version": evaluation.engine_version,
    }


def _version_payload(db: Session, version: ModelVersion, record: ModelResearchRecord) -> dict:
    spec = version.model_specification_json or {}
    return {
        "model_version_id": str(version.id),
        "record_id": str(record.id),
        "version_number": version.version_number,
        "is_active": record.active_version_id == version.id,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "revoked_at": version.revoked_at.isoformat() if version.revoked_at else None,
        "artifact_id": spec.get("artifact_id"),
        "artifact_sha256": spec.get("artifact_sha256"),
        "snapshot_id": spec.get("snapshot_id"),
        "snapshot_checksum": spec.get("snapshot_checksum"),
        "training_job_id": spec.get("training_job_id"),
        "strategy_version_id": spec.get("strategy_version_id"),
        "classes": spec.get("classes"),
        "n_rows": spec.get("n_rows"),
        "warnings": spec.get("warnings"),
        "split": spec.get("split"),
        "evaluation": _evaluation_for(db, version.id),
        "wording_zh": PERSONAL_WORDING_ZH,
        "not_for": spec.get("not_for"),
    }


@router.post("/snapshots/preview")
def preview_dataset_snapshot(
    body: SnapshotRequest,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    try:
        return preview_snapshot(db, current_user.id, body.strategy_version_id)
    except DatasetRefused as exc:
        raise _refuse(exc)


@router.get("/journey")
def personal_journey(
    strategy_version_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    """Read-only five-step journey plus worker/queue state.

    Answers 研究緊甚麼 / 做過甚麼 / 得到甚麼 / 仲缺甚麼 / 下一步做甚麼
    from server state. Never consumes quota, never writes.
    """
    try:
        return build_journey(db, current_user.id, strategy_version_id)
    except JourneyRefused as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": exc.code, "message_zh": exc.message_zh},
        )


@router.post("/snapshots", status_code=201)
def create_dataset_snapshot(
    body: SnapshotRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    try:
        snapshot, payload = create_snapshot(
            db, current_user.id, body.strategy_version_id
        )
    except DatasetRefused as exc:
        raise _refuse(exc)

    emit_event(
        db,
        entity_type="personalization_dataset_snapshot",
        entity_id=str(snapshot.id),
        event_type="personalization_dataset_snapshot_created",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "strategy_version_id": str(snapshot.strategy_version_id),
            "consent_id": str(snapshot.consent_id),
            "checksum": snapshot.checksum,
            "included_count": snapshot.included_count,
            "excluded_count": snapshot.excluded_count,
        },
    )
    db.commit()
    db.refresh(snapshot)
    return snapshot_payload(snapshot)


@router.get("/snapshots")
def list_dataset_snapshots(
    strategy_version_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    query = db.query(PersonalizationDatasetSnapshot).filter(
        PersonalizationDatasetSnapshot.owner_id == current_user.id
    )
    if strategy_version_id:
        query = query.filter(
            PersonalizationDatasetSnapshot.strategy_version_id == strategy_version_id
        )
    snapshots = query.order_by(
        PersonalizationDatasetSnapshot.created_at.desc()
    ).all()
    return {
        "snapshots": [snapshot_payload(snapshot) for snapshot in snapshots]
    }


@router.get("/snapshots/{snapshot_id}")
def get_dataset_snapshot(
    snapshot_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    snapshot = (
        db.query(PersonalizationDatasetSnapshot)
        .filter(
            PersonalizationDatasetSnapshot.id == snapshot_id,
            PersonalizationDatasetSnapshot.owner_id == current_user.id,
        )
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    payload = snapshot_payload(snapshot)
    payload["rows_preview"] = [
        {
            "decision_event_id": row.get("decision_event_id"),
            "action_label": row.get("action_label"),
            "instrument_id": row.get("instrument_id"),
            "snapshot_date": row.get("snapshot_date"),
            "feature_bar_date": row.get("feature_bar_date"),
        }
        for row in (snapshot.rows_json or [])[:5]
    ]
    return payload


@router.post("/training-jobs", status_code=202)
def start_personal_training(
    body: TrainingJobRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    if body.model_type not in ("logistic_regression", "baseline"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unknown_model_type",
                "message_zh": "不支援的模型類型。",
            },
        )
    try:
        snapshot = get_owned_snapshot(db, body.snapshot_id, current_user.id)
    except PersonalTrainingRefused as exc:
        raise _refuse(exc)

    fingerprint = request_fingerprint(
        {"snapshot_id": str(body.snapshot_id), "model_type": body.model_type}
    )
    if idempotency_key:
        if not 8 <= len(idempotency_key) <= 128:
            raise HTTPException(400, "Idempotency-Key must be 8-128 characters")
        lock_user_for_idempotency(db, current_user.id)
        try:
            existing = find_idempotent_job(
                db,
                current_user.id,
                idempotency_key,
                fingerprint,
                training_type=PERSONAL_TRAINING_TYPE,
            )
        except IdempotencyConflict as exc:
            db.rollback()
            raise HTTPException(409, {"error": "idempotency_conflict", "message": str(exc)})
        if existing is not None:
            db.commit()
            return {
                "job_id": str(existing.id),
                "status": existing.status.value,
                "training_type": PERSONAL_TRAINING_TYPE,
                "snapshot_id": str(body.snapshot_id),
                "poll_url": f"/api/v1/training/jobs/{existing.id}",
                "message": "個人決策模型訓練工作已排隊。",
            }

    reservation = reserve_quota(db, current_user.id, "ai_usage")
    try:
        job = create_personal_training_job(
            db,
            user=current_user,
            snapshot=snapshot,
            quota_reservation_id=reservation.id,
            model_type=body.model_type,
        )
    except PersonalTrainingRefused as exc:
        db.rollback()
        raise _refuse(exc)

    if idempotency_key:
        stamp_idempotency(job, idempotency_key, fingerprint)

    db.commit()
    return {
        "job_id": str(job.id),
        "status": job.status.value,
        "training_type": PERSONAL_TRAINING_TYPE,
        "snapshot_id": str(snapshot.id),
        "poll_url": f"/api/v1/training/jobs/{job.id}",
        "message": "個人決策模型訓練工作已排隊。",
    }


@router.get("/models")
def list_personal_models(
    strategy_version_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    records = db.query(ModelResearchRecord).filter(
        ModelResearchRecord.owner_id == current_user.id
    ).all()
    out = []
    for record in records:
        if record.title and not record.title.startswith("個人決策模型"):
            continue
        versions = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.model_research_record_id == record.id,
                ModelVersion.model_type == ModelType.personal_decision_model,
            )
            .order_by(ModelVersion.version_number)
            .all()
        )
        for version in versions:
            spec = version.model_specification_json or {}
            if strategy_version_id and str(spec.get("strategy_version_id")) != str(
                strategy_version_id
            ):
                continue
            out.append(_version_payload(db, version, record))
    out.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"models": out}


class ActivateRequest(BaseModel):
    expected_active_version_id: Optional[uuid.UUID] = None


@router.post("/models/{version_id}/activate")
def activate_personal_model(
    version_id: uuid.UUID,
    request: Request,
    body: Optional[ActivateRequest] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    """Legacy alias setter — same eligibility gate as an explicit selection."""
    enforce_expected = bool(body is not None and body.model_fields_set)
    try:
        outcome = select_version(
            db,
            current_user.id,
            version_id=version_id,
            rationale="啟用版本（使用中別名切換）",
            comparison_run_id=None,
            expected_active_version_id=(
                body.expected_active_version_id if body is not None else None
            ),
            enforce_expected=enforce_expected,
        )
    except SelectionRefused as exc:
        db.rollback()
        raise _selection_refused(exc)

    record = outcome["record"]
    version = outcome["version"]
    previous = outcome["previous_version_id"]

    if outcome["already_active"]:
        db.commit()
        return _version_payload(db, version, record)

    emit_event(
        db,
        entity_type="model_research_record",
        entity_id=str(record.id),
        event_type="personal_model_active_version_set",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "from_version_id": str(previous) if previous else None,
            "to_version_id": str(version.id),
            "to_version_number": version.version_number,
        },
    )
    db.commit()
    db.refresh(version)
    db.refresh(record)
    return _version_payload(db, version, record)


def _run_personal_prediction(
    db: Session,
    *,
    version: ModelVersion,
    record: ModelResearchRecord,
    body: PredictRequest,
    request: Request,
    current_user: User,
    alias_used: bool,
) -> dict:
    spec = _assert_personal_version(version)

    artifact_id = spec.get("artifact_id")
    if not artifact_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "artifact_missing",
                "message_zh": "此版本沒有可用的模型 artifact。",
            },
        )

    features, feature_names, feature_bar_date, exclusion = features_as_of(
        db, body.instrument_id, body.as_of_date
    )
    if exclusion or features is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": exclusion or "missing_features",
                "message_zh": "此標的或日期缺少決策時點可用的市場資料，未執行推論。",
            },
        )
    expected = spec.get("feature_names") or []
    if feature_names != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "feature_schema_mismatch",
                "message_zh": "目前特徵欄位與模型版本不一致，未執行推論。",
            },
        )

    reservation = reserve_quota(db, current_user.id, "ai_usage")
    try:
        result = predict_personal(artifact_id, features, owner_id=str(current_user.id))
    except FileNotFoundError:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "artifact_missing",
                "message_zh": "模型 artifact 已不存在，未執行推論。",
            },
        )
    except PermissionError:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(status_code=403, detail="Not authorized for this model")
    except PersonalTrainingRefused as exc:
        release_reservation(db, reservation.id)
        db.commit()
        raise _refuse(exc)
    except Exception:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={
                "code": "prediction_failed",
                "message_zh": "推論失敗，已釋放本次額度。",
            },
        )

    settle_reservation(db, reservation.id)

    from app.services.billing import record_api_call_no_commit

    record_api_call_no_commit(
        db,
        user_id=current_user.id,
        endpoint="/api/v1/ml/personal/models/{version_id}/predict",
        model=str(version.id),
        tokens_used=0,
        cost=0,
        metadata={
            "prediction_type": "personal_decision",
            "via": _via(request),
            "model_version_id": str(version.id),
            "record_id": str(record.id),
            "artifact_id": artifact_id,
            "artifact_sha256": spec.get("artifact_sha256"),
            "snapshot_id": spec.get("snapshot_id"),
            "snapshot_checksum": spec.get("snapshot_checksum"),
            "training_job_id": spec.get("training_job_id"),
            "instrument_id": body.instrument_id,
            "as_of_date": body.as_of_date,
        },
    )

    consent = get_active_consent(db, current_user.id)
    consent_scope = {str(v) for v in _scope_strategy_ids(consent)} if consent else set()

    db.commit()
    return {
        "resolved_version": {
            "model_version_id": str(version.id),
            "record_id": str(record.id),
            "version_number": version.version_number,
            "resolved_from_active_alias": alias_used,
        },
        "model_estimate_zh": PERSONAL_WORDING_ZH,
        "predicted_action": result["predicted_action"],
        "action_probabilities": result["action_probabilities"],
        "classes": result["classes"],
        "as_of": {
            "instrument_id": body.instrument_id,
            "as_of_date": body.as_of_date,
            "feature_bar_date": feature_bar_date,
        },
        "snapshot": {
            "snapshot_id": spec.get("snapshot_id"),
            "checksum": spec.get("snapshot_checksum"),
        },
        "artifact_sha256": spec.get("artifact_sha256"),
        "training_job_id": spec.get("training_job_id"),
        "consent_currently_active": (
            consent is not None
            and str(spec.get("strategy_version_id")) in consent_scope
        ),
        "not_for": [
            "市場方向預測",
            "交易是否正確或有利潤",
            "投資建議",
        ],
        "disclaimer_zh": "結果是模型對你本人決策的估計，不是市場方向、"
        "不是交易建議，也不代表回報或越用越準。",
    }


@router.post("/models/{version_id}/predict")
def predict_with_version(
    version_id: uuid.UUID,
    body: PredictRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write", "inference")),
    db: Session = Depends(get_db),
):
    version, record = _owned_version(db, version_id, current_user)
    return _run_personal_prediction(
        db,
        version=version,
        record=record,
        body=body,
        request=request,
        current_user=current_user,
        alias_used=False,
    )


@router.post("/records/{record_id}/predict")
def predict_with_record_alias(
    record_id: uuid.UUID,
    body: PredictRequest,
    request: Request,
    version_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write", "inference")),
    db: Session = Depends(get_db),
):
    record = _owned_record(db, record_id, current_user)
    alias_used = version_id is None
    target_id = version_id or record.active_version_id
    if target_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_active_version",
                "message_zh": "此模型尚未指定使用中的版本；請先啟用一個版本或明確指定 version_id。",
            },
        )

    version = db.query(ModelVersion).filter(
        ModelVersion.id == target_id,
        ModelVersion.model_research_record_id == record.id,
    ).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Model version not found in this record")

    return _run_personal_prediction(
        db,
        version=version,
        record=record,
        body=body,
        request=request,
        current_user=current_user,
        alias_used=alias_used,
    )


# ---------------------------------------------------------------------------
# Model evaluation and selection center
# ---------------------------------------------------------------------------


class ComparisonRequest(BaseModel):
    version_a_id: uuid.UUID
    version_b_id: uuid.UUID
    operation_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class SelectionRequest(BaseModel):
    rationale: str = Field(min_length=2, max_length=1000)
    comparison_run_id: Optional[uuid.UUID] = None
    # Required but nullable: null states "I expect no active version". The
    # value is compared under the record lock, so a concurrent switch is
    # refused instead of silently overwritten.
    expected_active_version_id: Optional[uuid.UUID]


class RollbackRequest(BaseModel):
    rationale: Optional[str] = Field(default=None, max_length=1000)
    expected_active_version_id: Optional[uuid.UUID]


def _comparison_refused(exc: ComparisonRefused) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": exc.code,
            "message_zh": exc.message_zh,
            "details": exc.details,
        },
    )


def _selection_refused(exc: SelectionRefused) -> HTTPException:
    return HTTPException(
        status_code=404 if exc.not_found else 409,
        detail={
            "code": exc.code,
            "message_zh": exc.message_zh,
            "details": exc.details,
        },
    )


def _selection_payload(
    selection: PersonalModelSelection, versions: dict
) -> dict:
    def _number(version_id):
        version = versions.get(version_id)
        return version.version_number if version else None

    return {
        "selection_id": str(selection.id),
        "action": selection.action.value,
        "record_id": str(selection.record_id),
        "version_id": str(selection.version_id),
        "version_number": _number(selection.version_id),
        "previous_version_id": (
            str(selection.previous_version_id)
            if selection.previous_version_id
            else None
        ),
        "previous_version_number": _number(selection.previous_version_id),
        "comparison_run_id": (
            str(selection.comparison_run_id)
            if selection.comparison_run_id
            else None
        ),
        "rationale": selection.rationale,
        "created_at": selection.created_at.isoformat()
        if selection.created_at
        else None,
    }


@router.get("/models/comparability")
def personal_model_comparability(
    version_a: uuid.UUID,
    version_b: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    """Read-only comparability report. Never consumes quota."""
    va, ra = _owned_version(db, version_a, current_user)
    vb, rb = _owned_version(db, version_b, current_user)
    return compare_contracts(db, current_user.id, va, vb, ra, rb)


@router.post("/models/comparisons")
def create_personal_model_comparison(
    body: ComparisonRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    """Evaluate two versions on comparable frozen evidence.

    Consumes one ai_usage unit; a failed run is rolled back, so the reservation
    is released. A repeated ``operation_key`` returns the stored result without
    consuming quota again.
    """
    va, ra = _owned_version(db, body.version_a_id, current_user)
    vb, rb = _owned_version(db, body.version_b_id, current_user)
    canonical = _canonical_request(va, vb)

    def _conflict() -> HTTPException:
        return HTTPException(
            status_code=409,
            detail={
                "code": "operation_key_conflict",
                "message_zh": "此操作識別碼已用於另一組比較內容（版本、快照、artifact 或切分不同）。",
            },
        )

    if body.operation_key:
        existing = existing_run(db, current_user.id, body.operation_key)
        if existing is not None:
            if not requests_match(existing.request_json, canonical):
                raise _conflict()
            return {**existing.result_json, "idempotent_replay": True}

    report = compare_contracts(db, current_user.id, va, vb, ra, rb)
    if not report["comparable"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_comparable",
                "message_zh": "兩個版本不可比；請先處理列出的差異。",
                "reasons": report["not_comparable_reasons"],
                "checks": report["checks"],
            },
        )

    reservation = reserve_quota(db, current_user.id, "ai_usage")
    try:
        result = evaluate_pair(
            db,
            current_user.id,
            va,
            vb,
            ra,
            rb,
            operation_key=body.operation_key,
            actor_type=request_actor_provenance(request),
        )
    except ComparisonRefused as exc:
        # The reservation was never committed; rolling back releases it and
        # discards any partial write.
        db.rollback()
        raise _comparison_refused(exc)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "code": "comparison_failed",
                "message_zh": "比較失敗，已釋放本次額度，未寫入任何評估記錄。",
            },
        )

    settle_reservation(db, reservation.id)
    result["quota"] = {"quota_type": "ai_usage", "amount": 1, "settled": True}
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same operation key committed first.
        # This transaction — including the quota settlement — is discarded.
        db.rollback()
        if not body.operation_key:
            raise
        existing = existing_run(db, current_user.id, body.operation_key)
        if existing is None or not requests_match(existing.request_json, canonical):
            raise _conflict()
        return {**existing.result_json, "idempotent_replay": True}
    return result


@router.get("/comparisons")
def list_personal_comparisons(
    strategy_version_id: Optional[uuid.UUID] = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    capped = max(1, min(int(limit), 50))
    runs = (
        db.query(PersonalModelComparisonRun)
        .filter(PersonalModelComparisonRun.owner_id == current_user.id)
        .order_by(PersonalModelComparisonRun.created_at.desc())
        .limit(capped)
        .all()
    )
    version_ids = set()
    for run in runs:
        version_ids.update({run.version_a_id, run.version_b_id})
    versions = (
        {
            version.id: version
            for version in db.query(ModelVersion)
            .filter(ModelVersion.id.in_(version_ids))
            .all()
        }
        if version_ids
        else {}
    )

    out = []
    for run in runs:
        va = versions.get(run.version_a_id)
        vb = versions.get(run.version_b_id)
        if strategy_version_id:
            strategies = {
                str((version.model_specification_json or {}).get("strategy_version_id"))
                for version in (va, vb)
                if version is not None
            }
            if str(strategy_version_id) not in strategies:
                continue
        result = run.result_json or {}
        out.append(
            {
                "comparison_run_id": str(run.id),
                "record_id": str(run.record_id),
                "mode": run.mode,
                "created_at": run.created_at.isoformat()
                if run.created_at
                else None,
                "version_a": {
                    "model_version_id": str(run.version_a_id),
                    "version_number": va.version_number if va else None,
                },
                "version_b": {
                    "model_version_id": str(run.version_b_id),
                    "version_number": vb.version_number if vb else None,
                },
                "same_evaluation_set": result.get("same_evaluation_set"),
                "rankable": result.get("rankable"),
                "evaluation_purpose": result.get("evaluation_purpose"),
                "evaluation_purpose_zh": result.get("evaluation_purpose_zh"),
                "independent_out_of_sample": result.get("independent_out_of_sample"),
                "summary_zh": result.get("summary_zh"),
                "warnings": result.get("warnings") or [],
                "delta": result.get("delta"),
                "disagreement": result.get("disagreement"),
            }
        )
    return {"comparisons": out}


@router.get("/comparisons/{run_id}")
def get_personal_comparison(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    run = (
        db.query(PersonalModelComparisonRun)
        .filter(
            PersonalModelComparisonRun.id == run_id,
            PersonalModelComparisonRun.owner_id == current_user.id,
        )
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return run.result_json


@router.post("/models/{version_id}/select")
def select_personal_model(
    version_id: uuid.UUID,
    body: SelectionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    """Select an active version, re-checking every rule at this moment."""
    try:
        outcome = select_version(
            db,
            current_user.id,
            version_id=version_id,
            rationale=body.rationale,
            comparison_run_id=body.comparison_run_id,
            expected_active_version_id=body.expected_active_version_id,
        )
    except SelectionRefused as exc:
        db.rollback()
        raise _selection_refused(exc)

    record = outcome["record"]
    version = outcome["version"]
    previous = outcome["previous_version_id"]

    if outcome["already_active"]:
        db.commit()
        return {
            "already_active": True,
            "selection": None,
            "active_version": _version_payload(db, version, record),
            "comparison_evidence": outcome["comparison_evidence"],
            "message_zh": "此版本已是使用中版本，沒有改動。",
        }

    selection = outcome["selection"]
    emit_event(
        db,
        entity_type="model_research_record",
        entity_id=str(record.id),
        event_type="personal_model_version_selected",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "from_version_id": str(previous) if previous else None,
            "to_version_id": str(version.id),
            "to_version_number": version.version_number,
            "comparison_run_id": (
                str(body.comparison_run_id)
                if body.comparison_run_id
                else None
            ),
            "rationale_saved": True,
        },
    )
    db.commit()
    db.refresh(selection)
    db.refresh(version)
    db.refresh(record)

    versions = {version.id: version}
    if previous:
        previous_version = (
            db.query(ModelVersion).filter(ModelVersion.id == previous).first()
        )
        if previous_version:
            versions[previous_version.id] = previous_version

    return {
        "already_active": False,
        "selection": _selection_payload(selection, versions),
        "active_version": _version_payload(db, version, record),
        "comparison_evidence": outcome["comparison_evidence"],
        "message_zh": "已切換使用中版本；舊版本的 artifact、評估與歷史推論保持不變。",
    }


@router.post("/records/{record_id}/rollback")
def rollback_personal_model(
    record_id: uuid.UUID,
    body: RollbackRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*WRITE_SCOPES)),
    db: Session = Depends(get_db),
):
    """Return to the version active before the latest switch."""
    try:
        outcome = rollback_selection(
            db,
            current_user.id,
            record_id=record_id,
            rationale=body.rationale,
            expected_active_version_id=body.expected_active_version_id,
        )
    except SelectionRefused as exc:
        db.rollback()
        raise _selection_refused(exc)

    record = outcome["record"]
    target = outcome["version"]
    selection = outcome["selection"]
    current = outcome["previous_version_id"]

    emit_event(
        db,
        entity_type="model_research_record",
        entity_id=str(record.id),
        event_type="personal_model_version_rolled_back",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "from_version_id": str(current) if current else None,
            "to_version_id": str(target.id),
            "to_version_number": target.version_number,
        },
    )
    db.commit()
    db.refresh(selection)
    db.refresh(target)
    db.refresh(record)

    versions = {target.id: target}
    if current:
        current_version = (
            db.query(ModelVersion).filter(ModelVersion.id == current).first()
        )
        if current_version:
            versions[current_version.id] = current_version

    return {
        "selection": _selection_payload(selection, versions),
        "active_version": _version_payload(db, target, record),
        "message_zh": "已回退到上一個版本；沒有刪除任何版本、artifact 或歷史推論。",
    }


@router.get("/records/{record_id}/selections")
def get_personal_selections(
    record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope(*READ_SCOPES)),
    db: Session = Depends(get_db),
):
    record = _owned_record(db, record_id, current_user)
    return {
        "record_id": str(record.id),
        "active_version_id": (
            str(record.active_version_id) if record.active_version_id else None
        ),
        "selections": list_selections(db, current_user.id, record.id),
    }
