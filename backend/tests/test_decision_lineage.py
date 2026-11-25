"""Decision lineage: each decision links to the specific inference that produced it.

Tests the POST /{portfolio_id}/decisions endpoint with linked_inference_id to verify:
1. Decision can reference a valid inference from the same owner/skill
2. Same owner with 2 inferences → decision points to exactly one (not ambiguous)
3. Later model switch doesn't retroactively change original decision's source
4. Cross-owner inference linkage is refused (404)
5. Cross-skill inference linkage is refused (400)
6. Inference without prediction_type="personal_decision" is refused (400)
7. Non-existent inference ID is refused (404)
8. Decision can still be recorded with no inference at all
9. Metadata that is not an object is refused rather than crashing the check
10. A missing, NULL or non-text skill_unit_id is refused, never read as "all skills"
11. A refusal writes no decision row
12. A malformed inference ID is refused by the request schema, not the database
13. An owned, frozen strategy version links, and reads back as the same id
14. A version the owner never froze is refused as not usable
15. Another owner's version is reported as absent, not as forbidden
16. A made-up version id is refused as version_not_found
17. A version that contradicts the account's own run binding is refused, echoing both ids
18. A version refusal writes no decision row
19. An entry/exit without rationale or expected outcome is refused before anything is written
20. The account-creation door refuses the same unfrozen, foreign and absent versions
21. The shared rule still admits its owner's own frozen version

Every refusal asserts the ``reason`` the caller actually receives, because a bare
status code is satisfied by any 400 for any other cause.

The inference is recorded in usage_records (billing.py) with stable UUID primary key.
The decision's linked_inference_id column references that key for traceability.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.models.agent_strategy import (
    AgentDatasetSnapshot,
    AgentStrategyTrainingJob,
)
from app.models.billing import UsageRecord
from app.models.experiment import Experiment, ExperimentStatus, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.model_research import ModelResearchRecord, ModelVersion
from app.models.paper_portfolio import DecisionRecord, PaperPortfolio, PortfolioStatus
from app.models.paper_trading_run import PaperTradingRun
from app.models.personal_model_selection import PersonalModelSelection
from app.services import model_activation
from app.services.strategy_versions import validate_strategy_version
from tests.conftest import TestSession


def _register_and_login(client, email):
    """Register a user and return auth headers."""
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "testpass123",
            "display_name": email.split("@")[0],
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "testpass123"},
    )
    assert login.status_code == 200, f"login for {email} -> {login.status_code}"
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_skill_unit(db, owner_id):
    """Create a minimal skill unit (paper portfolio) for testing."""
    portfolio = PaperPortfolio(
        owner_id=owner_id,
        currency="USD",
        initial_virtual_cash=100000.0,
        current_virtual_cash=100000.0,
        status=PortfolioStatus.active,
    )
    db.add(portfolio)
    db.flush()
    return portfolio


def _create_experiment_and_version(db, owner_id):
    """Create an experiment and version for training job."""
    experiment = Experiment(
        owner_id=owner_id,
        title="Test Experiment",
        hypothesis="Test",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.draft,
    )
    db.add(experiment)
    db.flush()

    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={"test": True},
    )
    db.add(version)
    db.flush()

    return version.id


def _create_training_artifact(db, owner_id):
    """Create a complete training job with snapshot and artifact."""
    experiment_version_id = _create_experiment_and_version(db, owner_id)

    snapshot = AgentDatasetSnapshot(
        owner_id=owner_id,
        strategy_version_id=experiment_version_id,
        consent_policy_version="v1",
        consent_status="active",
        status="ready",
        candidate_ids_json=[],
        rows_json=[],
        feature_names_json=["feature1", "feature2"],
        label_definition_json={},
        exclusion_breakdown_json={},
        outcome_distribution_json={},
        split_manifest_json={},
        checksum="0" * 64,
    )
    db.add(snapshot)
    db.flush()

    training_job = AgentStrategyTrainingJob(
        owner_id=owner_id,
        strategy_version_id=experiment_version_id,
        snapshot_id=snapshot.id,
        status="completed",
        training_contract_version="v1",
        derived_label_version="v1",
        dataset_checksum="0" * 64,
        hyperparameters_json={"lr": 0.01},
        hyperparameters_hash="0" * 64,
        row_counts_json={"train": 80, "test": 20},
        lineage_json={"parent_job_id": None},
        artifact_path="/tmp/test-artifact.pkl",
        artifact_checksum="a" * 64,
        artifact_metadata_checksum="m" * 64,
        activation_status="NOT_APPROVED",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(training_job)
    db.flush()

    return training_job, snapshot


def _create_model_version_with_training(db, owner_id, training_job):
    """Create a model version linked to a training job."""
    from app.models.model_research import ModelType
    record = ModelResearchRecord(
        owner_id=owner_id,
        title="Test Model",
        purpose="personal_decision",
    )
    db.add(record)
    db.flush()

    version = ModelVersion(
        model_research_record_id=record.id,
        version_number=1,
        model_type=ModelType.personal_decision_model,
        model_specification_json={
            "artifact_id": "test-artifact",
            "artifact_sha256": training_job.artifact_checksum,
            "training_job_id": str(training_job.id),
            "snapshot_id": str(training_job.snapshot_id),
            "feature_names": ["feature1", "feature2"],
            "task_type": "personal_decision_mimicry",
        },
    )
    db.add(version)
    db.flush()

    # Set the version as the active version for the record
    record.active_version_id = version.id
    db.flush()

    return record, version


def _create_inference_record(db, owner_id, model_version_id, skill_unit_id, metadata_extra=None):
    """Create a usage_record representing an inference."""
    metadata = {
        "prediction_type": "personal_decision",
        "model_version_id": str(model_version_id),
        "skill_unit_id": str(skill_unit_id),
        "instrument_id": "BTC-USD",
        "as_of_date": "2024-12-01",
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    record = UsageRecord(
        user_id=owner_id,
        subscription_id=None,
        endpoint="/api/v1/ml/personal/models/{version_id}/predict",
        model=str(model_version_id),
        tokens_used=0,
        cost=0,
        metadata_json=metadata,
    )
    db.add(record)
    db.flush()
    return record


def _assert_refused(response, status_code, reason):
    """Assert the refusal *and* its named cause.

    A bare status code is satisfied by any 400 or 404 raised for any other
    reason, which would let a branch disappear while its test stayed green.
    """
    assert response.status_code == status_code, (
        f"Expected {status_code}, got {response.status_code}: {response.text}"
    )
    detail = response.json()["detail"]
    assert isinstance(detail, dict), f"refusal names no reason: {detail!r}"
    assert detail.get("reason") == reason, response.text


def _create_metadata_only_inference(db, owner_id, metadata):
    """One usage row whose metadata is whatever the case under test needs.

    Deliberately no model version and no training job: the lineage check reads
    the row's owner and its metadata and nothing else, so a fixture that built the
    model chain here would imply it had tested something it never looked at.
    """
    record = UsageRecord(
        user_id=owner_id,
        subscription_id=None,
        endpoint="/api/v1/ml/personal/models/{version_id}/predict",
        model=None,
        tokens_used=0,
        cost=0,
        metadata_json=metadata,
    )
    db.add(record)
    db.flush()
    return record


_NO_KEY = object()


def _personal_metadata(skill_unit_id=_NO_KEY):
    """Metadata of the shape the prediction path writes.

    Omit the argument to leave ``skill_unit_id`` out of the object entirely; pass
    ``None`` to write it as JSON null. The two are different stored facts and the
    check must refuse both, so the fixture can produce both.
    """
    metadata = {
        "prediction_type": "personal_decision",
        "instrument_id": "BTC-USD",
        "as_of_date": "2024-12-01",
    }
    if skill_unit_id is not _NO_KEY:
        metadata["skill_unit_id"] = skill_unit_id
    return metadata


@pytest.fixture
def owner_a(client):
    """Owner A with headers and DB session."""
    headers = _register_and_login(client, "owner-a@test.com")
    db = TestSession()
    try:
        from app.models.user import User
        user = db.query(User).filter(User.email == "owner-a@test.com").first()
        yield user, headers, db
    finally:
        db.close()


@pytest.fixture
def owner_b(client):
    """Owner B (different user) with headers and DB session."""
    headers = _register_and_login(client, "owner-b@test.com")
    db = TestSession()
    try:
        from app.models.user import User
        user = db.query(User).filter(User.email == "owner-b@test.com").first()
        yield user, headers, db
    finally:
        db.close()


def test_decision_can_link_to_specific_inference(client, owner_a):
    """T1: Decision references a valid inference from same owner/skill."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    training_job, snapshot = _create_training_artifact(db, user.id)
    research, model_version = _create_model_version_with_training(db, user.id, training_job)
    inference = _create_inference_record(db, user.id, model_version.id, skill_unit.id)
    db.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "actor_type": "ai_draft",
            "rationale": "Test decision linked to inference",
            "expected_outcome": "Positive return",
            "expected_timeframe_days": 7,
            "self_reported_confidence": "medium",
            "linked_inference_id": str(inference.id),
        },
    )
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    decision_id = uuid.UUID(response.json()["id"])
    decision = db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).first()
    assert decision is not None
    assert decision.linked_inference_id == inference.id
    assert decision.owner_id == user.id
    # The row reads back with the same identity the caller sent, from the route
    # that answers "what produced this decision" -- not a re-derived guess.
    assert response.json()["linked_inference_id"] == str(inference.id)
    assert response.json()["linked_experiment_version_id"] is None


