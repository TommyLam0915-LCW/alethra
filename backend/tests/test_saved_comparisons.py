"""Tests for saved comparison functionality."""
import uuid
import pytest
from uuid import uuid4
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.saved_comparison import SavedComparison
from tests.conftest import TestSession


def _seed_user_experiment_runs(db, email="test@alethra.io"):
    """Create user (if missing) + experiment + version + 2 completed runs.

    Returns plain (string-safe) UUID values captured while the session is open,
    so callers can close the session before asserting without DetachedInstance.
    """
    user = db.query(User).filter(User.email == email).first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed, metrics={"total_return_pct": 12.5})
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed, metrics={"total_return_pct": 10.2})
    db.add_all([run_a, run_b])
    db.commit()
    return {
        "user_id": uuid.UUID(str(user.id)),
        "experiment_id": uuid.UUID(str(experiment.id)),
        "version_id": uuid.UUID(str(version.id)),
        "run_a_id": uuid.UUID(str(run_a.id)),
        "run_b_id": uuid.UUID(str(run_b.id)),
    }


def test_create_saved_comparison(client, auth_headers, setup_db):
    """Test creating a saved comparison."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a",
        status=RunStatus.completed, metrics={"total_return_pct": 12.5}
    )
    run_b = ResearchRun(
        id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b",
        status=RunStatus.completed, metrics={"total_return_pct": 10.2}
    )
    db.add(run_a)
    db.add(run_b)
    db.commit()

    response = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(experiment.id),
        "run_a_id": str(run_a.id),
        "run_b_id": str(run_b.id),
        "purpose": "Test commission rate impact",
        "notes": "Comparing two runs with different commission rates"
    }, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["purpose"] == "Test commission rate impact"
    assert data["notes"] == "Comparing two runs with different commission rates"
    assert data["run_a_id"] == str(run_a.id)
    assert data["run_b_id"] == str(run_b.id)
    assert data["experiment_id"] == str(experiment.id)

    db.close()


def test_list_saved_comparisons(client, auth_headers, setup_db):
    """Test listing saved comparisons for an experiment."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    run_c = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="c", status=RunStatus.completed)
    db.add_all([run_a, run_b, run_c])
    db.commit()

    # Create two saved comparisons
    saved1 = SavedComparison(
        id=uuid4(), author_id=user.id, experiment_id=experiment.id,
        run_a_id=run_a.id, run_b_id=run_b.id, purpose="Comparison 1"
    )
    saved2 = SavedComparison(
        id=uuid4(), author_id=user.id, experiment_id=experiment.id,
        run_a_id=run_b.id, run_b_id=run_c.id, purpose="Comparison 2"
    )
    db.add_all([saved1, saved2])
    db.commit()

    response = client.get(f"/api/v1/experiments/{experiment.id}/saved-comparisons", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    purposes = {item["purpose"] for item in data}
    assert "Comparison 1" in purposes
    assert "Comparison 2" in purposes

    db.close()


def test_get_saved_comparison(client, auth_headers, setup_db):
    """Test retrieving a specific saved comparison."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add_all([run_a, run_b])
    db.commit()

    saved = SavedComparison(
        id=uuid4(), author_id=user.id, experiment_id=experiment.id,
        run_a_id=run_a.id, run_b_id=run_b.id, purpose="Test purpose", notes="Test notes"
    )
    db.add(saved)
    db.commit()

    response = client.get(f"/api/v1/saved-comparisons/{saved.id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(saved.id)
    assert data["purpose"] == "Test purpose"
    assert data["notes"] == "Test notes"
    assert data["run_a_id"] == str(run_a.id)
    assert data["run_b_id"] == str(run_b.id)

    db.close()


def test_update_saved_comparison(client, auth_headers, setup_db):
    """Test updating a saved comparison's purpose and notes."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)

    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add_all([run_a, run_b])
    db.commit()

    saved = SavedComparison(
        id=uuid4(), author_id=user.id, experiment_id=experiment.id,
        run_a_id=run_a.id, run_b_id=run_b.id, purpose="Original purpose", notes="Original notes"
    )
    db.add(saved)
    db.commit()

    response = client.patch(f"/api/v1/saved-comparisons/{saved.id}", json={
        "purpose": "Updated purpose",
        "notes": "Updated notes"
    }, headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["purpose"] == "Updated purpose"
    assert data["notes"] == "Updated notes"
    # Run references should remain unchanged
    assert data["run_a_id"] == str(run_a.id)
    assert data["run_b_id"] == str(run_b.id)

    db.close()


def test_saved_comparison_cross_user_denied(client, setup_db):
    """Test that another user cannot access saved comparisons."""
    from app.auth.jwt import get_password_hash

    db = TestSession()

    # Create two users
    user_a = User(id=uuid4(), email="user_a@test.com", password_hash=get_password_hash("pass"), display_name="User A")
    user_b = User(id=uuid4(), email="user_b@test.com", password_hash=get_password_hash("pass"), display_name="User B")
    db.add_all([user_a, user_b])
    db.commit()

    # Create experiment and runs for user_a
    experiment = Experiment(id=uuid4(), owner_id=user_a.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user_a.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add_all([run_a, run_b])
    db.commit()

    # Create saved comparison for user_a
    saved = SavedComparison(
        id=uuid4(), author_id=user_a.id, experiment_id=experiment.id,
        run_a_id=run_a.id, run_b_id=run_b.id, purpose="User A's comparison"
    )
    db.add(saved)
    db.commit()

    saved_id = str(saved.id)
    experiment_id = str(experiment.id)
    db.close()

    # Login as user_b
    client.post("/api/auth/register", json={"email": "user_b@test.com", "password": "pass", "display_name": "User B", "user_type": "individual"})
    login = client.post("/api/auth/login", json={"email": "user_b@test.com", "password": "pass"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to access user_a's saved comparison
    response = client.get(f"/api/v1/saved-comparisons/{saved_id}", headers=headers)
    assert response.status_code == 404

    # Try to list user_a's experiment saved comparisons
    response = client.get(f"/api/v1/experiments/{experiment_id}/saved-comparisons", headers=headers)
    assert response.status_code == 404

    # Try to update user_a's saved comparison
    response = client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={
        "purpose": "Hacked purpose"
    }, headers=headers)
    assert response.status_code == 404


def test_saved_comparison_same_run_rejected(client, auth_headers, setup_db):
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

    response = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(experiment.id),
        "run_a_id": str(run.id),
        "run_b_id": str(run.id),
        "purpose": "Same run comparison"
    }, headers=auth_headers)

    assert response.status_code == 400
    assert "Cannot compare a run with itself" in response.json()["detail"]

    db.close()


def test_saved_comparison_invalid_run_rejected(client, auth_headers, setup_db):
    """Test that using a non-existent run is rejected."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    db.commit()

    response = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(experiment.id),
        "run_a_id": str(uuid4()),
        "run_b_id": str(uuid4()),
        "purpose": "Invalid runs"
    }, headers=auth_headers)

    assert response.status_code == 404

    db.close()


def test_saved_comparison_purpose_required(client, auth_headers, setup_db):
    """Test that purpose is required."""
    db = TestSession()

    user = db.query(User).filter(User.email == "test@alethra.io").first()
    experiment = Experiment(id=uuid4(), owner_id=user.id, title="Exp", status="draft", visibility="private")
    db.add(experiment)
    version = ExperimentVersion(id=uuid4(), experiment_id=experiment.id, version_number=1, strategy_spec={}, created_by=user.id)
    db.add(version)
    run_a = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="a", status=RunStatus.completed)
    run_b = ResearchRun(id=uuid4(), experiment_version_id=version.id, engine_version="1.0.0", config_hash="b", status=RunStatus.completed)
    db.add_all([run_a, run_b])
    db.commit()

    response = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(experiment.id),
        "run_a_id": str(run_a.id),
        "run_b_id": str(run_b.id),
        "purpose": ""
    }, headers=auth_headers)

    assert response.status_code == 422  # Validation error

    db.close()


# --------------------------------------------------------------------------
# Step 2 — persistence + update identity, proven via a fresh DB session
# --------------------------------------------------------------------------

def test_create_persists_across_fresh_session(client, auth_headers, setup_db):
    """A saved comparison written by the request session must be readable and
    identical when opened in a brand-new session (proves real DB persistence)."""
    write_db = TestSession()
    seed = _seed_user_experiment_runs(write_db)
    write_db.close()

    resp = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(seed["experiment_id"]),
        "run_a_id": str(seed["run_a_id"]),
        "run_b_id": str(seed["run_b_id"]),
        "purpose": "Fresh-session persistence",
        "notes": "written then closed",
    }, headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()

    # Open a genuinely new session after the request session is done.
    read_db = TestSession()
    try:
        rows = read_db.query(SavedComparison).filter(
            SavedComparison.experiment_id == seed["experiment_id"]
        ).all()
        assert len(rows) == 1, "create must produce exactly one row"
        row = rows[0]
        assert str(row.id) == body["id"]
        assert row.run_a_id == seed["run_a_id"]
        assert row.run_b_id == seed["run_b_id"]
        assert row.author_id == seed["user_id"]
        assert row.purpose == "Fresh-session persistence"
        assert row.notes == "written then closed"
    finally:
        read_db.close()


def test_update_persists_and_identity_is_immutable_fresh_session(client, auth_headers, setup_db):
    """After PATCH, a fresh session sees the new purpose/notes but the record
    identity (id / run_a / run_b) is unchanged and no second row appeared."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    saved = SavedComparison(
        id=uuid4(), author_id=seed["user_id"], experiment_id=seed["experiment_id"],
        run_a_id=seed["run_a_id"], run_b_id=seed["run_b_id"], purpose="Original", notes="Original notes",
    )
    db.add(saved)
    db.commit()
    saved_id = str(saved.id)
    db.close()

    resp = client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={
        "purpose": "Updated", "notes": "Updated notes",
    }, headers=auth_headers)
    assert resp.status_code == 200

    read_db = TestSession()
    try:
        rows = read_db.query(SavedComparison).filter(
            SavedComparison.experiment_id == seed["experiment_id"]
        ).all()
        assert len(rows) == 1, "update must not create a second row"
        row = rows[0]
        assert str(row.id) == saved_id, "id must not change on update"
        assert row.purpose == "Updated"
        assert row.notes == "Updated notes"
        # Run references immutable
        assert row.run_a_id == seed["run_a_id"]
        assert row.run_b_id == seed["run_b_id"]
        assert row.author_id == seed["user_id"]
    finally:
        read_db.close()


