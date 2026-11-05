"""Agent strategy learning — the isolated screening slice.

The question this slice answers is narrow: given an existing locked rule
strategy, which of its entry signals were worth taking, judged by a
cost-adjusted outcome under an explicit, versioned execution assumption?

It is not reinforcement learning, not autonomous strategy creation, not owner
imitation, and not a claim that a model will be profitable. It also is not the
personalization task: nothing here reads ``HumanDecisionEvent`` or
``DecisionLabel``.

Deliberate boundaries:

* A candidate is a rule signal, not a trade. Every signal is kept, including
  signals whose outcome is unknown or immature.
* "Observed" means an actual paper BUY and SELL round trip matched by lineage.
  Everything else is a deterministic counterfactual replay and says so.
* A non-take, a risk refusal, a cash shortfall and an execution failure are
  separate fields. None of them is stored as a loss.
* Features are read through the same as-of rule as personalization: a daily
  bar becomes readable at the next calendar day 00:00 UTC.
* Splits are chronological and grouped by decision date, with a gap and a
  purge wherever a label window crosses a boundary. No shuffling.
* No consent is granted or accepted here. A snapshot without the research
  consent is recorded as refused, never as a training set.
"""

import hashlib
import json
import math
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.auth.candidate_access import is_local_database_url
from app.auth.provenance import UNKNOWN
from app.engine.fill_math import quantity_precision_for, size_position
from app.config import (
    AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION,
    AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION,
    get_settings,
)
from app.lib.timeutil import utcnow as _utcnow
from app.models.agent_strategy import (
    AgentCandidate,
    AgentCandidateOutcome,
    AgentDatasetSnapshot,
    AgentStrategyConsent,
)
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
)
from app.services.personal_dataset import (
    FEATURE_SCHEMA_VERSION,
    daily_bar_available_at,
    features_as_of,
)
from app.services.model_activation import owner_activation_summary

PURPOSE = "agent_strategy_screening"
CAPABILITY_STATUS = "DEVELOPMENT"
CAPABILITY_STATUS_LABEL_ZH = "開發中／尚不可在原客戶環境執行"
CONSENT_VERSION = "agent-strategy-learning-v1"
# The owner has to accept this exact text, in this version, for themselves.
CONSENT_ACCEPTANCE_TEXT_VERSION = "agent-strategy-consent-text-v1"
CONSENT_ACCEPTANCE_TEXT_ZH = (
    "我確認我是此帳號的持有人，並同意 Alethra 以此用途使用本帳號的"
    "紙上委託、成交，以及由已鎖定規則策略產生的候選訊號與調整後結果，"
    "作候選篩選模型的開發與評估。我明白這不是投資建議、不保證利潤、"
    "不會自動下單或成為已啟用的客戶功能，我可以隨時撤銷。"
)
LABEL_DEFINITION_VERSION = "cost-adjusted-net-return-v1"
# Stored in the snapshot's authorization-basis columns. It says plainly that
# the dataset was created under the development fixture, not owner consent.
SNAPSHOT_AUTHORIZATION_MODE = "test_only_fixture"

EXECUTION_POLICY_VERSION = "next-open-fixed-hold-v1"
EXIT_POLICY = "close_after_5_bars"
HOLD_BARS = 5
COMMISSION_RATE = 0.001
SLIPPAGE_RATE = 0.0005
INITIAL_NOTIONAL = 100000.0

SPLIT_POLICY_VERSION = "chronological-grouped-gap-v1"
TRAIN_FRACTION = 0.6
VALIDATION_FRACTION = 0.2
GAP_DAYS = HOLD_BARS + 1
MIN_SPLIT_GROUPS = 10
MIN_SCREENING_CANDIDATES = 20

RULE_KEY = "sma_crossover"

EXCLUSION_LABELS_ZH = {
    "future_leakage": "決策時點晚於資料截止，屬未來資料",
    "missing_market_data": "決策時點缺少已儲存市場資料",
    "missing_features": "特徵暖機不足或含無效值",
    "invalid_context": "候選缺少可用策略／標的／時點",
    "no_execution_bar": "訊號後沒有可用的執行 bar",
    "label_window_incomplete": "固定持有期尚未完結，標籤未成熟",
    "insufficient_cash": "按固定名義金額無法買入一手",
    "invalid_execution_price": "執行 bar 價格無效",
    "no_matching_execution": "沒有可重播的執行 bar",
    "functional_test_trade": "功能性測試交易，不作為學習樣本",
    "no_strategy_lineage": "既有紙上交易沒有連結策略版本",
    "no_candidate_lineage": "找不到對應的候選訊號血緣",
    "unconfirmed_candidate_linkage": "只有共用日期，未確認候選血緣，不可納入研究",
    "non_human_execution": "實際執行不是由已驗證的本人工作階段寫入",
    "exit_policy_mismatch": "實際出場政策與固定持有目標不一致，僅保留為證據",
    "agent_strategy_consent_not_configured": "尚未設定代理策略研究用途同意",
    "insufficient_time_groups": "時間分組不足，無法建立時間切分",
    "insufficient_post_purge_samples": "gap／purge 後有切分為空",
}

FEATURE_EXCLUSIONS = {
    "future_leakage",
    "missing_market_data",
    "missing_features",
    "invalid_context",
}

FUNCTIONAL_TEST_MARKERS = (
    "functional_test",
    "functional test",
    "e2e",
    "smoke test",
    "fixture",
    "test trade",
    "測試交易",
)


class AgentStrategyRefused(ValueError):
    def __init__(self, code: str, message_zh: str, details: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}


# ---------------------------------------------------------------------------
# Policy and capabilities
# ---------------------------------------------------------------------------


def capability() -> Dict[str, Any]:
    return {
        "key": "agent_strategy_screening",
        "label_zh": "代理候選訊號篩選",
        "status": CAPABILITY_STATUS,
        "status_label_zh": CAPABILITY_STATUS_LABEL_ZH,
        "description_zh": (
            "對既有規則策略的入場訊號，在明確的執行、出場與成本假設下，"
            "估計每個候選的調整後結果，協助判斷 take／skip。"
            "不是自我學習的新策略，不是模仿 owner 決策，不是市場方向預測，"
            "亦不保證利潤或自動實盤。"
        ),
        "task_type": PURPOSE,
        "rule_strategy_scope": RULE_KEY,
        "surface": "隔離研究環境 · 代理策略學習（開發中）",
    }


def consent_policy() -> Dict[str, Any]:
    return {
        "purpose": PURPOSE,
        "consent_version": CONSENT_VERSION,
        "acceptance_text_version": CONSENT_ACCEPTANCE_TEXT_VERSION,
        "acceptance_text_zh": CONSENT_ACCEPTANCE_TEXT_ZH,
        "acceptance_available": True,
        "acceptance_owner_only": True,
        "fail_closed": True,
        "reason_code": "AGENT_STRATEGY_CONSENT_NOT_GRANTED",
        "data_scope_zh": (
            "此帳號擁有的紙上委託、成交，以及由已鎖定規則策略產生的候選訊號。"
            "不包括其他帳號、客戶 WB 或保留研究窗口的資料。"
        ),
        "retention_zh": "直到 owner 撤銷；資料集快照可刪除，拒絕紀錄保留原因。",
        "revoke_zh": "owner 可隨時撤銷；撤銷只影響之後的訓練，已建立的紀錄保留。",
        "output_limits_zh": (
            "只作候選篩選輔助；不建立自主策略、不模仿 owner、不保證利潤、"
            "不自動實盤、不自動重訓。"
        ),
        "training_allowed": False,
    }


def consent_status(db: Session, owner_id: uuid.UUID) -> Dict[str, Any]:
    """The owner's own consent state, read from the recorded rows.

    Silence is never permission: no row means ``not_configured`` and every
    training path stays closed.
    """
    active = (
        db.query(AgentStrategyConsent)
        .filter(
            AgentStrategyConsent.owner_id == owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "active",
        )
        .order_by(AgentStrategyConsent.accepted_at.desc())
        .first()
    )
    if active is not None:
        return {
            "purpose": PURPOSE,
            "consent_version": active.policy_version,
            "status": "active",
            "fail_closed": False,
            "reason_code": None,
            "accepted_at": active.accepted_at.isoformat()
            if active.accepted_at
            else None,
            "accepted_by": "owner",
            "acceptance_text_version": active.acceptance_text_version,
            "consent_id": str(active.id),
            "real_owner_consent": True,
        }

    revoked = (
        db.query(AgentStrategyConsent)
        .filter(
            AgentStrategyConsent.owner_id == owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "revoked",
        )
        .order_by(AgentStrategyConsent.revoked_at.desc())
        .first()
    )
    if revoked is not None:
        return {
            "purpose": PURPOSE,
            "consent_version": revoked.policy_version,
            "status": "revoked",
            "fail_closed": True,
            "reason_code": "AGENT_STRATEGY_CONSENT_REVOKED",
            "accepted_at": revoked.accepted_at.isoformat()
            if revoked.accepted_at
            else None,
            "accepted_by": "owner",
            "acceptance_text_version": revoked.acceptance_text_version,
            "revoked_at": revoked.revoked_at.isoformat()
            if revoked.revoked_at
            else None,
            "revoke_reason": revoked.revoke_reason,
            "real_owner_consent": False,
        }

    return {
        "purpose": PURPOSE,
        "consent_version": CONSENT_VERSION,
        "status": "not_configured",
        "fail_closed": True,
        "reason_code": "AGENT_STRATEGY_CONSENT_NOT_CONFIGURED",
        "accepted_at": None,
        "accepted_by": None,
        "real_owner_consent": False,
    }


def _normalise_acceptance(text: str) -> str:
    return " ".join((text or "").split())


