"""Pre-flight for the daily estimate clock: four items read, nothing written.

The lane bills per estimate, so "is tonight's run ready?" is a question that has
to be answered *before* a tick starts, from the same tables the tick reads. This
module answers it as a report rather than a decision: it never grants a consent,
never selects a version, never reserves quota and never calls the prediction
gate. It is deliberately not routed through ``GET /personalization/daily-estimate/status``,
which reports the *last* window, while an operator preparing a run needs the
next one.

The four items, in the order a missing one would be discovered:

1. the live ``personal_daily_estimate`` consent and the scope it declares — the
   third consent, granted only by the owner; training consent and a skill's model
   selection are separate facts and none of the three implies another;
2. every declared skill account: owned, served, and holding a selection whose
   model version and artifact can be named;
3. every declared instrument: the same ``features_as_of`` read the lane performs,
   reported as the cut bar and its age against the declared maximum, and whether
   this window's billing ledger has already paid for it;
4. the ``ai_usage`` units the declared scope would spend, beside what the same
   contract the lane reserves under says is left.

``billing_observation_allowed`` is the answer an experiment hangs on: it is true
only when all four items are complete, so a run that cannot settle a bill does
not get to report one. ``missing`` names each gap with the code the lane itself
uses, because an owner acting on the report has to recognise the condition.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.entitlements import get_quota_status
from app.models.kill_switch import KillSwitchScope
from app.services.daily_estimates import (
    DAILY_ESTIMATE_PURPOSE,
    MAX_INSTRUMENTS_PER_SKILL,
    MAX_SKILL_UNITS_PER_OWNER,
    SERVED_PORTFOLIO_STATUSES,
    EstimateRefused,
    _canonical_skill_id,
    _run_day,
    current_selection,
    declared_scope,
    estimate_window,
    get_daily_estimate_consent,
    owned_skill_unit,
    settled_estimates,
)
from app.services.kill_switch import is_kill_switch_active
from app.services.personal_dataset import features_as_of

QUOTA_TYPE = "ai_usage"


def _instrument_state(
    db: Session,
    instrument_id: str,
    as_of_date: str,
    max_data_age_days: Optional[int],
    *,
    already_billed: bool,
) -> Dict[str, Any]:
    """What the lane will see for one instrument, without asking it to bill.

    The cut-off and the age come from the same two functions the estimate calls
    (:func:`app.services.personal_dataset.features_as_of` and the lane's own
    ``_run_day``), so this report cannot say "fresh" about a bar the clock is
    about to refuse as stale — and cannot invent a freshness rule of its own.

    ``already_billed`` is the window's bill, not a guess: the lane re-reads
    :func:`app.services.daily_estimates.settled_estimates` per item and leaves a
    paid business key alone, so a pre-flight that counted it again would report
    a spend the tick no longer owes.
    """
    _, _, cut, exclusion = features_as_of(db, instrument_id, as_of_date)
    run_day = _run_day(as_of_date)
    bar_day = _run_day(cut) if cut is not None else None
    data_age_days = (
        max(0, (run_day - bar_day).days)
        if run_day is not None and bar_day is not None
        else None
    )
    refusal: Optional[str] = None
    if exclusion is not None:
        # The market-data door's own code, passed through unread: whatever the
        # lane would raise here, this report names the same condition.
        refusal = str(exclusion)
    elif (
        max_data_age_days is not None
        and data_age_days is not None
        and data_age_days > max_data_age_days
    ):
        refusal = "stale_market_data"
    return {
        "instrument_id": instrument_id,
        "last_bar_date": cut,
        "data_age_days": data_age_days,
        "max_data_age_days": max_data_age_days,
        "already_billed": already_billed,
        "refusal": refusal,
    }


def _selection_state(db: Session, owner_id: UUID, skill_unit_id: UUID) -> Dict[str, Any]:
    """The version this skill account is on, with the artifact identity beside it."""
    from app.models.model_research import ModelResearchRecord, ModelVersion

    selection = current_selection(db, owner_id, skill_unit_id)
    if selection is None:
        return {"refusal": "strategy_scope_missing", "skill_unit_id": str(skill_unit_id)}
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == selection.version_id)
        .one_or_none()
    )
    if version is None:
        return {
            "refusal": "model_version_not_found",
            "selection_id": str(selection.id),
            "model_version_id": str(selection.version_id),
        }
    record = (
        db.query(ModelResearchRecord)
        .filter(ModelResearchRecord.id == version.model_research_record_id)
        .one_or_none()
    )
    if record is None or record.owner_id != owner_id:
        return {
            "refusal": "model_version_not_found",
            "model_version_id": str(version.id),
        }
    return {
        "refusal": None,
        "selection_id": str(selection.id),
        "selection_action": selection.action.value if selection.action else None,
        "model_version_id": str(version.id),
        "model_research_record_id": str(version.model_research_record_id),
        # The artifact identity the gate compares bytes against, in the same
        # spelling ``_run_personal_prediction`` reads it: it lives inside the
        # version's specification, not in a column, and an estimate that starts
        # without it on record is a billing row whose source cannot be re-checked.
        "artifact_id": (version.model_specification_json or {}).get("artifact_id"),
        "artifact_sha256": (version.model_specification_json or {}).get(
            "artifact_sha256"
        ),
        "strategy_version_id": (version.model_specification_json or {}).get(
            "strategy_version_id"
        ),
        "retired_at": version.retired_at.isoformat() if version.retired_at else None,
        "revoked_at": version.revoked_at.isoformat() if version.revoked_at else None,
    }


def assess_readiness(
    db: Session, *, owner_id: UUID, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """The one report an operator needs before starting the clock. Read-only.

    ``now`` exists for the same reason the lane's window does: the report is
    about a particular UTC day, and a hand-off that has to be run at 04:5x to
    describe the coming tick is not a hand-off. Left unset it reads this instant.
    """
    window = estimate_window(now)
    missing: List[str] = []

    consent = get_daily_estimate_consent(db, owner_id)
    entries = declared_scope(consent)
    if consent is None:
        missing.append("no_active_daily_estimate_consent")
    if entries and len(entries) > MAX_SKILL_UNITS_PER_OWNER:
        missing.append("scope_too_large")

    skills: List[Dict[str, Any]] = []
    declared_instruments = 0
    spendable_instruments = 0
    settled = settled_estimates(db, owner_id, window)
    settled_by_skill: Dict[str, List[dict]] = {}
    for item in settled:
        settled_by_skill.setdefault(item["skill_unit_id"], []).append(item)
    for raw_skill, entry in entries:
        instruments = entry["instrument_ids"]
        declared_instruments += len(instruments)
        skill: Dict[str, Any] = {
            "skill_unit_id": str(raw_skill),
            "status": None,
            "max_data_age_days": entry["max_data_age_days"],
            "declared_instruments": instruments,
            "refusals": [],
            "selection": None,
            "instruments": [],
            # The window's own bills for this account, named by inference id: an
            # operator reading "already paid" needs the row the ledger holds, and
            # a count alone cannot be checked against the panel.
            "settled_inference_ids": sorted(
                str(item["inference_id"])
                for item in settled_by_skill.get(_canonical_skill_id(raw_skill), [])
            ),
        }
        skills.append(skill)
        if not instruments:
            skill["refusals"].append("no_instruments_declared")
            continue
        if len(instruments) > MAX_INSTRUMENTS_PER_SKILL:
            skill["refusals"].append("instrument_list_too_long")
            continue
        try:
            portfolio = owned_skill_unit(db, owner_id, str(raw_skill))
        except EstimateRefused as exc:
            skill["refusals"].append(exc.code)
            continue
        if portfolio is None:
            skill["refusals"].append("skill_unit_not_found")
            continue
        skill["status"] = portfolio.status.value
        if portfolio.status not in SERVED_PORTFOLIO_STATUSES:
            skill["refusals"].append(f"skill_{portfolio.status.value}")
            continue
        selection = _selection_state(db, owner_id, portfolio.id)
        skill["selection"] = selection
        if selection["refusal"] is not None:
            skill["refusals"].append(selection["refusal"])
            continue
        billed_here = {
            item["instrument_id"]
            for item in settled_by_skill.get(_canonical_skill_id(raw_skill), [])
        }
        for instrument_id in instruments:
            state = _instrument_state(
                db,
                instrument_id,
                window,
                entry["max_data_age_days"],
                already_billed=instrument_id in billed_here,
            )
            skill["instruments"].append(state)
            if state["refusal"] is not None:
                # One gap names the account once, not once per asset: the report
                # says which condition the owner has to fix, and the rows below
                # it say which instruments have it. Two assets missing bars is one
                # reason, and a list that repeated it would read as two.
                if state["refusal"] not in skill["refusals"]:
                    skill["refusals"].append(state["refusal"])
                continue
            # A key this window already paid for is fresh and complete — the tick
            # will leave it alone — so it is neither a refusal nor a spend.
            if not state["already_billed"]:
                spendable_instruments += 1

    for skill in skills:
        for code in skill["refusals"]:
            if code not in missing:
                missing.append(code)

    entitlement = get_quota_status(db, owner_id, QUOTA_TYPE)
    remaining = entitlement["remaining"]
    quota_state = {
        "quota_type": QUOTA_TYPE,
        "limit": entitlement["limit"],
        "used": entitlement["used"],
        "reserved": entitlement["reserved"],
        "remaining": remaining,
        "unlimited": entitlement["unlimited"],
        "source": entitlement["source"],
        "entitlement_active": entitlement["entitlement_active"],
        # The bill one window owes is one ``ai_usage`` unit per business key
        # (skill, instrument) the lane is about to price: the reservation happens
        # inside ``_run_personal_prediction``, once per instrument, so two skills
        # declaring three instruments each is six units and not two.
        # ``units_ready_to_spend`` is the honest remaining spend — keys this
        # window already paid for are subtracted, because the tick leaves them
        # alone, and refused keys are never added, because the freshness read
        # runs before the reservation. Sufficiency is measured against that
        # number, not against the declared total.
        "units_declared": declared_instruments,
        "units_billed_this_window": len(settled),
        "units_ready_to_spend": spendable_instruments,
        # A NULL limit is unlimited in this contract, so it is not a shortage.
        "sufficient": bool(remaining is None or remaining >= spendable_instruments),
    }
    if not quota_state["sufficient"]:
        missing.append("quota_exceeded")
    if entitlement["entitlement_active"] is False:
        missing.append("entitlement_inactive")

    try:
        kill_switch_active = is_kill_switch_active(db, KillSwitchScope.inference)
    except Exception:
        # The lane fails closed on an unreadable switch; a report that called
        # that "ready" would be the one lie this function exists to prevent.
        kill_switch_active = True
        missing.append("kill_switch_unreadable")
    if kill_switch_active:
        missing.append("kill_switch_active")

    if consent is not None and not entries:
        # A row exists and says nothing usable. Distinguished from no consent at
        # all because the owner's next action differs: rewrite the scope, not
        # grant a permission.
        missing.append("empty_scope")

    return {
        "owner_id": str(owner_id),
        "purpose": DAILY_ESTIMATE_PURPOSE,
        "window": window,
        "consent": {
            "present": consent is not None,
            "consent_id": str(consent.id) if consent is not None else None,
            "granted_at": consent.granted_at.isoformat()
            if consent is not None and consent.granted_at
            else None,
            "skill_units": [str(skill_id) for skill_id, _ in entries],
            "declared_instrument_count": declared_instruments,
        },
        "skills": skills,
        "quota": quota_state,
        "kill_switch": {"inference_active": kill_switch_active},
        "missing": missing,
        "billing_observation_allowed": not missing,
        "verdict": "READY" if not missing else "NOT_READY",
    }
