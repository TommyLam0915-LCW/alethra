"""Run one agent model's judgment as a paper trade on one skill account.

This is the door that makes an owner's activation approval *do* something the
owner can read back. Before it, the slice could score a candidate
(``POST /training/jobs/{id}/inference``, which writes nothing) and could record
an approval (``PUT /training/jobs/{id}/activation``), and no row anywhere joined
the two to a trade. An approval that no execution consults cannot be revoked in
any sense a customer can observe, so this module is where the approval becomes
load-bearing.

What may not be claimed here:

* **Paper only.** It writes ``paper_*`` rows for one skill account and reaches no
  broker, no live daemon and no external venue. ``runs_live_trading`` is a
  constant ``False`` in the response because nothing in this path can make it
  true.
* **Nothing a client names is taken as authorization.** The request carries one
  id — the skill account — plus the job in the path. Owner, instrument, signal
  date, features, price, quantity, strategy version, dataset, artifact and the
  approval are each re-read from the row that holds them. A checksum, an
  approval id or a status in the body would be a claim, not a fact.
* **A job-only approval authorizes no skill account.** ``skill_unit_id`` is NULL
  on such a row, and ``approval_covers_skill_unit`` reads NULL as "the artifact
  in isolation", never as "every account". So the scope leg refuses it by name
  even though the same row satisfies the inference gate. An owner who wants
  paper execution approves *for the account* (``skill_unit_id`` in the approval
  request); this module never revokes or rewrites the approval that is already
  there to make that possible.

Door order — every check that can refuse runs before the model is loaded, and
everything before any paper row is written:

1. the recorded safety switches (global / inference / execution),
2. the skill account: owned by this owner, and not paused/retired/completed,
3. the job: owned, completed,
4. the scope leg: the live approval binds *this* skill account,
5. the owner's own consent for this purpose,
6. the strategy version: owned, locked, and the same one the approval names,
7. the candidate: the newest durable signal for that version and its locked
   instrument, with readable features and a stored execution bar,
8. ``run_inference`` — the real loader, which re-verifies dataset, lineage,
   artifact bytes and the activation gate and refuses by name,
9. the shared risk policy and ``book_fill``, the same fill convention the manual
   and rule lanes use,
10. ``_approval_slot_is_live``, immediately before the one ``db.commit()`` this
    lane has: the approval is re-read under the lock the revoke path takes, so a
    withdrawal that won the race is answered by a named refusal instead of a
    trade the owner had already stopped.

A refusal at 1-7, 8 or 10 writes nothing at all; the response carries the named code
and an independent read of the tables shows no new decision, order or fill. A
refusal at 9 — the model ran, and the book declined the trade — is durable in
``agent_paper_executions`` with status ``blocked``, because a score that nobody
recorded is a score someone will later claim existed.
"""

import uuid
from datetime import date, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.provenance import UNKNOWN
from app.engine.fill_math import quantity_precision_for, size_position, slipped_price
from app.ml.agent_strategy_trainer import run_inference
from app.models.agent_paper_execution import (
    BLOCKED,
    EXECUTED,
    SKIPPED,
    AgentPaperExecution,
    execution_payload,
)
from app.models.agent_strategy import (
    AgentCandidate,
    AgentDatasetSnapshot,
    AgentModelActivationApproval,
    AgentStrategyTrainingJob,
)
from app.models.experiment_version import ExperimentVersion
from app.models.kill_switch import KillSwitchScope
from app.models.paper_portfolio import (
    ConfidenceLevel,
    DecisionRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    OutcomeStatus,
    PaperDecisionActorType,
    PaperDecisionType,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
    PositionStatus,
    RiskCheckStatus,
)
from app.services.agent_strategy import (
    COMMISSION_RATE,
    EXECUTION_POLICY_VERSION,
    FEATURE_SCHEMA_VERSION,
    SLIPPAGE_RATE,
    AgentStrategyRefused,
    _get_owned_locked_version,
    _load_daily_bars,
    _rule_spec,
    consent_status,
)
from app.services.kill_switch import is_kill_switch_active
from app.services.model_activation import (
    approval_for_job,
    check_skill_unit_activation,
)
from app.services.paper_execution import PaperExecutionError, book_fill
from app.services.risk_policy import (
    RiskInputs,
    count_entries,
    evaluate_risk,
    value_open_positions,
)

