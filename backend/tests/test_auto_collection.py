"""Tests for the interval collection service (``app.services.auto_collection``).

The module used to be a second implementation of the research service with its
own copy of every control the daily lane had been fixed on. These tests pin the
behaviour the fork could not have:

1. work is attributed to the accounts the service serves — the fork wrote every
   study for one ``UUID('96f9ea41-…')`` from a development database, so on this
   host every row belonged to an account that does not exist;
2. the kill switch stops a collection tick before it spends venue quota;
3. two ticks inside one interval window study once, and the window they share
   is recorded on the row;
4. a person asking for a cycle is not suppressed by the clock's window;
5. the outcome reaches the ledger with the truth of the tick, instead of being
   returned to the caller as ``runs``/``trades`` and then dropped;
6. ``get_collection_history`` reads the ticks the service recorded. The fork's
   query filtered ``settings_json->>'source' = 'auto_collection'`` in
   PostgreSQL syntax against a column ``persist_study`` does not write, so it
   could only ever have answered with an empty list;
7. the interval trigger runs on the UTC clock the module documents, and the
   deployment's opt-in cannot be granted by an operator's POST;
8. start, stop and status describe the worker that answered: ``running`` is a
   process fact, and stopping a worker that was never started is reported as a
   stopped worker instead of an error.

The work itself — freshness, the terminal status, the owner ceiling — is the
shared tick's, and is pinned in ``test_learning_scheduler.py``.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.lib.timeutil import utcnow_aware
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.user import User
from app.services import auto_collection as ac
from app.services.kill_switch import activate_kill_switch
from app.workers import learning_scheduler as ls
from tests.conftest import TestSession


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _make_user(email: str) -> uuid.UUID:
    from app.auth.jwt import get_password_hash

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name="Collection Test",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _make_skill_account(owner_id: uuid.UUID) -> uuid.UUID:
    db = TestSession()
    try:
        portfolio = PaperPortfolio(
            owner_id=owner_id,
            currency="USD",
            initial_virtual_cash=100000.0,
            current_virtual_cash=100000.0,
            status=PortfolioStatus.active,
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        return portfolio.id
    finally:
        db.close()


def _jobs(owner_id: uuid.UUID = None) -> list:
    db = TestSession()
    try:
        query = db.query(LearningJob)
        if owner_id is not None:
            query = query.filter(LearningJob.user_id == owner_id)
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


def _ok_study(*_args, **_kwargs):
    return {
        "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
        "interval_minutes": 1440,
        "status": "studied",
        "reason": None,
        "trades": 4,
        "bars": 720,
        "data_nature": "venue_completed_bars",
        "fingerprint": "fp",
        "integrity_ok": True,
        "is_stale": False,
    }


# ---------------------------------------------------------------------------
# the window key
# ---------------------------------------------------------------------------

def test_collection_window_buckets_onto_the_service_clock():
    """One 4-hour interval, one window — no matter where inside it the tick starts."""
    inside = datetime(2026, 11, 17, 7, 59, tzinfo=timezone.utc)
    opening = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
    assert ac.collection_window(4, now=inside) == ac.collection_window(4, now=opening)
    assert ac.collection_window(4, now=inside) == "2026-11-17T04:00Z/4h"

    next_window = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)
    assert ac.collection_window(4, now=next_window) == "2026-11-17T08:00Z/4h"


def test_collection_window_refuses_an_interval_that_would_never_dedup():
    """``interval_hours=0`` names a new window every call, so dedup silently dies."""
    try:
        ac.collection_window(0)
    except ValueError as exc:
        assert "at least 1" in str(exc)
    else:
        raise AssertionError("a zero-length interval must be refused, not bucketed")


# ---------------------------------------------------------------------------
# 1 — attribution: the accounts served, not a constant
# ---------------------------------------------------------------------------

def test_a_cycle_studies_for_every_served_account():
    owner_a = _make_user("cycle-a@alethra.io")
    owner_b = _make_user("cycle-b@alethra.io")
    _make_skill_account(owner_a)
    _make_skill_account(owner_b)

    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        outcome = ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")

    assert study.call_count == 2 * combos
    assert outcome["skipped"] is None
    assert sorted(row["user_id"] for row in _jobs()) == sorted([owner_a, owner_b])
    assert all(row["job_type"] == ac.COLLECTION_JOB_TYPE for row in _jobs())


def test_the_cycle_is_attributed_to_the_owner_of_a_live_skill_account():
    """An account whose portfolio retired is no longer served, as in the daily lane."""
    owner = _make_user("retired-owner@alethra.io")
    portfolio_id = _make_skill_account(owner)

    db = TestSession()
    try:
        portfolio = db.get(PaperPortfolio, portfolio_id)
        portfolio.status = PortfolioStatus.retired
        db.commit()
    finally:
        db.close()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        outcome = ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")

    assert study.call_count == 0
    assert outcome["skipped"] == "no_accounts_served"
    assert _jobs() == []


# ---------------------------------------------------------------------------
# 2 — stop: the kill switch the fork never consulted
# ---------------------------------------------------------------------------

def test_an_active_kill_switch_refuses_the_cycle_before_any_row():
    owner = _make_user("cycle-kill@alethra.io")
    _make_skill_account(owner)
    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.global_, "test", reason="maintenance")
    finally:
        db.close()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        outcome = ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")

    assert study.call_count == 0
    assert outcome["skipped"] == "kill_switch_active"
    assert _jobs() == []


# ---------------------------------------------------------------------------
# 3/4 — the window dedups the clock, not the operator
# ---------------------------------------------------------------------------

def test_two_ticks_inside_one_window_study_once():
    owner = _make_user("cycle-dedup@alethra.io")
    _make_skill_account(owner)
    window = "2026-11-17T04:00Z/4h"

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        first = ac.run_collection_cycle(window=window)
        second = ac.run_collection_cycle(window=window)

    assert first["skipped"] is None
    assert second["skipped"] == "already_ran_today"
    assert study.call_count == len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
    rows = _jobs(owner)
    assert len(rows) == 1
    assert rows[0]["config_json"]["scheduled_for"] == window


def test_two_ticks_on_the_clock_dedup_through_the_window_they_share():
    """The production path: nobody passes a window, the clock supplies one.

    A key that varied per call — a timestamp, a random id — would let every
    worker on the host study the same bars, so this pins the two default ticks
    onto one bucket. ``collection_window`` is the only clock read here.
    """
    owner = _make_user("cycle-default-window@alethra.io")
    _make_skill_account(owner)
    settings = ac.get_settings()
    original_hours = settings.auto_collection_interval_hours
    settings.auto_collection_interval_hours = 4

    class _Clock:
        @staticmethod
        def now(_tz):
            return datetime(2026, 11, 17, 7, 30, tzinfo=timezone.utc)

    try:
        with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
            with patch.object(ac, "datetime", _Clock):
                first = ac.run_collection_cycle()
                second = ac.run_collection_cycle()
    finally:
        settings.auto_collection_interval_hours = original_hours

    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
    assert first["skipped"] is None
    assert second["skipped"] == "already_ran_today"
    assert study.call_count == combos
    assert _jobs()[0]["config_json"]["scheduled_for"] == "2026-11-17T04:00Z/4h"


def test_the_next_window_is_work_again():
    owner = _make_user("cycle-next-window@alethra.io")
    _make_skill_account(owner)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")
        outcome = ac.run_collection_cycle(window="2026-11-17T08:00Z/4h")

    assert outcome["skipped"] is None
    assert [row["config_json"]["scheduled_for"] for row in _jobs(owner)] == [
        "2026-11-17T04:00Z/4h",
        "2026-11-17T08:00Z/4h",
    ]


def test_a_manual_trigger_is_not_suppressed_by_the_window():
    owner = _make_user("cycle-manual@alethra.io")
    _make_skill_account(owner)
    window = "2026-11-17T04:00Z/4h"

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        ac.run_collection_cycle(window=window)
        outcome = ac.trigger_manual_collection()

    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
    assert study.call_count == 2 * combos
    assert outcome["skipped"] is None
    rows = _jobs(owner)
    assert [row["config_json"].get("scope") for row in rows] == [
        ac.COLLECTION_SCOPE,
        ls.MANUAL_SCOPE,
    ]
    assert "scheduled_for" not in rows[1]["config_json"]


def test_the_interval_service_dedups_apart_from_the_daily_cron():
    """A daily row already filed for today must not silence the interval tick.

    Both lanes are the same work on the same table; only ``job_type`` keeps
    their dedup windows and their history apart, so this pins the reason the
    service files under its own type instead of reusing ``scheduled_study``.
    """
    owner = _make_user("cycle-separate@alethra.io")
    _make_skill_account(owner)
    today = utcnow_aware().date().isoformat()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        assert ls.run_scheduled_research(scope="daily")["skipped"] is None
        outcome = ac.run_collection_cycle(window=today)

    assert outcome["skipped"] is None
    rows = _jobs(owner)
    assert [row["job_type"] for row in rows] == [ls.DAILY_JOB_TYPE, ac.COLLECTION_JOB_TYPE]


# ---------------------------------------------------------------------------
# 5 — the outcome is recorded, not returned and dropped
# ---------------------------------------------------------------------------

def test_a_failing_cycle_is_recorded_as_failed():
    owner = _make_user("cycle-failed@alethra.io")
    _make_skill_account(owner)
    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)

    with patch.object(ls, "_run_single_study", side_effect=RuntimeError("venue down")):
        ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")

    rows = _jobs(owner)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["result_json"]["failed"] == combos
    assert rows[0]["result_json"]["studied"] == 0
    assert "venue down" in rows[0]["error_details"]


# ---------------------------------------------------------------------------
# 6 — history reads the ledger
# ---------------------------------------------------------------------------

def test_history_returns_the_ticks_the_service_recorded():
    owner = _make_user("history@alethra.io")
    _make_skill_account(owner)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")
        ac.trigger_manual_collection()
        # A daily-cron row for the same account must not appear as a collection.
        ls.run_scheduled_research(scope="daily")

    history = ac.get_collection_history(limit=20)

    assert len(history) == 2, [row["window"] for row in history]
    assert {row["user_id"] for row in history} == {str(owner)}
    newest, oldest = history[0], history[1]
    assert oldest["window"] == "2026-11-17T04:00Z/4h"
    assert oldest["scope"] == ac.COLLECTION_SCOPE
    assert newest["scope"] == ls.MANUAL_SCOPE
    assert newest["window"] is None
    for row in history:
        assert row["status"] == "completed"
        assert row["studied"] == (
            len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
        )
        assert row["skipped"] == 0
        assert row["failed"] == 0
        assert row["errors"] == []
        assert row["completed_at"] and row["created_at"]


def test_history_honours_the_limit_and_works_on_sqlite():
    owner = _make_user("history-limit@alethra.io")
    _make_skill_account(owner)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        for hour in (0, 4, 8):
            ac.run_collection_cycle(window=f"2026-11-17T{hour:02d}:00Z/4h")

    assert len(_jobs(owner)) == 3
    assert len(ac.get_collection_history(limit=2)) == 2
    assert [row["window"] for row in ac.get_collection_history(limit=2)] == [
        "2026-11-17T08:00Z/4h",
        "2026-11-17T04:00Z/4h",
    ]


# ---------------------------------------------------------------------------
# 7 — the clock, and who may switch it on
# ---------------------------------------------------------------------------

def test_the_interval_trigger_is_pinned_to_utc():
    scheduler = ac.create_scheduler()
    scheduler.start()
    try:
        assert str(scheduler.timezone) == "UTC"
        job = scheduler.get_job("auto_collection_job")
        assert str(job.trigger.timezone) == "UTC"
        assert job.func is ac.run_collection_cycle
    finally:
        # Without this a failing assert leaves the scheduler thread firing
        # against a database whose fixture has already been torn down.
        scheduler.shutdown(wait=False)


def test_the_service_fires_at_the_start_of_the_window_it_dedups_on():
    """The fire time and the dedup key must name the same instant.

    A process-relative wait would let a worker started three hours after
    another fire minutes after it, in the *next* bucket — two studies of one
    window, and the second one invisible to the guard. So the default interval
    is expressed as an aligned grid, and this pins the property: the fire at
    04:00 UTC is the fire whose window key is ``…T04:00Z/4h``.
    """
    settings = ac.get_settings()
    original = settings.auto_collection_interval_hours
    settings.auto_collection_interval_hours = 4
    try:
        trigger = ac.create_scheduler().get_job("auto_collection_job").trigger
    finally:
        settings.auto_collection_interval_hours = original

    after_midnight = datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc)
    first = trigger.get_next_fire_time(None, after_midnight)
    assert first == datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)
    assert ac.collection_window(4, now=first) == "2026-11-17T04:00Z/4h"
    assert trigger.get_next_fire_time(first, first) - first == timedelta(hours=4)


def test_an_interval_that_does_not_divide_the_day_keeps_its_own_wait():
    """``*/5`` in cron restarts at midnight, which is not a 5-hour cadence.

    Such an interval keeps the process-relative wait instead of silently
    becoming 0/5/10/15/20 — the trade-off is documented in ``_build_trigger``:
    the bucket guard then covers restarts and duplicate triggers, and the
    freshness check inside the tick is what stops the unchanged bars.
    """
    settings = ac.get_settings()
    original = settings.auto_collection_interval_hours
    settings.auto_collection_interval_hours = 5
    try:
        trigger = ac.create_scheduler().get_job("auto_collection_job").trigger
    finally:
        settings.auto_collection_interval_hours = original

    now = datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc)
    first = trigger.get_next_fire_time(None, now)
    assert trigger.get_next_fire_time(first, now) - first == timedelta(hours=5)


def test_init_scheduler_refuses_when_the_deployment_declined():
    settings = ac.get_settings()
    original = settings.auto_collection_enabled
    settings.auto_collection_enabled = False
    try:
        assert ac.init_scheduler() is None
        assert ac.stop_scheduler() is False
    finally:
        settings.auto_collection_enabled = original


def test_status_separates_the_deployment_opt_in_from_this_process():
    settings = ac.get_settings()
    original_enabled = settings.auto_collection_enabled
    original_hours = settings.auto_collection_interval_hours
    settings.auto_collection_enabled = True
    settings.auto_collection_interval_hours = 6
    try:
        scheduler = ac.init_scheduler()
        assert scheduler is not None
        status = ac.get_scheduler_status()
        assert status["enabled"] is True
        assert status["running"] is True
        assert status["interval_hours"] == 6
        assert status["timezone"] == "UTC"
        assert status["job_type"] == ac.COLLECTION_JOB_TYPE
        assert status["next_run"] is not None

        # A second start is the same service, not a second scheduler.
        assert ac.init_scheduler() is scheduler

        assert ac.stop_scheduler() is True
        assert ac.get_scheduler_status()["running"] is False
    finally:
        ac.stop_scheduler()
        settings.auto_collection_enabled = original_enabled
        settings.auto_collection_interval_hours = original_hours


# ---------------------------------------------------------------------------
# the operator surface
# ---------------------------------------------------------------------------

def test_start_endpoint_refuses_a_deployment_that_did_not_opt_in(client, admin_headers):
    """A POST cannot grant the opt-in; it reports the refusal instead.

    The fork read ``AUTO_COLLECTION_ENABLED`` off the environment and then
    passed ``enabled=True`` regardless, so a host configured against background
    research was switched on by an operator click and the panel said so.
    """
    settings = ac.get_settings()
    original = settings.auto_collection_enabled
    settings.auto_collection_enabled = False
    try:
        response = client.post("/api/v1/admin/collection/start", headers=admin_headers)
    finally:
        settings.auto_collection_enabled = original
        ac.stop_scheduler()

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "auto_collection_disabled"
    assert "AUTO_COLLECTION_ENABLED" in detail["message"]
    assert detail["status"]["enabled"] is False
    assert detail["status"]["running"] is False
    assert _jobs() == []


def test_trigger_endpoint_returns_the_tick_outcome(client, admin_headers):
    owner = _make_user("trigger-api@alethra.io")
    _make_skill_account(owner)

    response = client.post("/api/v1/admin/collection/trigger", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["skipped"] is None
    assert data["accounts"] == 1
    assert data["jobs"][0]["status"] == "completed"
    assert data["jobs"][0]["user_id"] == str(owner)
    assert data["completed_at"]

    rows = _jobs(owner)
    assert len(rows) == 1
    assert str(rows[0]["id"]) == data["jobs"][0]["job_id"]


def test_trigger_endpoint_names_the_reason_nothing_ran(client, admin_headers):
    """No accounts: the response says so instead of returning empty counts."""
    response = client.post("/api/v1/admin/collection/trigger", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["skipped"] == "no_accounts_served"
    assert data["accounts"] == 0
    assert data["jobs"] == []


def test_history_endpoint_serves_the_recorded_ticks(client, admin_headers):
    owner = _make_user("history-api@alethra.io")
    _make_skill_account(owner)
    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ac.run_collection_cycle(window="2026-11-17T04:00Z/4h")

    response = client.get(
        "/api/v1/admin/collection/history?limit=5", headers=admin_headers
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["window"] == "2026-11-17T04:00Z/4h"
    assert rows[0]["studied"] == (
        len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)
    )
    assert rows[0]["status"] == "completed"


def test_status_endpoint_reports_the_deployment_and_this_process(client, admin_headers):
    response = client.get("/api/v1/admin/collection/status", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "enabled",
        "running",
        "interval_hours",
        "timezone",
        "job_type",
        "next_run",
    }
    assert data["running"] is False
    assert data["interval_hours"] == ac.get_settings().auto_collection_interval_hours


def test_stop_endpoint_acts_on_this_worker_only(client, admin_headers):
    """``/collection/stop`` ends this process's clock and says which one.

    The interface asked for stop and resume, and the honest answer to either is
    about one worker: the scheduler is a process object, so ``running`` can
    never describe the deployment. A stop that is not running is reported as a
    stopped worker rather than an error, because the operator's question — "is
    this worker still firing?" — has the same answer either way.
    """
    settings = ac.get_settings()
    original = settings.auto_collection_enabled
    settings.auto_collection_enabled = True
    try:
        assert client.post(
            "/api/v1/admin/collection/start", headers=admin_headers
        ).status_code == 200
        assert client.post(
            "/api/v1/admin/collection/stop", headers=admin_headers
        ).json()["running"] is False

        stopped_again = client.post("/api/v1/admin/collection/stop", headers=admin_headers)
        assert stopped_again.status_code == 200
        assert stopped_again.json()["running"] is False
        assert stopped_again.json()["enabled"] is True
    finally:
        ac.stop_scheduler()
        settings.auto_collection_enabled = original
