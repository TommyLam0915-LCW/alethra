"""The activation gate is a read of a recorded approval, not a constant.

``activation_status`` used to be written by the trainer as the literal
``"NOT_APPROVED"``, which meant two things could not be told apart: a gate that
says no because nothing was approved, and a column that can never say anything
else. These tests separate them. Every test here runs against the disposable
test database, so the approvals it records are harness fixtures, not the
owner's decision — the owner's approval can only come from the owner through
``PUT /api/v1/agent-strategy/training/jobs/{job_id}/activation``, and the last
class in this file is what that route does.

What the suite pins down:

* the default is ``NOT_APPROVED`` for every failure mode: no job, job not
  completed, no approval row, a non-``owner_ui`` provenance, an approval
  recorded by someone else or for someone else, a checksum or contract that
  does not match, an approval older than the artifact, withdrawn consent, and a
  stale policy version;
* one self-owned, owner-UI, artifact-bound approval flips the gate — a gate
  that could not flip would prove nothing;
* an approval binds exactly one artifact: it does not carry to the owner's
  other jobs, and revoking or withdrawing consent returns the column to
  ``NOT_APPROVED`` without deleting the audit row;
* ``movable_to_customer_environment`` stays false even when activated: release
  to a customer environment is a second judgment this gate does not make.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.conftest import TestSession
from app.lib.timeutil import utcnow
from app.ml.personal_trainer import PERSONAL_TASK_TYPE
from app.models.agent_strategy import (
    AgentDatasetSnapshot,
    AgentModelActivationApproval,
    AgentStrategyConsent,
    AgentStrategyTrainingJob,
)
from app.models.experiment import Experiment, ExperimentStatus, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.model_research import ModelResearchRecord, ModelType, ModelVersion
from app.models.personal_model_selection import (
    PersonalModelSelection,
    PersonalModelSelectionAction,
)
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.user import User
from app.services import model_activation
from app.services.model_activation import check_model_version_inference_authorization
from app.services.personal_training import PERSONAL_TRAINING_TYPE
from app.services.agent_strategy import (
    CONSENT_ACCEPTANCE_TEXT_VERSION,
    CONSENT_VERSION,
    AgentStrategyRefused,
    record_consent,
    revoke_consent,
    training_authorization,
)

ARTIFACT = "a" * 64
DATASET = "b" * 64
CONTRACT_VERSION = "agent-strategy-training-contract-v1"
# Always in the past: an approval is recorded with the current clock, and an
# approval older than the artifact must be refused.
COMPLETED_AT = datetime.now(timezone.utc) - timedelta(days=2)


def _checksum(seed: str) -> str:
    return seed[:64].ljust(64, "0")


def _new_user(db) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        email=f"act-{uuid.uuid4().hex[:10]}@example.test",
        password_hash="test-only",
        display_name="activation test",
    )
    db.add(user)
    db.flush()
    return user.id


def _locked_version_id(db, owner_id) -> uuid.UUID:
    experiment = Experiment(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Activation gate fixture",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
    )
    db.add(experiment)
    db.flush()
    version = ExperimentVersion(
        id=uuid.uuid4(),
        experiment_id=experiment.id,
        version_number=1,
        created_by=owner_id,
        strategy_spec={"template_type": "sma_crossover", "asset_ids": ["DEMO-ALPHA"]},
        locked_at=datetime.combine(datetime(2026, 11, 1).date(), datetime.min.time()),
    )
    db.add(version)
    db.flush()
    experiment.current_version_id = version.id
    return version.id


def _snapshot_id(db, owner_id, version_id, *, checksum=DATASET) -> uuid.UUID:
    snapshot = AgentDatasetSnapshot(
        id=uuid.uuid4(),
        owner_id=owner_id,
        strategy_version_id=version_id,
        consent_policy_version=CONSENT_VERSION,
        consent_status="active",
        status="ready",
        candidate_ids_json=[],
        rows_json=[],
        feature_names_json=[],
        label_definition_json={},
        exclusion_breakdown_json={},
        outcome_distribution_json={},
        split_manifest_json={},
        checksum=checksum,
    )
    db.add(snapshot)
    db.flush()
    return snapshot.id


def _job(db, owner_id, *, status="completed", artifact=ARTIFACT, dataset=DATASET,
         contract=CONTRACT_VERSION, completed_at=COMPLETED_AT, version_id=None):
    """A completed training row: the gate's input, written by nobody else."""
    version_id = version_id or _locked_version_id(db, owner_id)
    job = AgentStrategyTrainingJob(
        id=uuid.uuid4(),
        owner_id=owner_id,
        snapshot_id=_snapshot_id(db, owner_id, version_id, checksum=dataset),
        strategy_version_id=version_id,
        status=status,
        training_contract_version=contract,
        derived_label_version="agent-screening-label-v1",
        dataset_checksum=dataset,
        hyperparameters_json={},
        hyperparameters_hash=_checksum("hp"),
        row_counts_json={"train": 1},
        lineage_json={},
        artifact_path="/tmp/does-not-exist/model.joblib",
        artifact_checksum=artifact,
        activation_status="NOT_APPROVED",
        completed_at=completed_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _approval(db, job, owner_id, *, channel="owner_ui", recorder=None,
              approval_owner=None, artifact=None, dataset=None, contract=None,
              policy_version=None, approved_at=None, ack=None, decision="approved",
              consent_id=None):
    """Insert an approval row directly, bypassing the owner-UI route.

    This is how the non-qualifying provenances are produced: a row that exists
    but was not recorded by the owner through the authenticated route.
    """
    if consent_id is None:
        consent = db.query(AgentStrategyConsent).filter_by(
            owner_id=owner_id, status="active"
        ).first()
        consent_id = consent.id
    approval = AgentModelActivationApproval(
        id=uuid.uuid4(),
        owner_id=approval_owner or owner_id,
        approved_by_user_id=recorder or owner_id,
        job_id=job.id,
        strategy_version_id=job.strategy_version_id,
        artifact_checksum=artifact or job.artifact_checksum,
        dataset_checksum=dataset or job.dataset_checksum,
        training_contract_version=contract or job.training_contract_version,
        consent_record_id=consent_id,
        approval_policy_version=policy_version
        or model_activation.ACTIVATION_POLICY_VERSION,
        acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
        acknowledgement=ack or model_activation.ACTIVATION_ACK_TEXT_ZH,
        channel=channel,
        decision=decision,
        gate_snapshot_json={},
        approved_at=approved_at or (job.completed_at + timedelta(minutes=1)),
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


class Harness:
    """One owner, one active consent, one completed job — the gate's inputs."""

    def __init__(self, db):
        self.db = db
        self.owner_id = _new_user(db)
        consent = record_consent(
            db,
            self.owner_id,
            policy_version=CONSENT_VERSION,
            acceptance_text_version=CONSENT_ACCEPTANCE_TEXT_VERSION,
            acknowledgement=_consent_text(),
        )
        self.consent_id = uuid.UUID(consent["consent_id"])
        self.job = _job(db, self.owner_id)

    def approve_through_owner_ui(self):
        """The only write path that can activate: the owner's own decision."""
        return model_activation.record_activation_approval(
            self.db,
            self.owner_id,
            job_id=self.job.id,
            artifact_checksum=self.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
        )


def _consent_text() -> str:
    from app.services.agent_strategy import CONSENT_ACCEPTANCE_TEXT_ZH

    return CONSENT_ACCEPTANCE_TEXT_ZH


@pytest.fixture
def harness():
    with TestSession() as db:
        yield Harness(db)


# ---------------------------------------------------------------------------
# The closed state, and every way of failing to open it
# ---------------------------------------------------------------------------


class TestGateIsClosedByDefault:

    def test_no_approval_row_means_not_approved(self, harness):
        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "no_recorded_approval"
        assert state["approval_id"] is None
        assert state["bindings"] == {"approval_present": False}
        assert harness.job.activation_status == "NOT_APPROVED"

    def test_a_fresh_database_says_not_approved_without_a_job(self, harness):
        state = model_activation.activation_state(harness.db, None)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "no_job"
        report = model_activation.activation_report(harness.db, None)
        assert report["model_activation"] == "NOT_APPROVED"
        assert report["activation_available"] is False
        assert report["movable_to_customer_environment"] is False

    @pytest.mark.parametrize(
        "status",
        ["running", "failed", "refused"],
    )
    def test_an_unfinished_artifact_is_not_activatable(self, harness, status):
        job = _job(harness.db, harness.owner_id, status=status,
                   artifact=_checksum("unfinished"), completed_at=None)

        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db,
                harness.owner_id,
                job_id=job.id,
                artifact_checksum=job.artifact_checksum,
                policy_version=model_activation.ACTIVATION_POLICY_VERSION,
                acknowledgement_text_version=(
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            )
        assert exc.value.code == "agent_activation_job_not_completed"
        assert exc.value.details["job_status"] == status
        assert _approval_rows(harness.db) == []

    def test_an_artifact_without_a_checksum_binds_to_nothing(self, harness):
        job = _job(harness.db, harness.owner_id, artifact=None)

        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db,
                harness.owner_id,
                job_id=job.id,
                artifact_checksum="",
                policy_version=model_activation.ACTIVATION_POLICY_VERSION,
                acknowledgement_text_version=(
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            )
        assert exc.value.code == "agent_activation_artifact_missing"

    def test_the_column_is_a_read_not_an_authority(self, harness):
        """A hand-written APPROVED with no approval behind it is corrected."""
        harness.job.activation_status = "APPROVED"
        harness.db.commit()

        state = model_activation.refresh_job_activation(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert harness.job.activation_status == "NOT_APPROVED"


# ---------------------------------------------------------------------------
# Non-qualifying provenances
# ---------------------------------------------------------------------------


class TestNonQualifyingApprovals:

    def test_a_seeded_row_from_another_channel_never_qualifies(self, harness):
        _approval(harness.db, harness.job, harness.owner_id, channel="seed_script")

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_channel_not_owner_ui"
        assert state["bindings"]["channel_qualifies"] is False

    def test_an_approval_recorded_by_someone_else_does_not_activate(self, harness):
        other = _new_user(harness.db)
        _approval(harness.db, harness.job, harness.owner_id, recorder=other)

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_not_self_owned"
        assert state["bindings"]["self_owned"] is False

    def test_an_approval_for_a_different_owner_does_not_activate(self, harness):
        other = _new_user(harness.db)
        _approval(harness.db, harness.job, harness.owner_id, approval_owner=other,
                  consent_id=_other_consent_id(harness.db, other))

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_not_for_this_owner"

    def test_an_older_artifact_checksum_is_refused_by_the_gate(self, harness):
        _approval(harness.db, harness.job, harness.owner_id, artifact=_checksum("old"))

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_artifact_mismatch"

    def test_a_dataset_or_contract_mismatch_is_refused_by_the_gate(self, harness):
        wrong_dataset = _approval(harness.db, harness.job, harness.owner_id,
                                  dataset=_checksum("other-dataset"))
        assert (
            model_activation.activation_state(harness.db, harness.job)["reason_code"]
            == "approval_dataset_mismatch"
        )
        wrong_dataset.decision = "revoked"
        harness.db.commit()

        _approval(harness.db, harness.job, harness.owner_id, contract="contract-v0")
        assert (
            model_activation.activation_state(harness.db, harness.job)["reason_code"]
            == "approval_contract_mismatch"
        )

    def test_an_approval_dated_before_the_artifact_is_not_an_activation(
        self, harness
    ):
        _approval(harness.db, harness.job, harness.owner_id,
                  approved_at=harness.job.completed_at - timedelta(hours=1))

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_precedes_artifact"

    def test_a_stale_approval_policy_version_is_refused(self, harness):
        _approval(harness.db, harness.job, harness.owner_id,
                  policy_version="agent-model-activation-v0")

        state = model_activation.activation_state(harness.db, harness.job)

        assert state["reason_code"] == "approval_policy_version_mismatch"

    def test_withdrawn_consent_returns_the_gate_to_not_approved(self, harness):
        self_result = harness.approve_through_owner_ui()
        assert self_result["activation_status"] == model_activation.ACTIVATED

        revoke_consent(harness.db, harness.owner_id, reason="owner_changed_mind")

        state = model_activation.activation_state(harness.db, harness.job)
        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "owner_consent_not_active"
        assert (
            model_activation.refresh_job_activation(harness.db, harness.job)
            ["activation_status"]
            == model_activation.NOT_APPROVED
        )
        assert harness.job.activation_status == "NOT_APPROVED"


def _approval_rows(db):
    return db.query(AgentModelActivationApproval).all()


def _other_consent_id(db, owner_id):
    consent = AgentStrategyConsent(
        id=uuid.uuid4(),
        owner_id=owner_id,
        accepted_by_user_id=owner_id,
        policy_version=CONSENT_VERSION,
        acceptance_text_version=CONSENT_ACCEPTANCE_TEXT_VERSION,
        acknowledgement_json={"text_zh": "seeded"},
        status="active",
        accepted_at=datetime.now(timezone.utc),
    )
    db.add(consent)
    db.commit()
    return consent.id


# ---------------------------------------------------------------------------
# The one state that does flip it
# ---------------------------------------------------------------------------


class TestSelfOwnedApprovalFlipsTheGate:

    def test_owner_ui_approval_activates_this_artifact_and_no_other(self, harness):
        second = _job(harness.db, harness.owner_id, artifact=_checksum("second-run"),
                      dataset=_checksum("second-dataset"))

        result = harness.approve_through_owner_ui()

        assert result["activation_status"] == model_activation.ACTIVATED
        assert result["reason_code"] is None
        assert result["artifact_checksum"] == harness.job.artifact_checksum
        assert result["bindings"]["channel_qualifies"] is True
        assert result["bindings"]["self_owned"] is True
        assert result["bindings"]["consent_still_active"] is True
        assert result["movable_to_customer_environment"] is False

        stored = harness.db.get(AgentStrategyTrainingJob, harness.job.id)
        assert stored.activation_status == model_activation.ACTIVATED
        # The next run inherits nothing.
        assert (
            model_activation.activation_state(harness.db, second)["activation_status"]
            == model_activation.NOT_APPROVED
        )
        assert (
            model_activation.activation_state(harness.db, second)["reason_code"]
            == "no_recorded_approval"
        )

    def test_activation_still_says_the_customer_environment_is_not_released(
        self, harness
    ):
        harness.approve_through_owner_ui()

        report = model_activation.activation_report(harness.db, harness.job)

        assert report["model_activation"] == model_activation.ACTIVATED
        assert report["activation_available"] is True
        assert report["movable_to_customer_environment"] is False

    def test_owner_summary_counts_the_recorded_approval(self, harness):
        other_owner = _new_user(harness.db)
        _job(harness.db, other_owner, artifact=_checksum("someone-else"))

        before = model_activation.owner_activation_summary(harness.db, harness.owner_id)
        assert before == {
            "model_activation": model_activation.NOT_APPROVED,
            "completed_job_count": 1,
            "approved_job_count": 0,
            "approved_job_ids": [],
        }

        harness.approve_through_owner_ui()

        after = model_activation.owner_activation_summary(harness.db, harness.owner_id)
        assert after["model_activation"] == model_activation.ACTIVATED
        assert after["completed_job_count"] == 1
        assert after["approved_job_count"] == 1
        assert after["approved_job_ids"] == [str(harness.job.id)]

    def test_the_training_authorization_report_reads_the_gate_too(self, harness):
        settings = _dev_settings()

        before = training_authorization(harness.db, harness.owner_id, settings)
        assert before["model_activation"] == model_activation.NOT_APPROVED

        harness.approve_through_owner_ui()

        after = training_authorization(harness.db, harness.owner_id, settings)
        assert after["model_activation"] == model_activation.ACTIVATED
        assert after["approved_job_count"] == 1
        # An approved artifact authorises no new training on its own.
        assert after["authorization_mode"] == "test_only_fixture_and_owner_consent"
        assert after["movable_to_customer_environment"] is False

    def test_revocation_is_a_read_back_and_the_row_survives(self, harness):
        result = harness.approve_through_owner_ui()
        approval_id = uuid.UUID(result["approval_id"])

        revoked = model_activation.revoke_activation_approval(
            harness.db, harness.owner_id, approval_id=approval_id,
            reason="owner_no_longer_trusts_it",
        )

        assert revoked["revoked"] is True
        assert revoked["activation_status"] == model_activation.NOT_APPROVED
        row = harness.db.get(AgentModelActivationApproval, approval_id)
        assert row.decision == "revoked"
        assert row.revoked_at is not None
        assert row.revoke_reason == "owner_no_longer_trusts_it"
        assert harness.job.activation_status == "NOT_APPROVED"

        # A revoked row frees the slot: a later, considered decision binds.
        second = harness.approve_through_owner_ui()
        assert second["activation_status"] == model_activation.ACTIVATED
        assert second["approval_id"] != str(approval_id)

    def test_a_second_live_approval_for_the_same_artifact_is_refused(self, harness):
        harness.approve_through_owner_ui()

        with pytest.raises(AgentStrategyRefused) as exc:
            harness.approve_through_owner_ui()
        assert exc.value.code == "agent_activation_already_approved"
        assert len(_approval_rows(harness.db)) == 1


# ---------------------------------------------------------------------------
# What the owner-UI write path refuses to record
# ---------------------------------------------------------------------------


class TestOwnerUiWritePathRefusals:

    def test_acknowledgement_that_is_not_the_policy_text_is_not_persisted(
        self, harness
    ):
        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db,
                harness.owner_id,
                job_id=harness.job.id,
                artifact_checksum=harness.job.artifact_checksum,
                policy_version=model_activation.ACTIVATION_POLICY_VERSION,
                acknowledgement_text_version=(
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                acknowledgement="我同意（但我沒有讀過訓練契約）。",
            )
        assert exc.value.code == "agent_activation_acknowledgement_mismatch"
        assert exc.value.details["persisted"] is False
        assert _approval_rows(harness.db) == []

    def test_a_whitespace_only_difference_is_the_same_text(self, harness):
        """The echo is checked as content, so line wrapping cannot fake a no."""
        wrapped = "  ".join(model_activation.ACTIVATION_ACK_TEXT_ZH.split("\n"))

        result = model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=(
                model_activation.ACTIVATION_ACK_TEXT_VERSION
            ),
            acknowledgement=wrapped,
        )

        assert result["activation_status"] == model_activation.ACTIVATED

    @pytest.mark.parametrize(
        "field, value, code",
        [
            ("artifact_checksum", "c" * 64, "agent_activation_artifact_mismatch"),
            ("policy_version", "agent-model-activation-v9",
             "agent_activation_policy_version_mismatch"),
            ("acknowledgement_text_version", "activation-ack-zh-9",
             "agent_activation_text_version_mismatch"),
        ],
    )
    def test_mismatched_binding_is_refused(self, harness, field, value, code):
        kwargs = {
            "job_id": harness.job.id,
            "artifact_checksum": harness.job.artifact_checksum,
            "policy_version": model_activation.ACTIVATION_POLICY_VERSION,
            "acknowledgement_text_version": (
                model_activation.ACTIVATION_ACK_TEXT_VERSION
            ),
            "acknowledgement": model_activation.ACTIVATION_ACK_TEXT_ZH,
        }
        kwargs[field] = value

        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db, harness.owner_id, **kwargs
            )
        assert exc.value.code == code
        assert _approval_rows(harness.db) == []

    def test_no_consent_row_means_no_activation_even_for_the_owner(self, harness):
        stranger = _new_user(harness.db)
        job = _job(harness.db, stranger, artifact=_checksum("stranger"))

        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db,
                stranger,
                job_id=job.id,
                artifact_checksum=job.artifact_checksum,
                policy_version=model_activation.ACTIVATION_POLICY_VERSION,
                acknowledgement_text_version=(
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            )
        assert exc.value.code == "agent_activation_consent_not_active"
        assert _approval_rows(harness.db) == []

    def test_a_job_belonging_to_another_owner_is_not_found(self, harness):
        stranger = _new_user(harness.db)
        job = _job(harness.db, stranger, artifact=_checksum("stranger-job"))

        with pytest.raises(AgentStrategyRefused) as exc:
            model_activation.record_activation_approval(
                harness.db,
                harness.owner_id,
                job_id=job.id,
                artifact_checksum=job.artifact_checksum,
                policy_version=model_activation.ACTIVATION_POLICY_VERSION,
                acknowledgement_text_version=(
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            )
        assert exc.value.code == "agent_activation_job_not_found"
        assert _approval_rows(harness.db) == []

    def test_requirements_echo_the_bindings_the_owner_has_to_confirm(self, harness):
        requirements = model_activation.requirements(harness.job)

        assert requirements["artifact_checksum"] == harness.job.artifact_checksum
        assert requirements["dataset_checksum"] == harness.job.dataset_checksum
        assert requirements["training_contract_version"] == CONTRACT_VERSION
        assert requirements["approval_policy_version"] == (
            model_activation.ACTIVATION_POLICY_VERSION
        )
        assert requirements["acknowledgement_text_zh"] == (
            model_activation.ACTIVATION_ACK_TEXT_ZH
        )
        assert requirements["consent_status"] == "active"


def _dev_settings():
    from types import SimpleNamespace

    from app.services.agent_strategy import (
        AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION,
        AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION,
    )

    return SimpleNamespace(
        app_env="development",
        agent_strategy_screening_enabled=True,
        database_url=(
            os.environ.get("ALETHRA_TEST_DB")
            or os.environ.get("DATABASE_URL")
            or "sqlite:///./test.db"
        ),
        agent_strategy_test_snapshot_authorization=(
            AGENT_STRATEGY_TEST_SNAPSHOT_AUTHORIZATION
        ),
        agent_strategy_test_training_authorization=(
            AGENT_STRATEGY_TEST_TRAINING_AUTHORIZATION
        ),
        agent_strategy_owner_consent_mode=False,
    )


# ---------------------------------------------------------------------------
# The routes: authenticated owner only, and withdrawal always available
# ---------------------------------------------------------------------------


class TestActivationRoutes:

    def test_owner_ui_route_records_the_decision_and_capabilities_read_it(
        self, client, auth_headers, monkeypatch
    ):
        from app.api import agent_strategy as api

        monkeypatch.setattr(api, "get_settings", _dev_settings)
        job, consent_id = _http_seeded_job(client, auth_headers, monkeypatch)

        before = client.get(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
        )
        assert before.status_code == 200, before.text
        body = before.json()
        assert body["state"]["activation_status"] == "NOT_APPROVED"
        assert body["state"]["reason_code"] == "no_recorded_approval"
        assert body["requirements"]["artifact_checksum"] == job["artifact_checksum"]

        capabilities = client.get(
            "/api/v1/agent-strategy/capabilities", headers=auth_headers
        )
        assert capabilities.json()["training"]["model_activation"] == "NOT_APPROVED"
        assert capabilities.json()["training"]["activation_available"] is False

        approved = client.put(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
            json={
                "artifact_checksum": job["artifact_checksum"],
                "policy_version": model_activation.ACTIVATION_POLICY_VERSION,
                "acknowledgement_text_version": (
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                "acknowledgement": model_activation.ACTIVATION_ACK_TEXT_ZH,
            },
        )
        assert approved.status_code == 200, approved.text
        payload = approved.json()
        assert payload["activation_status"] == "APPROVED"
        assert payload["movable_to_customer_environment"] is False
        assert uuid.UUID(payload["approval_id"])

        after = client.get(
            "/api/v1/agent-strategy/capabilities", headers=auth_headers
        )
        assert after.json()["training"]["model_activation"] == "APPROVED"
        assert after.json()["training"]["activation_available"] is True
        assert after.json()["training"]["approved_job_count"] == 1
        assert after.json()["training"]["completed_job_count"] == 1

        revoked = client.post(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation/revoke",
            headers=auth_headers,
            json={"reason": "owner_revoked_through_ui"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        assert revoked.json()["activation_status"] == "NOT_APPROVED"

        final = client.get(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
        )
        assert final.json()["state"]["activation_status"] == "NOT_APPROVED"
        assert final.json()["state"]["reason_code"] == "no_recorded_approval"
        assert consent_id is not None

    def test_write_route_is_closed_when_screening_is_disabled_but_revoke_is_not(
        self, client, auth_headers, monkeypatch
    ):
        from types import SimpleNamespace

        from app.api import agent_strategy as api

        job, _ = _http_seeded_job(client, auth_headers, monkeypatch)
        body = {
            "artifact_checksum": job["artifact_checksum"],
            "policy_version": model_activation.ACTIVATION_POLICY_VERSION,
            "acknowledgement_text_version": (
                model_activation.ACTIVATION_ACK_TEXT_VERSION
            ),
            "acknowledgement": model_activation.ACTIVATION_ACK_TEXT_ZH,
        }
        disabled = SimpleNamespace(agent_strategy_screening_enabled=False)
        monkeypatch.setattr(api, "get_settings", lambda: disabled)

        refused = client.put(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
            json=body,
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "agent_strategy_screening_disabled"
        with TestSession() as db:
            assert _approval_rows(db) == []

        # Turning the write path off must not trap an activation already on, so
        # the revoke route is deliberately outside the screening gate.
        monkeypatch.setattr(api, "get_settings", _dev_settings)
        assert client.put(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
            json=body,
        ).status_code == 200
        monkeypatch.setattr(api, "get_settings", lambda: disabled)

        revoked = client.post(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation/revoke",
            headers=auth_headers,
            json={"reason": "owner_revoked_with_writes_disabled"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["activation_status"] == "NOT_APPROVED"

    def test_route_refuses_a_foreign_job_id(self, client, auth_headers, monkeypatch):
        from app.api import agent_strategy as api

        monkeypatch.setattr(api, "get_settings", _dev_settings)
        unknown = uuid.uuid4()

        resp = client.put(
            f"/api/v1/agent-strategy/training/jobs/{unknown}/activation",
            headers=auth_headers,
            json={
                "artifact_checksum": "d" * 64,
                "policy_version": model_activation.ACTIVATION_POLICY_VERSION,
                "acknowledgement_text_version": (
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                "acknowledgement": model_activation.ACTIVATION_ACK_TEXT_ZH,
            },
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "agent_activation_job_not_found"

    def test_route_refuses_a_mismatched_acknowledgement_over_http(
        self, client, auth_headers, monkeypatch
    ):
        from app.api import agent_strategy as api

        monkeypatch.setattr(api, "get_settings", _dev_settings)
        job, _ = _http_seeded_job(client, auth_headers, monkeypatch)

        resp = client.put(
            f"/api/v1/agent-strategy/training/jobs/{job['job_id']}/activation",
            headers=auth_headers,
            json={
                "artifact_checksum": job["artifact_checksum"],
                "policy_version": model_activation.ACTIVATION_POLICY_VERSION,
                "acknowledgement_text_version": (
                    model_activation.ACTIVATION_ACK_TEXT_VERSION
                ),
                "acknowledgement": "我同意",
            },
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == (
            "agent_activation_acknowledgement_mismatch"
        )
        with TestSession() as db:
            assert _approval_rows(db) == []


def _http_seeded_job(client, headers, monkeypatch):
    """A completed job + active consent for the authenticated test owner.

    Seeded at the service layer: the HTTP chain that produces a real artifact is
    covered by ``test_agent_strategy_slice.py`` and needs the ML stack. The gate
    only reads rows, so the row is enough here.
    """
    from app.models.user import User

    with TestSession() as db:
        owner_id = db.query(User).filter(User.email == "test@alethra.io").one().id
        consent = record_consent(
            db,
            owner_id,
            policy_version=CONSENT_VERSION,
            acceptance_text_version=CONSENT_ACCEPTANCE_TEXT_VERSION,
            acknowledgement=_consent_text(),
        )
        job = _job(db, owner_id)
        return {
            "job_id": str(job.id),
            "artifact_checksum": job.artifact_checksum,
        }, consent["consent_id"]


# ---------------------------------------------------------------------------
# Unified skill model: skill_unit_id scoping
# ---------------------------------------------------------------------------


class TestSkillUnitScoping:
    """Activation approvals are scoped to a specific skill unit (portfolio).

    An approval with NULL skill_unit_id does NOT authorize all skill units — it
    only covers the artifact in isolation. An approval with a specific
    skill_unit_id covers only that skill unit. This prevents old NULL-scope
    approvals from being misinterpreted as "all skills" authorization.
    """

    def test_approval_with_skill_unit_id_covers_that_skill_unit(self, harness):
        skill_unit_id = uuid.uuid4()

        result = model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_id,
        )

        assert result["activation_status"] == model_activation.ACTIVATED
        assert result["skill_unit_id"] == str(skill_unit_id)
        assert result["skill_unit_scope"] == "specific"

        # Verify the approval covers this skill unit.
        approval = model_activation.live_approval(harness.db, harness.job.id)
        assert model_activation.approval_covers_skill_unit(approval, skill_unit_id)
        # But not a different skill unit.
        other_skill_unit = uuid.uuid4()
        assert not model_activation.approval_covers_skill_unit(approval, other_skill_unit)

    def test_approval_without_skill_unit_id_does_not_cover_any_skill_unit(self, harness):
        result = model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=None,
        )

        assert result["activation_status"] == model_activation.ACTIVATED
        assert result["skill_unit_id"] is None
        assert result["skill_unit_scope"] == "none"

        # Verify the approval does NOT cover any skill unit.
        approval = model_activation.live_approval(harness.db, harness.job.id)
        some_skill_unit = uuid.uuid4()
        assert not model_activation.approval_covers_skill_unit(approval, some_skill_unit)
        # It only covers NULL (artifact in isolation).
        assert model_activation.approval_covers_skill_unit(approval, None)

    def test_inference_time_check_verifies_skill_unit_match(self, harness):
        skill_unit_id = uuid.uuid4()

        model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_id,
        )

        # Inference for the same skill unit: authorized.
        check = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, harness.job.id, skill_unit_id
        )
        assert check["authorized"] is True
        assert check["skill_unit_match"] is True
        assert check["approval_skill_unit_id"] == str(skill_unit_id)

        # Inference for a different skill unit: refused.
        other_skill_unit = uuid.uuid4()
        check = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, harness.job.id, other_skill_unit
        )
        assert check["authorized"] is False
        assert check["reason_code"] == "skill_unit_mismatch"
        assert check["requested_skill_unit_id"] == str(other_skill_unit)

        # Inference with NULL skill unit: refused (approval is scoped).
        check = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, harness.job.id, None
        )
        assert check["authorized"] is False
        assert check["reason_code"] == "skill_unit_mismatch"

    def test_inference_with_unscoped_approval_is_refused(self, harness):
        model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=None,
        )

        # Inference for any skill unit: refused (approval has no skill-unit scope).
        some_skill_unit = uuid.uuid4()
        check = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, harness.job.id, some_skill_unit
        )
        assert check["authorized"] is False
        assert check["reason_code"] == "skill_unit_mismatch"

    def test_list_activations_by_skill_unit_filters_correctly(self, harness):
        skill_unit_a = uuid.uuid4()
        skill_unit_b = uuid.uuid4()

        # Create two jobs and approve each for a different skill unit.
        job_a = _job(harness.db, harness.owner_id, artifact=_checksum("job-a"))
        job_b = _job(harness.db, harness.owner_id, artifact=_checksum("job-b"))

        model_activation.record_activation_approval(
            harness.db, harness.owner_id, job_id=job_a.id,
            artifact_checksum=job_a.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_a,
        )
        model_activation.record_activation_approval(
            harness.db, harness.owner_id, job_id=job_b.id,
            artifact_checksum=job_b.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_b,
        )

        # Query for skill_unit_a: only job_a's approval.
        approvals_a = model_activation.list_activations_by_skill_unit(
            harness.db, harness.owner_id, skill_unit_a
        )
        assert len(approvals_a) == 1
        assert approvals_a[0]["skill_unit_id"] == str(skill_unit_a)

        # Query for skill_unit_b: only job_b's approval.
        approvals_b = model_activation.list_activations_by_skill_unit(
            harness.db, harness.owner_id, skill_unit_b
        )
        assert len(approvals_b) == 1
        assert approvals_b[0]["skill_unit_id"] == str(skill_unit_b)

        # Query for a third skill unit: no approvals.
        approvals_c = model_activation.list_activations_by_skill_unit(
            harness.db, harness.owner_id, uuid.uuid4()
        )
        assert len(approvals_c) == 0

    def test_revoke_by_skill_unit_only_affects_that_scope(self, harness):
        skill_unit_a = uuid.uuid4()
        skill_unit_b = uuid.uuid4()

        job_a = _job(harness.db, harness.owner_id, artifact=_checksum("job-a"))
        job_b = _job(harness.db, harness.owner_id, artifact=_checksum("job-b"))

        model_activation.record_activation_approval(
            harness.db, harness.owner_id, job_id=job_a.id,
            artifact_checksum=job_a.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_a,
        )
        model_activation.record_activation_approval(
            harness.db, harness.owner_id, job_id=job_b.id,
            artifact_checksum=job_b.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_b,
        )

        # Revoke all approvals for skill_unit_a.
        result = model_activation.revoke_activations_for_skill_unit(
            harness.db, harness.owner_id, skill_unit_a, reason="testing"
        )
        assert result["revoked"] is True
        assert result["revoked_count"] == 1
        assert result["skill_unit_id"] == str(skill_unit_a)

        # Verify job_a's approval is revoked.
        check_a = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, job_a.id, skill_unit_a
        )
        assert check_a["authorized"] is False
        assert check_a["reason_code"] == "no_approval"

        # Verify job_b's approval is still active (different skill unit).
        check_b = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, job_b.id, skill_unit_b
        )
        assert check_b["authorized"] is True
        assert check_b["skill_unit_match"] is True

    def test_old_null_scope_approvals_are_not_all_skills(self, harness):
        """An approval recorded before skill_unit_id existed does NOT authorize
        all skill units — it only covers the artifact in isolation.

        This is the critical backward-compatibility test: old approvals with
        NULL skill_unit_id must not be misinterpreted as "all skills"
        authorization.
        """
        # Simulate an old approval by inserting a row directly with NULL skill_unit_id.
        _approval(harness.db, harness.job, harness.owner_id)

        # Verify the approval exists and has NULL skill_unit_id.
        approval = model_activation.live_approval(harness.db, harness.job.id)
        assert approval is not None
        assert approval.skill_unit_id is None

        # Verify it does NOT cover any skill unit.
        some_skill_unit = uuid.uuid4()
        assert not model_activation.approval_covers_skill_unit(approval, some_skill_unit)

        # Verify inference-time check refuses for any skill unit.
        check = model_activation.check_skill_unit_activation(
            harness.db, harness.owner_id, harness.job.id, some_skill_unit
        )
        assert check["authorized"] is False
        assert check["reason_code"] == "skill_unit_mismatch"

        # It only covers NULL (artifact in isolation).
        assert model_activation.approval_covers_skill_unit(approval, None)

    def test_activation_state_reports_skill_unit_scope(self, harness):
        skill_unit_id = uuid.uuid4()

        model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=skill_unit_id,
        )

        state = model_activation.activation_state(harness.db, harness.job)
        assert state["activation_status"] == model_activation.ACTIVATED
        assert state["skill_unit_id"] == str(skill_unit_id)
        assert state["skill_unit_scope"] == "specific"

    def test_approval_without_skill_unit_reports_none_scope(self, harness):
        model_activation.record_activation_approval(
            harness.db,
            harness.owner_id,
            job_id=harness.job.id,
            artifact_checksum=harness.job.artifact_checksum,
            policy_version=model_activation.ACTIVATION_POLICY_VERSION,
            acknowledgement_text_version=model_activation.ACTIVATION_ACK_TEXT_VERSION,
            acknowledgement=model_activation.ACTIVATION_ACK_TEXT_ZH,
            skill_unit_id=None,
        )

        state = model_activation.activation_state(harness.db, harness.job)
        assert state["activation_status"] == model_activation.ACTIVATED
        assert state["skill_unit_id"] is None
        assert state["skill_unit_scope"] == "none"


