"""Tests for the learning queue: two producers, one executor, one row per request.

The contract under test is the one 「二、先關閉多租戶與任務生命週期」 asks for:

- A producer (cron tick, or the API on behalf of an authenticated actor) files
  exactly one ``pending`` row and runs nothing.
- ``drain_learning_queue`` is the only executor, and it settles *that same row*.
  A successful submission therefore cannot sit at ``pending`` forever.
- Owner attribution is explicit: scheduled work needs
  ``LEARNING_SYSTEM_OWNER_EMAIL``, and nothing here reads ``select(User).limit(1)``.
- A claim whose lease expires is recorded ``failed``; it is not re-run, and the
  late executor cannot overwrite the settlement.

Node map: ``TestSystemOwner`` (who a tick belongs to), ``TestProducers`` (filings
only), ``TestQueueLifecycle`` (pending → running → terminal on one id),
``TestLeaseExpiry`` (an executor that stopped reporting),
``TestStatusTruthfulness`` (what the job list may claim), ``TestPatternAnalysis``
(the second job type and what it may attribute), ``TestOwnerScopedAttribution``
(cross-tenant), ``TestExecutorStatus`` (what the trigger endpoint may promise).
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from app.services.strategy_research import EXIT_BY_ID
from app.workers import learning_scheduler as worker
from app.workers.learning_scheduler import (
    DRAIN_JOB_ID,
    _bars_from_json,
    _find_best_rule_for_exit,
    claim_pending_learning_job_ids,
    create_learning_scheduler,
    drain_learning_queue,
    enqueue_pattern_analysis,
    enqueue_scheduled_research,
    execute_learning_job,
    executor_identity,
    learning_executor_status,
    reclaim_expired_learning_leases,
    register_learning_scheduler,
    resolve_system_owner_id,
    run_pattern_analysis_job,
    run_scheduled_research,
    settle_learning_job,
)
from tests.conftest import TestSession

OWNER_EMAIL = "system-owner@alethra.io"
OTHER_EMAIL = "bystander@alethra.io"

STUDY_RESULT = {"walk_forward": {"out_of_sample": {"trades": 5}}}

EXIT_ID = "exit_trend_following_2"
MEASURED_RULE = "mean_reversion_vwap"
# The value the removed fallback used to write into every proposal it could not
# attribute. A proposal carrying it here is a fabrication, not a measurement.
FABRICATED_RULE = "breakout_trend"


# ---------------------------------------------------------------------------
# fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_executor_leakage():
    """No test may inherit another test's registered scheduler.

    ``learning_executor_status()`` reads process-global state, so a leak would
    let a later test be admitted by an executor it did not create.
    """
    register_learning_scheduler(None)
    yield
    register_learning_scheduler(None)


def _make_user(email: str, *, is_active: bool = True) -> uuid.UUID:
    from app.auth.jwt import get_password_hash

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name="Queue Test",
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


def _settings(monkeypatch, **values):
    """Give the worker a settings object with these values, for this test only.

    ``get_settings`` is ``@lru_cache``d, so the values are patched on the worker
    module rather than written into the process-wide cached Settings.
    """
    patched = worker.get_settings().model_copy(update=values)
    monkeypatch.setattr(worker, "get_settings", lambda: patched)
    return patched


def _fetch(job_id: uuid.UUID):
    """Read the row back on a session that is not the one that wrote it."""
    db = TestSession()
    try:
        row = db.scalars(select(LearningJob).where(LearningJob.id == job_id)).one_or_none()
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def _rows(job_type: str) -> list:
    db = TestSession()
    try:
        return list(
            db.scalars(select(LearningJob).where(LearningJob.job_type == job_type)).all()
        )
    finally:
        db.close()


def _proposals() -> list:
    db = TestSession()
    try:
        return list(db.scalars(select(LearningProposal)).all())
    finally:
        db.close()


def _apply(job_id: uuid.UUID, **values) -> None:
    """Change one column of one row without touching the rest of its history."""
    db = TestSession()
    try:
        db.execute(update(LearningJob).where(LearningJob.id == job_id).values(**values))
        db.commit()
    finally:
        db.close()


def _file_pending_study(
    user_id: uuid.UUID,
    *,
    key=None,
    instruments=None,
    intervals=None,
    origin: str = "user",
):
    """File one study request through the product's own producer seam.

    ``None`` takes a one-combo grid; an explicit ``[]`` means the caller asked
    for nothing, and must not be widened into the scheduled default set.
    """
    if instruments is None:
        instruments = ["CRYPTO:KRAKEN:BTC-USD"]
    if intervals is None:
        intervals = [1440]

    db = TestSession()
    try:
        job, replayed = enqueue_scheduled_research(
            db,
            user_id=user_id,
            scope="entry-check",
            instruments=instruments,
            intervals=intervals,
            origin=origin,
            operation_key=key,
        )
        return job.id, replayed
    finally:
        db.close()


def _file_pending_analysis(user_id: uuid.UUID, **kwargs) -> uuid.UUID:
    db = TestSession()
    try:
        job, _ = enqueue_pattern_analysis(db, user_id=user_id, origin="user", **kwargs)
        return job.id
    finally:
        db.close()


def _make_run_with_trades(user_id: uuid.UUID, *, rule_id: str, exit_id: str) -> uuid.UUID:
    """One stored run carrying two of this owner's trades."""
    now = datetime.now(timezone.utc)
    db = TestSession()
    try:
        run = StrategyStudyRun(
            user_id=user_id,
            lane="strategy_research_simulated",
            research_version="1.0.0",
            instrument_id="CRYPTO:KRAKEN:BTC-USD",
            interval_minutes=1440,
            provider_id="kraken",
            data_nature="venue_completed_bars",
            bar_count=2,
            first_open_time=now,
            last_open_time=now,
            settings_json={"starting_cash": "10000"},
            walk_forward_json={"folds": []},
            bars_json=[],
            trade_count=2,
        )
        db.add(run)
        db.flush()
        cash = Decimal("10000")
        for i in range(2):
            pnl = Decimal("50.0") if i == 0 else Decimal("-30.0")
            cash += pnl
            db.add(
                StrategyStudyTrade(
                    run_id=run.id,
                    trade_index=i,
                    fold=0,
                    rule_id=rule_id,
                    exit_id=exit_id,
                    direction="long",
                    exit_reason="target" if i == 0 else "stop",
                    outcome="win" if i == 0 else "loss",
                    signal_time=now,
                    entry_time=now,
                    exit_time=now,
                    entry_price=Decimal("50000"),
                    exit_price=Decimal("50050"),
                    quantity=Decimal("0.02"),
                    gross_pnl=pnl + Decimal("2"),
                    commission=Decimal("1"),
                    slippage_cost=Decimal("1"),
                    total_cost=Decimal("2"),
                    net_pnl=pnl,
                    cash_after=cash,
                    bars_held=3,
                    pnl_reason_zh="Test",
                    pnl_reason_en="Test",
                )
            )
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def _adjustment(**overrides) -> dict:
    """The document ``propose_parameter_adjustment`` hands the handler."""
    base = {
        "pattern": "high_win_rate_low_pnl",
        "rationale": "widen the target",
        "evidence_status": "hypothesis",
        "baseline_exit_config": {
            "target_atr": "1.5",
            "stop_atr": "1.5",
            "time_stop_bars": 12,
        },
        "proposed_exit_config": {
            "target_atr": "2.0",
            "stop_atr": "1.5",
            "time_stop_bars": 12,
        },
    }
    base.update(overrides)
    return base


