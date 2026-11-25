"""#187: the daily model-estimate lane — off until a skill asks, one estimate per window, estimates only.

What this file proves
---------------------
Every node drives the lane the way an owner's account reaches it: a
password-authenticated session declares a scope through
``PUT /api/v1/ml/personalization/daily-estimate/consent``, the clock (or the
owner's own ``POST …/run``) walks that scope, and one estimate comes out of
``app.api.personal_models._run_personal_prediction`` — the same function the
owner's manual ``POST …/predict`` uses, with ``via="daily_service"``. The
assertions are made on what the platform *wrote*: the ``learning_jobs`` ledger
row, the ``usage_records`` billing row behind each estimate, and the
``GET …/daily-estimate/status`` read an owner uses to ask "did it run, on which
version, at what data time point, and if not — why".

Seven claims get their own node rather than riding along with a green suite:

* nothing runs before the owner names a skill, and a training consent — a
  different row with a different ``purpose`` — does not name one;
* a stop control the owner pulled stops the service, and the stop the owner did
  not pull does not;
* one window estimates one skill once, so a retry, a second worker or a restart
  cannot bill the same day twice — and the refusal that proves it is in the
  ledger, not in a process flag this test could not share with a real worker;
* switching the selected model does not rewrite the estimate that was already
  made, and cannot be used to re-bill the window it belongs to;
* stale data is refused *before* any quota is reserved, because the owner
  declared the ceiling;
* where the gate refuses, the service and the person at the HTTP door are told
  the same thing, by the same code;
* what a retry owes inside a window it was not billed in: a refusal is that
  window's record and is not attempted again, while an error that left neither a
  bill nor a refusal is.

What this file does NOT prove
-----------------------------
The model versions come from ``tests/test_successful_inference.py``'s
``personal_env`` fixture, which writes the artifact and a "completed" training
job directly; the bars are a deterministic synthetic series for
``TEST-ENGINEERING-ASSET``. So these nodes prove the *lane* — the consent, the
clock, the gate, the ledger, the read — not that a real training run produced a
usable model, and nothing here says anything about market validity. The
cross-worker overlap is proven by running the tick twice against the durable
ledger in one process; two OS processes racing on the same window is a
PostgreSQL row-lock question and is NOT RUN here (SQLite cannot show it, and no
PostgreSQL lane was approved for this candidate); that half has its own prepared
node, ``tests/test_daily_estimate_two_workers.py::
test_two_processes_on_one_window_bill_each_business_key_once``, which skips on
this lane by name so nothing here can claim it passed. The 05:00 UTC trigger is
asserted as a registered cron expression; no node waited for a real clock to
strike 05:00, and nothing here started the scheduler, a live daemon or an
outbound provider call.
"""

import os
import shutil
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.models.audit_event import AuditEvent
from app.models.billing import UsageRecord
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.models.paper_portfolio import (
    DecisionRecord,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.personalization import PersonalizationConsent
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaReservation
from app.models.user import User
from app.services import daily_estimates as de
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from app.workers import learning_scheduler as ls
from tests.conftest import TestSession
from tests.test_successful_inference import (  # noqa: F401  (fixture import)
    AS_OF_DATE,
    TEST_INSTRUMENT,
    _create_skill_unit,
    _register_and_login,
    _select,
    add_trained_version,
    personal_env,
    seed_daily_bars,
)
from tests.test_successful_inference import PERSONAL_ARTIFACTS_DIR

BASE = "/api/v1/ml/personalization/daily-estimate"

# A second instrument the fixture can seed bars for, so a scope that names two
# assets is proved to serve two assets rather than refuse one for missing data.
SECOND_INSTRUMENT = "TEST-ENGINEERING-ASSET-B"

# The windows are the dedup keys, and they are also the data cut-off: a window of
# 2024-01-31 over bars ending 2024-01-30 is one day of data age, which is what
# makes "fresher than the ceiling I declared" and "stale" the same arithmetic.
FRESH = AS_OF_DATE  # 2024-01-31 → last visible bar 2024-01-30, age 1 day
NEXT_WINDOW = "2024-02-01"  # age 2 days
STALE_WINDOW = "2024-06-01"  # age 123 days (2024 is a leap year)
STALE_WINDOW_B = "2024-06-02"  # age 124 days


# ---------------------------------------------------------------------------
# helpers: the formal doors, and the two books the lane writes into
# ---------------------------------------------------------------------------


def _grant(client, headers, units, *, confirm=True):
    return client.put(
        f"{BASE}/consent",
        headers=headers,
        json={"skill_units": units, "confirm": confirm},
    )


def _declared(client, headers):
    return client.get(f"{BASE}/consent", headers=headers)


def _status(client, headers, skill_unit_id=None):
    params = (
        {"skill_unit_id": str(skill_unit_id)} if skill_unit_id is not None else None
    )
    return client.get(f"{BASE}/status", headers=headers, params=params)


def _run_now(client, headers, skill_unit_id):
    return client.post(
        f"{BASE}/run", headers=headers, json={"skill_unit_id": str(skill_unit_id)}
    )


def _declare(env, *, instruments=(TEST_INSTRUMENT,), max_age=None, skill=None):
    """The owner's own opt-in, through the formal route."""
    body = {
        "skill_unit_id": str(skill or env["portfolio"].id),
        "instrument_ids": list(instruments),
    }
    if max_age is not None:
        body["max_data_age_days"] = max_age
    response = _grant(env["client"], env["headers"], [body])
    assert response.status_code == 200, response.text
    return response.json()


def _tick(*, window, user_id=None, scope="daily"):
    return de.run_daily_estimate_tick(scope=scope, window=window, user_id=user_id)


def _cron(*, user_id=None):
    """The scheduled entry itself, with no window handed to it.

    ``run_scheduled_daily_estimates`` is the function the 05:00 job calls; it
    derives today's UTC window. Using it rather than a literal keeps "the clock
    and the button file under one key" a statement about the two real entries
    and not about a string this file picked.
    """
    return de.run_scheduled_daily_estimates(user_id=user_id)


def _set_quota(owner_id, limit):
    """Give the test account a working ``ai_usage`` ceiling again."""
    db = TestSession()
    try:
        rows = (
            db.query(PilotEntitlement).filter(PilotEntitlement.user_id == owner_id).all()
        )
        assert rows, "the account this node exhausted has no entitlement row to lift"
        for row in rows:
            row.ai_usage_limit = limit
        db.commit()
    finally:
        db.close()


def _jobs(owner_id=None, *, job_type=de.DAILY_ESTIMATE_JOB_TYPE):
    """Plain values, so the assertions survive the session closing."""
    db = TestSession()
    try:
        query = db.query(LearningJob).filter(LearningJob.job_type == job_type)
        if owner_id is not None:
            query = query.filter(LearningJob.user_id == owner_id)
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "status": row.status,
                "config": dict(row.config_json or {}),
                "result": dict(row.result_json or {}),
                "error_details": row.error_details,
            }
            for row in query.order_by(LearningJob.created_at).all()
        ]
    finally:
        db.close()


def _last_job(owner_id):
    jobs = _jobs(owner_id)
    assert jobs, "the tick wrote no ledger row"
    return jobs[-1]


def _estimates(owner_id):
    """The billing rows the lane left behind, with the metadata it wrote."""
    db = TestSession()
    try:
        return [
            {
                "id": row.id,
                "endpoint": row.endpoint,
                "metadata": dict(row.metadata_json or {}),
            }
            for row in db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .order_by(UsageRecord.created_at)
            .all()
        ]
    finally:
        db.close()


def _served(owner_id):
    return [row for row in _estimates(owner_id) if row["metadata"].get("via") == "daily_service"]


def _reservations(owner_id):
    db = TestSession()
    try:
        return [
            row.status
            for row in db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == owner_id,
                QuotaReservation.quota_type == "ai_usage",
            )
            .all()
        ]
    finally:
        db.close()


def _window_state(owner_id, window, *, skill=None):
    """What the lane can durably prove about one owner in one window.

    Read through ``de.window_state`` rather than re-derived here, so the four
    states the owner asked to keep apart - claim, persisted estimate, settled
    quota, unwritten summary - are the service's own answer and not the test's.
    """
    db = TestSession()
    try:
        state = de.window_state(db, owner_id, window)
    finally:
        db.close()
    if skill is None:
        return state
    matches = [
        item for item in state["skills"] if item["skill_unit_id"] == str(skill)
    ]
    assert len(matches) == 1, [item["skill_unit_id"] for item in state["skills"]]
    return matches[0]


def _bar_count():
    """How many market bars exist in the database at all.

    The one output a collection job has, so a count that does not move across an
    estimate tick says nothing was collected on the way past.
    """
    db = TestSession()
    try:
        return db.query(MarketDataBar).count()
    finally:
        db.close()


def _open_env(client, personal_env):
    """``personal_env`` plus the client, because the tick needs both doors."""
    personal_env["client"] = client
    return personal_env


def _second_skill(env):
    """A second skill account of the same owner, with its own selection."""
    db = env["db"]
    portfolio = _create_skill_unit(db, env["user"].id)
    _select(db, env["user"].id, env["version"], env["record"], portfolio.id)
    db.commit()
    return portfolio


# ---------------------------------------------------------------------------
# 1 — default off
# ---------------------------------------------------------------------------