def test_two_inferences_decision_points_to_one(client, owner_a):
    """T2: Same owner/skill has 2 inferences → decision points to exactly one."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    training_job, snapshot = _create_training_artifact(db, user.id)
    research, model_version = _create_model_version_with_training(db, user.id, training_job)

    inference_1 = _create_inference_record(db, user.id, model_version.id, skill_unit.id)
    inference_2 = _create_inference_record(db, user.id, model_version.id, skill_unit.id)
    db.commit()

    assert inference_1.id != inference_2.id, "Two inferences should have different IDs"

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "rationale": "Test rationale",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(inference_1.id),
        },
    )
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    decision_id = uuid.UUID(response.json()["id"])
    decision = db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).first()
    assert decision.linked_inference_id == inference_1.id
    assert decision.linked_inference_id != inference_2.id


def test_later_model_switch_does_not_change_original_decision(client, owner_a):
    """T3: Later model switch doesn't retroactively change original decision's source."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    training_job_v1, _ = _create_training_artifact(db, user.id)
    research_v1, model_v1 = _create_model_version_with_training(db, user.id, training_job_v1)
    inference_v1 = _create_inference_record(db, user.id, model_v1.id, skill_unit.id)

    response_v1 = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(inference_v1.id),
            "rationale": "Decision based on model v1",
        },
    )
    assert response_v1.status_code == 201
    decision_id = uuid.UUID(response_v1.json()["id"])

    training_job_v2, _ = _create_training_artifact(db, user.id)
    research_v2, model_v2 = _create_model_version_with_training(db, user.id, training_job_v2)
    inference_v2 = _create_inference_record(db, user.id, model_v2.id, skill_unit.id)
    db.commit()

    decision = db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).first()
    assert decision.linked_inference_id == inference_v1.id
    assert decision.linked_inference_id != inference_v2.id

    original_inference = db.query(UsageRecord).filter(UsageRecord.id == inference_v1.id).first()
    assert original_inference.model == str(model_v1.id)