class _StubDrainJob:
    next_run_time = datetime(2099, 1, 1, tzinfo=timezone.utc)


class _StubScheduler:
    """Something that passes ``learning_executor_status`` checks without a thread.

    A real started scheduler would run genuine research work inside the test
    session. Admission reads this seam, so the stub exercises the same branches
    with no background thread and no provider call.
    """

    def __init__(self, *, running=True, drain=_StubDrainJob()):
        self.running = running
        self._drain = drain

    def get_job(self, job_id):
        if job_id == DRAIN_JOB_ID:
            return self._drain
        return None


# ---------------------------------------------------------------------------
# 二: who owns scheduler-triggered work
# ---------------------------------------------------------------------------


class TestSystemOwner:
    """``LEARNING_SYSTEM_OWNER_EMAIL`` is the only owner a tick may use."""

    def test_unset_email_finds_no_owner_even_when_users_exist(self, monkeypatch):
        """The defect being closed: the worker used to take the first user row.

        With an account present, ``select(User).limit(1)`` happily returned
        somebody, and that person's job list filled with research they never
        asked for.
        """
        _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email="")

        db = TestSession()
        try:
            assert resolve_system_owner_id(db) is None
        finally:
            db.close()

    def test_email_must_match_an_account(self, monkeypatch):
        _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email="ghost@alethra.io")

        db = TestSession()
        try:
            assert resolve_system_owner_id(db) is None
        finally:
            db.close()

    def test_inactive_account_is_not_an_owner(self, monkeypatch):
        _make_user(OWNER_EMAIL, is_active=False)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        db = TestSession()
        try:
            assert resolve_system_owner_id(db) is None
        finally:
            db.close()

    def test_names_the_configured_account_not_the_first_row(self, monkeypatch):
        """Creation order is irrelevant: only the email decides."""
        first = _make_user(OTHER_EMAIL)
        named = _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL.upper())

        db = TestSession()
        try:
            resolved = resolve_system_owner_id(db)
        finally:
            db.close()

        assert resolved == named
        assert resolved != first

    def test_empty_database_resolves_to_no_owner(self, monkeypatch):
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        db = TestSession()
        try:
            assert resolve_system_owner_id(db) is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 二: producers file work and run nothing
