"""Personalization dataset eligibility.

Capability C (a model trained on *this owner's* confirmed decisions) is not
implemented: there is no trainer and no consumer yet. What this module does
provide is the honest gate in front of it — it inspects the owner's recorded
decision events, applies the consent scope, and returns either an explicit
refusal with measured reasons or a statement that the dataset requirements are
met. It never fabricates labels and never mixes owners.

Rules encoded here (see docs/personalization-contract.md):

* Only explicitly recorded decisions count. No action is ever synthesised as
  HOLD.
* A decision without a stated reason is not a usable label.
* Superseded revisions do not count; the correction does.
* Only strategy versions listed in an active training consent count. Saving
  research data, approving an AI memory, or any other opt-in is not consent.
* Features must be observable at the decision snapshot: the snapshot date has
  to be at or before the session's data cutoff and stored bars must exist.
"""

import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.models.human_decision import HumanDecisionEvent
from app.models.market_data import MarketDataBar
from app.models.personalization import (
    PERSONALIZATION_TRAINING_PURPOSE,
    PersonalizationConsent,
)

CONSENT_VERSION = "personalization-training-v1"

MIN_CONFIRMED_DECISIONS = 200
MIN_DISTINCT_SESSIONS = 30
MIN_DISTINCT_SNAPSHOT_DATES = 60
MIN_PER_ACTION = 30
MIN_FEATURE_COVERAGE_RATIO = 0.95

CAPABILITY_STATUS = "AVAILABLE"


