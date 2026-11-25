"""The 03:00-UTC replay tick: what it steps, what it refuses, what it writes.

``POST /api/v1/simulations/{id}/advance`` has always stepped a replay, and the
learning service has always had a ledger, a UTC clock, a kill switch and a
same-day guard — but nothing connected them: a session stayed where the owner
last clicked unless they came back the next day. These tests drive the third
tick (``app.workers.learning_scheduler._advance_for_owner``) and assert the rows
it leaves behind, for the same reason the research ticks do: a tick that
returned without raising says nothing about whether it stepped the right
account, attributed the fill to itself, or counted a refusal as a success.

Three claims get their own test rather than riding along with a green suite:

* the fill is booked by the same function the owner's button runs, so the two
  callers cannot grow into two replay implementations;
* the row it writes is attributed ``system``, because ``human`` is a claim about
  a credential this tick does not hold;
* the stop control it obeys is the execution switch, and the guard that keeps a
  restart from re-stepping a session reads the ledger rather than a flag.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.lib.timeutil import utcnow_aware
from app.models.audit_event import AuditEvent
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
)
from app.models.simulation import SimulationDecision, SimulationSession, SimulationStatus
from app.services.kill_switch import activate_kill_switch
from app.workers import learning_scheduler as ls
from tests.conftest import TestSession


INSTRUMENT = "US:AAPL"
STRANDED = "US:MSFT"
WINDOW = ["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05", "2025-06-06"]
PRICES = [211.0, 212.0, 213.0, 214.0, 215.0]
START, END = WINDOW[0], WINDOW[-1]
CASH = 10000.0


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------

def _store_bars(instrument, days, prices):
    db = TestSession()
    try:
        for day_str, price in zip(days, prices):
            day = date.fromisoformat(day_str)
            db.add(MarketDataBar(
                instrument_id=instrument,
                provider_id="demo",
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1_000_000,
            ))
        db.commit()
    finally:
        db.close()


def _erase_bars(instrument):
    db = TestSession()
    try:
        db.query(MarketDataBar).filter(
            MarketDataBar.instrument_id == instrument
        ).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _stored_dataset(setup_db):
    """A price ladder the demo fixture does not contain.

    The replay reads these rows, so a tick that stepped off another series would
    land on a date this file never stored rather than passing quietly.
    """
    _store_bars(INSTRUMENT, WINDOW, PRICES)
    yield


def _user_id_of(headers):
    """Decode the token the same way the server does — signature verified."""
    from jose import jwt

    from app.config import get_settings

    settings = get_settings()
    token = headers["Authorization"].split(" ")[1]
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    return uuid.UUID(payload["sub"])


def _identity(client, email):
    client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": email,
        "password": "testpass123",
        "display_name": email.split("@")[0],
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": email, "password": "testpass123",
    })
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return {"client": client, "headers": headers, "user_id": _user_id_of(headers)}


@pytest.fixture()
def owner(client):
    return _identity(client, "replay-owner@alethra.io")


def _replay(account, *, instrument=INSTRUMENT, start=START, end=END):
    """Draft → lock → replay session: the three calls the research UI makes."""
    client, headers = account["client"], account["headers"]
    created = client.post("/api/v1/experiments/", headers=headers, json={
        "title": f"Daily tick replay {instrument}",
        "research_type": "quantitative",
        "strategy_spec": {
            "template_type": "buy_and_hold",
            "asset_ids": [instrument],
            "start_date": start,
            "end_date": end,
            "initial_cash": CASH,
        },
    })
    assert created.status_code == 201, created.text
    experiment = created.json()

    locked = client.put(
        f"/api/v1/experiments/{experiment['id']}",
        headers=headers,
        json={"scope": {"status": "locked"}},
    )
    assert locked.status_code == 200, locked.text

    session = client.post(
        f"/api/v1/simulations/versions/{experiment['current_version_id']}",
        headers=headers,
        json={"initial_virtual_cash": CASH, "provider_id": "demo"},
    )
    assert session.status_code == 201, session.text
    return uuid.UUID(session.json()["id"])


def _run_tick(*, scope="manual", user_id=None, job_id=None):
    return ls.run_scheduled_simulations(scope=scope, user_id=user_id, job_id=job_id)


def _session_row(session_id):
    db = TestSession()
    try:
        row = db.get(SimulationSession, session_id)
        if row is None:
            return None
        return SimpleNamespace(
            status=getattr(row.status, "value", row.status),
            cursor=row.current_snapshot_date,
            total_decisions=row.total_decisions,
            timeframe=row.timeframe,
        )
    finally:
        db.close()


def _decision_rows(session_id):
    db = TestSession()
    try:
        return [
            SimpleNamespace(
                step_number=row.step_number,
                snapshot_date=row.snapshot_date,
                decision_type=getattr(row.decision_type, "value", row.decision_type),
                detail=dict(row.decision_detail or {}),
                paper_cash=row.paper_cash,
                paper_equity=row.paper_equity,
            )
            for row in db.query(SimulationDecision)
            .filter(SimulationDecision.session_id == session_id)
            .order_by(SimulationDecision.step_number)
            .all()
        ]
    finally:
        db.close()


def _step_events(session_id):
    db = TestSession()
    try:
        return [
            SimpleNamespace(
                actor_id=row.actor_id,
                actor_type=row.actor_type,
                event_type=row.event_type,
                payload=dict(row.payload_json or {}),
            )
            for row in db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "simulation",
                AuditEvent.entity_id == str(session_id),
                AuditEvent.event_type.in_(
                    ("simulation_advanced", "simulation_completed")
                ),
            )
            .order_by(AuditEvent.created_at)
            .all()
        ]
    finally:
        db.close()


def _tick_jobs(user_id=None):
    """Plain values from the ledger, so the assertions survive the session closing."""
    db = TestSession()
    try:
        query = db.query(LearningJob).filter(
            LearningJob.job_type == ls.SIMULATION_JOB_TYPE
        )
        if user_id is not None:
            query = query.filter(LearningJob.user_id == user_id)
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "status": row.status,
                "job_type": row.job_type,
                "config_json": dict(row.config_json or {}),
                "result_json": dict(row.result_json or {}),
                "error_details": row.error_details,
            }
            for row in query.order_by(LearningJob.created_at).all()
        ]
    finally:
        db.close()


def _patch_status(session_id, status):
    """Move a session to a status the owner chooses in the UI, not one invented here."""
    db = TestSession()
    try:
        db.get(SimulationSession, session_id).status = status
        db.commit()
    finally:
        db.close()


def _patch_time(session_id, *, created_at=None, timeframe=None):
    db = TestSession()
    try:
        row = db.get(SimulationSession, session_id)
        if created_at is not None:
            row.created_at = created_at
        if timeframe is not None:
            row.timeframe = timeframe
        db.commit()
    finally:
        db.close()


def _pending_request(user_id):
    """The row ``POST /learning/jobs/trigger`` records before it dispatches."""
    db = TestSession()
    try:
        job = LearningJob(
            user_id=user_id,
            status="pending",
            job_type=ls.SIMULATION_JOB_TYPE,
            config_json={"scope": "manual", "job_type": ls.SIMULATION_JOB_TYPE},
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _activate(scope):
    db = TestSession()
    try:
        activate_kill_switch(db, scope, "test", reason="maintenance")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# the schedule: a third job, on the clock the module documents
# ---------------------------------------------------------------------------

def test_the_replay_tick_is_registered_behind_the_research_tick():
    scheduler = ls.create_learning_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert {"daily_research", "daily_simulation", "weekly_analysis"} <= set(jobs)
    assert jobs["daily_simulation"].func is ls.run_scheduled_simulations
    assert jobs["daily_simulation"].kwargs == {"scope": "daily"}


def _next_fire(job_id, reference):
    scheduler = ls.create_learning_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.id == job_id)
    return job.trigger.get_next_fire_time(None, reference)


def test_the_replay_tick_fires_at_03_00_utc_not_03_00_on_the_host_clock():
    """The defect the research tick was pinned for, now checked on the third job.

    A bare ``CronTrigger(hour=3)`` resolves against the host timezone — measured
    on this machine as Asia/Hong_Kong — which would fire this tick at 19:00 UTC,
    eight hours from the documented minute and behind the weekly analysis job
    instead of ahead of it.
    """
    reference = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)

    fire = _next_fire("daily_simulation", reference)
    assert fire.utcoffset() == timedelta(0), fire
    assert fire.astimezone(timezone.utc) == datetime(
        2026, 11, 17, ls.SIMULATION_HOUR_UTC, 0, tzinfo=timezone.utc
    )
    assert fire > _next_fire("daily_research", reference), (
        "the replay ran before the research tick that precedes it"
    )


# ---------------------------------------------------------------------------
# the step: one bar per open session, attributed to the machine
# ---------------------------------------------------------------------------

def test_the_tick_steps_the_replay_and_records_the_step_it_took(owner):
    session_id = _replay(owner)

    outcome = _run_tick(user_id=owner["user_id"])

    assert outcome["skipped"] is None
    assert len(outcome["jobs"]) == 1
    assert outcome["jobs"][0]["user_id"] == str(owner["user_id"])
    assert outcome["jobs"][0]["status"] == "completed"

    rows = _tick_jobs()
    assert len(rows) == 1
    job = rows[0]
    assert job["job_type"] == ls.SIMULATION_JOB_TYPE
    assert job["user_id"] == owner["user_id"]
    assert job["status"] == "completed"
    assert job["error_details"] in (None, "")
    assert job["config_json"]["statuses_served"] == ["pending", "active"]
    result = job["result_json"]
    assert result["scheduled_for"] is None
    assert result["sessions_offered"] == 1
    assert result["advanced"] == 1
    assert result["exhausted"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 0
    assert result["errors"] == []
    assert [(e["session_id"], e["outcome"], e["step_number"], e["snapshot_date"])
            for e in result["results"]] == [(str(session_id), "advanced", 1, WINDOW[0])]

    stepped = _session_row(session_id)
    assert stepped.cursor == WINDOW[0]
    assert stepped.status == "active", "pending must become active on the first step"
    assert stepped.total_decisions == 1

    decisions = _decision_rows(session_id)
    assert len(decisions) == 1
    assert decisions[0].detail["execution_price"] == pytest.approx(PRICES[0], abs=1e-6)


def test_the_tick_books_the_step_as_system_not_as_the_owner(owner):
    """``system`` is the only claim a caller with no credential may make.

    ``decision_actor_type`` reserves it for rows a background executor writes;
    labelling this tick ``human`` would put a step nobody clicked into the
    owner's provenance chain, and the product's "AI suggests, the user confirms"
    contract depends on those two staying tellable apart afterwards.
    """
    session_id = _replay(owner)

    _run_tick(user_id=owner["user_id"])

    events = _step_events(session_id)
    assert [(e.actor_type, e.actor_id) for e in events] == [
        ("system", owner["user_id"])
    ]
    assert events[0].payload["step_number"] == 1
    assert events[0].payload["snapshot_date"] == WINDOW[0]


def test_the_tick_and_the_owner_s_button_book_the_same_fill(owner):
    """One replay implementation, not two that happen to agree today.

    Same locked version, same stored bars: one session advanced by the HTTP
    route, the other by the tick. Every priced field of the decision has to
    match, while the two audit rows must still be tellable apart.
    """
    session_id = _replay(owner)
    ticked_id = _replay(owner)

    button = owner["client"].post(
        f"/api/v1/simulations/{session_id}/advance", headers=owner["headers"]
    )
    assert button.status_code == 200, button.text
    # Freeze the button's session so the tick below steps only the other one.
    _patch_status(session_id, SimulationStatus.paused)

    _run_tick(user_id=owner["user_id"])

    from_button, from_tick = _decision_rows(session_id), _decision_rows(ticked_id)
    assert len(from_button) == len(from_tick) == 1
    for field in ("step_number", "snapshot_date", "decision_type",
                  "paper_cash", "paper_equity"):
        assert getattr(from_button[0], field) == getattr(from_tick[0], field), field
    assert from_button[0].detail == from_tick[0].detail
    assert from_tick[0].detail["execution_price"] == pytest.approx(PRICES[0], abs=1e-6)

    assert [e.actor_type for e in _step_events(ticked_id)] == ["system"]
    assert [e.actor_type for e in _step_events(session_id)] != ["system"]
    assert _session_row(session_id).cursor == WINDOW[0]


def test_the_tick_moves_the_paper_account_bound_to_the_replay(owner):
    """A step that only wrote a replay log would leave the skill unit idle.

    ``advance_daily_step`` mirrors each rule fill into the linked portfolio, so
    what is asserted here is the account — order, fill, cash — because that is
    the difference between a daily paper service and a daily log line.
    """
    session_id = _replay(owner)
    created = owner["client"].post("/api/v1/paper-portfolio", headers=owner["headers"], json={
        "initial_virtual_cash": CASH, "currency": "USD",
        "simulation_session_id": str(session_id),
    })
    assert created.status_code == 201, created.text
    portfolio_id = uuid.UUID(created.json()["id"])

    _run_tick(user_id=owner["user_id"])

    decision = _decision_rows(session_id)[0]
    db = TestSession()
    try:
        order = db.query(PaperOrder).filter(PaperOrder.portfolio_id == portfolio_id).one()
        fill = db.query(PaperFill).filter(PaperFill.paper_order_id == order.id).one()
        cash = db.get(PaperPortfolio, portfolio_id).current_virtual_cash
    finally:
        db.close()

    assert order.idempotency_key == f"sim-{session_id}-step-1", (
        "a step booked under another key can be booked twice"
    )
    assert fill.quantity == pytest.approx(decision.detail["quantity"], abs=1e-9)
    assert fill.execution_price_before_costs == pytest.approx(
        decision.detail["execution_price"], abs=1e-6
    )
    assert cash < CASH, "the replay traded but the account did not"


# ---------------------------------------------------------------------------
# who is served: the owner with an open replay, and only that owner
# ---------------------------------------------------------------------------

def test_the_tick_serves_the_owner_of_the_open_replay(client, owner):
    """A paper account with no replay is not a claim on this lane's writes.

    The research lane asks who holds a skill account; this lane asks who has a
    session to step, so the two resolvers must not be interchangeable — the
    first account below holds a portfolio and gets nothing, and would have
    absorbed the tick under a lane that fanned out from the wrong table.
    """
    idle = TestSession()
    try:
        idle.add(PaperPortfolio(
            owner_id=owner["user_id"], currency="USD",
            initial_virtual_cash=CASH, current_virtual_cash=CASH,
            status=PortfolioStatus.active,
        ))
        idle.commit()
    finally:
        idle.close()

    second = _identity(client, "replay-second@alethra.io")
    served_id = _replay(second)

    outcome = _run_tick(scope="daily")

    rows = _tick_jobs()
    assert [row["user_id"] for row in rows] == [second["user_id"]]
    assert outcome["jobs"][0]["user_id"] == str(second["user_id"])
    assert _session_row(served_id).cursor == WINDOW[0]


@pytest.mark.parametrize(
    "status",
    [
        SimulationStatus.paused,
        SimulationStatus.completed,
        SimulationStatus.retired,
    ],
)
def test_a_replay_the_owner_stopped_is_not_stepped(owner, status):
    """The three statuses ``POST /advance`` itself refuses, and NULL is not served.

    Stepping a paused session would be the service overriding the owner's own
    decision to stop the replay; re-stepping a completed or retired one would
    add decisions to a book the owner closed.
    """
    session_id = _replay(owner)
    _patch_status(session_id, status)

    outcome = _run_tick(scope="daily")

    assert outcome["skipped"] == "no_accounts_served"
    assert _tick_jobs() == []
    assert _session_row(session_id).cursor is None
    assert _decision_rows(session_id) == []


def test_the_tick_serves_at_most_the_configured_number_of_accounts(client, owner):
    """The same owner ceiling the research tick honours, applied to this lane."""
    second = _identity(client, "replay-ceiling@alethra.io")
    owner_id = _replay(owner)
    second_id = _replay(second)
    expected = min(owner["user_id"], second["user_id"])

    settings = ls.get_settings()
    original = settings.learning_scheduler_max_owners
    settings.learning_scheduler_max_owners = 1
    try:
        _run_tick(scope="daily")
    finally:
        settings.learning_scheduler_max_owners = original

    assert [row["user_id"] for row in _tick_jobs()] == [expected]
    stepped, held = (owner_id, second_id) if expected == owner["user_id"] else (second_id, owner_id)
    assert _session_row(stepped).cursor == WINDOW[0]
    assert _session_row(held).cursor is None


def test_one_tick_steps_at_most_the_bounded_number_of_sessions(owner):
    """A 21st open replay waits for tomorrow rather than owning the afternoon.

    The ceiling is per account, so the oldest sessions by creation order go
    first and the record names how many it was offered.
    """
    session_ids = [_replay(owner) for _ in range(3)]
    base = datetime(2026, 1, 1)
    for index, session_id in enumerate(session_ids):
        _patch_time(session_id, created_at=base + timedelta(minutes=index))

    with patch.object(ls, "SIMULATION_SESSIONS_PER_OWNER", 2):
        _run_tick(user_id=owner["user_id"])

    job = _tick_jobs()[0]
    assert job["config_json"]["sessions_per_owner"] == 2
    assert job["result_json"]["sessions_offered"] == 2
    assert job["result_json"]["advanced"] == 2
    assert [_session_row(sid).cursor for sid in session_ids] == [
        WINDOW[0], WINDOW[0], None,
    ]


# ---------------------------------------------------------------------------
# dedup: one scheduled step per account per day
# ---------------------------------------------------------------------------

def test_a_restart_the_same_day_does_not_step_the_same_bar_twice(owner):
    """Every API worker starts its own scheduler, so the guard reads the ledger.

    A restart at 03:05 would otherwise walk the replay two bars for one day and
    leave a journal the owner cannot line up with a calendar.
    """
    session_id = _replay(owner)

    first = _run_tick(scope="daily")
    outcome = _run_tick(scope="daily")

    assert first["skipped"] is None
    assert outcome["skipped"] == "already_ran_today"
    assert len(_tick_jobs()) == 1
    assert [_session_row(session_id).cursor] == [WINDOW[0]]
    assert len(_decision_rows(session_id)) == 1


def test_the_daily_guard_does_not_veto_an_owner_triggered_step(client, owner):
    """The guard exists to stop a restart storm, not to refuse the account holder."""
    second = _identity(client, "replay-manual@alethra.io")
    session_id = _replay(second)

    _run_tick(scope="daily")
    _run_tick(scope="manual", user_id=second["user_id"])

    rows = _tick_jobs()
    assert [row["status"] for row in rows] == ["completed", "completed"]
    assert [row["config_json"].get("scheduled_for") for row in rows] == [
        utcnow_aware().date().isoformat(), None,
    ]
    assert _session_row(session_id).cursor == WINDOW[1]
    assert len(_decision_rows(session_id)) == 2


# ---------------------------------------------------------------------------
# stop: which switch halts a tick that writes to the owner's book
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "scope",
    [KillSwitchScope.global_, KillSwitchScope.execution],
)
def test_a_stop_switch_halts_the_tick_before_any_write(owner, scope):
    """Refusing writes no row: a ledger padded with skipped ticks is not evidence.

    The global switch stops every lane; the execution switch is the one this
    lane obeys, because a rule fill booked into a paper account is execution
    work even though no real order leaves the platform.
    """
    session_id = _replay(owner)
    _activate(scope)

    outcome = _run_tick(scope="daily")

    assert outcome["skipped"] == "kill_switch_active"
    assert outcome["jobs"] == []
    assert _tick_jobs() == []
    assert _session_row(session_id).cursor is None
    assert _decision_rows(session_id) == []
    assert _step_events(session_id) == []


def test_the_replay_lane_and_the_research_lane_obey_different_switches(owner):
    """A stop control that stops nothing, or everything, is not a control.

    Training work and booked fills are different acts on this platform, so the
    switch for one must not silently veto the other — and neither may run while
    the platform-wide switch is on.
    """
    session_id = _replay(owner)
    _activate(KillSwitchScope.training)

    with patch.object(ls, "_run_single_study", side_effect=RuntimeError("must not run")):
        research = ls.run_scheduled_research(scope="manual", user_id=owner["user_id"])
    replay = _run_tick(scope="manual", user_id=owner["user_id"])

    assert research["skipped"] == "kill_switch_active"
    assert replay["skipped"] is None
    assert _session_row(session_id).cursor == WINDOW[0]

    _activate(KillSwitchScope.global_)
    assert _run_tick(scope="manual", user_id=owner["user_id"])["skipped"] == (
        "kill_switch_active"
    )
    assert _session_row(session_id).cursor == WINDOW[0], "a stopped tick still stopped"


# ---------------------------------------------------------------------------
# the terminal status is the truth of the tick
# ---------------------------------------------------------------------------

def test_a_replay_with_no_bars_fails_the_row_and_names_the_session(owner):
    """``failed`` means nothing was stepped — not that the call returned."""
    session_id = _replay(owner)
    _erase_bars(INSTRUMENT)

    _run_tick(user_id=owner["user_id"])

    job = _tick_jobs()[0]
    assert job["status"] == "failed"
    assert job["result_json"]["sessions_offered"] == 1
    assert job["result_json"]["advanced"] == 0
    assert job["result_json"]["failed"] == 1
    errors = job["result_json"]["errors"]
    assert len(errors) == 1
    assert errors[0].startswith(f"{session_id}: ")
    assert "no stored bars" in errors[0]
    assert "no stored bars" in job["error_details"]
    assert _session_row(session_id).cursor is None
    assert _decision_rows(session_id) == []


def test_one_unreadable_replay_does_not_stop_the_others(owner):
    """The rest of the account's book still gets its step, and the row says partial."""
    good_id = _replay(owner)
    _store_bars(STRANDED, WINDOW, [301.0, 302.0, 303.0, 304.0, 305.0])
    stranded_id = _replay(owner, instrument=STRANDED)
    _erase_bars(STRANDED)

    _run_tick(user_id=owner["user_id"])

    job = _tick_jobs()[0]
    assert job["status"] == "partial", job["status"]
    assert job["result_json"]["advanced"] == 1
    assert job["result_json"]["failed"] == 1
    errors = job["result_json"]["errors"]
    assert len(errors) == 1 and errors[0].startswith(f"{stranded_id}: ")
    assert [e["session_id"] for e in job["result_json"]["results"]] == [str(good_id)]
    assert _session_row(good_id).cursor == WINDOW[0]
    assert _session_row(stranded_id).cursor is None