def test_the_clock_is_off_until_a_skill_is_named(client, personal_env):
    """No consent row → the tick serves nobody, writes nothing and bills nothing."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id

    state = _declared(client, env["headers"]).json()
    assert state["enabled"] is False
    assert state["skill_units"] == []
    assert state["consent_id"] is None
    assert state["purpose"] == de.DAILY_ESTIMATE_PURPOSE

    outcome = _tick(window=FRESH)
    assert outcome["skipped"] == "no_accounts_served"
    assert outcome["jobs"] == []
    assert _jobs(owner_id) == []
    assert _estimates(owner_id) == []

    # The same answer for the manual door: pressing run cannot create a scope.
    refused = _run_now(client, env["headers"], env["portfolio"].id)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "no_skills_enabled"
    assert _jobs(owner_id) == []


# ---------------------------------------------------------------------------
# 2 — the opt-in is an owner act, and withdrawing it is not
# ---------------------------------------------------------------------------


def test_only_a_password_session_can_open_the_daily_clock(client, personal_env):
    """A grant needs ``confirm`` and a human credential; a withdrawal needs neither."""
    env = _open_env(client, personal_env)
    skill = str(env["portfolio"].id)

    unconfirmed = _grant(client, env["headers"], [{"skill_unit_id": skill,
                                                  "instrument_ids": [TEST_INSTRUMENT]}],
                         confirm=False)
    assert unconfirmed.status_code == 400, unconfirmed.text
    assert _declared(client, env["headers"]).json()["enabled"] is False

    # An API key is a program's credential; it may not spend the owner's quota
    # on the owner's daily clock by the owner's authority.
    db = env["db"]
    created = client.post(
        "/api/v1/api-keys",
        headers=env["headers"],
        json={"name": "agent", "scopes": ["read", "write", "inference"]},
    )
    assert created.status_code == 201, created.text
    agent = {"Authorization": f"Bearer {created.json()['raw_key']}"}

    as_agent = client.put(
        f"{BASE}/consent",
        headers=agent,
        json={"skill_units": [{"skill_unit_id": skill,
                              "instrument_ids": [TEST_INSTRUMENT]}],
              "confirm": True},
    )
    assert as_agent.status_code == 403, as_agent.text
    assert _declared(client, env["headers"]).json()["enabled"] is False

    # Now the owner opts in, and the agent is allowed to pull the brake.
    assert _declare(env)["consent"]["enabled"] is True
    withdrawn = client.delete(f"{BASE}/consent", headers=agent)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["consent"]["enabled"] is False
    assert withdrawn.json()["consent"]["skill_units"] == []


# ---------------------------------------------------------------------------
# 3 — the scope is the owner's, read back as written
# ---------------------------------------------------------------------------


def test_the_scope_reads_back_exactly_what_was_named(client, personal_env):
    """Two skills, their instruments and their ceilings — and a regrant replaces."""
    env = _open_env(client, personal_env)
    first = env["portfolio"]
    second = _second_skill(env)

    body = _declare(
        env,
        instruments=[TEST_INSTRUMENT, "TEST-SECOND-ASSET"],
        max_age=5,
        skill=first.id,
    )
    units = [{u["skill_unit_id"]: u for u in body["consent"]["skill_units"]}]
    assert len(units) == 1
    named = units[0][str(first.id)]
    assert named["instrument_ids"] == [TEST_INSTRUMENT, "TEST-SECOND-ASSET"]
    assert named["max_data_age_days"] == 5
    assert body["consent"]["enabled"] is True
    assert body["consent"]["consent_version"] == de.DAILY_ESTIMATE_CONSENT_VERSION
    assert {s["skill_unit_id"] for s in body["skills"]} == {str(first.id)}
    assert body["skills"][0]["enabled"] is True

    added = _grant(
        client,
        env["headers"],
        [
            {"skill_unit_id": str(first.id), "instrument_ids": [TEST_INSTRUMENT]},
            {"skill_unit_id": str(second.id), "instrument_ids": [TEST_INSTRUMENT]},
        ],
    )
    assert added.status_code == 200, added.text
    declared = {u["skill_unit_id"] for u in added.json()["consent"]["skill_units"]}
    assert declared == {str(first.id), str(second.id)}
    # Regrant replaces: the ceiling the owner dropped is gone, not remembered.
    ceilings = {
        u["skill_unit_id"]: u["max_data_age_days"]
        for u in added.json()["consent"]["skill_units"]
    }
    assert ceilings[str(first.id)] is None

    # The limits are the platform's, and they are published with the scope.
    assert added.json()["consent"]["limits"] == {
        "skill_units_per_owner": de.MAX_SKILL_UNITS_PER_OWNER,
        "instruments_per_skill": de.MAX_INSTRUMENTS_PER_SKILL,
    }
    db = TestSession()
    try:
        rows = (
            db.query(PersonalizationConsent)
            .filter(
                PersonalizationConsent.owner_id == env["user"].id,
                PersonalizationConsent.purpose == de.DAILY_ESTIMATE_PURPOSE,
            )
            .all()
        )
        # Every superseded scope stays readable as history, closed at its time.
        assert len(rows) == 2
        assert sum(1 for row in rows if row.revoked_at is None) == 1
    finally:
        db.close()


def test_a_scope_naming_nobody_elses_skills_writes_nothing(client, personal_env):
    """Another account's skill id answers as "not yours" and the row is not written."""
    env = _open_env(client, personal_env)
    stranger = uuid.uuid4()

    refused = _grant(
        client,
        env["headers"],
        [{"skill_unit_id": str(stranger), "instrument_ids": [TEST_INSTRUMENT]}],
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "skill_unit_not_found"
    assert refused.json()["detail"]["message_zh"]
    assert _declared(client, env["headers"]).json()["enabled"] is False


# ---------------------------------------------------------------------------
# 4 — three consents, three rows
# ---------------------------------------------------------------------------


def test_training_consent_and_daily_consent_are_different_acts(client, personal_env):
    """Granting one does not grant the other; revoking one does not revoke the other."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    db = env["db"]
    experiment = Experiment(owner_id=owner_id, title="Training consent target")
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        strategy_spec={"rule_id": "test-rule"},
        created_by=owner_id,
    )
    db.add(version)
    db.commit()

    training = client.put(
        "/api/v1/ml/personalization/consent",
        headers=env["headers"],
        json={"strategy_version_ids": [str(version.id)], "confirm": True},
    )
    assert training.status_code == 200, training.text

    # The training consent bought the clock nothing: the scope is its own row.
    assert _declared(client, env["headers"]).json()["enabled"] is False
    outcome = _tick(window=FRESH)
    assert outcome["skipped"] == "no_accounts_served"
    assert _jobs(owner_id) == []

    _declare(env)
    withdrawn = client.delete(f"{BASE}/consent", headers=env["headers"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["training_consent_untouched"] is True

    db.expire_all()
    rows = (
        db.query(PersonalizationConsent)
        .filter(PersonalizationConsent.owner_id == owner_id)
        .all()
    )
    by_purpose = {}
    for row in rows:
        by_purpose.setdefault(row.purpose, []).append(row)
    assert set(by_purpose) == {de.DAILY_ESTIMATE_PURPOSE, "personalization_training"}
    live = [row for row in by_purpose["personalization_training"] if row.revoked_at is None]
    assert len(live) == 1
    assert [str(v) for v in live[0].scope_json["strategy_version_ids"]] == [str(version.id)]
    assert not [row for row in by_purpose[de.DAILY_ESTIMATE_PURPOSE] if row.revoked_at is None]


# ---------------------------------------------------------------------------
# 5 — one tick, one estimate, through the gate
# ---------------------------------------------------------------------------


def test_one_tick_estimates_through_the_gate_and_the_owner_can_read_it(
    client, personal_env
):
    """The estimate is a ``usage_records`` row the gate wrote, named by the status read."""
    env = _open_env(client, personal_env)
    owner_id, portfolio, version = env["user"].id, env["portfolio"], env["version"]
    db = TestSession()
    try:
        db.add(
            MarketDataMetadata(
                instrument_id=TEST_INSTRUMENT,
                provider_id="demo",
                data_nature="synthetic",
            )
        )
        db.commit()
    finally:
        db.close()

    _declare(env, max_age=5)
    outcome = _tick(window=FRESH)
    assert outcome["skipped"] is None
    assert outcome["jobs"] == [
        {
            "job_id": outcome["jobs"][0]["job_id"],
            "user_id": str(owner_id),
            "status": "completed",
            "estimated": 1,
            "refused": 0,
            # Pinned as a shape, not a selection: a tick that priced everything it
            # was asked still has to answer the third count with a zero, because
            # "refused: 0" alone does not distinguish a clean day from a dead one.
            "failed": 0,
        }
    ]

    job = _last_job(owner_id)
    assert job["status"] == "completed"
    assert job["config"]["purpose"] == de.DAILY_ESTIMATE_PURPOSE
    assert job["config"]["scope"] == "daily"
    assert job["config"]["window"] == FRESH
    assert job["config"]["kill_switch_scope"] == KillSwitchScope.inference.value
    # The shared tick's once-a-day guard keys on ``scheduled_for`` per owner. This
    # lane passes None and dedups per owner *and* skill off ``window``, so a
    # second skill declared the same day is served, not skipped as "already ran".
    assert "scheduled_for" not in job["config"]
    result = job["result"]
    assert result["estimated"] == 1 and result["refused"] == [] and result["errors"] == []

    estimates = _served(owner_id)
    assert len(estimates) == 1
    metadata = estimates[0]["metadata"]
    written = result["results"][0]
    assert written["inference_id"] == str(estimates[0]["id"])
    assert estimates[0]["endpoint"] == "/api/v1/ml/personal/models/{version_id}/predict"
    for key, value in (
        ("via", "daily_service"),
        ("model_version_id", str(version.id)),
        ("skill_unit_id", str(portfolio.id)),
        ("instrument_id", TEST_INSTRUMENT),
        ("as_of_date", FRESH),
        ("feature_bar_date", "2024-01-30"),
        ("artifact_sha256", env["artifact_sha256"]),
        ("prediction_type", "personal_decision"),
    ):
        assert metadata[key] == value, key
    assert written["provider_id"] == "demo"
    assert written["data_nature"] == "synthetic"
    assert written["data_age_days"] == 1
    assert written["max_data_age_days"] == 5
    assert written["predicted_action"] in written["classes"]
    assert written["not_for"] == list(de.NOT_FOR_ZH)
    assert written["disclaimer_zh"]

    state = _status(client, env["headers"], portfolio.id).json()
    assert state["declared"] is True and state["enabled"] is True
    assert state["disabled_reason"] is None
    assert state["instrument_ids"] == [TEST_INSTRUMENT]
    assert state["max_data_age_days"] == 5
    assert state["current_model_version_id"] == str(version.id)
    assert state["last_run"]["status"] == "completed"
    assert state["last_run"]["window"] == FRESH
    assert state["last_run"]["scope"] == "daily"
    assert state["last_estimate"]["inference_id"] == str(estimates[0]["id"])
    assert state["estimate_matches_current_selection"] is True
    assert state["not_for"] == list(de.NOT_FOR_ZH)

    # The whole list answers for every account the owner holds.
    every = _status(client, env["headers"]).json()["skills"]
    assert {s["skill_unit_id"] for s in every} == {str(portfolio.id)}


# ---------------------------------------------------------------------------
# 6 — the window is the dedup key, and the ledger is the memory
# ---------------------------------------------------------------------------


def test_a_second_tick_in_the_same_window_bills_nothing(client, personal_env):
    """A retry, a restart or a second worker reads the dedup answer off the ledger."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a = env["portfolio"].id
    _declare(env)

    assert _tick(window=FRESH)["skipped"] is None
    again = _tick(window=FRESH)
    assert again["skipped"] is None  # the tick ran; the skill inside it was refused
    job = _last_job(owner_id)
    assert job["config"]["window"] == FRESH
    assert job["status"] == "partial"
    assert job["result"]["estimated"] == 0
    assert [entry["reason"] for entry in job["result"]["refused"]] == [
        "already_estimated_this_window"
    ]
    assert job["result"]["refused"][0]["skill_unit_id"] == str(skill_a)

    # One day, one bill: the second tick spent no inference and no reservation.
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("reserved") == 0

    # The dedup key names the skill, not the owner: an account the owner adds
    # mid-window is served by the same tick that refuses the first one. That is
    # what the lane buying out of the shared per-owner guard means in practice.
    skill_b = _second_skill(env).id
    granted = _grant(
        client,
        env["headers"],
        [
            {"skill_unit_id": str(skill_a), "instrument_ids": [TEST_INSTRUMENT]},
            {"skill_unit_id": str(skill_b), "instrument_ids": [TEST_INSTRUMENT]},
        ],
    )
    assert granted.status_code == 200, granted.text

    assert _tick(window=FRESH)["skipped"] is None
    job = _last_job(owner_id)
    assert job["result"]["estimated"] == 1
    assert [entry["reason"] for entry in job["result"]["refused"]] == [
        "already_estimated_this_window"
    ]
    assert job["result"]["refused"][0]["skill_unit_id"] == str(skill_a)
    assert [e["skill_unit_id"] for e in job["result"]["results"]] == [str(skill_b)]
    assert len(_served(owner_id)) == 2

    # A new window is a new day, and both accounts are estimated in it.
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    assert len(_served(owner_id)) == 4


def test_a_tick_that_dies_mid_run_does_not_bill_the_estimate_it_had_paid_for(
    client, personal_env, monkeypatch
):
    """The bill, not the summary, is what a restart must read. #209, reproduced and fixed.

    Reproduced here on 2026-11-18 and asserted as the defect until it was fixed:
    the row opened before the first estimate was the only durable trace of the
    run, the dedup read ``result_json``, and that is written only when the tick
    closes the row — so a tick killed after a *committed* estimate re-priced that
    same skill and billed the window twice (the old assertion read ``3`` served
    rows for a two-skill scope, one of them bought twice).

    What the lane does now, measured in this node: the estimate that finished is
    durable in ``usage_records``, the retry reads that ledger, the paid skill is
    refused by name, the skill that never started is served, and the summary the
    dead run never wrote is rebuilt from the bill. The two skills are used
    because one estimate that committed itself is exactly where the two
    boundaries differ.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)
    real = de._estimate_one
    priced = []

    class KilledMidRun(BaseException):
        """Not an ``Exception``: the per-estimate guard must not swallow it."""

    def price_a_then_die(db, owner, portfolio, instrument_id, as_of_date, **kw):
        answer = real(db, owner, portfolio, instrument_id, as_of_date, **kw)
        priced.append(str(portfolio.id))
        if str(portfolio.id) == str(skill_a):
            raise KilledMidRun("the worker was stopped here")
        return answer

    monkeypatch.setattr(de, "_estimate_one", price_a_then_die)
    with pytest.raises(KilledMidRun):
        _tick(window=FRESH)

    # Three of the four states, as the dead run left them: the window was
    # claimed, one estimate is persisted and settled, and no summary describes
    # either.
    job = _last_job(owner_id)
    assert job["status"] == "running"
    assert job["config"]["window"] == FRESH
    assert job["result"] == {}
    assert priced == [str(skill_a)]

    paid = _served(owner_id)
    assert len(paid) == 1
    assert _reservations(owner_id).count("reserved") == 0
    assert _reservations(owner_id).count("settled") == 1

    monkeypatch.setattr(de, "_estimate_one", real)
    assert _tick(window=FRESH)["skipped"] is None

    # One window, one bill per skill and instrument: the retry bought the skill
    # the dead run never reached, and did not buy the other one again.
    served = _served(owner_id)
    assert len(served) == 2, [row["metadata"]["skill_unit_id"] for row in served]
    assert [row["metadata"]["skill_unit_id"] for row in served] == [
        str(skill_a),
        str(skill_b),
    ]
    assert _reservations(owner_id).count("settled") == 2
    assert _reservations(owner_id).count("reserved") == 0

    job = _last_job(owner_id)
    assert [entry["skill_unit_id"] for entry in job["result"]["results"]] == [str(skill_b)]
    assert job["result"]["refused"] == [
        {
            "skill_unit_id": str(skill_a),
            "reason": "already_estimated_this_window",
            # The bill said no, not the report: the dead run wrote no summary, so
            # only a lane that reads ``usage_records`` can name this skill - and
            # it owes the owner the id it is refusing to buy again.
            "decided_by": "billing_ledger",
            "inference_ids": [str(served[0]["id"])],
        }
    ]
    # The fourth state: the summary is rebuilt off the billing ledger, so the
    # window is described once even though the run that paid for it died. The
    # carry-over is the dead run's bill; the window's total is read after this
    # run's own estimate committed.
    assert job["result"]["billed_before_this_run"] == 1
    assert job["result"]["billed_in_window"] == 2
    recovered = job["result"]["recovered"]
    assert [entry["skill_unit_id"] for entry in recovered] == [str(skill_a)]
    assert recovered[0]["inference_id"] == str(served[0]["id"])
    assert recovered[0]["instrument_id"] == TEST_INSTRUMENT
    assert recovered[0]["model_version_id"] == str(env["version"].id)
    assert recovered[0]["artifact_sha256"] == env["artifact_sha256"]
    assert recovered[0]["as_of_date"] == FRESH
    # The verdict itself is not in the billing row, and the recovered entry says
    # so instead of inventing one.
    assert recovered[0]["predicted_action"] is None
    assert recovered[0]["verdict_durable"] is False
    assert job["status"] == "partial"

    # The owner's read answers for the skill whose run died: an estimate exists,
    # with the inference id the bill carries, and it still matches the selection.
    state = _status(client, env["headers"], skill_a).json()
    assert state["last_estimate"]["inference_id"] == str(served[0]["id"])
    assert state["last_estimate"]["model_version_id"] == str(env["version"].id)
    assert state["last_run"]["window"] == FRESH
    # What the panel's three-way split reads, since it cannot read an id. A row
    # rebuilt from the bill says it holds no verdict and does not dress itself as
    # a fresh estimate whose data age and source this row never carried; and the
    # refusal beside it is the dedup itself, which is a different fact from "the
    # service withheld this account".
    assert state["last_estimate"]["predicted_action"] is None
    assert state["last_estimate"]["verdict_durable"] is False
    assert "data_age_days" not in state["last_estimate"]
    assert "provider_id" not in state["last_estimate"]
    assert state["last_refusal"]["reason"] == "already_estimated_this_window"
    assert (
        state["last_refusal"]["message_zh"]
        == "此技能帳號今日的估計已存在，未重複執行。"
    )

    # A third tick buys nothing more; a new window buys each skill once.
    assert _tick(window=FRESH)["skipped"] is None
    assert len(_served(owner_id)) == 2
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    assert len(_served(owner_id)) == 4


def test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice(
    client, personal_env, monkeypatch
):
    """Two overlapping ticks, one business key, one bill: the other half of #209.

    :func:`test_a_tick_that_dies_mid_run_does_not_bill_the_estimate_it_had_paid_for`
    proved a *restart* cannot re-price a settled estimate. That fix cannot cover
    the case where the two ticks overlap: the reading a tick takes when it opens is
    a description of the window, not a lock on it, so worker A priced the second
    instrument after worker B had already paid for it and the owner was charged
    twice for one (skill, instrument, as-of).

    The interleaving is reproduced with two independent transactions rather than
    two OS processes, because what is under test here is which read the decision
    follows. The two-process race against a real server is
    ``tests/test_daily_estimate_two_workers.py``, PostgreSQL-only and NOT RUN by
    this lane.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id

    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()
    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])

    from app.models.user import User

    real = de._estimate_one
    billed_elsewhere: list = []

    def worker_b_prices_the_second_item():
        """Another session, another transaction, the same owner and window."""
        with TestSession() as other:
            owner_row = other.query(User).filter(User.id == owner_id).one()
            unit = other.query(PaperPortfolio).filter(PaperPortfolio.id == skill).one()
            billed_elsewhere.append(
                real(other, owner_row, unit, SECOND_INSTRUMENT, FRESH)["inference_id"]
            )

    def estimate_then_meet_a_competitor(session, owner, portfolio, instrument_id, as_of, **kw):
        answer = real(session, owner, portfolio, instrument_id, as_of, **kw)
        if instrument_id == TEST_INSTRUMENT:
            # A has committed its own bill and is walking to the next instrument;
            # B takes the remaining one while A is between the two.
            worker_b_prices_the_second_item()
        return answer

    monkeypatch.setattr(de, "_estimate_one", estimate_then_meet_a_competitor)
    assert _tick(window=FRESH)["skipped"] is None

    # One bill per business key, and the second instrument's row is worker B's:
    # A found it paid and stepped back instead of buying it again.
    served = _served(owner_id)
    assert len(billed_elsewhere) == 1
    assert [
        str(row["id"])
        for row in served
        if row["metadata"]["instrument_id"] == SECOND_INSTRUMENT
    ] == billed_elsewhere
    assert sorted(row["metadata"]["instrument_id"] for row in served) == sorted(
        [TEST_INSTRUMENT, SECOND_INSTRUMENT]
    )
    assert _reservations(owner_id).count("settled") == 2
    assert _reservations(owner_id).count("released") == 0
    assert _reservations(owner_id).count("reserved") == 0

    job = _last_job(owner_id)
    assert [entry["instrument_id"] for entry in job["result"]["results"]] == [
        TEST_INSTRUMENT
    ]
    assert job["result"]["refused"] == [
        {
            "skill_unit_id": str(skill),
            "instrument_id": SECOND_INSTRUMENT,
            "reason": "already_estimated_this_window",
            "decided_by": "billing_ledger",
            # Competitor B's row, read inside A's own transaction after the seat:
            # the refusal names the bill it stepped back from rather than
            # reporting a duplicate it had just written.
            "inference_id": billed_elsewhere[0],
        }
    ]
    # A opened on a window with nothing billed and that reading was not wrong, it
    # was just old by the time the item came round. The number that keeps the
    # window's account true is the re-read at the close, not the carry-over.
    assert job["result"]["billed_before_this_run"] == 0
    assert job["result"]["billed_in_window"] == 2

    # And the next tick for this window buys nothing further.
    monkeypatch.setattr(de, "_estimate_one", real)
    assert _tick(window=FRESH)["skipped"] is None
    assert len(_served(owner_id)) == 2


