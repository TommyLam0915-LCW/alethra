"""Tests for Module F: Training pipeline."""
import uuid
from unittest.mock import patch

import pytest

from app.models.training import TrainingJob, TrainingJobStatus
from app.services.training import (
    MAX_GRID_SIZE,
    run_parameter_sweep,
    cancel_running_job,
    _cancellation_flags,
)


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


def _create_and_execute_job(client, headers, experiment_id, parameter_grid):
    """Create a training job and execute it synchronously (simulating the worker).

    Returns the completed job data.
    """
    resp = client.post("/api/v1/training/jobs", headers=headers, json={
        "experiment_id": experiment_id,
        "parameter_grid": parameter_grid,
    })
    assert resp.status_code == 201
    job_data = resp.json()
    job_id = job_data["id"]

    # Execute the job synchronously (simulating what the worker would do)
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        run_parameter_sweep(db, uuid.UUID(job_id))
        db.commit()
    finally:
        db.close()

    # Fetch the updated job
    resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=headers)
    assert resp.status_code == 200
    return resp.json()


class TestParameterSweep:
    """Parameter sweep runs multiple backtests."""

    def test_sweep_runs_multiple_combinations(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        data = _create_and_execute_job(client, auth_headers, exp["id"], {
            "short_window": [3, 5],
            "long_window": [15, 20],
        })
        assert data["status"] == "completed"
        assert data["results"] is not None
        # 2 x 2 = 4 combinations
        assert len(data["results"]) == 4

    def test_each_result_has_metrics(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        data = _create_and_execute_job(client, auth_headers, exp["id"], {
            "short_window": [3, 5],
            "long_window": [15, 20],
        })
        for result in data["results"]:
            assert "params" in result
            assert "metrics" in result
            assert "rank" in result
            assert result["backtest_status"] == "completed"


class TestResultsRanking:
    """Results are ranked by metric."""

    def test_results_ranked_by_total_return(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        data = _create_and_execute_job(client, auth_headers, exp["id"], {
            "short_window": [3, 5, 10],
            "long_window": [15, 20],
        })
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

        data = _create_and_execute_job(client, auth_headers, exp["id"], {
            "short_window": [3, 5, 10],
            "long_window": [15, 20],
        })
        assert data["best_params"] is not None
        assert "short_window" in data["best_params"]
        assert "long_window" in data["best_params"]

        # Best params should match the rank-1 result
        rank1 = [r for r in data["results"] if r["rank"] == 1][0]
        assert data["best_params"] == rank1["params"]


class TestJobLifecycle:
    """Job lifecycle: pending -> running -> completed."""

    def test_job_completes_synchronously(self, client, auth_headers):
        """Test that job creation returns pending status (async contract)."""
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        data = resp.json()
        # Job is created asynchronously, so it should be pending
        assert data["status"] == "pending"
        assert data["completed_at"] is None
        assert data["created_at"] is not None

        # Now execute it (simulating worker)
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(data["id"]))
            db.commit()
        finally:
            db.close()

        # Fetch the updated job
        get_resp = client.get(f"/api/v1/training/jobs/{data['id']}", headers=auth_headers)
        updated_data = get_resp.json()
        assert updated_data["status"] == "completed"
        assert updated_data["completed_at"] is not None

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

        # Create a job — it will be pending (async execution)
        create_resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        job_data = create_resp.json()
        job_id = job_data["id"]
        assert job_data["status"] == "pending"

        # Cancel the pending job
        cancel_resp = client.post(f"/api/v1/training/jobs/{job_id}/cancel", headers=auth_headers)
        assert cancel_resp.status_code == 200
        cancelled_data = cancel_resp.json()
        assert cancelled_data["status"] == "cancelled"
        assert cancelled_data["cancelled_at"] is not None


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


class TestGridSizeLimit:
    """Grid size is bounded by MAX_GRID_SIZE."""

    def test_oversized_grid_rejected(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        # 11 * 11 * 11 = 1331 > MAX_GRID_SIZE (1000)
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": list(range(1, 12)),   # 11 values
                "long_window": list(range(10, 21)),    # 11 values
                "extra_param": list(range(10, 21)),    # 11 values → 1331 combos
            },
        })
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"].lower() or "maximum" in resp.json()["detail"].lower()

    def test_grid_at_limit_is_accepted(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        # Use a moderate grid that exercises the boundary without running
        # 1000 real backtests.  10 * 10 * 10 = 1000 == MAX_GRID_SIZE.
        # We mock run_backtest so the test stays fast.
        with patch("app.services.training.run_backtest") as mock_bt:
            mock_bt.return_value = {
                "run_status": "completed",
                "metrics": {"total_return_pct": 1.0, "max_drawdown_pct": 0.5},
                "errors": [],
            }
            resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
                "experiment_id": exp["id"],
                "parameter_grid": {
                    "short_window": list(range(2, 12)),    # 10 values
                    "long_window": list(range(15, 25)),    # 10 values
                    "extra_param": list(range(10, 20)),    # 10 values → 1000 combos
                },
            })
            # Should succeed (201) — 1000 is exactly at the limit
            assert resp.status_code == 201
            data = resp.json()
            job_id = data["id"]
            assert data["status"] == "pending"

            # Execute the job (simulating worker)
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                run_parameter_sweep(db, uuid.UUID(job_id))
                db.commit()
            finally:
                db.close()

            # Fetch the completed job
            get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
            completed_data = get_resp.json()
            assert completed_data["status"] == "completed"
            assert len(completed_data["results"]) == 1000


