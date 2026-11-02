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
import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    SMALL_CLASS_SUPPORT,
    SMALL_TEST_THRESHOLD,
    _class_metrics,
    _most_frequent_label,
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

COMPARISON_ENGINE_VERSION = "personal-comparison-v2"
COMPARISON_CONTRACT_VERSION = "personal-comparison-request-v1"
MAX_COMPARISON_WINDOWS = 3
SMALL_WINDOW_ROWS = 5
ARTIFACT_FILES = ("model.joblib", "scaler.joblib")

BASELINE_RULE = "most_frequent_label_in_train_segment"

PURPOSE_COMMON_UNSEEN = "common_unseen"
PURPOSE_REUSED = "exploratory_reused"
PURPOSE_INSUFFICIENT = "provenance_insufficient"

PURPOSE_ZH = {
    PURPOSE_COMMON_UNSEEN: "共同未見評估：兩邊都冇用呢批列去擬合，可以作同一批列排名。",
    PURPOSE_REUSED: "研究性重複使用：呢批列之前已經被評估／用嚟揀版本，今次只屬研究參考，唔係獨立樣本外證明。",
    PURPOSE_INSUFFICIENT: "來源不足：未能確認兩邊有一批共同而未見過嘅評估列，未計算可排名差值。",
}

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
COMMON_BATCH_SUMMARY_ZH = (
    "兩個版本在共同未見評估列上比較；呢批列唔係任何一邊嘅擬合資料，"
    "亦可能唔等於任何一邊自己嘅測試窗。"
)
REUSED_SUMMARY_ZH = (
    "兩個版本在同一批列上重新評分，但呢批列之前已經用過；"
    "只屬研究性比較，唔可以當成獨立樣本外品質證明。"
)
INSUFFICIENT_SUMMARY_ZH = (
    "未能建立共同而未見過嘅評估列；只保留各版本自己測試窗嘅並列結果，"
    "未計算可排名差值。"
)

