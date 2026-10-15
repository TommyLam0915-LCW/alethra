"""Integrated acceptance tests for the Alethra product backbone.

Covers the complete usable journey as a single integration test flow,
plus targeted edge-case tests for idempotency, quota enforcement,
API key lifecycle, cross-user isolation, billing webhook lifecycle,
training job cancellation, and unknown quota types.

All tests use the ``client`` and ``auth_headers`` fixtures from conftest.py.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from uuid import uuid4

import pytest

from app.models.billing import Plan, Subscription, SubscriptionStatus
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.user import User
from app.services.billing import activate_subscription, create_subscription
from tests.conftest import TestSession


# ── Helpers ──────────────────────────────────────────────────────────────────


def _register_and_login(client, email, password="testpass123", display_name="Test"):
    """Register a new user and return Bearer auth headers."""
    client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "display_name": display_name,
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": password,
    })
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_strategy_spec(**overrides):
    """Build a valid StrategySpec payload with sensible defaults."""
    spec = {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": ["DEMO-ALPHA"],
        "frequency": "1d",
        "start_date": "2024-01-02",
        "end_date": "2024-06-28",
        "initial_cash": 100000.0,
        "signal_parameters": {},
        "entry_rule": {},
        "exit_rule": {},
        "sizing_rule": {},
        "execution_timing": {"execute_at": "next_open"},
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "benchmark_id": "DEMO-BENCHMARK",
        "currency": "USD",
    }
    spec.update(overrides)
    return spec


def _create_experiment(client, headers, **overrides):
    """Create an experiment via the API and return the response JSON."""
    payload = {
        "title": "Integration Test Experiment",
        "original_idea": "Test hypothesis",
        "research_type": "quantitative",
        "hypothesis": "Buy and hold outperforms",
        "falsification_conditions": "Max drawdown > 30%",
        "benchmark": "DEMO-BENCHMARK",
        "strategy_spec": _make_strategy_spec(**overrides.pop("spec_overrides", {})),
    }
    payload.update(overrides)
    resp = client.post("/api/v1/experiments/", headers=headers, json=payload)
    assert resp.status_code == 201, f"Experiment creation failed: {resp.text}"
    return resp.json()


def _create_portfolio(client, headers, cash=100000.0):
    resp = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


# ── 1. Complete user journey ────────────────────────────────────────────────


class TestCompleteUserJourney:
    """A single test that walks through the entire product backbone flow."""

    def test_complete_user_journey(self, client, auth_headers):
        # ── Step 1: User is already registered + logged in via auth_headers ──
        # (The fixture handles register + login.)

        # ── Step 2: Create an experiment ──
        experiment = _create_experiment(client, auth_headers)
        experiment_id = experiment["id"]
        version_id = experiment["current_version_id"]
        assert experiment_id is not None
        assert version_id is not None

        # ── Step 3: Create a training job (parameter sweep) ──
        training_resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": experiment_id,
            "parameter_grid": {
                "short_window": [5, 10],
                "long_window": [20, 50],
            },
        })
        assert training_resp.status_code == 201
        training_job = training_resp.json()
        job_id = training_job["id"]
        assert training_job["status"] == "pending"

        # Execute the job (simulating worker)
        from app.database import SessionLocal
        from app.services.training import run_parameter_sweep
        import uuid
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(job_id))
            db.commit()
        finally:
            db.close()

        # Fetch the completed job
        get_completed_job = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        training_job = get_completed_job.json()
        assert training_job["status"] == "completed"
        assert training_job["results"] is not None
        assert len(training_job["results"]) > 0
        assert training_job["best_params"] is not None

        # ── Step 4: Verify training job via GET ──
        get_job = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        assert get_job.status_code == 200
        assert get_job.json()["status"] == "completed"
        assert get_job.json()["results"] is not None

        # ── Step 5: Create a model research record + version (for inference) ──
        model_record_resp = client.post("/api/v1/model-research", headers=auth_headers, json={
            "title": "Journey Model",
            "purpose": "Inference testing",
            "allowed_asset_universe": ["DEMO-ALPHA"],
        })
        assert model_record_resp.status_code == 201
        model_record_id = model_record_resp.json()["id"]

        model_version_resp = client.post(
            f"/api/v1/model-research/{model_record_id}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {
                    "rules": [
                        {"condition": "close > sma_20", "then": "BUY"},
                        {"condition": "close < sma_20", "then": "SELL"},
                    ]
                },
            },
        )
        assert model_version_resp.status_code == 201
        model_version_id = model_version_resp.json()["id"]

        # ── Step 6: Create an inference run ──
        inference_resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": model_version_id,
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-03-29",
            },
        })
        assert inference_resp.status_code == 201
        inference_run = inference_resp.json()
        assert inference_run["status"] == "completed"
        assert inference_run["output_signals"] is not None
        assert len(inference_run["output_signals"]) > 0

        # ── Step 7: Create a paper portfolio ──
        portfolio_id = _create_portfolio(client, auth_headers)

        # ── Step 8: Create an execution intent ──
        intent_resp = client.post("/api/v1/execution/intents", headers=auth_headers, json={
            "portfolio_id": portfolio_id,
            "symbol": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
        })
        assert intent_resp.status_code == 201
        intent_data = intent_resp.json()
        intent_id = intent_data["intent_id"]
        auth_token = intent_data["authorization_token"]
        assert intent_data["status"] == "pending"

        # ── Step 9: Authorize the intent ──
        auth_resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": auth_token},
        )
        assert auth_resp.status_code == 200
        assert auth_resp.json()["status"] == "authorized"

        # ── Step 10: Submit the intent (auto-fills in offline mode) ──
        submit_resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit",
            headers=auth_headers,
        )
        assert submit_resp.status_code == 200
        assert submit_resp.json()["status"] == "filled"
        assert submit_resp.json()["broker_ref"].startswith("OFFLINE-")

        # ── Step 11: Reconcile the order ──
        reconcile_resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/reconcile",
            headers=auth_headers,
        )
        assert reconcile_resp.status_code == 201
        recon = reconcile_resp.json()
        assert recon["expected_quantity"] == 10
        assert recon["filled_quantity"] == 10
        assert recon["discrepancy"] is False

        # ── Step 12: Check usage / entitlement state ──
        # The user is on the free plan (no entitlement row yet) so the
        # entitlement endpoint should return 404 (no entitlement created).
        ent_resp = client.get("/api/v1/pilot/entitlements/me", headers=auth_headers)
        assert ent_resp.status_code == 404


# ── 2. Duplicate execution intent (idempotency key) ─────────────────────────


class TestDuplicateExecutionIntent:
    """Same idempotency_key returns the existing intent."""

    def test_duplicate_idempotency_key_returns_existing(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        idem_key = "integration-test-idem-key-001"

        payload = {
            "portfolio_id": portfolio_id,
            "symbol": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 5,
            "order_type": "MARKET",
            "idempotency_key": idem_key,
        }

        # First request creates the intent
        resp1 = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
        assert resp1.status_code == 201
        first_intent_id = resp1.json()["intent_id"]
        assert resp1.json()["idempotency_key"] == idem_key

        # Second request with the same key returns the SAME intent
        resp2 = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["intent_id"] == first_intent_id
        assert resp2.json()["idempotency_key"] == idem_key


# ── 3. Insufficient quota blocks run ────────────────────────────────────────


class TestInsufficientQuotaBlocksRun:
    """When quota is exhausted, run creation returns 429."""

    def test_insufficient_quota_blocks_run(self, client, auth_headers):
        # Get the user ID from the DB so we can set a restrictive entitlement
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            assert user is not None
            user_id = user.id

            # Create an entitlement with research_run_limit = 0
            ent = PilotEntitlement(
                user_id=user_id,
                pilot_access=True,
                research_run_limit=0,
                ai_usage_limit=20,
                export_limit=10,
                forward_simulation_limit=10,
            )
            db.add(ent)
            db.commit()
        finally:
            db.close()

        # Create an experiment (does not consume quota)
        experiment = _create_experiment(client, auth_headers)
        version_id = experiment["current_version_id"]

        # Attempt to create a run — should be blocked with 429
        run_resp = client.post(
            f"/api/v1/versions/{version_id}/runs",
            headers=auth_headers,
        )
        assert run_resp.status_code == 429
        detail = run_resp.json()["detail"]
        assert detail["error"] == "quota_exceeded"
        assert detail["quota_type"] == "research_run"


# ── 4. Revoked API key rejected ─────────────────────────────────────────────


class TestRevokedApiKeyRejected:
    """API key authentication is enforced at HTTP endpoints."""

    def test_valid_api_key_accesses_protected_endpoint(self, client, auth_headers):
        key_resp = client.post("/api/v1/api-keys", headers=auth_headers, json={
            "name": "http-test-key",
            "scopes": ["read", "write"],
        })
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["raw_key"]

        key_headers = {"Authorization": f"Bearer {raw_key}"}
        resp = client.get("/api/v1/experiments/", headers=key_headers)
        assert resp.status_code == 200

    def test_revoked_api_key_rejected_at_http_endpoint(self, client, auth_headers):
        key_resp = client.post("/api/v1/api-keys", headers=auth_headers, json={
            "name": "revoke-http-test",
            "scopes": ["read", "write"],
        })
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["raw_key"]
        key_id = key_resp.json()["id"]

        key_headers = {"Authorization": f"Bearer {raw_key}"}
        assert client.get("/api/v1/experiments/", headers=key_headers).status_code == 200

        client.delete(f"/api/v1/api-keys/{key_id}", headers=auth_headers)

        resp = client.get("/api/v1/experiments/", headers=key_headers)
        assert resp.status_code == 401

    def test_invalid_api_key_rejected(self, client):
        resp = client.get(
            "/api/v1/experiments/",
            headers={"Authorization": "Bearer alk_invalid_key_that_does_not_exist"},
        )
        assert resp.status_code == 401

    def test_insufficient_scope_rejected(self, client, auth_headers):
        # Create a read-only API key
        key_resp = client.post("/api/v1/api-keys", headers=auth_headers, json={
            "name": "read-only-key",
            "scopes": ["read"],
        })
        assert key_resp.status_code == 201
        raw_key = key_resp.json()["raw_key"]

        # Create an experiment (which creates a version) using JWT
        exp_data = _create_experiment(client, auth_headers)
        exp_id = exp_data["id"]

        # Get the version ID
        versions_resp = client.get(f"/api/v1/experiments/{exp_id}/versions/", headers=auth_headers)
        assert versions_resp.status_code == 200
        version_id = versions_resp.json()[0]["id"]

        # Try to create a run with the read-only API key (should fail with 403)
        key_headers = {"Authorization": f"Bearer {raw_key}"}
        resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=key_headers, json={})
        assert resp.status_code == 403
        detail = resp.json()["detail"]
        assert detail["error"] == "insufficient_scope"
        assert detail["required_scope"] == "write"
        assert "read" in detail["held_scopes"]
        assert "write" not in detail["held_scopes"]

    def test_jwt_still_works_after_api_key_change(self, client, auth_headers):
        resp = client.get("/api/v1/experiments/", headers=auth_headers)
        assert resp.status_code == 200


# ── 5. Cross-user access denied ─────────────────────────────────────────────


class TestCrossUserAccessDenied:
    """User B cannot access User A's intents or experiments."""

    def _create_second_user(self, client):
        return _register_and_login(
            client,
            email="user_b_cross@alethra.io",
            password="testpass123",
            display_name="User B",
        )

    def test_cross_user_cannot_access_experiment(self, client, auth_headers):
        # User A creates an experiment
        experiment = _create_experiment(client, auth_headers)
        experiment_id = experiment["id"]

        # User B tries to read it
        headers_b = self._create_second_user(client)
        resp = client.get(f"/api/v1/experiments/{experiment_id}", headers=headers_b)
        assert resp.status_code == 404  # ownership filter hides it

    def test_cross_user_cannot_access_intent(self, client, auth_headers):
        # User A creates portfolio + intent
        portfolio_id = _create_portfolio(client, auth_headers)
        intent_resp = client.post("/api/v1/execution/intents", headers=auth_headers, json={
            "portfolio_id": portfolio_id,
            "symbol": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 5,
            "order_type": "MARKET",
        })
        assert intent_resp.status_code == 201
        intent_id = intent_resp.json()["intent_id"]

        # User B tries to read it
        headers_b = self._create_second_user(client)
        resp = client.get(f"/api/v1/execution/intents/{intent_id}", headers=headers_b)
        assert resp.status_code == 403

    def test_cross_user_cannot_authorize_intent(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        intent_resp = client.post("/api/v1/execution/intents", headers=auth_headers, json={
            "portfolio_id": portfolio_id,
            "symbol": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 5,
            "order_type": "MARKET",
        })
        intent_data = intent_resp.json()
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        headers_b = self._create_second_user(client)
        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=headers_b,
            json={"authorization_token": token},
        )
        assert resp.status_code == 403