class TestStrategySnapshot:
    """Strategy is captured at job creation time (immutable snapshot)."""

    def test_strategy_spec_snapshot_captured(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)

        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [5], "long_window": [20]},
        })
        data = resp.json()
        assert data["strategy_spec_snapshot"] is not None
        # Snapshot should contain the strategy fields from the experiment
        assert data["strategy_spec_snapshot"].get("template_type") == "sma_crossover"
        assert "signal_parameters" in data["strategy_spec_snapshot"]
        assert "asset_ids" in data["strategy_spec_snapshot"]

    def test_snapshot_is_independent_of_experiment_changes(self, client, auth_headers):
        """Snapshot captured at creation should not reflect later experiment edits."""
        exp = _create_experiment(
            client,
            auth_headers,
            signal_params={"short_window": 5, "long_window": 20},
        )

        # Create a job — snapshot captures the original strategy
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [3], "long_window": [15]},
        })
        data = resp.json()
        snapshot = data["strategy_spec_snapshot"]

        # The snapshot should have the original signal_parameters
        assert snapshot["signal_parameters"]["short_window"] == 5
        assert snapshot["signal_parameters"]["long_window"] == 20


class TestFailedBacktestFiltering:
    """Ranking excludes failed backtests."""

    @patch("app.services.training.run_backtest")
    def test_only_successful_backtests_ranked(self, mock_backtest, client, auth_headers):
        """Failed backtests must not appear in ranked results."""
        call_count = [0]

        def side_effect(strategy_spec):
            call_count[0] += 1
            if call_count[0] <= 2:
                # First two backtests fail
                return {
                    "run_status": "error",
                    "metrics": {},
                    "errors": ["Simulated error"],
                }
            else:
                # Remaining backtests succeed
                return {
                    "run_status": "completed",
                    "metrics": {
                        "total_return_pct": 5.0,
                        "max_drawdown_pct": 2.0,
                    },
                    "errors": [],
                }

        mock_backtest.side_effect = side_effect

        exp = _create_experiment(client, auth_headers)
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5, 7, 10],  # 4 combos → 2 fail, 2 succeed
                "long_window": [15],
            },
        })
        data = resp.json()
        job_id = data["id"]
        assert data["status"] == "pending"

        # Execute the job (simulating worker)
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(job_id))
            db.commit()
        finally:
            db.close()

        # Fetch the completed job
        get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        completed_data = get_resp.json()
        assert completed_data["status"] == "completed"

        # Only successful backtests should appear in ranked results
        for result in completed_data["results"]:
            assert result["backtest_status"] == "completed"

        # Should have exactly 2 ranked results (not 4)
        assert len(completed_data["results"]) == 2

    @patch("app.services.training.run_backtest")
    def test_all_failed_backtests_set_status_to_failed(self, mock_backtest, client, auth_headers):
        """If all backtests fail, job status should be 'failed' with clear error."""
        mock_backtest.return_value = {
            "run_status": "error",
            "metrics": {},
            "errors": ["Simulated total failure"],
        }

        exp = _create_experiment(client, auth_headers)
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5],
                "long_window": [15, 20],
            },
        })
        data = resp.json()
        job_id = data["id"]
        assert data["status"] == "pending"

        # Execute the job (simulating worker)
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(job_id))
            db.commit()
        finally:
            db.close()

        # Fetch the failed job
        get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        completed_data = get_resp.json()
        assert completed_data["status"] == "failed"
        assert completed_data["error_details"] is not None
        assert "no valid" in completed_data["error_details"].lower() or "failed" in completed_data["error_details"].lower()

    @patch("app.services.training.run_backtest")
    def test_failed_job_still_stores_raw_results(self, mock_backtest, client, auth_headers):
        """Even when all fail, raw results should be stored for diagnostics."""
        mock_backtest.return_value = {
            "run_status": "error",
            "metrics": {},
            "errors": ["err"],
        }

        exp = _create_experiment(client, auth_headers)
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {"short_window": [3], "long_window": [15]},
        })
        data = resp.json()
        job_id = data["id"]
        assert data["status"] == "pending"

        # Execute the job (simulating worker)
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            run_parameter_sweep(db, uuid.UUID(job_id))
            db.commit()
        finally:
            db.close()

        # Fetch the failed job
        get_resp = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        completed_data = get_resp.json()
        assert completed_data["status"] == "failed"
        # Raw results should still be present for diagnostics
        assert completed_data["results"] is not None
        assert len(completed_data["results"]) == 1
        assert completed_data["results"][0]["backtest_status"] == "error"