def test_the_window_state_separates_the_claim_from_the_bill_and_the_summary(
    client, personal_env, monkeypatch
):
    """Four questions, four fields, none of them inferred from the others.

    ``window_state`` is what an operator asks when a tick died: whether the
    window was claimed, whether the estimate is durable, whether it was paid
    for, and whether any run ever described it. The three states the lane can
    really be in are shown apart here — claimed-but-nothing-billed, billed-and-
    described, and billed-with-no-description.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)

    # Nothing has run: no claim on the window, and both skills are owed.
    state = _window_state(owner_id, FRESH)
    assert state["execution_claimed"] is False
    assert state["estimates_billed"] == 0
    assert {row["skill_unit_id"]: row["state"] for row in state["skills"]} == {
        str(skill_a): "not_started",
        str(skill_b): "not_started",
    }
    assert {tuple(row["retry_owes"]) for row in state["skills"]} == {(TEST_INSTRUMENT,)}

    # Billed and described: the ordinary tick answers ``settled``, with the
    # inference id and the version it ran on.
    assert _tick(window=FRESH)["skipped"] is None
    state = _window_state(owner_id, FRESH)
    assert state["execution_claimed"] is True
    assert state["estimates_billed"] == 2
    assert {row["state"] for row in state["skills"]} == {"settled"}
    assert {row["summary_written"] for row in state["skills"]} == {True}
    assert {len(row["inference_ids"]) for row in state["skills"]} == {1}
    assert {tuple(row["instruments_outstanding"]) for row in state["skills"]} == {()}

    # Billed with no description: the estimate commits inside the gate and the
    # summary is written afterwards, so killing the run between the two leaves
    # the bill and drops the report. The state still says ``settled`` — the
    # window's account comes from the ledger, and only ``summary_written`` moves.
    real = de._estimate_one

    class KilledBeforeSummary(BaseException):
        pass

    def price_then_die_before_closing(db, owner, portfolio, instrument_id, as_of_date, **kw):
        answer = real(db, owner, portfolio, instrument_id, as_of_date, **kw)
        if str(portfolio.id) == str(skill_a):
            raise KilledBeforeSummary("stopped after the bill, before the report")
        return answer

    monkeypatch.setattr(de, "_estimate_one", price_then_die_before_closing)
    with pytest.raises(KilledBeforeSummary):
        _tick(window=NEXT_WINDOW)
    state = _window_state(owner_id, NEXT_WINDOW)
    assert state["execution_claimed"] is True
    assert state["estimates_billed"] == 1
    by_skill = {row["skill_unit_id"]: row for row in state["skills"]}
    assert by_skill[str(skill_a)]["state"] == "settled"
    assert by_skill[str(skill_a)]["estimate_persisted"] is True
    assert by_skill[str(skill_a)]["quota_settled"] is True
    assert by_skill[str(skill_a)]["summary_written"] is False
    assert by_skill[str(skill_b)]["state"] == "not_started"
    assert by_skill[str(skill_b)]["retry_owes"] == [TEST_INSTRUMENT]

    monkeypatch.setattr(de, "_estimate_one", real)
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    state = _window_state(owner_id, NEXT_WINDOW)
    assert {row["state"] for row in state["skills"]} == {"settled"}
    assert {row["summary_written"] for row in state["skills"]} == {True}
    # Two windows, two bills each: the killed run paid for one skill of the
    # second window and its retry paid for the other, so four estimates and no
    # fifth.
    assert len(_served(owner_id)) == 4


def test_a_refusal_that_spent_nothing_is_that_windows_record_not_a_second_attempt(
    client, personal_env, monkeypatch
):
    """One instrument paid, the next broken: the bill is not doubled and the
    failed instrument is not retried inside the window it was refused in.

    The failure is raised at the inference call itself, so the reservation is
    made and the gate's own release path runs: nothing is deducted for work the
    engine did not finish. The retry contract this pins is the one the lane has
    always had — one attempt per instrument per window, refusals included,
    because the lane cannot tell whether a failure happened before or after the
    unit was spent — and the next window is where the instrument is served.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    # A second instrument with bars of its own, so the scope really has two
    # instruments to price and the failure lands on a servable one.
    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()
    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])

    from app.api import personal_models as pm

    real = de._estimate_one
    real_predict = pm.predict_personal

    def engine_dead(artifact_id, features, owner_id):
        raise RuntimeError("the inference engine died")

    def estimate(db, owner, portfolio, instrument_id, as_of_date, **kw):
        if instrument_id == SECOND_INSTRUMENT:
            with patch.object(pm, "predict_personal", side_effect=engine_dead):
                return real(db, owner, portfolio, instrument_id, as_of_date, **kw)
        return real(db, owner, portfolio, instrument_id, as_of_date, **kw)

    monkeypatch.setattr(de, "_estimate_one", estimate)
    assert _tick(window=FRESH)["skipped"] is None

    # The paid instrument is the only deduction; the broken one released its
    # reservation and wrote no billing row.
    served = _served(owner_id)
    assert [row["metadata"]["instrument_id"] for row in served] == [TEST_INSTRUMENT]
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("released") == 1
    assert _reservations(owner_id).count("reserved") == 0

    job = _last_job(owner_id)
    assert [entry["reason"] for entry in job["result"]["refused"]] == ["prediction_failed"]
    assert job["result"]["refused"][0]["instrument_id"] == SECOND_INSTRUMENT
    assert job["result"]["refused"][0]["http_status"] == 422
    # This run opened on an unbilled window, so it recovered nothing; the one
    # instrument it paid for is the whole of the window's bill.
    assert job["result"]["billed_before_this_run"] == 0
    assert job["result"]["recovered"] == []
    assert job["result"]["billed_in_window"] == 1

    state = _window_state(owner_id, FRESH)
    row = state["skills"][0]
    assert row["instruments_settled"] == [TEST_INSTRUMENT]
    assert row["instruments_outstanding"] == [SECOND_INSTRUMENT]

    # Inside the same window the refused instrument is not attempted again, so
    # the window stays at one bill.
    monkeypatch.setattr(de, "_estimate_one", real)
    assert _tick(window=FRESH)["skipped"] is None
    assert [row["metadata"]["instrument_id"] for row in _served(owner_id)] == [
        TEST_INSTRUMENT
    ]
    assert _reservations(owner_id).count("settled") == 1

    # The next window is where it gets served: both instruments, once each. The
    # first window keeps the one bill it made and never buys the second
    # instrument, so three deductions cover the two windows and none is a
    # retry of a paid instrument.
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    assert sorted(row["metadata"]["instrument_id"] for row in _served(owner_id)) == sorted(
        [TEST_INSTRUMENT, TEST_INSTRUMENT, SECOND_INSTRUMENT]
    )
    assert _reservations(owner_id).count("settled") == 3
    assert _reservations(owner_id).count("released") == 1


def test_an_error_that_named_nothing_is_attempted_again_in_the_same_window(
    client, personal_env, monkeypatch
):
    """What a retry owes when the failure left neither a bill nor a refusal.

    :func:`test_a_refusal_that_spent_nothing_is_that_windows_record_not_a_second_attempt`
    pins one half of the answer: a *refusal* is that window's record, and the
    instrument is served in the next window. This node pins the other half, which
    is not the same. An exception that is neither a gate refusal nor a bill lands
    in ``errors``, and ``errors`` is read by neither dedup half —
    :func:`app.services.daily_estimates.settled_estimates` sees no billing row and
    :func:`app.services.daily_estimates._summary_recorded_skills` reads only
    ``results``, ``refused`` and ``recovered``. So the next tick in the *same*
    window attempts it again, and the window is only closed when something was
    actually named.

    The asymmetry is part of the contract and is stated here rather than
    smoothed over: the summary gate works per skill account, so an instrument
    that errored while its sibling was billed is refused with the rest of the
    skill, while the same exception on a skill that named nothing at all is
    retried. ``state`` reports the second case as ``not_started``, which is the
    truth about what a retry owes and not a claim that no tick ever ran —
    ``execution_claimed`` is the field that answers that, and it is true here.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    _declare(env, instruments=[TEST_INSTRUMENT])

    real = de._estimate_one
    attempts: list = []

    def broken_twice(session, owner, portfolio, instrument_id, as_of, **kw):
        attempts.append(instrument_id)
        if len(attempts) <= 2:
            raise RuntimeError("the worker died between the gate and the ledger")
        return real(session, owner, portfolio, instrument_id, as_of, **kw)

    monkeypatch.setattr(de, "_estimate_one", broken_twice)
    assert _tick(window=FRESH)["skipped"] is None

    # Nothing was spent, because nothing reached the gate.
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []
    job = _last_job(owner_id)
    assert job["status"] == "failed"
    assert job["result"]["results"] == []
    assert job["result"]["refused"] == []
    assert len(job["result"]["errors"]) == 1, job["result"]["errors"]
    assert job["result"]["billed_before_this_run"] == 0
    assert job["result"]["billed_in_window"] == 0

    state = _window_state(owner_id, FRESH, skill=skill)
    assert state["state"] == "not_started"
    assert state["summary_written"] is False
    assert state["retry_owes"] == [TEST_INSTRUMENT]
    # The window does carry the tick's own record — a row was opened and closed —
    # so ``not_started`` is a statement about what is owed, not about whether a
    # run happened.
    assert _window_state(owner_id, FRESH)["execution_claimed"] is True

    # A second tick in the same window attempts it again, and still bills nothing.
    assert _tick(window=FRESH)["skipped"] is None
    assert attempts == [TEST_INSTRUMENT, TEST_INSTRUMENT]
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []

    # Once the failure is gone the same window serves it exactly once — the retry
    # is not a second charge, because there was never a first one.
    monkeypatch.setattr(de, "_estimate_one", real)
    assert _tick(window=FRESH)["skipped"] is None
    assert [row["metadata"]["instrument_id"] for row in _served(owner_id)] == [
        TEST_INSTRUMENT
    ]
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0
    assert _last_job(owner_id)["result"]["billed_in_window"] == 1

    served_state = _window_state(owner_id, FRESH, skill=skill)
    assert served_state["state"] == "settled"
    assert served_state["summary_written"] is True
    assert served_state["retry_owes"] == []

    # And now the window is closed: a fourth tick buys nothing.
    assert _tick(window=FRESH)["skipped"] is None
    assert len(_served(owner_id)) == 1


def test_a_lost_summary_cannot_buy_a_window_twice(client, personal_env):
    """Delete the report and the answer to "was this day billed" is unchanged.

    The summary is a description of the window, so it is the wrong thing to
    trust as its ledger. This node takes a completed, billed tick and wipes the
    row's ``result_json`` — the durable state a run leaves when it commits its
    estimates and dies before describing them — and asks the same question again.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    _declare(env)

    assert _tick(window=FRESH)["skipped"] is None
    served = _served(owner_id)
    assert len(served) == 1

    db = TestSession()
    try:
        row = (
            db.query(LearningJob)
            .filter(
                LearningJob.user_id == owner_id,
                LearningJob.job_type == de.DAILY_ESTIMATE_JOB_TYPE,
            )
            .order_by(LearningJob.created_at.desc())
            .first()
        )
        assert row is not None
        row.status = "running"
        row.result_json = {}
        db.commit()
    finally:
        db.close()

    state = _window_state(owner_id, FRESH)
    assert state["execution_claimed"] is True
    assert state["estimates_billed"] == 1
    assert state["skills"][0]["state"] == "settled"
    assert state["skills"][0]["summary_written"] is False

    assert _tick(window=FRESH)["skipped"] is None
    assert len(_served(owner_id)) == 1  # the bill still says it was paid
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0
    job = _last_job(owner_id)
    assert job["result"]["refused"] == [
        {
            "skill_unit_id": str(skill),
            "reason": "already_estimated_this_window",
            "decided_by": "billing_ledger",
            "inference_ids": [str(served[0]["id"])],
        }
    ]
    assert [entry["inference_id"] for entry in job["result"]["recovered"]] == [
        str(served[0]["id"])
    ]


