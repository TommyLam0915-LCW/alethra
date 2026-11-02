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
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.auth.provenance import HUMAN, event_actor_provenance
from app.ml.features import build_feature_matrix
from app.models.decision_label import DecisionLabelState
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import HumanDecisionEvent
from app.models.market_data import MarketDataBar
from app.models.personal_dataset import (
    PersonalizationDatasetSnapshot,
    PersonalizationSnapshotStatus,
)
from app.models.simulation import SimulationSession
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
SAMPLING_CONTRACT_VERSION = "as-of-bar-close-v1"

# A daily bar is final only after its session has closed. Without an exchange
# calendar we use the next calendar day 00:00 UTC, which is later than the cash
# close of every exchange this product stores (US, HK, JP, UK). The rule is
# deliberately bar-close based, not date based.
DAILY_BAR_CLOSE_OFFSET = timedelta(days=1)


def daily_bar_available_at(bar_date: date) -> datetime:
    """Earliest instant a daily bar may be read as complete."""
    return datetime.combine(bar_date + DAILY_BAR_CLOSE_OFFSET, time.min)


def sampling_contract() -> Dict[str, Any]:
    return {
        "version": SAMPLING_CONTRACT_VERSION,
        "as_of_rule": "bar_close_time",
        "daily_bar_available_at": "next_calendar_day_00:00_utc",
        "decision_instant_source": "server_recorded_market_clock",
        "legacy_decision_instant_fallback": "start_of_snapshot_date",
        "split_grouping": "decision_instant",
    }

LABEL_DEFINITION = {
    "task_type": TASK_TYPE,
    "label_source": "owner_confirmed_decision",
    "label_provenance": "verified_password_session",
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
    "unverified_provenance": "來源不是已驗證的本人密碼登入工作階段",
    "unverified_confirmation": "確認動作不是由已驗證的本人密碼登入工作階段寫入",
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


def _normalize_instant(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _daily_bars_visible_by(
    db: Session,
    instrument_id: str,
    cutoff: datetime,
    provider_id: Optional[str],
) -> List[MarketDataBar]:
    """Daily bars whose close was known by ``cutoff``, one provider only.

    Bars stored for a 5-minute timeframe share one ``bar_date``; selecting the
    midnight-timestamp rows keeps exactly one daily bar per date. Mixing two
    providers would duplicate dates, so one provider is bound explicitly (the
    session's provider when known, otherwise the provider covering the most
    daily bars, with a deterministic tie-break).
    """
    rows = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == instrument_id,
            MarketDataBar.bar_date <= cutoff.date(),
        )
        .order_by(MarketDataBar.bar_date, MarketDataBar.provider_id)
        .all()
    )
    daily = [
        row
        for row in rows
        if row.bar_timestamp == datetime.combine(row.bar_date, time.min)
    ]
    if not daily:
        return []

    if provider_id:
        selected = provider_id
    else:
        counts = Counter(row.provider_id for row in daily)
        selected = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    by_date: Dict[date, MarketDataBar] = {}
    for row in daily:
        if row.provider_id != selected:
            continue
        by_date.setdefault(row.bar_date, row)

    return [
        row
        for bar_date, row in sorted(by_date.items())
        if daily_bar_available_at(bar_date) <= cutoff
    ]


def _features_as_of(
    db: Session,
    instrument_id: str,
    snapshot_date: str,
    *,
    as_of_instant: Optional[datetime] = None,
    provider_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, float]], Optional[List[str]], Optional[str], Optional[str]]:
    """Features visible at the decision instant.

    Only daily bars that had already closed by ``as_of_instant`` are read. When
    the instant is absent (legacy rows, date-only prediction) the decision is
    treated as taken at the start of ``snapshot_date``, so that day's bar is
    never used before its close. Returns
    ``(features, feature_names, feature_bar_date, exclusion_code)``.
    """
    end = _parse_date(snapshot_date)
    if end is None:
        return None, None, None, "invalid_context"

    cutoff = _normalize_instant(as_of_instant) or datetime.combine(end, time.min)
    stored = _daily_bars_visible_by(db, instrument_id, cutoff, provider_id)
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
    feature_day = _parse_date(feature_bar_date)
    if feature_day is None:
        return None, None, None, "missing_features"
    # Defensive cutoffs: the chosen row cannot be from the future, and its bar
    # must have closed by the decision instant.
    if feature_day > end or daily_bar_available_at(feature_day) > cutoff:
        return None, None, None, "future_leakage"

    row = dict(zip(feature_names, features[-1]))
    return row, feature_names, feature_bar_date, None


def features_as_of(
    db: Session,
    instrument_id: str,
    as_of_date: str,
    *,
    as_of_instant: Optional[datetime] = None,
) -> Tuple[Optional[Dict[str, float]], Optional[List[str]], Optional[str], Optional[str]]:
    """Public wrapper: features visible at a decision instant or date.

    ``as_of_instant`` is the server-recorded decision instant. Without it the
    decision is read conservatively as happening at the start of ``as_of_date``.
    """
    return _features_as_of(
        db, instrument_id, as_of_date, as_of_instant=as_of_instant
    )


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


