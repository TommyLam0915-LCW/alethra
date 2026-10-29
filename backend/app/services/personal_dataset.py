"""Build the personalization training dataset from confirmed decisions.

This is the data path the personal trainer actually consumes — not an export.
Each row pairs an owner-confirmed action with the technical features that were
observable at or before the decision's snapshot date. Nothing from later than
that date is read: no future bars, no later P&L, no later notes.

A snapshot is refused unless the owner has an active consent whose scope lists
the strategy version, and the dataset meets the same minimums the eligibility
page reports. The refusal is explicit and measured; eligibility is never
lowered to make a build succeed.
"""

import hashlib
import json
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.ml.features import build_feature_matrix
from app.models.decision_label import DecisionLabelState
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import HumanDecisionEvent
from app.models.personal_dataset import (
    PersonalizationDatasetSnapshot,
    PersonalizationSnapshotStatus,
)
from app.services.decision_labels import latest_labels
from app.services.market_data_storage import MarketDataStorage
from app.services.personalization import (
    MIN_CONFIRMED_DECISIONS,
    get_active_consent,
    _scope_strategy_ids,
)

TASK_TYPE = "personal_decision_mimicry"
FEATURE_SCHEMA_VERSION = "ohlcv-technical-v1"
FEATURE_LOOKBACK_WINDOWS = [5, 10, 20]
MIN_FEATURE_BARS = max(FEATURE_LOOKBACK_WINDOWS) + 2

LABEL_DEFINITION = {
    "task_type": TASK_TYPE,
    "label_source": "owner_confirmed_decision",
    "classes": ["buy", "sell", "hold"],
    "definition_zh": (
        "以決策時點之前可見的市場特徵，估計該 owner 在此策略下會選擇的動作。"
    ),
    "not_a_prediction_of": [
        "市場升跌方向",
        "交易是否正確或有利潤",
        "投資建議",
    ],
}

EXCLUSION_LABELS_ZH = {
    "unconfirmed": "未經 owner 確認",
    "excluded": "owner 標記為不可用",
    "missing_market_data": "決策時點缺少已儲存市場資料",
    "missing_features": "特徵暖機不足或含無效值",
    "future_leakage": "決策時點晚於資料截止，屬未來資料",
    "invalid_label": "標籤動作無效",
    "feature_schema_mismatch": "特徵欄位與其他決策不一致",
    "invalid_context": "決策缺少可用策略／標的／時點",
}


class DatasetRefused(ValueError):
    def __init__(self, code: str, message_zh: str, details: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}


