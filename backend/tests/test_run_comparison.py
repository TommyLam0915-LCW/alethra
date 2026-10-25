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


def test_compare_runs_non_completed_run_rejected(client, auth_headers, setup_db):
    """Backend must reject comparison when either run is not completed."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    run_completed = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_failed = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.failed)
    run_running = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="c", status=RunStatus.running)
    db.add(run_completed)
    db.add(run_failed)
    db.add(run_running)
    db.commit()
    ids = (str(run_completed.id), str(run_failed.id), str(run_running.id))
    db.close()

    a, failed, running = ids
    r1 = client.get(f"/api/v1/runs/compare?run_a={a}&run_b={failed}", headers=auth_headers)
    assert r1.status_code == 400
    assert "completed" in r1.json()["detail"].lower()

    r2 = client.get(f"/api/v1/runs/compare?run_a={failed}&run_b={running}", headers=auth_headers)
    assert r2.status_code == 400

    r3 = client.get(f"/api/v1/runs/compare?run_a={running}&run_b={a}", headers=auth_headers)
    assert r3.status_code == 400


def test_compare_runs_engine_version_from_run_record(client, auth_headers, setup_db):
    """engine_version must be sourced from research_runs, not the version's current field."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1,
                                 strategy_spec={}, engine_version="9.9.9", created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="a", status=RunStatus.completed,
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.1.0",
                        config_hash="b", status=RunStatus.completed,
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    db.add(run_a)
    db.add(run_b)
    db.commit()
    ida, idb = str(run_a.id), str(run_b.id)
    version_id_str = str(version.id)
    run_a_id_str = str(run_a.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={ida}&run_b={idb}", headers=auth_headers)
    data = response.json()
    ev = next((c for c in data["conditions_comparison"] if c["field"] == "engine_version"), None)
    assert ev is not None
    assert ev["value_a"] == "1.0.0"
    assert ev["value_b"] == "1.1.0"
    assert ev["comparison_status"] == "differ"
    assert ev["provenance_a"] == "captured"
    assert ev["provenance_b"] == "captured"
    detail_a = ev["evidence_detail"]["run_a"]
    assert detail_a["source_category"] == "run_record"
    assert detail_a["record_id"] == run_a_id_str
    assert detail_a["field_path"] == "research_runs.engine_version"


def test_compare_runs_spec_only_field_labelled_current(client, auth_headers, setup_db):
    """A spec-only condition (benchmark_id) must be labelled 'current'
    with record_id pointing at the ExperimentVersion, not the run."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1,
                                 strategy_spec={"benchmark_id": "DEMO-BENCH", "start_date": "2024-01-01", "end_date": "2024-06-30"},
                                 created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="a", status=RunStatus.completed,
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="b", status=RunStatus.completed,
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    db.add(run_a)
    db.add(run_b)
    db.commit()
    ida, idb, vid = str(run_a.id), str(run_b.id), str(version.id)
    db.close()

    response = client.get(f"/api/v1/runs/compare?run_a={ida}&run_b={idb}", headers=auth_headers)
    data = response.json()
    bm = next((c for c in data["conditions_comparison"] if c["field"] == "benchmark_id"), None)
    assert bm is not None
    assert bm["value_a"] == "DEMO-BENCH"
    assert bm["provenance_a"] == "current"
    assert bm["provenance_b"] == "current"
    assert bm["comparison_status"] == "match"
    detail_a = bm["evidence_detail"]["run_a"]
    assert detail_a["source_category"] == "version_metadata"
    assert detail_a["record_id"] == vid


def test_compare_runs_one_sided_missing_symmetric(client, auth_headers, setup_db):
    """Both A-only-missing and B-only-missing must yield provenance='absent' on the missing side."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    # Case 1: A missing, B has max_drawdown_pct
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="a", status=RunStatus.completed,
                        metrics={"total_return_pct": 5.0},  # no max_drawdown_pct
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="b", status=RunStatus.completed,
                        metrics={"total_return_pct": 5.0, "max_drawdown_pct": -3.3},
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    db.add(run_a)
    db.add(run_b)
    db.commit()
    ida, idb = str(run_a.id), str(run_b.id)
    db.close()

    data = client.get(f"/api/v1/runs/compare?run_a={ida}&run_b={idb}", headers=auth_headers).json()
    dd = next(c for c in data["metrics_comparison"] if c["field"] == "max_drawdown_pct")
    assert dd["value_a"] is None
    assert dd["value_b"] == -3.3
    assert dd["provenance_a"] == "absent"
    assert dd["provenance_b"] == "derived"
    assert dd["comparison_status"] == "insufficient"
    # Evidence for the missing side must be None
    assert dd["evidence_detail"]["run_a"] is None
    assert dd["evidence_detail"]["run_b"] is not None


def test_compare_runs_unknown_period_suppresses_delta(client, auth_headers, setup_db):
    """When either side's period cannot be resolved, the return delta must be suppressed
    and a limitation recorded — not treated as equal periods."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    # Empty strategy_spec, no dates from either source
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1,
                                 strategy_spec={}, created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="a", status=RunStatus.completed,
                        metrics={"total_return_pct": 12.5},
                        result_json={})
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="b", status=RunStatus.completed,
                        metrics={"total_return_pct": 10.2},
                        result_json={})
    db.add(run_a)
    db.add(run_b)
    db.commit()
    ida, idb = str(run_a.id), str(run_b.id)
    db.close()

    data = client.get(f"/api/v1/runs/compare?run_a={ida}&run_b={idb}", headers=auth_headers).json()
    rt = next(c for c in data["metrics_comparison"] if c["field"] == "total_return_pct")
    # Values preserved but delta suppressed
    assert rt["value_a"] == 12.5
    assert rt["value_b"] == 10.2
    assert rt["comparison_status"] == "insufficient"
    assert data["period_overlap"] is None
    assert any("日期範圍" in lim or "無法確定" in lim for lim in data["limitations"])


def test_compare_runs_period_source_labelled(client, auth_headers, setup_db):
    """Period overlap must expose whether the period was run-captured or version-spec."""
    db = TestSession()
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1,
                                 strategy_spec={"start_date": "2024-01-01", "end_date": "2024-12-31"},
                                 created_by=user.id)
    db.add(version)
    # run_a has run-captured dates that differ from spec
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="a", status=RunStatus.completed,
                        result_json={"start_date": "2024-01-01", "end_date": "2024-06-30"})
    # run_b has no result_json dates → falls back to version spec
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0",
                        config_hash="b", status=RunStatus.completed,
                        result_json={})
    db.add(run_a)
    db.add(run_b)
    db.commit()
    ida, idb = str(run_a.id), str(run_b.id)
    db.close()

    data = client.get(f"/api/v1/runs/compare?run_a={ida}&run_b={idb}", headers=auth_headers).json()
    assert data["period_overlap"] is not None
    assert data["period_overlap"]["run_a_period"]["source"] == "run_captured"
    assert data["period_overlap"]["run_b_period"]["source"] == "version_spec"