def _source_key(event: HumanDecisionEvent) -> Tuple[Any, ...]:
    """Identity of the raw decision an event records.

    Two events with the same session, step, instrument, snapshot, action,
    quantity and rationale are the same decision submitted twice, not two
    samples. A correction keeps the original's context and is linked by
    ``supersedes_event_id``; it never reaches this grouping together with the
    original because the original is already superseded.
    """
    action = event.action.value if hasattr(event.action, "value") else str(event.action)
    return (
        str(event.simulation_session_id),
        event.server_step_number,
        (event.server_instrument_id or "").strip(),
        (event.server_snapshot_date or "").strip(),
        action,
        event.quantity,
        (event.rationale or "").strip(),
    )


def _dedupe_source_events(
    events: List[HumanDecisionEvent],
    labels: Dict[uuid.UUID, Any],
) -> Tuple[List[HumanDecisionEvent], int]:
    """Keep one event per raw decision, preferring a reviewed one."""
    groups: Dict[Tuple[Any, ...], List[HumanDecisionEvent]] = {}
    order: List[Tuple[Any, ...]] = []
    for event in events:
        key = _source_key(event)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(event)

    kept: List[HumanDecisionEvent] = []
    dropped = 0
    for key in order:
        group = groups[key]
        if len(group) == 1:
            kept.append(group[0])
            continue
        reviewed = [event for event in group if event.id in labels]
        pool = reviewed or group
        kept.append(
            max(
                pool,
                key=lambda event: (
                    event_actor_provenance(event) == HUMAN,
                    event.created_at,
                    str(event.id),
                ),
            )
        )
        dropped += len(group) - 1
    return kept, dropped


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
    superseded_revisions = len(all_events) - len(current)
    labels = latest_labels(db, owner_id, [event.id for event in current])
    current, duplicate_events_ignored = _dedupe_source_events(current, labels)

    session_ids = {event.simulation_session_id for event in current}
    sessions = (
        {
            session.id: session
            for session in db.query(SimulationSession)
            .filter(SimulationSession.id.in_(session_ids))
            .all()
        }
        if session_ids
        else {}
    )

    exclusion_breakdown: Dict[str, int] = {}
    instant_sources: Dict[str, int] = {}
    rows: List[dict] = []
    feature_names: Optional[List[str]] = None
    feature_cache: Dict[Tuple[str, str, Optional[str]], tuple] = {}
    instruments: set = set()

    for event in current:
        instrument = (event.server_instrument_id or "").strip()
        snapshot_date = (event.server_snapshot_date or "").strip()
        cutoff = (event.server_data_cutoff_at or "").strip()

        def _exclude(code: str) -> None:
            exclusion_breakdown[code] = exclusion_breakdown.get(code, 0) + 1

        # An agent-written or unrecorded-provenance event is not an owner
        # confirmation and never becomes a label. Historical rows are reported
        # here, not upgraded.
        if event_actor_provenance(event) != HUMAN:
            _exclude("unverified_provenance")
            continue
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
        # A confirmation revision is only an owner confirmation when a
        # password-authenticated owner session wrote it. Rows from another
        # channel are reported, never promoted.
        if (label.actor_type or "unknown") != HUMAN:
            _exclude("unverified_confirmation")
            continue
        if label.state is DecisionLabelState.excluded:
            _exclude("excluded")
            continue
        if label.action_label not in ("buy", "sell", "hold"):
            _exclude("invalid_label")
            continue

        instant = _normalize_instant(event.decision_instant)
        instant_source = "recorded"
        if instant is None:
            # Legacy rows carry no market-clock instant. Treat the decision as
            # happening at the start of its snapshot date, so that date's daily
            # bar (not yet closed) is never used.
            instant = datetime.combine(_parse_date(snapshot_date), time.min)
            instant_source = "legacy_date_start"
        session = sessions.get(event.simulation_session_id)
        provider_id = getattr(session, "provider_id", None)

        cache_key = (instrument, instant.isoformat(), provider_id)
        if cache_key not in feature_cache:
            feature_cache[cache_key] = _features_as_of(
                db,
                instrument,
                snapshot_date,
                as_of_instant=instant,
                provider_id=provider_id,
            )
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
        instant_sources[instant_source] = instant_sources.get(instant_source, 0) + 1
        rows.append(
            {
                "decision_event_id": str(event.id),
                "label_id": str(label.id),
                "label_revision": label.revision_number,
                "strategy_version_id": str(strategy_version_id),
                "instrument_id": instrument,
                "snapshot_date": snapshot_date,
                "decision_instant": instant.isoformat(),
                "decision_instant_source": instant_source,
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
        "sampling": {
            "contract": sampling_contract(),
            "decision_instant_sources": instant_sources,
        },
        "reasons": reasons,
        "can_build": not reasons,
        "total_current_decisions": len(current),
        "superseded_revisions": superseded_revisions,
        "duplicate_source_events_ignored": duplicate_events_ignored,
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
        "duplicate_source_events_ignored": assembled["duplicate_source_events_ignored"],
        "sampling_contract": assembled["sampling"]["contract"],
        "decision_instant_sources": assembled["sampling"]["decision_instant_sources"],
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
            "sampling_contract": assembled["sampling"]["contract"],
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
    stored_contract = label_definition.pop("sampling_contract", None)
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
        "sampling_contract": stored_contract,
        "checksum": snapshot.checksum,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "note": "訓練只會使用此快照內已凍結嘅資料列；重新匯出或更新資料不會改變此快照。",
    }
