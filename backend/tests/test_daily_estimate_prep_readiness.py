"""The isolated preparation a daily-estimate run needs, and the read-only check
that says whether it is complete. Written for the pending card B; nothing here
presses B's owner-only consent on the owner's behalf, starts a service, or
touches a PostgreSQL target.

Item 3 of the nineteenth round asks for the existing real training chain
minimised into reusable prep: **one** test owner, **one** skill account, **two**
eligible instruments, a **real** artifact, version and selection — reached only
through the formal endpoints — plus a readiness report an operator can run
before a scheduler starts. This module is that prep, executed on the SQLite
lane, and each node says what the report measured rather than what it should
hope for.

What is real and what is fixture:

* the model version is trained by ``POST /ml/personal/training-jobs`` and the
  worker's own ``run_claimed_job``, so the artifact bytes, the ``artifact_sha256``
  and the ``strategy_version_id`` inside the version's specification are
  produced, not declared. This is the difference between this file and
  ``tests/test_daily_model_estimate.py``, whose ``personal_env`` writes a
  completed training job and an artifact directly: that fixture proves the lane,
  this one proves the chain that has to run before the lane is worth starting;
* the skill account comes from ``POST /paper-portfolio``, the switch from
  ``POST …/models/{id}/select``, the bars from the deterministic synthetic series
  this repository's other personal-learning lanes use;
* the *consents* differ by purpose and stay separate here: training consent
  (``personalization_training``) is granted by the prep because the chain cannot
  build a dataset without it, while the daily-execution consent
  (``personal_daily_estimate``) is the owner's own act — so the prep node asserts
  it is **absent**, and the nodes below it grant it inside a throwaway database,
  which is branch evidence about the report and is not, and cannot become,
  anyone's consent for a live run.

One node (`test_one_press_through_the_formal_door_is_then_named_as_billed`) does
press the estimate door once, through ``POST …/daily-estimate/run``, and so does
spend two ``ai_usage`` units — on this process's own temporary SQLite file, with
no service, no scheduler, no 05:00 wait, no socket and no PostgreSQL target. The
stop line is read rather than asserted: that node counts what the billing ledger
holds under ``via="daily_service"``, and the nodes that claim nothing was billed
count zero rows there themselves.
"""

import os
import uuid
from datetime import datetime, time, timezone

import pytest

from app.models.audit_event import AuditEvent
from app.models.billing import UsageRecord
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.market_data import MarketDataMetadata
from app.models.personal_model_selection import PersonalModelSelection
from app.models.personalization import PersonalizationConsent
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.user import User
from app.services.daily_estimate_readiness import assess_readiness
from app.services.daily_estimates import DAILY_SERVICE_VIA, estimate_window
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from tests.conftest import TestSession
from tests.test_personal_model_comparison import _model_for_job, _start_training
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _artifact_sha256,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _open_skill_unit,
    _personal_artifacts,
    _seed_bars,
    _seed_confirmed_decisions,
    _select_for_skill,
    _user_id,
)

DAILY_BASE = "/api/v1/ml/personalization/daily-estimate"
SECOND_INSTRUMENT = "US:MSFT"
UNDECLARED_INSTRUMENT = "US:NOBARS"

# The window the report is asked about, pinned rather than left to the wall clock
# so a node cannot pass on one day and fail on the next. ``assess_readiness``
# reads its cut-off as the start of the UTC day, so the last bar it can see is
# the one before this date — measured, not asserted from a hope.
WINDOW = BARS[-1]
WINDOW_INSTANT = datetime.combine(WINDOW, time(5, 0), tzinfo=timezone.utc)
# The age of the newest bar that window can see, derived from the fixture's own
# series instead of typed in. Every freshness ceiling in this file is written
# relative to it, so a change to ``BARS`` moves the ceilings with it rather than
# turning a measured boundary into a stale constant.
BAR_AGE_DAYS = (BARS[-1] - BARS[-2]).days
LAST_VISIBLE_BAR = BARS[-2]