# ---------------------------------------------------------------------------


class TestProducers:
    def test_tick_files_one_pending_row_and_runs_nothing(self, monkeypatch):
        owner = _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        with patch.object(worker, "_run_single_study") as study:
            job_id = run_scheduled_research(
                scope="daily",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            )

        assert study.call_count == 0, "the cron entry executed work instead of queueing it"
        job = _fetch(job_id)
        assert job.user_id == owner
        assert job.status == "pending"
        assert job.origin == "schedule"
        assert job.started_at is None
        assert job.completed_at is None
        assert job.claimed_by is None
        assert job.config_json["instruments"] == ["CRYPTO:KRAKEN:BTC-USD"]
        assert job.config_json["intervals"] == [1440]
        assert job.config_json["scope"] == "daily"
        assert job.operation_key.startswith("cron:daily_research:")

    def test_tick_without_an_owner_files_no_row(self, monkeypatch):
        """A refusal must not leave evidence shaped like a request."""
        _make_user(OTHER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email="")

        with patch.object(worker, "_run_single_study") as study:
            job_id = run_scheduled_research(scope="daily")

        assert job_id is None
        assert study.call_count == 0
        assert _rows("scheduled_study") == []

    def test_analysis_tick_files_one_pending_row(self, monkeypatch):
        owner = _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        with patch.object(worker, "analyze_skill_patterns") as analyze:
            job_id = run_pattern_analysis_job()

        assert analyze.call_count == 0
        job = _fetch(job_id)
        assert job.job_type == "pattern_analysis"
        assert job.status == "pending"
        assert job.origin == "schedule"
        assert job.user_id == owner
        assert job.operation_key.startswith("cron:weekly_analysis:")

    def test_analysis_tick_without_an_owner_files_no_row(self, monkeypatch):
        _settings(monkeypatch, learning_system_owner_email="")

        job_id = run_pattern_analysis_job()

        assert job_id is None
        assert _rows("pattern_analysis") == []

    def test_explicit_user_id_beats_the_configured_owner(self, monkeypatch):
        """User-triggered work binds the actor; the system owner only serves a
        tick that has no actor to ask."""
        configured = _make_user(OWNER_EMAIL)
        actor = _make_user(OTHER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        job_id = run_scheduled_research(
            scope="manual",
            user_id=actor,
            instruments=["CRYPTO:KRAKEN:BTC-USD"],
            intervals=[1440],
        )

        job = _fetch(job_id)
        assert job.user_id == actor
        assert job.user_id != configured

    def test_two_ticks_in_one_slot_replay_one_row(self, monkeypatch):
        """A double-fired tick is one batch, not two helpings of quota."""
        _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)

        kwargs = {
            "scope": "daily",
            "instruments": ["CRYPTO:KRAKEN:BTC-USD"],
            "intervals": [1440],
            "operation_key": "cron:daily_research:2026-11-22T02",
        }
        first = run_scheduled_research(**kwargs)
        second = run_scheduled_research(**kwargs)

        assert first == second
        assert len(_rows("scheduled_study")) == 1