def test_a_billed_window_with_a_lost_summary_still_names_its_estimate(
    client, personal_env
):
    """What the owner's own screen reads when the report is gone but the bill is not.

    ``test_a_lost_summary_cannot_buy_a_window_twice`` proves the dedup survives a
    wiped ``result_json``. This proves the *display* does: the status route read
    only run summaries, so a window that was billed and never described came back
    as no estimate at all — the card telling the owner 「沒有估計」 about a day the
    ledger says was paid for, which is the false absence a customer reads as
    permission to buy it again.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    assert _tick(window=FRESH)["skipped"] is None
    paid = _served(owner_id)
    assert len(paid) == 1

    db = TestSession()
    try:
        row = (
            db.query(LearningJob)
            .filter(
                LearningJob.user_id == owner_id,
                LearningJob.job_type == de.DAILY_ESTIMATE_JOB_TYPE,
            )
            .order_by(LearningJob.created_at.desc())
            .first()
        )
        row.status = "running"
        row.result_json = {}
        db.commit()
    finally:
        db.close()

    state = _status(client, env["headers"], env["portfolio"].id).json()
    # The row is there, named by its billing id, and its verdict is stated as
    # absent rather than dressed up as a prediction: no summary ever saved one.
    assert state["last_run"] is None  # nothing describes this window any more
    assert state["last_estimate"]["inference_id"] == str(paid[0]["id"])
    assert state["last_estimate"]["verdict_durable"] is False
    assert state["last_estimate"]["predicted_action"] is None
    assert state["last_estimate"]["as_of_date"] == FRESH
    assert state["last_refusal"] is None
    # The window the card describes is the one the claim row opened, and the
    # claim is all the card may say: it is not evidence of a live process.
    assert state["window"] == FRESH
    assert state["window_claimed"] is True

    # Reading the card is not an estimate. The customer screen is opened to
    # decide whether to press, so a status read that billed would charge the
    # owner for looking.
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0


def test_a_run_that_opened_a_window_and_said_nothing_is_unconfirmed(
    client, personal_env, monkeypatch
):
    """A claim row with no bill and no report reads as unconfirmed, never as running.

    The tick opened its row and was stopped before it priced anything, so the
    lane holds one fact — something started — and no fact about the outcome. The
    card may not fill that gap with 「執行中」 (that needs a lease or heartbeat this
    lane does not write) nor with 「已逾時」 (that would be judging a process by the
    age of its row or the word in its status column), so the route hands back the
    claim and nothing else and lets the screen say 執行狀態未確認.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    class KilledBeforePricing(BaseException):
        """Not an ``Exception``: the per-estimate guard must not swallow it."""

    def die_before_pricing(db, owner, portfolio, instrument_id, as_of_date, **kw):
        raise KilledBeforePricing("the worker was stopped here")

    monkeypatch.setattr(de, "_estimate_one", die_before_pricing)
    with pytest.raises(KilledBeforePricing):
        _tick(window=FRESH)
    monkeypatch.undo()

    job = _last_job(owner_id)
    assert job["status"] == "running"
    assert job["config"]["window"] == FRESH
    assert job["result"] == {}
    assert _served(owner_id) == []

    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["window_claimed"] is True
    assert state["window"] == FRESH
    assert state["last_estimate"] is None
    assert state["last_refusal"] is None
    # The status column says ``running``; the read says nothing of the kind.
    # Nothing here releases a reservation, refunds a unit or rewrites a terminal.
    assert state["last_run"] is None
    assert _reservations(owner_id).count("reserved") == 0
    assert _reservations(owner_id).count("settled") == 0


def test_a_claim_row_that_outlived_its_worker_is_retried_without_erasing_it(
    client, personal_env, monkeypatch
):
    """The crash exactly at the claim commit, and what a retry may not do to it.

    ``_open_job`` commits the claim on its own (``app/workers/learning_scheduler.py``:456)
    and the first thing after that commit is the ledger read that decides what
    still needs pricing, so a worker stopped here leaves the same three durable
    facts as one stopped a line later: window claimed, nothing billed, nothing
    reported. What this node pins that the one above cannot is the retry's side
    of the deal — the dead row is the historical ``running`` row the brief says
    must not be turned into evidence of anything. Two things follow, and both are
    asserted: the retry pays for the window as a first charge, and it leaves the
    dead row's status and empty summary exactly as the crashed run wrote them.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    from app.workers import learning_scheduler as ls

    real_open = ls._open_job
    opened: list = []

    class KilledAtTheClaim(BaseException):
        """Not an ``Exception``: the per-estimate guard must not swallow it."""

    def open_row_then_die(db, owner_id, **kw):
        job = real_open(db, owner_id, **kw)
        opened.append(str(job.id))
        raise KilledAtTheClaim("the worker was stopped at the claim boundary")

    monkeypatch.setattr(ls, "_open_job", open_row_then_die)
    with pytest.raises(KilledAtTheClaim):
        _tick(window=FRESH)
    monkeypatch.undo()

    assert len(opened) == 1
    dead = _jobs(owner_id)
    assert [str(row["id"]) for row in dead] == opened, "the crashed tick wrote more than one row"
    assert dead[0]["status"] == "running"
    assert dead[0]["config"]["window"] == FRESH
    assert dead[0]["result"] == {}
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []

    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["window_claimed"] is True
    assert state["window"] == FRESH
    assert (state["last_estimate"], state["last_refusal"], state["last_run"]) == (
        None,
        None,
        None,
    )

    # The retry is a first charge, not a second one: nothing had been billed
    # before it, so the window's one bill is its own.
    assert _tick(window=FRESH)["skipped"] is None
    served = _served(owner_id)
    assert len(served) == 1
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0
    retry = _last_job(owner_id)
    assert retry["result"]["billed_before_this_run"] == 0
    assert retry["result"]["billed_in_window"] == 1
    assert [entry["skill_unit_id"] for entry in retry["result"]["results"]] == [
        str(env["portfolio"].id)
    ]

    # And the historical row stays what the crashed run left it: the retry opens
    # its own row rather than adopting the dead one, closing it, or filling in a
    # report it never wrote.
    after = _jobs(owner_id)
    assert len(after) == 2
    assert after[0]["status"] == "running"
    assert after[0]["result"] == {}
    assert str(after[0]["id"]) == opened[0]

    read = _status(client, env["headers"], env["portfolio"].id).json()
    assert read["last_estimate"]["inference_id"] == str(served[0]["id"])
    assert read["last_estimate"]["predicted_action"] is not None
    assert read["window_claimed"] is True


# ---------------------------------------------------------------------------
# 7 — a model switch moves the future, not the history
# ---------------------------------------------------------------------------


def test_a_model_switch_cannot_rebill_the_window_it_already_estimated(
    client, personal_env
):
    """After the switch the stored row still names the version that ran."""
    env = _open_env(client, personal_env)
    owner_id, portfolio = env["user"].id, env["portfolio"]
    _declare(env)
    _tick(window=FRESH)
    first = _served(owner_id)[0]

    newer = add_trained_version(env)
    _select(env["db"], owner_id, newer, env["record"], portfolio.id)
    env["db"].commit()

    # The switch does not open the window again.
    assert _tick(window=FRESH)["skipped"] is None
    assert [row["id"] for row in _served(owner_id)] == [first["id"]]

    state = _status(client, env["headers"], portfolio.id).json()
    assert state["last_estimate"]["model_version_id"] == str(env["version"].id)
    assert state["current_model_version_id"] == str(newer.id)
    assert state["estimate_matches_current_selection"] is False
    assert state["enabled"] is True

    # Two facts from two reads: the day that produced nothing says why, and the
    # estimate it refused to repeat is still the owner's last one. A status read
    # that took both from the newest row alone would answer "never estimated"
    # here, which is how a refused day used to erase the panel's estimate.
    assert state["last_run"]["window"] == FRESH
    assert state["last_run"]["status"] == "partial"
    assert state["last_refusal"]["reason"] == "already_estimated_this_window"
    assert state["last_estimate"]["as_of_date"] == FRESH

    # The next window runs on the version the owner selected, and the old row
    # still names the one that actually ran.
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    served = _served(owner_id)
    assert [row["metadata"]["model_version_id"] for row in served] == [
        str(env["version"].id),
        str(newer.id),
    ]
    after = _status(client, env["headers"], portfolio.id).json()
    assert after["estimate_matches_current_selection"] is True
    assert after["last_run"]["window"] == NEXT_WINDOW
    assert after["last_refusal"] is None
    assert after["last_estimate"]["as_of_date"] == NEXT_WINDOW


def test_a_switch_before_the_first_estimate_prices_the_version_named_now(
    client, personal_env
):
    """What the consent binds, measured on the switch nobody has estimated on yet.

    ``test_a_model_switch_cannot_rebill_the_window_it_already_estimated`` answers
    the switch *after* a row exists. This is the other half: the owner opts in
    while version A is selected, nothing runs, and the owner then selects B. The
    scope row names a skill account, its instruments and a freshness ceiling — it
    carries no version and no artifact — so the only version this lane may price
    is the one the live selection names, and the row it writes says so.
    """
    env = _open_env(client, personal_env)
    owner_id, portfolio = env["user"].id, env["portfolio"]
    declared = _declare(env)
    first_version = env["version"]

    # The declared scope, read back: three facts, none of them a model version.
    entry = declared["consent"]["skill_units"][0]
    assert sorted(entry) == ["instrument_ids", "max_data_age_days", "skill_unit_id"]
    assert _served(owner_id) == []

    newer = add_trained_version(env)
    _select(env["db"], owner_id, newer, env["record"], portfolio.id)
    env["db"].commit()

    assert _tick(window=FRESH)["skipped"] is None
    served = _served(owner_id)
    assert len(served) == 1, "the switch must not cost a second estimate"
    stamped = served[0]["metadata"]
    # Priced on the model the owner selected last, with that version's own
    # artifact — not the one in force when the consent was written.
    assert stamped["model_version_id"] == str(newer.id)
    assert stamped["model_version_id"] != str(first_version.id)
    assert stamped["artifact_sha256"] == newer.model_specification_json["artifact_sha256"]

    state = _status(client, env["headers"], portfolio.id).json()
    assert state["last_estimate"]["model_version_id"] == str(newer.id)
    assert state["current_model_version_id"] == str(newer.id)
    assert state["estimate_matches_current_selection"] is True


# ---------------------------------------------------------------------------
# 8 — the stop controls, and which one this lane obeys
# ---------------------------------------------------------------------------


def test_the_inference_switch_stops_the_lane_and_the_execution_switch_does_not(
    client, personal_env
):
    """``inference`` is what an estimate is; the execution book stays shut anyway."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.execution, "test", "other lane only")
    finally:
        db.close()
    assert _tick(window=FRESH)["skipped"] is None
    assert len(_served(owner_id)) == 1

    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.inference, "test", "owner pulled it")
    finally:
        db.close()

    outcome = _tick(window=NEXT_WINDOW)
    assert outcome["skipped"] == "kill_switch_active"
    assert outcome["jobs"] == []
    assert len(_served(owner_id)) == 1  # nothing added while the switch was up

    refused = _run_now(client, env["headers"], env["portfolio"].id)
    assert refused.status_code == 503, refused.text
    assert "kill switch active for inference" in refused.json()["detail"]

    # And the owner can still read that the service is switched off for them.
    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["enabled"] is True
    assert state["last_estimate"]["model_version_id"] == str(env["version"].id)


def test_a_kill_switch_that_cannot_be_read_stops_the_tick(client, personal_env):
    """Unreadable stop control means do nothing, and the reason says so."""
    env = _open_env(client, personal_env)
    _declare(env)
    with patch.object(ls, "is_kill_switch_active", side_effect=RuntimeError("db down")):
        outcome = _tick(window=FRESH)
    assert outcome["skipped"] == "kill_switch_unreadable"
    assert outcome["jobs"] == []
    assert _served(env["user"].id) == []


def test_the_global_switch_stops_the_estimate_lane_at_both_doors(client, personal_env):
    """The system-wide brake covers this lane, and so does the HTTP one.

    The lane asks for the ``inference`` scope, but the read it goes through
    (``app.services.kill_switch.is_kill_switch_active``) answers True for the
    ``global`` row first. That inheritance is the whole of the global promise, so
    it gets its own node rather than riding on the inference-scope test above.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.global_, "test", "operator stopped everything")
    finally:
        db.close()

    outcome = _tick(window=FRESH)
    assert outcome["skipped"] == "kill_switch_active"
    assert outcome["jobs"] == []
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []

    # The owner's own button meets the same brake at the same door.
    refused = _run_now(client, env["headers"], env["portfolio"].id)
    assert refused.status_code == 503, refused.text
    assert "kill switch active for inference" in refused.json()["detail"]

    # Reading is not spending: the panel still has to answer, and it answers
    # "declared and off", not "never heard of you".
    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["enabled"] is True
    assert state["last_estimate"] is None


def _declare_two(env):
    """Two skills the owner has declared, as a tuple of their ids.

    The mid-run nodes below need a scope of at least two entries: one to price
    and one that has not started when the control is pulled.
    """
    client = env["client"]
    skill_a, skill_b = env["portfolio"].id, _second_skill(env).id
    granted = _grant(
        client,
        env["headers"],
        [
            {"skill_unit_id": str(skill_a), "instrument_ids": [TEST_INSTRUMENT]},
            {"skill_unit_id": str(skill_b), "instrument_ids": [TEST_INSTRUMENT]},
        ],
    )
    assert granted.status_code == 200, granted.text
    return skill_a, skill_b


def test_a_withdrawal_arriving_mid_run_stops_the_skills_not_started_yet(
    client, personal_env, monkeypatch
):
    """The boundary a withdrawal honours is "has not started", named per skill.

    An estimate already in flight cannot be un-spent, so the row records what the
    lane can actually promise: the skill whose first call had not happened is
    refused, by name, in the same ledger row as the estimate that did run.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)
    real = de._estimate_one
    priced = []

    def estimate_then_withdraw(db, owner, portfolio, instrument_id, as_of_date, **kw):
        answer = real(db, owner, portfolio, instrument_id, as_of_date, **kw)
        priced.append(str(portfolio.id))
        if str(portfolio.id) == str(skill_a):
            other = TestSession()
            try:
                de.withdraw_daily_estimate(other, owner, [str(skill_b)])
            finally:
                other.close()
        return answer

    monkeypatch.setattr(de, "_estimate_one", estimate_then_withdraw)
    assert _tick(window=FRESH)["skipped"] is None

    job = _last_job(owner_id)
    assert [entry["skill_unit_id"] for entry in job["result"]["results"]] == [str(skill_a)]
    assert job["result"]["refused"] == [
        {"skill_unit_id": str(skill_b), "reason": "consent_withdrawn"}
    ]
    assert job["status"] == "partial"
    # Only the skill that was still declared was priced; the withdrawn one was
    # never billed, and the read says why.
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("reserved") == 0

    state = _status(client, env["headers"], skill_b).json()
    assert state["enabled"] is False  # no longer inside the declared scope
    assert state["last_refusal"]["reason"] == "consent_withdrawn"
    assert state["last_refusal"]["message_zh"]