@pytest.fixture(autouse=True)
def _two_instrument_market(setup_db):
    """Bars and provenance for both instruments the prepared scope declares."""
    db = TestSession()
    try:
        for instrument in (INSTRUMENT, SECOND_INSTRUMENT):
            _seed_bars(db, instrument)
            db.add(
                MarketDataMetadata(
                    instrument_id=instrument,
                    provider_id="synthetic",
                    data_nature="synthetic",
                    first_bar_date=BARS[0],
                    last_bar_date=BARS[-1],
                    total_bars=len(BARS),
                )
            )
        db.commit()
        seeded = {
            row.instrument_id
            for row in db.query(MarketDataMetadata)
            .filter(MarketDataMetadata.instrument_id.in_([INSTRUMENT, SECOND_INSTRUMENT]))
            .all()
        }
    finally:
        db.close()
    assert seeded == {INSTRUMENT, SECOND_INSTRUMENT}, seeded
    yield


@pytest.fixture()
def lane(client, auth_headers, tmp_path, monkeypatch):
    """One owner, one frozen strategy version, one real model, one skill account.

    This is the whole prep: every entry below is a route a customer passes
    through, and the freeze is load-bearing — an unlocked version is not a
    decision source the platform will book against.
    """
    owner_id = _user_id()
    experiment_id, strategy_version_id = _create_experiment(
        client, auth_headers, title="Daily estimate prep lane"
    )
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：凍結策略版本，作為每日估計的來源"},
    )
    assert locked.status_code == 200, locked.text

    _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))
    _grant_consent(client, auth_headers, strategy_version_id)
    snapshot = _build_snapshot(client, auth_headers, strategy_version_id)
    artifacts_dir = _personal_artifacts(tmp_path, monkeypatch)

    job_id = _start_training(
        client, auth_headers, snapshot["snapshot_id"], "logistic_regression"
    )
    model = _model_for_job(client, auth_headers, job_id, strategy_version_id)
    skill_unit_id = _open_skill_unit(client, auth_headers)
    _select_for_skill(client, auth_headers, model["model_version_id"], skill_unit_id)

    return {
        "owner_id": owner_id,
        "headers": auth_headers,
        "client": client,
        "artifacts_dir": artifacts_dir,
        "strategy_version_id": strategy_version_id,
        "snapshot_id": snapshot["snapshot_id"],
        "training_job_id": job_id,
        "model_version_id": model["model_version_id"],
        "record_id": model["record_id"],
        "artifact_id": model["artifact_id"],
        "artifact_sha256": model["artifact_sha256"],
        "skill_unit_id": skill_unit_id,
    }


def _units(skill_unit_id, instruments=(INSTRUMENT, SECOND_INSTRUMENT), max_age=None):
    entry = {"skill_unit_id": str(skill_unit_id), "instrument_ids": list(instruments)}
    if max_age is not None:
        entry["max_data_age_days"] = max_age
    return [entry]


def _grant_daily(client, headers, units):
    """The owner's own door, called in-fixture for branch evidence only.

    The body is the shape the panel sends. ``confirm`` is the owner's
    acknowledgement, and no part of it is being claimed on anyone's behalf
    outside this throwaway database.
    """
    return client.put(
        f"{DAILY_BASE}/consent",
        headers=headers,
        json={"skill_units": units, "confirm": True},
    )


def _report(lane, **kwargs):
    """The readiness report, by default for the pinned window."""
    kwargs.setdefault("now", WINDOW_INSTANT)
    db = TestSession()
    try:
        return assess_readiness(db, owner_id=lane["owner_id"], **kwargs)
    finally:
        db.close()


def _report_now(lane):
    """The same report on the wall clock, which is what a press bills against."""
    db = TestSession()
    try:
        return assess_readiness(db, owner_id=lane["owner_id"])
    finally:
        db.close()


def _skill(report, skill_unit_id):
    matches = [
        item
        for item in report["skills"]
        if item["skill_unit_id"] == str(skill_unit_id)
    ]
    assert len(matches) == 1, [item["skill_unit_id"] for item in report["skills"]]
    return matches[0]


def _consent_rows(owner_id):
    db = TestSession()
    try:
        return (
            db.query(PersonalizationConsent)
            .filter(PersonalizationConsent.owner_id == owner_id)
            .all()
        )
    finally:
        db.close()


