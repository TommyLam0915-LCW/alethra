"""Tests for the learning API endpoints.

Covers:
- GET /learning/jobs returns one owner's page with a real total
- POST /learning/jobs/trigger queues one row owned by the caller, and refuses
  when no executor could claim it
- resubmission safety: ``Idempotency-Key`` replay, conflict, per-owner
  namespace, and the database constraint underneath it
- the lifecycle a caller is promised: the row this route files is the row the
  executor settles
- GET /learning/proposals returns proposals
- POST /learning/proposals/{id}/approve records the owner decision
- POST /learning/proposals/{id}/deploy updates status
- GET /learning/analysis returns pattern data

Nothing here runs real research: the trigger is a queue write, and the one node
that drains the queue replaces ``_run_single_study`` so no provider call is made.
"""

import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.lib.timeutil import utcnow_aware
from app.models.audit_event import AuditEvent
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from app.workers import learning_scheduler as worker
from app.workers.learning_scheduler import (
    DRAIN_JOB_ID,
    drain_learning_queue,
    register_learning_scheduler,
)
from tests.conftest import TestSession
from tests.lanes import pg_only

STUDY_RESULT = {"walk_forward": {"out_of_sample": {"trades": 5}}}


class _StubDrainJob:
    """A drain job that reports its next tick without ever firing one."""

    next_run_time = datetime(2099, 1, 1, tzinfo=timezone.utc)


class _StubScheduler:
    """What ``learning_executor_status`` reads for "an executor is live here".

    The API admits a submission only when it can name a running drain, so these
    nodes need that answer without starting a real APScheduler thread — a live
    scheduler would run real research against the providers.
    """

    running = True

    def get_job(self, job_id):
        return _StubDrainJob() if job_id == DRAIN_JOB_ID else None


@pytest.fixture
def executor():
    """Publish a readable-but-inert executor for this test, then withdraw it."""
    register_learning_scheduler(_StubScheduler())
    yield
    register_learning_scheduler(None)


@pytest.fixture(autouse=True)
def _no_executor_leakage():
    register_learning_scheduler(None)
    yield
    register_learning_scheduler(None)


def _make_user_and_get_headers(client, email: str = "learning-api@alethra.io"):
    """Register a user and return auth headers."""
    resp = client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "testpass123",
            "display_name": "Learning API Test",
            "user_type": "individual",
        },
    )
    if resp.status_code == 400:
        pass  # already registered
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "testpass123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _get_user_id(headers: dict) -> uuid.UUID:
    """Extract user_id from auth headers by decoding the JWT.

    Decoded with the same library and secret the auth dependency uses
    (`jose`, `app/config.py:37-38`), so the signature is verified rather than
    skipped — this helper previously imported `jwt` (PyJWT), which is not a
    declared dependency and could never resolve.
    """
    from jose import jwt

    from app.config import get_settings

    settings = get_settings()
    token = headers["Authorization"].split(" ")[1]
    payload = jwt.decode(
        token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
    )
    return uuid.UUID(payload["sub"])