def test_a_switch_pulled_mid_run_stops_the_skills_not_started_yet(
    client, personal_env, monkeypatch
):
    """The same brake, arriving while the clock is already walking the list."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)
    real = de._estimate_one
    pulled = []

    def estimate_then_stop(db, owner, portfolio, instrument_id, as_of_date, **kw):
        answer = real(db, owner, portfolio, instrument_id, as_of_date, **kw)
        if str(portfolio.id) == str(skill_a) and not pulled:
            pulled.append(1)  # once, or the next window would be stopped too
            other = TestSession()
            try:
                activate_kill_switch(other, KillSwitchScope.global_, "test", "pulled mid-run")
            finally:
                other.close()
        return answer

    monkeypatch.setattr(de, "_estimate_one", estimate_then_stop)
    assert _tick(window=FRESH)["skipped"] is None

    job = _last_job(owner_id)
    assert [entry["skill_unit_id"] for entry in job["result"]["results"]] == [str(skill_a)]
    assert job["result"]["refused"] == [
        {"skill_unit_id": str(skill_b), "reason": "kill_switch_active"}
    ]
    assert len(_served(owner_id)) == 1

    # Releasing the brake inside the same window does not queue the refused
    # skill up again: the dedup counts refusals as well as estimates, because the
    # refusal is that window's record and a retry storm would bill the day twice.
    # What the owner gets instead is the next window, and this is the sentence
    # that has to be true for that to be the whole story.
    db = TestSession()
    try:
        deactivate_kill_switch(db, KillSwitchScope.global_)
    finally:
        db.close()
    assert _tick(window=FRESH)["skipped"] is None
    assert [
        (entry["skill_unit_id"], entry["reason"])
        for entry in _last_job(owner_id)["result"]["refused"]
    ] == [
        (str(skill_a), "already_estimated_this_window"),
        (str(skill_b), "already_estimated_this_window"),
    ]
    assert len(_served(owner_id)) == 1  # still nothing spent on the stopped skill

    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    assert len(_served(owner_id)) == 3  # both skills, plus the first one's day two
    state = _status(client, env["headers"], skill_b).json()
    assert state["last_estimate"]["as_of_date"] == NEXT_WINDOW


# ---------------------------------------------------------------------------
# 9 — the skill the owner paused, and the quota the owner ran out of
# ---------------------------------------------------------------------------


def test_a_paused_skill_is_refused_by_name_not_skipped_in_silence(
    client, personal_env
):
    """Declared while active, paused afterwards: the read says why nothing ran."""
    env = _open_env(client, personal_env)
    owner_id, portfolio = env["user"].id, env["portfolio"]
    _declare(env)

    db = TestSession()
    try:
        row = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio.id).first()
        row.status = PortfolioStatus.paused
        db.commit()
    finally:
        db.close()

    assert _tick(window=FRESH)["skipped"] is None
    job = _last_job(owner_id)
    assert job["status"] == "partial"
    assert [entry["reason"] for entry in job["result"]["refused"]] == ["skill_paused"]
    assert _served(owner_id) == []

    state = _status(client, env["headers"], portfolio.id).json()
    assert state["enabled"] is False
    assert state["skill_status"] == "paused"
    assert state["disabled_reason"] == "skill_paused"
    assert state["declared"] is True  # the scope still exists, and says it is not served
    assert state["last_refusal"]["reason"] == "skill_paused"
    assert state["last_refusal"]["message_zh"] == "此技能帳號已暫停，每日估計不會替它執行。"


def test_an_exhausted_quota_refuses_before_spending_anything(client, personal_env):
    """429 from the same billing contract, recorded as the reason, costing nothing."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    db = TestSession()
    try:
        db.add(PilotEntitlement(user_id=owner_id, ai_usage_limit=0))
        db.commit()
    finally:
        db.close()

    _declare(env)
    assert _tick(window=FRESH)["skipped"] is None
    job = _last_job(owner_id)
    refused = job["result"]["refused"]
    assert [entry["reason"] for entry in refused] == ["quota_exceeded"]
    assert refused[0]["http_status"] == 429
    assert refused[0]["instrument_id"] == TEST_INSTRUMENT
    assert _served(owner_id) == []
    # A refusal that reserved a unit would be a bill the owner never got work for.
    assert _reservations(owner_id) == []


# ---------------------------------------------------------------------------
# 10 — the freshness ceiling is the owner's declaration
# ---------------------------------------------------------------------------


def test_stale_data_is_refused_before_the_quota_is_spent(client, personal_env):
    """A declared ceiling stops the estimate; no ceiling means the owner asked for none."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id

    _declare(env, max_age=3)
    assert _tick(window=STALE_WINDOW)["skipped"] is None
    job = _last_job(owner_id)
    refused = job["result"]["refused"]
    assert [entry["reason"] for entry in refused] == ["stale_market_data"]
    assert refused[0]["instrument_id"] == TEST_INSTRUMENT
    assert "2024-01-30 is 123 days old" in refused[0]["detail"]
    assert "past the 3 declared" in refused[0]["detail"]
    assert refused[0]["skill_unit_id"] == str(env["portfolio"].id)
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []

    # The same stale series with no declared ceiling is served, not "fresh enough
    # by a platform default nobody agreed to": the data age is reported instead.
    _declare(env)
    assert _tick(window=STALE_WINDOW_B)["skipped"] is None
    served = _served(owner_id)
    assert len(served) == 1
    written = _last_job(owner_id)["result"]["results"][0]
    assert written["max_data_age_days"] is None
    assert written["data_age_days"] == 124
    assert written["as_of_date"] == STALE_WINDOW_B

    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["max_data_age_days"] is None
    assert state["last_refusal"] is None
    assert state["last_run"]["window"] == STALE_WINDOW_B
    assert state["last_estimate"]["data_age_days"] == 124


def test_a_skill_with_no_selection_is_refused_not_inherited(client, personal_env):
    """No selection of its own is not a licence to use whatever the record points at."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    db = TestSession()
    try:
        from app.models.personal_model_selection import PersonalModelSelection

        db.query(PersonalModelSelection).filter(
            PersonalModelSelection.owner_id == owner_id
        ).delete()
        db.commit()
    finally:
        db.close()

    assert _tick(window=FRESH)["skipped"] is None
    job = _last_job(owner_id)
    assert [entry["reason"] for entry in job["result"]["refused"]] == [
        "strategy_scope_missing"
    ]
    assert _served(owner_id) == []
    state = _status(client, env["headers"], env["portfolio"].id).json()
    assert state["current_model_version_id"] is None
    assert state["enabled"] is True  # the consent is intact; the selection is not


# ---------------------------------------------------------------------------
# 11 — the door and the clock refuse with the same words
# ---------------------------------------------------------------------------


def test_the_http_door_and_the_service_report_the_same_refusal(client, personal_env):
    """One gate, one code: a person and the tick are refused for the same reason."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id

    # Break the artifact/training-job agreement the gate checks on every call.
    db = env["db"]
    db.expire_all()
    job_row = db.query(type(env["job"])).filter(
        type(env["job"]).id == env["job"].id
    ).first()
    job_row.results = {**job_row.results, "artifact_sha256": "0" * 64}
    db.commit()

    at_the_door = client.post(
        f"/api/v1/ml/personal/models/{env['version'].id}/predict",
        headers=env["headers"],
        json={
            "instrument_id": TEST_INSTRUMENT,
            "as_of_date": FRESH,
            "skill_unit_id": str(env["portfolio"].id),
        },
    )
    assert at_the_door.status_code == 403, at_the_door.text
    human_reason = at_the_door.json()["detail"]["reason_code"]
    assert at_the_door.json()["detail"]["code"] == "inference_not_authorized"

    _declare(env)
    assert _tick(window=FRESH)["skipped"] is None
    refused = _last_job(owner_id)["result"]["refused"]
    assert len(refused) == 1
    assert refused[0]["reason"] == human_reason
    assert refused[0]["refused_by"] == "inference_not_authorized"
    assert refused[0]["http_status"] == 403
    assert _served(owner_id) == []


def test_the_manual_button_and_the_clock_write_the_same_estimate(client, personal_env):
    """``POST …/run`` is the clock's own lane: same window, same key, one seat.

    This node read the other way until 2026-11-19. It used to pin a press as
    outside the dedup - no window on its row, so a second press billed a second
    inference and the ledger counted four bills for one declared instrument.
    That was the defect, not the contract: the button and the 05:00 clock are two
    ways of asking for one service, so they share the window, the business key
    ``(owner, skill, instrument, window)`` and the durable seat, and only the
    "who asked" label differs.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    today = de.estimate_window()
    _declare(env)

    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    body = pressed.json()
    assert body["job"]["estimated"] == 1
    assert body["job"]["scope"] == "manual"
    served = _served(owner_id)
    assert len(served) == 1
    inference_id = str(served[0]["id"])
    # The response reads back the row the press itself wrote, dated the day the
    # owner asked - which is the window the clock would have filed it under.
    assert body["status"]["last_estimate"]["inference_id"] == inference_id
    assert body["status"]["last_run"]["scope"] == "manual"
    assert body["status"]["last_run"]["window"] == today
    assert served[0]["metadata"]["as_of_date"] == today
    job = _last_job(owner_id)
    assert job["config"]["scope"] == "manual"
    assert job["config"]["window"] == today
    assert job["result"]["window"] == today
    assert job["result"]["billed_in_window"] == 1
    # Money settled in the same breath as the bill: nothing left reserved.
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0

    # 手動 → 手動. A second press answers with the first press's inference id,
    # because the seat says the key is paid for; it spends no inference, no
    # reservation and writes no second bill.
    again = _run_now(client, env["headers"], env["portfolio"].id)
    assert again.status_code == 201, again.text
    second = again.json()
    assert second["job"]["estimated"] == 0
    assert len(_served(owner_id)) == 1
    assert second["status"]["last_estimate"]["inference_id"] == inference_id
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0
    refusal = _last_job(owner_id)["result"]["refused"][0]
    assert refusal["reason"] == "already_estimated_this_window"
    assert refusal["decided_by"] == "billing_ledger"
    assert refusal["inference_ids"] == [inference_id]
    # The reused answer is the same row, cited - not a fresh estimate wearing
    # the old one's clothes: a billing row holds no verdict and does not claim
    # to.
    reused = _last_job(owner_id)["result"]["recovered"][0]
    assert reused["inference_id"] == inference_id
    assert reused["predicted_action"] is None
    assert reused["verdict_durable"] is False


def test_the_cron_entry_and_a_press_after_it_bill_the_window_once(client, personal_env):
    """cron → 手動: the clock pays first, and the button inherits that bill."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    # The scheduled entry, not a test's own window string: it derives today.
    assert _cron(user_id=owner_id)["skipped"] is None
    served = _served(owner_id)
    assert len(served) == 1
    inference_id = str(served[0]["id"])
    clock_job = _last_job(owner_id)
    assert clock_job["config"]["scope"] == "daily"
    assert clock_job["config"]["window"] == de.estimate_window()

    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 0
    assert len(_served(owner_id)) == 1, "the press re-billed a day the clock paid for"
    assert pressed.json()["status"]["last_estimate"]["inference_id"] == inference_id
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0

    # And a third request of either kind still buys nothing.
    assert _cron(user_id=owner_id)["skipped"] is None
    assert _run_now(client, env["headers"], env["portfolio"].id).status_code == 201
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("settled") == 1


def test_the_button_pays_first_and_the_clock_inherits_that_bill(client, personal_env):
    """手動 → cron: the press pays for the day and 05:00 has nothing left to buy.

    The mirror of the node above, and it is a different test rather than a
    rearrangement of it: the two entries are entered by different code (the route
    by ``trigger_daily_estimate``, the job by ``run_scheduled_daily_estimates``),
    so only the *durable* key can make them one service. If the clock asked its
    own summary rows instead of the billing ledger whether a window was paid for,
    an owner who pressed the button at 09:00 would be billed a second time at
    05:00 without touching anything.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    today = de.estimate_window()
    _declare(env)

    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 1
    served = _served(owner_id)
    assert len(served) == 1
    inference_id = str(served[0]["id"])
    assert served[0]["metadata"]["as_of_date"] == today
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0

    # The scheduled entry, deriving its own window: the same UTC day the press
    # just filed its bill under.
    assert _cron(user_id=owner_id)["skipped"] is None
    after = _served(owner_id)
    assert len(after) == 1, "the clock re-billed a day the button paid for"
    assert str(after[0]["id"]) == inference_id
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0

    clock_job = _last_job(owner_id)
    assert clock_job["config"]["scope"] == "daily", clock_job["config"]
    assert clock_job["config"]["window"] == today
    assert clock_job["result"]["estimated"] == 0
    refusal = clock_job["result"]["refused"][0]
    assert refusal["reason"] == "already_estimated_this_window"
    assert refusal["decided_by"] == "billing_ledger"
    assert refusal["inference_ids"] == [inference_id]
    reused = clock_job["result"]["recovered"][0]
    assert reused["inference_id"] == inference_id
    assert reused["verdict_durable"] is False