def test_cross_owner_inference_refused(client, owner_a, owner_b):
    """T4: Cannot link decision to inference owned by different user."""
    user_a, headers_a, db_a = owner_a
    user_b, headers_b, db_b = owner_b

    skill_unit_a = _create_skill_unit(db_a, user_a.id)
    training_job_a, _ = _create_training_artifact(db_a, user_a.id)
    research_a, model_a = _create_model_version_with_training(db_a, user_a.id, training_job_a)
    inference_a = _create_inference_record(db_a, user_a.id, model_a.id, skill_unit_a.id)
    db_a.commit()

    skill_unit_b = _create_skill_unit(db_b, user_b.id)
    db_b.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit_b.id}/decisions",
        headers=headers_b,
        json={
            "decision_type": "entry",
            "rationale": "Test rationale",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(inference_a.id),
        },
    )
    _assert_refused(response, 404, "inference_not_found")


def test_cross_skill_inference_refused(client, owner_a):
    """T5: Inference for skill A cannot be linked to decision for skill B."""
    user, headers, db = owner_a

    skill_a = _create_skill_unit(db, user.id)
    skill_b = _create_skill_unit(db, user.id)
    training_job, _ = _create_training_artifact(db, user.id)
    research, model = _create_model_version_with_training(db, user.id, training_job)
    inference_a = _create_inference_record(db, user.id, model.id, skill_a.id)
    db.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_b.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "rationale": "Test rationale",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(inference_a.id),
        },
    )
    _assert_refused(response, 400, "inference_skill_mismatch")
    # The refusal says which account it looked at, so the caller can tell
    # "wrong skill" from "no skill recorded".
    assert response.json()["detail"]["inference_skill_unit_id"] == str(skill_a.id)
    assert response.json()["detail"]["portfolio_id"] == str(skill_b.id)