# ── 6. Payment webhook lifecycle ────────────────────────────────────────────


class TestPaymentWebhookLifecycle:
    """Create plan -> create subscription -> activate via service -> verify entitlement."""

    def test_payment_webhook_lifecycle(self, client, auth_headers):
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            assert user is not None
            user_id = user.id

            # Create a Plan directly in the DB
            plan = Plan(
                name=f"Acceptance Test Plan {uuid4().hex[:8]}",
                monthly_price=999,
                currency="USD",
                is_active=True,
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)
            plan_id = plan.id
        finally:
            db.close()

        # Verify plan is listed via API
        plans_resp = client.get("/api/v1/plans")
        assert plans_resp.status_code == 200
        plan_ids = [p["id"] for p in plans_resp.json()["plans"]]
        assert str(plan_id) in plan_ids

        # Create a subscription
        sub_resp = client.post(
            f"/api/v1/subscriptions?plan_id={plan_id}",
            headers=auth_headers,
        )
        assert sub_resp.status_code == 200
        sub_data = sub_resp.json()
        sub_id = sub_data["id"]
        assert sub_data["status"] == "trialing"
        assert sub_data["test_mode"] is True

        # Verify subscription via GET
        my_sub = client.get("/api/v1/subscriptions/me", headers=auth_headers)
        assert my_sub.status_code == 200
        assert my_sub.json()["subscription"]["id"] == sub_id

        # Send a payment webhook with valid signature
        webhook_secret = "test-webhook-secret"
        payload = json.dumps({
            "id": f"evt_{uuid4().hex[:12]}",
            "type": "payment.completed",
            "data": {"subscription_id": sub_id, "amount": 999},
        })
        sig = hmac.new(webhook_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        wh_resp = client.post(
            "/api/v1/webhooks/payment",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sig,
            },
        )
        assert wh_resp.status_code == 200
        assert wh_resp.json()["received"] is True

        # Activate the subscription via service (simulating what webhook processing triggers)
        db = TestSession()
        try:
            activated = activate_subscription(db, uuid.UUID(sub_id), "pi_test_acceptance")
            assert activated.status == SubscriptionStatus.active

            # Verify entitlement was created
            ent = db.query(PilotEntitlement).filter(
                PilotEntitlement.user_id == user_id,
            ).first()
            assert ent is not None
            assert ent.pilot_access is True
            # monthly_price=999 < 5000 → "starter" tier
            assert ent.research_run_limit == 50
            assert ent.ai_usage_limit == 20
        finally:
            db.close()

        # Verify entitlement is now accessible via API
        ent_resp = client.get("/api/v1/pilot/entitlements/me", headers=auth_headers)
        assert ent_resp.status_code == 200
        assert ent_resp.json()["pilot_access"] is True


