"""Train and run one bounded screening model from a frozen dataset snapshot.

The chain has three explicit steps and each one re-verifies the previous:

1. ``train_from_snapshot`` reads only a ``ready`` snapshot, recomputes its
   checksum from the stored record, and refuses when the record no longer
   matches. It fits on the frozen ``train`` split only and deliberately never
   touches the validation or test labels while fitting.
2. The artifact records every lineage field needed to prove what produced it.
   ``activation_status`` stays ``NOT_APPROVED``; this module cannot activate a
   model.
3. ``run_inference`` re-verifies the artifact checksum, the dataset checksum
   and the full lineage before returning a take/skip estimate. It writes
   nothing.

Two independent authorizations are required to reach step 1: the development
training fixture and the owner's own recorded consent. Neither is a customer
release. This is a development-only code path over fixture data; the numbers it
produces are not a profit claim and not a human-quality evaluation.
"""

import hashlib
import json
import math
import os
import uuid
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.preprocessing import StandardScaler

from app.lib.timeutil import utcnow
from app.ml.trainer import ARTIFACTS_DIR
from app.models.agent_strategy import (
    AgentDatasetSnapshot,
    AgentStrategyTrainingJob,
)
from app.services.agent_strategy import (
    EXECUTION_POLICY_VERSION,
    LABEL_DEFINITION_VERSION,
    PURPOSE,
    SPLIT_POLICY_VERSION,
    AgentStrategyRefused,
    _stored_checksum,
    training_authorization,
)

AGENT_ARTIFACTS_DIR = os.path.join(ARTIFACTS_DIR, "agent_strategy")
TRAINING_CONTRACT_VERSION = "agent-strategy-training-contract-v1"
DERIVED_LABEL_VERSION = "positive-net-return-v1"
INFERENCE_POLICY_VERSION = "snapshot-artifact-verified-v1"
TASK_TYPE = PURPOSE

HYPERPARAMETERS: Dict[str, Any] = {
    "model_type": "logistic_regression",
    "C": 1.0,
    "solver": "lbfgs",
    "max_iter": 200,
    "random_state": 42,
    "scaler": "standard",
    "positive_probability_threshold": 0.5,
    "label_rule": "net_return_pct > 0",
    "derived_label_version": DERIVED_LABEL_VERSION,
    "split_policy_version": SPLIT_POLICY_VERSION,
}

RANDOM_SKIP_SEED = 42
CLASS_LABELS = [0, 1]
CLASS_NAMES = {0: "skip", 1: "take"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":")
    )


def hyperparameters_hash(hyperparameters: Optional[Dict[str, Any]] = None) -> str:
    return hashlib.sha256(
        _canonical_json(hyperparameters or HYPERPARAMETERS).encode("utf-8")
    ).hexdigest()


def _safe_agent_dir(job_id: str) -> str:
    if not job_id or os.sep in job_id or "/" in job_id or ".." in job_id:
        raise ValueError("Invalid agent training job ID")
    base = os.path.realpath(AGENT_ARTIFACTS_DIR)
    job_dir = os.path.realpath(os.path.join(base, job_id))
    if not job_dir.startswith(base + os.sep):
        raise ValueError("Invalid agent training job ID")
    return job_dir


def _feature_matrix(
    rows: List[Dict[str, Any]], feature_names: List[str]
) -> np.ndarray:
    values: List[List[float]] = []
    for row in rows:
        features = row.get("features") or {}
        missing = [name for name in feature_names if name not in features]
        if missing:
            raise AgentStrategyRefused(
                "invalid_snapshot_features",
                "快照資料列缺少模型需要的特徵欄位。",
                {"missing": missing, "candidate_id": row.get("candidate_id")},
            )
        vector: List[float] = []
        for name in feature_names:
            value = features.get(name)
            if value is None or not math.isfinite(float(value)):
                raise AgentStrategyRefused(
                    "invalid_snapshot_features",
                    "快照特徵含非有限值，拒絕訓練。",
                    {
                        "feature": name,
                        "candidate_id": row.get("candidate_id"),
                    },
                )
            vector.append(float(value))
        values.append(vector)
    return np.array(values, dtype=float)


def _label(row: Dict[str, Any]) -> int:
    value = row.get("net_return_pct")
    if value is None:
        raise AgentStrategyRefused(
            "invalid_snapshot_label",
            "快照資料列沒有可用的 net_return_pct 標籤。",
            {"candidate_id": row.get("candidate_id")},
        )
    return 1 if float(value) > 0 else 0