def test_running_out_of_dataset_is_counted_as_work_done(owner):
    """Exhaustion is a state the session reaches, not a refusal by the service.

    The HTTP caller has always been handed a 400 for it; the tick reads the
    structured outcome instead, so the row must not turn into ``failed`` just
    because the replay finished. The window holds two stored bars, so the third
    tick is the one that finds nothing left to step.
    """
    last_bar = WINDOW[1]
    session_id = _replay(owner, start=WINDOW[0], end=last_bar)

    ticks = [_run_tick(user_id=owner["user_id"]) for _ in range(3)]

    assert [_outcomes(r) for r in ticks] == [
        ["advanced"], ["advanced"], ["completed"],
    ]
    rows = _tick_jobs()
    assert [row["status"] for row in rows] == ["completed"] * 3
    assert rows[2]["result_json"]["exhausted"] == 1
    assert rows[2]["result_json"]["advanced"] == 0
    assert rows[2]["result_json"]["sessions_offered"] == 1
    assert rows[2]["result_json"]["errors"] == []
    assert rows[2]["error_details"] in (None, "")
    assert _session_row(session_id).status == "completed"
    assert _session_row(session_id).cursor == last_bar
    assert [(e.actor_type, e.event_type) for e in _step_events(session_id)] == [
        ("system", "simulation_advanced"),
        ("system", "simulation_advanced"),
        ("system", "simulation_completed"),
    ]


