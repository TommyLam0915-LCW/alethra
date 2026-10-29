"""Queue and execute personal decision-model training jobs.

Reuses the durable TrainingJob table, worker, quota reservation and model
versioning. What is new is the job type: a personal decision model trains on a
frozen dataset snapshot (owner-confirmed decisions), never on OHLCV direction
labels.

Post-revocation policy is conservative: a job created while consent was
active is refused at execution if the consent has since been revoked. The
already-trained artifact is left untouched — revocation stops new training;
it is never claimed to erase what a model learned.
"""

import json
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    PersonalTrainingRefused,
    train_personal_model,
)
from app.models.model_research import (
    EvaluationType,
    EvaluatorType,
    ModelEvaluation,
    ModelResearchRecord,
    ModelStatus,
    ModelType,
    ModelVersion,
)
from app.models.personal_dataset import (
    PersonalizationDatasetSnapshot,
    PersonalizationSnapshotStatus,
)
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.user import User
from app.services import emit_event
from app.services.personal_dataset import LABEL_DEFINITION, _canonical_checksum
from app.services.personalization import get_active_consent, _scope_strategy_ids

PERSONAL_TRAINING_TYPE = "personal_decision_model"
PERSONAL_PURPOSE_ZH = (
    "以 owner 親自確認的決策訓練專屬模型，估計此 owner 在指定策略下會選擇的動作。"
    "不是市場方向預測，也不是交易建議。"
)
ENGINE_VERSION = "personal-trainer-v1"


def _active_consent_covers(db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID) -> bool:
    consent = get_active_consent(db, owner_id)
    return consent is not None and strategy_version_id in set(
        _scope_strategy_ids(consent)
    )


def get_owned_snapshot(
    db: Session, snapshot_id: uuid.UUID, owner_id: uuid.UUID
) -> PersonalizationDatasetSnapshot:
    snapshot = (
        db.query(PersonalizationDatasetSnapshot)
        .filter(PersonalizationDatasetSnapshot.id == snapshot_id)
        .first()
    )
    if snapshot is None or snapshot.owner_id != owner_id:
        raise PersonalTrainingRefused(
            "snapshot_not_found", "找不到此帳號擁有的訓練資料快照。"
        )
    return snapshot


def create_personal_training_job(
    db: Session,
    *,
    user: User,
    snapshot: PersonalizationDatasetSnapshot,
    quota_reservation_id: Optional[uuid.UUID] = None,
    model_type: str = "logistic_regression",
) -> TrainingJob:
    if snapshot.owner_id != user.id:
        raise PersonalTrainingRefused("snapshot_not_owned", "快照不屬於此帳號。")
    if snapshot.status is not PersonalizationSnapshotStatus.ready:
        raise PersonalTrainingRefused("snapshot_not_ready", "此快照不是可訓練狀態。")
    if not _active_consent_covers(db, user.id, snapshot.strategy_version_id):
        raise PersonalTrainingRefused(
            "consent_revoked",
            "此策略版本的個人化訓練同意已撤銷，不能開始新的相關訓練。",
        )

    spec: Dict[str, Any] = {
        "training_type": PERSONAL_TRAINING_TYPE,
        "snapshot_id": str(snapshot.id),
        "snapshot_checksum": snapshot.checksum,
        "strategy_version_id": str(snapshot.strategy_version_id),
        "model_type": model_type,
        "task_type": PERSONAL_TASK_TYPE,
    }
    if quota_reservation_id is not None:
        spec["quota_reservation_id"] = str(quota_reservation_id)

    job = TrainingJob(
        user_id=user.id,
        experiment_id=None,
        status=TrainingJobStatus.pending,
        parameter_grid={"task_type": [PERSONAL_TASK_TYPE], "model_type": [model_type]},
        strategy_spec_snapshot=spec,
    )
    db.add(job)
    db.flush()
    return job