def test_non_personal_decision_inference_refused(client, owner_a):
    """T6: Inference without prediction_type='personal_decision' is refused."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    training_job, _ = _create_training_artifact(db, user.id)
    research, model_version = _create_model_version_with_training(db, user.id, training_job)

    inference = _create_inference_record(
        db, user.id, model_version.id, skill_unit.id,
        metadata_extra={"prediction_type": "research_backtest"}
    )
    db.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "rationale": "Test rationale",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(inference.id),
        },
    )
    _assert_refused(response, 400, "inference_not_model_execution")


def test_nonexistent_inference_refused(client, owner_a):
    """T7: Non-existent inference ID is refused (404)."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    db.commit()

    fake_inference_id = uuid.uuid4()
    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "rationale": "Test rationale",
            "expected_outcome": "Test outcome",
            "actor_type": "ai_draft",
            "linked_inference_id": str(fake_inference_id),
        },
    )
    _assert_refused(response, 404, "inference_not_found")


def test_decision_without_inference_link_still_works(client, owner_a):
    """T8: Decision can be created without linked_inference_id (backward compatible)."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    db.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "no_action",
            "actor_type": "human",
            "rationale": "Manual decision without inference link",
        },
    )
    assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"

    decision_id = uuid.UUID(response.json()["id"])
    decision = db.query(DecisionRecord).filter(DecisionRecord.id == decision_id).first()
    assert decision is not None
    assert decision.linked_inference_id is None


@pytest.mark.parametrize(
    "metadata,reason",
    [
        ([], "inference_metadata_unreadable"),
        ("personal_decision", "inference_metadata_unreadable"),
        (None, "inference_metadata_unreadable"),
        (_personal_metadata(), "inference_skill_mismatch"),
        (_personal_metadata(None), "inference_skill_mismatch"),
        (_personal_metadata(12345), "inference_skill_mismatch"),
        (_personal_metadata({"skill": "nested"}), "inference_skill_mismatch"),
    ],
)
def test_inference_that_cannot_name_its_skill_is_refused(client, owner_a, metadata, reason):
    """T9/T10: no attribution is never read as "every skill", and never crashes.

    The absent key, a JSON null, a number and an object are four different stored
    facts; all four must come back as the same named refusal rather than one 500
    and three silent passes.
    """
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    inference = _create_metadata_only_inference(db, user.id, metadata)
    db.commit()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "actor_type": "ai_draft",
            "rationale": "Would claim an inference with no usable attribution",
            "expected_outcome": "Positive return",
            "linked_inference_id": str(inference.id),
        },
    )
    _assert_refused(response, 400, reason)


def test_refused_lineage_writes_no_decision(client, owner_a):
    """T11: a refused lineage is a refusal, not a decision saved without it."""
    user, headers, db = owner_a

    skill_a = _create_skill_unit(db, user.id)
    skill_b = _create_skill_unit(db, user.id)
    inference = _create_metadata_only_inference(
        db, user.id, _personal_metadata(str(skill_a.id))
    )
    db.commit()

    before = db.query(DecisionRecord).count()
    response = client.post(
        f"/api/v1/paper-portfolio/{skill_b.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "actor_type": "ai_draft",
            "rationale": "Inference belongs to the other account",
            "expected_outcome": "Positive return",
            "linked_inference_id": str(inference.id),
        },
    )
    _assert_refused(response, 400, "inference_skill_mismatch")

    db.expire_all()
    assert db.query(DecisionRecord).count() == before, (
        "the refusal still created a decision row"
    )
    assert client.get(
        f"/api/v1/paper-portfolio/{skill_b.id}/decisions", headers=headers
    ).json() == []


def test_malformed_inference_id_is_refused_before_the_database(client, owner_a):
    """T12: a non-UUID lineage value is a schema refusal, not a 500."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    db.commit()
    before = db.query(DecisionRecord).count()

    response = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "actor_type": "ai_draft",
            "rationale": "Inference id is not an id",
            "expected_outcome": "Positive return",
            "linked_inference_id": "not-a-uuid",
        },
    )
    assert response.status_code == 422, response.text

    db.expire_all()
    assert db.query(DecisionRecord).count() == before