# ---------------------------------------------------------------------------
# 二: the lifecycle of one row
# ---------------------------------------------------------------------------


class TestQueueLifecycle:
    def test_drain_claims_and_settles_the_request_row(self):
        """One id goes pending → running → completed; no second row appears."""
        owner = _make_user(OWNER_EMAIL)
        job_id, replayed = _file_pending_study(owner)
        assert replayed is False

        assert _fetch(job_id).status == "pending"
        assert _fetch(job_id).started_at is None

        with patch.object(worker, "_run_single_study", return_value=STUDY_RESULT) as study:
            ran = drain_learning_queue()

        assert ran == 1
        assert study.call_count == 1
        assert [r.id for r in _rows("scheduled_study")] == [
            job_id
        ], "the executor filed a separate run row"
        job = _fetch(job_id)
        assert job.status == "completed"
        assert job.origin == "user"
        assert job.claimed_by == executor_identity()
        assert job.started_at is not None
        assert job.claimed_at is not None
        assert job.completed_at is not None
        assert job.result_json["successful"] == 1
        assert job.result_json["failed"] == 0
        assert job.error_details is None

    def test_claim_is_committed_before_the_handler_starts(self):
        """A batch that dies mid-flight must be visibly in flight.

        The handler peeks at the row through a session nobody else holds; if
        the claim were still an uncommitted INSERT, that read would answer
        ``pending``.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        seen = {}

        def _peek(db, user_id, instrument_id, interval_minutes):
            row = _fetch(job_id)
            seen["status"] = row.status
            seen["claimed_by"] = row.claimed_by
            seen["started_at_set"] = row.started_at is not None
            return STUDY_RESULT

        with patch.object(worker, "_run_single_study", side_effect=_peek):
            drain_learning_queue()

        assert seen == {
            "status": "running",
            "claimed_by": executor_identity(),
            "started_at_set": True,
        }

    def test_drain_settles_each_row_once_and_never_repeats_the_work(self):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)

        with patch.object(worker, "_run_single_study", return_value=STUDY_RESULT) as study:
            assert drain_learning_queue() == 1
            assert drain_learning_queue() == 0
            assert drain_learning_queue() == 0

        assert study.call_count == 1
        assert _fetch(job_id).status == "completed"

    def test_claim_takes_pending_rows_in_fifo_order(self):
        owner = _make_user(OWNER_EMAIL)
        older, _ = _file_pending_study(owner, intervals=[1440])
        newer, _ = _file_pending_study(owner, intervals=[240])
        _apply(older, created_at=utcnow_aware() - timedelta(seconds=3600))

        db = TestSession()
        try:
            claimed = claim_pending_learning_job_ids(db, executor="test-exec", limit=1)
        finally:
            db.close()

        assert claimed == [older], "FIFO by created_at is the documented claim order"
        assert _fetch(older).status == "running"
        assert _fetch(older).claimed_by == "test-exec"
        assert _fetch(newer).status == "pending"
        assert _fetch(newer).claimed_by is None

    def test_executor_that_does_not_own_the_row_refuses_to_run_it(self):
        """A second executor cannot double-run a row it never claimed."""
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)

        with patch.object(worker, "_run_single_study") as study:
            assert execute_learning_job(job_id, executor="someone-else") is None

        assert study.call_count == 0
        job = _fetch(job_id)
        assert job.status == "pending"
        assert job.claimed_by is None

    def test_settle_refuses_a_row_another_executor_holds(self):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)

        db = TestSession()
        try:
            claim_pending_learning_job_ids(db, executor="holder", limit=1)
            written = settle_learning_job(
                db,
                job_id,
                executor="intruder",
                status="completed",
                result_json={"successful": 1},
            )
        finally:
            db.close()

        assert written is False
        job = _fetch(job_id)
        assert job.status == "running"
        assert job.claimed_by == "holder"
        assert job.completed_at is None

    def test_a_missing_row_is_reported_absent_not_settled(self):
        with patch.object(worker, "_run_single_study") as study:
            assert execute_learning_job(uuid.uuid4(), executor="x") is None

        assert study.call_count == 0

    def test_unsupported_job_type_fails_naming_the_type(self):
        """A row this build cannot serve must say so in the job record."""
        owner = _make_user(OWNER_EMAIL)
        db = TestSession()
        try:
            job = LearningJob(
                user_id=owner,
                status="pending",
                origin="user",
                job_type="parameter_proposal",
                config_json={"lookback_runs": 5},
            )
            db.add(job)
            db.commit()
            job_id = job.id
        finally:
            db.close()

        with patch.object(worker, "_run_single_study") as study:
            assert drain_learning_queue() == 1

        assert study.call_count == 0
        row = _fetch(job_id)
        assert row.status == "failed"
        assert "unsupported_job_type: parameter_proposal" in row.error_details
        assert row.completed_at is not None


# ---------------------------------------------------------------------------
# 二: an executor that stops reporting
# ---------------------------------------------------------------------------


class TestLeaseExpiry:
    def _claim(self, job_id: uuid.UUID, *, executor: str) -> None:
        db = TestSession()
        try:
            claim_pending_learning_job_ids(db, executor=executor, limit=1)
        finally:
            db.close()

    def test_expired_lease_is_settled_failed_and_not_requeued(self, monkeypatch):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        _settings(monkeypatch, learning_lease_seconds=60)
        self._claim(job_id, executor=executor_identity())
        _apply(job_id, claimed_at=utcnow_aware() - timedelta(seconds=3600))

        with patch.object(worker, "_run_single_study") as study:
            assert reclaim_expired_learning_leases() == 1
            assert drain_learning_queue() == 0

        assert study.call_count == 0, "an expired claim was re-run without being asked"
        job = _fetch(job_id)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "lease_expired" in job.error_details
        assert "submit a new request" in job.error_details

    def test_a_lease_still_inside_the_window_is_left_alone(self, monkeypatch):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        _settings(monkeypatch, learning_lease_seconds=1800)
        self._claim(job_id, executor=executor_identity())

        assert reclaim_expired_learning_leases() == 0
        assert _fetch(job_id).status == "running"

    def test_legacy_running_row_without_a_claim_is_judged_on_started_at(
        self, monkeypatch
    ):
        """Rows written by the pre-queue scheduler still have to reach a state."""
        owner = _make_user(OWNER_EMAIL)
        db = TestSession()
        try:
            job = LearningJob(
                user_id=owner,
                status="running",
                origin="schedule",
                job_type="scheduled_study",
                config_json={"instruments": [], "intervals": []},
                started_at=utcnow_aware() - timedelta(seconds=7200),
            )
            db.add(job)
            db.commit()
            job_id = job.id
        finally:
            db.close()
        _settings(monkeypatch, learning_lease_seconds=60)

        assert reclaim_expired_learning_leases() == 1
        assert _fetch(job_id).status == "failed"

    def test_late_executor_cannot_write_a_result_nobody_saw(self, monkeypatch):
        """Once the lease expires, the reclaim owns the row.

        Without the ``claimed_by`` predicate the returning executor would
        overwrite ``failed`` with ``completed``, and the job list would claim a
        batch whose outcome is genuinely unknown.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        holder = "holder-process"
        _settings(monkeypatch, learning_lease_seconds=60)
        self._claim(job_id, executor=holder)
        _apply(job_id, claimed_at=utcnow_aware() - timedelta(seconds=3600))

        assert reclaim_expired_learning_leases() == 1

        late = TestSession()
        try:
            wrote = settle_learning_job(
                late,
                job_id,
                executor=holder,
                status="completed",
                result_json={"successful": 4},
            )
        finally:
            late.close()

        assert wrote is False
        job = _fetch(job_id)
        assert job.status == "failed"
        assert "lease_expired" in job.error_details
        assert (job.result_json or {}).get("successful") is None

        with patch.object(worker, "_run_single_study") as study:
            assert execute_learning_job(job_id, executor=holder) is None
        assert study.call_count == 0

    def test_unwritable_terminal_write_still_records_the_outcome(self):
        """The handler finished a batch; a broken writer may not lose that.

        The success path originally had no fallback at all: when the settle
        write itself raised, the row stayed ``running`` and the only thing that
        could ever report the batch was the lease reclaim — which would have
        called a completed run ``failed``. The outcome has to survive on a
        session the handler cannot poison.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        executor = executor_identity()
        self._claim(job_id, executor=executor)

        with (
            patch.object(worker, "_run_single_study", return_value=STUDY_RESULT),
            patch.object(
                worker, "settle_learning_job", side_effect=RuntimeError("session is poisoned")
            ),
        ):
            assert execute_learning_job(job_id, executor=executor) == "completed"

        job = _fetch(job_id)
        assert job.status == "completed"
        assert job.completed_at is not None
        assert job.result_json["successful"] == 1
        assert job.result_json["failed"] == 0
        assert job.error_details is None

    def test_handler_death_still_leaves_a_terminal_record(self):
        """The handler raised and the worker's session can no longer write.

        Both writes are broken here in turn, and the row must still end
        ``failed`` carrying the handler's reason — not the reason the writer
        failed.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)
        executor = executor_identity()
        self._claim(job_id, executor=executor)

        def _boom(db, job):
            raise RuntimeError("study engine died mid-batch")

        with (
            patch.dict(worker._JOB_HANDLERS, {"scheduled_study": _boom}),
            patch.object(
                worker, "settle_learning_job", side_effect=RuntimeError("session is poisoned")
            ),
        ):
            assert execute_learning_job(job_id, executor=executor) == "failed"

        job = _fetch(job_id)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "study engine died mid-batch" in job.error_details
        assert "session is poisoned" not in job.error_details