def _selection_rows(owner_id, skill_unit_id):
    db = TestSession()
    try:
        return (
            db.query(PersonalModelSelection)
            .filter(
                PersonalModelSelection.owner_id == owner_id,
                PersonalModelSelection.skill_unit_id == uuid.UUID(str(skill_unit_id)),
            )
            .all()
        )
    finally:
        db.close()


def _daily_bills(owner_id):
    """The billing rows this lane wrote, and only those."""
    db = TestSession()
    try:
        return [
            row
            for row in db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .all()
            if (row.metadata_json or {}).get("via") == DAILY_SERVICE_VIA
        ]
    finally:
        db.close()


def test_the_prepared_lane_is_complete_except_for_the_owners_own_consent(lane):
    """Three of the four items measure positive; the fourth is absent by design.

    This node is the prep's own readback. The single reason the report refuses a
    billing observation is the consent nobody but the owner grants, and the reason
    its ``skills`` list is empty is an empty scope — not a missing switch: the
    selection row the prep wrote is read back straight from the store beside it.
    A fixture that had quietly skipped the training would fail here, because the
    artifact digest and the strategy version are the ones the worker's row carries.
    """
    report = _report(lane)

    assert report["verdict"] == "NOT_READY", report["missing"]
    assert report["missing"] == ["no_active_daily_estimate_consent"], report["missing"]
    assert report["billing_observation_allowed"] is False
    assert report["purpose"] == "personal_daily_estimate"
    assert report["window"] == WINDOW.isoformat()
    assert report["consent"]["present"] is False
    assert report["consent"]["consent_id"] is None
    assert report["consent"]["skill_units"] == []
    assert report["consent"]["declared_instrument_count"] == 0

    # Nothing declared, so nothing measured — and nothing owed. An empty scope is
    # reported as empty rather than as a row of unknown instruments.
    assert report["skills"] == []
    assert report["quota"]["units_declared"] == 0
    assert report["quota"]["units_ready_to_spend"] == 0
    assert report["quota"]["units_billed_this_window"] == 0

    # The consent that IS present belongs to another purpose, and the report
    # cannot be reading it: a query on the training row would say "ready" about
    # an opt-in nobody made.
    purposes = {row.purpose for row in _consent_rows(lane["owner_id"])}
    assert "personalization_training" in purposes, purposes
    assert "personal_daily_estimate" not in purposes, purposes

    # The other two facts this report has not been asked about are in the store,
    # written by the routes: the switch, and the version it points at.
    selections = _selection_rows(lane["owner_id"], lane["skill_unit_id"])
    assert len(selections) == 1, selections
    assert str(selections[0].version_id) == lane["model_version_id"]
    assert len(lane["artifact_sha256"]) == 64
    assert _daily_bills(lane["owner_id"]) == []