MISMATCH_FIELDS = ("decision_event_id", "action_label", "features")


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

    relationship = None
    relationship_error = None
    if snapshot_a is not None and snapshot_b is not None:
        try:
            slices_a = _segment_rows(version_a, snapshot_a)
            slices_b = _segment_rows(version_b, snapshot_b)
            relationship, _batch_rows = evaluation_relationship(
                db, owner_id, record_a.id, version_a, version_b, slices_a, slices_b
            )
        except ComparisonRefused as exc:
            relationship_error = {
                "code": exc.code,
                "message_zh": exc.message_zh,
            }

    # "Same evaluation set" means the same original decision groups, not the
    # same snapshot UUID: two snapshots can carry the same decisions.
    same_set = bool(relationship and relationship["test_slices_identical"])
    checks.append(
        _check(
            "same_evaluation_set",
            same_set,
            "兩邊的測試列以原始決策事件去重後完全相同，可在同一批列上比較。",
            {
                "a_checksum": snapshot_a.checksum if snapshot_a else None,
                "b_checksum": snapshot_b.checksum if snapshot_b else None,
                "shared_test_group_count": (
                    relationship["shared_test_group_count"] if relationship else None
                ),
                "a_test_size": split_a["test_size"],
                "b_test_size": split_b["test_size"],
            },
            required=False,
        )
    )

    checks.append(
        _check(
            "common_evaluation_rows",
            bool(relationship and relationship["row_count"] > 0),
            "兩邊有一批共同而未用於任何一邊擬合的評估列。",
            relationship_error
            or (
                {
                    "purpose": relationship["purpose"],
                    "row_count": relationship["row_count"],
                    "reasons": relationship["reasons"],
                }
                if relationship
                else None
            ),
            required=False,
        )
    )

    checks.append(
        _check(
            "no_peer_leakage",
            bool(
                relationship
                and not relationship["leakage"]["a_test_groups_used_in_b_fit"]
                and not relationship["leakage"]["b_test_groups_used_in_a_fit"]
            ),
            "任何一邊的測試列都未曾被另一邊用於擬合。",
            (relationship["leakage"] if relationship else relationship_error),
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
        "evaluation_relationship": relationship,
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


def _row_group(row: dict) -> str:
    """Original decision identity, not the snapshot UUID the row arrived in."""
    return _split_group_key(row)


def _group_payload(rows: List[dict]) -> List[tuple]:
    ordered = sorted(rows, key=lambda row: str(row.get("decision_event_id") or ""))
    return [
        (
            str(row.get("decision_event_id") or ""),
            row.get("action_label"),
            json.dumps(row.get("features") or {}, sort_keys=True, default=str),
        )
        for row in ordered
    ]


def _grouped(rows: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for row in rows:
        out.setdefault(_row_group(row), []).append(row)
    return out


def _segment_rows(version: ModelVersion, snapshot: PersonalizationDatasetSnapshot) -> Dict[str, Any]:
    slices = _frozen_slices(version, snapshot)
    spec = version.model_specification_json or {}
    train_size, val_size, _test_size = _segment_bounds(spec)
    ordered = slices["ordered"]
    return {
        "train_rows": ordered[:train_size],
        "val_rows": ordered[train_size : train_size + val_size],
        "test_rows": slices["test_rows"],
    }


def _baseline_contract(version: ModelVersion, train_rows: List[dict]) -> dict:
    """The baseline is fixed before the test rows are read.

    Newer versions record the label at training time from the training segment.
    Older immutable versions have no such field; for them the same rule is
    re-derived from the saved training segment and the provenance is labelled.
    Test labels are never consulted to choose a baseline.
    """
    spec = version.model_specification_json or {}
    labels = sorted(spec.get("classes") or [])
    recorded = dict(spec.get("baseline") or {})
    derived = _most_frequent_label([row["action_label"] for row in train_rows])
    label = recorded.get("label") or derived
    if recorded.get("rule") and recorded["rule"] != BASELINE_RULE:
        raise ComparisonRefused(
            "baseline_contract_mismatch",
            "版本記錄的基線規則與此比較引擎固定的規則不一致。",
            {"recorded": recorded.get("rule"), "expected": BASELINE_RULE},
        )
    if label is None:
        raise ComparisonRefused(
            "baseline_unavailable", "訓練時段沒有可用標籤，無法建立固定基線。"
        )
    if label not in labels:
        raise ComparisonRefused(
            "baseline_contract_mismatch",
            "基線標籤不在版本記錄的動作類別內，拒絕使用。",
            {"label": label, "classes": labels},
        )
    if derived is not None and label != derived:
        raise ComparisonRefused(
            "baseline_contract_mismatch",
            "記錄的基線標籤與訓練時段最常見類別不一致。",
            {"recorded": label, "derived": derived},
        )
    return {
        "rule": BASELINE_RULE,
        "label": label,
        "source": (
            recorded.get("source") or "recorded_at_training"
            if recorded.get("label")
            else "derived_from_train_segment_at_comparison"
        ),
    }


def _canonical_request(version_a: ModelVersion, version_b: ModelVersion) -> dict:
    """Immutable request identity used for idempotency, in side order."""

    def side(version: ModelVersion) -> dict:
        spec = version.model_specification_json or {}
        split = _split_contract(spec)
        return {
            "model_version_id": str(version.id),
            "snapshot_id": spec.get("snapshot_id"),
            "snapshot_checksum": spec.get("snapshot_checksum"),
            "artifact_sha256": spec.get("artifact_sha256"),
            "task_type": spec.get("task_type"),
            "strategy_version_id": spec.get("strategy_version_id"),
            "classes": sorted(spec.get("classes") or []),
            "feature_names": list(spec.get("feature_names") or []),
            "split": {
                "kind": split["kind"],
                "group_aware": split["group_aware"],
                "train_ratio": split["train_ratio"],
                "val_ratio": split["val_ratio"],
                "train_size": split["train_size"],
                "val_size": split["val_size"],
                "test_size": split["test_size"],
                "test_start_event_id": split["test_start_event_id"],
                "test_end_event_id": split["test_end_event_id"],
            },
        }

    return {
        "contract_version": COMPARISON_CONTRACT_VERSION,
        "a": side(version_a),
        "b": side(version_b),
    }


def requests_match(stored: Optional[dict], canonical: dict) -> bool:
    """True only when a stored request is the same canonical request.

    Runs written before the relationship contract only carried the two version
    ids. Their stored result stays replayable for the identical pair.
    """
    if not stored:
        return False
    if stored.get("contract_version") == canonical.get("contract_version"):
        return all(stored.get(key) == canonical.get(key) for key in ("a", "b"))
    return (
        str(stored.get("version_a_id")) == canonical["a"]["model_version_id"]
        and str(stored.get("version_b_id")) == canonical["b"]["model_version_id"]
    )


def _prior_run_overlap(
    db: Session,
    owner_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    batch_groups: set,
    record_id: uuid.UUID,
) -> dict:
    prior_runs = (
        db.query(PersonalModelComparisonRun)
        .filter(
            PersonalModelComparisonRun.owner_id == owner_id,
            or_(
                PersonalModelComparisonRun.version_a_id.in_([version_a.id, version_b.id]),
                PersonalModelComparisonRun.version_b_id.in_([version_a.id, version_b.id]),
            ),
        )
        .all()
    )
    selection_runs = {
        row.comparison_run_id: row.version_id
        for row in db.query(PersonalModelSelection)
        .filter(
            PersonalModelSelection.owner_id == owner_id,
            PersonalModelSelection.record_id == record_id,
            PersonalModelSelection.comparison_run_id.isnot(None),
        )
        .all()
    }
    overlapping: List[str] = []
    overlapping_groups: set = set()
    used_for_selection: List[str] = []
    prior_versions: set = set()
    for run in prior_runs:
        stored = run.result_json or {}
        prior_groups = set((stored.get("common_evaluation") or {}).get("group_keys") or [])
        same_pair = {run.version_a_id, run.version_b_id} == {version_a.id, version_b.id}
        overlap = prior_groups & batch_groups
        if not overlap and same_pair and not prior_groups:
            # A run stored before the relationship contract covered the same
            # pair; conservatively treat its rows as already seen.
            overlap = set(batch_groups)
        if not overlap:
            continue
        overlapping.append(str(run.id))
        overlapping_groups |= overlap
        prior_versions.update({str(run.version_a_id), str(run.version_b_id)})
        if run.id in selection_runs:
            used_for_selection.append(str(run.id))
    return {
        "prior_run_count": len(prior_runs),
        "overlapping_run_ids": overlapping,
        "overlapping_group_count": len(overlapping_groups),
        "selection_linked_run_ids": used_for_selection,
        "prior_run_version_ids": sorted(prior_versions),
    }


def _tuning_exposure(version: ModelVersion) -> dict:
    split = (version.model_specification_json or {}).get("split") or {}
    source = split.get("selection_source")
    fixed = source == "fixed_hyperparameters_per_model_type"
    return {
        "used_rows": 0 if fixed else None,
        "source": source or "unrecorded",
        "verified_fixed": fixed,
    }


def _side_exposure(
    version: ModelVersion,
    slices: Dict[str, Any],
    batch_rows: List[dict],
) -> dict:
    split = (version.model_specification_json or {}).get("split") or {}
    own_train_end = (split.get("train") or {}).get("end_decided_at")
    after_own = sum(
        1
        for row in batch_rows
        if own_train_end is None or str(row.get("decided_at") or "") > str(own_train_end)
    )
    tuning = _tuning_exposure(version)
    return {
        "fit_rows": len(slices["train_rows"]),
        "fit_transform_rows": len(slices["train_rows"]),
        "validation_rows_measured": len(slices["val_rows"]),
        "tuning": tuning,
        "batch_rows_after_own_train_end": after_own,
        "own_train_end_decided_at": own_train_end,
    }


def evaluation_relationship(
    db: Session,
    owner_id: uuid.UUID,
    record_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    slices_a: Dict[str, Any],
    slices_b: Dict[str, Any],
) -> Tuple[dict, List[dict]]:
    """Decide which rows may serve as one common evaluation batch.

    The anchor is the union of both recorded test slices, keyed by the original
    decision instant (and, within a group, the original decision event), not by
    the snapshot UUID the row happened to live in. Any group that either side
    used to fit or fit_transform is removed. Shared groups whose payload differs
    cannot be trusted and make provenance insufficient.
    """
    groups_a = {
        "train": _grouped(slices_a["train_rows"]),
        "val": _grouped(slices_a["val_rows"]),
        "test": _grouped(slices_a["test_rows"]),
    }
    groups_b = {
        "train": _grouped(slices_b["train_rows"]),
        "val": _grouped(slices_b["val_rows"]),
        "test": _grouped(slices_b["test_rows"]),
    }
    test_a, test_b = set(groups_a["test"]), set(groups_b["test"])
    shared = test_a & test_b
    fit_a, fit_b = set(groups_a["train"]), set(groups_b["train"])
    val_a, val_b = set(groups_a["val"]), set(groups_b["val"])

    conflicts: List[dict] = []
    for group in sorted(shared):
        if _group_payload(groups_a["test"][group]) != _group_payload(groups_b["test"][group]):
            conflicts.append(
                {
                    "group_key": group,
                    "fields": "decision_event_id/action_label/features",
                }
            )

    excluded = fit_a | fit_b
    batch_groups = (test_a | test_b) - excluded
    batch_rows: List[dict] = []
    sources: Dict[str, str] = {}
    for group in sorted(batch_groups):
        source = groups_a["test"].get(group) or groups_b["test"][group]
        sources[group] = "a" if group in groups_a["test"] else "b"
        batch_rows.extend(sorted(source, key=lambda row: str(row.get("decision_event_id") or "")))

    purpose = PURPOSE_COMMON_UNSEEN
    reasons: List[str] = []
    if conflicts:
        purpose = PURPOSE_INSUFFICIENT
        reasons.append("conflicting_row_identity")
        batch_rows = []
        batch_groups = set()
    elif not batch_rows:
        purpose = PURPOSE_INSUFFICIENT
        reasons.append("no_common_unseen_rows")

    reuse = _prior_run_overlap(db, owner_id, version_a, version_b, batch_groups, record_id)
    if purpose == PURPOSE_COMMON_UNSEEN and reuse["overlapping_run_ids"]:
        purpose = PURPOSE_REUSED
        reasons.append("batch_already_evaluated")
        if reuse["selection_linked_run_ids"]:
            reasons.append("batch_used_for_version_selection")

    side_a = _side_exposure(version_a, slices_a, batch_rows)
    side_b = _side_exposure(version_b, slices_b, batch_rows)
    side_a["own_test_groups_in_peer_fit"] = len(test_a & fit_b)
    side_b["own_test_groups_in_peer_fit"] = len(test_b & fit_a)
    side_a["own_test_groups_in_peer_validation"] = len(test_a & val_b)
    side_b["own_test_groups_in_peer_validation"] = len(test_b & val_a)
    side_a["prior_comparison"] = reuse
    side_b["prior_comparison"] = reuse

    public = {
        "purpose": purpose,
        "purpose_zh": PURPOSE_ZH[purpose],
        "usable_for_ranking": purpose == PURPOSE_COMMON_UNSEEN,
        "independent_out_of_sample": purpose == PURPOSE_COMMON_UNSEEN,
        "reasons": reasons,
        "row_count": len(batch_rows),
        "group_count": len(batch_groups),
        "group_keys": sorted(batch_groups),
        "row_sources": sources,
        "shared_test_group_count": len(shared),
        "only_a_test_group_count": len(test_a - test_b),
        "only_b_test_group_count": len(test_b - test_a),
        "test_slices_identical": bool(test_a) and test_a == test_b and not conflicts,
        "leakage": {
            "a_test_groups_used_in_b_fit": sorted(test_a & fit_b),
            "b_test_groups_used_in_a_fit": sorted(test_b & fit_a),
            "a_test_groups_in_b_validation": sorted(test_a & val_b),
            "b_test_groups_in_a_validation": sorted(test_b & val_a),
        },
        "conflicts": conflicts,
        "sides": {"a": side_a, "b": side_b},
        "note_zh": (
            "評估批次以原始決策時點／事件去重，再剔除任何一方用於擬合的列；"
            "共同批次唔一定等於任何一邊自己嘅測試窗。"
        ),
    }
    return public, batch_rows


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


def _score_rows(
    owner_id: uuid.UUID,
    version: ModelVersion,
    slices: Dict[str, Any],
    rows: List[dict],
    nature: set,
) -> Dict[str, Any]:
    """Predict `rows` with this version's artifact and score it.

    The baseline is the rule fixed from this version's own training segment, so
    every row set (own test window or a common batch) is scored by both the
    model and a baseline that was never chosen after seeing labels.
    """
    spec = version.model_specification_json or {}
    labels = sorted(spec.get("classes") or [])

    _model, _scaler, digest, metadata, predictions = _load_and_predict(
        owner_id, version, rows
    )

    truth = np.array([row["action_label"] for row in rows])
    pred = np.array(predictions)
    metrics = _class_metrics(truth, pred, labels)

    baseline = _baseline_contract(version, slices["train_rows"])
    baseline_metrics = _class_metrics(
        truth, np.array([baseline["label"]] * len(rows)), labels
    )
    return {
        "predictions": predictions,
        "metrics": metrics,
        "artifact_sha256": digest,
        "trained_at": metadata.get("trained_at"),
        "baseline": {
            "rule": baseline["rule"],
            "label": baseline["label"],
            "source": baseline["source"],
            "accuracy": baseline_metrics["accuracy"],
            "macro_f1": baseline_metrics["macro_f1"],
            "note_zh": (
                "基線＝訓練時段最常見的動作類別；規則在訓練時固定，"
                "並在同一次評估所用的列上計分。"
            ),
        },
        "warnings": _warnings(rows, metrics, labels, nature),
    }


def _evaluate_version(
    owner_id: uuid.UUID,
    version: ModelVersion,
    snapshot: PersonalizationDatasetSnapshot,
    slices: Dict[str, Any],
) -> Dict[str, Any]:
    spec = version.model_specification_json or {}
    test_rows = slices["test_rows"]
    labels = sorted(spec.get("classes") or [])

    scored = _score_rows(
        owner_id, version, slices, test_rows, _nature_values(snapshot)
    )
    metrics = scored["metrics"]
    windows = _windows(test_rows, scored["predictions"], labels)

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
            "artifact_sha256": scored["artifact_sha256"],
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
            "trained_at": scored["trained_at"],
            "created_by": str(owner_id),
        },
        "evaluation_set": evaluation_set,
        "metrics": metrics,
        "baseline": scored["baseline"],
        "windows": windows,
        "warnings": scored["warnings"],
        "predictions": scored["predictions"],
        "test_rows": test_rows,
    }


def _public_version_payload(
    version: ModelVersion,
    evaluated: Dict[str, Any],
    is_active: bool,
    common: Optional[Dict[str, Any]] = None,
    common_windows: Optional[List[dict]] = None,
) -> dict:
    identity = evaluated["identity"]
    metrics = evaluated["metrics"]
    payload = {
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
        # This block always describes the version's own frozen test window.
        # A ranking, when one exists, is reported separately under "common".
        "evaluation_basis": "own_test_window",
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
        "common": None,
    }
    if common is not None:
        payload["common"] = {
            "metrics": {
                "test_size": common["metrics"]["n_samples"],
                "accuracy": common["metrics"]["accuracy"],
                "macro_f1": common["metrics"]["macro_f1"],
                "weighted_f1": common["metrics"]["weighted_f1"],
                "per_class": common["metrics"]["per_class"],
                "confusion_matrix": common["metrics"]["confusion_matrix"],
                "class_distribution": common["metrics"]["class_distribution"],
            },
            "baseline": common["baseline"],
            "windows": common_windows or [],
            "warnings": common["warnings"],
        }
    return payload


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
    common_a: Optional[dict],
    common_b: Optional[dict],
    purpose: str,
    mode: str,
    started_at,
    disagreement: Optional[dict] = None,
) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    summary = PURPOSE_ZH[purpose]
    for role, version, evaluated, peer, common in (
        ("a", version_a, evaluated_a, version_b, common_a),
        ("b", version_b, evaluated_b, version_a, common_b),
    ):
        warnings = sorted(
            set(evaluated["warnings"]) | set(common["warnings"] if common else [])
        )
        metrics_json = {
            "comparison_run_id": str(run_id),
            "mode": mode,
            "evaluation_purpose": purpose,
            "evaluation_purpose_zh": PURPOSE_ZH[purpose],
            "independent_out_of_sample": purpose == PURPOSE_COMMON_UNSEEN,
            "role": role,
            "peer_model_version_id": str(peer.id),
            "identity": evaluated["identity"],
            # The version's own frozen test window is always recorded, and it is
            # labelled as such so it can never be mistaken for the ranking rows.
            "evaluation_set": evaluated["evaluation_set"],
            "metrics": evaluated["metrics"],
            "baseline": evaluated["baseline"],
            "per_window": evaluated["windows"],
            "comparison_evaluation": (
                {
                    "purpose": purpose,
                    "purpose_zh": PURPOSE_ZH[purpose],
                    "independent_out_of_sample": purpose == PURPOSE_COMMON_UNSEEN,
                    "rankable": purpose == PURPOSE_COMMON_UNSEEN,
                    "n_rows": common["metrics"]["n_samples"],
                    "metrics": common["metrics"],
                    "baseline": common["baseline"],
                    "warnings": common["warnings"],
                }
                if common
                else None
            ),
            "disagreement": disagreement,
            "warnings": warnings,
            "created_at": started_at.isoformat(),
            "not_for": NOT_FOR,
        }
        evaluation = ModelEvaluation(
            model_version_id=version.id,
            model_research_record_id=version.model_research_record_id,
            evaluation_type=EvaluationType.model_comparison,
            result_summary=summary,
            metrics_json=metrics_json,
            warning_flags=warnings,
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


def _mode_for(purpose: str, identical: bool) -> str:
    if purpose == PURPOSE_COMMON_UNSEEN:
        return "same_evaluation_set" if identical else "common_evaluation_batch"
    if purpose == PURPOSE_REUSED:
        return "exploratory_reused"
    return "per_version_test_window"


def _summary_for(purpose: str, identical: bool) -> str:
    if purpose == PURPOSE_COMMON_UNSEEN:
        return SAME_SET_SUMMARY_ZH if identical else COMMON_BATCH_SUMMARY_ZH
    if purpose == PURPOSE_REUSED:
        return REUSED_SUMMARY_ZH
    return INSUFFICIENT_SUMMARY_ZH


def _caveats_for(purpose: str, identical: bool, warnings: List[str]) -> List[str]:
    caveats = list(CAVEATS_ZH)
    if purpose == PURPOSE_COMMON_UNSEEN:
        caveats.append(
            "兩邊測試列以原始決策事件去重後完全相同。"
            if identical
            else "共同評估批次由兩邊測試列以原始決策事件去重後聯集而成，"
            "唔等於任何一邊自己嘅測試窗。"
        )
    elif purpose == PURPOSE_REUSED:
        caveats.append(
            "呢批評估列之前已經用過；今次只屬研究性比較，"
            "唔可以當成獨立樣本外品質證明。"
        )
    else:
        caveats.append(
            "未能建立共同而未用於擬合嘅評估列；只保留各版本自己測試窗嘅並列結果。"
        )
    if warnings:
        caveats.append("存在資料量或類別覆蓋警告，解讀時要一併考慮。")
    return caveats


def evaluate_pair(
    db: Session,
    owner_id: uuid.UUID,
    version_a: ModelVersion,
    version_b: ModelVersion,
    record_a: ModelResearchRecord,
    record_b: ModelResearchRecord,
    *,
    operation_key: Optional[str] = None,
    actor_type: str = "unknown",
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
    slices_a = _segment_rows(version_a, snapshot_a)
    slices_b = _segment_rows(version_b, snapshot_b)

    relationship, batch_rows = evaluation_relationship(
        db, owner_id, record_a.id, version_a, version_b, slices_a, slices_b
    )
    purpose = relationship["purpose"]
    identical = relationship["test_slices_identical"]
    labels = sorted(spec_a.get("classes") or [])

    canonical_request = _canonical_request(version_a, version_b)
    run_id = run_id_for(owner_id, operation_key)
    started_at = utcnow()

    evaluated_a = _evaluate_version(owner_id, version_a, snapshot_a, slices_a)
    evaluated_b = _evaluate_version(owner_id, version_b, snapshot_b, slices_b)

    common_a = common_b = None
    common_windows_a: List[dict] = []
    common_windows_b: List[dict] = []
    common_block = None
    delta = None
    disagreement = None
    if batch_rows:
        common_a = _score_rows(
            owner_id, version_a, slices_a, batch_rows, _nature_values(snapshot_a)
        )
        common_b = _score_rows(
            owner_id, version_b, slices_b, batch_rows, _nature_values(snapshot_b)
        )
        common_windows_a = _windows(batch_rows, common_a["predictions"], labels)
        common_windows_b = _windows(batch_rows, common_b["predictions"], labels)

    rankable = purpose == PURPOSE_COMMON_UNSEEN
    if rankable and common_a is not None and common_b is not None:
        delta = {
            "accuracy": round(
                common_a["metrics"]["accuracy"] - common_b["metrics"]["accuracy"], 4
            ),
            "macro_f1": round(
                common_a["metrics"]["macro_f1"] - common_b["metrics"]["macro_f1"], 4
            ),
            "direction": "a_minus_b",
            "n_rows": common_a["metrics"]["n_samples"],
            "note_zh": "差值只在同一批共同未見評估列上有意義；樣本少時可能只是噪音。",
        }
    if common_a is not None and common_b is not None:
        disagreement = _disagreement(
            batch_rows, common_a["predictions"], common_b["predictions"]
        )

    if common_a is not None and common_b is not None:
        common_block = {
            "purpose": purpose,
            "purpose_zh": PURPOSE_ZH[purpose],
            "rankable": rankable,
            "independent_out_of_sample": relationship["independent_out_of_sample"],
            "reasons": relationship["reasons"],
            "n_rows": len(batch_rows),
            "n_groups": relationship["group_count"],
            "group_keys": relationship["group_keys"],
            "row_sources": relationship["row_sources"],
            "shared_test_group_count": relationship["shared_test_group_count"],
            "only_a_test_group_count": relationship["only_a_test_group_count"],
            "only_b_test_group_count": relationship["only_b_test_group_count"],
            "test_slices_identical": identical,
            "baseline_shared": (
                common_a["baseline"]["label"] == common_b["baseline"]["label"]
            ),
            "leakage": relationship["leakage"],
            "conflicts": relationship["conflicts"],
            "sides": relationship["sides"],
            "warnings": sorted(
                set(common_a["warnings"]) | set(common_b["warnings"])
            ),
            "note_zh": relationship["note_zh"],
        }

    is_active_a = record_a.active_version_id == version_a.id
    is_active_b = record_b.active_version_id == version_b.id

    payload_a = _public_version_payload(
        version_a, evaluated_a, is_active_a, common_a, common_windows_a
    )
    payload_b = _public_version_payload(
        version_b, evaluated_b, is_active_b, common_b, common_windows_b
    )

    warnings = sorted(
        set(evaluated_a["warnings"])
        | set(evaluated_b["warnings"])
        | set(common_block["warnings"] if common_block else [])
    )
    mode = _mode_for(purpose, identical)

    result = {
        "comparison_run_id": str(run_id),
        "mode": mode,
        "comparable": True,
        "same_evaluation_set": identical,
        "rankable": rankable,
        "evaluation_purpose": purpose,
        "evaluation_purpose_zh": PURPOSE_ZH[purpose],
        "independent_out_of_sample": relationship["independent_out_of_sample"],
        "evaluation_reasons": relationship["reasons"],
        "checks": report["checks"],
        "versions": {"a": payload_a, "b": payload_b},
        "common_evaluation": common_block,
        "delta": delta,
        "disagreement": disagreement,
        "warnings": warnings,
        "caveats_zh": _caveats_for(purpose, identical, warnings),
        "summary_zh": _summary_for(purpose, identical),
        "evaluation_record_ids": {},
        "created_at": started_at.isoformat(),
        "operation_key": operation_key,
        "request_canonical": canonical_request,
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
        common_a=common_a,
        common_b=common_b,
        purpose=purpose,
        mode=mode,
        started_at=started_at,
        disagreement=disagreement,
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
        mode=mode,
        operation_key=operation_key,
        request_json={
            **canonical_request,
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
        actor_type=actor_type,
        payload={
            "comparison_run_id": str(run_id),
            "version_a_id": str(version_a.id),
            "version_b_id": str(version_b.id),
            "mode": mode,
            "same_evaluation_set": identical,
            "evaluation_purpose": purpose,
            "rankable": rankable,
            "common_evaluation_rows": len(batch_rows),
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