# ---------------------------------------------------------------------------
# 一/二: what the job list is allowed to claim
# ---------------------------------------------------------------------------


class TestStatusTruthfulness:
    """``status`` and ``error_details`` reach ``GET /learning/jobs`` verbatim."""

    def test_every_combo_failing_is_recorded_as_failed(self):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)

        with patch.object(
            worker, "_run_single_study", side_effect=RuntimeError("bars unavailable")
        ):
            assert drain_learning_queue() == 1

        job = _fetch(job_id)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "bars unavailable" in job.error_details
        assert job.result_json["successful"] == 0
        assert job.result_json["failed"] == 1

    def test_partial_failure_stays_completed_with_the_reason_attached(self):
        """One combo succeeding is a completed batch with a known gap.

        ``failed`` has to mean "produced nothing", or a real total loss and this
        case read the same way.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner, intervals=[1440, 240])

        with patch.object(
            worker,
            "_run_single_study",
            side_effect=[STUDY_RESULT, RuntimeError("provider quota exhausted")],
        ):
            assert drain_learning_queue() == 1

        job = _fetch(job_id)
        assert job.status == "completed"
        assert (job.result_json["successful"], job.result_json["failed"]) == (1, 1)
        assert job.result_json["total_combos"] == 2
        assert "provider quota exhausted" in job.error_details

    def test_empty_grid_runs_nothing_and_reports_no_combos(self):
        """An empty grid was once silently widened into the full 3x4 workload.

        The API refuses ``[]``; this node pins the executor's behaviour if such
        a row exists anyway: zero studies actually run, and the recorded count
        says zero instead of borrowing a default.
        """
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner, instruments=[], intervals=[])

        with patch.object(worker, "_run_single_study") as study:
            assert drain_learning_queue() == 1

        assert study.call_count == 0
        job = _fetch(job_id)
        assert job.status == "completed"
        assert job.result_json["total_combos"] == 0
        assert job.result_json["results"] == []

    def test_failure_reason_reaches_the_ui_without_path_or_dsn(self):
        owner = _make_user(OWNER_EMAIL)
        job_id, _ = _file_pending_study(owner)

        with patch.object(
            worker,
            "_run_single_study",
            side_effect=RuntimeError(
                "connect failed for postgresql://alethra:s3cr3t@db.internal:5432/ai_lab "
                "from /Users/operator/repos/alethra/backend"
            ),
        ):
            drain_learning_queue()

        job = _fetch(job_id)
        assert "s3cr3t" not in job.error_details
        assert "db.internal" not in job.error_details
        assert "/Users/operator" not in job.error_details
        assert "<redacted-url>" in job.error_details
        assert "<path>" in job.error_details
        # Which combo failed is still answerable after scrubbing.
        assert "CRYPTO:KRAKEN:BTC-USD @ 1440m" in job.error_details


# ---------------------------------------------------------------------------
# 二/七: the second job type, and what it may attribute
# ---------------------------------------------------------------------------


class TestPatternAnalysis:
    def test_clean_pass_settles_the_request_row(self, monkeypatch):
        owner = _make_user(OWNER_EMAIL)
        _settings(monkeypatch, learning_system_owner_email=OWNER_EMAIL)
        monkeypatch.setattr(
            worker, "analyze_skill_patterns", lambda db, **kw: {"per_exit_config": {}}
        )

        job_id = run_pattern_analysis_job()

        assert _fetch(job_id).status == "pending"
        assert drain_learning_queue() == 1
        job = _fetch(job_id)
        assert job.status == "completed"
        assert job.user_id == owner
        assert job.completed_at is not None
        assert job.result_json["proposals_created"] == 0
        assert job.error_details is None

    def test_handler_analyses_the_rows_owner_with_the_rows_own_lookback(self):
        """Neither the first user nor the current setting may decide this.

        The request row carries its own ``lookback_runs``; an executor that
        re-read a global setting would run an analysis nobody asked for.
        """
        owner = _make_user(OWNER_EMAIL)
        bystander = _make_user(OTHER_EMAIL)
        job_id = _file_pending_analysis(owner, lookback_runs=7)
        assert bystander != owner

        seen = {}

        def _capture(db, *, user_id, lookback_runs):
            seen["user_id"] = user_id
            seen["lookback_runs"] = lookback_runs
            return {"per_exit_config": {}}

        with patch.object(worker, "analyze_skill_patterns", side_effect=_capture):
            assert drain_learning_queue() == 1

        assert seen == {"user_id": owner, "lookback_runs": 7}
        assert _fetch(job_id).status == "completed"

    def test_analysis_failure_leaves_a_terminal_record(self):
        """The row is not rolled back with the work it failed to finish."""
        owner = _make_user(OWNER_EMAIL)
        job_id = _file_pending_analysis(owner)

        with patch.object(
            worker,
            "analyze_skill_patterns",
            side_effect=RuntimeError(
                "no bars under /Users/operator/repos/alethra for postgresql://u:p@h/db"
            ),
        ):
            assert drain_learning_queue() == 1

        job = _fetch(job_id)
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "<redacted-url>" in job.error_details
        assert "<path>" in job.error_details
        assert "u:p@h" not in job.error_details

    def test_no_run_for_this_owner_files_no_proposal(self):
        """An analysis that names an exit is not yet evidence about a trade."""
        owner = _make_user(OWNER_EMAIL)
        job_id = _file_pending_analysis(owner)

        with patch.object(
            worker,
            "analyze_skill_patterns",
            return_value={"per_exit_config": {EXIT_ID: {"trade_count": 40}}},
        ):
            assert drain_learning_queue() == 1

        assert _proposals() == []
        assert _fetch(job_id).result_json["proposals_created"] == 0

    def test_proposal_is_attributed_to_this_owners_trades_and_linked_to_its_row(self):
        """Both halves of the attribution the owner reads.

        ``rule_id`` comes from the owner's own trades — the removed fallback
        would have said ``breakout_trend`` — and ``learning_job_id`` is the row
        this drain settled, so the proposal stays traceable to its run.
        """
        owner = _make_user(OWNER_EMAIL)
        _make_run_with_trades(owner, rule_id=MEASURED_RULE, exit_id=EXIT_ID)
        job_id = _file_pending_analysis(owner)

        with (
            patch.object(
                worker,
                "analyze_skill_patterns",
                return_value={"per_exit_config": {EXIT_ID: {"trade_count": 40}}},
            ),
            patch.object(
                worker, "propose_parameter_adjustment", return_value=_adjustment()
            ),
            patch.object(
                worker,
                "validate_proposal",
                return_value={"passed": False, "error": "skipped_for_test"},
            ),
        ):
            assert drain_learning_queue() == 1

        proposals = _proposals()
        assert len(proposals) == 1
        proposal = proposals[0]
        assert str(proposal.learning_job_id) == str(job_id)
        assert str(proposal.user_id) == str(owner)
        assert proposal.rule_id == MEASURED_RULE
        assert proposal.rule_id != FABRICATED_RULE
        assert proposal.exit_id == EXIT_ID
        # A hypothesis stays pending: the executor never promotes its own work.
        assert proposal.status == "pending"
        assert _fetch(job_id).result_json["proposals_created"] == 1


# ---------------------------------------------------------------------------
# 二/七: attribution cannot cross tenants
# ---------------------------------------------------------------------------


class TestOwnerScopedAttribution:
    def test_rule_attribution_reads_only_this_owners_trades(self):
        """A run id from elsewhere cannot lend its trades to this owner.

        The other tenant's own lookup must return the rule its trades carry, so
        a ``None`` here can only mean the owner predicate, not an empty table.
        """
        owner = _make_user(OWNER_EMAIL)
        other = _make_user(OTHER_EMAIL)
        other_run = _make_run_with_trades(other, rule_id=MEASURED_RULE, exit_id=EXIT_ID)

        db = TestSession()
        try:
            assert _find_best_rule_for_exit(db, other, other_run, EXIT_ID) == MEASURED_RULE
            assert _find_best_rule_for_exit(db, owner, other_run, EXIT_ID) is None
            assert _find_best_rule_for_exit(db, other, other_run, "no_such_exit") is None
        finally:
            db.close()

    def test_exit_lookup_resolves_registered_ids_only(self):
        """A lookup, not a guess: an unknown id gets nothing back."""
        known = next(iter(EXIT_BY_ID))
        assert worker._resolve_exit_config(known) is not None
        assert worker._resolve_exit_config("no_such_exit_id") is None

    def test_bars_from_json_parses_stored_rows(self):
        now = datetime.now(timezone.utc)
        bars = _bars_from_json(
            [
                {
                    "open_time": now.isoformat(),
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
# 三/二: what the trigger endpoint may promise
# ---------------------------------------------------------------------------


class TestExecutorStatus:
    """Admission reads the live executor, never a configuration flag.

    ``ENABLE_LEARNING_SCHEDULER=true`` describes what the deployment asked for;
    these nodes assert what this process can actually do.
    """

    def test_no_scheduler_in_this_process(self):
        result = learning_executor_status()

        assert result["available"] is False
        assert result["reason"] == "no_learning_scheduler_in_this_process"
        assert "ENABLE_LEARNING_SCHEDULER" in result["remedy"]

    def test_scheduler_created_but_never_started(self):
        register_learning_scheduler(create_learning_scheduler())

        result = learning_executor_status()

        assert result["available"] is False
        assert result["reason"] == "learning_scheduler_not_running"

    def test_scheduler_without_a_drain_is_not_an_executor(self):
        class _NoDrain(_StubScheduler):
            def get_job(self, job_id):
                return None

        register_learning_scheduler(_NoDrain())

        result = learning_executor_status()

        assert result["available"] is False
        assert result["reason"] == "learning_queue_drain_missing"
        assert "pending" in result["remedy"]

    def test_paused_drain_is_not_an_executor(self):
        class _Paused:
            next_run_time = None

        register_learning_scheduler(_StubScheduler(drain=_Paused()))

        result = learning_executor_status()

        assert result["available"] is False
        assert result["reason"] == "learning_queue_drain_paused"

    def test_scheduler_that_cannot_be_read_is_not_an_executor(self):
        class _Unreadable:
            running = True

            def get_job(self, job_id):
                raise RuntimeError("jobstore is gone")

        register_learning_scheduler(_Unreadable())

        result = learning_executor_status()

        assert result["available"] is False
        assert result["reason"] == "learning_scheduler_unreadable"

    def test_live_drain_admits_work_and_names_the_next_claim(self, monkeypatch):
        _settings(monkeypatch, learning_lease_seconds=900)
        register_learning_scheduler(_StubScheduler())

        result = learning_executor_status()

        assert result["available"] is True
        assert result["reason"] == "learning_queue_drain_running"
        assert result["next_claim_at"].startswith("2099-01-01")
        assert result["lease_seconds"] == 900


class TestSchedulerConstruction:
    def test_registers_two_producers_and_one_executor(self):
        job_ids = {j.id for j in create_learning_scheduler().get_jobs()}

        assert {"daily_research", "weekly_analysis", DRAIN_JOB_ID} <= job_ids

    def test_starts_and_stops(self):
        scheduler = create_learning_scheduler()
        scheduler.start()
        assert scheduler.running
        scheduler.shutdown(wait=False)
        assert not scheduler.running