def test_the_granted_scope_reads_back_the_prep_identity(lane, client, auth_headers):
    """Grant the scope in-fixture and the report answers with the prep's identity.

    The four items, all from rows the platform wrote: the selection names the
    version this fixture trained, ``artifact_sha256`` equals the digest recomputed
    from the artifact bytes on disk — not the string the API echoed — the strategy
    version is the frozen one the snapshot was built from, and both declared
    instruments carry a cut bar with an age this module derives from ``BARS``.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text

    report = _report(lane)
    assert report["verdict"] == "READY", report["missing"]
    assert report["billing_observation_allowed"] is True
    assert report["consent"]["present"] is True
    assert report["consent"]["skill_units"] == [str(lane["skill_unit_id"])]
    assert report["consent"]["declared_instrument_count"] == 2

    skill = _skill(report, lane["skill_unit_id"])
    assert skill["status"] == "active", skill
    assert skill["refusals"] == []
    assert skill["declared_instruments"] == [INSTRUMENT, SECOND_INSTRUMENT]
    assert skill["settled_inference_ids"] == []
    assert skill["selection"]["model_version_id"] == lane["model_version_id"]
    assert skill["selection"]["artifact_id"] == lane["artifact_id"]
    assert skill["selection"]["artifact_sha256"] == lane["artifact_sha256"]
    assert skill["selection"]["strategy_version_id"] == str(lane["strategy_version_id"])
    assert skill["selection"]["artifact_sha256"] == _artifact_sha256(
        lane["artifacts_dir"], lane["artifact_id"]
    )
    assert skill["selection"]["retired_at"] is None
    assert skill["selection"]["revoked_at"] is None

    assert [item["instrument_id"] for item in skill["instruments"]] == [
        INSTRUMENT,
        SECOND_INSTRUMENT,
    ]
    for item in skill["instruments"]:
        assert item["refusal"] is None, item
        assert item["already_billed"] is False, item
        assert item["last_bar_date"] == LAST_VISIBLE_BAR.isoformat(), item
        assert item["max_data_age_days"] is None, item
        assert item["data_age_days"] == (WINDOW - LAST_VISIBLE_BAR).days, item
        assert item["data_age_days"] == BAR_AGE_DAYS, item

    quota = report["quota"]
    assert quota["quota_type"] == "ai_usage"
    assert quota["source"] == "free_plan", quota
    assert quota["units_declared"] == 2
    assert quota["units_billed_this_window"] == 0
    assert quota["units_ready_to_spend"] == 2
    assert quota["sufficient"] is True
    assert quota["remaining"] == quota["limit"] - quota["used"] - quota["reserved"]
    assert quota["remaining"] >= 2


def test_an_instrument_without_bars_is_named_by_the_market_data_door(
    lane, client, auth_headers
):
    """A declared asset with no stored history is a refusal, not an empty cell.

    The code is the one ``features_as_of`` returns, passed through unread — the
    report cannot call a gap "ready", and cannot rename it into something the
    panel would not recognise. The spend count follows the refusal, because the
    freshness read runs before any unit is reserved.
    """
    granted = _grant_daily(
        client,
        auth_headers,
        _units(lane["skill_unit_id"], instruments=[INSTRUMENT, UNDECLARED_INSTRUMENT]),
    )
    assert granted.status_code == 200, granted.text

    report = _report(lane)
    assert report["verdict"] == "NOT_READY"
    assert report["missing"] == ["missing_market_data"], report["missing"]
    skill = _skill(report, lane["skill_unit_id"])
    by_id = {item["instrument_id"]: item for item in skill["instruments"]}
    assert by_id[INSTRUMENT]["refusal"] is None
    assert by_id[UNDECLARED_INSTRUMENT]["last_bar_date"] is None
    assert by_id[UNDECLARED_INSTRUMENT]["data_age_days"] is None
    assert by_id[UNDECLARED_INSTRUMENT]["refusal"] == "missing_market_data"
    assert skill["refusals"] == ["missing_market_data"], skill
    assert report["billing_observation_allowed"] is False
    assert report["quota"]["units_declared"] == 2
    assert report["quota"]["units_ready_to_spend"] == 1


def test_a_bar_older_than_the_declared_ceiling_is_reported_stale(
    lane, client, auth_headers
):
    """The ceiling the owner declared is arithmetic, and the report does it.

    Same data as the READY node — an age of ``BAR_AGE_DAYS`` — measured against a
    ceiling exactly equal to it and one year past it, so the boundary is measured
    rather than argued: at the ceiling is fresh, past it is ``stale_market_data``.
    The route's own ``ge=1`` floor means a ceiling *below* this fixture's age is
    unrequestable rather than refused, which is the first branch below.
    """
    zero = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"], max_age=0))
    # ``ge=1`` is the route's own floor: a ceiling of zero would refuse every
    # window, so the request that asks for one is invalid rather than denied.
    assert zero.status_code == 422, zero.text

    exact = _grant_daily(
        client,
        auth_headers,
        _units(lane["skill_unit_id"], max_age=BAR_AGE_DAYS),
    )
    assert exact.status_code == 200, exact.text
    ready = _report(lane)
    assert ready["verdict"] == "READY", ready["missing"]
    for item in _skill(ready, lane["skill_unit_id"])["instruments"]:
        assert item["data_age_days"] == item["max_data_age_days"], item
        assert item["refusal"] is None, item

    loose_window = WINDOW.replace(year=WINDOW.year + 1)
    stale = _report(
        lane, now=datetime.combine(loose_window, time(5, 0), tzinfo=timezone.utc)
    )
    assert stale["verdict"] == "NOT_READY", stale["missing"]
    assert stale["missing"] == ["stale_market_data"], stale["missing"]
    assert stale["window"] == loose_window.isoformat()
    for item in _skill(stale, lane["skill_unit_id"])["instruments"]:
        assert item["refusal"] == "stale_market_data", item
        assert item["data_age_days"] > item["max_data_age_days"], item
    assert stale["quota"]["units_ready_to_spend"] == 0


def test_a_declared_skill_without_a_selection_refuses_only_itself(
    lane, client, auth_headers
):
    """Two accounts in scope, one of them never switched on: the report splits.

    ``strategy_scope_missing`` is the lane's own code for this. An account the
    owner declared but never selected a model for must not turn the other
    account's readiness into a guess, and must not be silently dropped either —
    a row that disappears reads as "never declared".
    """
    silent_skill = _open_skill_unit(client, auth_headers)
    granted = _grant_daily(
        client,
        auth_headers,
        _units(lane["skill_unit_id"]) + _units(silent_skill),
    )
    assert granted.status_code == 200, granted.text

    report = _report(lane)
    assert report["verdict"] == "NOT_READY"
    assert report["missing"] == ["strategy_scope_missing"], report["missing"]
    assert report["consent"]["declared_instrument_count"] == 4

    prepared = _skill(report, lane["skill_unit_id"])
    assert prepared["refusals"] == [], prepared
    assert prepared["selection"]["model_version_id"] == lane["model_version_id"]
    assert len(prepared["instruments"]) == 2

    silent = _skill(report, silent_skill)
    assert silent["refusals"] == ["strategy_scope_missing"], silent
    assert silent["selection"]["refusal"] == "strategy_scope_missing"
    assert silent["instruments"] == []
    assert report["quota"]["units_declared"] == 4
    assert report["quota"]["units_ready_to_spend"] == 2


def test_exhausted_quota_is_named_before_any_estimate_is_billed(
    lane, client, auth_headers
):
    """Short the scope by one unit of what it owes, and the report says NOT_READY.

    The limit and the units are read out of the first report rather than
    hardcoded, so the number this node spends is the platform's current
    ``ai_usage`` allowance and the arithmetic survives a plan change. Moving that
    counter is a row on a throwaway database — no estimate ran, so the daily lane
    billed nothing, which is the whole point of asking before the clock does.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text

    before = _report(lane)
    assert before["verdict"] == "READY", before["missing"]
    limit = before["quota"]["limit"]
    assert limit is not None, before["quota"]
    reserved = before["quota"]["reserved"]
    needed = before["quota"]["units_ready_to_spend"]
    assert needed == 2, before["quota"]
    shortfall = needed - 1

    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == lane["owner_id"],
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            )
            .one_or_none()
        )
        target_used = limit - shortfall - reserved
        assert target_used >= 0, (limit, shortfall, reserved)
        if counter is None:
            db.add(
                QuotaCounter(
                    user_id=lane["owner_id"],
                    quota_type="ai_usage",
                    period="lifetime",
                    used=target_used,
                )
            )
        else:
            counter.used = target_used
        db.commit()
    finally:
        db.close()

    after = _report(lane)
    assert after["verdict"] == "NOT_READY", after["missing"]
    assert after["missing"] == ["quota_exceeded"], after["missing"]
    assert after["quota"]["units_declared"] == 2
    assert after["quota"]["units_ready_to_spend"] == needed
    assert after["quota"]["used"] == target_used
    assert after["quota"]["remaining"] == shortfall
    assert after["quota"]["sufficient"] is False
    assert after["billing_observation_allowed"] is False

    assert _daily_bills(lane["owner_id"]) == []
    db = TestSession()
    try:
        assert (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == lane["owner_id"],
                QuotaReservation.status == "reserved",
            )
            .count()
            == 0
        )
    finally:
        db.close()


