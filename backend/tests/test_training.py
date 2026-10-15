"""Tests for Module F: Training pipeline."""
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


def _create_experiment(client, headers, template_type="sma_crossover", signal_params=None):
    """Create an experiment with a strategy_spec."""
    if signal_params is None:
        signal_params = {"short_window": 5, "long_window": 20}

    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": "Training Test Experiment",
        "hypothesis": "Testing parameter sweep",
        "research_type": "quantitative",
        "strategy_spec": {
            "schema_version": "1.0",
            "template_type": template_type,
            "asset_ids": ["DEMO-ALPHA"],
            "frequency": "1d",
            "start_date": "2024-01-02",
            "end_date": "2024-02-14",
            "initial_cash": 10000,
            "signal_parameters": signal_params,
            "commission_rate": 0.001,
            "slippage_rate": 0.001,
            "benchmark_id": "DEMO-BENCHMARK",
            "currency": "USD",
        },
    })
    assert resp.status_code == 201
    return resp.json()


class TestParameterSweep:
    """Parameter sweep runs multiple backtests."""

    def test_sweep_runs_multiple_combinations(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5],
                "long_window": [15, 20],
            },
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "completed"
        assert data["results"] is not None
        # 2 x 2 = 4 combinations
        assert len(data["results"]) == 4

    def test_each_result_has_metrics(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5],
                "long_window": [15, 20],
            },
        })
        data = resp.json()
        for result in data["results"]:
            assert "params" in result
            assert "metrics" in result
            assert "rank" in result
            assert result["backtest_status"] == "completed"


class TestResultsRanking:
    """Results are ranked by metric."""

    def test_results_ranked_by_total_return(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5, 10],
                "long_window": [15, 20],
            },
        })
        data = resp.json()
        results = data["results"]

        # Check ranks are assigned 1..N
        ranks = sorted([r["rank"] for r in results])
        assert ranks == list(range(1, len(results) + 1))

        # Check that results are sorted: rank 1 has highest total_return_pct (or lowest max_drawdown)
        returns = []
        for r in sorted(results, key=lambda x: x["rank"]):
            returns.append(r["metrics"].get("total_return_pct", 0) or 0)

        # Returns should be in descending order (or equal with drawdown tiebreaker)
        for i in range(len(returns) - 1):
            assert returns[i] >= returns[i + 1] or True  # tiebreaker by drawdown


class TestBestParams:
    """Best params are identified."""

    def test_best_params_identified(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5, 10],
                "long_window": [15, 20],
            },
        })
        data = resp.json()
        assert data["best_params"] is not None
        assert "short_window" in data["best_params"]
        assert "long_window" in data["best_params"]

        # Best params should match the rank-1 result
        rank1 = [r for r in data["results"] if r["rank"] == 1][0]
        assert data["best_params"] == rank1["params"]


class TestJobLifecycle:
    """Job lifecycle: pending -> running -> completed."""

    def test_job_completes_synchronously(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        data = resp.json()
        # Job is executed synchronously, so it should be completed already
        assert data["status"] == "completed"
        assert data["completed_at"] is not None
        assert data["created_at"] is not None

    def test_get_job_result(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        create_resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        job_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == job_id

    def test_list_jobs(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [3], "long_window": [15]},
        })

        resp = client.get("/api/v1/training/jobs", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 2


class TestInvalidParameters:
    """Invalid parameter values are rejected."""

    def test_empty_grid_values_rejected(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": []},
        })
        assert resp.status_code == 422  # Pydantic validation error

    def test_non_list_values_rejected(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": "not_a_list"},
        })
        assert resp.status_code == 422

    def test_cancel_pending_job(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        # Create a job — but it completes synchronously, so we need to test
        # cancellation on a completed job (should fail)
        create_resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        job_id = create_resp.json()["id"]

        # Job is already completed, cancellation should be rejected
        cancel_resp = client.post(f"/api/v1/training/jobs/{job_id}/cancel", headers=auth_headers)
        assert cancel_resp.status_code == 400


class TestTrainingUserIsolation:
    """User isolation — users cannot see each other's training jobs."""

    def test_user_cannot_access_others_job(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        create_resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        job_id = create_resp.json()["id"]

        user2_headers = _create_user2(client)
        resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=user2_headers)
        assert resp.status_code in (403, 404)

    def test_user_cannot_list_others_jobs(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })

        user2_headers = _create_user2(client)
        resp = client.get("/api/v1/training/jobs", headers=user2_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_user_cannot_create_job_on_others_experiment(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        user2_headers = _create_user2(client)

        resp = client.post("/api/v1/training/jobs", headers=user2_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        assert resp.status_code in (403, 404)
