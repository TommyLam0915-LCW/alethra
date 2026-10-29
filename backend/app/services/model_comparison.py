"""Compare two personal decision models on evidence that can be compared.

Two versions are only comparable when they answer the same question with the
same label contract, the same feature schema, the same strategy scope and the
same evaluation method. When they also share one frozen dataset snapshot they
are evaluated on *the same* test rows; otherwise each is evaluated on its own
frozen test window and the result is explicitly not a ranking.

Nothing here retrains, rewrites a split boundary, promotes a model, or reads
live market data. Rows come from the snapshot the model was trained on; the
artifact file is hashed before use, so the metrics are bound to the exact bytes
that produced them. Every persisted evaluation records the artifact hash,
snapshot checksum, feature/label contract, split, parameters and creator.
"""

import hashlib
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    SMALL_CLASS_SUPPORT,
    SMALL_TEST_THRESHOLD,
    _class_metrics,
    _split_group_key,
    _safe_personal_dir,
    load_personal_model,
    _predict_with,
)
from app.models.model_research import (
    EvaluationType,
    EvaluatorType,
    ModelEvaluation,
    ModelResearchRecord,
    ModelType,
    ModelVersion,
)
from app.models.personal_dataset import (
    PersonalizationDatasetSnapshot,
    PersonalizationSnapshotStatus,
)
from app.models.personal_model_selection import (
    PersonalModelComparisonRun,
    PersonalModelSelection,
    PersonalModelSelectionAction,
)
from app.services import emit_event
from app.services.personal_dataset import (
    LABEL_DEFINITION,
    _canonical_checksum,
)

COMPARISON_ENGINE_VERSION = "personal-comparison-v1"
MAX_COMPARISON_WINDOWS = 3
SMALL_WINDOW_ROWS = 5
ARTIFACT_FILES = ("model.joblib", "scaler.joblib")

NOT_FOR = [
    "市場方向預測",
    "交易是否正確或有利潤",
    "投資建議",
]

CAVEATS_ZH = [
    "決策模仿分數唔係投資回報，亦唔係市場方向預測。",
    "測試集樣本少或者缺少類別嗰陣，準確率同 macro F1 嘅差距可能只係噪音。",
    "若之前用同一批資料挑選過版本，今次結果唔構成獨立效能證明。",
]

SAME_SET_SUMMARY_ZH = (
    "兩個版本在同一凍結測試集上比較；分數只反映模仿本人已確認決策的能力，"
    "不代表回報、市場方向或越用越準。"
)
PER_VERSION_SUMMARY_ZH = (
    "兩個版本各自在自己嘅凍結測試集上評估；兩邊測試列唔相同，"
    "只可以並列參考，唔可以當作同一評估集排名。"
)


class ComparisonRefused(ValueError):
    """A comparison the product must refuse (not a server error)."""

    def __init__(self, code: str, message_zh: str, details: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}


# ---------------------------------------------------------------------------
# Identity and contract
# ---------------------------------------------------------------------------


