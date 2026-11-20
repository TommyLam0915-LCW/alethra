"""Tests for the learning API endpoints.

Covers:
- GET /learning/jobs returns jobs
- POST /learning/jobs/trigger creates job
- GET /learning/proposals returns proposals
- POST /learning/proposals/{id}/approve records the owner decision
- POST /learning/proposals/{id}/deploy updates status
- GET /learning/analysis returns pattern data
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.lib.timeutil import utcnow_aware
from app.models.audit_event import AuditEvent
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
    (learning_scheduler.py:360), runs the backtest and stores
    ``validation_result`` unconditionally (:383), and only then decides the
    status (:387-401). A fixture that omits either field is not describing a
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
    def test_creates_job(self, client, auth_headers):
        resp = client.post(
            "/api/v1/learning/jobs/trigger",
            json={"job_type": "scheduled_study"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["job"]["status"] == "pending"
        assert data["job"]["job_type"] == "scheduled_study"

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


class TestApproveProposal:
    """The rung between "analysis produced a proposal" and "owner deploys it".

    Nothing in the product wrote ``evidence_status="validated_candidate"``
    before this endpoint: the only occurrence of that value in ``app/`` is the
    deploy gate that demands it (api/learning.py:305), while
    learning_scheduler.py:360 stores whatever pattern analysis produced
    (hypothesis / descriptive / insufficient) and :387-388 forces
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
