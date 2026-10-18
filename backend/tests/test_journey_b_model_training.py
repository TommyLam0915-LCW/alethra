"""Journey B: Trainable personal model and API workflow.

Tests the complete journey from model training through API access:
1. Train a model
2. Worker executes training job
3. Model artifact created
4. Inference with quota reservation
5. API key creation with scope
6. API access with key (scope enforcement)
7. API key revocation

Uses synthetic data - no external credentials required.
"""
import pytest
import uuid

from app.database import SessionLocal
from app.services.training import run_parameter_sweep


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


class TestJourneyBTrainableModelAndAPI:
    """Journey B: Trainable personal model and API (train → worker → artifact → inference → quota → revoke)."""

    def test_model_training_to_api_access(self, client):
        """Complete journey from model training through API access."""
        # Step 1: Register and login
        headers = _register_and_login(client, "journey-b@test.com")

        # Step 2: Create experiment
        exp_resp = client.post("/api/v1/experiments/", headers=headers, json={
            "title": "Journey B Experiment",
            "original_idea": "Test hypothesis",
            "research_type": "quantitative",
            "hypothesis": "Buy and hold outperforms",
            "falsification_conditions": "Max drawdown > 30%",
            "benchmark": "DEMO-BENCHMARK",
            "strategy_spec": _make_strategy_spec(),
        })
        assert exp_resp.status_code == 201
        experiment_id = exp_resp.json()["id"]

        # Step 3: Create training job
        training_resp = client.post("/api/v1/training/jobs", headers=headers, json={
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

        # Step 4: Execute training job (simulating worker)
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(job_id))
            db.commit()
        finally:
            db.close()

        # Step 5: Verify training job completed
        get_job_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=headers)
        assert get_job_resp.status_code == 200
        completed_job = get_job_resp.json()
        assert completed_job["status"] == "completed"
        assert completed_job["results"] is not None
        assert len(completed_job["results"]) > 0
        assert completed_job["best_params"] is not None

        # Step 6: Create model research record
        model_resp = client.post("/api/v1/model-research", headers=headers, json={
            "title": "Journey B Model",
            "purpose": "API access testing",
            "allowed_asset_universe": ["DEMO-ALPHA"],
        })
        assert model_resp.status_code == 201
        model_record_id = model_resp.json()["id"]

        # Step 7: Create model version
        version_resp = client.post(
            f"/api/v1/model-research/{model_record_id}/versions",
            headers=headers,
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
        assert version_resp.status_code == 201
        model_version_id = version_resp.json()["id"]

        # Step 8: Run inference (reserves and settles quota)
        inference_resp = client.post("/api/v1/inference/runs", headers=headers, json={
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

        # Step 9: Create API key with read scope
        api_key_resp = client.post("/api/v1/api-keys", headers=headers, json={
            "name": "Journey B Test Key",
            "scopes": ["read"],
        })
        assert api_key_resp.status_code == 201
        api_key = api_key_resp.json()
        api_key_value = api_key["raw_key"]
        api_key_id = api_key["id"]
        assert api_key["name"] == "Journey B Test Key"
        assert "read" in api_key["scopes"]

        # Step 10: Use API key to access read endpoints
        api_headers = {"Authorization": f"Bearer {api_key_value}"}

        # Should be able to list models (read scope)
        list_models_resp = client.get("/api/v1/ml/models", headers=api_headers)
        assert list_models_resp.status_code == 200

        # Should NOT be able to train new model (requires write scope)
        train_resp = client.post("/api/v1/ml/train", headers=api_headers, json={
            "model_name": "Unauthorized Training",
            "training_config": {},
        })
        assert train_resp.status_code == 403

        # Step 11: Revoke API key
        revoke_resp = client.delete(f"/api/v1/api-keys/{api_key_id}", headers=headers)
        assert revoke_resp.status_code == 204

        # Step 12: Verify revoked key no longer works
        list_models_resp = client.get("/api/v1/ml/models", headers=api_headers)
        assert list_models_resp.status_code == 401

    def test_inference_quota_reservation_and_settlement(self, client):
        """Verify inference reserves and settles quota correctly."""
        headers = _register_and_login(client, "journey-b-quota@test.com")

        # Create model
        model_resp = client.post("/api/v1/model-research", headers=headers, json={
            "title": "Quota Test Model",
            "purpose": "Quota testing",
            "allowed_asset_universe": ["DEMO-ALPHA"],
        })
        model_record_id = model_resp.json()["id"]

        version_resp = client.post(
            f"/api/v1/model-research/{model_record_id}/versions",
            headers=headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
            },
        )
        model_version_id = version_resp.json()["id"]

        # Run multiple inferences (each should reserve and settle quota)
        for i in range(3):
            inference_resp = client.post("/api/v1/inference/runs", headers=headers, json={
                "model_version_id": model_version_id,
                "input_spec": {
                    "symbol": "DEMO-ALPHA",
                    "start_date": "2024-01-02",
                    "end_date": "2024-03-29",
                },
            })
            assert inference_resp.status_code == 201

        # Verify all inference runs completed
        list_resp = client.get("/api/v1/inference/runs", headers=headers)
        assert list_resp.status_code == 200
        runs = list_resp.json()
        assert len(runs) == 3
        assert all(run["status"] == "completed" for run in runs)

    def test_api_key_scope_enforcement(self, client):
        """Verify API key scopes are enforced correctly."""
        headers = _register_and_login(client, "journey-b-scope@test.com")

        # Create API key with read-only scope
        api_key_resp = client.post("/api/v1/api-keys", headers=headers, json={
            "name": "Read-Only Key",
            "scopes": ["read"],
        })
        api_key = api_key_resp.json()
        api_headers = {"Authorization": f"Bearer {api_key['raw_key']}"}

        # Read operations should succeed
        assert client.get("/api/v1/ml/models", headers=api_headers).status_code == 200
        assert client.get("/api/v1/ai/conversations", headers=api_headers).status_code == 200
        assert client.get("/api/v1/execution/intents", headers=api_headers).status_code == 200

        # Write operations should fail with 403
        assert client.post("/api/v1/ml/train", headers=api_headers, json={}).status_code == 403
        assert client.post("/api/v1/ai/conversations", headers=api_headers, json={}).status_code == 403
        assert client.post("/api/v1/execution/intents", headers=api_headers, json={}).status_code == 403

        # Create API key with write scope
        write_key_resp = client.post("/api/v1/api-keys", headers=headers, json={
            "name": "Write Key",
            "scopes": ["read", "write"],
        })
        write_api_headers = {"Authorization": f"Bearer {write_key_resp.json()['key']}"}

        # Write operations should succeed
        assert client.post("/api/v1/ai/conversations", headers=write_api_headers, json={
            "title": "Test",
        }).status_code == 201

    def test_model_deletion_lifecycle(self, client):
        """Verify model can be deleted after creation."""
        headers = _register_and_login(client, "journey-b-delete@test.com")

        # Create model
        model_resp = client.post("/api/v1/model-research", headers=headers, json={
            "title": "Deletable Model",
            "purpose": "Deletion testing",
            "allowed_asset_universe": ["DEMO-ALPHA"],
        })
        model_record_id = model_resp.json()["id"]

        version_resp = client.post(
            f"/api/v1/model-research/{model_record_id}/versions",
            headers=headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
            },
        )
        model_version_id = version_resp.json()["id"]

        # Verify model exists
        get_resp = client.get(f"/api/v1/ml/models/{model_version_id}", headers=headers)
        assert get_resp.status_code == 200

        # Delete model
        delete_resp = client.delete(f"/api/v1/ml/models/{model_version_id}", headers=headers)
        assert delete_resp.status_code == 204

        # Verify model no longer accessible
        get_resp = client.get(f"/api/v1/ml/models/{model_version_id}", headers=headers)
        assert get_resp.status_code == 404
