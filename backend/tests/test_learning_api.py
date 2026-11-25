"""Tests for the learning API endpoints.

Covers:
- GET /learning/jobs returns jobs
- POST /learning/jobs/trigger creates job
- GET /learning/proposals returns proposals
- POST /learning/proposals/{id}/deploy updates status
- GET /learning/analysis returns pattern data
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from tests.conftest import TestSession


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
    """Extract user_id from auth headers by decoding the JWT."""
    # Same library, key and algorithm the issuer uses (app/auth/jwt.py), so the
    # signature is verified rather than skipped — and PyJWT, which is neither in
    # requirements.txt nor installed here, is not needed.
    from jose import jwt

    from app.config import get_settings

    settings = get_settings()
    token = headers["Authorization"].split(" ")[1]
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
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


def _make_proposal(user_id: uuid.UUID, *, status: str = "validated") -> uuid.UUID:
    """Create a learning job and proposal for testing."""
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
                # The deploy route allow-lists evidence_status=='validated_candidate'
                # (api/learning.py:277); only a validated proposal carries that stamp.
                **(
                    {"evidence_status": "validated_candidate"}
                    if status == "validated"
                    else {}
                ),
            },
            validation_result={
                "passed": True,
                "sharpe_before": 0.1,
                "sharpe_after": 0.4,
                "max_dd_before": 20.0,
                "max_dd_after": 15.0,
            }
            if status == "validated"
            else None,
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


class TestTriggerLearningJob:
    """The trigger's two outcomes, both of which end in the ledger.

    The endpoint used to answer 201 with a ``pending`` row and the message
    "It will run on the next scheduler tick or immediately if the scheduler is
    active". Neither half was true: a scheduled tick writes its own records and
    reads no pending ones, and the dispatch (when it happened) called
    ``run_scheduled_research(scope="manual")`` with no ``user_id`` — so it
    studied for whichever user the old ``select(User).limit(1)`` returned and
    wrote a *second* row. A request in an account with no scheduler left a row
    nothing would ever advance.
    """

    def test_refuses_and_records_when_no_learning_service_runs(
        self, client, auth_headers
    ):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "scheduled_study"},
            headers=auth_headers,
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "learning_service_not_running"
        job_id = uuid.UUID(detail["job"]["id"])

        db = TestSession()
        try:
            row = db.query(LearningJob).filter(LearningJob.id == job_id).first()
            assert row.status == "failed", row.status
            assert row.started_at is None, "a refused request must not look started"
            assert row.completed_at is not None
            assert "ENABLE_LEARNING_SCHEDULER" in row.error_details
            assert row.result_json is None
        finally:
            db.close()

    def test_dispatch_names_the_requesting_account_and_its_own_row(
        self, client, auth_headers
    ):
        user_id = _get_user_id(auth_headers)

        with patch("app.main._learning_scheduler", SimpleNamespace(running=True)), \
             patch("app.workers.learning_scheduler.run_scheduled_research") as research, \
             patch("app.workers.learning_scheduler.run_pattern_analysis_job") as analysis, \
             patch("threading.Thread") as thread:
            resp = client.post(
                "/api/v1/learning/jobs/trigger",
                json={
                    "job_type": "scheduled_study",
                    "instruments": ["CRYPTO:KRAKEN:BTC-USD"],
                    "intervals": [1440],
                },
                headers=auth_headers,
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["job"]["status"] == "pending"
        # What the caller asked for is what the record says was asked for.
        assert body["job"]["config_json"]["instruments"] == ["CRYPTO:KRAKEN:BTC-USD"]
        assert body["job"]["config_json"]["intervals"] == [1440]

        kwargs = thread.call_args.kwargs
        assert kwargs["target"] is research
        assert kwargs["kwargs"] == {
            "scope": "manual",
            "user_id": user_id,
            "job_id": uuid.UUID(body["job"]["id"]),
            "instruments": ["CRYPTO:KRAKEN:BTC-USD"],
            "intervals": [1440],
        }
        thread.return_value.start.assert_called_once()
        # Nothing runs inside the request itself: the row stays the caller's to
        # follow, and the worker owns every later write.
        assert research.call_count == 0
        assert analysis.call_count == 0

    def test_the_analysis_trigger_dispatches_the_analysis_tick(
        self, client, auth_headers
    ):
        user_id = _get_user_id(auth_headers)

        with patch("app.main._learning_scheduler", SimpleNamespace(running=True)), \
             patch("app.workers.learning_scheduler.run_scheduled_research") as research, \
             patch("app.workers.learning_scheduler.run_pattern_analysis_job") as analysis, \
             patch("threading.Thread") as thread:
            resp = client.post(
                "/api/v1/learning/jobs/trigger",
                json={"job_type": "pattern_analysis"},
                headers=auth_headers,
            )

        assert resp.status_code == 201, resp.text
        kwargs = thread.call_args.kwargs
        assert kwargs["target"] is analysis
        assert kwargs["kwargs"] == {
            "scope": "manual",
            "user_id": user_id,
            "job_id": uuid.UUID(resp.json()["job"]["id"]),
        }
        assert research.call_count == 0

    def test_the_replay_trigger_dispatches_the_replay_tick(self, client, auth_headers):
        """The third job type steps the caller's own sessions, not a named grid.

        ``instruments``/``intervals`` are deliberately absent from the dispatch:
        a replay reads the dataset bound to each of its own sessions, so a
        request that named another instrument would have the record claim a
        parameter the tick cannot honour.
        """
        user_id = _get_user_id(auth_headers)

        with patch("app.main._learning_scheduler", SimpleNamespace(running=True)), \
             patch("app.workers.learning_scheduler.run_scheduled_research") as research, \
             patch("app.workers.learning_scheduler.run_pattern_analysis_job") as analysis, \
             patch("app.workers.learning_scheduler.run_scheduled_simulations") as replay, \
             patch("threading.Thread") as thread:
            resp = client.post(
                "/api/v1/learning/jobs/trigger",
                json={
                    "job_type": "simulation_daily",
                    "instruments": ["CRYPTO:KRAKEN:BTC-USD"],
                },
                headers=auth_headers,
            )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        kwargs = thread.call_args.kwargs
        assert kwargs["target"] is replay
        assert kwargs["kwargs"] == {
            "scope": "manual",
            "user_id": user_id,
            "job_id": uuid.UUID(body["job"]["id"]),
        }
        assert "instruments" not in body["job"]["config_json"]
        assert "intervals" not in body["job"]["config_json"]
        assert research.call_count == 0
        assert analysis.call_count == 0

    def test_a_replay_request_is_refused_the_same_way_when_nothing_will_run(
        self, client, auth_headers
    ):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "simulation_daily"},
            headers=auth_headers,
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "learning_service_not_running"
        assert detail["job"]["job_type"] == "simulation_daily"
        assert detail["job"]["status"] == "failed"

    def test_rejects_invalid_job_type(self, client, auth_headers):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "invalid_type"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


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


class TestDeployProposal:
    def test_deploys_validated_proposal(self, client, auth_headers):
        user_id = _get_user_id(auth_headers)
        proposal_id = _make_proposal(user_id, status="validated")

        resp = client.post(
            f"/api/v1/learning/proposals/{proposal_id}/deploy",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["proposal"]["status"] == "deployed"
        assert data["proposal"]["deployed_at"] is not None

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
