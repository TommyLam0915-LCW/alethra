"""Tests for the controlled daily/weekly research service.

Every test asserts an outcome the worker *writes* (a status, a row, a skip
count) — not that the call returned without raising. The previous version of
this file asserted nothing: ``test_creates_learning_job`` collected rows and
then commented "May find 0 if user wasn't the first", and
``test_returns_none_when_no_users`` asserted ``result is None or isinstance(...)``
— a condition true for every value. Those pass whatever the worker does, so
they could not have detected any of the six defects pinned here:

1. the cron triggers carried no timezone (02:00 UTC documented, 02:00 *local*
   executed — Asia/Hong_Kong measured on this host);
2. a tick whose every combo failed was recorded ``completed``;
3. the daily lane never consulted the kill switch the training worker honours;
4. work was attributed to ``select(User).limit(1)`` — an arbitrary account;
5. the tick created no durable row until it finished, so a crash left no trace;
6. the dataset evidence row was hardcoded (``fingerprint=None``,
   ``integrity_ok=True``, ``gap_count=0``) and yesterday's stored bars were
   re-studied forever with no freshness check.

A seventh section covers the wiring the trigger endpoint depends on: the row a
request already recorded is the row the tick finishes, so one request leaves
one record — including when the tick refuses to run.

The scheduler-object section also pins the registration control: which ticks
this process may put on the clock, that naming one lane does not co-schedule
another, and that a lane name the build cannot map refuses the start instead
of quietly narrowing it.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import (
    DEFAULT_LEARNING_SCHEDULER_LANES,
    LEARNING_SCHEDULER_LANES,
    Settings,
)
from app.lib.timeutil import utcnow_aware
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.strategy_study import StrategyStudyRun
from app.models.user import User
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
            display_name="Service Test",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _make_skill_account(owner_id: uuid.UUID) -> uuid.UUID:
    """One paper portfolio: the account the daily service exists to serve."""
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
    """Plain values, so the assertions survive the session closing."""
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
        "instrument_id": "x",
        "interval_minutes": 1,
        "status": "studied",
        "reason": None,
        "trades": 5,
        "bars": 200,
        "data_nature": "venue_completed_bars",
        "fingerprint": "fp",
        "integrity_ok": True,
        "is_stale": False,
    }


def _study_result():
    return {
        "lane": "strategy_research_simulated",
        "research_version": "1.0.0",
        "settings": {"starting_cash": "10000"},
        "walk_forward": {"out_of_sample": {"trades": 3}},
        "out_of_sample_trades": [],
        "highlights": {},
        "disclosures": [],
    }


def _series(candles, **overrides):
    """A CandleSeries-shaped stand-in: the worker reads these same attributes."""
    base = {
        "completed": tuple(candles),
        "forming": None,
        "fingerprint": "series-fingerprint",
        "integrity_ok": False,
        "gaps": (
            (datetime(2026, 1, 1, tzinfo=timezone.utc),
             datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ),
        "missing_intervals": 288,
        "fetched_at": utcnow_aware(),
        "is_stale": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _candles(count: int, *, interval_minutes: int = 1440, start=None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        SimpleNamespace(
            open_time=start + timedelta(minutes=interval_minutes * i),
            open=str(100 + i),
            high=str(101 + i),
            low=str(99 + i),
            close=str(100 + i),
            volume="10.0",
        )
        for i in range(count)
    ]


def _stored_run(owner, *, candles, fingerprint, instrument, interval=1440):
    db = TestSession()
    try:
        run = StrategyStudyRun(
            user_id=owner,
            lane="strategy_research_simulated",
            research_version="1.0.0",
            instrument_id=instrument,
            interval_minutes=interval,
            provider_id="kraken",
            data_nature="venue_completed_bars",
            bar_count=len(candles),
            first_open_time=candles[0].open_time,
            last_open_time=candles[-1].open_time,
            dataset_fingerprint=fingerprint,
            settings_json={"starting_cash": "10000"},
            walk_forward_json={"folds": []},
            bars_json=[
                {
                    "open_time": candle.open_time.isoformat(),
                    "interval_minutes": interval,
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                }
                for candle in candles
            ],
            trade_count=0,
        )
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# the scheduler object itself
# ---------------------------------------------------------------------------

def test_creating_the_scheduler_registers_exactly_the_named_lanes():
    """Research, replay, analysis and estimate: four jobs, and a new one is visible.

    The set is asserted exactly rather than with ``<=`` so that registering a
    further tick - or losing one - has to be a deliberate change here, the same
    way the migration tail is pinned. The four are named rather than left to the
    default because the default is the subject of the next node, and a test that
    reads its own expected set out of the registry cannot see it move.
    """
    with _lane_allow_list(",".join(LEARNING_SCHEDULER_LANES)):
        scheduler = ls.create_learning_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {
        "daily_research",
        "daily_simulation",
        "weekly_analysis",
        "daily_model_estimate",
    }


def test_an_upgrade_registers_no_lane_the_host_never_asked_for():
    """A host with the shared switch on and no allow-list keeps its old schedule.

    ``LEARNING_SCHEDULER_JOBS`` was introduced after these four ticks existed,
    so the empty value is what every deployment running today already has. If
    empty meant "every lane this build knows", then the build that adds
    ``daily_model_estimate`` would give that host a 05:00 UTC tick that deducts
    quota from its owners, on the strength of a switch the operator pulled for a
    different service. Unset therefore means ``DEFAULT_LEARNING_SCHEDULER_LANES``
    - the three - and the estimate lane appears only when it is named. The
    general form is asserted too: the default is what the switch has always
    meant, not merely "the registry minus the newest entry".
    """
    with _lane_allow_list(""):
        assert ls.registered_lane_ids() == [
            "daily_research",
            "daily_simulation",
            "weekly_analysis",
        ]
        scheduler = ls.create_learning_scheduler()
        # Built, not started: the job set is decided by ``add_job``, and
        # ``test_starting_an_estimate_only_scheduler_adds_no_other_lane`` already
        # proves it survives ``.start()``.
        assert {job.id for job in scheduler.get_jobs()} == {
            "daily_research",
            "daily_simulation",
            "weekly_analysis",
        }

    # The same host, told to want it, gets it - and only it.
    with _lane_allow_list("daily_model_estimate"):
        assert ls.registered_lane_ids() == ["daily_model_estimate"]

    # And the pair the two nodes above depend on is checked, not assumed: the
    # default has to be something the registry can actually schedule.
    assert set(DEFAULT_LEARNING_SCHEDULER_LANES) < set(LEARNING_SCHEDULER_LANES)
    assert "daily_model_estimate" in LEARNING_SCHEDULER_LANES
    assert "daily_model_estimate" not in DEFAULT_LEARNING_SCHEDULER_LANES


def test_the_startup_path_is_what_puts_a_lane_on_the_clock():
    """The app's own startup events, not ``create_learning_scheduler()`` by hand.

    Building the object and reading ``get_jobs()`` cannot answer what the owner
    has to know before turning the switch on in production: whether the startup
    handler consults ``ENABLE_LEARNING_SCHEDULER`` at all, whether it starts the
    object or merely creates it, what other handler schedules something beside
    it in the same process, and whether stopping the app stops all of it. This
    node drives the ASGI lifespan, which runs the same two ``startup`` and two
    ``shutdown`` handlers uvicorn would, and reads each service back off the
    module that owns it. Every lane body is replaced with a recorder first: the
    point is what gets *scheduled*, and a tick that fell due inside the block
    must not spend quota against the fixture's database.
    """
    from unittest.mock import MagicMock

    import app.main as main
    from app.services import auto_collection

    idle = {
        name: MagicMock()
        for name in (
            "run_scheduled_research",
            "run_scheduled_simulations",
            "run_pattern_analysis_job",
            "run_scheduled_daily_estimates",
        )
    }

    # The upgrade case: the shared switch was already on, nothing was named.
    with _process_settings(
        enable_learning_scheduler=True,
        learning_scheduler_jobs="",
        auto_collection_enabled=False,
    ), patch.multiple(ls, **idle):
        with TestClient(main.app):
            scheduler = main._learning_scheduler
            assert scheduler is not None and scheduler.running
            assert {job.id for job in scheduler.get_jobs()} == set(
                DEFAULT_LEARNING_SCHEDULER_LANES
            )
            assert "daily_model_estimate" not in {job.id for job in scheduler.get_jobs()}
            # The other scheduler in the same process is gated by its own flag,
            # which is off here: startup must not start what the configuration
            # declined, and must not report a start that did not happen.
            status = auto_collection.get_scheduler_status()
            assert status["enabled"] is False
            assert status["running"] is False
        assert not scheduler.running, "shutdown left a thread pool firing"
        assert auto_collection.get_scheduler_status()["running"] is False

    # The named case, and only this lane: the estimate tick arrives because an
    # operator asked for it, and asking for it does not co-schedule research.
    with _process_settings(
        enable_learning_scheduler=True,
        learning_scheduler_jobs="daily_model_estimate",
        auto_collection_enabled=False,
    ), patch.multiple(ls, **idle):
        with TestClient(main.app):
            scheduler = main._learning_scheduler
            assert [job.id for job in scheduler.get_jobs()] == ["daily_model_estimate"]
            assert str(scheduler.get_job("daily_model_estimate").trigger) == (
                "cron[hour='5', minute='0']"
            )
        assert not scheduler.running

    # The switch off: no scheduler object exists at all, whatever the lanes say.
    with _process_settings(
        enable_learning_scheduler=False,
        learning_scheduler_jobs="daily_model_estimate",
        auto_collection_enabled=False,
    ), patch.multiple(ls, **idle):
        main._learning_scheduler = None
        with TestClient(main.app):
            assert main._learning_scheduler is None
        assert all(spy.call_count == 0 for spy in idle.values())


def test_scheduler_starts_and_stops():
    scheduler = ls.create_learning_scheduler()
    scheduler.start()
    try:
        assert scheduler.running
    finally:
        # Without this, a failing assert leaves a BackgroundScheduler thread
        # pool running for the rest of the session — it keeps the process
        # alive and keeps firing jobs against a database whose fixture has
        # already been torn down.
        scheduler.shutdown(wait=False)
    assert not scheduler.running


@contextmanager
def _lane_allow_list(value: str):
    """Register only the lanes ``value`` names, for the duration of the block.

    ``get_settings`` is cached, so this writes the process-wide settings object
    and restores the field it touched; no other lane's configuration changes.
    """
    settings = ls.get_settings()
    original = settings.learning_scheduler_jobs
    settings.learning_scheduler_jobs = value
    try:
        yield settings
    finally:
        settings.learning_scheduler_jobs = original


@contextmanager
def _process_settings(**values):
    """Several settings fields at once, restored however the block ends.

    The startup-path node below has to hold three switches at the same time -
    the shared scheduler, the lane allow-list and the collection service's own
    opt-in - because reading them one at a time cannot show which handler starts
    what beside which.
    """
    settings = ls.get_settings()
    original = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        setattr(settings, name, value)
    try:
        yield settings
    finally:
        for name, value in original.items():
            setattr(settings, name, value)


def test_the_narrowable_names_and_the_scheduled_lanes_are_one_list():
    """A lane the allow-list accepts but the registry lacks is the bug to ban.

    ``LEARNING_SCHEDULER_JOBS`` is validated against the config constant and
    applied against the worker's own lane table. If the two drift, an operator
    can name a lane that registers nothing, or hold a lane no host can narrow
    away — and both read as a correctly narrowed schedule. Order is part of the
    assertion because the hours are the reason for the order.
    """
    assert [lane_id for lane_id, *_ in ls._lane_definitions()] == list(
        LEARNING_SCHEDULER_LANES
    )


def test_an_estimate_only_host_registers_the_estimate_tick_and_nothing_else():
    """One switch for four kinds of work needs a way to ask for one kind.

    The registered set, the callable and the filed scope are all asserted: the
    brief is that calling the tick body directly does not touch the other
    lanes, and that does not yet prove that *starting* the shared scheduler
    does not co-schedule research, replay or the weekly proposals. Naming the
    estimate lane here is what makes the second claim checkable.
    """
    with _lane_allow_list("daily_model_estimate"):
        assert ls.registered_lane_ids() == ["daily_model_estimate"]
        scheduler = ls.create_learning_scheduler()
        jobs = scheduler.get_jobs()

    assert [job.id for job in jobs] == ["daily_model_estimate"]
    assert jobs[0].func.__name__ == "run_scheduled_daily_estimates"
    assert jobs[0].kwargs == {"scope": "daily"}
    assert {job.func.__name__ for job in jobs}.isdisjoint(
        {"run_scheduled_research", "run_scheduled_simulations", "run_pattern_analysis_job"}
    )


def test_starting_an_estimate_only_scheduler_adds_no_other_lane():
    """``.start()`` is what a deployment does; the job set must survive it.

    Every lane body is replaced before the scheduler is built, so a tick that
    happens to fall due inside the test window calls a recorder instead of
    spending quota or writing a ledger row against the fixture's database.
    """
    with _lane_allow_list("daily_model_estimate"), patch.object(
        ls, "run_scheduled_daily_estimates", side_effect=lambda **kwargs: None
    ):
        scheduler = ls.create_learning_scheduler()
        scheduler.start()
        try:
            assert [job.id for job in scheduler.get_jobs()] == ["daily_model_estimate"]
        finally:
            scheduler.shutdown(wait=False)


def test_two_named_lanes_register_two_and_only_two():
    """The allow-list is a set, not a single choice, and keeps clock order."""
    with _lane_allow_list("daily_model_estimate,daily_simulation"):
        assert ls.registered_lane_ids() == ["daily_simulation", "daily_model_estimate"]
        scheduler = ls.create_learning_scheduler()
        assert {job.id for job in scheduler.get_jobs()} == {
            "daily_simulation",
            "daily_model_estimate",
        }


def test_the_registered_cron_field_is_the_documented_utc_hour():
    """The hours the module documents are the fields APScheduler will read.

    Asserted on the trigger object rather than on ``DAILY_*_HOUR_UTC``: defect
    1 in this file's header was a documented UTC hour that executed as a local
    one, and comparing the constant to itself cannot see that again. The
    estimate tick is the one the owner-facing daily service depends on, so its
    05:00 UTC expression is pinned here. All four are named rather than left to
    the default, because the default no longer includes the estimate lane and
    this node is about the hour a lane fires on, not about which lanes are on.
    """
    with _lane_allow_list(",".join(LEARNING_SCHEDULER_LANES)):
        scheduler = ls.create_learning_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert str(jobs["daily_model_estimate"].trigger) == "cron[hour='5', minute='0']"
    assert str(jobs["daily_research"].trigger) == "cron[hour='2', minute='0']"
    assert str(jobs["daily_simulation"].trigger) == "cron[hour='3', minute='0']"
    assert str(jobs["weekly_analysis"].trigger) == (
        "cron[day_of_week='sun', hour='4', minute='0']"
    )


def test_a_lane_name_this_build_cannot_map_refuses_the_process_at_read():
    """Typo in the allow-list is a refused start, not a narrowed schedule.

    The three shapes that lose a lane silently — a name that exists nowhere, a
    list that carries one such name beside a good one, and separators that name
    nothing — all raise while the settings object is built, before a job is
    registered and before a thread exists.
    """
    for value in (
        "daily_model_estamate",
        "daily_research,weekly_analyses",
        ",",
    ):
        with pytest.raises(ValidationError, match="LEARNING_SCHEDULER_JOBS"):
            Settings(learning_scheduler_jobs=value, _env_file=None)
    assert Settings(learning_scheduler_jobs="", _env_file=None).learning_scheduler_job_list == []


def test_reconstructs_stored_bars_as_exact_decimals():
    bars = ls._bars_from_json(
        [
            {
                "open_time": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                "interval_minutes": 60,
                "open": "50000.12345678",
                "high": "50100.0",
                "low": "49900.0",
                "close": "50050.0",
                "volume": "100.5",
            }
        ],
        60,
    )
    assert len(bars) == 1
    assert bars[0].interval_minutes == 60
    assert bars[0].open == Decimal("50000.12345678")
    assert bars[0].volume == Decimal("100.5")


# ---------------------------------------------------------------------------
# 1 — the schedule fires on the clock the module documents
# ---------------------------------------------------------------------------

def test_every_registered_tick_fires_on_utc_not_the_host_clock():
    """The minutes the module documents must be UTC minutes, not host minutes.

    A bare ``CronTrigger(hour=2)`` resolves to the host timezone: measured here
    as Asia/Hong_Kong, so the batch ran eight hours away from what the module
    record and the operator both read. Every registered job is checked, because
    the defect was one argument and any new job can omit it again - which is why
    all four lanes are named here rather than left to the default. The default
    no longer registers the estimate lane, and a loop over what is registered
    would then stop covering it without saying so.
    """
    with _lane_allow_list(",".join(LEARNING_SCHEDULER_LANES)):
        scheduler = ls.create_learning_scheduler()
    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert str(scheduler.timezone) == "UTC"
    assert set(jobs) == set(LEARNING_SCHEDULER_LANES)
    for job_id, job in jobs.items():
        assert str(job.trigger.timezone) == "UTC", job_id


# ---------------------------------------------------------------------------
# 2 — the terminal status is the truth of the tick
# ---------------------------------------------------------------------------

def test_every_combo_failing_records_failed_not_completed():
    owner = _make_user("all-fail@alethra.io")
    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)

    with patch.object(ls, "_run_single_study", side_effect=RuntimeError("venue down")) as study:
        ls.run_scheduled_research(scope="daily", user_id=owner)

    assert study.call_count == combos
    rows = _jobs(owner)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed", rows[0]["status"]
    assert rows[0]["result_json"]["failed"] == combos
    assert rows[0]["result_json"]["studied"] == 0
    assert rows[0]["error_details"] and "venue down" in rows[0]["error_details"]


def test_some_combos_failing_records_partial():
    owner = _make_user("partial@alethra.io")
    broken = ls.SCHEDULED_INSTRUMENTS[0]

    def side_effect(_db, _user_id, instrument_id, _interval):
        if instrument_id == broken:
            raise RuntimeError("venue down")
        return _ok_study()

    with patch.object(ls, "_run_single_study", side_effect=side_effect):
        ls.run_scheduled_research(scope="daily", user_id=owner)

    intervals = len(ls.SCHEDULED_INTERVALS)
    rows = _jobs(owner)
    assert rows[0]["status"] == "partial", rows[0]["status"]
    assert rows[0]["result_json"]["failed"] == intervals
    assert rows[0]["result_json"]["studied"] == (
        len(ls.SCHEDULED_INSTRUMENTS) - 1
    ) * intervals


def test_a_clean_tick_reports_completed_and_carries_no_error_text():
    owner = _make_user("clean@alethra.io")

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ls.run_scheduled_research(scope="daily", user_id=owner)

    rows = _jobs(owner)
    assert rows[0]["status"] == "completed"
    assert rows[0]["result_json"]["failed"] == 0
    assert rows[0]["error_details"] in (None, "")


# ---------------------------------------------------------------------------
# 3 — stop: the kill switch the rest of the platform already honours
# ---------------------------------------------------------------------------

def test_active_global_kill_switch_stops_the_tick_before_any_work():
    """``training_worker`` checks the switch; the daily lane must too.

    Refusing writes no row: a job per owner per tick would bury the ledger in
    records of work that was never offered.
    """
    owner = _make_user("killswitch@alethra.io")
    _make_skill_account(owner)
    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.global_, "test", reason="maintenance")
    finally:
        db.close()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        outcome = ls.run_scheduled_research(scope="daily")

    assert study.call_count == 0
    assert _jobs() == []
    assert outcome["skipped"] == "kill_switch_active"


# ---------------------------------------------------------------------------
# 4 — attribution: the owner who holds the account, not whoever the DB returns
# ---------------------------------------------------------------------------

def test_tick_serves_the_owner_with_the_skill_account_not_the_first_row():
    """``select(User).limit(1)`` has no ORDER BY and no relation to the account.

    The un-served account is created first here, so the old code wrote the
    studies under ``owner_a`` — a ledger entry for work nobody asked for, in
    the wrong account, while ``owner_b`` silently got none.
    """
    owner_a = _make_user("no-accounts@alethra.io")
    owner_b = _make_user("has-account@alethra.io")
    _make_skill_account(owner_b)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ls.run_scheduled_research(scope="daily")

    assert _jobs(owner_a) == []
    rows = _jobs(owner_b)
    assert len(rows) == 1
    assert rows[0]["user_id"] == owner_b


def test_retired_accounts_are_not_served():
    owner = _make_user("retired@alethra.io")
    portfolio_id = _make_skill_account(owner)
    db = TestSession()
    try:
        row = db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).first()
        row.status = PortfolioStatus.retired
        db.commit()
    finally:
        db.close()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        outcome = ls.run_scheduled_research(scope="daily")

    assert _jobs(owner) == []
    assert outcome["skipped"] == "no_accounts_served"


def test_tick_is_bounded_by_the_owner_limit():
    """One tick must not fan out to every account on the box unasked."""
    owner_x = _make_user("cap-x@alethra.io")
    owner_y = _make_user("cap-y@alethra.io")
    _make_skill_account(owner_x)
    _make_skill_account(owner_y)
    expected = min(owner_x, owner_y)

    settings = ls.get_settings()
    original = settings.learning_scheduler_max_owners
    settings.learning_scheduler_max_owners = 1
    try:
        with patch.object(ls, "_run_single_study", side_effect=_ok_study):
            ls.run_scheduled_research(scope="daily")
    finally:
        settings.learning_scheduler_max_owners = original

    assert [row["user_id"] for row in _jobs()] == [expected]


# ---------------------------------------------------------------------------
# 5 — dedup: one scheduled tick per account per day
# ---------------------------------------------------------------------------

def test_second_scheduled_tick_on_the_same_day_does_the_work_once():
    owner = _make_user("dedup@alethra.io")
    combos = len(ls.SCHEDULED_INSTRUMENTS) * len(ls.SCHEDULED_INTERVALS)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        ls.run_scheduled_research(scope="daily", user_id=owner)
        calls_after_first = study.call_count
        outcome = ls.run_scheduled_research(scope="daily", user_id=owner)

    assert calls_after_first == combos
    assert study.call_count == combos, "the second tick re-ran the whole grid"
    assert len(_jobs(owner)) == 1
    assert outcome["skipped"] == "already_ran_today"


def test_a_manual_trigger_is_not_suppressed_by_the_daily_dedup():
    """The daily guard exists to stop a restart storm, not to veto the owner."""
    owner = _make_user("manual@alethra.io")

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ls.run_scheduled_research(scope="manual", user_id=owner)
        ls.run_scheduled_research(scope="manual", user_id=owner)

    rows = _jobs(owner)
    assert len(rows) == 2
    assert all("scheduled_for" not in row["config_json"] for row in rows)


def test_the_in_flight_tick_is_already_in_the_ledger():
    """The row must be durable before the work starts, or a crash leaves no trace."""
    owner = _make_user("in-flight@alethra.io")
    seen = []

    def peek(_db, _user_id, _instrument_id, _interval):
        seen.append([row["status"] for row in _jobs(owner)])
        return _ok_study()

    with patch.object(ls, "_run_single_study", side_effect=peek):
        ls.run_scheduled_research(scope="daily", user_id=owner)

    assert seen and seen[0] == ["running"], seen[:1]
    assert _jobs(owner)[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# 7 — one record per request: the tick advances the row the trigger wrote
# ---------------------------------------------------------------------------

def _pending_request(owner_id, *, job_type="scheduled_study") -> uuid.UUID:
    """The row POST /learning/jobs/trigger records before it dispatches."""
    db = TestSession()
    try:
        job = LearningJob(
            user_id=owner_id,
            status="pending",
            job_type=job_type,
            config_json={"scope": "manual", "job_type": job_type},
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def test_a_triggered_request_advances_its_own_row_instead_of_adding_one():
    owner = _make_user("adopt@alethra.io")
    request_id = _pending_request(owner)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ls.run_scheduled_research(scope="manual", user_id=owner, job_id=request_id)

    rows = _jobs(owner)
    assert len(rows) == 1, [row["status"] for row in rows]
    assert rows[0]["id"] == request_id
    assert rows[0]["status"] == "completed"
    assert rows[0]["result_json"]["scheduled_for"] is None


def test_another_accounts_row_is_never_adopted():
    owner = _make_user("adopter@alethra.io")
    stranger = _make_user("stranger@alethra.io")
    stranger_row = _pending_request(stranger)

    with patch.object(ls, "_run_single_study", side_effect=_ok_study):
        ls.run_scheduled_research(scope="manual", user_id=owner, job_id=stranger_row)

    assert [row["status"] for row in _jobs(stranger)] == ["pending"]
    rows = _jobs(owner)
    assert len(rows) == 1 and rows[0]["id"] != stranger_row


def test_a_refused_trigger_closes_the_row_it_was_given():
    """A skip must not leave the caller's request queued behind nothing.

    The scheduled tick that skips writes no row of its own — but a row the
    caller already recorded is a promise about that row, so the refusal is
    written into it.
    """
    owner = _make_user("refused@alethra.io")
    _make_skill_account(owner)
    request_id = _pending_request(owner)
    db = TestSession()
    try:
        activate_kill_switch(db, KillSwitchScope.global_, "test", reason="maintenance")
    finally:
        db.close()

    with patch.object(ls, "_run_single_study", side_effect=_ok_study) as study:
        outcome = ls.run_scheduled_research(scope="manual", user_id=owner, job_id=request_id)

    assert study.call_count == 0
    assert outcome["skipped"] == "kill_switch_active"
    rows = _jobs(owner)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_details"] == "refused: kill_switch_active"
    assert rows[0]["result_json"] == {}


# ---------------------------------------------------------------------------
# 6 — the dataset evidence is the venue's verdict, and no new bars stops a study
# ---------------------------------------------------------------------------

def test_fetched_bars_persist_the_series_own_integrity_verdict():
    """A gap the venue reported cannot be stored as ``integrity_ok=True``.

    ``auto_collection`` already writes ``series.fingerprint``,
    ``series.integrity_ok`` and ``len(series.gaps)``; the daily lane wrote the
    constants ``None`` / ``True`` / ``0`` into those same columns.
    """
    owner = _make_user("evidence@alethra.io")
    series = _series(_candles(200))
    captured = {}

    def capture(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), trades=[])

    provider = SimpleNamespace(get_candle_series=lambda *a, **k: series)
    instrument = ls.SCHEDULED_INSTRUMENTS[0]
    db = TestSession()
    try:
        with patch("app.providers.crypto_registry.get_crypto_provider", return_value=provider), \
             patch.object(ls, "run_study", return_value=_study_result()), \
             patch.object(ls, "persist_study", side_effect=capture):
            summary = ls._run_single_study(db, owner, instrument, 1440)
    finally:
        db.close()

    dataset = captured["dataset"]
    assert dataset["fingerprint"] == "series-fingerprint"
    assert dataset["integrity_ok"] is False
    assert dataset["gap_count"] == 1
    assert dataset["missing_intervals"] == 288
    assert dataset["is_stale"] is True
    assert summary["status"] == "studied"
    assert summary["integrity_ok"] is False


def test_unreachable_venue_with_identical_stored_bars_is_skipped_not_restudied():
    """Re-studying the same bars was the default path, every day, forever.

    The old code preferred ``bars_json`` from the newest stored run and only
    fetched when nothing was stored, so a "daily" record could be produced from
    data that had not changed since the last one. Reuse is now a declared
    fallback, and a fallback holding no new bars is reported as skipped.
    """
    owner = _make_user("fallback-same@alethra.io")
    instrument = ls.SCHEDULED_INSTRUMENTS[0]
    candles = _candles(200)
    _stored_run(
        owner,
        candles=candles,
        fingerprint=ls.dataset_fingerprint(instrument, 1440, candles),
        instrument=instrument,
    )

    def explode(*_a, **_k):
        raise RuntimeError("venue unreachable")

    db = TestSession()
    try:
        with patch("app.providers.crypto_registry.get_crypto_provider", side_effect=explode), \
             patch.object(ls, "run_study", return_value=_study_result()), \
             patch.object(ls, "persist_study") as persist:
            summary = ls._run_single_study(db, owner, instrument, 1440)
    finally:
        db.close()

    assert summary["status"] == "skipped"
    assert summary["reason"] == "no_new_bars"
    assert persist.call_count == 0


def test_unreachable_venue_with_older_stored_bars_studies_and_labels_them():
    owner = _make_user("fallback-new@alethra.io")
    instrument = ls.SCHEDULED_INSTRUMENTS[0]
    older = _candles(200, start=datetime(2025, 1, 1, tzinfo=timezone.utc))
    _stored_run(owner, candles=older, fingerprint="an-older-window", instrument=instrument)

    def explode(*_a, **_k):
        raise RuntimeError("venue unreachable")

    captured = {}
    studied_bars = []

    def capture(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), trades=[])

    def study(bars, **_kwargs):
        studied_bars.extend(bars)
        return _study_result()

    db = TestSession()
    try:
        with patch("app.providers.crypto_registry.get_crypto_provider", side_effect=explode), \
             patch.object(ls, "run_study", side_effect=study), \
             patch.object(ls, "persist_study", side_effect=capture):
            summary = ls._run_single_study(db, owner, instrument, 1440)
    finally:
        db.close()

    dataset = captured["dataset"]
    assert summary["status"] == "studied"
    assert dataset["data_nature"] == "stored_bars_reused"
    assert len(studied_bars) == 200
    assert dataset["fingerprint"] == ls.dataset_fingerprint(instrument, 1440, studied_bars)