def _segment_metrics(
    rows: List[Dict[str, Any]],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    probabilities: np.ndarray,
) -> Dict[str, Any]:
    returns = np.array([float(row["net_return_pct"]) for row in rows], dtype=float)
    taken = probabilities >= HYPERPARAMETERS["positive_probability_threshold"]
    return {
        "n_rows": int(len(rows)),
        "positive_count": int(sum(y_true)),
        "negative_count": int(len(y_true) - sum(y_true)),
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "macro_f1": round(
            float(
                f1_score(
                    y_true, y_pred, labels=CLASS_LABELS, average="macro",
                    zero_division=0,
                )
            ),
            6,
        ),
        "coverage": round(float(taken.mean()), 6) if len(rows) else 0.0,
        "rejection_count": int((~taken).sum()),
        "model_take_total_net_return_pct": round(
            float(returns[taken].sum()), 6
        ),
        "net_return_pct_mean": round(float(returns.mean()), 6)
        if len(returns)
        else None,
    }


def _baseline_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = np.array([float(row["net_return_pct"]) for row in rows], dtype=float)
    rng = np.random.default_rng(RANDOM_SKIP_SEED)
    random_take = rng.random(len(rows)) >= 0.5
    return {
        "take_all": {
            "coverage": 1.0,
            "rejection_count": 0,
            "total_net_return_pct": round(float(returns.sum()), 6),
        },
        "skip_all": {
            "coverage": 0.0,
            "rejection_count": len(rows),
            "total_net_return_pct": 0.0,
        },
        "random_skip_seed_42": {
            "seed": RANDOM_SKIP_SEED,
            "skip_probability": 0.5,
            "coverage": round(float(random_take.mean()), 6) if len(rows) else 0.0,
            "rejection_count": int((~random_take).sum()),
            "total_net_return_pct": round(
                float(returns[random_take].sum()), 6
            ),
        },
    }


def _split_rows(
    snapshot: AgentDatasetSnapshot,
) -> Dict[str, List[Dict[str, Any]]]:
    manifest = snapshot.split_manifest_json or {}
    splits = manifest.get("splits") or {}
    by_id = {row["candidate_id"]: row for row in (snapshot.rows_json or [])}
    result: Dict[str, List[Dict[str, Any]]] = {}
    for name in ("train", "validation", "test"):
        ids = ((splits.get(name) or {}).get("candidate_ids")) or []
        missing = [candidate_id for candidate_id in ids if candidate_id not in by_id]
        if missing:
            raise AgentStrategyRefused(
                "snapshot_split_inconsistent",
                "快照的切分引用了不存在的資料列。",
                {"split": name, "missing": missing[:10]},
            )
        result[name] = [by_id[candidate_id] for candidate_id in ids]
    return result


def _authorize(db, owner_id: uuid.UUID, settings=None) -> Dict[str, Any]:
    authorization = training_authorization(db, owner_id, settings)
    if not authorization["granted"]:
        raise AgentStrategyRefused(
            "agent_strategy_training_not_authorized",
            "訓練需要 owner 本人已記錄的同意，以及一項授權基礎"
            "（開發測試 fixture，或本機 owner 試用模式）；兩者皆具備才會執行。",
            {
                "reason_code": authorization["reason_code"],
                "fixture_granted": authorization["fixture_granted"],
                "owner_consent_mode": authorization["owner_consent_mode"],
                "authorization_mode": authorization["authorization_mode"],
                "consent_status": authorization["consent_status"],
                "model_activation": authorization["model_activation"],
            },
        )
    return authorization


def _load_ready_snapshot(db, owner_id: uuid.UUID, snapshot_id: uuid.UUID):
    snapshot = (
        db.query(AgentDatasetSnapshot)
        .filter(
            AgentDatasetSnapshot.id == snapshot_id,
            AgentDatasetSnapshot.owner_id == owner_id,
        )
        .first()
    )
    if snapshot is None:
        raise AgentStrategyRefused(
            "snapshot_not_found",
            "找不到屬於此帳號的資料集快照。",
            {"snapshot_id": str(snapshot_id)},
        )
    if snapshot.status != "ready":
        raise AgentStrategyRefused(
            "snapshot_not_ready",
            "只有 ready 快照可以訓練；拒絕紀錄不是訓練集。",
            {"snapshot_id": str(snapshot_id), "status": snapshot.status},
        )
    recomputed = _stored_checksum(snapshot)
    if recomputed != snapshot.checksum:
        raise AgentStrategyRefused(
            "snapshot_checksum_mismatch",
            "由快照紀錄重算的 checksum 與凍結值不符，拒絕訓練。",
            {
                "snapshot_id": str(snapshot_id),
                "stored": snapshot.checksum,
                "recomputed": recomputed,
            },
        )
    return snapshot