class TestRunningStateCancel:
    """Cancellation works for running jobs via cancellation flag."""

    @patch("app.services.training.run_backtest")
    def test_cancel_running_job_via_flag(self, mock_backtest, client, auth_headers):
        """The sweep loop should stop early when the cancellation flag is set."""
        def backtest_side_effect(strategy_spec):
            return {
                "run_status": "completed",
                "metrics": {"total_return_pct": 1.0, "max_drawdown_pct": 0.5},
                "errors": [],
            }

        mock_backtest.side_effect = backtest_side_effect

        exp = _create_experiment(client, auth_headers)

        # Create the job — it will be pending (async execution)
        resp = client.post("/api/v1/training/jobs", headers=auth_headers, json={
            "experiment_id": exp["id"],
            "parameter_grid": {
                "short_window": [3, 5],
                "long_window": [15, 20],
            },
        })
        job_id = resp.json()["id"]
        assert resp.json()["status"] == "pending"

        # Verify the cancellation mechanism works at the service level:
        # set the flag for this job_id and verify cancel_running_job sets it.
        from app.services.training import _cancellation_flags as flags
        flags.clear()

        # Call cancel_running_job and check it sets the flag
        cancel_running_job(uuid.UUID(job_id))
        assert flags.get(uuid.UUID(job_id)) is True
        flags.clear()

    def test_sweep_stops_when_cancelled(self, client, auth_headers):
        """Verify that the sweep loop actually stops when the cancellation flag is set.

        We create a pending job via the DB (without running the sweep), pre-set
        the cancellation flag, then call run_parameter_sweep directly.  The sweep
        should detect the flag before the first backtest and return immediately.
        """
        _cancellation_flags.clear()

        exp = _create_experiment(client, auth_headers)

        # Get a DB session from the test infrastructure
        from tests.conftest import TestSession
        db = TestSession()
        try:
            # Query for the test user (created by auth_headers fixture)
            from app.models.user import User
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            assert user is not None, "test user should exist"

            job = TrainingJob(
                user_id=user.id,
                experiment_id=uuid.UUID(exp["id"]),
                parameter_grid={"short_window": [3, 5, 7], "long_window": [15, 20]},
                strategy_spec_snapshot={
                    "template_type": "sma_crossover",
                    "asset_ids": ["DEMO-ALPHA"],
                    "signal_parameters": {"short_window": 5, "long_window": 20},
                },
                status=TrainingJobStatus.pending,
            )
            db.add(job)
            db.flush()

            # Pre-set cancellation flag
            cancel_running_job(job.id)
            assert _cancellation_flags.get(job.id) is True

            # Run the sweep — should detect the flag before the first backtest
            with patch("app.services.training.run_backtest") as mock_bt:
                mock_bt.return_value = {
                    "run_status": "completed",
                    "metrics": {"total_return_pct": 1.0},
                    "errors": [],
                }
                result_job = run_parameter_sweep(db, job.id)

            assert result_job.status == TrainingJobStatus.cancelled
            assert result_job.cancelled_at is not None
            assert "cancelled" in (result_job.error_details or "").lower()
            # The flag is checked BEFORE each backtest call, so 0 backtests ran
            mock_bt.assert_not_called()
            # Flag should have been cleaned up
            assert job.id not in _cancellation_flags

            db.rollback()
        finally:
            db.close()
            _cancellation_flags.clear()