# ---------------------------------------------------------------------------
# T13-T19: the other lineage column, linked_experiment_version_id.
#
# ``experiment_versions`` has no owner column and no status column: ownership is
# the parent experiment's, and the state that makes a version runnable at all is
# the freeze stamp (``locked_at``), the same precondition the replay lane and the
# agent-strategy service already use. These tests exist because a precondition
# written against the columns that are not there answers every request -- good or
# bad -- with the same server error.
# ---------------------------------------------------------------------------


def _create_version_for_owner(db, owner_id, *, locked=True, title="Owned strategy"):
    """One experiment version, optionally frozen, belonging to ``owner_id``."""
    experiment = Experiment(
        owner_id=owner_id,
        title=title,
        hypothesis="Test",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked if locked else ExperimentStatus.draft,
    )
    db.add(experiment)
    db.flush()

    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={"test": True},
        locked_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
        if locked
        else None,
    )
    db.add(version)
    db.flush()
    return version


def _post_with_version(client, headers, portfolio_id, version_id, **extra):
    body = {
        "decision_type": "no_action",
        "actor_type": "human",
        "linked_experiment_version_id": str(version_id),
    }
    body.update(extra)
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers, json=body
    )


def test_decision_can_link_an_owned_frozen_version(client, owner_a):
    """T13: the version the caller names is stored and read back as itself."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    version = _create_version_for_owner(db, user.id)
    db.commit()

    response = _post_with_version(client, headers, skill_unit.id, version.id)
    assert response.status_code == 201, response.text
    assert response.json()["linked_experiment_version_id"] == str(version.id)

    decision = (
        db.query(DecisionRecord)
        .filter(DecisionRecord.id == uuid.UUID(response.json()["id"]))
        .first()
    )
    assert decision.linked_experiment_version_id == version.id


def test_unfrozen_experiment_version_is_not_usable(client, owner_a):
    """T14: a version the owner never locked cannot be a decision's origin."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    version = _create_version_for_owner(db, user.id, locked=False)
    db.commit()

    response = _post_with_version(client, headers, skill_unit.id, version.id)
    _assert_refused(response, 400, "version_not_usable")
    assert response.json()["detail"]["state"] == "unlocked", response.text


def test_another_owners_version_is_not_found(client, owner_a, owner_b):
    """T15: not yours is reported as absent, the same as no version at all."""
    user_a, headers_a, db_a = owner_a
    user_b, _headers_b, db_b = owner_b

    skill_unit = _create_skill_unit(db_a, user_a.id)
    foreign_version = _create_version_for_owner(db_b, user_b.id)
    db_a.commit()
    db_b.commit()

    response = _post_with_version(client, headers_a, skill_unit.id, foreign_version.id)
    _assert_refused(response, 404, "version_not_found")