def record_consent(
    db: Session,
    owner_id: uuid.UUID,
    *,
    policy_version: str,
    acceptance_text_version: str,
    acknowledgement: str,
) -> Dict[str, Any]:
    """Record that the authenticated owner accepted the policy themselves.

    There is no parameter for an accepting user other than ``owner_id``: the
    route passes the authenticated user, so a request cannot record consent for
    somebody else. The exact policy text has to be echoed back, so an accidental
    ``accept: true`` without reading the text is refused.
    """
    if policy_version != CONSENT_VERSION:
        raise AgentStrategyRefused(
            "agent_strategy_consent_policy_version_mismatch",
            "同意政策版本不符，未記錄任何同意。",
            {"expected": CONSENT_VERSION, "received": policy_version},
        )
    if acceptance_text_version != CONSENT_ACCEPTANCE_TEXT_VERSION:
        raise AgentStrategyRefused(
            "agent_strategy_consent_text_version_mismatch",
            "同意文本版本不符，未記錄任何同意。",
            {
                "expected": CONSENT_ACCEPTANCE_TEXT_VERSION,
                "received": acceptance_text_version,
            },
        )
    if _normalise_acceptance(acknowledgement) != _normalise_acceptance(
        CONSENT_ACCEPTANCE_TEXT_ZH
    ):
        raise AgentStrategyRefused(
            "agent_strategy_consent_acknowledgement_mismatch",
            "確認文字與政策全文不一致，未記錄任何同意。",
            {"persisted": False},
        )

    existing = (
        db.query(AgentStrategyConsent)
        .filter(
            AgentStrategyConsent.owner_id == owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "active",
        )
        .with_for_update()
        .first()
    )
    # Accepting the same policy and text again is the same decision, not a new
    # consent: return the live row so repeated clicks do not multiply evidence.
    if (
        existing is not None
        and existing.policy_version == policy_version
        and existing.acceptance_text_version == acceptance_text_version
    ):
        db.rollback()
        payload = consent_status(db, owner_id)
        payload["created"] = False
        payload["deduplicated"] = True
        return payload

    now = _utcnow()
    if existing is not None:
        existing.status = "superseded"
        existing.superseded_at = now

    row = AgentStrategyConsent(
        owner_id=owner_id,
        # Same authenticated user; stored twice so a mismatch is auditable.
        accepted_by_user_id=owner_id,
        purpose=PURPOSE,
        policy_version=policy_version,
        acceptance_text_version=acceptance_text_version,
        acknowledgement_json={
            "text_zh": CONSENT_ACCEPTANCE_TEXT_ZH,
            "text_version": acceptance_text_version,
        },
        status="active",
        accepted_at=now,
    )
    try:
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    payload = consent_status(db, owner_id)
    payload["created"] = True
    payload["deduplicated"] = False
    payload["consent_id"] = str(row.id)
    return payload


def revoke_consent(
    db: Session, owner_id: uuid.UUID, *, reason: Optional[str] = None
) -> Dict[str, Any]:
    active = (
        db.query(AgentStrategyConsent)
        .filter(
            AgentStrategyConsent.owner_id == owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "active",
        )
        .with_for_update()
        .first()
    )
    if active is None:
        return {"revoked": False, "reason": "no_active_consent", **consent_status(db, owner_id)}
    active.status = "revoked"
    active.revoked_at = _utcnow()
    active.revoke_reason = (reason or "owner_revoked")[:64]
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"revoked": True, **consent_status(db, owner_id)}


def training_authorization(
    db: Session, owner_id: uuid.UUID, settings=None
) -> Dict[str, Any]:
    """Separate judgments for the training/inference chain.

    Two bases can authorize a training run and they are never merged:

    * the development fixture, which is a test harness and says nothing about
      a real owner;
    * ``AGENT_STRATEGY_OWNER_CONSENT_MODE`` in a local trial, which only becomes
      a permission when the authenticated owner's own consent row exists.

    Neither the fixture nor the switch is consent, and neither alone is enough.
    """
    settings = settings or get_settings()
    configured = (
        getattr(settings, "agent_strategy_test_training_authorization", "") or ""
    ).strip()
    environment_allowed = (
        getattr(settings, "app_env", "") == "development"
        and bool(getattr(settings, "agent_strategy_screening_enabled", False))
        and is_local_database_url(getattr(settings, "database_url", ""))
    )
    fixture_granted = (
        configured == AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION
        and environment_allowed
    )
    owner_consent_mode = (
        bool(getattr(settings, "agent_strategy_owner_consent_mode", False))
        and environment_allowed
    )
    consent = consent_status(db, owner_id)
    consent_active = consent["status"] == "active"
    trial_granted = consent_active and owner_consent_mode

    if not consent_active:
        reason_code = "agent_strategy_consent_not_configured"
    elif not (fixture_granted or owner_consent_mode):
        reason_code = "agent_strategy_training_authorization_not_configured"
    else:
        reason_code = None

    if fixture_granted and consent_active:
        authorization_mode = "test_only_fixture_and_owner_consent"
    elif trial_granted:
        authorization_mode = "owner_consent_local_trial"
    else:
        authorization_mode = "none"

    return {
        "purpose": PURPOSE,
        "environment_allowed": environment_allowed,
        "fixture_granted": fixture_granted,
        "owner_consent_mode": owner_consent_mode,
        "authorization_mode": authorization_mode,
        "real_owner_consent": consent_active,
        "consent_status": consent["status"],
        "consent_record_id": consent.get("consent_id"),
        "granted": trial_granted or (fixture_granted and consent_active),
        "reason_code": reason_code,
        # Reading recorded approvals, not a constant: training is still refused
        # when this says APPROVED, and an APPROVED here authorises nothing new.
        **owner_activation_summary(db, owner_id),
        "movable_to_customer_environment": False,
    }


def snapshot_authorization(
    settings=None, *, owner_consent_active: bool = False
) -> Dict[str, Any]:
    """Whether this process may freeze a ready dataset, and on what basis.

    A snapshot can be created on the development fixture or, in a local owner
    trial, on the owner's own recorded consent. The fixture basis is never
    reported as consent, and consent without the operator switch is not a
    permission either.
    """
    settings = settings or get_settings()
    configured = (
        getattr(settings, "agent_strategy_test_snapshot_authorization", "") or ""
    ).strip()
    environment_allowed = (
        getattr(settings, "app_env", "") == "development"
        and bool(getattr(settings, "agent_strategy_screening_enabled", False))
        and is_local_database_url(getattr(settings, "database_url", ""))
    )
    fixture_granted = (
        configured == AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION
        and environment_allowed
    )
    owner_consent_mode = (
        bool(getattr(settings, "agent_strategy_owner_consent_mode", False))
        and environment_allowed
    )
    trial_granted = owner_consent_active and owner_consent_mode
    granted = fixture_granted or trial_granted
    if fixture_granted:
        mode = SNAPSHOT_AUTHORIZATION_MODE
    elif trial_granted:
        mode = "owner_consent_local_trial"
    else:
        mode = "none"
    if granted:
        reason_code = None
    elif configured:
        reason_code = "AGENT_STRATEGY_TEST_AUTHORIZATION_ENVIRONMENT_REFUSED"
    elif owner_consent_mode:
        reason_code = "agent_strategy_consent_not_configured"
    else:
        reason_code = "AGENT_STRATEGY_TEST_AUTHORIZATION_NOT_CONFIGURED"
    return {
        "purpose": PURPOSE,
        "mode": mode,
        "authorization_id": (
            AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION if fixture_granted else None
        ),
        "granted": granted,
        "environment_allowed": environment_allowed,
        "owner_consent_mode": owner_consent_mode,
        "real_owner_consent": bool(trial_granted),
        "movable_to_customer_environment": False,
        "reason_code": reason_code,
    }


# ---------------------------------------------------------------------------
# Rule versions and stored bars
# ---------------------------------------------------------------------------