def _job_payload(job: AgentStrategyTrainingJob) -> Dict[str, Any]:
    return {
        "job_id": str(job.id),
        "snapshot_id": str(job.snapshot_id),
        "strategy_version_id": str(job.strategy_version_id),
        "status": job.status,
        "training_contract_version": job.training_contract_version,
        "derived_label_version": job.derived_label_version,
        "dataset_checksum": job.dataset_checksum,
        "hyperparameters": job.hyperparameters_json,
        "hyperparameters_hash": job.hyperparameters_hash,
        "row_counts": job.row_counts_json,
        "metrics": job.metrics_json,
        "lineage": job.lineage_json,
        "artifact_path": job.artifact_path,
        "artifact_checksum": job.artifact_checksum,
        "artifact_metadata_checksum": job.artifact_metadata_checksum,
        "activation_status": job.activation_status,
        "failure_reason": job.failure_reason,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat()
        if job.completed_at
        else None,
        "model_activated": False,
        "runs_inference_for_customer": False,
        "movable_to_customer_environment": False,
    }


def _find_existing_job(
    db,
    owner_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    hp_hash: str,
) -> Optional[AgentStrategyTrainingJob]:
    return (
        db.query(AgentStrategyTrainingJob)
        .filter(
            AgentStrategyTrainingJob.owner_id == owner_id,
            AgentStrategyTrainingJob.snapshot_id == snapshot_id,
            AgentStrategyTrainingJob.hyperparameters_hash == hp_hash,
            AgentStrategyTrainingJob.status == "completed",
        )
        .first()
    )