def test_nonexistent_experiment_version_is_not_found(client, owner_a):
    """T16: a made-up version id is a refusal naming the missing row."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    db.commit()

    response = _post_with_version(client, headers, skill_unit.id, uuid.uuid4())
    _assert_refused(response, 404, "version_not_found")


def test_version_conflicting_with_the_portfolios_run_is_refused(client, owner_a):
    """T17: the account's own run already names a different strategy version.

    Both versions belong to the caller and both are frozen, so only the run
    binding can decide -- which is why the refusal echoes both ids.
    """
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    run_version = _create_version_for_owner(db, user.id, title="Version under run")
    claimed_version = _create_version_for_owner(db, user.id, title="Version claimed")
    db.add(
        PaperTradingRun(
            portfolio_id=skill_unit.id,
            skill="fisherman",
            instrument_id="US:AAPL",
            interval_minutes=1440,
            exit_config="none",
            start_date=datetime(2026, 1, 1),
            initial_capital=100000.0,
            strategy_version_id=run_version.id,
        )
    )
    db.commit()

    response = _post_with_version(client, headers, skill_unit.id, claimed_version.id)
    _assert_refused(response, 400, "version_mismatch")
    detail = response.json()["detail"]
    assert detail["run_version"] == str(run_version.id)
    assert detail["decision_version"] == str(claimed_version.id)


def test_refused_experiment_version_writes_no_decision(client, owner_a):
    """T18: a version refusal is a refusal, not a decision saved without it."""
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    version = _create_version_for_owner(db, user.id, locked=False)
    db.commit()

    before = db.query(DecisionRecord).count()
    response = _post_with_version(
        client,
        headers,
        skill_unit.id,
        version.id,
        decision_type="entry",
        actor_type="human",
        rationale="Should not be recorded at all",
        expected_outcome="Positive return",
    )
    _assert_refused(response, 400, "version_not_usable")

    db.expire_all()
    assert db.query(DecisionRecord).count() == before
    assert client.get(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions", headers=headers
    ).json() == []


def test_entry_needs_rationale_and_expected_outcome(client, owner_a):
    """T19: the two fields the journal cannot reconstruct later are required.

    Asserted on the reason the route actually gives, in both directions: a bare
    400 would also be returned by any other defect on the way in.
    """
    user, headers, db = owner_a

    skill_unit = _create_skill_unit(db, user.id)
    db.commit()
    before = db.query(DecisionRecord).count()

    missing_rationale = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "entry",
            "actor_type": "human",
            "expected_outcome": "Positive return",
        },
    )
    assert missing_rationale.status_code == 400, missing_rationale.text
    assert "ationale" in str(missing_rationale.json()["detail"])

    missing_outcome = client.post(
        f"/api/v1/paper-portfolio/{skill_unit.id}/decisions",
        headers=headers,
        json={
            "decision_type": "exit",
            "actor_type": "human",
            "rationale": "Position reached its target",
        },
    )
    assert missing_outcome.status_code == 400, missing_outcome.text
    assert "xpected outcome" in str(missing_outcome.json()["detail"])

    db.expire_all()
    assert db.query(DecisionRecord).count() == before


# ---------------------------------------------------------------------------
# T20-T21: the other door that binds an account to a strategy version.
#
# POST /paper-trading/create writes the run whose ``strategy_version_id`` T17
# compares a decision against, so the two doors have to refuse the same way or
# an account can be bound to a version its decisions are then told not to name.
# The refusals happen before the engine opens anything, which is also why they
# are testable here: what this lane cannot do is *complete* a create (the engine
# passes Decimal money values into raw SQL the SQLite driver refuses), so the
# proceeding case is proven by calling the shared validator directly, not by
# asserting a 200 that this lane cannot produce.
# ---------------------------------------------------------------------------


def _create_portfolio_with_version(client, headers, version_id):
    return client.post(
        "/api/v1/paper-trading/create",
        headers=headers,
        json={
            "skill": "fisherman",
            "instrument_id": "US:AAPL",
            "interval_minutes": 1440,
            "exit_config": "none",
            "initial_capital": 100000.0,
            "strategy_version_id": str(version_id),
        },
    )


def test_portfolio_run_binding_refuses_the_same_versions(client, owner_a, owner_b):
    """T20: unfrozen, foreign and absent versions are refused, and nothing is written."""
    user_a, headers_a, db_a = owner_a
    user_b, _headers_b, db_b = owner_b

    unfrozen = _create_version_for_owner(db_a, user_a.id, locked=False, title="Unfrozen")
    db_a.commit()
    foreign = _create_version_for_owner(db_b, user_b.id)
    db_b.commit()

    portfolios_before = db_a.query(PaperPortfolio).count()
    runs_before = db_a.query(PaperTradingRun).count()

    cases = [
        (unfrozen.id, 400, "version_not_usable"),
        (foreign.id, 404, "version_not_found"),
        (uuid.uuid4(), 404, "version_not_found"),
    ]
    for version_id, status_code, reason in cases:
        response = _create_portfolio_with_version(client, headers_a, version_id)
        assert response.status_code == status_code, (
            f"version {version_id} -> {response.status_code}: {response.text}"
        )
        detail = response.json()["detail"]
        assert isinstance(detail, dict), f"version {version_id}: {detail!r}"
        assert detail["reason"] == reason, f"version {version_id}: {detail!r}"

    db_a.expire_all()
    assert db_a.query(PaperPortfolio).count() == portfolios_before, (
        "a refused version binding still wrote a portfolio"
    )
    assert db_a.query(PaperTradingRun).count() == runs_before, (
        "a refused version binding still wrote a run"
    )


def test_the_shared_validator_admits_the_owners_own_frozen_version(owner_a):
    """T21: the precondition both doors share answers for the case it must admit.

    Called directly because the create route's success leg needs the money types
    this lane cannot bind; the same object the validator returns is what the
    route would keep using, so an over-strict rule would fail here rather than
    hide behind a skip.
    """
    user, _headers, db = owner_a

    version = _create_version_for_owner(db, user.id)
    db.commit()

    resolved = validate_strategy_version(
        db, version_id=version.id, owner_id=user.id
    )
    assert resolved.id == version.id
    assert resolved.locked_at is not None