def _outcomes(outcome):
    return [
        _job_result(entry["job_id"])["results"][0]["outcome"]
        for entry in outcome["jobs"]
    ]


def _job_result(job_id):
    db = TestSession()
    try:
        return dict(db.get(LearningJob, uuid.UUID(str(job_id))).result_json or {})
    finally:
        db.close()


def test_an_intraday_replay_is_named_in_the_record_not_stepped_by_a_daily_clock(owner):
    """One tick per day cannot drive a bar clock, so the lane says so and moves on.

    The timeframe is written onto the session row rather than authored through
    ``POST /experiments``: what is under test is which sessions this reader
    offers, exactly as ``test_daily_replay_reads_stored_bars`` separates the
    reader tier from the request tier.
    """
    session_id = _replay(owner)
    _patch_time(session_id, timeframe="5m")

    _run_tick(user_id=owner["user_id"])

    job = _tick_jobs()[0]
    assert job["status"] == "completed"
    assert job["result_json"]["sessions_offered"] == 1
    assert job["result_json"]["advanced"] == 0
    assert job["result_json"]["skipped"] == 1
    assert job["result_json"]["errors"] == []
    assert job["result_json"]["skipped_detail"] == [
        {"session_id": str(session_id), "timeframe": "5m",
         "reason": "intraday_timeframe_needs_a_bar_clock"}
    ]
    assert _session_row(session_id).cursor is None
    assert _decision_rows(session_id) == []


