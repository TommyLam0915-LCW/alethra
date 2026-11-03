"""Trainer for the owner's personal decision model.

Task: estimate which action the owner would choose under a strategy, using
only features that were observable at the decision snapshot. It is not a
market-direction model, not a profit predictor and not an assessment of
whether a past decision was right.

Evaluation semantics kept explicit in the artifact:

* the split is a single chronological hold-out (train → validation → test),
  labelled ``chronological_holdout`` — not a fixed-strategy period study;
* the split is group-aware: rows sharing one decision instant stay whole in
  one segment, so a simultaneous decision is never half-train, half-test;
* an advisory expanding-window (walk-forward) re-evaluation is reported next
  to the hold-out, refitting scaler and estimator per fold with one whole
  decision group as a gap; it never replaces the hold-out and no gate reads it;
* hyper-parameters are fixed per model type, so no per-fold selection is
  claimed (``selection_source`` says so);
* the label is the action recorded at decision time, so no forward-looking
  label horizon exists and no cross-boundary label can leak into training;
* the segments are disjoint, so the test window is not double-counted as
  independent evidence (``test_windows_overlap: false``);
* feature warm-up bars precede the decision and are never counted as test
  performance.

Artifacts live under ``<ARTIFACTS_DIR>/personal/`` so the OHLCV market model
loader can never open a personal action model by ID.
"""

import hashlib
import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.preprocessing import StandardScaler

from app.lib.timeutil import utcnow
from app.ml.trainer import ARTIFACTS_DIR

PERSONAL_TASK_TYPE = "personal_decision_mimicry"
SPLIT_GROUP_KEY = "decision_instant"
PERSONAL_ARTIFACTS_DIR = os.path.join(ARTIFACTS_DIR, "personal")
PERSONAL_WORDING_ZH = "模型估計你在此策略下的決策選擇"

MIN_PERSONAL_ROWS = 20
MIN_TRAIN_ROWS = 6
MIN_VAL_ROWS = 2
MIN_TEST_ROWS = 2
SMALL_TEST_THRESHOLD = 30
SMALL_CLASS_SUPPORT = 5

# Walk-forward is an advisory re-evaluation, not the published gate. It keeps
# one whole decision-instant group as a gap so the boundary between training
# and test is never adjacent, and it refits the scaler and estimator per fold.
WALK_FORWARD_GAP_GROUPS = 1
WALK_FORWARD_INITIAL_TRAIN_RATIO = 0.5
WALK_FORWARD_MAX_FOLDS = 4
WALK_FORWARD_MIN_TEST_ROWS = 2


class PersonalTrainingRefused(ValueError):
    """A training request the product must refuse (not a server error)."""

    def __init__(self, code: str, message_zh: str, details: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}


def _safe_personal_dir(model_id: str) -> str:
    if not model_id or os.sep in model_id or "/" in model_id or ".." in model_id:
        raise ValueError("Invalid personal model ID")
    base = os.path.realpath(PERSONAL_ARTIFACTS_DIR)
    model_dir = os.path.realpath(os.path.join(base, model_id))
    if not model_dir.startswith(base + os.sep):
        raise ValueError("Invalid personal model ID")
    return model_dir


def _class_metrics(y_true: np.ndarray, y_pred: np.ndarray, labels: List[str]) -> Dict[str, Any]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    distribution: Dict[str, int] = {label: 0 for label in labels}
    for value in y_true:
        distribution[str(value)] = distribution.get(str(value), 0) + 1
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(
            float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
            4,
        ),
        "weighted_f1": round(
            float(
                f1_score(
                    y_true, y_pred, labels=labels, average="weighted", zero_division=0
                )
            ),
            4,
        ),
        "per_class": {
            label: {
                "precision": round(float(precision[i]), 4),
                "recall": round(float(recall[i]), 4),
                "f1": round(float(f1[i]), 4),
                "support": int(support[i]),
            }
            for i, label in enumerate(labels)
        },
        "confusion_matrix": {
            "labels": labels,
            "matrix": confusion_matrix(y_true, y_pred, labels=labels).tolist(),
        },
        "n_samples": int(len(y_true)),
        "class_distribution": distribution,
    }


