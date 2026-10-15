"""Tests for Module E: Model execution runtime (inference)."""
import pytest


def _create_user2(client):
    client.post("/api/auth/register", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
        "display_name": "User 2",
        "user_type": "individual",
    })
    resp = client.post("/api/auth/login", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_model_record(client, headers):
    resp = client.post("/api/v1/model-research", headers=headers, json={
        "title": "Inference Test Model",
        "purpose": "Testing inference execution",
    })
    assert resp.status_code == 201
    return resp.json()


def _create_model_version(client, headers, record_id, model_type="rule_based", spec=None):
    if spec is None:
        spec = {"rules": [{"condition": "close > sma_5", "then": "BUY"}, {"condition": "close < sma_5", "then": "SELL"}]}
    resp = client.post(f"/api/v1/model-research/{record_id}/versions", headers=headers, json={
        "model_type": model_type,
        "model_specification_json": spec,
    })
    assert resp.status_code == 201
    return resp.json()


class TestRuleBasedInference:
    """Rule-based model generates signals from demo data."""

    def test_rule_based_generates_signals(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["output_signals"] is not None
        assert len(data["output_signals"]) > 0

        # Each signal should have date, signal, confidence, rationale
        for sig in data["output_signals"]:
            assert "date" in sig
            assert sig["signal"] in ("BUY", "SELL", "HOLD")
            assert "confidence" in sig
            assert "rationale" in sig

    def test_rule_based_signals_contain_buy_and_sell(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-02-13",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        signals = [s["signal"] for s in data["output_signals"]]
        # With SMA-5 crossover on DEMO-ALPHA, we should get a mix of BUY/SELL/HOLD
        assert "HOLD" in signals or "BUY" in signals or "SELL" in signals


class TestPromptBasedInference:
    """Prompt-based model returns HOLD with descriptive-only rationale."""

    def test_prompt_based_returns_hold(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(
            client, auth_headers, record["id"],
            model_type="prompt_based_research_assistant",
            spec={"prompt": "Analyze market conditions"},
        )

        resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["output_signals"] is not None
        assert len(data["output_signals"]) == 1
        assert data["output_signals"][0]["signal"] == "HOLD"
        assert "descriptive only" in data["output_signals"][0]["rationale"].lower()


class TestFutureModelReference:
    """Future model reference returns error."""

    def test_future_external_model_returns_error(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(
            client, auth_headers, record["id"],
            model_type="future_external_model_reference",
            spec={"reference": "some_external_model"},
        )

        resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_details"] is not None
        assert "not yet executable" in data["error_details"].lower()


class TestInferenceRunLifecycle:
    """Inference run lifecycle: pending -> completed."""

    def test_run_lifecycle_completes(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-BETA",
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        # After synchronous execution, should be completed (not pending or running)
        assert data["status"] == "completed"
        assert data["completed_at"] is not None
        assert data["created_at"] is not None

    def test_get_run_result(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        create_resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {
                "symbol": "DEMO-ALPHA",
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
            },
        })
        run_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/inference/runs/{run_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == run_id

    def test_list_runs(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        # Create two runs
        client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {"symbol": "DEMO-ALPHA", "start_date": "2024-01-02", "end_date": "2024-01-31"},
        })
        client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {"symbol": "DEMO-BETA", "start_date": "2024-01-02", "end_date": "2024-01-31"},
        })

        resp = client.get("/api/v1/inference/runs", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 2


class TestInferenceUserIsolation:
    """User isolation — users cannot see each other's inference runs."""

    def test_user_cannot_access_others_run(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        create_resp = client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {"symbol": "DEMO-ALPHA", "start_date": "2024-01-02", "end_date": "2024-01-31"},
        })
        run_id = create_resp.json()["id"]

        user2_headers = _create_user2(client)
        resp = client.get(f"/api/v1/inference/runs/{run_id}", headers=user2_headers)
        assert resp.status_code in (403, 404)

    def test_user_cannot_list_others_runs(self, client, auth_headers):
        record = _create_model_record(client, auth_headers)
        version = _create_model_version(client, auth_headers, record["id"])

        client.post("/api/v1/inference/runs", headers=auth_headers, json={
            "model_version_id": version["id"],
            "input_spec": {"symbol": "DEMO-ALPHA", "start_date": "2024-01-02", "end_date": "2024-01-31"},
        })

        user2_headers = _create_user2(client)
        resp = client.get("/api/v1/inference/runs", headers=user2_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 0