def get_active_consent(
    db: Session, owner_id: uuid.UUID
) -> Optional[PersonalizationConsent]:
    return (
        db.query(PersonalizationConsent)
        .filter(
            PersonalizationConsent.owner_id == owner_id,
            PersonalizationConsent.purpose == PERSONALIZATION_TRAINING_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .order_by(PersonalizationConsent.granted_at.desc())
        .first()
    )


def _scope_strategy_ids(consent: Optional[PersonalizationConsent]) -> List[uuid.UUID]:
    if consent is None or not isinstance(consent.scope_json, dict):
        return []
    raw = consent.scope_json.get("strategy_version_ids") or []
    out: List[uuid.UUID] = []
    for value in raw:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            continue
    return out


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _count_by_action(events: Sequence[HumanDecisionEvent]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for event in events:
        key = event.action.value if hasattr(event.action, "value") else str(event.action)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _pair_coverage(
    db: Session, events: Sequence[HumanDecisionEvent]
) -> Tuple[int, int, List[HumanDecisionEvent]]:
    """Count how many events have a stored bar at (instrument, snapshot_date).

    Returns (covered, considered, rows_without_usable_context).
    """
    pairs: Set[Tuple[str, str]] = set()
    invalid: List[HumanDecisionEvent] = []
    for event in events:
        instrument = (event.server_instrument_id or "").strip()
        snapshot = (event.server_snapshot_date or "").strip()
        if not instrument or not _parse_date(snapshot):
            invalid.append(event)
            continue
        pairs.add((instrument, snapshot))

    if not pairs:
        return 0, 0, invalid

    instruments = {instrument for instrument, _ in pairs}
    bar_dates = sorted({_parse_date(snapshot) for _, snapshot in pairs})
    rows = (
        db.query(MarketDataBar.instrument_id, MarketDataBar.bar_date)
        .filter(
            MarketDataBar.instrument_id.in_(instruments),
            MarketDataBar.bar_date.in_(bar_dates),
        )
        .distinct()
        .all()
    )
    stored = {(instrument, bar_date.isoformat()) for instrument, bar_date in rows}

    covered = 0
    considered = 0
    for event in events:
        instrument = (event.server_instrument_id or "").strip()
        snapshot = (event.server_snapshot_date or "").strip()
        if not instrument or not _parse_date(snapshot):
            continue
        considered += 1
        if (instrument, snapshot) in stored:
            covered += 1
    return covered, considered, invalid


def _future_leakage_rows(
    events: Sequence[HumanDecisionEvent],
) -> List[HumanDecisionEvent]:
    leaking: List[HumanDecisionEvent] = []
    for event in events:
        snapshot = (event.server_snapshot_date or "").strip()
        cutoff = (event.server_data_cutoff_at or "").strip()
        if not snapshot or not cutoff:
            continue
        if snapshot > cutoff:
            leaking.append(event)
    return leaking


def _consent_payload(
    consent: Optional[PersonalizationConsent],
) -> Dict[str, Any]:
    if consent is None:
        return {
            "granted": False,
            "purpose": PERSONALIZATION_TRAINING_PURPOSE,
            "consent_version": None,
            "scope": {"strategy_version_ids": []},
            "granted_at": None,
            "revoked_at": None,
        }
    return {
        "granted": True,
        "purpose": consent.purpose,
        "consent_version": consent.consent_version,
        "scope": {
            "strategy_version_ids": [str(v) for v in _scope_strategy_ids(consent)]
        },
        "granted_at": consent.granted_at.isoformat() if consent.granted_at else None,
        "revoked_at": consent.revoked_at.isoformat() if consent.revoked_at else None,
    }


def _reason(
    code: str, message_zh: str, measured: Any, requirement: Any
) -> Dict[str, Any]:
    return {
        "code": code,
        "message_zh": message_zh,
        "measured": measured,
        "requirement": requirement,
    }


def evaluate_eligibility(
    db: Session, owner_id: uuid.UUID
) -> Dict[str, Any]:
    """Compute the personalization dataset gate for one owner.

    All reads are scoped to ``owner_id``; private data from other owners is
    never counted or returned.
    """
    events = (
        db.query(HumanDecisionEvent)
        .filter(HumanDecisionEvent.owner_id == owner_id)
        .all()
    )
    total_recorded = len(events)

    superseded_ids = {
        _as_uuid(event.supersedes_event_id)
        for event in events
        if event.supersedes_event_id
    }
    latest = [event for event in events if event.id not in superseded_ids]
    superseded_count = total_recorded - len(latest)

    consent = get_active_consent(db, owner_id)
    scope_ids = set(_scope_strategy_ids(consent))

    in_scope: List[HumanDecisionEvent] = []
    outside_scope_count = 0
    for event in latest:
        strategy_id = _as_uuid(event.server_strategy_version_id)
        if strategy_id is not None and strategy_id in scope_ids:
            in_scope.append(event)
        else:
            outside_scope_count += 1

    missing_rationale = [
        event for event in in_scope if not (event.rationale or "").strip()
    ]
    confirmed = [
        event for event in in_scope if (event.rationale or "").strip()
    ]

    action_counts = _count_by_action(confirmed)
    distinct_sessions = len({str(event.simulation_session_id) for event in confirmed})
    distinct_snapshot_dates = len(
        {(event.server_snapshot_date or "").strip() for event in confirmed} - {""}
    )

    covered, considered, invalid_context = _pair_coverage(db, confirmed)
    coverage_ratio: Optional[float] = (
        covered / considered if considered > 0 else None
    )
    leaking = _future_leakage_rows(confirmed)

    strategies: List[Dict[str, Any]] = []
    for strategy_id in sorted(
        {
            str(_as_uuid(event.server_strategy_version_id))
            for event in latest
            if _as_uuid(event.server_strategy_version_id) is not None
        }
    ):
        strategy_latest = [
            event
            for event in latest
            if str(_as_uuid(event.server_strategy_version_id)) == strategy_id
        ]
        strategy_confirmed = [
            event
            for event in confirmed
            if str(_as_uuid(event.server_strategy_version_id)) == strategy_id
        ]
        strategies.append(
            {
                "strategy_version_id": strategy_id,
                "recorded_decisions": len(strategy_latest),
                "confirmed_decisions": len(strategy_confirmed),
                "distinct_sessions": len(
                    {str(event.simulation_session_id) for event in strategy_confirmed}
                ),
                "distinct_snapshot_dates": len(
                    {
                        (event.server_snapshot_date or "").strip()
                        for event in strategy_confirmed
                    }
                    - {""}
                ),
                "action_counts": _count_by_action(strategy_confirmed),
                "in_consent_scope": uuid.UUID(strategy_id) in scope_ids,
            }
        )

    reasons: List[Dict[str, Any]] = []

    if total_recorded == 0:
        reasons.append(
            _reason(
                "NO_RECORDED_DECISIONS",
                "尚未記錄任何人工決策，沒有可用的標籤。",
                0,
                ">= 1",
            )
        )
    if consent is None:
        reasons.append(
            _reason(
                "CONSENT_MISSING",
                "尚未記錄個人化訓練同意。保存研究記錄、開啟 AI 記憶或其他 opt-in "
                "並不等於同意用於訓練。",
                "missing",
                f"active consent ({CONSENT_VERSION})",
            )
        )
    elif not scope_ids:
        reasons.append(
            _reason(
                "CONSENT_SCOPE_EMPTY",
                "同意記錄沒有列出任何策略版本，沒有可用於訓練的資料範圍。",
                0,
                ">= 1 strategy version",
            )
        )

    if len(confirmed) < MIN_CONFIRMED_DECISIONS:
        reasons.append(
            _reason(
                "INSUFFICIENT_CONFIRMED_DECISIONS",
                f"在同意範圍內、具理由且未撤回的決策只有 {len(confirmed)} 筆，"
                f"未達最低 {MIN_CONFIRMED_DECISIONS} 筆。",
                len(confirmed),
                MIN_CONFIRMED_DECISIONS,
            )
        )
    if distinct_sessions < MIN_DISTINCT_SESSIONS:
        reasons.append(
            _reason(
                "INSUFFICIENT_DISTINCT_SESSIONS",
                f"已確認決策只來自 {distinct_sessions} 個回放工作階段，"
                f"未達最低 {MIN_DISTINCT_SESSIONS} 個。",
                distinct_sessions,
                MIN_DISTINCT_SESSIONS,
            )
        )
    if distinct_snapshot_dates < MIN_DISTINCT_SNAPSHOT_DATES:
        reasons.append(
            _reason(
                "INSUFFICIENT_DISTINCT_SNAPSHOT_DATES",
                f"已確認決策只覆蓋 {distinct_snapshot_dates} 個不同決策時點，"
                f"未達最低 {MIN_DISTINCT_SNAPSHOT_DATES} 個。",
                distinct_snapshot_dates,
                MIN_DISTINCT_SNAPSHOT_DATES,
            )
        )

    short_classes = {
        action: action_counts.get(action, 0)
        for action in ("buy", "sell", "hold")
        if action_counts.get(action, 0) < MIN_PER_ACTION
    }
    if short_classes:
        reasons.append(
            _reason(
                "INSUFFICIENT_CLASS_COVERAGE",
                "各類動作的已確認樣本不足："
                + "、".join(
                    f"{action} {count} 筆" for action, count in short_classes.items()
                )
                + f"（每類最低 {MIN_PER_ACTION} 筆）。",
                short_classes,
                MIN_PER_ACTION,
            )
        )

    if missing_rationale:
        reasons.append(
            _reason(
                "UNLABELLED_WITHOUT_REASON",
                f"同意範圍內有 {len(missing_rationale)} 筆決策沒有填寫理由，"
                "不能作為已確認標籤。",
                len(missing_rationale),
                0,
            )
        )
    if outside_scope_count:
        reasons.append(
            _reason(
                "OUTSIDE_CONSENT_SCOPE",
                f"有 {outside_scope_count} 筆決策不在同意的策略範圍內，未計入。",
                outside_scope_count,
                0,
            )
        )

    if not leaking and coverage_ratio is not None and coverage_ratio < MIN_FEATURE_COVERAGE_RATIO:
        reasons.append(
            _reason(
                "INSUFFICIENT_FEATURE_COVERAGE",
                f"只有 {covered}/{considered} 筆已確認決策在決策時點有已儲存的行情"
                f"（{coverage_ratio:.4f}），未達 {MIN_FEATURE_COVERAGE_RATIO}。",
                {"covered": covered, "considered": considered, "ratio": coverage_ratio},
                MIN_FEATURE_COVERAGE_RATIO,
            )
        )
    if leaking:
        reasons.append(
            _reason(
                "FUTURE_DATA_LEAKAGE",
                f"{len(leaking)} 筆已確認決策的決策時點超過來自工作階段的資料截止日，"
                "這些資料不能進入訓練集。",
                len(leaking),
                0,
            )
        )
    if invalid_context:
        reasons.append(
            _reason(
                "INVALID_SNAPSHOT_CONTEXT",
                f"{len(invalid_context)} 筆已確認決策缺少可用的決策時點或標的脈絡，"
                "無法確定決策時可知的特徵。",
                len(invalid_context),
                0,
            )
        )

    dataset_eligible = len(reasons) == 0

    return {
        "capability": "C_PERSONALIZATION",
        "label_zh": "個人化模型",
        "capability_status": CAPABILITY_STATUS,
        "capability_status_zh": (
            "已建立工程鏈：以你親自確認、具同意範圍的決策訓練專屬模型，"
            "資料不足、同意不符或版本撤銷時直接拒絕；"
            "分數只代表模型模仿你本人決策的能力，不代表回報、市場方向或愈用愈準。"
        ),
        "eligible": dataset_eligible,
        "status": "ELIGIBLE" if dataset_eligible else "NOT_ELIGIBLE",
        "reasons": reasons,
        "requirements": {
            "consent_version": CONSENT_VERSION,
            "min_confirmed_decisions": MIN_CONFIRMED_DECISIONS,
            "min_distinct_sessions": MIN_DISTINCT_SESSIONS,
            "min_distinct_snapshot_dates": MIN_DISTINCT_SNAPSHOT_DATES,
            "min_per_action": MIN_PER_ACTION,
            "min_feature_coverage_ratio": MIN_FEATURE_COVERAGE_RATIO,
        },
        "consent": _consent_payload(consent),
        "dataset": {
            "total_recorded_events": total_recorded,
            "eligible_events": len(confirmed),
            "excluded": {
                "superseded_revisions": superseded_count,
                "outside_consent_scope": outside_scope_count,
                "missing_reason": len(missing_rationale),
                "missing_snapshot_context": len(invalid_context),
            },
            "distinct_sessions": distinct_sessions,
            "distinct_snapshot_dates": distinct_snapshot_dates,
            "action_counts": action_counts,
            "feature_coverage": {
                "covered": covered,
                "considered": considered,
                "ratio": coverage_ratio,
            },
            "future_leakage_events": len(leaking),
        },
        "strategies": strategies,
        "evaluated_at": utcnow().isoformat(),
        "disclaimer_zh": (
            "個人化訓練資格只依已記錄且經同意的決策判斷；不足時直接拒絕，"
            "不以模擬或推測標籤補足。"
        ),
    }


def grant_consent(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_ids: Sequence[uuid.UUID],
) -> PersonalizationConsent:
    """Replace any active training consent with a new explicit scope."""
    now = utcnow()
    active = (
        db.query(PersonalizationConsent)
        .filter(
            PersonalizationConsent.owner_id == owner_id,
            PersonalizationConsent.purpose == PERSONALIZATION_TRAINING_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .all()
    )
    for row in active:
        row.revoked_at = now

    consent = PersonalizationConsent(
        owner_id=owner_id,
        purpose=PERSONALIZATION_TRAINING_PURPOSE,
        consent_version=CONSENT_VERSION,
        scope_json={
            "strategy_version_ids": [str(v) for v in strategy_version_ids],
            "data_sources": ["human_decisions"],
        },
        granted_at=now,
    )
    db.add(consent)
    db.flush()
    return consent


def revoke_consent(db: Session, owner_id: uuid.UUID) -> int:
    rows = (
        db.query(PersonalizationConsent)
        .filter(
            PersonalizationConsent.owner_id == owner_id,
            PersonalizationConsent.purpose == PERSONALIZATION_TRAINING_PURPOSE,
            PersonalizationConsent.revoked_at.is_(None),
        )
        .all()
    )
    now = utcnow()
    for row in rows:
        row.revoked_at = now
    db.flush()
    return len(rows)


def capabilities(db: Session, owner_id: uuid.UUID) -> List[Dict[str, Any]]:
    """The three capability classes, labelled by what is actually delivered."""
    return [
        {
            "key": "market_model",
            "label_zh": "市場資料模型（A）",
            "status": "AVAILABLE",
            "description_zh": (
                "以 OHLCV 技術特徵預測指定市場目標（次日漲跌方向）。"
                "不學習個別用戶的決策。"
            ),
            "surface": "/training · ML 模型訓練",
        },
        {
            "key": "rule_check",
            "label_zh": "策略規則檢查（B）",
            "status": "AVAILABLE",
            "description_zh": (
                "以已保存的規則檢查操作是否符合計劃（例如風控與掛單規則）。"
                "屬規則比對，不是權重學習。"
            ),
            "surface": "模擬工作區 · 手動／規則委託、風控拒絕原因",
        },
        {
            "key": "personalization",
            "label_zh": "個人化模型（C）",
            "status": CAPABILITY_STATUS,
            "description_zh": (
                "以明確同意、可追溯的個人決策資料訓練，學習該用戶在特定策略下的"
                "決策偏好。資料不足時直接拒絕，不以 A 或 B 冒充；"
                "推論結果只描述決策選擇，不是市場方向或投資建議。"
            ),
            "surface": "本頁 · 個人化訓練資格與個人策略學習",
        },
    ]