# Named in every ledger row and refusal so this lane can be told apart from the
# direct, session and rule lanes in the same tables.
LANE = "agent_model"
PAPER_EXECUTION_POLICY_VERSION = "agent-model-paper-execution-v1"
# The decision and order rows say a program booked them, which is what happened:
# the model decided and this code executed. The human's part — the approval that
# let it — is a foreign key on the ledger row, not an actor label on the trade.
EXECUTION_ACTOR_TYPE = "system"

# A portfolio in any of these states trades nothing, so the door says so before
# it spends a model load on an account that cannot book the answer.
UNTRADABLE_STATUSES = {
    PortfolioStatus.paused: "paper_execution_skill_unit_paused",
    PortfolioStatus.retired: "paper_execution_skill_unit_retired",
    PortfolioStatus.completed: "paper_execution_skill_unit_completed",
}

# The three switches this path is under. Each gets its own code so a caller
# reading ``execution_blocked_by_kill_switch`` does not have to guess which one
# an operator pulled, and so one switch cannot be reported as another.
KILL_SWITCHES = (
    (KillSwitchScope.global_, "paper_execution_kill_switch_global"),
    (KillSwitchScope.inference, "paper_execution_kill_switch_inference"),
    (KillSwitchScope.execution, "paper_execution_kill_switch_execution"),
)


def _refused(code: str, message_zh: str, details: Optional[dict] = None):
    raise AgentStrategyRefused(code, message_zh, details)


def _owned_portfolio(db: Session, owner_id: UUID, skill_unit_id: UUID) -> PaperPortfolio:
    portfolio = db.scalars(
        select(PaperPortfolio).where(
            PaperPortfolio.id == skill_unit_id,
            PaperPortfolio.owner_id == owner_id,
        )
    ).first()
    if portfolio is None:
        # Another owner's account is reported as absent, the way this API already
        # reports another owner's job and snapshot.
        _refused(
            "paper_execution_skill_unit_not_found",
            "找不到屬於此帳號的技能單位（紙上投資組合）。",
            {"skill_unit_id": str(skill_unit_id)},
        )
    return portfolio


def _owned_job(db: Session, owner_id: UUID, job_id: UUID) -> AgentStrategyTrainingJob:
    job = db.scalars(
        select(AgentStrategyTrainingJob).where(
            AgentStrategyTrainingJob.id == job_id,
            AgentStrategyTrainingJob.owner_id == owner_id,
        )
    ).first()
    if job is None:
        _refused(
            "paper_execution_job_not_found",
            "找不到屬於此帳號的訓練工作。",
            {"job_id": str(job_id)},
        )
    if job.status != "completed":
        _refused(
            "training_job_not_completed",
            "只有完成的訓練工作可以執行紙上決策。",
            {"job_id": str(job.id), "status": job.status},
        )
    if not job.artifact_checksum:
        _refused(
            "artifact_missing",
            "該訓練工作沒有產物雜湊，無東西可執行。",
            {"job_id": str(job.id)},
        )
    return job