def test_a_press_that_finds_the_clock_refused_still_asks_the_gate(client, personal_env):
    """A refusal spent nothing, so it does not silence the button.

    The two entries keep the *same* rule for money and *different* rules for
    reporting: the billing ledger decides for both, while the closed-summary book
    - a report of what one run decided, refusals included - belongs to the clock
    alone. A clock that refused for ``quota_exceeded`` recorded no bill, so the
    owner who presses the button must be told the reason that is true now, not
    "already estimated this window", which would be a lie about money nobody
    spent. What the clock does keep for its own window is the contract
    :func:`test_a_refusal_that_spent_nothing_is_that_windows_record_not_a_second_attempt`
    pins: one attempt per instrument per window, refusals included.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    db = TestSession()
    try:
        db.add(PilotEntitlement(user_id=owner_id, ai_usage_limit=0))
        db.commit()
    finally:
        db.close()
    _declare(env)

    assert _cron(user_id=owner_id)["skipped"] is None
    clock_refused = _last_job(owner_id)["result"]["refused"][0]
    assert clock_refused["reason"] == "quota_exceeded"
    assert _served(owner_id) == []

    # The press is not told "already estimated this window" by the clock's
    # refusal: it re-asks the gate and gets the reason that is true today.
    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    press_refused = _last_job(owner_id)["result"]["refused"][0]
    assert press_refused["reason"] == "quota_exceeded", press_refused
    assert _served(owner_id) == []
    assert _reservations(owner_id) == []

    # Once the quota is back the same window serves through the button, because
    # nothing had been billed and nothing had been claimed but a report.
    _set_quota(owner_id, 10)
    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 1
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("settled") == 1


def test_a_clock_that_finds_the_button_refused_still_walks_the_scope(
    client, personal_env
):
    """The other direction: a manual row's summary is not the clock's ledger.

    If the refusal book were keyed to the window alone, the press below would
    have written a row naming the skill, and the 05:00 entry would then read its
    own window as reported and buy nothing - an owner's aborted press silently
    switching off the day's service. It is not, so the clock attempts, and the
    bill it makes is the day's only one.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    db = TestSession()
    try:
        db.add(PilotEntitlement(user_id=owner_id, ai_usage_limit=0))
        db.commit()
    finally:
        db.close()
    _declare(env)

    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    assert _last_job(owner_id)["config"]["scope"] == "manual"
    assert _last_job(owner_id)["result"]["refused"][0]["reason"] == "quota_exceeded"
    assert _served(owner_id) == []

    _set_quota(owner_id, 10)
    assert _cron(user_id=owner_id)["skipped"] is None
    job = _last_job(owner_id)
    assert job["config"]["scope"] == "daily"
    assert [entry["skill_unit_id"] for entry in job["result"]["results"]] == [str(skill)]
    assert job["result"]["refused"] == []
    assert len(_served(owner_id)) == 1

    # The window is now billed, and both entries stop at that fact.
    assert _cron(user_id=owner_id)["skipped"] is None
    assert _run_now(client, env["headers"], skill).status_code == 201
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0


def test_a_press_killed_after_one_bill_serves_the_rest_of_the_window(
    client, personal_env, monkeypatch
):
    """局部成功＋摘要遺失＋手動入口, on one window.

    Two instruments, one press: the first is billed, the process dies before the
    run describes anything. The durable state is one bill and an open ``running``
    row - no summary, nothing in RAM. The next press is asked what the ledger
    says, and it owes exactly the instrument that was never priced.

    Evidence levels differ across the two halves of this node, and the
    difference is the fixture rather than the product: the death is injected at
    the service entry the manual route calls (``trigger_daily_estimate``),
    because a ``BaseException`` raised inside a request is re-raised by
    ``TestClient`` straight through its anyio portal and takes the portal down
    with it - so no HTTP response exists to read, which is also true of a
    process that stopped. The recovery press is the formal ``POST …/run`` door,
    and the ledger readback is an independent session. Nothing here proves what
    a browser would have shown for the killed press.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()
    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])

    real = de._estimate_one
    priced: list = []

    class KilledMidRun(BaseException):
        """Not an ``Exception``: the per-estimate guard must not swallow it."""

    def price_one_then_die(session, owner, portfolio, instrument_id, as_of, **kw):
        answer = real(session, owner, portfolio, instrument_id, as_of, **kw)
        priced.append(instrument_id)
        raise KilledMidRun("the worker was stopped here")

    monkeypatch.setattr(de, "_estimate_one", price_one_then_die)
    dead_session = TestSession()
    try:
        owner_row = dead_session.query(User).filter(User.id == owner_id).one()
        with pytest.raises(KilledMidRun):
            de.trigger_daily_estimate(dead_session, owner_row, skill)
    finally:
        dead_session.close()
    assert priced == [TEST_INSTRUMENT]

    paid = _served(owner_id)
    assert len(paid) == 1
    assert paid[0]["metadata"]["instrument_id"] == TEST_INSTRUMENT
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0
    dead = _last_job(owner_id)
    assert dead["status"] == "running"
    assert dead["result"] == {}
    assert dead["config"]["scope"] == de.MANUAL_SCOPE
    assert dead["config"]["window"] == de.estimate_window()

    monkeypatch.setattr(de, "_estimate_one", real)
    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 1
    served = _served(owner_id)
    assert sorted(row["metadata"]["instrument_id"] for row in served) == sorted(
        [TEST_INSTRUMENT, SECOND_INSTRUMENT]
    ), "the retry bought an instrument the dead press had already paid for"
    assert _reservations(owner_id).count("settled") == 2
    assert _reservations(owner_id).count("reserved") == 0
    # Each of the two bills is named once, by the run that wrote it: the dead
    # press owns the first ``inference_id`` and the retry owns the second, so
    # the retry neither re-bills the first nor leaves it unattributed.
    job = _last_job(owner_id)
    assert [entry["instrument_id"] for entry in job["result"]["results"]] == [
        SECOND_INSTRUMENT
    ]
    assert job["result"]["results"][0]["inference_id"] == str(served[1]["id"])
    assert [entry["inference_id"] for entry in job["result"]["recovered"]] == [
        str(served[0]["id"])
    ]
    assert job["result"]["billed_before_this_run"] == 1
    assert job["result"]["billed_in_window"] == 2
    # The clock, arriving after all that, finds the day paid and buys none of it.
    assert _cron(user_id=owner_id)["skipped"] is None
    assert len(_served(owner_id)) == 2
    # The clock, arriving after all that, finds the day paid and buys none of it.
    assert _cron(user_id=owner_id)["skipped"] is None
    assert len(_served(owner_id)) == 2


def test_a_failed_press_that_never_settled_is_asked_again(client, personal_env, monkeypatch):
    """未到結算的失敗: an estimate that died before its bill is not a paid day.

    The gate reserves, the engine dies, the reservation is released in its own
    commit and no billing row is written. The window therefore owes the
    instrument again, and the retry is a first charge, not a second one.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    _declare(env)

    from app.api import personal_models as pm

    real = de._estimate_one
    real_predict = pm.predict_personal
    calls: list = []

    def engine_dead(model_id, features, owner_id=None):
        """Same shape as ``predict_personal(model_id, features, owner_id=…)``.

        A mock whose signature does not match the door it replaces raises
        ``TypeError`` before its own body, and the refusal this node reports
        would then describe the fixture rather than a dead engine.
        """
        raise RuntimeError("the inference engine died")

    def estimate_once_broken(session, owner, portfolio, instrument_id, as_of, **kw):
        calls.append(instrument_id)
        if len(calls) == 1:
            with patch.object(pm, "predict_personal", side_effect=engine_dead):
                return real(session, owner, portfolio, instrument_id, as_of, **kw)
        return real(session, owner, portfolio, instrument_id, as_of, **kw)

    monkeypatch.setattr(de, "_estimate_one", estimate_once_broken)
    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 0
    assert _served(owner_id) == []
    # Nothing is left hanging: the gate's own release path ran in its own commit,
    # so the failed press ends with a terminal reservation and no unsettled one.
    assert _reservations(owner_id).count("reserved") == 0
    assert _reservations(owner_id).count("released") == 1
    refusal = _last_job(owner_id)["result"]["refused"][0]
    assert refusal["reason"] == "prediction_failed"
    assert "decided_by" not in refusal, refusal

    monkeypatch.setattr(de, "_estimate_one", real)
    again = _run_now(client, env["headers"], skill)
    assert again.status_code == 201, again.text
    assert again.json()["job"]["estimated"] == 1
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0


def test_a_partial_tick_settles_only_the_skill_whose_engine_answered(
    client, personal_env, monkeypatch
):
    """部分成功 counted in the ledger: one item dies, its sibling is billed.

    ``status == "partial"`` is only the row's own word for itself. What makes it
    true is that the bill and the reservation follow the estimate, item by item:
    settling the skill whose call raised would charge for an estimate that never
    existed, and leaving the one that answered unpaid would let the next press
    buy it a second time. Both are read out of ``usage_records`` and
    ``quota_reservations`` here rather than inferred from the status string. The
    one-sided cases are the node above (nothing billed, because nothing reached
    the gate) and ``test_a_tick_that_dies_mid_run_does_not_bill_the_estimate_it_had_paid_for``.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)

    real = de._estimate_one

    def estimate_one_broken(session, owner, portfolio, instrument_id, as_of, **kw):
        if str(portfolio.id) == str(skill_a):
            raise RuntimeError("the worker died between the gate and the ledger")
        return real(session, owner, portfolio, instrument_id, as_of, **kw)

    monkeypatch.setattr(de, "_estimate_one", estimate_one_broken)
    outcome = _tick(window=FRESH)
    assert outcome["skipped"] is None
    # The counts the response hands a caller, for a tick that answered one account
    # and lost the other: ``estimated`` alone would read as a clean success, and
    # ``refused`` alone cannot say why the second account is missing.
    owner_outcome = outcome["jobs"][0]
    assert (owner_outcome["estimated"], owner_outcome["refused"]) == (1, 0), owner_outcome
    assert owner_outcome["failed"] == 1, owner_outcome
    assert owner_outcome["status"] == "partial", owner_outcome

    job = _last_job(owner_id)
    assert job["status"] == "partial", job["result"]
    assert job["result"]["estimated"] == 1
    assert job["result"]["failed_count"] == 1
    assert job["result"]["errors"], "a dead engine left no error behind to name"
    assert str(skill_a) in job["result"]["errors"][0]

    served = _served(owner_id)
    assert [row["metadata"]["skill_unit_id"] for row in served] == [str(skill_b)], (
        "the ledger billed a skill whose estimate raised, or failed to bill the "
        "one that answered"
    )
    assert _reservations(owner_id).count("settled") == 1
    assert _reservations(owner_id).count("reserved") == 0


def test_a_press_whose_engine_died_names_the_failure_in_the_response(
    client, personal_env, monkeypatch
):
    """The 201 body answers 「the engine died」, not 「nothing was refused」.

    A press prices one skill, so its job dict carries exactly one nonzero count:
    estimated, refused or failed. Before today only the first two travelled: an
    item whose worker raised after the gate landed in neither, so the response
    read ``estimated=0, refused=0`` beside a job row whose own status said
    ``failed`` — and the panel, which is told to read the body rather than the
    status code, had nothing left to say what did not land. This is the HTTP door
    of the same three-way split the clock records, pinned where a person reads it.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    _declare(env)

    def engine_died(session, owner, portfolio, instrument_id, as_of, **kw):
        raise RuntimeError("the worker died between the gate and the ledger")

    monkeypatch.setattr(de, "_estimate_one", engine_died)

    pressed = _run_now(client, env["headers"], env["portfolio"].id)
    assert pressed.status_code == 201, pressed.text
    job = pressed.json()["job"]
    assert job["scope"] == "manual"
    assert job["status"] == "failed", job
    assert job["estimated"] == 0, job
    assert job["refused"] == 0, job
    assert job["failed"] == 1, job
    # Nothing was priced for an estimate that never existed, and no seat was left
    # held by the run that died.
    assert _served(owner_id) == [], "a dead engine wrote a billing row"
    assert _reservations(owner_id).count("settled") == 0
    assert _reservations(owner_id).count("reserved") == 0
    result = _last_job(owner_id)["result"]
    assert result["failed_count"] == 1
    assert "the worker died between the gate and the ledger" in result["errors"][0]