# --------------------------------------------------------------------------
# Step 3 — referenced-resource authorization (both runs) + safe failure
# --------------------------------------------------------------------------

def test_get_denied_when_referenced_run_not_in_experiment(client, auth_headers, setup_db):
    """A crafted saved comparison whose run_a_id points at a run belonging to a
    DIFFERENT experiment must 404 on GET (run re-authorization, no leak)."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)

    # Separate experiment + a run that belongs to it, not to `experiment`.
    other_exp = Experiment(id=uuid4(), owner_id=seed["user_id"], title="Other", status="draft", visibility="private")
    db.add(other_exp)
    other_version = ExperimentVersion(id=uuid4(), experiment_id=other_exp.id, version_number=1, strategy_spec={}, created_by=seed["user_id"])
    db.add(other_version)
    foreign_run = ResearchRun(id=uuid4(), experiment_version_id=other_version.id, engine_version="1.0.0", config_hash="x", status=RunStatus.completed)
    db.add(foreign_run)
    db.commit()
    foreign_run_id = uuid.UUID(str(foreign_run.id))

    saved = SavedComparison(
        id=uuid4(), author_id=seed["user_id"], experiment_id=seed["experiment_id"],
        run_a_id=foreign_run_id, run_b_id=seed["run_b_id"], purpose="Cross-experiment ref",
    )
    db.add(saved)
    db.commit()
    saved_id = str(saved.id)
    db.close()

    resp = client.get(f"/api/v1/saved-comparisons/{saved_id}", headers=auth_headers)
    assert resp.status_code == 404
    assert "purpose" not in resp.json()  # no metadata leak from the blocked record


def test_get_404_not_500_when_referenced_run_not_completed(client, auth_headers, setup_db):
    """A referenced run that is no longer completed must yield 404, never 500."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    pending = ResearchRun(id=uuid4(), experiment_version_id=seed["version_id"], engine_version="1.0.0", config_hash="p", status=RunStatus.running)
    db.add(pending)
    db.commit()
    pending_id = uuid.UUID(str(pending.id))

    saved = SavedComparison(
        id=uuid4(), author_id=seed["user_id"], experiment_id=seed["experiment_id"],
        run_a_id=pending_id, run_b_id=seed["run_b_id"], purpose="Pending ref",
    )
    db.add(saved)
    db.commit()
    saved_id = str(saved.id)
    db.close()

    resp = client.get(f"/api/v1/saved-comparisons/{saved_id}", headers=auth_headers)
    assert resp.status_code == 404  # not a 500