def test_an_active_inference_switch_makes_the_report_not_ready(lane, client, auth_headers):
    """A pulled stop control is a reason not to start, named as such.

    The lane fails closed on an unreadable or active ``inference`` switch; a
    pre-flight that ignored it would hand an operator a READY for a tick that
    bills nothing and records refusals.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text
    assert _report(lane)["verdict"] == "READY"

    db = TestSession()
    try:
        activate_kill_switch(
            db,
            KillSwitchScope.inference,
            activated_by=str(lane["owner_id"]),
            reason="測試：確認就緒檢查讀到停機開關",
        )
        db.commit()
        during = assess_readiness(db, owner_id=lane["owner_id"], now=WINDOW_INSTANT)
        deactivate_kill_switch(db, KillSwitchScope.inference)
        db.commit()
        after = assess_readiness(db, owner_id=lane["owner_id"], now=WINDOW_INSTANT)
    finally:
        db.close()

    assert during["kill_switch"]["inference_active"] is True
    assert during["missing"] == ["kill_switch_active"], during["missing"]
    assert during["billing_observation_allowed"] is False
    assert after["kill_switch"]["inference_active"] is False
    assert after["verdict"] == "READY", after["missing"]


def test_one_press_through_the_formal_door_is_then_named_as_billed(
    lane, client, auth_headers
):
    """What the report calls spendable becomes, after a press, what it calls paid.

    The prep's last open question is whether the four items it measures are the
    same four the tick acts on. This node answers it by pressing the owner's own
    button once, through ``POST …/daily-estimate/run`` — the clock's own lane with
    ``scope="manual"`` — and reading the result back out of ``usage_records`` and
    out of the report, in both windows:

    * the wall-clock window the press billed shows two settled keys, nothing left
      to spend, and the two inference ids the ledger actually holds;
    * the pinned 2024 window the other nodes measure still shows nothing billed,
      because the window — not the clock — is the dedup key.

    Two ``ai_usage`` units are genuinely spent here, on this process's own
    temporary SQLite file. No service, scheduler, socket or PostgreSQL target is
    involved, and the estimate is read off synthetic 2024 bars for
    ``US:AAPL``/``US:MSFT`` — it is not advice about anything.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text
    assert _report(lane)["verdict"] == "READY"

    pressed = client.post(
        f"{DAILY_BASE}/run",
        headers=auth_headers,
        json={"skill_unit_id": str(lane["skill_unit_id"])},
    )
    assert pressed.status_code == 201, pressed.text
    body = pressed.json()
    assert body["job"]["scope"] == "manual"
    assert body["job"]["estimated"] == 2, body["job"]
    assert body["job"]["refused"] == 0, body["job"]
    assert body["job"]["failed"] == 0, body["job"]
    today = estimate_window()
    assert body["status"]["last_run"]["window"] == today, body["status"]["last_run"]

    bills = _daily_bills(lane["owner_id"])
    assert len(bills) == 2, bills
    for row in bills:
        meta = row.metadata_json or {}
        assert meta["via"] == DAILY_SERVICE_VIA
        assert meta["skill_unit_id"] == str(lane["skill_unit_id"])
        assert meta["as_of_date"] == today, meta
        assert meta["model_version_id"] == lane["model_version_id"], meta
        assert meta["artifact_sha256"] == lane["artifact_sha256"], meta
    assert {row.metadata_json["instrument_id"] for row in bills} == {
        INSTRUMENT,
        SECOND_INSTRUMENT,
    }

    now_report = _report_now(lane)
    assert now_report["window"] == today, now_report["window"]
    assert now_report["verdict"] == "READY", now_report["missing"]
    now_skill = _skill(now_report, lane["skill_unit_id"])
    assert now_skill["settled_inference_ids"] == sorted(str(row.id) for row in bills)
    for item in now_skill["instruments"]:
        assert item["already_billed"] is True, item
    assert now_report["quota"]["units_billed_this_window"] == 2
    assert now_report["quota"]["units_ready_to_spend"] == 0

    # The pinned window is untouched by a bill in today's: two windows, two
    # accounts, and the report answers each one separately.
    pinned = _report(lane)
    assert pinned["window"] == WINDOW.isoformat()
    assert pinned["quota"]["units_billed_this_window"] == 0
    assert pinned["quota"]["units_ready_to_spend"] == 2
    assert _skill(pinned, lane["skill_unit_id"])["settled_inference_ids"] == []

    db = TestSession()
    try:
        job = (
            db.query(LearningJob)
            .filter(LearningJob.user_id == lane["owner_id"])
            .order_by(LearningJob.created_at.desc())
            .first()
        )
        assert job.config_json["scope"] == "manual", job.config_json
        assert job.config_json["window"] == today, job.config_json
        assert job.result_json["billed_in_window"] == 2, job.result_json
        assert job.result_json["refused"] == [], job.result_json
        assert job.result_json["errors"] == [], job.result_json
        assert (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == lane["owner_id"],
                QuotaReservation.status == "reserved",
            )
            .count()
            == 0
        )
    finally:
        db.close()