def test_a_saved_failure_is_named_on_a_reloaded_card_without_its_exception_text(
    client, personal_env, monkeypatch
):
    """The card the owner gets back minutes later, with nothing internal in it.

    ``test_a_press_whose_engine_died_names_the_failure_in_the_response`` reads
    the press response, which is gone the moment the page is reloaded. This is
    the durable half: the run's row is the only thing left, and the status route
    used to scan past a row that only ever recorded ``errors`` — so the newest
    answer a reloaded card could name was some earlier day, and a failure that
    is written in ``learning_jobs`` read as 「沒有估計」. The read-back now carries
    the account, the instrument and a sentence, and carries no part of the
    exception: the traceback stays in the log, the text stays in the job row,
    and neither is what the owner is shown.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()
    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])

    real = de._estimate_one

    def estimate(session, owner, portfolio, instrument_id, as_of, **kw):
        if instrument_id == SECOND_INSTRUMENT:
            raise RuntimeError("the worker died between the gate and the ledger")
        return real(session, owner, portfolio, instrument_id, as_of, **kw)

    monkeypatch.setattr(de, "_estimate_one", estimate)
    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["status"] == "partial", pressed.text
    assert pressed.json()["job"]["estimated"] == 1, pressed.text
    assert pressed.json()["job"]["failed"] == 1, pressed.text
    monkeypatch.undo()

    # Reload: a plain status read, with the engine intact and nothing pressed.
    # Reading the card must not price anything a second time.
    read = _status(client, env["headers"], skill)
    state = read.json()
    assert state["last_run"] is not None, "a failed run named nothing on reload"
    assert state["last_run"]["status"] == "partial"
    # A press files into the window the clock owns today, which is today's UTC
    # day rather than the fixture's FRESH data cut-off.
    assert state["last_run"]["window"] == de.estimate_window()
    failure = state["last_failure"]
    assert failure is not None, "the saved failure was still not read back"
    assert failure["skill_unit_id"] == str(skill)
    assert failure["instrument_id"] == SECOND_INSTRUMENT
    assert failure["reason"] == "engine_failure"
    assert failure["count"] == 1
    assert "attributed_from" not in failure, failure
    # The sentence, not merely a sentence: this is the string the door's own table
    # holds for this reason, which is what the panel is allowed to show.
    from app.api.personalization import _DAILY_ESTIMATE_MESSAGES_ZH

    assert failure["message_zh"] == _DAILY_ESTIMATE_MESSAGES_ZH["engine_failure"]
    # The other half of the run is still on the card: one instrument answered,
    # and a failure beside it does not erase the estimate that was billed.
    assert state["last_estimate"]["instrument_id"] == TEST_INSTRUMENT
    # What the owner may not see: the exception text, in any field of the body.
    assert "the worker died between the gate and the ledger" not in read.text
    assert "RuntimeError" not in read.text, read.text
    assert len(_served(owner_id)) == 1
    assert _reservations(owner_id).count("reserved") == 0


def test_a_row_written_before_the_failure_field_is_attributed_without_guessing(
    client, personal_env, monkeypatch
):
    """Legacy rows: the account is read off the prefix, and the text is not read.

    Rows written before ``failures`` existed hold the same event as one bare
    string per item, so the only reliable attribution in them is the
    ``"{skill_unit_id}/{instrument_id}: "`` prefix this lane itself wrote. This
    node rewrites a run's row into exactly that shape and hands it four lines —
    one naming this skill, one naming the owner's other skill, one naming an
    account nobody holds, and one with no account in it at all — and checks that
    each card claims only its own line, counts one item rather than four, and
    quotes none of the text that follows the separator. An unattributable line
    produces no answer here; inventing one would be the same guess the brief
    rules out.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    other = _second_skill(env).id
    stranger = uuid.uuid4()
    _declare(env)

    # A run whose engine died, so the window it filed into carries no bill: the
    # legacy row below then has nothing to point at but its own error strings.
    def engine_died(session, owner, portfolio, instrument_id, as_of, **kw):
        raise RuntimeError("nothing billed here")

    monkeypatch.setattr(de, "_estimate_one", engine_died)
    assert _tick(window=FRESH)["skipped"] is None
    monkeypatch.undo()
    assert _served(owner_id) == []
    db = TestSession()
    try:
        row = (
            db.query(LearningJob)
            .filter(
                LearningJob.user_id == owner_id,
                LearningJob.job_type == de.DAILY_ESTIMATE_JOB_TYPE,
            )
            .order_by(LearningJob.created_at.desc())
            .first()
        )
        assert row is not None
        row.status = "failed"
        row.result_json = {
            "window": FRESH,
            "estimated": 0,
            "refused_count": 0,
            "failed_count": 4,
            "results": [],
            "recovered": [],
            "refused": [],
            "errors": [
                f"{skill}/{TEST_INSTRUMENT}: ValueError the card must not quote",
                f"{other}/{SECOND_INSTRUMENT}: KeyError another account's item",
                f"{stranger}/{TEST_INSTRUMENT}: ValueError nobody holds this",
                "a line with no account in it at all",
            ],
        }
        db.commit()
    finally:
        db.close()

    read = _status(client, env["headers"], skill)
    state = read.json()
    failure = state["last_failure"]
    assert failure["skill_unit_id"] == str(skill)
    assert failure["instrument_id"] == TEST_INSTRUMENT
    assert failure["reason"] == "engine_failure"
    assert failure["count"] == 1, "four lines in the row, one of them this account's"
    assert failure["attributed_from"] == "error_string_prefix"
    # The row is now the newest thing that names this skill, so the card reads
    # its status instead of falling back to an older day.
    assert state["last_run"]["status"] == "failed"
    assert state["last_estimate"] is None
    assert "ValueError" not in read.text, read.text
    assert "no account in it at all" not in read.text
    assert str(stranger) not in read.text

    # The owner's other account is attributed on its own card, and only by its
    # own line.
    other_state = _status(client, env["headers"], other).json()
    assert other_state["last_failure"]["instrument_id"] == SECOND_INSTRUMENT
    assert other_state["last_failure"]["count"] == 1


def test_a_failure_row_is_worded_at_the_door_whatever_reason_it_carries(
    client, personal_env, monkeypatch
):
    """No failure reaches the card as a bare code, known reason or not.

    ``engine_failure`` is the reason this lane writes when the worker raises, and
    it is the sixth code this door words from one table. Two halves are pinned
    here, because an enumeration checked only against the reasons thought of today
    is exactly how a new one arrives unreadable:

    * a row whose reason the door knows comes back as that table's own sentence —
      not a sentence of similar length, the entry a reader would be shown;
    * a row whose reason this build has never heard of — which is what the same
      column looks like when a newer build wrote the row and this one reads it —
      still comes back as a sentence, keeps its code in ``reason`` beside it, and
      does not fall through to ``None``, to the raw code, or to a 500.
    """
    from app.api.personalization import _DAILY_ESTIMATE_MESSAGES_ZH

    env = _open_env(client, personal_env)
    skill = env["portfolio"].id
    _declare(env)

    def engine_died(session, owner, portfolio, instrument_id, as_of, **kw):
        raise RuntimeError("the worker died between the gate and the ledger")

    monkeypatch.setattr(de, "_estimate_one", engine_died)
    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    monkeypatch.undo()

    def persist(reason):
        """Rewrite the run's own failure entry and hand back the card read."""
        db = TestSession()
        try:
            row = (
                db.query(LearningJob)
                .filter(
                    LearningJob.user_id == env["user"].id,
                    LearningJob.job_type == de.DAILY_ESTIMATE_JOB_TYPE,
                )
                .order_by(LearningJob.created_at.desc())
                .first()
            )
            assert row is not None
            payload = dict(row.result_json or {})
            assert payload.get("failures"), "the tick wrote no structured failure"
            # One entry, this skill, on a field rather than parsed off a string —
            # so the only thing that varies between the two reads is the reason.
            payload["failures"] = [
                {
                    "skill_unit_id": str(skill),
                    "instrument_id": TEST_INSTRUMENT,
                    "reason": reason,
                }
            ]
            row.result_json = payload
            db.commit()
        finally:
            db.close()
        read = _status(client, env["headers"], skill)
        assert read.status_code == 200, read.text
        return read

    unknown = "reason_this_build_has_no_sentence_for"
    assert unknown not in _DAILY_ESTIMATE_MESSAGES_ZH

    failure = persist(unknown).json()["last_failure"]
    assert failure["reason"] == unknown, failure
    message = failure["message_zh"]
    assert isinstance(message, str) and message, (
        f"an unworded reason came back without a sentence: {failure!r}"
    )
    assert "。" in message, message
    assert unknown not in message, (
        f"the card printed the code where a person reads: {message}"
    )
    assert message != _DAILY_ESTIMATE_MESSAGES_ZH["engine_failure"], (
        "the fallback sentence is the mapped one, so this read proved nothing"
    )

    worded = persist("engine_failure")
    failure = worded.json()["last_failure"]
    assert failure["reason"] == "engine_failure"
    assert failure["message_zh"] == _DAILY_ESTIMATE_MESSAGES_ZH["engine_failure"]
    assert "RuntimeError" not in worded.text, worded.text


def test_a_tick_where_every_engine_died_settles_nothing(
    client, personal_env, monkeypatch
):
    """全部失敗 counted in the ledger: two skills asked, neither answered.

    The node above leaves one skill paid, so it cannot show the shape the owner
    asked to be true: a tick in which every item died must add no billing row and
    no settlement for either skill, while still ending with no reservation left
    hanging — otherwise the day reads as bought twice over, or as bought once for
    estimates that were never written. Everything is read out of ``usage_records``,
    ``quota_reservations`` and the job row the panel is allowed to show, and the
    window is then re-asked, because a day that produced nothing owes one.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)

    from app.api import personal_models as pm

    def engine_dead(model_id, features, owner_id=None):
        """Same shape as ``predict_personal(model_id, features, owner_id=…)``.

        A mock whose signature does not match the door it replaces raises
        ``TypeError`` inside the fixture, and what this node then reported would
        describe the test rather than a dead engine.
        """
        raise RuntimeError("the inference engine died")

    real_predict = pm.predict_personal
    # Killed below the gate and above the ledger, so each account really did open
    # a reservation and have it handed back: replacing ``_estimate_one`` whole
    # would spend nothing at all and prove the wrong thing.
    monkeypatch.setattr(pm, "predict_personal", engine_dead)
    # The clock walks every declared account, so one tick asks both engines.
    assert _tick(window=FRESH)["skipped"] is None

    assert _served(owner_id) == [], "an all-failed tick wrote billing rows"
    reservations = _reservations(owner_id)
    assert reservations.count("settled") == 0, reservations
    assert reservations.count("reserved") == 0, reservations
    assert reservations.count("released") == 2, (
        "each dead engine must end with its own terminal release: "
        f"{reservations}"
    )

    job = _last_job(owner_id)
    result = job["result"]
    assert result["estimated"] == 0
    assert result["billed_in_window"] == 0, result
    assert result["failed_count"] == 0, result
    assert result["errors"] == [], result
    # A dead engine is a refusal the run names, not an error that ends it: both
    # accounts are here with the reason the gate gave them.
    assert result["refused_count"] == 2, result
    assert {entry["skill_unit_id"] for entry in result["refused"]} == {
        str(skill_a), str(skill_b)
    }, result
    assert {entry["reason"] for entry in result["refused"]} == {"prediction_failed"}, result
    # Stated, not smoothed over: the row's own word for this run is ``partial``,
    # the same word it uses when some accounts landed, because the status ladder
    # only asks whether anything broke or was refused — never how many answered.
    # What separates 部分成功 from 全部失敗 is ``estimated``, which is the count the
    # panel composes its notice from.
    assert job["status"] == "partial", result

    # And the window is not marked as served: the next press asks the engine again
    # instead of carrying over a day that produced nothing.
    monkeypatch.setattr(pm, "predict_personal", real_predict)
    again = _run_now(client, env["headers"], skill_a)
    assert again.status_code == 201, again.text
    # The 201 names the attempt row, as it always does here; what the day cost is
    # the count below it, read back from the ledger rather than from the status.
    assert again.json()["job"]["estimated"] == 1, again.text
    assert [row["metadata"]["skill_unit_id"] for row in _served(owner_id)] == [str(skill_a)]
    assert _reservations(owner_id).count("settled") == 1


def test_a_press_on_one_skill_leaves_the_other_windows_and_skills_alone(
    client, personal_env
):
    """跨技能: the key is per skill account, and a press prices only what it names."""
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)

    pressed = _run_now(client, env["headers"], skill_a)
    assert pressed.status_code == 201, pressed.text
    served = _served(owner_id)
    assert [row["metadata"]["skill_unit_id"] for row in served] == [str(skill_a)]

    # The clock then serves the skill the press never named, and refuses the one
    # it paid for - both facts in the same row.
    assert _cron(user_id=owner_id)["skipped"] is None
    served = _served(owner_id)
    assert [row["metadata"]["skill_unit_id"] for row in served] == [str(skill_a), str(skill_b)]
    job = _last_job(owner_id)
    assert [entry["skill_unit_id"] for entry in job["result"]["results"]] == [str(skill_b)]
    assert [entry["skill_unit_id"] for entry in job["result"]["refused"]] == [str(skill_a)]
    assert job["result"]["refused"][0]["reason"] == "already_estimated_this_window"

    # A new window is a new day for both, and the press's bill does not leak into
    # it: two instruments, two bills, no third.
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    assert len(_served(owner_id)) == 4


def test_a_press_bills_todays_window_without_claiming_the_other_ones(
    client, personal_env
):
    """跨 UTC 窗口 from the button's side: the claim is the window it named.

    A press cannot be asked for another day - it files under today by
    construction - so the other half of the boundary is tested from the clock's
    side: a window billed *before* today and a window billed *by* today must not
    answer for each other. If the press's seat were per skill rather than per
    ``(skill, instrument, window)``, the clock would find the day it was asked
    for already paid and buy nothing, and a second press would cite the wrong
    ``inference_id``. Both of those are asserted here, in the two directions.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill = env["portfolio"].id
    today = de.estimate_window()
    assert today != FRESH, "the fixture date has arrived; this node needs two windows"
    _declare(env)

    # The clock bills a window that is not today's.
    assert _tick(window=FRESH, user_id=owner_id)["skipped"] is None
    early = _served(owner_id)
    assert len(early) == 1
    assert early[0]["metadata"]["as_of_date"] == FRESH

    # Today's press is not silenced by that bill: a different window is a
    # different day, and the owner is asking for this one.
    pressed = _run_now(client, env["headers"], skill)
    assert pressed.status_code == 201, pressed.text
    assert pressed.json()["job"]["estimated"] == 1
    served = _served(owner_id)
    assert len(served) == 2
    assert [row["metadata"]["as_of_date"] for row in served] == [FRESH, today]
    assert _reservations(owner_id).count("settled") == 2
    assert _reservations(owner_id).count("reserved") == 0
    today_id = str(served[1]["id"])

    # And the reverse: once today is paid, a re-press names today's bill and
    # only today's - the older window is not offered as the reason.
    again = _run_now(client, env["headers"], skill)
    assert again.status_code == 201, again.text
    assert again.json()["job"]["estimated"] == 0
    assert len(_served(owner_id)) == 2, "the press billed a window it had already paid"
    refusal = _last_job(owner_id)["result"]["refused"][0]
    assert refusal["reason"] == "already_estimated_this_window"
    assert refusal["decided_by"] == "billing_ledger"
    assert refusal["inference_ids"] == [today_id], refusal
    assert again.json()["status"]["last_estimate"]["inference_id"] == today_id


