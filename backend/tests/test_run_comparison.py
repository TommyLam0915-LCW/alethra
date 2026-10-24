"""Tests for run comparison endpoint."""
import pytest
from uuid import uuid4
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from tests.conftest import TestSession


def test_compare_runs_authorized_same_experiment(client, auth_headers, setup_db):
    """Test authorized comparison of two runs from the same experiment."""
    
    db = TestSession()

    # Get the test user
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    assert user is not None

    # Create experiment
    experiment_id = uuid4()
    experiment = Experiment(
        id=experiment_id,
        owner_id=user.id,
        title="Test Experiment",
        status="draft",
        visibility="private",
    )
    db.add(experiment)

    # Create two versions
    version_a_id = uuid4()
    version_b_id = uuid4()
    version_a = ExperimentVersion(
        id=version_a_id,
        experiment_id=experiment_id,
        version_number=1,
        strategy_spec={
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "initial_cash": 10000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        },
        created_by=user.id,
    )
    version_b = ExperimentVersion(
        id=version_b_id,
        experiment_id=experiment_id,
        version_number=2,
        strategy_spec={
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "initial_cash": 10000,
            "commission_rate": 0.002,
            "slippage_rate": 0.0005,
        },
        created_by=user.id,
    )
    db.add(version_a)
    db.add(version_b)

    # Create two runs
    run_a_id = uuid4()
    run_b_id = uuid4()
    run_a = ResearchRun(
        id=run_a_id,
        experiment_version_id=version_a_id,
        engine_version="1.0.0",
        config_hash="abc123",
        status=RunStatus.completed,
        metrics={"total_return_pct": 12.5, "final_equity": 11250, "total_trades": 5},
        result_json={
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "initial_cash": 10000,
            "final_equity": 11250,
            "data_provider": "demo",
            "dataset_checksum": "checksum_a",
        },
    )
    run_b = ResearchRun(
        id=run_b_id,
        experiment_version_id=version_b_id,
        engine_version="1.0.0",
        config_hash="def456",
        status=RunStatus.completed,
        metrics={"total_return_pct": 10.2, "final_equity": 11020, "total_trades": 5},
        result_json={
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "initial_cash": 10000,
            "final_equity": 11020,
            "data_provider": "demo",
            "dataset_checksum": "checksum_b",
        },
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a_id)
    run_b_str = str(run_b_id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["run_a_id"] == run_a_str
    assert data["run_b_id"] == run_b_str
    assert data["status_a"] == "completed"
    assert data["status_b"] == "completed"
    assert len(data["metrics_comparison"]) > 0
    assert len(data["conditions_comparison"]) > 0


def test_compare_runs_different_experiments_rejected(client, auth_headers, setup_db):
    """Test that comparing runs from different experiments is rejected."""
    
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    assert user is not None

    # Create two experiments
    experiment_1 = Experiment(id=uuid4(), owner_id=user.id, title="Exp 1", status="draft", visibility="private")
    experiment_2 = Experiment(id=uuid4(), owner_id=user.id, title="Exp 2", status="draft", visibility="private")
    db.add(experiment_1)
    db.add(experiment_2)

    version_1 = ExperimentVersion(id=uuid4(), experiment_id=experiment_1.id, version_number=1, strategy_spec={}, created_by=user.id)
    version_2 = ExperimentVersion(id=uuid4(), experiment_id=experiment_2.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version_1)
    db.add(version_2)

    run_1 = ResearchRun(id=uuid4(), experiment_version_id=version_1.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_2 = ResearchRun(id=uuid4(), experiment_version_id=version_2.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add(run_1)
    db.add(run_2)
    db.commit()

    run_1_str = str(run_1.id)
    run_2_str = str(run_2.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_1_str}&run_b={run_2_str}", headers=auth_headers)
    assert response.status_code == 400
    assert "same experiment" in response.json()["detail"]


def test_compare_runs_same_run_rejected(client, auth_headers, setup_db):
    """Test that comparing a run with itself is rejected."""
    
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    run = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    db.add(run)
    db.commit()

    run_str = str(run.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_str}&run_b={run_str}", headers=auth_headers)
    assert response.status_code == 400
    assert "Cannot compare" in response.json()["detail"]


def test_compare_runs_not_found(client, auth_headers):
    """Test that comparing non-existent runs returns 404."""
    fake_id = str(uuid4())
    response = client.get(f"/api/v1/runs/compare?run_a={fake_id}&run_b={fake_id}", headers=auth_headers)
    assert response.status_code == 404


def test_compare_runs_metrics_differ(client, auth_headers, setup_db):
    """Test that metrics differences are detected."""
    
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed,
        metrics={"total_return_pct": 12.5},
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    run_b = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed,
        metrics={"total_return_pct": 10.2},
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    data = response.json()

    return_comparison = next((m for m in data["metrics_comparison"] if m["field"] == "total_return_pct"), None)
    assert return_comparison is not None
    assert return_comparison["comparison_status"] == "differ"
    assert return_comparison["value_a"] == 12.5
    assert return_comparison["value_b"] == 10.2


def test_compare_runs_provenance_labelled(client, auth_headers, setup_db):
    """Test that provenance is correctly labelled."""
    
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    data = response.json()

    for field in data["metrics_comparison"]:
        assert "provenance_a" in field
        assert "provenance_b" in field
        assert field["provenance_a"] in ["captured", "derived", "absent", "current", "unavailable"]


def test_compare_runs_cross_user_rejected(client, setup_db):
    """Test that comparing runs from different users is rejected."""
    from app.auth.jwt import get_password_hash

    db = TestSession()

    # Create two users with proper password hashes
    user_a = User(id=uuid4(), email="user_a@test.com", password_hash=get_password_hash("testpass123"), display_name="User A")
    user_b = User(id=uuid4(), email="user_b@test.com", password_hash=get_password_hash("testpass123"), display_name="User B")
    db.add(user_a)
    db.add(user_b)

    # Create experiments for each user
    exp_a = Experiment(id=uuid4(), owner_id=user_a.id, title="Exp A", status="draft", visibility="private")
    exp_b = Experiment(id=uuid4(), owner_id=user_b.id, title="Exp B", status="draft", visibility="private")
    db.add(exp_a)
    db.add(exp_b)

    ver_a = ExperimentVersion(id=uuid4(), experiment_id=exp_a.id, version_number=1, strategy_spec={}, created_by=user_a.id)
    ver_b = ExperimentVersion(id=uuid4(), experiment_id=exp_b.id, version_number=1, strategy_spec={}, created_by=user_b.id)
    db.add(ver_a)
    db.add(ver_b)

    run_a = ResearchRun(id=uuid4(), experiment_version_id=ver_a.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=ver_b.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    # Login as user_a
    client.post("/api/auth/register", json={"email": "user_a@test.com", "password": "testpass123", "display_name": "User A", "user_type": "individual"})
    login = client.post("/api/auth/login", json={"email": "user_a@test.com", "password": "testpass123"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=headers)
    assert response.status_code == 404


def test_compare_runs_different_periods_suppress_delta(client, auth_headers, setup_db):
    """Test that return delta is suppressed when periods differ."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed,
        metrics={"total_return_pct": 12.5},
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    run_b = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed,
        metrics={"total_return_pct": 10.2},
        result_json={"start_date": "2024-03-01", "end_date": "2024-09-30"},
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    data = response.json()

    # Check that period_overlap is present
    assert data["period_overlap"] is not None
    assert data["period_overlap"]["overlap"]["start"] == "2024-03-01"
    assert data["period_overlap"]["overlap"]["end"] == "2024-06-30"

    # Check that return delta is suppressed but values are preserved
    return_comparison = next((m for m in data["metrics_comparison"] if m["field"] == "total_return_pct"), None)
    assert return_comparison is not None
    assert return_comparison["comparison_status"] == "insufficient"
    assert return_comparison["value_a"] == 12.5  # Recorded value preserved
    assert return_comparison["value_b"] == 10.2  # Recorded value preserved

    # Check limitation message
    assert any("日期範圍唔同" in lim for lim in data["limitations"])


def test_compare_runs_one_sided_missing(client, auth_headers, setup_db):
    """Test that one-sided missing data is handled correctly."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    # Run A has max_drawdown_pct, Run B does not
    run_a = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed,
        metrics={"total_return_pct": 12.5, "max_drawdown_pct": -8.2},
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    run_b = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed,
        metrics={"total_return_pct": 10.2},  # max_drawdown_pct missing
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    data = response.json()

    # Check max_drawdown_pct: one side present, one side missing
    drawdown_comparison = next((m for m in data["metrics_comparison"] if m["field"] == "max_drawdown_pct"), None)
    assert drawdown_comparison is not None
    assert drawdown_comparison["value_a"] == -8.2  # Present value preserved
    assert drawdown_comparison["value_b"] is None  # Missing value is None
    assert drawdown_comparison["comparison_status"] == "insufficient"  # Not a meaningful difference
    assert drawdown_comparison["provenance_a"] == "derived"
    assert drawdown_comparison["provenance_b"] == "absent"  # Correctly labelled as absent


def test_compare_runs_both_sides_missing(client, auth_headers, setup_db):
    """Test that both-sides missing data is handled correctly."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    # Both runs missing sharpe_ratio
    run_a = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed,
        metrics={"total_return_pct": 12.5},  # sharpe_ratio missing
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    run_b = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed,
        metrics={"total_return_pct": 10.2},  # sharpe_ratio missing
        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    data = response.json()

    # Check sharpe_ratio: both sides missing
    sharpe_comparison = next((m for m in data["metrics_comparison"] if m["field"] == "sharpe_ratio"), None)
    assert sharpe_comparison is not None
    assert sharpe_comparison["value_a"] is None
    assert sharpe_comparison["value_b"] is None
    assert sharpe_comparison["comparison_status"] == "insufficient"  # Not a match
    assert sharpe_comparison["provenance_a"] == "absent"
    assert sharpe_comparison["provenance_b"] == "absent"


def test_compare_runs_read_only(client, auth_headers, setup_db):
    """Test that comparison endpoint does not modify state."""
    db = TestSession()

    # Capture initial state
    initial_run_count = db.query(ResearchRun).count()
    initial_version_count = db.query(ExperimentVersion).count()
    initial_experiment_count = db.query(Experiment).count()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add(run_a)
    db.add(run_b)
    db.commit()

    run_a_str = str(run_a.id)
    run_b_str = str(run_b.id)
    db.close()

    # Call comparison endpoint
    response = client.get(f"/api/v1/runs/compare?run_a={run_a_str}&run_b={run_b_str}", headers=auth_headers)
    assert response.status_code == 200

    # Verify state unchanged
    db = TestSession()
    assert db.query(ResearchRun).count() == initial_run_count + 2
    assert db.query(ExperimentVersion).count() == initial_version_count + 1
    assert db.query(Experiment).count() == initial_experiment_count + 1
    db.close()