def train_from_snapshot(
    db,
    owner_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    settings=None,
) -> Dict[str, Any]:
    """Fit one model on a frozen snapshot's train split and persist the job."""
    authorization = _authorize(db, owner_id, settings)
    hp_hash = hyperparameters_hash()
    existing = _find_existing_job(db, owner_id, snapshot_id, hp_hash)
    if existing is not None:
        payload = _job_payload(existing)
        payload["created"] = False
        payload["deduplicated"] = True
        return payload

    snapshot = _load_ready_snapshot(db, owner_id, snapshot_id)
    feature_names = list(snapshot.feature_names_json or [])
    if not feature_names:
        raise AgentStrategyRefused(
            "invalid_snapshot_features",
            "快照沒有記錄任何特徵欄位，拒絕訓練。",
            {"snapshot_id": str(snapshot_id)},
        )

    split_rows = _split_rows(snapshot)
    train_rows = split_rows["train"]
    if not train_rows:
        raise AgentStrategyRefused(
            "empty_train_split",
            "train 切分沒有樣本，拒絕訓練。",
            {"snapshot_id": str(snapshot_id)},
        )

    X_train = _feature_matrix(train_rows, feature_names)
    y_train = np.array([_label(row) for row in train_rows], dtype=int)
    if len(set(y_train.tolist())) < 2:
        raise AgentStrategyRefused(
            "single_class_train_segment",
            "train 時段只有一個結果類別，無法訓練分類模型。",
            {"train_classes": sorted(set(y_train.tolist()))},
        )

    # The scaler is fit on the train segment only; validation and test are
    # transformed with those frozen statistics.
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)

    model = LogisticRegression(
        C=HYPERPARAMETERS["C"],
        solver=HYPERPARAMETERS["solver"],
        max_iter=HYPERPARAMETERS["max_iter"],
        random_state=HYPERPARAMETERS["random_state"],
    )
    model.fit(X_train_scaled, y_train)

    seg_metrics: Dict[str, Any] = {}
    for name, rows in split_rows.items():
        if not rows:
            seg_metrics[name] = {"n_rows": 0, "available": False}
            continue
        X_seg = scaler.transform(_feature_matrix(rows, feature_names))
        y_true = np.array([_label(row) for row in rows], dtype=int)
        y_pred = model.predict(X_seg)
        proba = model.predict_proba(X_seg)[:, list(model.classes_).index(1)]
        seg_metrics[name] = {
            "available": True,
            **_segment_metrics(rows, y_true, y_pred, proba),
        }

    test_rows = split_rows["test"]
    metrics: Dict[str, Any] = {
        "task_type": TASK_TYPE,
        "label_rule": HYPERPARAMETERS["label_rule"],
        "derived_label_version": DERIVED_LABEL_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "segments": seg_metrics,
        "baselines": {
            "test": _baseline_metrics(test_rows) if test_rows else {},
        },
        "not_a_profit_claim": True,
        "not_human_quality_evaluation": True,
        "market_data_nature_verified": False,
    }

    job_id = uuid.uuid4()
    job_dir = _safe_agent_dir(str(job_id))
    os.makedirs(job_dir, exist_ok=True)
    import joblib

    joblib.dump(model, os.path.join(job_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(job_dir, "scaler.joblib"))
    with open(os.path.join(job_dir, "feature_names.json"), "w") as fh:
        json.dump(feature_names, fh)

    artifact_checksum_hex = hashlib.sha256(
        b"".join(
            open(os.path.join(job_dir, filename), "rb").read()
            for filename in ("model.joblib", "scaler.joblib")
        )
    ).hexdigest()

    lineage: Dict[str, Any] = {
        "snapshot_id": str(snapshot.id),
        "dataset_checksum": snapshot.checksum,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "label_definition_version": LABEL_DEFINITION_VERSION,
        "training_contract_version": TRAINING_CONTRACT_VERSION,
        "hyperparameters_hash": hp_hash,
        "consent_record_id": authorization.get("consent_record_id"),
        "artifact_metadata_checksum": None,
        "model_artifact_checksum": artifact_checksum_hex,
    }

    metadata: Dict[str, Any] = {
        "job_id": str(job_id),
        "task_type": TASK_TYPE,
        "artifact_scope": "agent_candidate_screening_model",
        "owner_id": str(owner_id),
        "snapshot_id": str(snapshot.id),
        "dataset_checksum": snapshot.checksum,
        "strategy_version_id": str(snapshot.strategy_version_id),
        "training_contract_version": TRAINING_CONTRACT_VERSION,
        "derived_label_version": DERIVED_LABEL_VERSION,
        "label_definition_version": LABEL_DEFINITION_VERSION,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "inference_policy_version": INFERENCE_POLICY_VERSION,
        "artifact_sha256": artifact_checksum_hex,
        "feature_names": feature_names,
        "classes": CLASS_LABELS,
        "hyperparameters": HYPERPARAMETERS,
        "hyperparameters_hash": hp_hash,
        "row_counts": {
            name: len(rows) for name, rows in split_rows.items()
        },
        "metrics": metrics,
        "authorization_mode": authorization["authorization_mode"],
        "real_owner_consent": authorization["real_owner_consent"],
        "model_activation": "NOT_APPROVED",
        "movable_to_customer_environment": False,
        "usable_for_customer": False,
        "trained_at": utcnow().isoformat(),
        "not_for": [
            "保證利潤",
            "市場方向預測",
            "投資建議",
            "自動實盤",
            "已啟用的客戶功能",
        ],
        "wording_zh": "此模型只作候選訊號 take／skip 的開發期研究輔助",
    }
    metadata_checksum = hashlib.sha256(
        _canonical_json(metadata).encode("utf-8")
    ).hexdigest()
    metadata["artifact_metadata_checksum"] = metadata_checksum
    lineage["artifact_metadata_checksum"] = metadata_checksum
    with open(os.path.join(job_dir, "metadata.json"), "w") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)

    job = AgentStrategyTrainingJob(
        id=job_id,
        owner_id=owner_id,
        snapshot_id=snapshot.id,
        strategy_version_id=snapshot.strategy_version_id,
        status="completed",
        training_contract_version=TRAINING_CONTRACT_VERSION,
        derived_label_version=DERIVED_LABEL_VERSION,
        dataset_checksum=snapshot.checksum,
        hyperparameters_json=HYPERPARAMETERS,
        hyperparameters_hash=hp_hash,
        row_counts_json={name: len(rows) for name, rows in split_rows.items()},
        metrics_json=metrics,
        lineage_json=lineage,
        artifact_path=os.path.join(job_dir, "model.joblib"),
        artifact_checksum=artifact_checksum_hex,
        artifact_metadata_checksum=metadata_checksum,
        activation_status="NOT_APPROVED",
        completed_at=utcnow(),
    )
    try:
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
        existing = _find_existing_job(db, owner_id, snapshot_id, hp_hash)
        if existing is not None:
            payload = _job_payload(existing)
            payload["created"] = False
            payload["deduplicated"] = True
            payload["concurrent_insert_collapsed"] = True
            return payload
        raise
    db.refresh(job)
    payload = _job_payload(job)
    payload["created"] = True
    payload["deduplicated"] = False
    return payload