def _artifact_digest(artifact_id: str) -> str:
    try:
        base = _safe_personal_dir(artifact_id)
    except ValueError:
        raise ComparisonRefused("artifact_missing", "模型 artifact 識別碼無效。")
    digest = hashlib.sha256()
    for name in ARTIFACT_FILES:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            raise ComparisonRefused(
                "artifact_missing", "模型 artifact 已不存在，未執行比較。"
            )
        with open(path, "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()


def _snapshot_for(
    db: Session, owner_id: uuid.UUID, version: ModelVersion, spec: dict
) -> PersonalizationDatasetSnapshot:
    raw = spec.get("snapshot_id")
    if not raw:
        raise ComparisonRefused(
            "snapshot_missing", "此模型版本沒有可追溯的訓練資料快照。"
        )
    try:
        snapshot_id = uuid.UUID(str(raw))
    except (ValueError, TypeError):
        raise ComparisonRefused("snapshot_missing", "此模型版本的快照識別碼無效。")
    snapshot = (
        db.query(PersonalizationDatasetSnapshot)
        .filter(
            PersonalizationDatasetSnapshot.id == snapshot_id,
            PersonalizationDatasetSnapshot.owner_id == owner_id,
        )
        .first()
    )
    if snapshot is None:
        raise ComparisonRefused(
            "snapshot_not_found", "找不到此帳號擁有的訓練資料快照。"
        )
    if snapshot.status is not PersonalizationSnapshotStatus.ready:
        raise ComparisonRefused("snapshot_not_ready", "此快照不是可評估狀態。")

    recomputed = _canonical_checksum(
        snapshot.rows_json or [],
        snapshot.feature_names_json or [],
        LABEL_DEFINITION,
    )
    if recomputed != snapshot.checksum:
        raise ComparisonRefused(
            "snapshot_checksum_mismatch", "快照內容與記錄的 checksum 不一致。"
        )
    if spec.get("snapshot_checksum") != snapshot.checksum:
        raise ComparisonRefused(
            "snapshot_identity_mismatch",
            "模型版本記錄的快照 checksum 與快照本體不一致。",
        )
    return snapshot


def _label_contract(snapshot: PersonalizationDatasetSnapshot) -> dict:
    definition = dict(snapshot.label_definition_json or {})
    return {
        "task_type": definition.get("task_type"),
        "label_source": definition.get("label_source"),
        "classes": sorted(definition.get("classes") or []),
    }


def _nature_values(snapshot: PersonalizationDatasetSnapshot) -> set:
    definition = dict(snapshot.label_definition_json or {})
    nature = definition.get("data_nature_by_instrument") or {}
    return {value for values in nature.values() for value in values}


def _split_contract(spec: dict) -> dict:
    split = spec.get("split") or {}
    return {
        "kind": split.get("kind"),
        "group_aware": bool(split.get("group_aware")),
        "group_key": split.get("group_key"),
        "train_ratio": split.get("train_ratio"),
        "val_ratio": split.get("val_ratio"),
        "train_size": (split.get("train") or {}).get("size"),
        "val_size": (split.get("val") or {}).get("size"),
        "test_size": (split.get("test") or {}).get("size"),
        "test_start_event_id": (split.get("test") or {}).get("start_event_id"),
        "test_end_event_id": (split.get("test") or {}).get("end_event_id"),
        "boundaries": split.get("boundaries") or {},
    }


def _check(code: str, ok: bool, message_zh: str, detail: Any = None, required: bool = True) -> dict:
    return {
        "code": code,
        "ok": bool(ok),
        "required": required,
        "message_zh": message_zh,
        "detail": detail,
    }


def _same_evaluation_set(spec_a: dict, spec_b: dict, snapshot_a, snapshot_b) -> bool:
    if snapshot_a.checksum != snapshot_b.checksum:
        return False
    contract_a = _split_contract(spec_a)
    contract_b = _split_contract(spec_b)
    keys = ("kind", "group_aware", "test_size", "boundaries", "test_start_event_id")
    return all(contract_a.get(key) == contract_b.get(key) for key in keys)


def compare_contracts(
    db: Session,
    owner_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    record_a: ModelResearchRecord,
    record_b: ModelResearchRecord,
    *,
    load_snapshots: bool = True,
) -> Dict[str, Any]:
    """Read-only comparability report. Never consumes quota."""
    spec_a = version_a.model_specification_json or {}
    spec_b = version_b.model_specification_json or {}

    checks: List[dict] = []

    personal_a = (
        version_a.model_type is ModelType.personal_decision_model
        and spec_a.get("task_type") == PERSONAL_TASK_TYPE
    )
    personal_b = (
        version_b.model_type is ModelType.personal_decision_model
        and spec_b.get("task_type") == PERSONAL_TASK_TYPE
    )
    checks.append(
        _check(
            "personal_decision_model",
            personal_a and personal_b,
            "兩邊都必須是以個人決策訓練的模型版本。",
            {"a": version_a.model_type.value, "b": version_b.model_type.value},
        )
    )

    checks.append(
        _check(
            "same_model_record",
            record_a.id == record_b.id,
            "兩個版本必須屬於同一個個人模型研究記錄。",
            {"a": str(record_a.id), "b": str(record_b.id)},
        )
    )

    checks.append(
        _check(
            "same_task",
            spec_a.get("task_type") == spec_b.get("task_type"),
            "兩個版本必須回答同一個任務。",
            {"a": spec_a.get("task_type"), "b": spec_b.get("task_type")},
        )
    )

    checks.append(
        _check(
            "same_strategy_scope",
            str(spec_a.get("strategy_version_id"))
            == str(spec_b.get("strategy_version_id")),
            "兩個版本必須屬於同一個策略版本範圍。",
            {
                "a": spec_a.get("strategy_version_id"),
                "b": spec_b.get("strategy_version_id"),
            },
        )
    )

    classes_a = sorted(spec_a.get("classes") or [])
    classes_b = sorted(spec_b.get("classes") or [])
    checks.append(
        _check(
            "same_classes",
            bool(classes_a) and classes_a == classes_b,
            "兩個版本的動作類別必須相同。",
            {"a": classes_a, "b": classes_b},
        )
    )

    features_a = list(spec_a.get("feature_names") or [])
    features_b = list(spec_b.get("feature_names") or [])
    checks.append(
        _check(
            "same_feature_schema",
            bool(features_a) and features_a == features_b,
            "兩個版本的特徵欄位與次序必須完全一致。",
            {"a_count": len(features_a), "b_count": len(features_b)},
        )
    )

    split_a = _split_contract(spec_a)
    split_b = _split_contract(spec_b)
    checks.append(
        _check(
            "same_evaluation_method",
            split_a["kind"] == split_b["kind"]
            and split_a["group_aware"] == split_b["group_aware"]
            and split_a["train_ratio"] == split_b["train_ratio"]
            and split_a["val_ratio"] == split_b["val_ratio"],
            "兩個版本必須使用同一種評估方法（時間切分、群組規則與比例）。",
            {"a": split_a, "b": split_b},
        )
    )

    checks.append(
        _check(
            "artifacts_available",
            bool(spec_a.get("artifact_id")) and bool(spec_b.get("artifact_id")),
            "兩個版本都必須有可讀取的模型 artifact。",
            {
                "a": bool(spec_a.get("artifact_id")),
                "b": bool(spec_b.get("artifact_id")),
            },
        )
    )

    snapshot_a = snapshot_b = None
    snapshot_error = None
    if load_snapshots:
        try:
            snapshot_a = _snapshot_for(db, owner_id, version_a, spec_a)
        except ComparisonRefused as exc:
            snapshot_error = {"version": "a", "code": exc.code, "message_zh": exc.message_zh}
        try:
            snapshot_b = _snapshot_for(db, owner_id, version_b, spec_b)
        except ComparisonRefused as exc:
            snapshot_error = snapshot_error or {
                "version": "b",
                "code": exc.code,
                "message_zh": exc.message_zh,
            }

    checks.append(
        _check(
            "snapshots_available",
            snapshot_a is not None and snapshot_b is not None,
            "兩個版本都必須有可追溯且 checksum 一致的凍結資料快照。",
            snapshot_error,
        )
    )

    label_a = _label_contract(snapshot_a) if snapshot_a else None
    label_b = _label_contract(snapshot_b) if snapshot_b else None
    checks.append(
        _check(
            "same_label_contract",
            label_a is not None
            and label_b is not None
            and label_a == label_b
            and label_a.get("label_source") == "owner_confirmed_decision",
            "兩個版本的標籤定義與來源必須相同（本人已確認決策）。",
            {"a": label_a, "b": label_b},
        )
    )

    same_set = (
        snapshot_a is not None
        and snapshot_b is not None
        and _same_evaluation_set(spec_a, spec_b, snapshot_a, snapshot_b)
    )
    checks.append(
        _check(
            "same_evaluation_set",
            same_set,
            "兩邊共用同一凍結快照與同一測試切分，可在同一批測試列上比較。",
            {
                "a_checksum": snapshot_a.checksum if snapshot_a else None,
                "b_checksum": snapshot_b.checksum if snapshot_b else None,
                "a_test_size": split_a["test_size"],
                "b_test_size": split_b["test_size"],
            },
            required=False,
        )
    )

    revoked = [v for v in (version_a, version_b) if v.revoked_at is not None]
    checks.append(
        _check(
            "no_revoked_versions",
            not revoked,
            "有版本已被撤銷；只可作研究參考，不可以被選為使用中版本。",
            [str(v.id) for v in revoked],
            required=False,
        )
    )

    reasons = [
        {
            "code": check["code"],
            "message_zh": check["message_zh"],
            "detail": check["detail"],
        }
        for check in checks
        if check["required"] and not check["ok"]
    ]

    return {
        "comparable": not reasons,
        "same_evaluation_set": same_set,
        "checks": checks,
        "not_comparable_reasons": reasons,
        "versions": {
            "a": _version_identity(version_a, spec_a, snapshot_a, split_a),
            "b": _version_identity(version_b, spec_b, snapshot_b, split_b),
        },
        "quota": {"quota_type": "ai_usage", "amount": 0},
        "note_zh": "可比性檢查只讀取已保存的版本與快照資料，不扣額度、不訓練、不改動任何版本。",
    }


def _version_identity(
    version: ModelVersion,
    spec: dict,
    snapshot: Optional[PersonalizationDatasetSnapshot],
    split: dict,
) -> dict:
    return {
        "model_version_id": str(version.id),
        "record_id": str(version.model_research_record_id),
        "version_number": version.version_number,
        "model_type": spec.get("model_type"),
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "revoked_at": version.revoked_at.isoformat() if version.revoked_at else None,
        "artifact_id": spec.get("artifact_id"),
        "artifact_sha256": spec.get("artifact_sha256"),
        "snapshot_id": str(snapshot.id) if snapshot else spec.get("snapshot_id"),
        "snapshot_checksum": spec.get("snapshot_checksum"),
        "feature_schema_version": (
            (snapshot.label_definition_json or {}).get("feature_schema_version")
            if snapshot
            else None
        ),
        "feature_names": list(spec.get("feature_names") or []),
        "classes": sorted(spec.get("classes") or []),
        "split": split,
        "data_cutoff_at": snapshot.data_cutoff_at if snapshot else None,
        "date_range": (
            {"start": snapshot.date_range_start, "end": snapshot.date_range_end}
            if snapshot
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Evaluation on frozen rows
# ---------------------------------------------------------------------------


def _ordered_rows(rows: List[dict]) -> List[dict]:
    return sorted(
        rows,
        key=lambda row: (
            _split_group_key(row),
            row.get("decided_at") or "",
            row.get("decision_event_id") or "",
        ),
    )


def _segment_bounds(spec: dict) -> Tuple[int, int, int]:
    split = spec.get("split") or {}
    train_size = int((split.get("train") or {}).get("size") or 0)
    val_size = int((split.get("val") or {}).get("size") or 0)
    test_size = int((split.get("test") or {}).get("size") or 0)
    return train_size, val_size, test_size


def _frozen_slices(
    version: ModelVersion, snapshot: PersonalizationDatasetSnapshot
) -> Dict[str, Any]:
    spec = version.model_specification_json or {}
    ordered = _ordered_rows(list(snapshot.rows_json or []))
    train_size, val_size, test_size = _segment_bounds(spec)
    if len(ordered) != train_size + val_size + test_size:
        raise ComparisonRefused(
            "snapshot_row_count_mismatch",
            "快照資料列數與模型版本記錄的切分不一致，拒絕在未經驗證的資料上評估。",
            {
                "rows": len(ordered),
                "split_total": train_size + val_size + test_size,
                "model_version_id": str(version.id),
            },
        )
    start = train_size + val_size
    test_rows = ordered[start : start + test_size]
    if len(test_rows) != test_size:
        raise ComparisonRefused(
            "evaluation_slice_mismatch",
            "無法重建此版本記錄的測試切分。",
            {"model_version_id": str(version.id)},
        )
    recorded_start = ((spec.get("split") or {}).get("test") or {}).get("start_event_id")
    if recorded_start and str(test_rows[0]["decision_event_id"]) != str(recorded_start):
        raise ComparisonRefused(
            "evaluation_slice_mismatch",
            "重建的測試切分起點與模型版本記錄不一致，拒絕繼續。",
            {
                "model_version_id": str(version.id),
                "recorded": recorded_start,
                "rebuilt": test_rows[0]["decision_event_id"],
            },
        )
    return {
        "ordered": ordered,
        "train_rows": ordered[:train_size],
        "test_rows": test_rows,
    }


def _most_frequent(labels: List[str]) -> Optional[str]:
    if not labels:
        return None
    counts: Dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _windows(test_rows: List[dict], predictions: List[str], labels: List[str]) -> List[dict]:
    """Up to three contiguous chronological windows over whole decision groups.

    Window boundaries are derived from the frozen rows only; the original
    train/validation/test boundaries are never rewritten.
    """
    if not test_rows:
        return []

    groups: List[dict] = []
    previous_key: Optional[str] = None
    for index, row in enumerate(test_rows):
        key = _split_group_key(row)
        if groups and key == previous_key:
            groups[-1]["end"] = index + 1
            groups[-1]["n_rows"] += 1
        else:
            groups.append({"start": index, "end": index + 1, "n_rows": 1})
        previous_key = key

    window_count = min(MAX_COMPARISON_WINDOWS, len(groups))
    total = len(test_rows)
    buckets: List[List[dict]] = [[] for _ in range(window_count)]
    for group in groups:
        # Map each group by its first row position; contiguous groups keep the
        # buckets contiguous even when group sizes are very uneven.
        bucket = min(int(group["start"] * window_count / total), window_count - 1)
        buckets[bucket].append(group)
    buckets = [bucket for bucket in buckets if bucket]

    out: List[dict] = []
    for number, bucket in enumerate(buckets, start=1):
        start = bucket[0]["start"]
        end = bucket[-1]["end"]
        rows = test_rows[start:end]
        predicted = predictions[start:end]
        truth = np.array([row["action_label"] for row in rows])
        pred = np.array(predicted)
        metrics = _class_metrics(truth, pred, labels)
        present = set(truth.tolist())
        predicted_set = set(pred.tolist())
        missing = [label for label in labels if label not in present]
        reasons: List[str] = []
        if len(rows) < SMALL_WINDOW_ROWS:
            reasons.append("small_window")
        if missing:
            reasons.append("missing_class_in_window")
        out.append(
            {
                "index": number,
                "start_decision_instant": rows[0].get("decision_instant"),
                "end_decision_instant": rows[-1].get("decision_instant"),
                "start_decided_at": rows[0].get("decided_at"),
                "end_decided_at": rows[-1].get("decided_at"),
                "n_rows": len(rows),
                "n_groups": len(bucket),
                "class_distribution": metrics["class_distribution"],
                "predicted_distribution": {
                    label: predicted.count(label) for label in sorted(predicted_set)
                },
                "accuracy": metrics["accuracy"],
                "macro_f1": metrics["macro_f1"],
                "per_class": metrics["per_class"],
                "insufficient": bool(reasons),
                "insufficient_reasons": reasons,
            }
        )
    return out


def _warnings(
    test_rows: List[dict],
    metrics: dict,
    labels: List[str],
    nature: set,
) -> List[str]:
    warnings: List[str] = []
    if len(test_rows) < SMALL_TEST_THRESHOLD:
        warnings.append("small_test_set")
    if any(
        metrics["per_class"][label]["support"] < SMALL_CLASS_SUPPORT
        for label in labels
    ):
        warnings.append("small_class_support")
    present = {row["action_label"] for row in test_rows}
    if any(label not in present for label in labels):
        warnings.append("missing_class_in_test")
    if "synthetic" in nature:
        warnings.append("synthetic_market_features")
    if "mixed" in nature:
        warnings.append("mixed_market_features")
    return warnings


def _load_and_predict(
    owner_id: uuid.UUID, version: ModelVersion, test_rows: List[dict]
) -> Tuple[Any, Any, str, dict, List[str]]:
    spec = version.model_specification_json or {}
    artifact_id = spec.get("artifact_id")
    if not artifact_id:
        raise ComparisonRefused(
            "artifact_missing", "此模型版本沒有可用的 artifact。"
        )
    digest = _artifact_digest(artifact_id)
    recorded = spec.get("artifact_sha256")
    if not recorded or digest != recorded:
        raise ComparisonRefused(
            "artifact_checksum_mismatch",
            "模型 artifact 內容與版本記錄的 hash 不一致，拒絕使用。",
            {
                "model_version_id": str(version.id),
                "recorded": recorded,
                "actual": digest,
            },
        )
    try:
        model, scaler, metadata, feature_names = load_personal_model(
            artifact_id, owner_id=str(owner_id)
        )
    except FileNotFoundError:
        raise ComparisonRefused("artifact_missing", "模型 artifact 已不存在。")
    except PermissionError:
        raise ComparisonRefused("artifact_not_owned", "模型 artifact 不屬於此帳號。")
    except ValueError:
        raise ComparisonRefused(
            "artifact_invalid", "模型 artifact 不是個人決策模型。"
        )

    expected_features = list(spec.get("feature_names") or [])
    if feature_names != expected_features:
        raise ComparisonRefused(
            "artifact_feature_mismatch",
            "模型 artifact 的特徵欄位與版本記錄不一致。",
        )
    expected_classes = sorted(spec.get("classes") or [])
    artifact_classes = sorted(str(value) for value in getattr(model, "classes_", []))
    if artifact_classes != expected_classes:
        raise ComparisonRefused(
            "artifact_class_mismatch",
            "模型 artifact 的類別與版本記錄不一致。",
        )
    if metadata.get("snapshot_checksum") != spec.get("snapshot_checksum"):
        raise ComparisonRefused(
            "artifact_snapshot_mismatch",
            "模型 artifact 記錄的訓練快照與版本不一致。",
        )

    predictions: List[str] = []
    for row in test_rows:
        features = row.get("features") or {}
        missing = [name for name in feature_names if name not in features]
        if missing:
            raise ComparisonRefused(
                "missing_features",
                "快照資料列缺少模型需要的特徵欄位。",
                {"missing": missing, "decision_event_id": row.get("decision_event_id")},
            )
        predictions.append(
            _predict_with(model, scaler, feature_names, features)["predicted_action"]
        )
    return model, scaler, digest, metadata, predictions


def _evaluate_version(
    owner_id: uuid.UUID,
    version: ModelVersion,
    snapshot: PersonalizationDatasetSnapshot,
) -> Dict[str, Any]:
    spec = version.model_specification_json or {}
    slices = _frozen_slices(version, snapshot)
    test_rows = slices["test_rows"]
    labels = sorted(spec.get("classes") or [])

    _model, _scaler, digest, metadata, predictions = _load_and_predict(
        owner_id, version, test_rows
    )

    truth = np.array([row["action_label"] for row in test_rows])
    pred = np.array(predictions)
    metrics = _class_metrics(truth, pred, labels)

    baseline_label = _most_frequent(
        [row["action_label"] for row in slices["train_rows"]]
    )
    baseline_metrics = (
        _class_metrics(truth, np.array([baseline_label] * len(test_rows)), labels)
        if baseline_label
        else None
    )

    windows = _windows(test_rows, predictions, labels)
    warnings = _warnings(test_rows, metrics, labels, _nature_values(snapshot))

    evaluation_set = {
        "snapshot_id": str(snapshot.id),
        "snapshot_checksum": snapshot.checksum,
        "n_rows": snapshot.included_count,
        "train_size": len(slices["train_rows"]),
        "test_size": len(test_rows),
        "test_start_decision_instant": test_rows[0].get("decision_instant"),
        "test_end_decision_instant": test_rows[-1].get("decision_instant"),
        "test_start_event_id": test_rows[0].get("decision_event_id"),
        "test_end_event_id": test_rows[-1].get("decision_event_id"),
        "date_range": {
            "start": snapshot.date_range_start,
            "end": snapshot.date_range_end,
        },
        "data_cutoff_at": snapshot.data_cutoff_at,
        "feature_schema_version": (
            (snapshot.label_definition_json or {}).get("feature_schema_version")
        ),
    }

    return {
        "identity": {
            "model_version_id": str(version.id),
            "record_id": str(version.model_research_record_id),
            "version_number": version.version_number,
            "artifact_id": spec.get("artifact_id"),
            "artifact_sha256": digest,
            "snapshot_id": evaluation_set["snapshot_id"],
            "snapshot_checksum": evaluation_set["snapshot_checksum"],
            "strategy_version_id": spec.get("strategy_version_id"),
            "feature_schema_version": evaluation_set["feature_schema_version"],
            "feature_names": list(spec.get("feature_names") or []),
            "classes": labels,
            "label_contract": _label_contract(snapshot),
            "split": _split_contract(spec),
            "execution_parameters": {
                "model_type": spec.get("model_type"),
                "engine_version": COMPARISON_ENGINE_VERSION,
                "max_windows": MAX_COMPARISON_WINDOWS,
            },
            "trained_at": metadata.get("trained_at"),
            "created_by": str(owner_id),
        },
        "evaluation_set": evaluation_set,
        "metrics": metrics,
        "baseline": (
            {
                "label": baseline_label,
                "accuracy": baseline_metrics["accuracy"],
                "macro_f1": baseline_metrics["macro_f1"],
                "note_zh": "基線＝訓練時段最常見的動作類別，在同一測試列上評估。",
            }
            if baseline_metrics
            else None
        ),
        "windows": windows,
        "warnings": warnings,
        "predictions": predictions,
        "test_rows": test_rows,
    }


def _public_version_payload(
    version: ModelVersion, evaluated: Dict[str, Any], is_active: bool
) -> dict:
    identity = evaluated["identity"]
    metrics = evaluated["metrics"]
    return {
        "model_version_id": identity["model_version_id"],
        "version_number": identity["version_number"],
        "model_type": identity["execution_parameters"]["model_type"],
        "artifact_sha256": identity["artifact_sha256"],
        "artifact_verified": True,
        "snapshot_id": identity["snapshot_id"],
        "snapshot_checksum": identity["snapshot_checksum"],
        "trained_at": identity["trained_at"],
        "is_active": is_active,
        "revoked": version.revoked_at is not None,
        "evaluation_set": evaluated["evaluation_set"],
        "metrics": {
            "test_size": metrics["n_samples"],
            "accuracy": metrics["accuracy"],
            "macro_f1": metrics["macro_f1"],
            "weighted_f1": metrics["weighted_f1"],
            "per_class": metrics["per_class"],
            "confusion_matrix": metrics["confusion_matrix"],
            "class_distribution": metrics["class_distribution"],
        },
        "baseline": evaluated["baseline"],
        "windows": [
            {key: value for key, value in window.items()}
            for window in evaluated["windows"]
        ],
        "warnings": evaluated["warnings"],
    }


def _disagreement(
    test_rows: List[dict], predictions_a: List[str], predictions_b: List[str]
) -> dict:
    total = len(test_rows)
    differing = [
        index
        for index, (a, b) in enumerate(zip(predictions_a, predictions_b))
        if a != b
    ]
    return {
        "count": len(differing),
        "share": round(len(differing) / total, 4) if total else 0.0,
        "n_rows": total,
        "note_zh": "不一致＝兩個版本在同一測試列上給出不同動作；不代表哪一個正確。",
    }


def _persist_evaluations(
    db: Session,
    *,
    run_id: uuid.UUID,
    owner_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    evaluated_a: dict,
    evaluated_b: dict,
    mode: str,
    started_at,
) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    summary = SAME_SET_SUMMARY_ZH if mode == "same_evaluation_set" else PER_VERSION_SUMMARY_ZH
    for role, version, evaluated, peer in (
        ("a", version_a, evaluated_a, version_b),
        ("b", version_b, evaluated_b, version_a),
    ):
        metrics_json = {
            "comparison_run_id": str(run_id),
            "mode": mode,
            "role": role,
            "peer_model_version_id": str(peer.id),
            "identity": evaluated["identity"],
            "evaluation_set": evaluated["evaluation_set"],
            "metrics": evaluated["metrics"],
            "baseline": evaluated["baseline"],
            "per_window": evaluated["windows"],
            "disagreement": None,
            "warnings": evaluated["warnings"],
            "created_at": started_at.isoformat(),
            "not_for": NOT_FOR,
        }
        evaluation = ModelEvaluation(
            model_version_id=version.id,
            model_research_record_id=version.model_research_record_id,
            evaluation_type=EvaluationType.model_comparison,
            result_summary=summary,
            metrics_json=metrics_json,
            warning_flags=evaluated["warnings"],
            evaluation_date=started_at,
            data_cutoff_at=evaluated["evaluation_set"]["data_cutoff_at"]
            or evaluated["evaluation_set"]["date_range"]["end"]
            or "",
            engine_version=COMPARISON_ENGINE_VERSION,
            evaluator=EvaluatorType.system,
            methodology_version=COMPARISON_ENGINE_VERSION,
        )
        db.add(evaluation)
        db.flush()
        ids[role] = str(evaluation.id)
    return ids


def existing_run(
    db: Session, owner_id: uuid.UUID, operation_key: str
) -> Optional[PersonalModelComparisonRun]:
    return (
        db.query(PersonalModelComparisonRun)
        .filter(
            PersonalModelComparisonRun.owner_id == owner_id,
            PersonalModelComparisonRun.operation_key == operation_key,
        )
        .first()
    )


def run_id_for(owner_id: uuid.UUID, operation_key: Optional[str]) -> uuid.UUID:
    if not operation_key:
        return uuid.uuid4()
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"alethra:comparison:{owner_id}:{operation_key}"
    )


def evaluate_pair(
    db: Session,
    owner_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    record_a: ModelResearchRecord,
    record_b: ModelResearchRecord,
    *,
    operation_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare two versions and persist the result. No quota handling here."""
    if version_a.id == version_b.id:
        raise ComparisonRefused(
            "same_version", "請選擇兩個不同的模型版本作比較。"
        )

    report = compare_contracts(
        db, owner_id, version_a, version_b, record_a, record_b
    )
    if not report["comparable"]:
        raise ComparisonRefused(
            "not_comparable",
            "兩個版本不可比；請先處理列出的差異。",
            {"reasons": report["not_comparable_reasons"]},
        )

    spec_a = version_a.model_specification_json or {}
    spec_b = version_b.model_specification_json or {}
    snapshot_a = _snapshot_for(db, owner_id, version_a, spec_a)
    snapshot_b = _snapshot_for(db, owner_id, version_b, spec_b)

    same_set = report["same_evaluation_set"]
    run_id = run_id_for(owner_id, operation_key)
    started_at = utcnow()

    if same_set:
        slices_a = _frozen_slices(version_a, snapshot_a)
        slices_b = _frozen_slices(version_b, snapshot_b)
        test_ids_a = [row["decision_event_id"] for row in slices_a["test_rows"]]
        test_ids_b = [row["decision_event_id"] for row in slices_b["test_rows"]]
        if test_ids_a != test_ids_b:
            raise ComparisonRefused(
                "evaluation_slice_mismatch",
                "兩個版本記錄的測試列不一致，拒絕把它們當作同一評估集。",
            )

    evaluated_a = _evaluate_version(owner_id, version_a, snapshot_a)
    evaluated_b = _evaluate_version(owner_id, version_b, snapshot_b)

    is_active_a = record_a.active_version_id == version_a.id
    is_active_b = record_b.active_version_id == version_b.id

    payload_a = _public_version_payload(version_a, evaluated_a, is_active_a)
    payload_b = _public_version_payload(version_b, evaluated_b, is_active_b)

    delta = None
    disagreement = None
    if same_set:
        metrics_a = evaluated_a["metrics"]
        metrics_b = evaluated_b["metrics"]
        delta = {
            "accuracy": round(metrics_a["accuracy"] - metrics_b["accuracy"], 4),
            "macro_f1": round(metrics_a["macro_f1"] - metrics_b["macro_f1"], 4),
            "direction": "a_minus_b",
            "note_zh": "差值只在同一評估集上有意義；樣本少時可能只是噪音。",
        }
        disagreement = _disagreement(
            evaluated_a["test_rows"],
            evaluated_a["predictions"],
            evaluated_b["predictions"],
        )

    warnings = sorted(set(evaluated_a["warnings"]) | set(evaluated_b["warnings"]))
    caveats = list(CAVEATS_ZH)
    if same_set:
        caveats.append("兩邊共用同一凍結快照與同一測試列。")
    else:
        caveats.append(
            "兩邊測試列唔相同：只可以並列參考，唔可以宣稱邊個版本更好。"
        )
    if warnings:
        caveats.append("存在資料量或類別覆蓋警告，解讀時要一併考慮。")

    public_a = payload_a
    public_b = payload_b

    result = {
        "comparison_run_id": str(run_id),
        "mode": "same_evaluation_set" if same_set else "per_version_test_window",
        "comparable": True,
        "same_evaluation_set": same_set,
        "rankable": same_set,
        "checks": report["checks"],
        "versions": {"a": public_a, "b": public_b},
        "delta": delta,
        "disagreement": disagreement,
        "warnings": warnings,
        "caveats_zh": caveats,
        "summary_zh": SAME_SET_SUMMARY_ZH if same_set else PER_VERSION_SUMMARY_ZH,
        "evaluation_record_ids": {},
        "created_at": started_at.isoformat(),
        "operation_key": operation_key,
        "quota": {"quota_type": "ai_usage", "amount": 1},
        "not_for": NOT_FOR,
    }

    evaluation_ids = _persist_evaluations(
        db,
        run_id=run_id,
        owner_id=owner_id,
        version_a=version_a,
        version_b=version_b,
        evaluated_a=evaluated_a,
        evaluated_b=evaluated_b,
        mode=result["mode"],
        started_at=started_at,
    )
    result["evaluation_record_ids"] = evaluation_ids
    result["versions"]["a"]["evaluation_record_id"] = evaluation_ids["a"]
    result["versions"]["b"]["evaluation_record_id"] = evaluation_ids["b"]

    run = PersonalModelComparisonRun(
        id=run_id,
        owner_id=owner_id,
        record_id=record_a.id,
        version_a_id=version_a.id,
        version_b_id=version_b.id,
        mode=result["mode"],
        operation_key=operation_key,
        request_json={
            "version_a_id": str(version_a.id),
            "version_b_id": str(version_b.id),
        },
        result_json=result,
        created_at=started_at,
    )
    db.add(run)

    emit_event(
        db,
        entity_type="model_research_record",
        entity_id=str(record_a.id),
        event_type="personal_model_comparison_created",
        actor_id=owner_id,
        payload={
            "comparison_run_id": str(run_id),
            "version_a_id": str(version_a.id),
            "version_b_id": str(version_b.id),
            "mode": result["mode"],
            "same_evaluation_set": same_set,
            "evaluation_record_ids": evaluation_ids,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Selection history
# ---------------------------------------------------------------------------


def record_selection(
    db: Session,
    owner_id: uuid.UUID,
    record: ModelResearchRecord,
    version: ModelVersion,
    *,
    rationale: str,
    comparison_run_id: Optional[uuid.UUID],
    previous_version_id: Optional[uuid.UUID] = None,
) -> PersonalModelSelection:
    if version.model_research_record_id != record.id:
        raise ComparisonRefused(
            "version_not_in_record", "此版本不屬於指定的模型記錄。"
        )
    selection = PersonalModelSelection(
        owner_id=owner_id,
        record_id=record.id,
        version_id=version.id,
        previous_version_id=previous_version_id,
        comparison_run_id=comparison_run_id,
        action=PersonalModelSelectionAction.select,
        rationale=rationale,
    )
    db.add(selection)
    return selection


def latest_selection(
    db: Session, owner_id: uuid.UUID, record_id: uuid.UUID
) -> Optional[PersonalModelSelection]:
    return (
        db.query(PersonalModelSelection)
        .filter(
            PersonalModelSelection.owner_id == owner_id,
            PersonalModelSelection.record_id == record_id,
        )
        .order_by(
            PersonalModelSelection.created_at.desc(),
            PersonalModelSelection.id.desc(),
        )
        .first()
    )


def list_selections(
    db: Session, owner_id: uuid.UUID, record_id: uuid.UUID, limit: int = 25
) -> List[dict]:
    rows = (
        db.query(PersonalModelSelection)
        .filter(
            PersonalModelSelection.owner_id == owner_id,
            PersonalModelSelection.record_id == record_id,
        )
        .order_by(
            PersonalModelSelection.created_at.desc(),
            PersonalModelSelection.id.desc(),
        )
        .limit(limit)
        .all()
    )
    versions = {
        version.id: version
        for version in db.query(ModelVersion)
        .filter(ModelVersion.id.in_([row.version_id for row in rows] + [row.previous_version_id for row in rows if row.previous_version_id]))
        .all()
    } if rows else {}

    def _number(version_id):
        version = versions.get(version_id)
        return version.version_number if version else None

    return [
        {
            "selection_id": str(row.id),
            "action": row.action.value,
            "version_id": str(row.version_id),
            "version_number": _number(row.version_id),
            "previous_version_id": (
                str(row.previous_version_id) if row.previous_version_id else None
            ),
            "previous_version_number": _number(row.previous_version_id),
            "comparison_run_id": (
                str(row.comparison_run_id) if row.comparison_run_id else None
            ),
            "rationale": row.rationale,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