def _newest_candidate(
    db: Session,
    owner_id: UUID,
    strategy_version_id: UUID,
    instrument_id: str,
) -> AgentCandidate:
    candidate = db.scalars(
        select(AgentCandidate)
        .where(
            AgentCandidate.owner_id == owner_id,
            AgentCandidate.strategy_version_id == strategy_version_id,
            AgentCandidate.instrument_id == instrument_id,
        )
        .order_by(AgentCandidate.signal_date.desc(), AgentCandidate.created_at.desc())
    ).first()
    if candidate is None:
        _refused(
            "paper_execution_no_candidate",
            "此策略版本與標的目前沒有候選訊號可執行；先收集候選。",
            {"strategy_version_id": str(strategy_version_id), "instrument_id": instrument_id},
        )
    if candidate.feature_schema_version != FEATURE_SCHEMA_VERSION:
        _refused(
            "paper_execution_candidate_schema_mismatch",
            "最近一筆候選的特徵版本與目前模型契約不符，拒絕執行。",
            {
                "candidate_id": str(candidate.id),
                "candidate_feature_schema_version": candidate.feature_schema_version,
                "required_feature_schema_version": FEATURE_SCHEMA_VERSION,
            },
        )
    if candidate.execution_policy_version != EXECUTION_POLICY_VERSION:
        _refused(
            "paper_execution_candidate_policy_mismatch",
            "最近一筆候選的執行政策版本與本道帳本不符，拒絕執行。",
            {
                "candidate_id": str(candidate.id),
                "candidate_execution_policy_version": candidate.execution_policy_version,
                "required_execution_policy_version": EXECUTION_POLICY_VERSION,
            },
        )
    if candidate.execution_feasibility == "execution_failure":
        _refused(
            "paper_execution_candidate_not_executable",
            "最近一筆候選在其執行日沒有有效成交價，拒絕執行。",
            {
                "candidate_id": str(candidate.id),
                "execution_feasibility": candidate.execution_feasibility,
                "rejection_reason": candidate.rejection_reason,
            },
        )
    features = candidate.features_json or {}
    if not features:
        _refused(
            "paper_execution_candidate_features_missing",
            "最近一筆候選沒有可用特徵（收集當時被資料可用性排除），拒絕執行。",
            {"candidate_id": str(candidate.id), "rejection_reason": candidate.rejection_reason},
        )
    return candidate


def _split_membership(snapshot, candidate_id: uuid.UUID) -> str:
    """Which frozen split this candidate was in — in-sample must stay visible."""
    splits = (snapshot.split_manifest_json or {}).get("splits") or {}
    for name in ("train", "validation", "test"):
        if str(candidate_id) in [str(value) for value in (splits.get(name) or {}).get("candidate_ids") or []]:
            return name
    return "not_in_snapshot"


def _execution_bar(candidate: AgentCandidate, db: Session) -> Dict[str, Any]:
    """The next stored daily bar after the signal — the frozen execution policy.

    Read from the dataset the candidate itself was collected against, not the
    demo fixture the manual lane prices from: an agent model's paper trade has
    to cost out on the same bars its own outcomes were measured on.
    """
    signal_day = date.fromisoformat(candidate.signal_date)
    bars = _load_daily_bars(
        db, candidate.instrument_id, start=(signal_day + timedelta(days=1)).isoformat()
    )
    if not bars:
        _refused(
            "paper_execution_no_execution_bar",
            "訊號日之後沒有已儲存的日線，本道不猜價格，拒絕執行。",
            {"instrument_id": candidate.instrument_id, "signal_date": candidate.signal_date},
        )
    bar = bars[0]
    open_price = float(bar.open) if bar.open is not None else 0.0
    if open_price <= 0:
        _refused(
            "paper_execution_invalid_execution_price",
            "執行日開盤價無效，拒絕執行。",
            {"instrument_id": candidate.instrument_id, "execution_date": bar.bar_date.isoformat()},
        )
    return {"date": bar.bar_date.isoformat(), "open": open_price, "provider_id": bar.provider_id}