# ---------------------------------------------------------------------------
# one record per request: the tick finishes the row the trigger wrote
# ---------------------------------------------------------------------------

def test_a_triggered_step_advances_its_own_row_instead_of_adding_one(owner):
    _replay(owner)
    request_id = _pending_request(owner["user_id"])

    _run_tick(user_id=owner["user_id"], job_id=request_id)

    rows = _tick_jobs()
    assert len(rows) == 1, [row["status"] for row in rows]
    assert rows[0]["id"] == request_id
    assert rows[0]["status"] == "completed"
    assert rows[0]["config_json"]["scope"] == "manual"
    assert "scheduled_for" not in rows[0]["config_json"]


def test_another_account_s_row_is_never_adopted(client, owner):
    stranger = _identity(client, "replay-stranger@alethra.io")
    stranger_row = _pending_request(stranger["user_id"])
    _replay(owner)

    _run_tick(user_id=owner["user_id"], job_id=stranger_row)

    db = TestSession()
    try:
        assert db.get(LearningJob, stranger_row).status == "pending"
    finally:
        db.close()
    rows = _tick_jobs(owner["user_id"])
    assert len(rows) == 1 and rows[0]["id"] != stranger_row
    assert rows[0]["status"] == "completed"


def test_a_refused_step_closes_the_row_the_endpoint_promised(owner):
    """A 201 that nothing will ever advance is a lie about the queue.

    The trigger endpoint already recorded the request before dispatching, so the
    refusal is written into that row instead of being left queued behind a
    service that is switched off.
    """
    session_id = _replay(owner)
    request_id = _pending_request(owner["user_id"])
    _activate(KillSwitchScope.execution)

    outcome = _run_tick(user_id=owner["user_id"], job_id=request_id)

    assert outcome["skipped"] == "kill_switch_active"
    rows = _tick_jobs()
    assert len(rows) == 1
    assert rows[0]["id"] == request_id
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_details"] == "refused: kill_switch_active"
    assert rows[0]["result_json"] == {}
    assert _session_row(session_id).cursor is None


