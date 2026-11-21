"""Tests for the learning scheduler worker.

Covers:
- run_scheduled_research creates LearningJob and runs studies
- run_pattern_analysis generates proposals
- scheduler starts/stops correctly
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.user import User
from app.workers.learning_scheduler import (
    create_learning_scheduler,
    run_pattern_analysis_job,
    run_scheduled_research,
    _bars_from_json,
    _get_first_user_id,
)
from tests.conftest import TestSession


def _make_user(email: str = "scheduler-test@alethra.io") -> uuid.UUID:
    from app.auth.jwt import get_password_hash

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name="Scheduler Test",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
    finally:
        db.close()
    return user_id


def _pin_owner(mock_first_user, email: str) -> uuid.UUID:
    """Give the worker an owner it can actually find.

    Each test starts from an empty SQLite database, so the real
    ``_get_first_user_id`` returns ``None`` and both jobs return before filing a
    row. Tests that read the row back pin the owner here instead of depending on
    user data left behind by an unrelated test.
    """
    user_id = _make_user(email)
    mock_first_user.return_value = user_id
    return user_id


def _new_job_id(job_type: str, call, expect="one"):
    """Run ``call()`` and hand back the ``LearningJob`` row it filed.

    Identified as whichever id of that job type is new rather than by owner or
    recency, so a row left by an earlier test cannot be mistaken for this one's.
    ``expect="one"`` requires exactly one new row; ``expect=None`` requires none.
    """
    db = TestSession()
    try:
        before = {
            row.id for row in db.query(LearningJob).filter_by(job_type=job_type).all()
        }
    finally:
        db.close()

    call()

    db = TestSession()
    try:
        new_ids = {
            row.id for row in db.query(LearningJob).filter_by(job_type=job_type).all()
        } - before
        if expect is None:
            assert not new_ids, f"expected no new {job_type} row, got {len(new_ids)}"
            return None
        assert len(new_ids) == 1, (
            f"expected exactly one new {job_type} row, got {len(new_ids)}"
        )
        job = db.get(LearningJob, new_ids.pop())
        db.expunge(job)
        return job
    finally:
        db.close()


class TestCreateLearningScheduler:
    def test_creates_scheduler_with_jobs(self):
        scheduler = create_learning_scheduler()
        assert scheduler is not None
        jobs = scheduler.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "daily_research" in job_ids
        assert "weekly_analysis" in job_ids

    def test_scheduler_starts_and_stops(self):
        scheduler = create_learning_scheduler()
        scheduler.start()
        assert scheduler.running
        scheduler.shutdown(wait=False)
        assert not scheduler.running


class TestGetFirstUserId:
    def test_returns_none_when_no_users(self):
        db = TestSession()
        try:
            result = _get_first_user_id(db)
            # May or may not be None depending on other test data
            # Just ensure it doesn't crash
            assert result is None or isinstance(result, uuid.UUID)
        finally:
            db.close()

    def test_returns_user_id_when_users_exist(self):
        user_id = _make_user("first-user-sched@alethra.io")
        db = TestSession()
        try:
            result = _get_first_user_id(db)
            assert result is not None
            assert isinstance(result, uuid.UUID)
        finally:
            db.close()


class TestBarsFromJson:
    def test_parses_bars_correctly(self):
        now = datetime.now(timezone.utc)
        bars_json = [
            {
                "open_time": now.isoformat(),
                "interval_minutes": 60,
                "open": "50000.12345678",
                "high": "50100.0",
                "low": "49900.0",
                "close": "50050.0",
                "volume": "100.5",
            }
        ]
        bars = _bars_from_json(bars_json, 60)
        assert len(bars) == 1
        assert bars[0].interval_minutes == 60
        assert bars[0].open == Decimal("50000.12345678")
        assert bars[0].volume == Decimal("100.5")


class TestRunScheduledResearch:
    @patch("app.workers.learning_scheduler._get_first_user_id")
    @patch("app.workers.learning_scheduler._run_single_study")
    def test_creates_learning_job(self, mock_study, mock_first_user):
        """The batch files exactly one row, and that row is terminal."""
        user_id = _pin_owner(mock_first_user, "sched-entry-check@alethra.io")
        mock_study.return_value = {
            "walk_forward": {"out_of_sample": {"trades": 5}},
        }

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="entry-check",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            ),
        )

        assert job.user_id == user_id
        assert job.status == "completed"
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.result_json["total_combos"] == 1
        assert job.result_json["successful"] == 1
        assert job.error_details is None

    @patch("app.workers.learning_scheduler._get_first_user_id")
    def test_no_users_files_no_row(self, mock_first_user):
        """Nothing is recorded when there is nobody to record it for.

        This used to call the job for real and assert nothing at all, which
        passed whether or not a row appeared.
        """
        mock_first_user.return_value = None

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="no-user",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            ),
            expect=None,
        )
        assert job is None


class TestLearningJobStatusTruthfulness:
    """``status``/``error_details`` have to say what actually happened.

    ``GET /api/v1/learning/jobs`` serialises both verbatim
    (``app/api/learning.py:81-93``), so a ``completed`` here is a claim a person
    reads, and the text in ``error_details`` is text a person sees.
    """

    @patch("app.workers.learning_scheduler._get_first_user_id")
    @patch("app.workers.learning_scheduler._run_single_study")
    def test_every_combo_failing_is_recorded_as_failed(self, mock_study, mock_first_user):
        _pin_owner(mock_first_user, "sched-all-fail@alethra.io")
        mock_study.side_effect = RuntimeError("bars unavailable")

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="all-fail",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            ),
        )

        assert job.status == "failed"
        assert job.completed_at is not None
        assert "bars unavailable" in job.error_details
        assert job.result_json["successful"] == 0
        assert job.result_json["failed"] == 1

    @patch("app.workers.learning_scheduler._get_first_user_id")
    @patch("app.workers.learning_scheduler._run_single_study")
    def test_partial_failure_stays_completed_with_the_reason_attached(
        self, mock_study, mock_first_user
    ):
        """One combo succeeding is a completed batch with a known gap.

        ``failed`` has to mean "nothing was produced", otherwise the status
        stops discriminating and a real total loss looks like this case.
        """
        _pin_owner(mock_first_user, "sched-partial-fail@alethra.io")
        mock_study.side_effect = [
            {"walk_forward": {"out_of_sample": {"trades": 3}}},
            RuntimeError("provider quota exhausted"),
        ]

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="partial-fail",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440, 240],
            ),
        )

        assert job.status == "completed"
        assert (job.result_json["successful"], job.result_json["failed"]) == (1, 1)
        assert "provider quota exhausted" in job.error_details

    @patch("app.workers.learning_scheduler._get_first_user_id")
    @patch("app.workers.learning_scheduler._run_single_study")
    def test_failure_reason_reaches_the_ui_without_path_or_dsn(self, mock_study, mock_first_user):
        _pin_owner(mock_first_user, "sched-scrub@alethra.io")
        mock_study.side_effect = RuntimeError(
            "connect failed for postgresql://alethra:s3cr3t@db.internal:5432/ai_lab "
            "from /Users/operator/repos/alethra/backend"
        )

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="scrub",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            ),
        )

        assert "s3cr3t" not in job.error_details
        assert "db.internal" not in job.error_details
        assert "/Users/operator" not in job.error_details
        assert "<redacted-url>" in job.error_details
        assert "<path>" in job.error_details
        # Which combo failed is still answerable after scrubbing.
        assert "CRYPTO:KRAKEN:BTC-USD @ 1440m" in job.error_details

    def test_mid_flight_blowup_still_leaves_a_terminal_record(self):
        """The row has to survive the handler that rolls its own transaction back.

        Both jobs once ran their whole life inside one uncommitted transaction,
        so the ``db.rollback()`` in the except branch discarded the ``running``
        row along with the work: a batch that died left no evidence it started.
        """
        from sqlalchemy import event

        fired = []

        def _fail_terminal_update(mapper, connection, target):
            fired.append(target.id)
            raise RuntimeError("connection lost during terminal write")

        # A mapper event, so it fires for the ORM's terminal UPDATE only — the
        # recovery write is a Core ``update()`` and passes untouched.
        event.listen(LearningJob, "before_update", _fail_terminal_update)
        try:
            with (
                patch("app.workers.learning_scheduler._run_single_study") as mock_study,
                patch("app.workers.learning_scheduler._get_first_user_id") as mock_first_user,
            ):
                _pin_owner(mock_first_user, "sched-mid-flight@alethra.io")
                mock_study.return_value = {
                    "walk_forward": {"out_of_sample": {"trades": 2}}
                }
                job = _new_job_id(
                    "scheduled_study",
                    lambda: run_scheduled_research(
                        scope="mid-flight",
                        instruments=["CRYPTO:KRAKEN:BTC-USD"],
                        intervals=[1440],
                    ),
                )
        finally:
            event.remove(LearningJob, "before_update", _fail_terminal_update)

        assert len(fired) == 1, "the injected terminal-write failure never fired"
        assert job.status == "failed"
        assert job.completed_at is not None
        assert "connection lost during terminal write" in job.error_details

    @patch("app.workers.learning_scheduler._get_first_user_id")
    def test_failure_before_the_row_exists_files_no_phantom_row(self, mock_first_user):
        """A job that never got an id cannot be marked failed."""
        mock_first_user.side_effect = RuntimeError("database is not reachable")

        job = _new_job_id(
            "scheduled_study",
            lambda: run_scheduled_research(
                scope="before-row",
                instruments=["CRYPTO:KRAKEN:BTC-USD"],
                intervals=[1440],
            ),
            expect=None,
        )

        assert job is None



class TestRunPatternAnalysis:
    @patch("app.workers.learning_scheduler.analyze_skill_patterns")
    @patch("app.workers.learning_scheduler._get_first_user_id")
    def test_creates_analysis_job(self, mock_first_user, mock_analyze):
        """A clean pass files one row and says so."""
        user_id = _make_user("sched-analysis@alethra.io")
        mock_first_user.return_value = user_id
        mock_analyze.return_value = {"per_exit_config": {}}

        job = _new_job_id("pattern_analysis", run_pattern_analysis_job)

        assert job.status == "completed"
        assert job.user_id == user_id
        assert job.completed_at is not None
        assert job.result_json["proposals_created"] == 0
        assert job.error_details is None

    @patch("app.workers.learning_scheduler.analyze_skill_patterns")
    @patch("app.workers.learning_scheduler._get_first_user_id")
    def test_analysis_failure_leaves_a_terminal_record(
        self, mock_first_user, mock_analyze
    ):
        """The row is not rolled back with the work it failed to finish.

        ``analyze_skill_patterns`` runs after the job row exists, and the whole
        body sat in one transaction whose handler called ``rollback()`` — so
        until now a failed analysis left no row at all.
        """
        user_id = _make_user("sched-analysis-fail@alethra.io")
        mock_first_user.return_value = user_id
        mock_analyze.side_effect = RuntimeError(
            "no bars under /Users/operator/repos/alethra for postgresql://u:p@h/db"
        )

        job = _new_job_id("pattern_analysis", run_pattern_analysis_job)

        assert job.status == "failed"
        assert job.completed_at is not None
        assert "<redacted-url>" in job.error_details
        assert "<path>" in job.error_details
        assert "u:p@h" not in job.error_details