# ── 7. Cancelled training job ───────────────────────────────────────────────


class TestCancelledTrainingJob:
    """Create a job in pending state, cancel it, verify status."""

    def test_cancelled_training_job(self, client, auth_headers):
        # Create an experiment first
        experiment = _create_experiment(client, auth_headers)
        experiment_id = experiment["id"]

        # Insert a pending training job directly into the DB
        # (We cannot cancel through the API after synchronous completion.)
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            job = TrainingJob(
                user_id=user.id,
                experiment_id=uuid.UUID(experiment_id),
                parameter_grid={"window": [10, 20]},
                strategy_spec_snapshot=_make_strategy_spec(),
                status=TrainingJobStatus.pending,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id
        finally:
            db.close()

        # Cancel via API
        cancel_resp = client.post(
            f"/api/v1/training/jobs/{job_id}/cancel",
            headers=auth_headers,
        )
        assert cancel_resp.status_code == 200
        cancelled = cancel_resp.json()
        assert cancelled["status"] == "cancelled"
        assert cancelled["cancelled_at"] is not None

        # Verify via GET
        get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "cancelled"


# ── 8. Unknown quota type rejected ──────────────────────────────────────────


class TestUnknownQuotaTypeRejected:
    """Unknown quota_type raises ValueError in the entitlement layer."""

    def test_unknown_quota_type_raises_value_error(self):
        from app.auth.entitlements import _get_limit, check_quota_available, consume_quota

        # _get_limit with unknown type
        with pytest.raises(ValueError, match="Unknown quota_type"):
            _get_limit(None, "nonexistent_quota")

        # check_quota_available with unknown type
        db = TestSession()
        try:
            fake_user_id = uuid4()
            with pytest.raises(ValueError, match="Unknown quota_type"):
                check_quota_available(db, fake_user_id, "bogus_type")

            with pytest.raises(ValueError, match="Unknown quota_type"):
                consume_quota(db, fake_user_id, "bogus_type")
        finally:
            db.close()
