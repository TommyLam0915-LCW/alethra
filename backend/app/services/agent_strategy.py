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

from app.auth.provenance import UNKNOWN
from app.lib.timeutil import utcnow as _utcnow
from app.models.agent_strategy import (
    AgentCandidate,
    AgentCandidateOutcome,
    AgentDatasetSnapshot,
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

PURPOSE = "agent_strategy_screening"
CAPABILITY_STATUS = "DEVELOPMENT"
CAPABILITY_STATUS_LABEL_ZH = "開發中／尚不可在原客戶環境執行"
CONSENT_VERSION = "agent-strategy-learning-v1"

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
        "status": "not_configured",
        "fail_closed": True,
        "reason_code": "AGENT_STRATEGY_CONSENT_NOT_CONFIGURED",
        "data_scope_zh": (
            "此帳號擁有的紙上委託、成交，以及由已鎖定規則策略產生的候選訊號。"
            "不包括其他帳號、客戶 WB 或保留研究窗口的資料。"
        ),
        "retention_zh": "直到 owner 撤銷；資料集快照可刪除，拒絕紀錄保留原因。",
        "revoke_zh": "本輪只提供政策介面，未實作撤銷與刪除動作。",
        "output_limits_zh": (
            "只作候選篩選輔助；不建立自主策略、不模仿 owner、不保證利潤、"
            "不自動實盤、不自動重訓。"
        ),
        "accepted_this_round": False,
        "training_allowed": False,
    }


def consent_status(db: Session, owner_id: uuid.UUID) -> Dict[str, Any]:
    # There is no agent-strategy consent table yet, and no endpoint accepts one.
    # Returning an explicit not-configured status keeps every dataset path
    # fail-closed instead of treating silence as permission.
    return {
        "purpose": PURPOSE,
        "consent_version": CONSENT_VERSION,
        "status": "not_configured",
        "fail_closed": True,
        "reason_code": "AGENT_STRATEGY_CONSENT_NOT_CONFIGURED",
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
            affordable = int(
                INITIAL_NOTIONAL / (execution_bar.open * (1 + COMMISSION_RATE))
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
    reasons.append(
        {
            "code": "AGENT_STRATEGY_CONSENT_NOT_CONFIGURED",
            "message_zh": "未設定代理策略研究用途同意；任何資料集建立都會被拒絕。",
            "measured": consent["status"],
            "requirement": "active agent-strategy consent",
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
        "training_ready": False,
        "training_blocked_by": "agent_strategy_consent_not_configured",
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

    return {
        "strategy_version_id": str(strategy_version_id),
        "purpose": PURPOSE,
        "consent": consent_status(db, owner_id),
        "status": "preview",
        "screening_ready": screening_ready,
        "training_ready": False,
        "training_blocked_by": "agent_strategy_consent_not_configured",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": feature_names,
        "rows": dataset_rows,
        "included_count": len(dataset_rows),
        "excluded_count": sum(exclusions.values()),
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
    """Record a refused snapshot while no research consent exists.

    The row is an audit record, not a training set: it has no rows and cannot
    be consumed. A ready snapshot is only possible once a consent interface is
    approved and implemented.
    """
    preview = preview_snapshot(
        db, owner_id, strategy_version_id, instrument_id=instrument_id
    )
    consent = preview["consent"]
    if consent["status"] != "active":
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
        db.add(snapshot)
        db.commit()
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

    raise AgentStrategyRefused(
        "agent_strategy_snapshot_not_approved",
        "本輪未批准建立可訓練的代理策略資料集。",
    )


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
    return {
        "id": str(snapshot.id),
        "strategy_version_id": str(snapshot.strategy_version_id),
        "purpose": snapshot.purpose,
        "consent_policy_version": snapshot.consent_policy_version,
        "consent_status": snapshot.consent_status,
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
        "checksum": snapshot.checksum,
        "failure_reason": snapshot.failure_reason,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "usable_for_training": False,
    }