# ---------------------------------------------------------------------------
# what the owner can see: GET /simulations/{id}/daily-service
#
# The tick changes a replay while the owner is not looking, so the workspace has
# to answer the same questions the worker asks itself — am I enrolled, on what
# clock, why not, and what happened last. These tests read the endpoint rather
# than the helper because the claim under test is that the page and the tick
# agree, and a page that derives its answer somewhere else would pass here.
# ---------------------------------------------------------------------------

def _service(account, session_id):
    resp = account["client"].get(
        f"/api/v1/simulations/{session_id}/daily-service",
        headers=account["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _in_flight_row(user_id):
    """A row the tick opened before it stepped anything: running, no outcome yet."""
    db = TestSession()
    try:
        job = LearningJob(
            user_id=user_id,
            status="running",
            job_type=ls.SIMULATION_JOB_TYPE,
            config_json={
                "scope": "daily",
                "scheduled_for": utcnow_aware().date().isoformat(),
            },
            created_at=utcnow_aware() + timedelta(minutes=5),
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def test_an_untouched_replay_is_offered_the_cadence_the_worker_uses(owner):
    """Every schedule number in the response is the constant the tick reads.

    A status card that hardcoded "03:00" would keep telling the owner the truth
    for one hour and then lie, so the test changes nothing and checks the read
    against ``ls`` directly.
    """
    from app.main import _learning_scheduler

    # The claim below is only meaningful if this process really has no service.
    assert _learning_scheduler is None

    session_id = _replay(owner)
    body = _service(owner, session_id)

    assert body["cadence_hour_utc"] == ls.SIMULATION_HOUR_UTC
    assert body["cadence_timezone"] == ls.SERVICE_TIMEZONE
    assert body["job_type"] == ls.SIMULATION_JOB_TYPE
    assert body["statuses_served"] == [s.value for s in ls.SIMULATION_SERVED_STATUSES]
    assert body["batch_limit"] == ls.SIMULATION_SESSIONS_PER_OWNER
    assert body["enrolled"] is True
    assert body["enrolment_reason"] is None
    assert body["queue_position"] == 0
    assert body["in_next_batch"] is True
    assert body["last_run"] is None
    assert body["in_flight"] is False
    assert body["kill_switch"] == "inactive"
    assert body["runs_in_this_process"] is False
    assert body["next_run_at"] is None


def test_the_read_names_the_tick_that_last_stepped_this_replay(owner):
    session_id = _replay(owner)
    _run_tick(scope="daily", user_id=owner["user_id"])

    body = _service(owner, session_id)
    row = _tick_jobs()[0]

    assert body["last_run"]["job_id"] == str(row["id"])
    assert body["last_run"]["job_status"] == "completed"
    assert body["last_run"]["scope"] == "daily"
    assert body["last_run"]["scheduled_for"] == row["config_json"]["scheduled_for"]
    assert body["last_run"]["outcome"] == "advanced"
    assert body["last_run"]["step_number"] == 1
    assert body["last_run"]["snapshot_date"] == WINDOW[0]
    assert body["last_run"]["detail"] is None
    # The step committed, so the state and the history agree about the cursor.
    assert _session_row(session_id).cursor == WINDOW[0]


def test_pausing_is_the_stop_control_and_resuming_is_the_start_one(client, owner):
    """The owner's own buttons, not a second switch the page invents.

    ``pause`` writes the status the tick filters on, so the service that stops
    for the button and the service that reads this page cannot disagree.
    """
    session_id = _replay(owner)
    _run_tick(user_id=owner["user_id"])

    paused = owner["client"].post(
        f"/api/v1/simulations/{session_id}/pause", headers=owner["headers"]
    )
    assert paused.status_code == 200, paused.text

    body = _service(owner, session_id)
    assert body["session_status"] == "paused"
    assert body["enrolled"] is False
    assert body["enrolment_reason"] == "paused"
    # A stopped replay has no position in a queue it is not in, but the history
    # of the step it did take stays readable.
    assert body["queue_position"] is None
    assert body["last_run"]["outcome"] == "advanced"

    resumed = owner["client"].post(
        f"/api/v1/simulations/{session_id}/resume", headers=owner["headers"]
    )
    assert resumed.status_code == 200, resumed.text
    assert _service(owner, session_id)["enrolled"] is True


@pytest.mark.parametrize(
    "final_status", [SimulationStatus.completed, SimulationStatus.retired]
)
def test_a_finished_replay_is_reported_as_finished_not_waiting(
    owner, final_status
):
    session_id = _replay(owner)
    _patch_status(session_id, final_status)

    body = _service(owner, session_id)

    assert body["session_status"] == final_status.value
    assert body["enrolled"] is False
    assert body["enrolment_reason"] == final_status.value
    assert body["last_run"] is None


def test_an_intraday_replay_is_reported_as_waiting_for_a_bar_clock(owner):
    """The one case where the replay is open, eligible and still not stepped."""
    session_id = _replay(owner)
    _patch_time(session_id, timeframe="5m")

    _run_tick(user_id=owner["user_id"])
    body = _service(owner, session_id)

    assert body["timeframe"] == "5m"
    assert body["enrolled"] is False
    assert body["enrolment_reason"] == "intraday_timeframe_needs_a_bar_clock"
    assert body["last_run"]["outcome"] == "skipped"
    assert body["last_run"]["detail"] == "intraday_timeframe_needs_a_bar_clock"


def test_a_step_that_failed_its_data_is_reported_with_the_tick_s_reason(owner):
    session_id = _replay(owner)
    _erase_bars(INSTRUMENT)

    _run_tick(user_id=owner["user_id"])
    body = _service(owner, session_id)

    assert body["enrolled"] is True
    assert body["last_run"]["job_status"] == "failed"
    assert body["last_run"]["outcome"] == "failed"
    assert "no stored bars" in body["last_run"]["detail"]
    assert body["last_run"]["step_number"] is None


def test_a_replay_beyond_the_batch_is_deferred_rather_than_refused(owner):
    """The 21st open replay is told "tomorrow", not "no".

    The limit the page quotes is the limit the tick reads, so the deferral
    cannot be reported against a number the worker never used.
    """
    first, second = _replay(owner), _replay(owner)
    base = datetime(2026, 1, 1)
    _patch_time(first, created_at=base)
    _patch_time(second, created_at=base + timedelta(minutes=1))

    with patch.object(ls, "SIMULATION_SESSIONS_PER_OWNER", 1):
        _run_tick(user_id=owner["user_id"])
        held = _service(owner, second)
        served = _service(owner, first)

    assert held["batch_limit"] == 1
    assert held["enrolled"] is True
    assert held["queue_position"] == 1
    assert held["in_next_batch"] is False
    assert held["last_run"] is None
    assert _session_row(second).cursor is None

    assert served["queue_position"] == 0
    assert served["in_next_batch"] is True
    assert served["last_run"]["outcome"] == "advanced"


def test_the_switch_that_stops_the_service_is_the_one_the_service_reads(owner):
    """The execution switch, not the training switch, and not a guess.

    The replay tick steps portfolios, so it obeys the switch that claims
    execution; a page that watched the training switch would show "running"
    over a tick that refuses to start.
    """
    session_id = _replay(owner)

    _activate(KillSwitchScope.training)
    assert _service(owner, session_id)["kill_switch"] == "inactive"

    _activate(KillSwitchScope.execution)
    body = _service(owner, session_id)
    assert body["kill_switch"] == "active"
    # Enrolment is about the replay's own row; the switch is reported beside it
    # rather than replacing it, so the owner sees which of the two to change.
    assert body["enrolled"] is True


def test_a_kill_switch_that_cannot_be_read_is_not_reported_as_off(owner, monkeypatch):
    def unreadable(*args, **kwargs):
        raise RuntimeError("database is not reachable")

    monkeypatch.setattr(
        "app.services.kill_switch.is_kill_switch_active", unreadable
    )
    session_id = _replay(owner)

    assert _service(owner, session_id)["kill_switch"] == "unreadable"


def test_a_tick_still_running_is_visible_without_borrowing_an_older_outcome(owner):
    session_id = _replay(owner)
    _run_tick(user_id=owner["user_id"])
    completed_job = _tick_jobs()[0]["id"]
    running_job = _in_flight_row(owner["user_id"])

    body = _service(owner, session_id)

    assert body["in_flight"] is True
    assert body["last_run"]["job_id"] == str(completed_job)
    assert body["last_run"]["job_id"] != str(running_job)
    assert body["last_run"]["outcome"] == "advanced"


def test_the_page_describes_only_the_process_that_answered(client, owner, monkeypatch):
    """``runs_in_this_process`` is a claim about one worker, and is tested as one.

    A deployment with several API workers has exactly one of them holding the
    interval service, so the field has to name the answering process and
    ``next_run_at`` has to come from the replay job rather than the research one.
    """
    import app.main as main_module

    session_id = _replay(owner)
    simulation_next = datetime(2026, 11, 18, ls.SIMULATION_HOUR_UTC, tzinfo=timezone.utc)
    jobs = [
        SimpleNamespace(id="daily_research", next_run_time=None),
        SimpleNamespace(id="daily_simulation", next_run_time=simulation_next),
    ]

    monkeypatch.setattr(
        main_module,
        "_learning_scheduler",
        SimpleNamespace(running=True, get_jobs=lambda: jobs),
    )
    body = _service(owner, session_id)
    assert body["runs_in_this_process"] is True
    assert body["next_run_at"] == simulation_next.isoformat()

    # A scheduler object that is not started runs nothing, and saying so is the
    # difference between "the service exists" and "the service will fire".
    monkeypatch.setattr(
        main_module, "_learning_scheduler", SimpleNamespace(running=False, get_jobs=lambda: [])
    )
    stopped = _service(owner, session_id)
    assert stopped["runs_in_this_process"] is False
    assert stopped["next_run_at"] is None


def test_another_account_cannot_read_a_replay_s_service_state(client, owner):
    stranger = _identity(client, "replay-curious@alethra.io")
    session_id = _replay(owner)

    resp = stranger["client"].get(
        f"/api/v1/simulations/{session_id}/daily-service",
        headers=stranger["headers"],
    )
    assert resp.status_code == 403, resp.text