def list_paper_executions(
    db: Session,
    owner_id: UUID,
    *,
    job_id: Optional[UUID] = None,
    skill_unit_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """The formal read of this lane's ledger, scoped to the calling owner."""
    query = db.query(AgentPaperExecution).filter(
        AgentPaperExecution.owner_id == owner_id
    )
    if job_id is not None:
        query = query.filter(AgentPaperExecution.job_id == job_id)
    if skill_unit_id is not None:
        query = query.filter(AgentPaperExecution.skill_unit_id == skill_unit_id)
    rows = query.order_by(AgentPaperExecution.created_at.desc()).all()
    return {
        "lane": LANE,
        "paper_execution_policy_version": PAPER_EXECUTION_POLICY_VERSION,
        "count": len(rows),
        "executions": [execution_payload(row) for row in rows],
        "runs_live_trading": False,
    }


def execute_paper_decision(
    db: Session,
    owner_id: UUID,
    *,
    job_id: UUID,
    skill_unit_id: UUID,
    actor_type: str = UNKNOWN,
) -> Dict[str, Any]:
    """Consult one approved artifact and book what the book accepts.

    Returns the durable ledger row. Raises ``AgentStrategyRefused`` with a named
    code — and writes nothing — when a precondition is not met.
    """
    for scope, code in KILL_SWITCHES:
        if is_kill_switch_active(db, scope):
            _refused(
                code,
                "安全開關已啟動，本道未載入模型、未寫入任何紙上紀錄。",
                {"scope": scope.value, "job_id": str(job_id), "skill_unit_id": str(skill_unit_id)},
            )

    portfolio = _owned_portfolio(db, owner_id, skill_unit_id)
    if portfolio.status in UNTRADABLE_STATUSES:
        _refused(
            UNTRADABLE_STATUSES[portfolio.status],
            "此技能單位目前狀態不接受新進場，未載入模型、未寫入任何紙上紀錄。",
            {"skill_unit_id": str(portfolio.id), "status": portfolio.status.value},
        )

    job = _owned_job(db, owner_id, job_id)

    # The scope leg, on the live approval row itself: NULL covers no account, and
    # another account's approval covers only that other account.
    scope_check = check_skill_unit_activation(db, owner_id, job.id, portfolio.id)
    if not scope_check["authorized"]:
        _refused(
            "paper_execution_approval_scope_not_granted",
            "生效中的啟用同意未綁定此技能單位（job-only／NULL scope 不覆蓋任何帳號），拒絕執行。",
            {
                "job_id": str(job.id),
                "skill_unit_id": str(portfolio.id),
                "reason_code": scope_check["reason_code"],
                "approval_id": scope_check["approval_id"],
                "approval_skill_unit_id": scope_check.get("approval_skill_unit_id"),
                "requested_skill_unit_id": scope_check.get("requested_skill_unit_id"),
            },
        )
    approval = approval_for_job(db, owner_id, job.id)

    consent = consent_status(db, owner_id)
    if consent["status"] != "active":
        _refused(
            "paper_execution_owner_consent_not_active",
            "此用途沒有有效的本人同意，拒絕執行模型紙上決策。",
            {"consent_status": consent["status"], "reason_code": consent.get("reason_code")},
        )

    if str(approval.strategy_version_id) != str(job.strategy_version_id):
        _refused(
            "paper_execution_version_mismatch",
            "啟用同意所綁定的策略版本與該訓練工作不符，拒絕執行。",
            {
                "approval_strategy_version_id": str(approval.strategy_version_id),
                "job_strategy_version_id": str(job.strategy_version_id),
            },
        )
    version = _get_owned_locked_version(db, owner_id, job.strategy_version_id)
    instrument_id = _rule_spec(version, None)["instrument_id"]

    snapshot_row = db.scalars(
        select(AgentDatasetSnapshot).where(AgentDatasetSnapshot.id == job.snapshot_id)
    ).first()
    if snapshot_row is None:
        _refused(
            "snapshot_not_found",
            "找不到該訓練工作參照的資料集快照。",
            {"snapshot_id": str(job.snapshot_id)},
        )

    candidate = _newest_candidate(db, owner_id, job.strategy_version_id, instrument_id)

    identity = {
        "job_id": str(job.id),
        "skill_unit_id": str(portfolio.id),
        "candidate_id": str(candidate.id),
    }
    existing = db.scalars(
        select(AgentPaperExecution).where(
            AgentPaperExecution.skill_unit_id == portfolio.id,
            AgentPaperExecution.job_id == job.id,
            AgentPaperExecution.candidate_id == candidate.id,
        )
    ).first()
    if existing is not None:
        # The same intent, asked for twice, is the same row. Re-scoring it would
        # be a second judgment on one signal; the response says it is a repeat.
        payload = execution_payload(existing)
        payload["duplicate"] = True
        payload["paper_only"] = True
        return payload

    bar = _execution_bar(candidate, db)

    # The real loader: joblib, the artifact checksum recomputed from bytes, the
    # dataset checksum, the lineage and the activation gate all re-read here.
    inference = run_inference(db, owner_id, job.id, dict(candidate.features_json))
    if inference["activation_approval_id"] != str(approval.id):
        # The scope leg and the loader must have read the same approval. If an
        # owner replaced the approval between the two reads, this execution binds
        # nothing, rather than binding the row that did not authorize it.
        _refused(
            "paper_execution_approval_changed_midflight",
            "模型載入時的生效同意與權限檢查讀到的不是同一筆，拒絕執行。",
            {**identity, "gate_approval_id": str(approval.id),
             "loader_approval_id": inference["activation_approval_id"]},
        )
    if inference["artifact_checksum"] != approval.artifact_checksum:
        _refused(
            "paper_execution_artifact_mismatch",
            "模型載入所用的產物雜湊與同意所綁定的不符，拒絕執行。",
            {**identity, "loaded_artifact_checksum": inference["artifact_checksum"],
             "approved_artifact_checksum": approval.artifact_checksum},
        )

    ledger = AgentPaperExecution(
        owner_id=owner_id,
        skill_unit_id=portfolio.id,
        job_id=job.id,
        snapshot_id=job.snapshot_id,
        strategy_version_id=job.strategy_version_id,
        approval_id=approval.id,
        candidate_id=candidate.id,
        instrument_id=candidate.instrument_id,
        signal_date=candidate.signal_date,
        split_membership=_split_membership(snapshot_row, candidate.id),
        model_decision=inference["decision"],
        positive_probability=inference["positive_probability"],
        threshold=inference["threshold"],
        inference_policy_version=inference["inference_policy_version"],
        training_contract_version=inference["training_contract_version"],
        dataset_checksum=inference["dataset_checksum"],
        artifact_checksum=inference["artifact_checksum"],
        execution_policy_version=EXECUTION_POLICY_VERSION,
        execution_date=bar["date"],
        actor_type=actor_type,
    )

    if inference["decision"] != "take":
        ledger.execution_status = SKIPPED
        return _commit_ledger(db, ledger, identity)

    # The order row is this lane's first write, so two presses of one intent part
    # ways at ``uq_paper_order_portfolio_idempotency`` — before either owns a
    # ledger row — and an IntegrityError raised there used to leave this door
    # whole, answering the customer with an unhandled failure while the trade they
    # were racing had already committed.
    try:
        return _book_entry(db, ledger, portfolio=portfolio, candidate=candidate,
                           bar=bar, version=version, identity=identity)
    except IntegrityError as exc:
        collapsed = _collapse_racing_press(db, identity, exc)
        if collapsed is None:
            raise
        return collapsed


def _book_entry(
    db: Session,
    ledger: AgentPaperExecution,
    *,
    portfolio: PaperPortfolio,
    candidate: AgentCandidate,
    bar: Dict[str, Any],
    version: ExperimentVersion,
    identity: Dict[str, str],
) -> Dict[str, Any]:
    """Size, ask the shared policy, and book through ``book_fill``."""
    observed_price = float(bar["open"])
    # The slipped price is what the fill actually pays, so it is the price the
    # order is sized against: sizing on the observed open can afford a unit the
    # booked fill then cannot pay for, which is the divergence ``fill_math``
    # exists to remove. The shared risk policy is still asked about the
    # decision-time price, because that is what the bar showed.
    execution_price = slipped_price(observed_price, "BUY", SLIPPAGE_RATE)
    # Sized on this account's own cash: ``INITIAL_NOTIONAL`` is the cost model the
    # counterfactual outcomes assume, not a budget any skill account holds.
    quantity = size_position(
        float(portfolio.current_virtual_cash),
        execution_price,
        COMMISSION_RATE,
        quantity_precision_for(ledger.instrument_id),
    )
    ledger.quantity = float(quantity) if quantity else 0.0
    ledger.observed_price = observed_price
    ledger.execution_price = execution_price
    ledger.commission_rate = COMMISSION_RATE
    if not quantity or float(quantity) <= 0:
        ledger.execution_status = BLOCKED
        ledger.blocked_reason = "insufficient_cash"
        return _commit_ledger(
            db,
            ledger,
            identity,
            refuse_code="paper_execution_insufficient_cash",
            refuse_zh="此技能單位的現金買不下最小交易單位，未建立委託或成交。",
        )

    # Serialize the account before the caps are evaluated, exactly as the rule and
    # direct lanes do, so two executions against one account cannot both spend the
    # last slot of the same budget.
    db.query(PaperPortfolio).populate_existing().filter(
        PaperPortfolio.id == portfolio.id
    ).with_for_update().first()

    open_value, unpriced = value_open_positions(
        db,
        portfolio,
        price_by_asset={ledger.instrument_id: observed_price},
        on_or_before=bar["date"],
        provider_id=bar["provider_id"],
    )
    held = sum(
        float(p.quantity or 0)
        for p in portfolio.positions
        if p.asset_id == ledger.instrument_id and p.status == PositionStatus.open
    )
    risk = evaluate_risk(
        RiskInputs(
            lane=LANE,
            side="BUY",
            asset_id=ledger.instrument_id,
            quantity=float(quantity),
            portfolio_status=portfolio.status.value,
            portfolio_cash=float(portfolio.current_virtual_cash),
            held_quantity=held,
            price=observed_price,
            open_positions_value=None if unpriced else open_value,
            unpriced_assets=unpriced,
            entries_on_date=count_entries(db, portfolio, market_date=candidate.signal_date),
            decision_date=candidate.signal_date,
        )
    )
    ledger.risk_check_json = risk.as_dict()
    if not risk.allowed:
        ledger.execution_status = BLOCKED
        ledger.blocked_reason = risk.code
        return _commit_ledger(
            db,
            ledger,
            identity,
            refuse_code="paper_execution_risk_blocked",
            refuse_zh=f"共同風險政策拒絕此進場（{risk.code}），未建立決策、委託或成交。",
        )

    rationale = (
        f"Agent model paper entry: job {ledger.job_id} scored "
        f"{ledger.positive_probability:g} >= {ledger.threshold:g} on the rule signal of "
        f"{candidate.signal_date}. Approval {ledger.approval_id} binds artifact "
        f"{ledger.artifact_checksum}; simulated paper only, not a recommendation."
    )
    idempotency_key = f"agent-model-{ledger.job_id}-{ledger.candidate_id}"

    decision = DecisionRecord(
        owner_id=portfolio.owner_id,
        paper_portfolio_id=portfolio.id,
        actor_type=PaperDecisionActorType.system,
        decision_type=PaperDecisionType.entry,
        data_cutoff_at=candidate.signal_date,
        linked_experiment_version_id=version.id,
        rationale=rationale,
        self_reported_confidence=ConfidenceLevel.not_provided,
        outcome_status=OutcomeStatus.pending,
        risk_flags={
            "lane": LANE,
            "paper_execution_policy_version": PAPER_EXECUTION_POLICY_VERSION,
            "agent_job_id": str(ledger.job_id),
            "agent_approval_id": str(ledger.approval_id),
            "agent_candidate_id": str(ledger.candidate_id),
            "positive_probability": ledger.positive_probability,
            "threshold": ledger.threshold,
            "split_membership": ledger.split_membership,
            "risk_check": risk.as_dict(),
        },
    )
    db.add(decision)
    db.flush()

    order = PaperOrder(
        portfolio_id=portfolio.id,
        decision_id=decision.id,
        asset_id=ledger.instrument_id,
        side=OrderSide.BUY,
        quantity=float(quantity),
        order_type=OrderType.market,
        status=OrderStatus.pending,
        model_or_strategy_version_id=version.id,
        data_cutoff_at=candidate.signal_date,
        rationale=rationale,
        risk_check_status=RiskCheckStatus.passed,
        idempotency_key=idempotency_key,
        actor_type=EXECUTION_ACTOR_TYPE,
    )
    db.add(order)
    db.flush()

    try:
        fill = book_fill(
            db,
            order=order,
            portfolio=portfolio,
            execution_date=bar["date"],
            observed_price=observed_price,
            execution_price=execution_price,
            quantity=float(quantity),
            commission_rate=COMMISSION_RATE,
            fill_reason="agent_model_entry",
        )
    except PaperExecutionError as exc:
        # No half-applied cash: the decision and order go back with the fill, and
        # the refusal survives as this lane's own durable row. ``ledger`` was
        # never added to the session, so the rollback leaves it intact to carry
        # the score that was actually produced.
        db.rollback()
        ledger.execution_status = BLOCKED
        ledger.blocked_reason = exc.code
        return _commit_ledger(
            db,
            ledger,
            identity,
            refuse_code="paper_execution_fill_refused",
            refuse_zh=f"{exc.message}（{exc.code}）：未保留決策、委託或成交。",
        )

    # ``book_fill`` adds the row without flushing, and the fill's id is a
    # client-side column default, so the uuid only exists after this flush.
    # Without it the ledger would record a decision and an order but no trace of
    # the fill that actually moved the cash.
    db.flush()
    ledger.execution_status = EXECUTED
    ledger.decision_record_id = decision.id
    ledger.order_id = order.id
    ledger.fill_id = fill.id
    return _commit_ledger(db, ledger, identity)


def _approval_slot_is_live(
    db: Session, ledger: AgentPaperExecution, identity: Dict[str, str]
) -> None:
    """Ask the approval row itself whether it still authorizes this, under lock.

    The scope leg (:352, :366) reads the approval without locking it, so a revoke
    that commits between that read and the single ``db.commit()`` below would
    otherwise be answered with a trade the owner has already withdrawn — and this
    module's whole claim is that revoking an approval changes something readable.
    Taking ``FOR UPDATE`` on the same primary key is the lock
    ``revoke_activation_approval`` takes (app/services/model_activation.py:573-580),
    so the two transactions cannot both finish in the order they each saw the
    world: whichever acquires the row first decides which one of the two outcomes
    the ledger records. No sleep, no retry, and no read afterwards is asked to
    reconstruct an order the database did not enforce.

    ``populate_existing`` is not decoration. The scope leg already put this row in
    the session's identity map, and in this SQLAlchemy (2.0.36, measured on
    app/models/agent_strategy.py's own mapper) ``with_for_update()`` does not
    imply it: loading.py:971 takes ``populate_existing`` only from the execution
    option or ``mapper.always_refresh``. Without the option the re-read hands back
    the cached ``approved`` copy and the guard can never fire.

    What may not be claimed from here: SQLite renders no ``FOR UPDATE`` at all
    (measured: the same statement compiles with no lock clause on the sqlite
    dialect), so on that lane only the *decision* re-read is enforced — which is
    what the isolated nodes below observe. The mutual exclusion itself is a
    PostgreSQL behaviour and stays a named, unexecuted readback.
    """
    approval_id = ledger.approval_id
    live = db.scalars(
        select(AgentModelActivationApproval)
        .where(AgentModelActivationApproval.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if live is None or live.decision != "approved":
        # Nothing of ours is committed yet: the ledger row has not been added, and
        # any decision/order/fill this call had flushed goes back with the
        # transaction. A withdrawal that has taken effect buys no execution.
        db.rollback()
        _refused(
            "paper_execution_approval_revoked_midflight",
            "生效同意在這筆執行提交前已被撤回，拒絕提交執行，未留下決策、委託、成交或帳本列。",
            {
                **identity,
                "approval_id": str(approval_id),
                "approval_decision": live.decision if live is not None else None,
            },
        )


def _collides_with_intent_guard(exc: BaseException) -> bool:
    """Whether ``exc`` is a unique violation of a key this lane owns.

    ``app.api.conflicts`` holds the dialect-aware naming — PostgreSQL answers with
    a constraint name, SQLite with the violated column set — that the request
    doors already refuse to guess at. It is imported inside the function because
    ``app.api`` pulls in every router and one of those routers imports this
    module, so a top-level import leaves neither side importable first.

    Anything the vocabulary does not name is not a duplicate: a broken foreign key
    or a missing NOT NULL is a platform defect, and renaming it to "you pressed
    twice" hides it behind a message about the customer's own retry.
    """
    from app.api.conflicts import (
        AGENT_PAPER_EXECUTION_INTENT,
        PAPER_ORDER_IDEMPOTENCY,
        expected_unique_violation,
    )

    return expected_unique_violation(exc) in (
        AGENT_PAPER_EXECUTION_INTENT,
        PAPER_ORDER_IDEMPOTENCY,
    )


def _collapse_racing_press(
    db: Session, identity: Dict[str, str], exc: IntegrityError
) -> Optional[Dict[str, Any]]:
    """Undo this press's partial booking and hand back the row that won, or ``None``.

    ``db.rollback()`` is not optional: after an integrity failure the transaction
    is unusable on PostgreSQL, so a re-read that skipped it would see nothing and
    answer the winner's success with an error. ``None`` is returned when the
    constraint is not one this lane owns, or when it is one it owns but no
    committed intent row stands behind it — either way the caller re-raises
    instead of inventing a duplicate.
    """
    db.rollback()
    if not _collides_with_intent_guard(exc):
        return None
    winner = db.scalars(
        select(AgentPaperExecution).where(
            AgentPaperExecution.skill_unit_id == uuid.UUID(identity["skill_unit_id"]),
            AgentPaperExecution.job_id == uuid.UUID(identity["job_id"]),
            AgentPaperExecution.candidate_id == uuid.UUID(identity["candidate_id"]),
        )
    ).first()
    if winner is None:
        return None
    payload = execution_payload(winner)
    payload["duplicate"] = True
    payload["concurrent_insert_collapsed"] = True
    return payload


def _commit_ledger(
    db: Session,
    ledger: AgentPaperExecution,
    identity: Dict[str, str],
    *,
    refuse_code: Optional[str] = None,
    refuse_zh: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist the ledger row, then either answer the caller or refuse by name."""
    _approval_slot_is_live(db, ledger, identity)
    db.add(ledger)
    try:
        db.commit()
    except IntegrityError as exc:
        # The unique business key collapsed a concurrent double press into one
        # intent. Say so rather than pretending this request wrote the row; an
        # error this lane does not own keeps travelling out unchanged.
        collapsed = _collapse_racing_press(db, identity, exc)
        if collapsed is None:
            raise
        return collapsed
    db.refresh(ledger)
    payload = execution_payload(ledger)
    payload["duplicate"] = False
    if refuse_code is not None:
        # The ledger row is committed on its own above, so the named refusal the
        # caller receives is backed by a durable record rather than a message.
        raise AgentStrategyRefused(
            refuse_code,
            refuse_zh or "拒絕執行。",
            {
                **identity,
                "execution_id": payload["execution_id"],
                "execution_status": payload["execution_status"],
                "blocked_reason": payload["blocked_reason"],
                "risk_check": payload["risk_check"],
            },
        )
    return payload