def _verify_artifact(job: AgentStrategyTrainingJob) -> Dict[str, Any]:
    if not job.artifact_path or not os.path.isfile(job.artifact_path):
        raise AgentStrategyRefused(
            "artifact_missing",
            "找不到此訓練工作的模型 artifact。",
            {"job_id": str(job.id)},
        )
    job_dir = os.path.dirname(job.artifact_path)
    metadata_path = os.path.join(job_dir, "metadata.json")
    try:
        with open(metadata_path) as fh:
            metadata = json.load(fh)
    except (OSError, ValueError):
        raise AgentStrategyRefused(
            "artifact_metadata_missing",
            "模型 artifact 缺少可讀的中繼資料。",
            {"job_id": str(job.id)},
        )
    if metadata.get("task_type") != TASK_TYPE:
        raise AgentStrategyRefused(
            "artifact_scope_mismatch",
            "artifact 不屬於代理候選篩選任務。",
            {"job_id": str(job.id)},
        )
    computed_metadata_checksum = hashlib.sha256(
        _canonical_json(
            {
                key: value
                for key, value in metadata.items()
                if key != "artifact_metadata_checksum"
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        computed_metadata_checksum != job.artifact_metadata_checksum
        or metadata.get("artifact_metadata_checksum")
        != job.artifact_metadata_checksum
    ):
        raise AgentStrategyRefused(
            "artifact_metadata_checksum_mismatch",
            "artifact 中繼資料 checksum 與訓練紀錄不符，拒絕推論。",
            {"job_id": str(job.id)},
        )
    computed_artifact = hashlib.sha256(
        b"".join(
            open(os.path.join(job_dir, filename), "rb").read()
            for filename in ("model.joblib", "scaler.joblib")
        )
    ).hexdigest()
    if computed_artifact != job.artifact_checksum:
        raise AgentStrategyRefused(
            "artifact_checksum_mismatch",
            "artifact 內容 checksum 與訓練紀錄不符，拒絕推論。",
            {
                "job_id": str(job.id),
                "stored": job.artifact_checksum,
                "recomputed": computed_artifact,
            },
        )
    return metadata


def _validate_training_lineage(job: AgentStrategyTrainingJob) -> Dict[str, Any]:
    from app.services.agent_strategy import training_contract

    required = training_contract()["lineage_required_training"]
    lineage = job.lineage_json or {}
    missing = [name for name in required if not lineage.get(name)]
    if missing:
        raise AgentStrategyRefused(
            "training_lineage_incomplete",
            "訓練工作的血緣欄位不完整，拒絕推論。",
            {"job_id": str(job.id), "missing": missing},
        )
    return {"valid": True, "missing": [], "lineage": lineage}


def run_inference(
    db,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    features: Dict[str, Any],
    *,
    settings=None,
) -> Dict[str, Any]:
    """Score one candidate in memory; re-verify dataset and artifact first."""
    _authorize(db, owner_id, settings)
    job = (
        db.query(AgentStrategyTrainingJob)
        .filter(
            AgentStrategyTrainingJob.id == job_id,
            AgentStrategyTrainingJob.owner_id == owner_id,
        )
        .first()
    )
    if job is None:
        raise AgentStrategyRefused(
            "training_job_not_found",
            "找不到屬於此帳號的訓練工作。",
            {"job_id": str(job_id)},
        )
    if job.status != "completed":
        raise AgentStrategyRefused(
            "training_job_not_completed",
            "只有完成的訓練工作可以推論。",
            {"job_id": str(job_id), "status": job.status},
        )

    snapshot = _load_ready_snapshot(db, owner_id, job.snapshot_id)
    if snapshot.checksum != job.dataset_checksum:
        raise AgentStrategyRefused(
            "dataset_checksum_mismatch",
            "訓練工作的資料集 checksum 與目前快照不符，拒絕推論。",
            {
                "job_id": str(job_id),
                "trained_on": job.dataset_checksum,
                "snapshot": snapshot.checksum,
            },
        )
    lineage_check = _validate_training_lineage(job)
    metadata = _verify_artifact(job)

    if str(metadata.get("snapshot_id")) != str(snapshot.id):
        raise AgentStrategyRefused(
            "artifact_snapshot_mismatch",
            "artifact 記錄的快照與工作不符，拒絕推論。",
            {"job_id": str(job_id)},
        )
    if metadata.get("dataset_checksum") != job.dataset_checksum:
        raise AgentStrategyRefused(
            "artifact_dataset_mismatch",
            "artifact 記錄的資料集 checksum 與工作不符，拒絕推論。",
            {"job_id": str(job_id)},
        )

    feature_names = list(metadata.get("feature_names") or [])
    missing = [name for name in feature_names if name not in features]
    if missing:
        raise AgentStrategyRefused(
            "missing_features",
            "推論輸入缺少模型需要的特徵欄位。",
            {"missing": missing},
        )
    try:
        vector = np.array(
            [[float(features[name]) for name in feature_names]], dtype=float
        )
    except (TypeError, ValueError):
        raise AgentStrategyRefused(
            "invalid_features",
            "推論輸入含非數值特徵。",
            {"job_id": str(job_id)},
        )
    if not np.all(np.isfinite(vector)):
        raise AgentStrategyRefused(
            "invalid_features",
            "推論輸入含非有限值。",
            {"job_id": str(job_id)},
        )

    import joblib

    job_dir = os.path.dirname(job.artifact_path)
    model = joblib.load(os.path.join(job_dir, "model.joblib"))
    scaler = joblib.load(os.path.join(job_dir, "scaler.joblib"))
    scaled = scaler.transform(vector)
    probabilities = model.predict_proba(scaled)[0]
    class_list = list(model.classes_)
    positive_index = class_list.index(1) if 1 in class_list else None
    probability = float(probabilities[positive_index]) if positive_index is not None else 0.0
    decision = (
        "take"
        if probability >= HYPERPARAMETERS["positive_probability_threshold"]
        else "skip"
    )
    return {
        "job_id": str(job.id),
        "snapshot_id": str(job.snapshot_id),
        "decision": decision,
        "positive_probability": round(probability, 6),
        "threshold": HYPERPARAMETERS["positive_probability_threshold"],
        "feature_names": feature_names,
        "inference_policy_version": INFERENCE_POLICY_VERSION,
        "training_contract_version": job.training_contract_version,
        "dataset_checksum": job.dataset_checksum,
        "artifact_checksum": job.artifact_checksum,
        "lineage_verified": lineage_check["valid"],
        "artifact_verified": True,
        "activation_status": "NOT_APPROVED",
        "model_activated": False,
        "runs_inference_for_customer": False,
        "not_a_recommendation": True,
        "not_a_profit_claim": True,
    }


def list_training_jobs(
    db, owner_id: uuid.UUID, *, snapshot_id: Optional[uuid.UUID] = None
) -> Dict[str, Any]:
    query = db.query(AgentStrategyTrainingJob).filter(
        AgentStrategyTrainingJob.owner_id == owner_id
    )
    if snapshot_id is not None:
        query = query.filter(AgentStrategyTrainingJob.snapshot_id == snapshot_id)
    jobs = query.order_by(AgentStrategyTrainingJob.created_at.desc()).all()
    return {
        "jobs": [_job_payload(job) for job in jobs],
        "count": len(jobs),
        "activation_available": False,
        "model_activation": "NOT_APPROVED",
    }


def get_training_job(
    db, owner_id: uuid.UUID, job_id: uuid.UUID
) -> Dict[str, Any]:
    job = (
        db.query(AgentStrategyTrainingJob)
        .filter(
            AgentStrategyTrainingJob.id == job_id,
            AgentStrategyTrainingJob.owner_id == owner_id,
        )
        .first()
    )
    if job is None:
        raise AgentStrategyRefused(
            "training_job_not_found",
            "找不到屬於此帳號的訓練工作。",
            {"job_id": str(job_id)},
        )
    return _job_payload(job)