def _parse_date(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((value or "").strip())
    except (ValueError, TypeError):
        return None


def _superseded_ids(events: List[HumanDecisionEvent]) -> set:
    return {
        event.supersedes_event_id
        for event in events
        if event.supersedes_event_id
    }


def _get_owned_version(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> ExperimentVersion:
    version = (
        db.query(ExperimentVersion)
        .join(Experiment, ExperimentVersion.experiment_id == Experiment.id)
        .filter(
            ExperimentVersion.id == strategy_version_id,
            Experiment.owner_id == owner_id,
        )
        .first()
    )
    if not version:
        raise DatasetRefused(
            "strategy_version_not_found",
            "找不到此帳號擁有的策略版本。",
        )
    return version


def _resolve_consent(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    required: bool,
):
    consent = get_active_consent(db, owner_id)
    scope = set(_scope_strategy_ids(consent)) if consent else set()
    in_scope = consent is not None and strategy_version_id in scope

    if required:
        if consent is None:
            raise DatasetRefused(
                "consent_missing",
                "尚未記錄個人化訓練同意，不能建立訓練資料快照。",
            )
        if consent.owner_id != owner_id:
            raise DatasetRefused("consent_owner_mismatch", "同意的擁有者不符。")
        if not in_scope:
            raise DatasetRefused(
                "consent_scope_mismatch",
                "此策略版本不在目前同意的範圍內。",
                {"scope_strategy_version_ids": sorted(str(v) for v in scope)},
            )
    return consent, in_scope


def _features_as_of(
    db: Session,
    instrument_id: str,
    snapshot_date: str,
) -> Tuple[Optional[Dict[str, float]], Optional[List[str]], Optional[str], Optional[str]]:
    """Features visible at the decision snapshot.

    Reads only stored bars dated at or before ``snapshot_date``. Returns
    ``(features, feature_names, feature_bar_date, exclusion_code)``.
    """
    end = _parse_date(snapshot_date)
    if end is None:
        return None, None, None, "invalid_context"

    storage = MarketDataStorage(db)
    stored = storage.get_bars(instrument_id=instrument_id, end_date=end)
    if len(stored) < MIN_FEATURE_BARS:
        return None, None, None, "missing_market_data"

    bars = [
        {
            "date": bar.bar_date.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in stored
    ]
    features, feature_names, valid_dates = build_feature_matrix(
        bars, lookback_windows=FEATURE_LOOKBACK_WINDOWS
    )
    if not features or not valid_dates:
        return None, None, None, "missing_features"

    feature_bar_date = valid_dates[-1]
    # Defensive cutoff check: the chosen row cannot be from the future.
    if feature_bar_date > snapshot_date:
        return None, None, None, "future_leakage"

    row = dict(zip(feature_names, features[-1]))
    return row, feature_names, feature_bar_date, None


def features_as_of(
    db: Session, instrument_id: str, as_of_date: str
) -> Tuple[Optional[Dict[str, float]], Optional[List[str]], Optional[str], Optional[str]]:
    """Public wrapper: features visible at a decision snapshot date."""
    return _features_as_of(db, instrument_id, as_of_date)


def _canonical_checksum(
    rows: List[dict], feature_names: List[str], label_definition: dict
) -> str:
    payload = {
        "rows": sorted(rows, key=lambda row: row["decision_event_id"]),
        "feature_names": feature_names,
        "label_definition": label_definition,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _assemble(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    require_consent: bool,
) -> Dict[str, Any]:
    version = _get_owned_version(db, owner_id, strategy_version_id)
    consent, consent_in_scope = _resolve_consent(
        db, owner_id, strategy_version_id, required=require_consent
    )

    all_events = (
        db.query(HumanDecisionEvent)
        .filter(
            HumanDecisionEvent.owner_id == owner_id,
            HumanDecisionEvent.server_strategy_version_id == strategy_version_id,
        )
        .order_by(HumanDecisionEvent.created_at)
        .all()
    )
    superseded = _superseded_ids(all_events)
    current = [event for event in all_events if event.id not in superseded]
    labels = latest_labels(db, owner_id, [event.id for event in current])

    exclusion_breakdown: Dict[str, int] = {}
    rows: List[dict] = []
    feature_names: Optional[List[str]] = None
    feature_cache: Dict[Tuple[str, str], tuple] = {}
    instruments: set = set()

    for event in current:
        instrument = (event.server_instrument_id or "").strip()
        snapshot_date = (event.server_snapshot_date or "").strip()
        cutoff = (event.server_data_cutoff_at or "").strip()

        def _exclude(code: str) -> None:
            exclusion_breakdown[code] = exclusion_breakdown.get(code, 0) + 1

        if not instrument or _parse_date(snapshot_date) is None:
            _exclude("invalid_context")
            continue
        if cutoff and snapshot_date > cutoff:
            _exclude("future_leakage")
            continue

        label = labels.get(event.id)
        if label is None:
            _exclude("unconfirmed")
            continue
        if label.state is DecisionLabelState.excluded:
            _exclude("excluded")
            continue
        if label.action_label not in ("buy", "sell", "hold"):
            _exclude("invalid_label")
            continue

        cache_key = (instrument, snapshot_date)
        if cache_key not in feature_cache:
            feature_cache[cache_key] = _features_as_of(db, instrument, snapshot_date)
        features, names, feature_bar_date, exclusion = feature_cache[cache_key]
        if exclusion:
            _exclude(exclusion)
            continue

        assert features is not None and names is not None
        if feature_names is None:
            feature_names = names
        elif names != feature_names:
            _exclude("feature_schema_mismatch")
            continue

        instruments.add(instrument)
        rows.append(
            {
                "decision_event_id": str(event.id),
                "label_id": str(label.id),
                "label_revision": label.revision_number,
                "strategy_version_id": str(strategy_version_id),
                "instrument_id": instrument,
                "snapshot_date": snapshot_date,
                "feature_bar_date": feature_bar_date,
                "data_cutoff_at": cutoff or snapshot_date,
                "decided_at": event.created_at.isoformat(),
                "features": features,
                "action_label": label.action_label,
            }
        )

    feature_names = feature_names or []
    class_distribution: Dict[str, int] = {}
    for row in rows:
        key = row["action_label"]
        class_distribution[key] = class_distribution.get(key, 0) + 1

    date_values = [row["snapshot_date"] for row in rows]
    data_cutoffs = [row["data_cutoff_at"] for row in rows if row["data_cutoff_at"]]

    nature_map = MarketDataStorage(db).get_nature_map(sorted(instruments))
    nature_by_instrument = {
        instrument: sorted(set(providers.values()))
        for instrument, providers in nature_map.items()
    }

    reasons: List[Dict[str, Any]] = []
    if consent is None:
        reasons.append(
            {
                "code": "CONSENT_MISSING",
                "message_zh": "尚未記錄個人化訓練同意，未達到可建立快照的條件。",
                "measured": "missing",
                "requirement": "active consent",
            }
        )
    elif not consent_in_scope:
        reasons.append(
            {
                "code": "CONSENT_SCOPE_MISMATCH",
                "message_zh": "此策略版本不在目前同意的範圍內。",
                "measured": sorted(
                    str(v) for v in _scope_strategy_ids(consent)
                ),
                "requirement": str(strategy_version_id),
            }
        )
    if len(rows) < MIN_CONFIRMED_DECISIONS:
        reasons.append(
            {
                "code": "INSUFFICIENT_CONFIRMED_DECISIONS",
                "message_zh": (
                    f"可用嘅已確認決策只有 {len(rows)} 筆，"
                    f"未達最低 {MIN_CONFIRMED_DECISIONS} 筆。"
                ),
                "measured": len(rows),
                "requirement": MIN_CONFIRMED_DECISIONS,
            }
        )
    if len(class_distribution) < 2:
        reasons.append(
            {
                "code": "SINGLE_CLASS_DATASET",
                "message_zh": "已確認決策只有一個動作類別，無法訓練分類模型。",
                "measured": class_distribution,
                "requirement": ">= 2 classes",
            }
        )

    checksum = _canonical_checksum(rows, feature_names, LABEL_DEFINITION)

    return {
        "version": version,
        "consent": consent,
        "consent_in_scope": consent_in_scope,
        "rows": rows,
        "feature_names": feature_names,
        "exclusion_breakdown": exclusion_breakdown,
        "class_distribution": class_distribution,
        "date_range_start": min(date_values) if date_values else None,
        "date_range_end": max(date_values) if date_values else None,
        "data_cutoff_at": max(data_cutoffs) if data_cutoffs else None,
        "checksum": checksum,
        "data_nature_by_instrument": nature_by_instrument,
        "reasons": reasons,
        "can_build": not reasons,
        "total_current_decisions": len(current),
        "superseded_revisions": len(all_events) - len(current),
    }


def _payload(assembled: Dict[str, Any], snapshot: Optional[PersonalizationDatasetSnapshot] = None) -> Dict[str, Any]:
    consent = assembled["consent"]
    return {
        "snapshot_id": str(snapshot.id) if snapshot else None,
        "status": (
            snapshot.status.value if snapshot else "preview"
        ),
        "strategy_version_id": str(assembled["version"].id),
        "consent": {
            "id": str(consent.id) if consent else None,
            "consent_version": consent.consent_version if consent else None,
            "granted_at": (
                consent.granted_at.isoformat()
                if consent and consent.granted_at
                else None
            ),
            "in_scope": assembled["consent_in_scope"],
        },
        "total_current_decisions": assembled["total_current_decisions"],
        "superseded_revisions": assembled["superseded_revisions"],
        "included_count": len(assembled["rows"]),
        "excluded_count": sum(assembled["exclusion_breakdown"].values()),
        "exclusion_breakdown": assembled["exclusion_breakdown"],
        "exclusion_labels_zh": EXCLUSION_LABELS_ZH,
        "class_distribution": assembled["class_distribution"],
        "date_range": {
            "start": assembled["date_range_start"],
            "end": assembled["date_range_end"],
        },
        "data_cutoff_at": assembled["data_cutoff_at"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": assembled["feature_names"],
        "label_definition": LABEL_DEFINITION,
        "data_nature_by_instrument": assembled["data_nature_by_instrument"],
        "checksum": assembled["checksum"],
        "can_build": assembled["can_build"],
        "reasons": assembled["reasons"],
        "note": "特徵只用決策時點或之前已儲存嘅市場資料；"
        "已確認決策係人類標籤，唔係系統訊號。",
        "not_claimed": [
            "唔代表模型有效或越用越準",
            "唔係市場方向預測",
            "唔係交易建議",
        ],
    }


def preview_snapshot(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> Dict[str, Any]:
    # Preview is allowed before consent so the owner can review their decisions
    # first; it reports consent state as a build blocker instead of hiding it.
    return _payload(
        _assemble(db, owner_id, strategy_version_id, require_consent=False)
    )


def create_snapshot(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> Tuple[PersonalizationDatasetSnapshot, Dict[str, Any]]:
    assembled = _assemble(db, owner_id, strategy_version_id, require_consent=True)
    if not assembled["can_build"]:
        first = assembled["reasons"][0]
        raise DatasetRefused(first["code"].lower(), first["message_zh"], assembled["reasons"])

    snapshot = PersonalizationDatasetSnapshot(
        owner_id=owner_id,
        strategy_version_id=strategy_version_id,
        consent_id=assembled["consent"].id,
        consent_version=assembled["consent"].consent_version,
        status=PersonalizationSnapshotStatus.ready,
        rows_json=assembled["rows"],
        feature_names_json=assembled["feature_names"],
        label_definition_json={
            **LABEL_DEFINITION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "data_nature_by_instrument": assembled["data_nature_by_instrument"],
        },
        exclusion_breakdown_json=assembled["exclusion_breakdown"],
        class_distribution_json=assembled["class_distribution"],
        included_count=len(assembled["rows"]),
        excluded_count=sum(assembled["exclusion_breakdown"].values()),
        date_range_start=assembled["date_range_start"],
        date_range_end=assembled["date_range_end"],
        data_cutoff_at=assembled["data_cutoff_at"],
        checksum=assembled["checksum"],
    )
    db.add(snapshot)
    db.flush()
    return snapshot, _payload(assembled, snapshot)


def snapshot_payload(snapshot: PersonalizationDatasetSnapshot) -> Dict[str, Any]:
    label_definition = dict(snapshot.label_definition_json or {})
    data_nature = label_definition.pop("data_nature_by_instrument", {})
    feature_schema_version = label_definition.pop(
        "feature_schema_version", FEATURE_SCHEMA_VERSION
    )
    return {
        "snapshot_id": str(snapshot.id),
        "status": snapshot.status.value,
        "strategy_version_id": str(snapshot.strategy_version_id),
        "consent_id": str(snapshot.consent_id),
        "consent_version": snapshot.consent_version,
        "included_count": snapshot.included_count,
        "excluded_count": snapshot.excluded_count,
        "exclusion_breakdown": snapshot.exclusion_breakdown_json or {},
        "exclusion_labels_zh": EXCLUSION_LABELS_ZH,
        "class_distribution": snapshot.class_distribution_json or {},
        "date_range": {
            "start": snapshot.date_range_start,
            "end": snapshot.date_range_end,
        },
        "data_cutoff_at": snapshot.data_cutoff_at,
        "feature_schema_version": feature_schema_version,
        "feature_names": snapshot.feature_names_json or [],
        "label_definition": label_definition,
        "data_nature_by_instrument": data_nature,
        "checksum": snapshot.checksum,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "note": "訓練只會使用此快照內已凍結嘅資料列；重新匯出或更新資料不會改變此快照。",
    }