def _make_run_with_trades(user_id: uuid.UUID) -> uuid.UUID:
    """Create a stored run with trades for analysis."""
    db = TestSession()
    try:
        now = datetime.now(timezone.utc)
        run = StrategyStudyRun(
            user_id=user_id,
            lane="strategy_research_simulated",
            research_version="1.0.0",
            instrument_id="CRYPTO:KRAKEN:BTC-USD",
            interval_minutes=1440,
            provider_id="kraken",
            data_nature="venue_completed_bars",
            bar_count=200,
            first_open_time=now,
            last_open_time=now,
            settings_json={"starting_cash": "10000"},
            walk_forward_json={"folds": []},
            bars_json=[
                {
                    "open_time": now.isoformat(),
                    "interval_minutes": 1440,
                    "open": "50000.0",
                    "high": "50100.0",
                    "low": "49900.0",
                    "close": "50050.0",
                    "volume": "100.0",
                }
                for _ in range(200)
            ],
            trade_count=5,
        )
        db.add(run)
        db.flush()

        cash = Decimal("10000")
        for i in range(5):
            pnl = Decimal("50.0") if i < 3 else Decimal("-30.0")
            cash = cash + pnl
            db.add(
                StrategyStudyTrade(
                    run_id=run.id,
                    trade_index=i,
                    fold=0,
                    rule_id="breakout_trend",
                    exit_id="balanced_1_1",
                    direction="long",
                    exit_reason="target" if i < 3 else "stop",
                    outcome="win" if i < 3 else "loss",
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
        run_id = run.id
    finally:
        db.close()
    return run_id


def _make_proposal(
    user_id: uuid.UUID,
    *,
    status: str = "pending",
    evidence_status: str = "hypothesis",
    validation_passed: bool = True,
) -> uuid.UUID:
    """Create a proposal shaped the way learning_scheduler creates one.

    The scheduler writes ``rationale_json["evidence_status"]``
    (learning_scheduler.py:1038), runs the backtest and stores
    ``validation_result`` unconditionally (:1061), and only then decides the
    status (:1065-1066). A fixture that omits either field is not describing a
    product state, and the deploy gate reads both.

    ``status="validated"`` still exists for the node that proves deploy refuses
    a row whose status was set by hand instead of through the approve rung.
    """
    db = TestSession()
    try:
        job = LearningJob(
            user_id=user_id,
            status="completed",
            job_type="pattern_analysis",
            config_json={"instruments": ["BTC-USD"], "intervals": [1440]},
            result_json={"proposals_created": 1},
        )
        db.add(job)
        db.flush()

        proposal = LearningProposal(
            user_id=user_id,
            learning_job_id=job.id,
            rule_id="breakout_trend",
            exit_id="balanced_1_1",
            instrument_id="CRYPTO:KRAKEN:BTC-USD",
            interval_minutes=1440,
            baseline_exit_config={
                "target_atr": "1.0",
                "stop_atr": "1.0",
                "time_stop_bars": 6,
            },
            proposed_exit_config={
                "target_atr": "1.5",
                "stop_atr": "1.0",
                "time_stop_bars": 6,
            },
            rationale_json={
                "pattern": "high_win_rate_low_pnl",
                "baseline_metrics": {"win_rate": 0.6, "avg_pnl": -5.0},
                "evidence_status": evidence_status,
            },
            validation_result={
                "passed": validation_passed,
                "sharpe_before": 0.1,
                "sharpe_after": 0.4,
                "max_dd_before": 20.0,
                "max_dd_after": 15.0,
            },
            status=status,
            validated_at=utcnow_aware() if status == "validated" else None,
        )
        db.add(proposal)
        db.commit()
        db.refresh(proposal)
        proposal_id = proposal.id
    finally:
        db.close()
    return proposal_id


def _readback_proposal(proposal_id: uuid.UUID) -> LearningProposal:
    """Read a proposal back on a session independent of the request's."""
    db = TestSession()
    try:
        return db.scalars(
            select(LearningProposal).where(LearningProposal.id == proposal_id)
        ).one()
    finally:
        db.close()


def _approval_events(proposal_id: uuid.UUID) -> list:
    db = TestSession()
    try:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "learning_proposal",
                    AuditEvent.entity_id == str(proposal_id),
                    AuditEvent.event_type == "proposal_approved",
                )
            ).all()
        )
    finally:
        db.close()


def _job_row(job_id: uuid.UUID) -> LearningJob:
    """Read one job back on a session independent of the request's."""
    db = TestSession()
    try:
        row = db.scalars(select(LearningJob).where(LearningJob.id == job_id)).one()
        db.expunge(row)
        return row
    finally:
        db.close()


def _job_rows_for(user_id: uuid.UUID) -> list:
    db = TestSession()
    try:
        rows = (
            db.query(LearningJob)
            .filter(LearningJob.user_id == user_id)
            .order_by(LearningJob.created_at.asc())
            .all()
        )
        for row in rows:
            db.expunge(row)
        return rows
    finally:
        db.close()


def _queued_events(job_ids) -> list:
    db = TestSession()
    try:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == "learning_job",
                    AuditEvent.event_type == "learning_job_queued",
                    AuditEvent.entity_id.in_([str(i) for i in job_ids]),
                )
            ).all()
        )
    finally:
        db.close()