def test_withdrawing_one_skill_does_not_silence_the_other_press(client, personal_env):
    """撤回: the DELETE scope is per skill, and a press honours exactly what is left.

    Withdrawing A must not remove B, must not price A, and must not leave a
    ledger row pretending to be a run. The *mid-run* withdrawal - the scope read
    at tick open naming a skill the owner has since closed - is a different
    boundary and is pinned by
    :func:`test_a_withdrawal_arriving_mid_run_stops_the_skills_not_started_yet`.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a, skill_b = _declare_two(env)

    withdrawn = client.delete(
        f"{BASE}/consent", headers=env["headers"], params={"skill_unit_id": str(skill_a)}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    remaining = [
        entry["skill_unit_id"] for entry in withdrawn.json()["consent"]["skill_units"]
    ]
    assert remaining == [str(skill_b)]
    # The read agrees with the write: B is still declared and still enabled, and
    # A is declared no longer - not "declared and refused".
    skills = {
        str(item["skill_unit_id"]): item
        for item in _status(client, env["headers"]).json()["skills"]
    }
    assert skills[str(skill_a)]["declared"] is False
    assert skills[str(skill_b)]["declared"] is True
    assert skills[str(skill_b)]["enabled"] is True
    # The read also names why A is off, from the owner's own revoke event: not
    # 「never declared」, which would erase an instruction the owner did give.
    assert skills[str(skill_a)]["disabled_reason"] == "consent_withdrawn"
    assert skills[str(skill_b)]["disabled_reason"] is None

    jobs_before = len(_jobs(owner_id))
    # The account the owner just withdrew cannot be priced by the button, and the
    # refusal is the door's own, before any ledger row is opened.
    refused = _run_now(client, env["headers"], skill_a)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "no_skills_enabled"
    assert _served(owner_id) == []
    assert len(_jobs(owner_id)) == jobs_before

    # The account still declared is served by the same press door.
    pressed = _run_now(client, env["headers"], skill_b)
    assert pressed.status_code == 201, pressed.text
    assert [row["metadata"]["skill_unit_id"] for row in _served(owner_id)] == [str(skill_b)]

    # And the clock, walking what is left of the scope, buys that bill no again.
    assert _cron(user_id=owner_id)["skipped"] is None
    assert len(_served(owner_id)) == 1
    last = _last_job(owner_id)
    assert last["result"]["skills_declared"] == 1
    assert [entry["skill_unit_id"] for entry in last["result"]["refused"]] == [str(skill_b)]
    assert last["result"]["refused"][0]["reason"] == "already_estimated_this_window"

    # Closing the whole scope stops the button for both.
    revoked = client.delete(f"{BASE}/consent", headers=env["headers"])
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["consent"]["skill_units"] == []
    assert _run_now(client, env["headers"], skill_b).status_code == 400
    assert len(_served(owner_id)) == 1
    # Each closure now has its own durable basis: A's is the per-skill revoke,
    # B's is the whole-scope one that named nothing and followed a grant naming
    # B. Neither reads as 「從未聲明」.
    closed = {
        str(item["skill_unit_id"]): item
        for item in _status(client, env["headers"]).json()["skills"]
    }
    assert closed[str(skill_a)]["disabled_reason"] == "consent_withdrawn"
    assert closed[str(skill_b)]["disabled_reason"] == "consent_withdrawn"


def test_a_skill_nobody_declared_is_not_reported_as_withdrawn(client, personal_env):
    """Two facts the panel used to share one word for, separated by the events.

    ``disabled_reason`` distinguishes 「本人撤回過，現在關著」 from 「從來沒有聲明過」
    only because the first has a row to cite: the revoke event the DELETE entry
    emits, and the grant that first put the account in scope. An account nobody
    declared has neither, so it keeps the honest 「目前未啟用」 wording instead of
    being credited with a withdrawal it never saw — which is why this node reads
    the event table rather than accepting the label.
    """
    env = _open_env(client, personal_env)
    owner_id = env["user"].id
    skill_a = env["portfolio"].id
    # A second account of the same owner: created, selected, and never declared.
    skill_c = _second_skill(env).id
    _declare(env)

    withdrawn = client.delete(
        f"{BASE}/consent", headers=env["headers"], params={"skill_unit_id": str(skill_a)}
    )
    assert withdrawn.status_code == 200, withdrawn.text

    skills = {
        str(item["skill_unit_id"]): item
        for item in _status(client, env["headers"]).json()["skills"]
    }
    assert skills[str(skill_a)]["disabled_reason"] == "consent_withdrawn"
    assert skills[str(skill_c)]["disabled_reason"] == "not_declared"

    db = TestSession()
    try:
        rows = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "daily_estimate_consent",
                AuditEvent.entity_id == str(owner_id),
                AuditEvent.event_type == "daily_estimate_consent_revoked",
            )
            .all()
        )
    finally:
        db.close()
    assert [row.payload_json["skill_unit_id"] for row in rows] == [str(skill_a)], (
        "the label the owner reads must be traceable to one named event row, "
        "not to the absence this node is about"
    )


# ---------------------------------------------------------------------------
# 12 — estimates only
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 12 — estimates only
# ---------------------------------------------------------------------------


def test_an_estimate_writes_no_order_no_decision_and_no_replay_step(
    client, personal_env
):
    """The lane may not invent trades: the trade journal stays as empty as it was."""
    env = _open_env(client, personal_env)
    owner_id, portfolio = env["user"].id, env["portfolio"]
    # A second instrument with bars of its own, so a two-asset scope is a real
    # two-asset scope and not one estimate plus a missing-data refusal.
    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()

    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])
    assert _tick(window=FRESH)["skipped"] is None
    job = _last_job(owner_id)
    assert job["result"]["estimated"] == 2
    assert job["status"] == "completed"
    assert [entry["instrument_id"] for entry in job["result"]["results"]] == [
        TEST_INSTRUMENT,
        SECOND_INSTRUMENT,
    ]
    assert len(_served(owner_id)) == 2

    db = TestSession()
    try:
        assert db.query(DecisionRecord).filter(
            DecisionRecord.paper_portfolio_id == portfolio.id
        ).count() == 0
        assert db.query(PaperOrder).count() == 0
    finally:
        db.close()

    # Both faces of the same export: the JSON package answers "no rows", and the
    # CSV deliverable stays a header line with nothing under it, so a month of
    # estimates cannot put a trade in the journal that nobody made.
    journal = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/journal",
        headers=env["headers"],
        params={"format": "json"},
    )
    assert journal.status_code == 200, journal.text
    assert journal.json()["records"] == []
    assert journal.json()["row_count"] == 0

    as_csv = client.get(
        f"/api/v1/paper-portfolio/{portfolio.id}/journal", headers=env["headers"]
    )
    assert as_csv.status_code == 200, as_csv.text
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert len([line for line in as_csv.text.splitlines() if line.strip()]) == 1

    # The estimate is readable as a decision source, which is how it is meant to
    # be used: the owner records the decision, the platform does not.
    inference_id = _served(owner_id)[0]["id"]
    recorded = client.post(
        f"/api/v1/paper-portfolio/{portfolio.id}/decisions",
        headers=env["headers"],
        json={
            "decision_type": "no_action",
            "actor_type": "human",
            "rationale": "看到每日估計後決定今日不動手。",
            "linked_inference_id": str(inference_id),
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["linked_inference_id"] == str(inference_id)

    # Recording that decision is the owner's act, and it still writes no order:
    # the estimate lane and the decision door together leave the trade side of
    # the platform untouched.
    db = TestSession()
    try:
        assert db.query(PaperOrder).count() == 0
        assert db.query(DecisionRecord).filter(
            DecisionRecord.paper_portfolio_id == portfolio.id
        ).count() == 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 13 — owners and skills stay apart
# ---------------------------------------------------------------------------


def test_one_owner_cannot_see_or_serve_another_owners_skill(client, personal_env):
    """Scope, tick and status read are all bounded by the authenticated owner."""
    from app.models.user import User

    env = _open_env(client, personal_env)
    owner_a, skill_a = env["user"].id, env["portfolio"].id
    email_b = f"estimates-b-{uuid.uuid4().hex[:8]}@test.com"
    headers_b = _register_and_login(client, email_b)

    _declare(env)
    assert _tick(window=FRESH, user_id=owner_a)["skipped"] is None
    assert len(_served(owner_a)) == 1

    # The other owner's every door answers empty, including for the same skill id.
    assert _declared(client, headers_b).json()["enabled"] is False
    assert _status(client, headers_b).json()["skills"] == []
    assert _status(client, headers_b, skill_a).status_code == 404
    assert _run_now(client, headers_b, skill_a).status_code == 400
    assert _served(owner_a) and len(_served(owner_a)) == 1

    # A tick with no owner named serves only the owners who declared a scope.
    assert _tick(window=NEXT_WINDOW)["skipped"] is None
    owners = {str(row["user_id"]) for row in _jobs()}
    assert owners == {str(owner_a)}
    assert len(_served(owner_a)) == 2

    # And nothing at all was written for the second owner, through any door: no
    # ledger row, no billing row, no reservation. Not a row that was merely
    # unreadable from her side — no row with her id on it.
    db = TestSession()
    try:
        owner_b = db.query(User).filter(User.email == email_b).one().id
    finally:
        db.close()
    assert _jobs(owner_b) == []
    assert _estimates(owner_b) == []
    assert _reservations(owner_b) == []


# ---------------------------------------------------------------------------
# 14 — the fourth tick, on its own hour, starting nothing else
# ---------------------------------------------------------------------------


@contextmanager
def _lanes_named(value: str):
    """Register exactly the lanes ``value`` names, for the duration of the block.

    ``get_settings`` is cached, so this writes the process-wide settings object
    and restores the one field it touched. Needed because an unset allow-list no
    longer registers this lane at all - see
    ``test_learning_scheduler.test_an_upgrade_registers_no_lane_the_host_never_asked_for``
    - and this node asks what the lane does once it *is* on a host.
    """
    settings = ls.get_settings()
    original = settings.learning_scheduler_jobs
    settings.learning_scheduler_jobs = value
    try:
        yield settings
    finally:
        settings.learning_scheduler_jobs = original


def test_the_estimate_tick_starts_no_other_lane():
    """Registering the estimate job opts nobody in and launches no sibling work."""
    with _lanes_named("daily_model_estimate,daily_research"):
        scheduler = ls.create_learning_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"daily_research", "daily_model_estimate"}
    hours = {name: str(job.trigger) for name, job in jobs.items()}
    assert hours["daily_model_estimate"] != hours["daily_research"]
    assert jobs["daily_model_estimate"].kwargs == {"scope": "daily"}
    assert de.DAILY_ESTIMATE_HOUR_UTC == 5
    assert ls.SIMULATION_HOUR_UTC < de.DAILY_ESTIMATE_HOUR_UTC

    owner_id, model_id = _owner_with_a_declared_scope_only()
    spies = {
        name: MagicMock()
        for name in ("run_scheduled_research", "run_scheduled_simulations",
                     "run_pattern_analysis_job")
    }
    # Count the bars before the cron entry runs: if the estimate tick dragged a
    # collection job with it, the series it just estimated on would grow.
    bars_before = _bar_count()
    try:
        with patch.multiple(ls, **{name: spy for name, spy in spies.items()}):
            outcome = de.run_scheduled_daily_estimates()
    finally:
        shutil.rmtree(
            os.path.join(PERSONAL_ARTIFACTS_DIR, model_id), ignore_errors=True
        )
    assert outcome["skipped"] is None
    for name, spy in spies.items():
        assert spy.call_count == 0, name

    db = TestSession()
    try:
        types = {row.job_type for row in db.query(LearningJob).all()}
        sessions = {
            "research": db.query(LearningJob)
            .filter(LearningJob.job_type == "daily_research").count()
        }
    finally:
        db.close()
    assert types == {de.DAILY_ESTIMATE_JOB_TYPE}
    assert sessions["research"] == 0
    assert _bar_count() == bars_before  # nothing was collected on the way past

    # The cron entry derived its own window from the clock rather than being
    # handed one, and served the one skill that was declared for it.
    job = _last_job(owner_id)
    assert job["config"]["window"] == de.estimate_window()
    assert job["config"]["scope"] == "daily"
    assert job["result"]["estimated"] == 1
    assert len(_served(owner_id)) == 1


def _owner_with_a_declared_scope_only():
    """An owner with a skill, a selection and a scope, built through the doors.

    Stands alone (no ``personal_env``) because this node must run without the
    TestClient's DB override: it drives the worker session only.

    Returns ``(owner_id, model_id)`` and leaves the artifact on disk, because the
    node still has to run the tick over it; the caller removes it. Deleting the
    artifact here would have the cron answer ``artifact_missing`` for a build the
    owner never lost.
    """
    from app.auth.jwt import get_password_hash
    from app.ml.personal_trainer import PERSONAL_TASK_TYPE
    from app.models.model_research import ModelResearchRecord, ModelType, ModelVersion
    from app.models.personal_model_selection import PersonalModelSelection
    from app.models.training import TrainingJob, TrainingJobStatus
    from app.services.personal_dataset import FEATURE_LOOKBACK_WINDOWS
    from tests.test_successful_inference import (
        _complete_training_job,
        seed_daily_bars,
        write_engineering_artifact,
        real_feature_names,
        real_feature_rows,
    )

    db = TestSession()
    from datetime import datetime, timedelta, timezone

    try:
        from app.models.user import User

        user = User(
            email=f"estimates-cron-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=get_password_hash("testpass123"),
            display_name="Cron owner",
        )
        db.add(user)
        db.flush()
        seed_daily_bars(db)
        feature_names, _ = real_feature_names(db)
        model_id, checksum = write_engineering_artifact(
            user.id, feature_names, real_feature_rows(db, feature_names)
        )
        job = _complete_training_job(db, user.id, checksum)
        record = ModelResearchRecord(
            owner_id=user.id, title="Cron personal model", purpose="personal_decision"
        )
        db.add(record)
        db.flush()
        version = ModelVersion(
            model_research_record_id=record.id,
            version_number=1,
            model_type=ModelType.personal_decision_model,
            model_specification_json={
                "task_type": PERSONAL_TASK_TYPE,
                "artifact_id": model_id,
                "artifact_sha256": checksum,
                "feature_names": feature_names,
                "training_job_id": str(job.id),
                "training_type": "personal_decision_model",
            },
        )
        db.add(version)
        db.flush()
        record.active_version_id = version.id
        portfolio = _create_skill_unit(db, user.id)
        db.add(
            PersonalModelSelection(
                owner_id=user.id,
                skill_unit_id=portfolio.id,
                record_id=record.id,
                version_id=version.id,
                action="select",
                rationale="cron-only fixture",
            )
        )
        db.add(
            PersonalizationConsent(
                owner_id=user.id,
                purpose=de.DAILY_ESTIMATE_PURPOSE,
                consent_version=de.DAILY_ESTIMATE_CONSENT_VERSION,
                scope_json={
                    "skill_units": [
                        {
                            "skill_unit_id": str(portfolio.id),
                            "instrument_ids": [TEST_INSTRUMENT],
                            "max_data_age_days": None,
                        }
                    ]
                },
                granted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        db.commit()
        return user.id, model_id
    finally:
        db.close()