def _matrix(rows: List[dict], feature_names: List[str]) -> np.ndarray:
    values = []
    for row in rows:
        missing = [name for name in feature_names if name not in row["features"]]
        if missing:
            raise PersonalTrainingRefused(
                "missing_features",
                "資料列缺少模型需要的特徵欄位。",
                {"missing": missing, "decision_event_id": row["decision_event_id"]},
            )
        values.append([float(row["features"][name]) for name in feature_names])
    return np.array(values)


def _split_group_key(row: dict) -> str:
    """Identity of the moment a decision was taken, oldest-first sortable.

    Rows from a snapshot carry the server-recorded ``decision_instant``. Rows
    handed to the trainer directly fall back to the snapshot date, then to the
    recording time, so simultaneous decisions still land in one group.
    """
    for field in (SPLIT_GROUP_KEY, "snapshot_date", "decided_at"):
        value = row.get(field)
        if value:
            return str(value)
    return str(row.get("decision_event_id"))


def _most_frequent_label(labels: List[str]) -> Optional[str]:
    if not labels:
        return None
    counts: Dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def grouped_temporal_split(
    group_sizes: List[int],
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> Tuple[List[int], List[int], List[int], Dict[str, int]]:
    """Chronological split that never cuts through one decision-instant group.

    ``group_sizes`` are ordered oldest-first. Each segment starts on a group
    boundary, so every row sharing an instant lands whole in one segment; a
    simultaneous decision cannot be half training evidence and half test.
    """
    total = sum(group_sizes)
    train_target = total * train_ratio
    val_target = total * (train_ratio + val_ratio)

    train_groups: List[int] = []
    val_groups: List[int] = []
    test_groups: List[int] = []
    start = 0
    for number, size in enumerate(group_sizes):
        if start < train_target:
            train_groups.append(number)
        elif start < val_target:
            val_groups.append(number)
        else:
            test_groups.append(number)
        start += size

    bounds: List[int] = []
    cursor = 0
    for size in group_sizes:
        bounds.append(cursor)
        cursor += size

    def _rows(groups: List[int]) -> List[int]:
        indices: List[int] = []
        for number in groups:
            indices.extend(range(bounds[number], bounds[number] + group_sizes[number]))
        return indices

    return (
        _rows(train_groups),
        _rows(val_groups),
        _rows(test_groups),
        {"train": len(train_groups), "val": len(val_groups), "test": len(test_groups)},
    )


def walk_forward_folds(
    group_sizes: List[int],
    *,
    gap_groups: int = WALK_FORWARD_GAP_GROUPS,
    initial_train_ratio: float = WALK_FORWARD_INITIAL_TRAIN_RATIO,
    max_folds: int = WALK_FORWARD_MAX_FOLDS,
) -> List[Dict[str, int]]:
    """Expanding-window folds over whole, oldest-first decision groups.

    Each fold trains on every group older than its test block and tests on the
    next contiguous block. ``gap_groups`` whole groups sit between the last
    training group and the first test group; a group is never split. Test
    blocks are ordered and disjoint, so no window is counted twice.
    """
    total_groups = len(group_sizes)
    if total_groups < 3:
        return []
    initial_train = max(2, int(total_groups * initial_train_ratio))
    if initial_train + gap_groups >= total_groups:
        return []
    block = max(1, (total_groups - initial_train) // max_folds)

    bounds: List[int] = []
    cursor = 0
    for size in group_sizes:
        bounds.append(cursor)
        cursor += size

    def _row_range(first_group: int, last_group: int) -> Tuple[int, int]:
        return bounds[first_group], bounds[last_group] + group_sizes[last_group]

    folds: List[Dict[str, int]] = []
    train_end_group = initial_train - 1
    while len(folds) < max_folds:
        test_start_group = train_end_group + 1 + gap_groups
        if test_start_group >= total_groups:
            break
        test_end_group = min(total_groups - 1, test_start_group + block - 1)
        train_start, train_end = _row_range(0, train_end_group)
        test_start, test_end = _row_range(test_start_group, test_end_group)
        folds.append(
            {
                "fold": len(folds) + 1,
                "train_group_start": 0,
                "train_group_end": train_end_group,
                "test_group_start": test_start_group,
                "test_group_end": test_end_group,
                "train_row_start": train_start,
                "train_row_end": train_end,
                "test_row_start": test_start,
                "test_row_end": test_end,
            }
        )
        train_end_group = test_end_group
    return folds


def _walk_forward_evaluation(
    ordered: List[dict],
    X: np.ndarray,
    y: np.ndarray,
    labels: List[str],
    group_sizes: List[int],
    model_type: str,
) -> Dict[str, Any]:
    """Advisory expanding-window evaluation.

    It is reported next to the published chronological hold-out, never in place
    of it: the hold-out still decides what the owner sees as the model's test
    score, and no threshold reads this block.
    """
    report: Dict[str, Any] = {
        "kind": "expanding_window",
        "primary_evaluation": "chronological_holdout",
        "advisory_only": True,
        "not_a_release_gate": True,
        "gap_groups": WALK_FORWARD_GAP_GROUPS,
        "available": False,
        "n_folds": 0,
        "folds": [],
        "mean_accuracy": None,
        "std_accuracy": None,
        "mean_macro_f1": None,
        "baseline_mean_accuracy": None,
    }
    folds = walk_forward_folds(group_sizes)
    if not folds:
        report["unavailable_reason"] = "insufficient_decision_groups"
        return report

    results: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for fold in folds:
        train_idx = list(range(fold["train_row_start"], fold["train_row_end"]))
        test_idx = list(range(fold["test_row_start"], fold["test_row_end"]))
        if len(train_idx) < MIN_TRAIN_ROWS or len(test_idx) < WALK_FORWARD_MIN_TEST_ROWS:
            skipped.append({"fold": fold["fold"], "reason": "segment_too_small"})
            continue
        if len(set(y[train_idx].tolist())) < 2:
            skipped.append({"fold": fold["fold"], "reason": "single_class_train_segment"})
            continue

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X[train_idx])
        X_test_scaled = scaler.transform(X[test_idx])
        model = (
            DummyClassifier(strategy="most_frequent")
            if model_type == "baseline"
            else LogisticRegression(max_iter=1000, random_state=42)
        )
        model.fit(X_train_scaled, y[train_idx])
        predictions = model.predict(X_test_scaled)
        metrics = _class_metrics(y[test_idx], predictions, labels)

        baseline = DummyClassifier(strategy="most_frequent")
        baseline.fit(X_train_scaled, y[train_idx])
        baseline_metrics = _class_metrics(
            y[test_idx], baseline.predict(X_test_scaled), labels
        )

        results.append(
            {
                "fold": fold["fold"],
                "train_rows": len(train_idx),
                "test_rows": len(test_idx),
                "train_groups": fold["train_group_end"] - fold["train_group_start"] + 1,
                "test_groups": fold["test_group_end"] - fold["test_group_start"] + 1,
                "train_end_decision_instant": ordered[train_idx[-1]].get("decision_instant"),
                "test_start_decision_instant": ordered[test_idx[0]].get("decision_instant"),
                "test_end_decision_instant": ordered[test_idx[-1]].get("decision_instant"),
                "accuracy": metrics["accuracy"],
                "macro_f1": metrics["macro_f1"],
                "baseline_accuracy": baseline_metrics["accuracy"],
                "class_distribution": metrics["class_distribution"],
                "insufficient": False,
                "insufficient_reasons": [],
            }
        )

    if not results:
        report["unavailable_reason"] = "folds_too_small"
        report["skipped_folds"] = skipped
        return report

    accuracies = [row["accuracy"] for row in results]
    f1_values = [row["macro_f1"] for row in results]
    baseline_values = [row["baseline_accuracy"] for row in results]
    report.update(
        {
            "available": True,
            "n_folds": len(results),
            "folds": results,
            "mean_accuracy": round(float(np.mean(accuracies)), 4),
            "std_accuracy": round(float(np.std(accuracies)), 4),
            "mean_macro_f1": round(float(np.mean(f1_values)), 4),
            "baseline_mean_accuracy": round(float(np.mean(baseline_values)), 4),
        }
    )
    if skipped:
        report["skipped_folds"] = skipped
    return report


def train_personal_model(
    rows: List[dict],
    feature_names: List[str],
    *,
    owner_id: str,
    snapshot_id: str,
    snapshot_checksum: str,
    strategy_version_id: str,
    label_definition: Optional[dict] = None,
    training_job_id: Optional[str] = None,
    model_type: str = "logistic_regression",
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    market_data_nature: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Fit, evaluate, persist and reload a personal decision classifier."""
    if model_type not in ("logistic_regression", "baseline"):
        raise PersonalTrainingRefused(
            "unknown_model_type", f"不支援的模型類型：{model_type}"
        )
    if not feature_names:
        raise PersonalTrainingRefused("missing_features", "沒有可用的特徵欄位。")
    if len(rows) < MIN_PERSONAL_ROWS:
        raise PersonalTrainingRefused(
            "insufficient_rows",
            f"可用訓練資料只有 {len(rows)} 筆，未達工程最低 {MIN_PERSONAL_ROWS} 筆。",
            {"measured": len(rows), "requirement": MIN_PERSONAL_ROWS},
        )

    ordered = sorted(
        rows,
        key=lambda row: (
            _split_group_key(row),
            row.get("decided_at") or "",
            row.get("decision_event_id") or "",
        ),
    )
    group_sizes: List[int] = []
    previous_key: Optional[str] = None
    for row in ordered:
        key = _split_group_key(row)
        if group_sizes and key == previous_key:
            group_sizes[-1] += 1
        else:
            group_sizes.append(1)
            previous_key = key

    labels: List[str] = sorted({row["action_label"] for row in ordered})
    if len(labels) < 2:
        raise PersonalTrainingRefused(
            "single_class_dataset",
            "已確認標籤只有一個動作類別，無法訓練分類模型。",
            {"classes": labels},
        )

    X = _matrix(ordered, feature_names)
    y = np.array([row["action_label"] for row in ordered])

    train_idx, val_idx, test_idx, groups_per_segment = grouped_temporal_split(
        group_sizes, train_ratio, val_ratio
    )
    if (
        len(train_idx) < MIN_TRAIN_ROWS
        or len(val_idx) < MIN_VAL_ROWS
        or len(test_idx) < MIN_TEST_ROWS
    ):
        raise PersonalTrainingRefused(
            "split_too_small",
            "時間切分後訓練／驗證／測試樣本不足。",
            {
                "train": len(train_idx),
                "val": len(val_idx),
                "test": len(test_idx),
            },
        )

    train_labels = set(y[train_idx].tolist())
    if len(train_labels) < 2:
        raise PersonalTrainingRefused(
            "single_class_train_segment",
            "訓練時段只有一個動作類別；只有之後時段出現另一類別，不能據此訓練。",
            {"train_classes": sorted(train_labels)},
        )

    X_train, y_train = X[train_idx], y[train_idx]
    X_val, y_val = X[val_idx], y[val_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    # Preprocessing is fit on the train segment only. Fitting before the split
    # would leak validation/test distribution into the scaler.
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    if model_type == "baseline":
        model = DummyClassifier(strategy="most_frequent")
    else:
        model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(X_train_scaled, y_train)

    train_metrics = _class_metrics(y_train, model.predict(X_train_scaled), labels)
    val_metrics = _class_metrics(y_val, model.predict(X_val_scaled), labels)
    test_metrics = _class_metrics(y_test, model.predict(X_test_scaled), labels)

    baseline = DummyClassifier(strategy="most_frequent")
    baseline.fit(X_train_scaled, y_train)
    baseline_test = _class_metrics(y_test, baseline.predict(X_test_scaled), labels)
    # The baseline rule is fixed here, from the training segment only, so a later
    # evaluation can score it without ever looking at the test labels to choose it.
    baseline_label = _most_frequent_label(y_train.tolist())

    warnings: List[str] = []
    if len(test_idx) < SMALL_TEST_THRESHOLD:
        warnings.append("small_test_set")
    if any(
        test_metrics["per_class"][label]["support"] < SMALL_CLASS_SUPPORT
        for label in labels
    ):
        warnings.append("small_class_support")
    test_classes = set(y_test.tolist())
    if len(test_classes) < len(labels):
        warnings.append("missing_class_in_test")
    nature_values = {
        value
        for values in (market_data_nature or {}).values()
        for value in values
    }
    if "synthetic" in nature_values:
        warnings.append("synthetic_market_features")
    if "mixed" in nature_values:
        warnings.append("mixed_market_features")

    walk_forward = _walk_forward_evaluation(
        ordered, X, y, labels, group_sizes, model_type
    )
    if (
        walk_forward["available"]
        and walk_forward["mean_accuracy"] < walk_forward["baseline_mean_accuracy"]
    ):
        warnings.append("walk_forward_below_baseline")

    model_id = uuid.uuid4().hex
    model_dir = _safe_personal_dir(model_id)
    os.makedirs(model_dir, exist_ok=True)

    import joblib

    joblib.dump(model, os.path.join(model_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(model_dir, "scaler.joblib"))

    artifact_sha256 = hashlib.sha256()
    for filename in ("model.joblib", "scaler.joblib"):
        with open(os.path.join(model_dir, filename), "rb") as fh:
            artifact_sha256.update(fh.read())

    def _segment(indices: List[int]) -> Dict[str, Any]:
        return {
            "size": len(indices),
            "start_decided_at": ordered[indices[0]]["decided_at"] if indices else None,
            "end_decided_at": ordered[indices[-1]]["decided_at"] if indices else None,
            "start_event_id": (
                ordered[indices[0]]["decision_event_id"] if indices else None
            ),
            "end_event_id": (
                ordered[indices[-1]]["decision_event_id"] if indices else None
            ),
        }

    metadata: Dict[str, Any] = {
        "model_id": model_id,
        "task_type": PERSONAL_TASK_TYPE,
        "artifact_scope": "personal_decision_model",
        "owner_id": owner_id,
        # Existing loaders check user_id; keep it equal to the owner.
        "user_id": owner_id,
        "strategy_version_id": strategy_version_id,
        "snapshot_id": snapshot_id,
        "snapshot_checksum": snapshot_checksum,
        "artifact_sha256": artifact_sha256.hexdigest(),
        "training_job_id": training_job_id,
        "trained_at": utcnow().isoformat(),
        "model_type": model_type,
        "classes": labels,
        "feature_names": feature_names,
        "n_rows": len(ordered),
        "class_distribution": test_metrics["class_distribution"],
        "baseline": {
            "rule": "most_frequent_label_in_train_segment",
            "label": baseline_label,
            "source": "fixed_at_training",
        },
        "label_definition": label_definition or {},
        "split": {
            "kind": "chronological_holdout",
            "group_aware": True,
            "group_key": SPLIT_GROUP_KEY,
            "n_groups": len(group_sizes),
            "groups_per_segment": groups_per_segment,
            "train_ratio": train_ratio,
            "val_ratio": val_ratio,
            "selection_source": "fixed_hyperparameters_per_model_type",
            "test_windows_overlap": False,
            "label_horizon_crossing": False,
            "train": _segment(train_idx),
            "val": _segment(val_idx),
            "test": _segment(test_idx),
            "boundaries": {
                "train_end_index": len(train_idx) - 1,
                "val_end_index": len(train_idx) + len(val_idx) - 1,
            },
        },
        "metrics": {
            "train": train_metrics,
            "val": val_metrics,
            "test": test_metrics,
            "baseline_test": baseline_test,
        },
        "walk_forward": walk_forward,
        "warnings": warnings,
        "market_data_nature": market_data_nature or {},
        "wording_zh": PERSONAL_WORDING_ZH,
        "not_for": [
            "市場方向預測",
            "交易是否正確或有利潤",
            "投資建議",
        ],
        "disclaimer_zh": "分數只反映模型模仿此 owner 已確認決策的能力，"
        "不代表真實回報、準確度保證或越用越準。",
    }
    with open(os.path.join(model_dir, "metadata.json"), "w") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)
    with open(os.path.join(model_dir, "feature_names.json"), "w") as fh:
        json.dump(feature_names, fh)

    # Reload from disk and run one prediction so a corrupt artifact fails here,
    # not on the first owner-facing request.
    loaded_model, loaded_scaler, loaded_meta, loaded_names = load_personal_model(
        model_id, owner_id=owner_id
    )
    smoke = _predict_with(
        loaded_model,
        loaded_scaler,
        loaded_names,
        ordered[test_idx[0]]["features"],
    )
    if smoke["predicted_action"] not in labels:
        raise PersonalTrainingRefused(
            "artifact_reload_failed", "模型重新載入後輸出不在已知類別內。"
        )

    return {
        "model_id": model_id,
        "task_type": PERSONAL_TASK_TYPE,
        "artifact_scope": "personal_decision_model",
        "strategy_version_id": strategy_version_id,
        "snapshot_id": snapshot_id,
        "snapshot_checksum": snapshot_checksum,
        "artifact_sha256": metadata["artifact_sha256"],
        "training_job_id": training_job_id,
        "classes": labels,
        "feature_names": feature_names,
        "n_rows": len(ordered),
        "split": metadata["split"],
        "metrics": metadata["metrics"],
        "walk_forward": walk_forward,
        "baseline": metadata["baseline"],
        "warnings": warnings,
        "market_data_nature": market_data_nature or {},
        "reload_verified": True,
        "wording_zh": PERSONAL_WORDING_ZH,
    }


def load_personal_model(
    model_id: str, owner_id: Optional[str] = None
) -> Tuple[Any, Any, Dict[str, Any], List[str]]:
    import joblib

    model_dir = _safe_personal_dir(model_id)
    if not os.path.isdir(model_dir):
        raise FileNotFoundError("Personal model artifact not found")

    with open(os.path.join(model_dir, "metadata.json")) as fh:
        metadata = json.load(fh)

    if metadata.get("task_type") != PERSONAL_TASK_TYPE:
        raise ValueError("Artifact is not a personal decision model")

    if owner_id is not None and str(metadata.get("owner_id")) != str(owner_id):
        raise PermissionError("Personal model is not owned by this user")

    model = joblib.load(os.path.join(model_dir, "model.joblib"))
    scaler = joblib.load(os.path.join(model_dir, "scaler.joblib"))
    with open(os.path.join(model_dir, "feature_names.json")) as fh:
        feature_names = json.load(fh)
    return model, scaler, metadata, feature_names


def _predict_with(
    model: Any, scaler: Any, feature_names: List[str], features: Dict[str, float]
) -> Dict[str, Any]:
    missing = [name for name in feature_names if name not in features]
    if missing:
        raise PersonalTrainingRefused(
            "missing_features",
            "推論輸入缺少模型需要的特徵欄位。",
            {"missing": missing},
        )
    vector = np.array([[float(features[name]) for name in feature_names]])
    scaled = scaler.transform(vector)
    predicted = model.predict(scaled)[0]
    probabilities: Dict[str, float] = {}
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(scaled)[0]
        for label, value in zip(list(model.classes_), proba):
            probabilities[str(label)] = round(float(value), 4)
    return {
        "predicted_action": str(predicted),
        "action_probabilities": probabilities,
    }


def predict_personal(
    model_id: str, features: Dict[str, float], owner_id: Optional[str] = None
) -> Dict[str, Any]:
    model, scaler, metadata, feature_names = load_personal_model(
        model_id, owner_id=owner_id
    )
    result = _predict_with(model, scaler, feature_names, features)
    return {
        **result,
        "model_id": model_id,
        "task_type": metadata.get("task_type"),
        "strategy_version_id": metadata.get("strategy_version_id"),
        "snapshot_id": metadata.get("snapshot_id"),
        "snapshot_checksum": metadata.get("snapshot_checksum"),
        "artifact_sha256": metadata.get("artifact_sha256"),
        "classes": metadata.get("classes"),
        "trained_at": metadata.get("trained_at"),
        "wording_zh": PERSONAL_WORDING_ZH,
        "not_for": metadata.get("not_for"),
        "disclaimer_zh": metadata.get("disclaimer_zh"),
    }