def test_patch_cannot_reassign_runs(client, auth_headers, setup_db):
    """PATCH payload must not be able to change run_a_id/run_b_id/id/author_id."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    other = ResearchRun(id=uuid4(), experiment_version_id=seed["version_id"], engine_version="1.0.0", config_hash="other", status=RunStatus.completed)
    db.add(other)
    saved = SavedComparison(
        id=uuid4(), author_id=seed["user_id"], experiment_id=seed["experiment_id"],
        run_a_id=seed["run_a_id"], run_b_id=seed["run_b_id"], purpose="Keep me",
    )
    db.add(saved)
    db.commit()
    saved_id = str(saved.id)
    other_id = str(other.id)
    db.close()

    resp = client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={
        "purpose": "Changed",
        "run_a_id": other_id,
        "run_b_id": other_id,
        "id": str(uuid.uuid4()),
        "author_id": str(uuid.uuid4()),
    }, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == saved_id
    assert body["run_a_id"] == str(seed["run_a_id"])
    assert body["run_b_id"] == str(seed["run_b_id"])
    assert body["author_id"] == str(seed["user_id"])


# --------------------------------------------------------------------------
# Step 4 — input validation + notes omission semantics + read-only runs
# --------------------------------------------------------------------------

def test_purpose_whitespace_rejected(client, auth_headers, setup_db):
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    db.close()

    resp = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(seed["experiment_id"]),
        "run_a_id": str(seed["run_a_id"]),
        "run_b_id": str(seed["run_b_id"]),
        "purpose": "    ",
    }, headers=auth_headers)
    assert resp.status_code == 422


def test_purpose_and_notes_max_length(client, auth_headers, setup_db):
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    db.close()

    too_long_purpose = "x" * 501
    resp = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(seed["experiment_id"]),
        "run_a_id": str(seed["run_a_id"]),
        "run_b_id": str(seed["run_b_id"]),
        "purpose": too_long_purpose,
    }, headers=auth_headers)
    assert resp.status_code == 422

    too_long_notes = "y" * 5001
    resp = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(seed["experiment_id"]),
        "run_a_id": str(seed["run_a_id"]),
        "run_b_id": str(seed["run_b_id"]),
        "purpose": "ok",
        "notes": too_long_notes,
    }, headers=auth_headers)
    assert resp.status_code == 422


def test_patch_notes_omitted_preserves_explicit_null_clears(client, auth_headers, setup_db):
    """Omitting `notes` on PATCH keeps the stored note; sending notes=null clears it."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    saved = SavedComparison(
        id=uuid4(), author_id=seed["user_id"], experiment_id=seed["experiment_id"],
        run_a_id=seed["run_a_id"], run_b_id=seed["run_b_id"], purpose="P", notes="Keep this note",
    )
    db.add(saved)
    db.commit()
    saved_id = str(saved.id)
    db.close()

    # Omit notes → preserved, only purpose changes.
    resp = client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={
        "purpose": "P2",
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Keep this note"

    # Explicit null → cleared.
    resp = client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={
        "purpose": "P3", "notes": None,
    }, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["notes"] is None


def test_save_and_update_do_not_mutate_runs(client, auth_headers, setup_db):
    """Persisting a saved comparison is read-only over the runs: no status or
    metrics change."""
    db = TestSession()
    seed = _seed_user_experiment_runs(db)
    a_id, b_id = seed["run_a_id"], seed["run_b_id"]
    before = {}
    for rid in (a_id, b_id):
        run = db.query(ResearchRun).filter(ResearchRun.id == rid).first()
        before[str(rid)] = (run.status, dict(run.metrics or {}))
    db.close()

    resp = client.post("/api/v1/saved-comparisons", json={
        "experiment_id": str(seed["experiment_id"]),
        "run_a_id": str(a_id),
        "run_b_id": str(b_id),
        "purpose": "read only",
    }, headers=auth_headers)
    assert resp.status_code == 201
    saved_id = resp.json()["id"]

    client.patch(f"/api/v1/saved-comparisons/{saved_id}", json={"purpose": "still read only"}, headers=auth_headers)

    check = TestSession()
    try:
        for rid in (a_id, b_id):
            run = check.query(ResearchRun).filter(ResearchRun.id == rid).first()
            assert (run.status, dict(run.metrics or {})) == before[str(rid)]
    finally:
        check.close()