def test_the_report_reads_and_writes_nothing(lane, client, auth_headers):
    """The tables the report touches, counted around it.

    Counted because "read-only" is the property card B's operator relies on: a
    pre-flight that quietly settled a reservation or revoked a lapsed consent
    would change the state it claims only to observe. The census covers every
    table ``assess_readiness`` reads, and the two reports are compared for
    equality so a second read cannot answer differently.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text

    tables = (
        PersonalizationConsent,
        PersonalModelSelection,
        UsageRecord,
        QuotaCounter,
        QuotaReservation,
        LearningJob,
        AuditEvent,
        MarketDataMetadata,
        User,
    )

    def census(db):
        return {table.__tablename__: db.query(table).count() for table in tables}

    db = TestSession()
    try:
        before = census(db)
        first = assess_readiness(db, owner_id=lane["owner_id"], now=WINDOW_INSTANT)
        second = assess_readiness(db, owner_id=lane["owner_id"], now=WINDOW_INSTANT)
        after = census(db)
    finally:
        db.close()

    assert before == after, {
        name: (before[name], after[name])
        for name in before
        if before[name] != after[name]
    }
    assert first == second
    assert first["verdict"] == "READY", first["missing"]
    assert first["consent"]["consent_id"] == second["consent"]["consent_id"]
    assert _daily_bills(lane["owner_id"]) == []


def test_the_cli_refuses_before_it_reports(monkeypatch, capsys, client, auth_headers):
    """The operator's entry point answers its guards, then the report, by exit code.

    No ``DATABASE_URL``, an id that is not a uuid and an owner who is not in the
    target database all exit ``2`` without producing a report. The third is the
    one that matters: a missing owner reported as ``NOT_READY`` would send the
    operator looking for a permission they never needed to grant.

    Then the real read: this lane's own SQLite file, the prep's owner, and exit
    ``3`` — a valid answer, not a crash — because the daily consent is the one
    thing this file cannot obtain for anybody. The printed report is checked for
    the gap by name and for the absence of the connection string: the script
    discloses dialect, database, host and port, and never a URL or a password.
    """
    import scripts.daily_estimate_readiness as cli

    configured = os.environ["DATABASE_URL"]

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.main(["--owner-id", str(uuid.uuid4())]) == 2
    assert "DATABASE_URL is unset" in capsys.readouterr().err

    monkeypatch.setenv("DATABASE_URL", configured)
    assert cli.main(["--owner-id", "not-a-uuid"]) == 2
    assert "not a uuid" in capsys.readouterr().err

    stranger = uuid.uuid4()
    assert cli.main(["--owner-id", str(stranger)]) == 2
    refused = capsys.readouterr().err
    assert f"no user {stranger}" in refused, refused

    assert cli.main(["--owner-id", str(_user_id())]) == 3
    printed = capsys.readouterr().out
    assert '"verdict": "NOT_READY"' in printed, printed
    assert '"no_active_daily_estimate_consent"' in printed, printed
    assert '"dialect": "sqlite"' in printed, printed
    assert "://" not in printed, printed
    assert configured not in printed, printed