def _record_for(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> ModelResearchRecord:
    record = (
        db.query(ModelResearchRecord)
        .filter(
            ModelResearchRecord.owner_id == owner_id,
            ModelResearchRecord.title == _record_title(strategy_version_id),
        )
        .first()
    )
    if record:
        return record

    record = ModelResearchRecord(
        owner_id=owner_id,
        title=_record_title(strategy_version_id),
        description=(
            "由個人化訓練工作產生。每個版本對應一個不可變的資料快照與 "
            "scikit-learn artifact。"
        ),
        purpose=PERSONAL_PURPOSE_ZH,
        status=ModelStatus.researching,
        allowed_research_actions=["predict_personal_decision"],
    )
    db.add(record)
    db.flush()
    return record


def _record_title(strategy_version_id: uuid.UUID) -> str:
    return f"個人決策模型 · {strategy_version_id}"


def _verify_snapshot_checksum(snapshot: PersonalizationDatasetSnapshot) -> None:
    recomputed = _canonical_checksum(
        snapshot.rows_json or [],
        snapshot.feature_names_json or [],
        LABEL_DEFINITION,
    )
    if recomputed != snapshot.checksum:
        raise PersonalTrainingRefused(
            "snapshot_checksum_mismatch",
            "快照內容與記錄的 checksum 不一致，已拒絕訓練。",
        )


def execute_personal_training(db: Session, job_id: uuid.UUID) -> TrainingJob:
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise ValueError(f"Training job {job_id} not found")
    if job.status not in (TrainingJobStatus.pending, TrainingJobStatus.running):
        raise ValueError(f"Job must be pending or running, got {job.status}")

    # The cancel endpoint marks a running job with cancelled_at but leaves the
    # status running for the sweep loop to notice. This trainer has no sweep
    # loop, so it must notice here: do not train, do not consume quota.
    if job.cancelled_at is not None:
        job.status = TrainingJobStatus.cancelled
        job.completed_at = utcnow()
        db.flush()
        return job

    job.status = TrainingJobStatus.running
    db.flush()

    try:
        spec = dict(job.strategy_spec_snapshot or {})
        if spec.get("training_type") != PERSONAL_TRAINING_TYPE:
            raise PersonalTrainingRefused(
                "wrong_job_type", "此工作不是個人決策模型訓練。"
            )

        snapshot = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(PersonalizationDatasetSnapshot.id == uuid.UUID(spec["snapshot_id"]))
            .first()
        )
        if snapshot is None or snapshot.owner_id != job.user_id:
            raise PersonalTrainingRefused(
                "snapshot_not_found", "找不到此工作的訓練資料快照。"
            )
        if snapshot.status is not PersonalizationSnapshotStatus.ready:
            raise PersonalTrainingRefused(
                "snapshot_not_ready", "快照不是可訓練狀態。"
            )

        # Conservative post-revocation policy: no new training after revoke.
        if not _active_consent_covers(db, job.user_id, snapshot.strategy_version_id):
            raise PersonalTrainingRefused(
                "consent_revoked_before_training",
                "訓練前發現同意已撤銷，未開始訓練；已存在的模型不會被改動。",
            )

        _verify_snapshot_checksum(snapshot)

        label_definition = dict(snapshot.label_definition_json or {})
        market_nature = label_definition.get("data_nature_by_instrument") or {}

        result = train_personal_model(
            snapshot.rows_json or [],
            snapshot.feature_names_json or [],
            owner_id=str(job.user_id),
            snapshot_id=str(snapshot.id),
            snapshot_checksum=snapshot.checksum,
            strategy_version_id=str(snapshot.strategy_version_id),
            label_definition=label_definition,
            training_job_id=str(job.id),
            model_type=spec.get("model_type", "logistic_regression"),
            market_data_nature=market_nature,
        )

        record = _record_for(db, job.user_id, snapshot.strategy_version_id)
        next_number = (
            db.query(ModelVersion)
            .filter(ModelVersion.model_research_record_id == record.id)
            .count()
            + 1
        )
        version = ModelVersion(
            model_research_record_id=record.id,
            version_number=next_number,
            model_type=ModelType.personal_decision_model,
            model_specification_json={
                "task_type": PERSONAL_TASK_TYPE,
                "artifact_id": result["model_id"],
                "artifact_sha256": result["artifact_sha256"],
                "snapshot_id": result["snapshot_id"],
                "snapshot_checksum": result["snapshot_checksum"],
                "strategy_version_id": result["strategy_version_id"],
                "training_job_id": str(job.id),
                "classes": result["classes"],
                "feature_names": result["feature_names"],
                "n_rows": result["n_rows"],
                "split": result["split"],
                "baseline": result.get("baseline"),
                "warnings": result["warnings"],
                "reload_verified": result["reload_verified"],
                "wording_zh": result["wording_zh"],
                "not_for": [
                    "市場方向預測",
                    "交易是否正確或有利潤",
                    "投資建議",
                ],
            },
            linked_dataset_snapshot_ids=[str(snapshot.id)],
        )
        db.add(version)
        db.flush()

        db.add(
            ModelEvaluation(
                model_version_id=version.id,
                model_research_record_id=record.id,
                evaluation_type=EvaluationType.historical,
                result_summary=(
                    "時間序列留出評估（chronological hold-out）。"
                    "分數只反映模仿本人已確認決策的能力，不代表回報或市場方向。"
                ),
                metrics_json={
                    "metrics": result["metrics"],
                    "split": result["split"],
                    "baseline": result["metrics"].get("baseline_test"),
                    "warnings": result["warnings"],
                    "n_rows": result["n_rows"],
                },
                warning_flags=result["warnings"],
                evaluation_date=utcnow(),
                data_cutoff_at=(
                    snapshot.data_cutoff_at or snapshot.date_range_end or ""
                ),
                engine_version=ENGINE_VERSION,
                evaluator=EvaluatorType.system,
                methodology_version=ENGINE_VERSION,
            )
        )

        emit_event(
            db,
            entity_type="model_version",
            entity_id=str(version.id),
            event_type="personal_model_version_created",
            actor_id=job.user_id,
            actor_type="system",
            payload={
                "record_id": str(record.id),
                "version_number": version.version_number,
                "snapshot_id": str(snapshot.id),
                "snapshot_checksum": snapshot.checksum,
                "artifact_id": result["model_id"],
                "artifact_sha256": result["artifact_sha256"],
                "training_job_id": str(job.id),
                "task_type": PERSONAL_TASK_TYPE,
            },
        )

        job.results = result
        job.best_params = {
            "model_id": result["model_id"],
            "task_type": PERSONAL_TASK_TYPE,
            "snapshot_id": str(snapshot.id),
            "model_version_id": str(version.id),
        }
        job.status = TrainingJobStatus.completed

    except PersonalTrainingRefused as exc:
        job.status = TrainingJobStatus.failed
        job.error_details = json.dumps(
            {"code": exc.code, "message_zh": exc.message_zh, "details": exc.details},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 - worker records any failure
        job.status = TrainingJobStatus.failed
        job.error_details = str(exc)

    job.completed_at = utcnow()
    db.flush()
    return job