class TestListLearningJobs:
    def test_returns_empty_when_no_jobs(self, client, auth_headers):
        resp = client.get("/api/v1/learning/jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["jobs"] == []

    def test_returns_jobs_for_user(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        # Create a job directly
        db = TestSession()
        try:
            job = LearningJob(
                user_id=user_id,
                status="completed",
                job_type="scheduled_study",
                config_json={"instruments": ["BTC-USD"]},
            )
            db.add(job)
            db.commit()
        finally:
            db.close()

        resp = client.get("/api/v1/learning/jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert data["jobs"][0]["job_type"] == "scheduled_study"

    def test_total_is_the_whole_set_not_the_page(self, client, auth_headers):
        """"20 rows out of 2,000" must not read back as "20 total".

        ``total`` used to be ``len(jobs)``, which made a page look like the
        complete history and let a truncated list be reported as all the work
        there is.
        """
        user_id = _get_user_id(auth_headers)
        db = TestSession()
        try:
            for _ in range(3):
                db.add(
                    LearningJob(
                        user_id=user_id,
                        status="completed",
                        job_type="scheduled_study",
                        config_json={"instruments": ["BTC-USD"]},
                    )
                )
            db.commit()
        finally:
            db.close()

        resp = client.get(
            "/api/v1/learning/jobs?limit=2&offset=0", headers=auth_headers
        )
        data = resp.json()
        assert (data["total"], len(data["jobs"])) == (3, 2)
        assert data["limit"] == 2 and data["offset"] == 0
        assert data["has_more"] is True

        second = client.get(
            "/api/v1/learning/jobs?limit=2&offset=2", headers=auth_headers
        ).json()
        assert len(second["jobs"]) == 1
        assert second["has_more"] is False
        assert {j["id"] for j in data["jobs"]} & {j["id"] for j in second["jobs"]} == set()

    def test_jobs_of_another_owner_are_not_listed(self, client, auth_headers):
        """The list is scoped to the caller, not to the whole tenant."""
        other_headers = _make_user_and_get_headers(client, "jobs-isolation-b@alethra.io")
        other_id = _get_user_id(other_headers)
        _make_run_with_trades(other_id)  # creates no job; insert one directly
        db = TestSession()
        try:
            db.add(
                LearningJob(
                    user_id=other_id,
                    status="completed",
                    job_type="scheduled_study",
                    config_json={"instruments": ["BTC-USD"]},
                )
            )
            db.commit()
        finally:
            db.close()

        resp = client.get("/api/v1/learning/jobs", headers=auth_headers)
        assert resp.json()["total"] == 0


class TestTriggerLearningJob:
    def test_creates_job(self, client, auth_headers, executor):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "scheduled_study"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["job"]["status"] == "pending"
        assert data["job"]["job_type"] == "scheduled_study"
        # The row belongs to the caller: an endpoint-spawned run used to attribute
        # the batch to "the first user in the table".
        assert data["job"]["user_id"] == str(_get_user_id(auth_headers))
        assert data["job"]["origin"] == "user"
        assert data["replayed"] is False
        assert data["next_claim_at"] is not None

    def test_queued_request_is_audited_once(self, client, auth_headers, executor):
        job_id = uuid.UUID(
            client.post(
                "/api/v1/learning/jobs/trigger",
                json={"job_type": "scheduled_study"},
                headers=auth_headers,
            ).json()["job"]["id"]
        )
        events = _queued_events([job_id])
        assert len(events) == 1
        assert str(job_id) in str(events[0].entity_id)

    def test_rejects_invalid_job_type(self, client, auth_headers, executor):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "invalid_type"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_empty_instruments_is_refused_not_widened(self, client, auth_headers, executor):
        """``[]`` used to be rewritten into the full scheduled grid.

        A request for no instruments that runs three symbols x four grains is a
        bigger run than the caller asked for, charged to their compute.
        """
        for payload in (
            {"job_type": "scheduled_study", "instruments": []},
            {"job_type": "scheduled_study", "intervals": []},
        ):
            resp = client.post(
                "/api/v1/learning/jobs/trigger", json=payload, headers=auth_headers
            )
            assert resp.status_code == 400, resp.json()
        assert _job_rows_for(_get_user_id(auth_headers)) == []

    def test_pattern_analysis_rejects_a_grid_it_does_not_use(
        self, client, auth_headers, executor
    ):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "pattern_analysis", "instruments": ["BTC-USD"]},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_refuses_without_an_executor_and_files_nothing(self, client, auth_headers):
        """No live drain ⇒ the request is refused rather than parked.

        This is the orphan-request defect: the endpoint accepted work that
        nothing could ever claim, and the owner was left with a row at
        ``pending`` and no deadline.
        """
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "scheduled_study"},
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.json()
        detail = resp.json()["detail"]
        assert detail["reason"] == "learning_executor_unavailable"
        assert detail["remedy"]
        assert _job_rows_for(_get_user_id(auth_headers)) == [], "a refused request filed a row"

    def test_trigger_then_drain_settles_the_same_row(self, client, auth_headers, executor):
        """The row this route files is the row the executor finishes.

        One id goes ``pending`` → ``running`` → ``completed``; no second row
        appears, and the caller's request does not sit queued forever.
        """
        user_id = _get_user_id(auth_headers)
        body = {"job_type": "scheduled_study", "instruments": ["CRYPTO:KRAKEN:BTC-USD"], "intervals": [1440]}
        job_id = uuid.UUID(
            client.post(
                "/api/v1/learning/jobs/trigger", json=body, headers=auth_headers
            ).json()["job"]["id"]
        )
        assert _job_row(job_id).status == "pending"

        with patch.object(worker, "_run_single_study", return_value=STUDY_RESULT) as study:
            assert drain_learning_queue() == 1

        assert study.call_count == 1
        assert [r.id for r in _job_rows_for(user_id)] == [job_id]
        job = _job_row(job_id)
        assert (job.status, job.claimed_by) == ("completed", worker.executor_identity())
        assert job.started_at is not None and job.completed_at is not None
        assert job.result_json["successful"] == 1

        listed = client.get("/api/v1/learning/jobs", headers=auth_headers).json()
        assert listed["jobs"][0]["id"] == str(job_id)
        assert listed["jobs"][0]["status"] == "completed"


class TestResubmissionSafety:
    """"三、實作提交重送安全" at the HTTP seam.

    A retry of the same submit must return the task the first one filed; the
    same key carrying a different request must be refused without rewriting the
    first one; and a deliberate second run must not be mistaken for a retry.
    """

    KEY = "journal-double-click-0001"
    BODY = {"job_type": "scheduled_study", "instruments": ["CRYPTO:KRAKEN:BTC-USD"], "intervals": [1440]}

    def _post(self, client, headers, body=None, key=KEY):
        return client.post(
            "/api/v1/learning/jobs/trigger",
            json=body if body is not None else self.BODY,
            headers={**headers, "Idempotency-Key": key},
        )

    def test_same_key_same_content_returns_the_first_row(self, client, auth_headers, executor):
        first = self._post(client, auth_headers)
        assert first.status_code == 201, first.json()
        assert first.json()["replayed"] is False

        retry = self._post(client, auth_headers)
        assert retry.status_code == 201, retry.json()
        assert retry.json()["replayed"] is True
        assert retry.json()["job"]["id"] == first.json()["job"]["id"]

        rows = _job_rows_for(_get_user_id(auth_headers))
        assert [r.id for r in rows] == [uuid.UUID(first.json()["job"]["id"])]
        assert len(_queued_events([r.id for r in rows])) == 1, "the replay re-audited the submit"

    def test_same_key_different_content_is_conflicted_not_rewritten(
        self, client, auth_headers, executor
    ):
        first = self._post(client, auth_headers)
        job_id = uuid.UUID(first.json()["job"]["id"])

        changed = {"job_type": "scheduled_study", "instruments": ["CRYPTO:KRAKEN:ETH-USD"], "intervals": [1440]}
        resp = self._post(client, auth_headers, changed)

        assert resp.status_code == 409, resp.json()
        assert resp.json()["detail"]["reason"] == "idempotency_conflict"

        job = _job_row(job_id)
        assert job.status == "pending"
        assert job.config_json["instruments"] == ["CRYPTO:KRAKEN:BTC-USD"], "the refused retry rewrote the first request"
        assert len(_job_rows_for(_get_user_id(auth_headers))) == 1

    def test_same_key_different_job_type_is_conflicted(self, client, auth_headers, executor):
        self._post(client, auth_headers)
        resp = self._post(client, auth_headers, {"job_type": "pattern_analysis"})
        assert resp.status_code == 409

    def test_a_key_blocks_only_its_own_owner(self, client, auth_headers, executor):
        """Keys live in a per-owner namespace, not a global one."""
        a_id = _get_user_id(auth_headers)
        b_headers = _make_user_and_get_headers(client, "resubmit-bystander@alethra.io")
        b_id = _get_user_id(b_headers)

        a = self._post(client, auth_headers)
        assert a.status_code == 201

        # Identical content and identical key, different owner: this is B's own
        # operation and must not be handed A's row.
        b = self._post(client, b_headers)
        assert b.status_code == 201, b.json()
        assert b.json()["replayed"] is False
        assert b.json()["job"]["user_id"] == str(b_id)
        assert b.json()["job"]["id"] != a.json()["job"]["id"]

        rows = _job_rows_for(a_id) + _job_rows_for(b_id)
        keyed = [r for r in rows if r.operation_key == f"user:{self.KEY}"]
        assert len(keyed) == 2
        assert {r.user_id for r in keyed} == {a_id, b_id}

    def test_unkeyed_submits_are_two_deliberate_runs(self, client, auth_headers, executor):
        """A caller who asks twice means twice."""
        first = client.post(
            "/api/v1/learning/jobs/trigger", json=self.BODY, headers=auth_headers
        )
        second = client.post(
            "/api/v1/learning/jobs/trigger", json=self.BODY, headers=auth_headers
        )
        assert first.json()["job"]["id"] != second.json()["job"]["id"]
        assert second.json()["replayed"] is False
        assert len(_job_rows_for(_get_user_id(auth_headers))) == 2

    def test_key_too_short_is_refused(self, client, auth_headers, executor):
        resp = self._post(client, auth_headers, key="abc")
        assert resp.status_code == 400
        assert len(_job_rows_for(_get_user_id(auth_headers))) == 0

    def test_key_is_stored_namespaced_and_a_cron_key_cannot_be_spoofed(
        self, client, auth_headers, executor
    ):
        """The caller's key is stored under ``user:``, so it cannot collide with
        the slots the scheduler mints for itself."""
        resp = self._post(client, auth_headers, key="x" * 40)
        assert resp.status_code == 201
        stored = _job_rows_for(_get_user_id(auth_headers))[0]
        assert stored.operation_key == f"user:{'x' * 40}"
        assert not stored.operation_key.startswith("cron:")

    def test_unique_index_refuses_a_second_row_for_the_same_submit(
        self, client, auth_headers, executor
    ):
        """The guarantee is the constraint, not a disabled button.

        Two writers that never saw each other's INSERT still cannot both file
        this submit: the owner-scoped partial unique index refuses the loser.
        """
        db_a = TestSession()
        db_b = TestSession()
        try:
            from app.workers.learning_scheduler import create_or_replay_learning_job

            config = {"scope": "manual", "instruments": ["BTC-USD"], "intervals": [1440]}
            job_a, replayed_a = create_or_replay_learning_job(
                db_a,
                user_id=_get_user_id(auth_headers),
                job_type="scheduled_study",
                config_json=config,
                origin="user",
                operation_key=f"user:{self.KEY}",
            )
            assert replayed_a is False
            db_b.expunge_all()
            job_b, replayed_b = create_or_replay_learning_job(
                db_b,
                user_id=_get_user_id(auth_headers),
                job_type="scheduled_study",
                config_json=config,
                origin="user",
                operation_key=f"user:{self.KEY}",
            )
            assert replayed_b is True, "the second writer filed a duplicate row"
            assert job_b.id == job_a.id
        finally:
            db_a.close()
            db_b.close()

    @pg_only
    def test_concurrent_double_click_files_one_row(self, client, auth_headers, executor):
        """Two threads, one submit: one row, and both callers get it.

        PostgreSQL-only. This SQLite lane shares one connection across every
        session, so two concurrent writers cannot exist here at all — measured,
        not assumed: the same node run on this lane produced
        ``sqlite3.DatabaseError: UNIQUE constraint failed`` as a 500, because
        the second thread's INSERT landed on the first thread's transaction on
        the same connection rather than racing it. Reporting that as a product
        defect would blame the harness for a dialect that has no two writers,
        and reporting a pass would claim a race that never happened. The real
        parallel INSERT is run on the PostgreSQL lane, where the loser meets a
        23505 that ``create_or_replay_learning_job`` already handles.
        """
        results = {}
        barrier = threading.Barrier(2)

        def _fire(name):
            barrier.wait()
            resp = self._post(client, auth_headers)
            results[name] = (resp.status_code, resp.json())

        threads = [threading.Thread(target=_fire, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "a concurrent trigger never returned"

        assert set(code for code, _ in results.values()) == {201}, results
        ids = {data["job"]["id"] for _, data in results.values()}
        assert len(ids) == 1, f"one submit produced {len(ids)} rows: {ids}"
        assert sorted(data["replayed"] for _, data in results.values()) == [False, True]

        rows = _job_rows_for(_get_user_id(auth_headers))
        assert [r.id for r in rows] == [uuid.UUID(next(iter(ids)))]


class TestListProposals:
    def test_returns_empty_when_no_proposals(self, client, auth_headers):
        resp = client.get("/api/v1/learning/proposals", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_returns_proposals_for_user(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        _make_proposal(user_id, status="pending")

        resp = client.get("/api/v1/learning/proposals", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_filters_by_status(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        _make_proposal(user_id, status="validated")

        resp = client.get(
            "/api/v1/learning/proposals?status=validated",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for p in data["proposals"]:
            assert p["status"] == "validated"


class TestApproveProposal:
    """The rung between "analysis produced a proposal" and "owner deploys it".

    Nothing in the product wrote ``evidence_status="validated_candidate"``
    before this endpoint: the only occurrence of that value in ``app/`` is the
    deploy gate that demands it (api/learning.py:552), while
    learning_scheduler.py:1038 stores whatever pattern analysis produced
    (hypothesis / descriptive / insufficient) and :1065-1066 forces
    ``status="pending"`` for hypotheses. The ladder therefore had no producer,
    and no proposal the product created could legally reach deploy.
    """

    def test_approve_moves_pending_proposal_to_validated(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id)

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.json()
        body = resp.json()["proposal"]
        assert body["status"] == "validated"
        assert body["validated_at"] is not None
        assert body["deployed_at"] is None

        # The response is not the evidence: read the row back independently.
        stored = _readback_proposal(proposal_id)
        assert stored.status == "validated"
        assert stored.validated_at is not None
        # A JSON column only persists on whole-value reassignment, so assert the
        # stored document rather than the dict the endpoint held in memory.
        assert stored.rationale_json["evidence_status"] == "validated_candidate"
        # Promotion must not discard what the analysis recorded.
        assert stored.rationale_json["pattern"] == "high_win_rate_low_pnl"
        assert stored.validation_result["passed"] is True

    def test_approve_records_one_audit_event_with_human_provenance(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id)

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.json()
        events = _approval_events(proposal_id)
        assert len(events) == 1
        event = events[0]
        assert event.entity_type == "learning_proposal"
        assert event.entity_id == str(proposal_id)
        # auth_headers comes from a password login, so the verified channel is
        # human by contract (app/auth/provenance.py) — not inferred from the
        # endpoint being owner-facing.
        assert event.actor_type == "human"
        assert str(event.actor_id) == str(user_id)
        assert event.payload_json == {
            "rule_id": "breakout_trend",
            "exit_id": "balanced_1_1",
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "status_before": "pending",
            "status_after": "validated",
            "evidence_status_before": "hypothesis",
            "evidence_status_after": "validated_candidate",
        }

    def test_approve_refuses_proposal_whose_validation_failed(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id, validation_passed=False)

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

        assert resp.status_code == 409, resp.json()
        assert resp.json()["detail"]["reason"] == "validation_not_passed"
        stored = _readback_proposal(proposal_id)
        assert stored.status == "pending"
        assert stored.validated_at is None
        assert stored.rationale_json["evidence_status"] == "hypothesis"
        assert _approval_events(proposal_id) == []

    def test_second_approve_is_refused_and_does_not_double_count(self, client, auth_headers):
        """Re-sending the same decision must not re-record it."""
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id)

        first = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )
        second = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

        assert first.status_code == 200, first.json()
        assert second.status_code == 409, second.json()
        assert second.json()["detail"]["reason"] == "proposal_not_approvable"
        assert second.json()["detail"]["current_status"] == "validated"
        assert len(_approval_events(proposal_id)) == 1
        assert _readback_proposal(proposal_id).rationale_json["evidence_status"] == (
            "validated_candidate"
        )

    def test_approve_of_other_users_proposal_is_not_found(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        other_user_id = _make_user_and_get_id("other-approve@alethra.io")
        proposal_id = _make_proposal(other_user_id)

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )

        assert resp.status_code == 404, resp.json()
        assert resp.json()["detail"]["reason"] == "proposal_not_found"
        other = _readback_proposal(proposal_id)
        assert other.status == "pending"
        assert str(other.user_id) == str(other_user_id)
        assert other.user_id != user_id
        assert _approval_events(proposal_id) == []


class TestDeployProposal:
    def test_deploys_validated_proposal(self, client, auth_headers):
        """The full ladder: scheduler-shaped row -> approve -> deploy.

        The row is no longer seeded with ``status="validated"``: that state is
        what the approve endpoint exists to produce, and fabricating it here
        would have this node certify a document the product cannot create.
        """
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id)

        approve = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/approve",
            headers=auth_headers,
        )
        assert approve.status_code == 200, approve.json()

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/deploy",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["proposal"]["status"] == "deployed"
        assert data["proposal"]["deployed_at"] is not None

    def test_deploy_refuses_a_hand_validated_proposal(self, client, auth_headers):
        """Status alone is not approval.

        This is the gate the deploy endpoint was written around: a row whose
        ``status`` says validated but whose evidence was never promoted keeps
        being refused, so the approve rung cannot be bypassed by writing the
        status column directly.
        """
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id, status="validated")

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/deploy",
            headers=auth_headers,
        )

        assert resp.status_code == 409, resp.json()
        detail = resp.json()["detail"]
        assert detail["reason"] == "proposal_not_validated"
        assert detail["evidence_status"] == "hypothesis"
        assert detail["allowed_status"] == "validated_candidate"
        assert _readback_proposal(proposal_id).status == "validated"

    def test_rejects_non_validated_proposal(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id, status="pending")

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/deploy",
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_returns_404_for_unknown_proposal(self, client, auth_headers):
        fake_id = uuid.uuid4()
        resp = client.post(
            f"/api/v1/learning/proposals/{fake_id}/deploy",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_rejects_other_users_proposal(self, client, auth_headers):
        """A user cannot deploy another user's proposal."""
        other_user_id = _make_user_and_get_id("other-deploy@alethra.io")
        proposal_id = _make_proposal(other_user_id, status="validated")

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/deploy",
            headers=auth_headers,
        )
        assert resp.status_code == 404  # Not found for this user


def _make_user_and_get_id(email: str) -> uuid.UUID:
    """Create a user and return their ID."""
    from app.auth.jwt import get_password_hash

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name="Other User",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id
    finally:
        db.close()


class TestGetPatternAnalysis:
    def test_returns_analysis_with_no_data(self, client, auth_headers):
        resp = client.get("/api/v1/learning/analysis", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "analysis" in data
        assert data["analysis"]["runs_analyzed"] == 0

    def test_returns_analysis_with_data(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        _make_run_with_trades(user_id)

        resp = client.get("/api/v1/learning/analysis", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["analysis"]["runs_analyzed"] >= 1
        assert "per_exit_config" in data["analysis"]
        assert "disclaimer" in data