def _get_owned_locked_version(
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
        raise AgentStrategyRefused(
            "strategy_version_not_found", "找不到此帳號擁有的策略版本。"
        )
    # A version is locked by stamping ``locked_at``; there is no status column.
    if version.locked_at is None:
        raise AgentStrategyRefused(
            "strategy_version_not_locked",
            "候選篩選只接受已鎖定的規則策略版本，避免規則在收集期間變動。",
            {"strategy_version_id": str(strategy_version_id)},
        )
    return version


def _rule_spec(version: ExperimentVersion, instrument_id: Optional[str]) -> Dict[str, Any]:
    spec = version.strategy_spec or {}
    template = spec.get("template_type")
    if template != RULE_KEY:
        raise AgentStrategyRefused(
            "unsupported_rule",
            f"本切片只支援 {RULE_KEY} 規則策略，收到 {template!r}。",
        )
    params = spec.get("signal_parameters") or {}
    short = params.get("short_window")
    long = params.get("long_window")
    if (
        not isinstance(short, int)
        or not isinstance(long, int)
        or short < 2
        or long <= short
    ):
        raise AgentStrategyRefused(
            "invalid_rule_parameters",
            "SMA 參數無效：需要 short_window >= 2 且 long_window > short_window。",
            {"signal_parameters": params},
        )
    assets = spec.get("asset_ids") or []
    if not assets:
        raise AgentStrategyRefused(
            "strategy_version_missing_instrument",
            "策略版本沒有指定標的。",
        )
    instrument = instrument_id or assets[0]
    if instrument not in assets:
        raise AgentStrategyRefused(
            "instrument_not_in_strategy",
            "指定標的不在此策略版本的 asset_ids 內。",
            {"instrument_id": instrument, "asset_ids": assets},
        )
    return {
        "instrument_id": instrument,
        "short_window": short,
        "long_window": long,
        "start_date": spec.get("start_date"),
        "end_date": spec.get("end_date"),
        "rule_parameters": {
            "template_type": template,
            "short_window": short,
            "long_window": long,
        },
    }


def _parse_date(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat((value or "").strip())
    except (ValueError, TypeError):
        return None


def _load_daily_bars(
    db: Session,
    instrument_id: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> List[MarketDataBar]:
    query = db.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == instrument_id
    )
    start_day = _parse_date(start)
    end_day = _parse_date(end)
    if start_day is not None:
        query = query.filter(MarketDataBar.bar_date >= start_day)
    if end_day is not None:
        query = query.filter(MarketDataBar.bar_date <= end_day)
    rows = query.order_by(MarketDataBar.bar_date, MarketDataBar.provider_id).all()
    daily = [row for row in rows if row.bar_timestamp == datetime.combine(row.bar_date, datetime.min.time())]
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
    return [row for _, row in sorted(by_date.items())]


# ---------------------------------------------------------------------------
# Candidate collection
# ---------------------------------------------------------------------------


def _candidate_dedupe_key(signal_date: date, execution_date: date) -> str:
    return (
        f"{RULE_KEY}:entry:{signal_date.isoformat()}:"
        f"{execution_date.isoformat()}:{EXECUTION_POLICY_VERSION}"
    )


def collect_candidates(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    actor_type: str = UNKNOWN,
) -> Dict[str, Any]:
    """Collect entry signals from a locked SMA rule. Idempotent by dedupe key."""
    version = _get_owned_locked_version(db, owner_id, strategy_version_id)
    rule = _rule_spec(version, instrument_id)
    instrument = rule["instrument_id"]
    bars = _load_daily_bars(
        db,
        instrument,
        start=rule["start_date"],
        end=rule["end_date"],
        provider_id=provider_id,
    )
    if len(bars) < rule["long_window"] + 2:
        raise AgentStrategyRefused(
            "missing_market_data",
            "已儲存日線不足以計算此 SMA 規則。",
            {
                "instrument_id": instrument,
                "stored_bars": len(bars),
                "required_bars": rule["long_window"] + 2,
            },
        )

    selected_provider = bars[0].provider_id if bars else provider_id
    closes = [bar.close for bar in bars]
    created = 0
    existing = 0
    feature_excluded = 0
    feasibility_counts: Counter = Counter()
    new_candidates: List[AgentCandidate] = []

    for b in range(rule["long_window"] + 1, len(bars)):
        i = b - 1
        sma_short = sum(closes[i - rule["short_window"] + 1: i + 1]) / rule["short_window"]
        sma_long = sum(closes[i - rule["long_window"] + 1: i + 1]) / rule["long_window"]
        prev_short = sum(closes[i - rule["short_window"]: i]) / rule["short_window"]
        prev_long = sum(closes[i - rule["long_window"]: i]) / rule["long_window"]
        cross_up = prev_short <= prev_long and sma_short > sma_long
        if not cross_up:
            continue

        signal_day = bars[i].bar_date
        execution_bar = bars[b]
        decision_instant = daily_bar_available_at(signal_day)
        dedupe_key = _candidate_dedupe_key(signal_day, execution_bar.bar_date)

        exists = (
            db.query(AgentCandidate.id)
            .filter(
                AgentCandidate.owner_id == owner_id,
                AgentCandidate.strategy_version_id == strategy_version_id,
                AgentCandidate.instrument_id == instrument,
                AgentCandidate.dedupe_key == dedupe_key,
            )
            .first()
        )
        if exists:
            existing += 1
            continue

        features, feature_names, feature_bar_date, feature_exclusion = features_as_of(
            db,
            instrument,
            signal_day.isoformat(),
            as_of_instant=decision_instant,
            provider_id=selected_provider,
        )
        rejection_reason = feature_exclusion
        if feature_exclusion:
            feature_excluded += 1
            features = {}
            feature_names = []

        execution_feasibility = "executable"
        if execution_bar.open is None or execution_bar.open <= 0:
            execution_feasibility = "execution_failure"
            rejection_reason = "invalid_execution_price"
        else:
            affordable = size_position(
                INITIAL_NOTIONAL,
                execution_bar.open,
                COMMISSION_RATE,
                quantity_precision_for(instrument),
            )
            if affordable <= 0:
                execution_feasibility = "insufficient_cash"
                rejection_reason = "insufficient_cash"
        feasibility_counts[execution_feasibility] += 1

        candidate = AgentCandidate(
            owner_id=owner_id,
            strategy_version_id=strategy_version_id,
            instrument_id=instrument,
            candidate_kind="entry",
            rule_key=RULE_KEY,
            rule_parameters_json=rule["rule_parameters"],
            logged_action="rule_entry_signal",
            created_by_actor_type=actor_type,
            signal_date=signal_day.isoformat(),
            decision_instant=decision_instant,
            decision_instant_source="daily_bar_available_at",
            data_cutoff_at=signal_day.isoformat(),
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            feature_names_json=feature_names or [],
            features_json=features or {},
            feature_bar_date=feature_bar_date,
            execution_policy_version=EXECUTION_POLICY_VERSION,
            execution_feasibility=execution_feasibility,
            rejection_reason=rejection_reason,
            dedupe_key=dedupe_key,
        )
        db.add(candidate)
        db.flush()
        new_candidates.append(candidate)
        created += 1

    db.commit()

    return {
        "strategy_version_id": str(strategy_version_id),
        "instrument_id": instrument,
        "rule_key": RULE_KEY,
        "rule_parameters": rule["rule_parameters"],
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "stored_bars": len(bars),
        "created": created,
        "existing": existing,
        "feature_excluded": feature_excluded,
        "execution_feasibility": dict(feasibility_counts),
        "candidates": [candidate_payload(c) for c in new_candidates],
    }


# ---------------------------------------------------------------------------
# Observed vs counterfactual outcomes
# ---------------------------------------------------------------------------


def is_functional_test_order(
    order: PaperOrder, explicit_ids: Optional[Iterable[str]] = None
) -> bool:
    if explicit_ids and str(order.id) in {str(value) for value in explicit_ids}:
        return True
    text = f"{order.rationale or ''} {order.idempotency_key or ''}".lower()
    return any(marker in text for marker in FUNCTIONAL_TEST_MARKERS)


def _observed_round_trip(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    instrument_id: str,
    entry_date: str,
    exit_date: str,
    explicit_functional_test_ids: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    base = (
        db.query(PaperFill)
        .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
        .join(PaperPortfolio, PaperOrder.portfolio_id == PaperPortfolio.id)
        .outerjoin(DecisionRecord, PaperOrder.decision_id == DecisionRecord.id)
        .filter(
            PaperPortfolio.owner_id == owner_id,
            PaperOrder.asset_id == instrument_id,
            PaperOrder.status == OrderStatus.filled,
        )
    )
    buy = (
        base.filter(
            PaperOrder.side == OrderSide.BUY,
            PaperFill.execution_date == entry_date,
            or_(
                DecisionRecord.linked_experiment_version_id == strategy_version_id,
                PaperOrder.model_or_strategy_version_id == strategy_version_id,
            ),
        )
        .order_by(PaperFill.id)
        .first()
    )
    if buy is None or is_functional_test_order(buy.order, explicit_functional_test_ids):
        return None
    if buy.order.actor_type != "human":
        return None
    # The first SELL at or after entry is the actual exit candidate. When its
    # date differs from the fixed-hold target, the actual round trip used a
    # different exit policy; it is recorded as evidence but must not replace
    # the declared target automatically.
    sell = (
        base.filter(
            PaperOrder.side == OrderSide.SELL,
            PaperFill.execution_date >= entry_date,
            PaperOrder.portfolio_id == buy.order.portfolio_id,
        )
        .order_by(PaperFill.execution_date, PaperFill.id)
        .first()
    )
    if sell is None or is_functional_test_order(sell.order, explicit_functional_test_ids):
        return {
            "buy": buy,
            "sell": None,
            "note": "observed_entry_without_observed_exit",
        }
    if sell.execution_date != exit_date:
        return {
            "buy": buy,
            "sell": sell,
            "note": "observed_exit_policy_mismatch",
            "target_exit_date": exit_date,
            "actual_exit_date": sell.execution_date,
        }
    return {"buy": buy, "sell": sell, "note": None}


def _cost_model() -> Dict[str, Any]:
    return {
        "version": EXECUTION_POLICY_VERSION,
        "entry_rule": "next_bar_open_with_slippage",
        "exit_rule": EXIT_POLICY,
        "hold_bars": HOLD_BARS,
        "commission_rate": COMMISSION_RATE,
        "slippage_rate": SLIPPAGE_RATE,
        "initial_notional": INITIAL_NOTIONAL,
        "counterfactual": True,
    }


def _str_or_none(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 10)
    except (TypeError, ValueError):
        return None


def _outcome_signature(values: Dict[str, Any]) -> Tuple[Any, ...]:
    """Content identity of one outcome version, used for idempotent writes.

    Accepts both the persistence payload (``notes_json``) and the serializer
    payload (``notes``) so the same comparison works on either side.
    """
    notes = values.get("notes_json")
    if notes is None:
        notes = values.get("notes")
    return (
        values.get("outcome_status"),
        values.get("label_maturity"),
        bool(values.get("label_eligible")),
        values.get("outcome_source"),
        bool(values.get("counterfactual")),
        _str_or_none(values.get("observed_order_id")),
        _str_or_none(values.get("observed_fill_id")),
        values.get("entry_execution_date"),
        values.get("exit_execution_date"),
        _float_or_none(values.get("entry_price")),
        _float_or_none(values.get("exit_price")),
        _float_or_none(values.get("gross_return_pct")),
        _float_or_none(values.get("net_return_pct")),
        values.get("cost_model_version"),
        values.get("exit_policy"),
        values.get("exclusion_reason"),
        json.dumps(notes or {}, sort_keys=True, default=str),
    )


def _outcome_economic_signature(values: Dict[str, Any]) -> Tuple[Any, ...]:
    """Result identity without descriptive notes, for deduplicating evidence.

    The same declared replay can acquire an extra note when an observed entry
    appears later; that must not create a second copy of the same result.
    """
    return _outcome_signature(values)[:-1]


def build_outcomes(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
    explicit_functional_test_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Append outcome versions; a previously selected result is never rewritten.

    The selected result may change (for example when an actual paper round trip
    appears), but the earlier version stays in place with its own policy,
    timestamp and source so a frozen snapshot remains reproducible.
    """
    version = _get_owned_locked_version(db, owner_id, strategy_version_id)
    rule = _rule_spec(version, instrument_id)
    instruments = (
        [rule["instrument_id"]] if instrument_id else list(version.strategy_spec.get("asset_ids") or [])
    )
    outcomes_created = 0
    outcomes_superseded = 0
    outcomes_preserved = 0
    preserved_observed = 0
    now = _utcnow()

    for instrument in instruments:
        bars = _load_daily_bars(
            db,
            instrument,
            start=rule["start_date"],
            end=rule["end_date"],
        )
        if not bars:
            continue
        index_by_date = {bar.bar_date.isoformat(): idx for idx, bar in enumerate(bars)}
        candidates = (
            db.query(AgentCandidate)
            .filter(
                AgentCandidate.owner_id == owner_id,
                AgentCandidate.strategy_version_id == strategy_version_id,
                AgentCandidate.instrument_id == instrument,
            )
            .order_by(AgentCandidate.signal_date, AgentCandidate.id)
            .all()
        )
        for candidate in candidates:
            signal_idx = index_by_date.get(candidate.signal_date)
            evaluation = _evaluate_candidate(
                db,
                owner_id,
                strategy_version_id,
                candidate,
                bars,
                signal_idx,
                explicit_functional_test_ids,
            )
            selected_payload = evaluation["selected"]
            selected_reason = evaluation["selected_reason"]
            additional = list(evaluation["additional"])

            versions = (
                db.query(AgentCandidateOutcome)
                .filter(AgentCandidateOutcome.candidate_id == candidate.id)
                .order_by(AgentCandidateOutcome.version_seq)
                .all()
            )
            current = next((row for row in versions if row.is_current), None)
            # Compare result identity, not descriptive notes: a declared replay
            # that merely gains an "observed entry exists" note is still the
            # same result and must not be re-versioned.
            current_signature = (
                _outcome_economic_signature(outcome_payload(current)) if current else None
            )
            selected_signature = _outcome_economic_signature(selected_payload)

            if (
                current is not None
                and current.outcome_status == "observed"
                and current_signature != selected_signature
            ):
                # An observed result is terminal. A later recomputation must
                # not demote it; keep the evidence as a non-selected version.
                preserved_observed += 1
                additional.insert(0, (selected_payload, "not_selected_after_observed"))
                selected_payload = None
                selected_reason = None
            elif current_signature is not None and current_signature == selected_signature:
                # Recomputing the same evidence must not create a new version.
                outcomes_preserved += 1
                if current.selection_reason is None:
                    current.selection_reason = selected_reason
                selected_payload = None
                selected_reason = None

            if selected_payload is not None:
                if current is not None:
                    current.is_current = False
                    current.superseded_at = now
                    outcomes_superseded += 1
                    # Flush the demotion before the insert so the partial
                    # unique index never sees two selected rows.
                    db.flush()
                next_seq = max((row.version_seq or 1) for row in versions) + 1 if versions else 1
                created = AgentCandidateOutcome(
                    candidate_id=candidate.id,
                    version_seq=next_seq,
                    is_current=True,
                    selection_reason=selected_reason,
                    **selected_payload,
                )
                db.add(created)
                db.flush()
                versions.append(created)
                outcomes_created += 1

            known = {
                _outcome_economic_signature(outcome_payload(row)) for row in versions
            }
            next_seq = max((row.version_seq or 1) for row in versions) + 1 if versions else 1
            for payload, reason in additional:
                signature = _outcome_economic_signature(payload)
                if signature in known:
                    continue
                db.add(
                    AgentCandidateOutcome(
                        candidate_id=candidate.id,
                        version_seq=next_seq,
                        is_current=False,
                        superseded_at=now,
                        selection_reason=reason,
                        **payload,
                    )
                )
                db.flush()
                known.add(signature)
                next_seq += 1
                outcomes_created += 1

    db.commit()

    current_rows = (
        db.query(AgentCandidate, AgentCandidateOutcome)
        .join(
            AgentCandidateOutcome,
            and_(
                AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                AgentCandidateOutcome.is_current.is_(True),
            ),
        )
        .filter(
            AgentCandidate.owner_id == owner_id,
            AgentCandidate.strategy_version_id == strategy_version_id,
        )
        .all()
    )
    status_counts = Counter(outcome.outcome_status for _, outcome in current_rows)
    history_versions = (
        db.query(AgentCandidateOutcome)
        .join(
            AgentCandidate,
            AgentCandidateOutcome.candidate_id == AgentCandidate.id,
        )
        .filter(
            AgentCandidate.owner_id == owner_id,
            AgentCandidate.strategy_version_id == strategy_version_id,
        )
        .count()
    )
    return {
        "strategy_version_id": str(strategy_version_id),
        "created": outcomes_created,
        "updated": outcomes_superseded,
        "superseded": outcomes_superseded,
        "preserved": outcomes_preserved,
        "preserved_observed": preserved_observed,
        "history_versions": history_versions,
        "selection_policy": "one_current_version_per_candidate",
        "outcome_distribution": dict(status_counts),
    }


def _selected(
    base: Dict[str, Any],
    status: str,
    maturity: str,
    source: str,
    exclusion: Optional[str],
    reason: str,
    **extra: Any,
) -> Tuple[Dict[str, Any], str]:
    return (
        {
            **base,
            "outcome_status": status,
            "label_maturity": maturity,
            "outcome_source": source,
            "exclusion_reason": exclusion,
            **extra,
        },
        reason,
    )


def _round_trip_returns(buy: PaperFill, sell: PaperFill) -> Tuple[Optional[float], Optional[float]]:
    net = None
    if buy.net_cash_change:
        net = (
            (sell.net_cash_change + buy.net_cash_change)
            / abs(buy.net_cash_change)
            * 100.0
        )
    gross = None
    if buy.execution_price_before_costs:
        gross = (
            sell.execution_price_before_costs / buy.execution_price_before_costs - 1
        ) * 100.0
    return gross, net


def _replay_prices(
    entry_bar: MarketDataBar, exit_bar: MarketDataBar
) -> Optional[Tuple[float, float, float, float]]:
    if entry_bar.open is None or entry_bar.open <= 0 or exit_bar.close is None:
        return None
    entry_price = entry_bar.open * (1 + SLIPPAGE_RATE)
    exit_price = exit_bar.close * (1 - SLIPPAGE_RATE)
    gross = (exit_price / entry_price - 1) * 100.0
    net = (
        (exit_price * (1 - COMMISSION_RATE))
        / (entry_price * (1 + COMMISSION_RATE))
        - 1
    ) * 100.0
    return entry_price, exit_price, gross, net


def _evaluate_candidate(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    candidate: AgentCandidate,
    bars: List[MarketDataBar],
    signal_idx: Optional[int],
    explicit_functional_test_ids: Optional[Iterable[str]],
) -> Dict[str, Any]:
    """Evaluate one candidate into a selected result plus retained evidence.

    ``additional`` holds results that are evidence but are not the selected
    outcome, such as the fixed-hold replay once an exact observed round trip
    exists, or an observed round trip whose exit policy differs from the
    declared target.
    """
    cost_model = _cost_model()
    base = {
        "label_eligible": False,
        "counterfactual": False,
        "observed_order_id": None,
        "observed_fill_id": None,
        "entry_execution_date": None,
        "exit_execution_date": None,
        "entry_price": None,
        "exit_price": None,
        "gross_return_pct": None,
        "net_return_pct": None,
        "cost_model_version": EXECUTION_POLICY_VERSION,
        "cost_model_json": cost_model,
        "exit_policy": EXIT_POLICY,
        "exclusion_reason": None,
        "notes_json": {},
    }

    def evaluation(payload: Dict[str, Any], reason: str, additional=None) -> Dict[str, Any]:
        return {
            "selected": payload,
            "selected_reason": reason,
            "additional": additional or [],
        }

    if signal_idx is None:
        payload, reason = _selected(
            base, "unavailable", "unavailable", "none", "no_matching_execution",
            "no_matching_execution",
        )
        return evaluation(payload, reason)

    execution_idx = signal_idx + 1
    exit_idx = execution_idx + HOLD_BARS
    if execution_idx >= len(bars):
        payload, reason = _selected(
            base, "immature", "immature", "none", "no_execution_bar",
            "no_execution_bar",
        )
        return evaluation(payload, reason)
    if exit_idx >= len(bars):
        payload, reason = _selected(
            base, "immature", "immature", "none", "label_window_incomplete",
            "label_window_incomplete",
            entry_execution_date=bars[execution_idx].bar_date.isoformat(),
        )
        return evaluation(payload, reason)

    entry_bar = bars[execution_idx]
    exit_bar = bars[exit_idx]
    entry_date = entry_bar.bar_date.isoformat()
    exit_date = exit_bar.bar_date.isoformat()
    base["entry_execution_date"] = entry_date
    base["exit_execution_date"] = exit_date

    if candidate.execution_feasibility != "executable":
        payload, reason = _selected(
            base,
            "unavailable",
            "unavailable",
            "none",
            candidate.rejection_reason or candidate.execution_feasibility,
            "execution_constraint",
        )
        return evaluation(payload, reason)

    observed = _observed_round_trip(
        db,
        owner_id,
        strategy_version_id,
        candidate.instrument_id,
        entry_date,
        exit_date,
        explicit_functional_test_ids,
    )
    excluded = candidate.rejection_reason if candidate.rejection_reason in FEATURE_EXCLUSIONS else None

    replay = _replay_prices(entry_bar, exit_bar)
    replay_notes: Dict[str, Any] = {
        "matching": "counterfactual_replay",
        "observed_entry_without_exit": bool(observed),
    }
    if observed:
        replay_notes["observed_entry_order_id"] = str(observed["buy"].paper_order_id)
    if replay is None:
        replay_payload, replay_reason = _selected(
            base,
            "unavailable",
            "unavailable",
            "none",
            "invalid_execution_price",
            "invalid_execution_price",
            notes_json=replay_notes,
        )
    else:
        entry_price, exit_price, gross, net = replay
        replay_payload = {
            **base,
            "outcome_status": "counterfactual",
            "label_maturity": "mature",
            "outcome_source": "counterfactual_replay",
            "counterfactual": True,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "label_eligible": excluded is None and math.isfinite(net),
            "exclusion_reason": excluded,
            "notes_json": replay_notes,
        }
        replay_reason = "counterfactual_replay"

    if observed and observed.get("sell") is not None:
        buy = observed["buy"]
        sell = observed["sell"]
        gross, net = _round_trip_returns(buy, sell)
        exact_policy = observed.get("note") is None
        notes = {
            "exit_fill_id": str(sell.id),
            "exit_order_id": str(sell.paper_order_id),
            "matching": (
                "actual_paper_round_trip"
                if exact_policy
                else "actual_paper_round_trip_exit_policy_mismatch"
            ),
            "target_exit_execution_date": exit_date,
            "observed_exit_execution_date": sell.execution_date,
            "declared_exit_policy": EXIT_POLICY,
            "declared_hold_bars": HOLD_BARS,
        }
        if not exact_policy:
            notes["holding_policy_version"] = EXECUTION_POLICY_VERSION
        observed_exclusion = excluded
        if not exact_policy:
            observed_exclusion = "exit_policy_mismatch"
        elif observed_exclusion is None and net is None:
            observed_exclusion = "invalid_execution_price"
        observed_payload = {
            **base,
            "outcome_status": "observed",
            "label_maturity": "mature",
            "outcome_source": "paper_fill",
            "counterfactual": False,
            "observed_order_id": buy.paper_order_id,
            "observed_fill_id": buy.id,
            "entry_price": buy.execution_price_before_costs,
            "exit_price": sell.execution_price_before_costs,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "label_eligible": exact_policy and observed_exclusion is None,
            "exclusion_reason": observed_exclusion,
            "entry_execution_date": entry_date,
            "exit_execution_date": sell.execution_date,
            "notes_json": notes,
        }
        if exact_policy:
            return evaluation(
                observed_payload,
                "observed_round_trip",
                additional=[(replay_payload, "counterfactual_not_selected")],
            )
        # The actual exit used a different policy. Keep the declared target
        # selected and retain the actual result as unselected evidence.
        return evaluation(
            replay_payload,
            replay_reason,
            additional=[(observed_payload, "exit_policy_mismatch")],
        )

    if observed:
        replay_notes = {
            **replay_notes,
            "observed_entry_without_exit": True,
            "observed_entry_order_id": str(observed["buy"].paper_order_id),
        }
        replay_payload["notes_json"] = replay_notes
    return evaluation(replay_payload, replay_reason)


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _candidate_rows(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    instrument_id: Optional[str] = None,
) -> List[Tuple[AgentCandidate, Optional[AgentCandidateOutcome]]]:
    query = (
        db.query(AgentCandidate, AgentCandidateOutcome)
        .outerjoin(
            AgentCandidateOutcome,
            and_(
                AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                AgentCandidateOutcome.is_current.is_(True),
            ),
        )
        .filter(
            AgentCandidate.owner_id == owner_id,
            AgentCandidate.strategy_version_id == strategy_version_id,
        )
    )
    if instrument_id:
        query = query.filter(AgentCandidate.instrument_id == instrument_id)
    return query.order_by(AgentCandidate.signal_date, AgentCandidate.id).all()


def list_candidates(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
) -> Dict[str, Any]:
    rows = _candidate_rows(db, owner_id, strategy_version_id, instrument_id)
    return {
        "strategy_version_id": str(strategy_version_id),
        "candidate_count": len(rows),
        "candidates": [
            {
                **candidate_payload(candidate),
                "outcome": outcome_payload(outcome) if outcome is not None else None,
            }
            for candidate, outcome in rows
        ],
    }


def candidate_outcome_history(
    db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID
) -> Dict[str, Any]:
    """Return every outcome version for one candidate, newest last."""
    candidate = (
        db.query(AgentCandidate)
        .filter(
            AgentCandidate.id == candidate_id,
            AgentCandidate.owner_id == owner_id,
        )
        .first()
    )
    if candidate is None:
        raise AgentStrategyRefused(
            "candidate_not_found", "找不到此帳號擁有的候選訊號。"
        )
    versions = (
        db.query(AgentCandidateOutcome)
        .filter(AgentCandidateOutcome.candidate_id == candidate.id)
        .order_by(AgentCandidateOutcome.version_seq)
        .all()
    )
    current = next((row for row in versions if row.is_current), None)
    return {
        "candidate_id": str(candidate.id),
        "strategy_version_id": str(candidate.strategy_version_id),
        "instrument_id": candidate.instrument_id,
        "signal_date": candidate.signal_date,
        "version_count": len(versions),
        "current_outcome_id": str(current.id) if current else None,
        "versions": [outcome_payload(row) for row in versions],
    }


def evaluate_eligibility(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
) -> Dict[str, Any]:
    rows = _candidate_rows(db, owner_id, strategy_version_id, instrument_id)
    exclusions: Counter = Counter()
    outcome_distribution: Counter = Counter()
    eligible_rows: List[Tuple[AgentCandidate, AgentCandidateOutcome]] = []

    for candidate, outcome in rows:
        if outcome is None:
            exclusions["no_outcome"] += 1
            continue
        outcome_distribution[outcome.outcome_status] += 1
        if outcome.label_eligible:
            eligible_rows.append((candidate, outcome))
            continue
        exclusions[outcome.exclusion_reason or "not_eligible"] += 1

    distinct_dates = {candidate.signal_date for candidate, _ in rows}
    mature_dates = {candidate.signal_date for candidate, _ in eligible_rows}
    consent = consent_status(db, owner_id)
    authorization = training_authorization(db, owner_id)

    reasons: List[Dict[str, Any]] = []
    if not rows:
        reasons.append(
            {
                "code": "NO_CANDIDATES",
                "message_zh": "尚未收集此策略版本的候選訊號。",
                "measured": 0,
                "requirement": ">= 1 candidate",
            }
        )
    if not eligible_rows:
        reasons.append(
            {
                "code": "NO_MATURE_OUTCOMES",
                "message_zh": "沒有成熟且可用的候選結果。",
                "measured": 0,
                "requirement": ">= 1 mature outcome",
            }
        )
    if consent["status"] != "active":
        reasons.append(
            {
                "code": "AGENT_STRATEGY_CONSENT_NOT_CONFIGURED",
                "message_zh": "未設定代理策略研究用途同意；任何資料集建立都會被拒絕。",
                "measured": consent["status"],
                "requirement": "active agent-strategy consent",
            }
        )
    elif not authorization["granted"]:
        reasons.append(
            {
                "code": "AGENT_STRATEGY_TRAINING_AUTHORIZATION_NOT_CONFIGURED",
                "message_zh": (
                    "尚未設定訓練授權；本輪不會訓練或推論。開發測試可用 fixture，"
                    "本機 owner 試用則需 AGENT_STRATEGY_OWNER_CONSENT_MODE。"
                ),
                "measured": {
                    "fixture_granted": authorization["fixture_granted"],
                    "owner_consent_mode": authorization["owner_consent_mode"],
                },
                "requirement": (
                    "AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION or "
                    "AGENT_STRATEGY_OWNER_CONSENT_MODE with active owner consent"
                ),
            }
        )

    screening_ready = (
        len(eligible_rows) >= MIN_SCREENING_CANDIDATES
        and len(mature_dates) >= MIN_SPLIT_GROUPS
    )
    return {
        "strategy_version_id": str(strategy_version_id),
        "candidate_count": len(rows),
        "distinct_signal_dates": len(distinct_dates),
        "eligible_count": len(eligible_rows),
        "distinct_eligible_dates": len(mature_dates),
        "outcome_distribution": dict(outcome_distribution),
        "exclusion_breakdown": dict(exclusions),
        "exclusion_labels_zh": {
            key: EXCLUSION_LABELS_ZH.get(key, key) for key in exclusions
        },
        "reasons": reasons,
        "consent": consent,
        "screening_ready": screening_ready,
        "training_ready": bool(screening_ready and authorization["granted"]),
        "training_blocked_by": authorization["reason_code"],
        "training_authorization": authorization,
        "not_human_quality_evaluation": True,
    }


# ---------------------------------------------------------------------------
# Chronological split with gap and purge
# ---------------------------------------------------------------------------


def build_split_manifest(
    rows: List[Dict[str, Any]],
    *,
    policy_version: str = SPLIT_POLICY_VERSION,
) -> Dict[str, Any]:
    """Chronological, date-grouped split with a gap and label-window purge.

    ``rows`` must be sorted-agnostic dicts with ``candidate_id``,
    ``decision_date`` (signal date) and ``label_end_date``.
    """
    eligible = [row for row in rows if row.get("label_eligible")]
    # Group by parsed dates so every comparison below is date-vs-date; the
    # input rows arrive as ISO strings.
    dates = sorted(
        {
            _parse_date(row["decision_date"])
            for row in eligible
            if _parse_date(row["decision_date"]) is not None
        }
    )
    manifest: Dict[str, Any] = {
        "policy_version": policy_version,
        "no_shuffle": True,
        "grouping": "decision_date",
        "gap_days": GAP_DAYS,
        "purge_rule": "label_window_crosses_boundary",
        "train_fraction": TRAIN_FRACTION,
        "validation_fraction": VALIDATION_FRACTION,
        "total_eligible": len(eligible),
        "splits": {
            "train": {"candidate_ids": [], "date_start": None, "date_end": None},
            "validation": {"candidate_ids": [], "date_start": None, "date_end": None},
            "test": {"candidate_ids": [], "date_start": None, "date_end": None},
        },
        "purged": [],
        "refusal_reason": None,
    }
    if len(dates) < MIN_SPLIT_GROUPS:
        manifest["refusal_reason"] = "insufficient_time_groups"
        manifest["measured_groups"] = len(dates)
        manifest["required_groups"] = MIN_SPLIT_GROUPS
        return manifest

    n = len(dates)
    n_train = max(1, int(n * TRAIN_FRACTION))
    n_val = max(1, int(n * VALIDATION_FRACTION))
    if n_train + n_val >= n:
        n_train = max(1, n - 2)
        n_val = 1
    boundary_train = dates[n_train]
    boundary_val = dates[n_train + n_val]
    boundaries = [boundary_train, boundary_val]

    def provisional_split(decision_day: date) -> str:
        if decision_day < boundary_train:
            return "train"
        if decision_day < boundary_val:
            return "validation"
        return "test"

    assigned: Dict[str, List[str]] = {"train": [], "validation": [], "test": []}
    for row in eligible:
        decision_day = _parse_date(row["decision_date"])
        label_end = _parse_date(row.get("label_end_date")) or decision_day
        if decision_day is None:
            manifest["purged"].append(
                {"candidate_id": row["candidate_id"], "reason": "invalid_context"}
            )
            continue
        purge_reason = None
        for boundary in boundaries:
            if decision_day < boundary <= label_end:
                purge_reason = "label_window_crosses_boundary"
                break
            if decision_day < boundary and (boundary - decision_day).days <= GAP_DAYS:
                purge_reason = "gap_before_boundary"
                break
        if purge_reason:
            manifest["purged"].append(
                {"candidate_id": row["candidate_id"], "reason": purge_reason}
            )
            continue
        assigned[provisional_split(decision_day)].append(row["candidate_id"])

    for split_name, ids in assigned.items():
        manifest["splits"][split_name]["candidate_ids"] = ids
        split_dates = sorted(
            {
                row["decision_date"]
                for row in eligible
                if row["candidate_id"] in set(ids)
            }
        )
        if split_dates:
            manifest["splits"][split_name]["date_start"] = split_dates[0]
            manifest["splits"][split_name]["date_end"] = split_dates[-1]
        manifest["splits"][split_name]["count"] = len(ids)

    if any(len(ids) == 0 for ids in assigned.values()):
        manifest["refusal_reason"] = "insufficient_post_purge_samples"
    manifest["boundaries"] = [boundary.isoformat() for boundary in boundaries]
    manifest["total_purged"] = len(manifest["purged"])
    manifest["total_assigned"] = sum(len(ids) for ids in assigned.values())
    return manifest


# ---------------------------------------------------------------------------
# Snapshot preview and fail-closed creation
# ---------------------------------------------------------------------------


def _label_definition() -> Dict[str, Any]:
    return {
        "task_type": PURPOSE,
        "label_definition_version": LABEL_DEFINITION_VERSION,
        "label_source": "cost_adjusted_outcome",
        "label_kind": "net_return_pct",
        "observed_source": "actual_paper_round_trip",
        "unobserved_source": "counterfactual_replay",
        "counterfactual_explicit": True,
        "outcome_version_policy": "one_selected_version_per_candidate",
        "history_retained": True,
        "observed_overrides_target_only_when_exit_policy_matches": True,
        "not_a_prediction_of": [
            "owner 個人決策",
            "市場升跌方向",
            "投資建議",
            "保證利潤",
        ],
        "definition_zh": (
            "以候選訊號時點之前可見的市場特徵，估計在固定執行／出場／成本假設下的"
            "淨結果，協助 take／skip 篩選；未實際成交者標示為 counterfactual。"
        ),
    }


def _canonical_checksum(rows: List[dict], label_definition: dict) -> str:
    payload = json.dumps(
        {"rows": rows, "label_definition": label_definition},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dataset_checksum_input(
    *,
    strategy_version_id: str,
    purpose: str,
    rows: List[dict],
    feature_names: List[str],
    label_definition: dict,
    split_manifest: dict,
    included_count: int,
    excluded_count: int,
    date_range_start: Optional[str],
    date_range_end: Optional[str],
    data_cutoff_at: Optional[str],
) -> Dict[str, Any]:
    """Everything a frozen dataset consists of, including its versions.

    All inputs are stored on the snapshot, so the checksum can be recomputed
    from the record alone without re-reading a current outcome.
    """
    return {
        "purpose": purpose,
        "strategy_version_id": strategy_version_id,
        "versions": {
            "feature_schema": sorted(
                {row["feature_schema_version"] for row in rows}
            ),
            "label_definition": sorted(
                {row["label_definition_version"] for row in rows}
            ),
            "execution_policy": sorted(
                {row["execution_policy_version"] for row in rows}
            ),
            "cost_model": sorted(
                {row["outcome_cost_model_version"] for row in rows}
            ),
            "split_policy": split_manifest.get("policy_version"),
        },
        "feature_names": feature_names,
        "rows": rows,
        "label_definition": label_definition,
        "split_manifest": split_manifest,
        "included_count": included_count,
        "excluded_count": excluded_count,
        "date_range_start": date_range_start,
        "date_range_end": date_range_end,
        "data_cutoff_at": data_cutoff_at,
    }


def _checksum_of(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ready_checksum(preview: Dict[str, Any]) -> str:
    return _checksum_of(
        _dataset_checksum_input(
            strategy_version_id=preview["strategy_version_id"],
            purpose=preview["purpose"],
            rows=preview["rows"],
            feature_names=preview["feature_names"],
            label_definition=preview["label_definition"],
            split_manifest=preview["split_manifest"],
            included_count=preview["included_count"],
            excluded_count=preview["excluded_count"],
            date_range_start=preview["date_range_start"],
            date_range_end=preview["date_range_end"],
            data_cutoff_at=preview["data_cutoff_at"],
        )
    )


def _stored_checksum(snapshot: AgentDatasetSnapshot) -> str:
    return _checksum_of(
        _dataset_checksum_input(
            strategy_version_id=str(snapshot.strategy_version_id),
            purpose=snapshot.purpose,
            rows=snapshot.rows_json or [],
            feature_names=snapshot.feature_names_json or [],
            label_definition=snapshot.label_definition_json or {},
            split_manifest=snapshot.split_manifest_json or {},
            included_count=snapshot.included_count,
            excluded_count=snapshot.excluded_count,
            date_range_start=snapshot.date_range_start,
            date_range_end=snapshot.date_range_end,
            data_cutoff_at=snapshot.data_cutoff_at,
        )
    )


def preview_snapshot(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
) -> Dict[str, Any]:
    rows = _candidate_rows(db, owner_id, strategy_version_id, instrument_id)
    eligibility = evaluate_eligibility(
        db, owner_id, strategy_version_id, instrument_id=instrument_id
    )
    feature_names: Optional[List[str]] = None
    dataset_rows: List[Dict[str, Any]] = []
    split_input: List[Dict[str, Any]] = []
    exclusions: Counter = Counter()

    for candidate, outcome in rows:
        if outcome is None:
            exclusions["no_outcome"] += 1
            continue
        if not outcome.label_eligible:
            exclusions[outcome.exclusion_reason or "not_eligible"] += 1
            continue
        names = list(candidate.feature_names_json or [])
        if feature_names is None:
            feature_names = names
        elif names != feature_names:
            exclusions["feature_schema_mismatch"] += 1
            continue
        row = {
            "candidate_id": str(candidate.id),
            "strategy_version_id": str(strategy_version_id),
            "instrument_id": candidate.instrument_id,
            "signal_date": candidate.signal_date,
            "decision_date": candidate.signal_date,
            "decision_instant": candidate.decision_instant.isoformat(),
            "decision_instant_source": candidate.decision_instant_source,
            "data_cutoff_at": candidate.data_cutoff_at,
            "feature_bar_date": candidate.feature_bar_date,
            "feature_schema_version": candidate.feature_schema_version,
            "label_definition_version": LABEL_DEFINITION_VERSION,
            "features": candidate.features_json,
            "execution_policy_version": candidate.execution_policy_version,
            # A snapshot references the exact outcome version it consumed, so
            # a later selected result cannot silently change its meaning.
            "outcome_id": str(outcome.id),
            "outcome_version": outcome.version_seq,
            "outcome_selection_reason": outcome.selection_reason,
            "outcome_cost_model_version": outcome.cost_model_version,
            "outcome_status": outcome.outcome_status,
            "outcome_source": outcome.outcome_source,
            "counterfactual": outcome.counterfactual,
            "label_maturity": outcome.label_maturity,
            "entry_execution_date": outcome.entry_execution_date,
            "exit_execution_date": outcome.exit_execution_date,
            "net_return_pct": outcome.net_return_pct,
            "gross_return_pct": outcome.gross_return_pct,
            "label_end_date": outcome.exit_execution_date or candidate.signal_date,
            "label_eligible": True,
        }
        dataset_rows.append(row)
        split_input.append(row)

    feature_names = feature_names or []
    manifest = build_split_manifest(split_input)
    label_definition = _label_definition()
    checksum = _canonical_checksum(dataset_rows, label_definition)
    outcome_distribution = Counter(row["outcome_status"] for row in dataset_rows)
    split_sizes = {
        name: len(manifest["splits"][name]["candidate_ids"])
        for name in ("train", "validation", "test")
    }
    screening_ready = (
        eligibility["screening_ready"]
        and manifest["refusal_reason"] is None
        and all(size > 0 for size in split_sizes.values())
    )
    signal_dates = sorted(row["signal_date"] for row in dataset_rows)
    cutoffs = sorted(
        row["data_cutoff_at"]
        for row in dataset_rows
        if row.get("data_cutoff_at")
    )
    authorization = training_authorization(db, owner_id)

    return {
        "strategy_version_id": str(strategy_version_id),
        "purpose": PURPOSE,
        "consent": consent_status(db, owner_id),
        "status": "preview",
        "screening_ready": screening_ready,
        # A preview never authorizes training by itself: both the fixture and
        # the owner's own consent have to be present, and the snapshot still
        # has to be created and its checksum verified.
        "training_ready": bool(screening_ready and authorization["granted"]),
        "training_blocked_by": authorization["reason_code"],
        "training_authorization": authorization,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": feature_names,
        "rows": dataset_rows,
        "included_count": len(dataset_rows),
        "excluded_count": sum(exclusions.values()),
        "date_range_start": signal_dates[0] if signal_dates else None,
        "date_range_end": signal_dates[-1] if signal_dates else None,
        "data_cutoff_at": cutoffs[-1] if cutoffs else None,
        "exclusion_breakdown": dict(exclusions),
        "exclusion_labels_zh": {
            key: EXCLUSION_LABELS_ZH.get(key, key) for key in exclusions
        },
        "outcome_distribution": dict(outcome_distribution),
        "split_manifest": manifest,
        "label_definition": label_definition,
        "checksum": checksum,
        "eligibility": eligibility,
    }


def create_snapshot(
    db: Session,
    owner_id: uuid.UUID,
    strategy_version_id: uuid.UUID,
    *,
    instrument_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Freeze one reproducible dataset, or record an explicit refusal.

    Success requires four separate conditions: the owner's locked version
    exists, this process is allowed to write (development + screening), some
    authorization basis is present, and the data passes the eligibility and
    chronological-split checks. The basis may be the development fixture or, in
    a local owner trial, the owner's own recorded consent; the two are never
    merged. Without any basis the historical refusal record is kept; a data
    refusal writes nothing.

    The returned snapshot is immutable: rows carry their exact outcome id and
    version, and the checksum covers rows, versions and split manifest. A
    repeated request with identical content returns the same snapshot.
    """
    _get_owned_locked_version(db, owner_id, strategy_version_id)
    # Serialize concurrent creates for the same version so "check then insert"
    # cannot produce two identical ready rows. SQLite ignores FOR UPDATE.
    (
        db.query(ExperimentVersion)
        .filter(ExperimentVersion.id == strategy_version_id)
        .with_for_update()
        .first()
    )
    preview = preview_snapshot(
        db, owner_id, strategy_version_id, instrument_id=instrument_id
    )
    consent = preview["consent"]
    authorization = snapshot_authorization(
        owner_consent_active=consent["status"] == "active"
    )

    if not authorization["granted"]:
        if consent["status"] == "active":
            # The owner has consented, but this process has no basis to act on
            # it: either the trial switch is off or the environment refuses it.
            # Consent is not a permission by itself.
            raise AgentStrategyRefused(
                "agent_strategy_snapshot_not_approved",
                "已記錄 owner 同意，但此程序未啟用本機 owner 試用授權，"
                "所以不會建立資料快照；測試 fixture 與 owner 同意不會互相代替。",
                {
                    "authorization_reason": authorization["reason_code"],
                    "owner_consent_mode": authorization["owner_consent_mode"],
                },
            )
        refusal_payload = {
            "strategy_version_id": str(strategy_version_id),
            "purpose": PURPOSE,
            "reason": "agent_strategy_consent_not_configured",
            "checksum_of_preview": preview["checksum"],
        }
        snapshot = AgentDatasetSnapshot(
            owner_id=owner_id,
            strategy_version_id=strategy_version_id,
            purpose=PURPOSE,
            consent_policy_version=CONSENT_VERSION,
            consent_status=consent["status"],
            status="refused",
            candidate_ids_json=[],
            rows_json=[],
            feature_names_json=preview["feature_names"],
            label_definition_json=preview["label_definition"],
            exclusion_breakdown_json=preview["exclusion_breakdown"],
            outcome_distribution_json=preview["outcome_distribution"],
            split_manifest_json=preview["split_manifest"],
            included_count=0,
            excluded_count=preview["excluded_count"],
            date_range_start=None,
            date_range_end=None,
            data_cutoff_at=None,
            checksum=_canonical_checksum([], refusal_payload),
            failure_reason="agent_strategy_consent_not_configured",
        )
        try:
            db.add(snapshot)
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(snapshot)
        raise AgentStrategyRefused(
            "agent_strategy_consent_not_configured",
            "未設定代理策略研究用途同意，已記錄拒絕，沒有建立訓練資料集。",
            {
                "snapshot_id": str(snapshot.id),
                "status": "refused",
                "stored_rows": 0,
                "preview_checksum": preview["checksum"],
            },
        )

    data_reasons = _snapshot_data_blockers(preview)
    if data_reasons:
        db.rollback()
        raise AgentStrategyRefused(
            "snapshot_data_not_eligible",
            "資料資格或時間切分未通過，沒有建立任何 ready 快照。",
            {
                "reasons": data_reasons,
                "included_count": preview["included_count"],
                "excluded_count": preview["excluded_count"],
                "screening_ready": preview["screening_ready"],
                "split_refusal_reason": preview["split_manifest"].get(
                    "refusal_reason"
                ),
            },
        )

    checksum = _ready_checksum(preview)
    existing = (
        db.query(AgentDatasetSnapshot)
        .filter(
            AgentDatasetSnapshot.owner_id == owner_id,
            AgentDatasetSnapshot.strategy_version_id == strategy_version_id,
            AgentDatasetSnapshot.purpose == PURPOSE,
            AgentDatasetSnapshot.status == "ready",
            AgentDatasetSnapshot.checksum == checksum,
        )
        .first()
    )
    if existing is not None:
        db.rollback()
        payload = snapshot_payload(existing)
        payload["created"] = False
        payload["deduplicated"] = True
        return payload

    owner_trial_basis = authorization["mode"] == "owner_consent_local_trial"
    snapshot = AgentDatasetSnapshot(
        owner_id=owner_id,
        strategy_version_id=strategy_version_id,
        purpose=PURPOSE,
        # Record the basis that actually authorized this write. Under the
        # fixture that is the fixture token plus its mode label; under a local
        # owner trial it is the consent version the owner accepted and their
        # live status. Neither value ever means "training is approved".
        consent_policy_version=(
            CONSENT_VERSION
            if owner_trial_basis
            else AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION
        ),
        consent_status="active" if owner_trial_basis else SNAPSHOT_AUTHORIZATION_MODE,
        status="ready",
        candidate_ids_json=[row["candidate_id"] for row in preview["rows"]],
        rows_json=preview["rows"],
        feature_names_json=preview["feature_names"],
        label_definition_json=preview["label_definition"],
        exclusion_breakdown_json=preview["exclusion_breakdown"],
        outcome_distribution_json=preview["outcome_distribution"],
        split_manifest_json=preview["split_manifest"],
        included_count=preview["included_count"],
        excluded_count=preview["excluded_count"],
        date_range_start=preview["date_range_start"],
        date_range_end=preview["date_range_end"],
        data_cutoff_at=preview["data_cutoff_at"],
        checksum=checksum,
        failure_reason=None,
    )
    try:
        db.add(snapshot)
        # One commit: the row, its rows/candidate ids and its manifest land
        # together or not at all.
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(snapshot)
    payload = snapshot_payload(snapshot)
    payload["created"] = True
    payload["deduplicated"] = False
    return payload


def _snapshot_data_blockers(preview: Dict[str, Any]) -> List[Dict[str, Any]]:
    blockers: List[Dict[str, Any]] = []
    manifest = preview["split_manifest"]
    if not preview["rows"]:
        blockers.append(
            {"code": "NO_ELIGIBLE_ROWS", "message_zh": "沒有可凍結的合格資料列。"}
        )
    if not preview["screening_ready"]:
        blockers.append(
            {
                "code": "SCREENING_NOT_READY",
                "message_zh": "資料資格未達最低門檻。",
                "reasons": preview["eligibility"].get("reasons", []),
            }
        )
    if manifest.get("refusal_reason"):
        blockers.append(
            {
                "code": "SPLIT_REFUSED",
                "message_zh": "時間切分被拒絕。",
                "reason": manifest["refusal_reason"],
            }
        )
    for name in ("train", "validation", "test"):
        if not manifest["splits"][name]["candidate_ids"]:
            blockers.append(
                {
                    "code": "EMPTY_SPLIT",
                    "message_zh": f"{name} 切分沒有任何樣本。",
                    "split": name,
                }
            )
    return blockers


# ---------------------------------------------------------------------------
# Training / inference contract
# ---------------------------------------------------------------------------


def training_contract() -> Dict[str, Any]:
    return {
        "task_type": PURPOSE,
        "status": "development_contract_only",
        "capability_status": CAPABILITY_STATUS,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "label_definition": _label_definition(),
        "split_policy": {
            "version": SPLIT_POLICY_VERSION,
            "no_shuffle": True,
            "grouping": "decision_date",
            "gap_days": GAP_DAYS,
            "purge": "label_window_crosses_boundary",
            "train_fraction": TRAIN_FRACTION,
            "validation_fraction": VALIDATION_FRACTION,
        },
        "preprocessing_fit_scope": "train_only",
        "baselines": [
            {"key": "take_all", "description_zh": "接受所有入場訊號"},
            {"key": "skip_all", "description_zh": "全部跳過，不作結果標籤"},
            {
                "key": "random_skip_seed_42",
                "description_zh": "固定種子隨機跳過，作為覆蓋率對照",
            },
        ],
        "evaluation_metrics": [
            "cost_adjusted_return",
            "coverage",
            "rejection_count",
            "risk",
            "not_only_accuracy",
        ],
        "lineage_required": [
            "strategy_version_id",
            "candidate_id",
            "feature_snapshot_checksum",
            "execution_policy_version",
            "dataset_snapshot_checksum",
            "model_artifact_checksum",
            "inference_policy_version",
        ],
        # The training chain has more lineage than a single candidate: the
        # whole frozen snapshot, the job's hyperparameters and its consent.
        "lineage_required_training": [
            "snapshot_id",
            "dataset_checksum",
            "split_policy_version",
            "label_definition_version",
            "training_contract_version",
            "hyperparameters_hash",
            "consent_record_id",
            "model_artifact_checksum",
            "artifact_metadata_checksum",
        ],
        # Code path status, kept apart from the three authorization gates below.
        "training_chain": {
            "implementation": "development_fixture_only",
            "real_data": "NOT_APPROVED",
            "customer_release": "NOT_APPROVED",
            "activation": "NOT_APPROVED",
            "runs_inference_for_customer": False,
        },
        "real_training": "NOT_APPROVED",
        "model_activation": "NOT_APPROVED",
        "inference": "NOT_APPROVED",
        "consent": consent_policy(),
    }


def validate_lineage(lineage: Dict[str, Any]) -> Dict[str, Any]:
    required = training_contract()["lineage_required"]
    missing = [name for name in required if not lineage.get(name)]
    return {
        "valid": not missing,
        "missing": missing,
        "training_performed": False,
        "model_activated": False,
        "inference_performed": False,
    }


# ---------------------------------------------------------------------------
# Existing paper trades — read-only traceability
# ---------------------------------------------------------------------------


def existing_trade_eligibility(
    db: Session,
    owner_id: uuid.UUID,
    *,
    portfolio_id: Optional[uuid.UUID] = None,
    explicit_functional_test_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Classify existing paper orders without creating or changing any row."""
    query = (
        db.query(PaperOrder, DecisionRecord)
        .join(PaperPortfolio, PaperOrder.portfolio_id == PaperPortfolio.id)
        .outerjoin(DecisionRecord, PaperOrder.decision_id == DecisionRecord.id)
        .filter(PaperPortfolio.owner_id == owner_id)
    )
    if portfolio_id is not None:
        query = query.filter(PaperOrder.portfolio_id == portfolio_id)
    rows = query.order_by(PaperOrder.created_at, PaperOrder.id).all()

    report: List[Dict[str, Any]] = []
    counts: Counter = Counter()
    for order, decision in rows:
        linked_version = None
        if decision is not None:
            linked_version = decision.linked_experiment_version_id
        if linked_version is None:
            linked_version = order.model_or_strategy_version_id
        fill_ids = [str(fill.id) for fill in order.fills]

        # A declared functional-test trade is excluded whatever its lineage is,
        # so classify it before touching the candidate tables. That keeps the
        # report usable on an environment where the agent-strategy migration
        # has not been applied, and never lets a test trade look learnable.
        functional = is_functional_test_order(order, explicit_functional_test_ids)
        lineage_status = "not_linked"
        candidate_id = None
        outcome_id = None
        linkage_evidence = None
        if functional:
            lineage_status = "not_checked_functional_test"
            exclusion = "functional_test_trade"
        else:
            date_only_match = None
            if linked_version is not None:
                candidate_query = (
                    db.query(AgentCandidate, AgentCandidateOutcome)
                    .outerjoin(
                        AgentCandidateOutcome,
                        and_(
                            AgentCandidateOutcome.candidate_id == AgentCandidate.id,
                            AgentCandidateOutcome.is_current.is_(True),
                        ),
                    )
                    .filter(
                        AgentCandidate.owner_id == owner_id,
                        AgentCandidate.strategy_version_id == linked_version,
                        AgentCandidate.instrument_id == order.asset_id,
                    )
                )
                fill_id_set = {fill.id for fill in order.fills}
                for candidate, outcome in candidate_query.all():
                    if outcome is None:
                        continue
                    if outcome.observed_order_id == order.id:
                        lineage_status = "candidate_linked"
                        candidate_id = str(candidate.id)
                        outcome_id = str(outcome.id)
                        linkage_evidence = "outcome_observed_order_id"
                        break
                    if (
                        outcome.observed_fill_id is not None
                        and outcome.observed_fill_id in fill_id_set
                    ):
                        lineage_status = "candidate_linked"
                        candidate_id = str(candidate.id)
                        outcome_id = str(outcome.id)
                        linkage_evidence = "outcome_observed_fill_id"
                        break
                    if outcome.entry_execution_date == order.data_cutoff_at:
                        date_only_match = (candidate, outcome)
                if lineage_status != "candidate_linked" and date_only_match is not None:
                    # A shared date is traceability, not proof of identity:
                    # without an order/fill id the linkage stays unconfirmed.
                    candidate_id = str(date_only_match[0].id)
                    lineage_status = "date_only_unconfirmed"

            if linked_version is None:
                exclusion = "no_strategy_lineage"
            elif lineage_status == "date_only_unconfirmed":
                exclusion = "unconfirmed_candidate_linkage"
            elif lineage_status != "candidate_linked":
                exclusion = "no_candidate_lineage"
            elif order.actor_type != "human":
                exclusion = "non_human_execution"
            else:
                # Even a lineage-confirmed human trade has no confirmed research
                # purpose, outcome policy or authorization scope this round.
                exclusion = "agent_strategy_consent_not_configured"

        counts[exclusion] += 1
        report.append(
            {
                "order_id": str(order.id),
                "portfolio_id": str(order.portfolio_id),
                "asset_id": order.asset_id,
                "side": order.side.value if hasattr(order.side, "value") else str(order.side),
                "quantity": order.quantity,
                "status": order.status.value if hasattr(order.status, "value") else str(order.status),
                "order_actor_type": order.actor_type,
                "decision_id": str(order.decision_id) if order.decision_id else None,
                "decision_actor_type": (
                    decision.actor_type.value
                    if decision is not None and hasattr(decision.actor_type, "value")
                    else (str(decision.actor_type) if decision is not None else None)
                ),
                "linked_experiment_version_id": str(linked_version) if linked_version else None,
                "candidate_id": candidate_id,
                "outcome_id": outcome_id,
                "lineage_status": lineage_status,
                "linkage_evidence": linkage_evidence,
                "linkage_confirmation": {
                    "candidate_linkage": lineage_status,
                    "data_source_confirmed": False,
                    "research_purpose_confirmed": False,
                    "outcome_policy_confirmed": False,
                    "authorization_scope_confirmed": False,
                },
                "fill_ids": fill_ids,
                "data_cutoff_at": order.data_cutoff_at,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "functional_test": functional,
                "eligible_for_c_dataset": False,
                "exclusion_reason": exclusion,
                "exclusion_label_zh": EXCLUSION_LABELS_ZH.get(exclusion, exclusion),
            }
        )

    return {
        "read_only": True,
        "writes_performed": 0,
        "source_tables": ["paper_orders", "paper_fills", "decision_records"],
        "order_count": len(report),
        "exclusion_breakdown": dict(counts),
        "orders": report,
    }


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def candidate_payload(candidate: AgentCandidate) -> Dict[str, Any]:
    return {
        "id": str(candidate.id),
        "strategy_version_id": str(candidate.strategy_version_id),
        "instrument_id": candidate.instrument_id,
        "candidate_kind": candidate.candidate_kind,
        "rule_key": candidate.rule_key,
        "rule_parameters": candidate.rule_parameters_json,
        "logged_action": candidate.logged_action,
        "created_by_actor_type": candidate.created_by_actor_type,
        "signal_date": candidate.signal_date,
        "decision_instant": candidate.decision_instant.isoformat(),
        "decision_instant_source": candidate.decision_instant_source,
        "data_cutoff_at": candidate.data_cutoff_at,
        "feature_schema_version": candidate.feature_schema_version,
        "feature_names": candidate.feature_names_json,
        "features": candidate.features_json,
        "feature_bar_date": candidate.feature_bar_date,
        "execution_policy_version": candidate.execution_policy_version,
        "execution_feasibility": candidate.execution_feasibility,
        "rejection_reason": candidate.rejection_reason,
        "dedupe_key": candidate.dedupe_key,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
    }


def outcome_payload(outcome: AgentCandidateOutcome) -> Dict[str, Any]:
    return {
        "outcome_id": str(outcome.id),
        "candidate_id": str(outcome.candidate_id),
        "version_seq": outcome.version_seq,
        "is_current": outcome.is_current,
        "selection_reason": outcome.selection_reason,
        "superseded_at": (
            outcome.superseded_at.isoformat() if outcome.superseded_at else None
        ),
        "outcome_status": outcome.outcome_status,
        "label_maturity": outcome.label_maturity,
        "label_eligible": outcome.label_eligible,
        "outcome_source": outcome.outcome_source,
        "counterfactual": outcome.counterfactual,
        "observed_order_id": str(outcome.observed_order_id) if outcome.observed_order_id else None,
        "observed_fill_id": str(outcome.observed_fill_id) if outcome.observed_fill_id else None,
        "entry_execution_date": outcome.entry_execution_date,
        "exit_execution_date": outcome.exit_execution_date,
        "entry_price": outcome.entry_price,
        "exit_price": outcome.exit_price,
        "gross_return_pct": outcome.gross_return_pct,
        "net_return_pct": outcome.net_return_pct,
        "cost_model_version": outcome.cost_model_version,
        "cost_model": outcome.cost_model_json,
        "exit_policy": outcome.exit_policy,
        "exclusion_reason": outcome.exclusion_reason,
        "notes": outcome.notes_json,
        "evaluated_at": outcome.evaluated_at.isoformat() if outcome.evaluated_at else None,
    }


def snapshot_payload(snapshot: AgentDatasetSnapshot) -> Dict[str, Any]:
    if snapshot.status == "ready":
        recomputed = _stored_checksum(snapshot)
        integrity = {
            "checksum_verified": recomputed == snapshot.checksum,
            "recomputed_checksum": recomputed,
        }
    else:
        integrity = {
            "checksum_verified": None,
            "reason": "refused_record_has_no_dataset_payload",
        }
    # ``active`` is the row's own record that the owner's consent authorized
    # this snapshot; the fixture mode label never means owner consent.
    owner_trial_basis = snapshot.consent_status == "active"
    verified_ready = (
        snapshot.status == "ready" and integrity.get("checksum_verified") is True
    )
    return {
        "id": str(snapshot.id),
        "strategy_version_id": str(snapshot.strategy_version_id),
        "purpose": snapshot.purpose,
        "consent_policy_version": snapshot.consent_policy_version,
        "consent_status": snapshot.consent_status,
        "authorization": {
            "basis": snapshot.consent_status,
            "policy_version": snapshot.consent_policy_version,
            "real_owner_consent": owner_trial_basis,
            "movable_to_customer_environment": False,
        },
        "status": snapshot.status,
        "candidate_ids": snapshot.candidate_ids_json,
        "rows": snapshot.rows_json,
        "feature_names": snapshot.feature_names_json,
        "label_definition": snapshot.label_definition_json,
        "exclusion_breakdown": snapshot.exclusion_breakdown_json,
        "outcome_distribution": snapshot.outcome_distribution_json,
        "split_manifest": snapshot.split_manifest_json,
        "included_count": snapshot.included_count,
        "excluded_count": snapshot.excluded_count,
        "date_range_start": snapshot.date_range_start,
        "date_range_end": snapshot.date_range_end,
        "data_cutoff_at": snapshot.data_cutoff_at,
        "checksum": snapshot.checksum,
        "integrity": integrity,
        "failure_reason": snapshot.failure_reason,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        # Two bases, kept apart. A ready fixture snapshot feeds the development
        # chain; an owner-trial snapshot carries the owner's own consent. Neither
        # is a customer training set and neither is usable for real training.
        "usable_for_fixture_training": (
            verified_ready and snapshot.consent_status == SNAPSHOT_AUTHORIZATION_MODE
        ),
        "usable_for_owner_trial_training": verified_ready and owner_trial_basis,
        "usable_for_real_training": False,
    }


def get_snapshot(
    db: Session, owner_id: uuid.UUID, snapshot_id: uuid.UUID
) -> Dict[str, Any]:
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
            "snapshot_not_found", "找不到此帳號擁有的資料快照。"
        )
    return snapshot_payload(snapshot)