class TestModelVersionInferenceAuthorization:
    """Personal-model inference gate: selection + the personal training job.

    The personal family is served by ``training_jobs`` (written by
    ``app.services.personal_training``) and ``personal_model_selections``. It is
    *not* served by ``agent_strategy_training_jobs`` or
    ``AgentModelActivationApproval``: those belong to the agent-strategy
    screening model, which has its own trainer, its own job table, its own
    artifact type and its own consent scope. An earlier version of this gate
    demanded an agent approval for a personal version, which refused legitimate
    personal inference and, worse, would have honored an approval recorded for a
    different model family.

    These tests therefore hold two things at once: the personal gate still
    refuses every way of not having earned inference, and it refuses to read the
    agent table either way.
    """

    CHECKSUM = "a" * 64

    def _personal_job(self, harness, *, status=TrainingJobStatus.completed,
                      checksum=None, owner_id=None):
        job = TrainingJob(
            id=uuid.uuid4(),
            user_id=owner_id or harness.owner_id,
            status=status,
            parameter_grid={"training_type": PERSONAL_TRAINING_TYPE},
            results={"artifact_sha256": checksum or self.CHECKSUM},
            completed_at=utcnow(),
        )
        harness.db.add(job)
        harness.db.commit()
        return job

    def _version(self, harness, spec):
        record = ModelResearchRecord(
            owner_id=harness.owner_id,
            title="Personal record",
            purpose="personal_decision",
        )
        harness.db.add(record)
        harness.db.flush()
        version = ModelVersion(
            model_research_record_id=record.id,
            version_number=1,
            model_type=ModelType.personal_decision_model,
            model_specification_json=spec,
        )
        harness.db.add(version)
        harness.db.flush()
        return record, version

    def _select(self, harness, record, version, skill_unit_id):
        harness.db.add(
            PersonalModelSelection(
                owner_id=harness.owner_id,
                skill_unit_id=skill_unit_id,
                record_id=record.id,
                version_id=version.id,
                action=PersonalModelSelectionAction.select,
                rationale="Test selection",
            )
        )
        harness.db.commit()

    def _spec(self, job, checksum=None):
        return {
            "task_type": PERSONAL_TASK_TYPE,
            "artifact_id": "personal-artifact",
            "artifact_sha256": checksum or self.CHECKSUM,
            "training_job_id": str(job.id),
        }

    def test_inference_refused_when_no_training_job_in_spec(self, harness):
        """A version that names no training job has no provable source."""
        record, version = self._version(
            harness,
            {
                "task_type": PERSONAL_TASK_TYPE,
                "artifact_id": "test-artifact",
                "artifact_sha256": self.CHECKSUM,
            },
        )
        harness.db.commit()

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, uuid.uuid4(),
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "no_training_job"

    def test_inference_refused_when_training_job_id_malformed(self, harness):
        """A non-UUID job reference is refused, not coerced or ignored."""
        record, version = self._version(
            harness, self._spec(SimpleNamespace(id="not-a-uuid"))
        )
        harness.db.commit()

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, uuid.uuid4(),
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "invalid_training_job_id"

    def test_inference_refused_when_no_skill_unit_selection(self, harness):
        """A completed job without a selection for THIS skill unit is refused."""
        job = self._personal_job(harness)
        record, version = self._version(harness, self._spec(job))
        harness.db.commit()

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, uuid.uuid4(),
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "no_skill_unit_selection"

    def test_inference_refused_when_job_belongs_to_another_owner(self, harness):
        """A personal job is not a shared credential."""
        stranger = _new_user(harness.db)
        job = self._personal_job(harness, owner_id=stranger)
        record, version = self._version(harness, self._spec(job))
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "training_job_not_found"

    def test_inference_refused_when_job_not_completed(self, harness):
        """A running job has produced nothing that may be served."""
        job = self._personal_job(harness, status=TrainingJobStatus.running)
        record, version = self._version(harness, self._spec(job))
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "training_job_not_completed"
        assert result["job_status"] == "running"

    def test_inference_refused_on_artifact_checksum_mismatch(self, harness):
        """The version must carry the artifact the job actually recorded."""
        job = self._personal_job(harness)
        record, version = self._version(harness, self._spec(job, checksum="f" * 64))
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "artifact_mismatch"

    def test_inference_refused_for_a_different_skill_unit(self, harness):
        """A selection for skill A never authorizes skill B."""
        job = self._personal_job(harness)
        record, version = self._version(harness, self._spec(job))
        skill_unit_a = uuid.uuid4()
        skill_unit_b = uuid.uuid4()
        self._select(harness, record, version, skill_unit_a)

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_b,
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "no_skill_unit_selection"
        assert result["skill_unit_id"] == str(skill_unit_b)

    def test_inference_allowed_for_the_selected_skill_unit(self, harness):
        """Selection plus a completed, owned, checksum-matching job is enough."""
        job = self._personal_job(harness)
        record, version = self._version(harness, self._spec(job))
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is True, result
        assert result["reason_code"] is None
        assert result["training_job_id"] == str(job.id)
        assert result["skill_unit_id"] == str(skill_unit_id)
        # No agent approval was recorded anywhere on this path.
        assert harness.db.query(AgentModelActivationApproval).count() == 0

    def test_agent_activation_row_does_not_authorize_personal_inference(self, harness):
        """Cross-family isolation, positive direction.

        An owner-UI approval for an *agent-strategy* job says nothing about a
        personal version. Even with a selection present and the agent job fully
        approved, the personal gate must refuse, because the job id it is handed
        names no personal training job.
        """
        harness.approve_through_owner_ui()
        record, version = self._version(
            harness, self._spec(harness.job)  # agent job id inside a personal spec
        )
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        assert harness.db.query(AgentModelActivationApproval).count() == 1

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is False
        assert result["reason_code"] == "training_job_not_found"

    def test_missing_agent_approval_does_not_block_personal_inference(self, harness):
        """Cross-family isolation, negative direction — the regression.

        Before the fix this scenario returned ``no_approval``: a legitimate
        personal version was refused because the agent-strategy table had no
        row for it. The personal gate must not consult that table at all.
        """
        job = self._personal_job(harness)
        record, version = self._version(harness, self._spec(job))
        skill_unit_id = uuid.uuid4()
        self._select(harness, record, version, skill_unit_id)

        assert model_activation.live_approval(harness.db, job.id) is None

        result = check_model_version_inference_authorization(
            harness.db, harness.owner_id, version.id,
            version.model_specification_json, skill_unit_id,
        )
        assert result["authorized"] is True, result
        assert "approval_id" not in result


# ---------------------------------------------------------------------------
# The agent-strategy family keeps its own gate
# ---------------------------------------------------------------------------


class TestAgentFamilyGateStillEnforced:
    """The personal fix must not have softened the agent path it belongs to."""

    def test_agent_activation_still_requires_recorded_approval(self, harness):
        state = model_activation.activation_state(harness.db, harness.job)
        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "no_recorded_approval"

    def test_agent_activation_still_binds_artifact_dataset_and_contract(self, harness):
        _approval(harness.db, harness.job, harness.owner_id, artifact="b" * 64)
        state = model_activation.activation_state(harness.db, harness.job)
        assert state["activation_status"] == model_activation.NOT_APPROVED
        assert state["reason_code"] == "approval_artifact_mismatch"

    def test_agent_skill_unit_scope_still_refuses_a_borrowed_skill(self, harness):
        approval = harness.approve_through_owner_ui()["approval_id"]
        row = harness.db.get(AgentModelActivationApproval, uuid.UUID(approval))
        row.skill_unit_id = uuid.uuid4()
        harness.db.commit()

        other_skill = uuid.uuid4()
        assert model_activation.approval_covers_skill_unit(row, other_skill) is False
        # A NULL scope is "no skill unit", never "every skill unit".
        row.skill_unit_id = None
        harness.db.commit()
        assert model_activation.approval_covers_skill_unit(row, other_skill) is False
