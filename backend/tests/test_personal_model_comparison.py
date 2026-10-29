"""Personal model comparison and explicit selection.

Every bar, decision, label and artifact in this module is a SYNTHETIC FIXTURE
produced inside the test. The tests prove the comparison and selection
contracts (identity binding, refusal, quota, history) — not that any model is
effective, that decision-mimicry scores predict returns, or that a real owner
would prefer one model. The small-dataset case builds its frozen snapshot
directly so the small-sample warning can be exercised below the product's
200-decision snapshot minimum; it still trains with the real trainer.
"""
import uuid

import pytest

from tests.conftest import TestSession
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    train_personal_model,
)
from app.models.market_data import MarketDataMetadata
from app.models.model_research import (
    EvaluationType,
    ModelEvaluation,
    ModelResearchRecord,
    ModelStatus,
    ModelType,
    ModelVersion,
)
from app.models.personal_dataset import (
    PersonalizationDatasetSnapshot,
    PersonalizationSnapshotStatus,
)
from app.models.personal_model_selection import (
    PersonalModelComparisonRun,
    PersonalModelSelection,
)
from app.services.personal_dataset import (
    FEATURE_SCHEMA_VERSION,
    LABEL_DEFINITION,
    _canonical_checksum,
)
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _artifact_sha256,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _personal_artifacts,
    _quota_state,
    _run_worker_job,
    _second_user_headers,
    _seed_bars,
    _seed_confirmed_decisions,
    _user_id,
)


@pytest.fixture(autouse=True)
def _synthetic_market(setup_db):
    """The same deterministic synthetic OHLCV series used by the chain tests."""
    db = TestSession()
    try:
        _seed_bars(db)
        db.add(
            MarketDataMetadata(
                instrument_id=INSTRUMENT,
                provider_id="synthetic",
                data_nature="synthetic",
                first_bar_date=BARS[0],
                last_bar_date=BARS[-1],
                total_bars=len(BARS),
            )
        )
        db.commit()
    finally:
        db.close()
    yield


def _start_training(client, headers, snapshot_id, model_type):
    resp = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=headers,
        json={"snapshot_id": snapshot_id, "model_type": model_type},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    _run_worker_job(uuid.UUID(job_id))
    return job_id


def _model_for_job(client, headers, job_id, strategy_version_id):
    models = client.get(
        "/api/v1/ml/personal/models",
        headers=headers,
        params={"strategy_version_id": strategy_version_id},
    ).json()["models"]
    matches = [model for model in models if model["training_job_id"] == job_id]
    assert len(matches) == 1, models
    return matches[0]


@pytest.fixture()
def pair(client, auth_headers, tmp_path, monkeypatch):
    """One strategy, one frozen snapshot, two real artifacts (LR + baseline)."""
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)
    artifacts = _personal_artifacts(tmp_path, monkeypatch)

    job_a = _start_training(client, auth_headers, snapshot["snapshot_id"], "logistic_regression")
    job_b = _start_training(client, auth_headers, snapshot["snapshot_id"], "baseline")
    model_a = _model_for_job(client, auth_headers, job_a, version_id)
    model_b = _model_for_job(client, auth_headers, job_b, version_id)

    assert model_a["artifact_id"] != model_b["artifact_id"]
    return {
        "owner_id": owner_id,
        "strategy_version_id": version_id,
        "snapshot": snapshot,
        "record_id": model_a["record_id"],
        "model_a": model_a,
        "model_b": model_b,
        "artifacts": artifacts,
    }


def _comparison_body(pair, operation_key=None):
    body = {
        "version_a_id": pair["model_a"]["model_version_id"],
        "version_b_id": pair["model_b"]["model_version_id"],
    }
    if operation_key:
        body["operation_key"] = operation_key
    return body


# ---------------------------------------------------------------------------
# 1. Comparability is a free, read-only report.
# ---------------------------------------------------------------------------


def test_comparability_reports_same_evaluation_set_without_spending_quota(
    pair, client, auth_headers
):
    before = _quota_state(pair["owner_id"])["used"]
    resp = client.get(
        "/api/v1/ml/personal/models/comparability",
        headers=auth_headers,
        params={
            "version_a": pair["model_a"]["model_version_id"],
            "version_b": pair["model_b"]["model_version_id"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["comparable"] is True
    assert body["same_evaluation_set"] is True
    assert body["not_comparable_reasons"] == []
    codes = {check["code"]: check for check in body["checks"]}
    for required in (
        "personal_decision_model",
        "same_task",
        "same_classes",
        "same_feature_schema",
        "same_strategy_scope",
        "same_evaluation_method",
        "same_label_contract",
        "snapshots_available",
        "artifacts_available",
    ):
        assert codes[required]["ok"] is True, required
        assert codes[required]["required"] is True
    assert codes["same_evaluation_set"]["required"] is False
    assert body["quota"] == {"quota_type": "ai_usage", "amount": 0}
    assert _quota_state(pair["owner_id"])["used"] == before


# ---------------------------------------------------------------------------
# 2. Persisted evaluations are bound to the artifact bytes and snapshot.
# ---------------------------------------------------------------------------


def test_comparison_metrics_are_bound_to_artifact_and_snapshot(
    pair, client, auth_headers
):
    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key="cmp-identity-0001"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "same_evaluation_set"
    assert body["rankable"] is True
    assert body["delta"] is not None
    assert body["disagreement"]["n_rows"] == body["versions"]["a"]["metrics"]["test_size"]
    assert body["not_for"] == ["市場方向預測", "交易是否正確或有利潤", "投資建議"]

    for role in ("a", "b"):
        version_payload = body["versions"][role]
        model = pair["model_a"] if role == "a" else pair["model_b"]
        digest = _artifact_sha256(pair["artifacts"], model["artifact_id"])
        assert version_payload["artifact_sha256"] == digest
        assert version_payload["artifact_verified"] is True
        assert (
            version_payload["snapshot_checksum"]
            == pair["snapshot"]["checksum"]
        )
        assert version_payload["metrics"]["test_size"] > 0
        assert version_payload["evaluation_set"]["test_start_event_id"]
        assert version_payload["evaluation_set"]["test_end_event_id"]

    db = TestSession()
    try:
        evaluations = (
            db.query(ModelEvaluation)
            .filter(
                ModelEvaluation.evaluation_type == EvaluationType.model_comparison
            )
            .all()
        )
        assert len(evaluations) == 2
        for evaluation in evaluations:
            identity = (evaluation.metrics_json or {})["identity"]
            assert identity["created_by"] == str(pair["owner_id"])
            assert identity["snapshot_checksum"] == pair["snapshot"]["checksum"]
            assert identity["artifact_sha256"] in {
                body["versions"]["a"]["artifact_sha256"],
                body["versions"]["b"]["artifact_sha256"],
            }
            assert identity["feature_names"]
            assert identity["split"]["kind"] == "chronological_holdout"
            assert identity["classes"] == sorted(pair["model_a"]["classes"])
            assert evaluation.engine_version == "personal-comparison-v1"
            assert evaluation.data_cutoff_at
            assert evaluation.evaluator.value == "system"
            assert str(evaluation.id) in body["evaluation_record_ids"].values()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. One run costs one ai_usage unit; an operation key makes a retry free.
# ---------------------------------------------------------------------------


def test_comparison_quota_and_idempotent_retry(pair, client, auth_headers):
    before = _quota_state(pair["owner_id"])["used"]
    key = "cmp-retry-0001"
    first = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key=key),
    )
    assert first.status_code == 200, first.text
    assert first.json().get("idempotent_replay") is None
    after_first = _quota_state(pair["owner_id"])["used"]
    assert after_first == before + 1

    replay = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key=key),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["comparison_run_id"] == first.json()["comparison_run_id"]
    assert _quota_state(pair["owner_id"])["used"] == after_first

    conflict = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json={
            "version_a_id": pair["model_b"]["model_version_id"],
            "version_b_id": pair["model_a"]["model_version_id"],
            "operation_key": key,
        },
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "operation_key_conflict"
    assert _quota_state(pair["owner_id"])["used"] == after_first

    fresh = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json={
            "version_a_id": pair["model_b"]["model_version_id"],
            "version_b_id": pair["model_a"]["model_version_id"],
            "operation_key": "cmp-retry-0002",
        },
    )
    assert fresh.status_code == 200, fresh.text
    assert _quota_state(pair["owner_id"])["used"] == after_first + 1


# ---------------------------------------------------------------------------
# 4. Incompatible versions are refused with reasons, before any charge.
# ---------------------------------------------------------------------------


def test_incompatible_versions_are_refused_with_reasons(pair, client, auth_headers):
    db = TestSession()
    try:
        version_b = (
            db.query(ModelVersion)
            .filter(
                ModelVersion.id == uuid.UUID(pair["model_b"]["model_version_id"])
            )
            .one()
        )
        version_b.model_specification_json = {
            **(version_b.model_specification_json or {}),
            "strategy_version_id": str(uuid.uuid4()),
        }
        db.commit()
    finally:
        db.close()

    before = _quota_state(pair["owner_id"])["used"]
    report = client.get(
        "/api/v1/ml/personal/models/comparability",
        headers=auth_headers,
        params={
            "version_a": pair["model_a"]["model_version_id"],
            "version_b": pair["model_b"]["model_version_id"],
        },
    ).json()
    assert report["comparable"] is False
    assert "same_strategy_scope" in {
        reason["code"] for reason in report["not_comparable_reasons"]
    }

    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair),
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "not_comparable"
    assert "same_strategy_scope" in {reason["code"] for reason in detail["reasons"]}
    assert _quota_state(pair["owner_id"])["used"] == before

    db = TestSession()
    try:
        assert db.query(PersonalModelComparisonRun).count() == 0
        assert (
            db.query(ModelEvaluation)
            .filter(ModelEvaluation.evaluation_type == EvaluationType.model_comparison)
            .count()
            == 0
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Cross-owner access is a 404 on every read and write.
# ---------------------------------------------------------------------------


def test_cross_owner_is_refused(pair, client, auth_headers):
    other = _second_user_headers(client)
    params = {
        "version_a": pair["model_a"]["model_version_id"],
        "version_b": pair["model_b"]["model_version_id"],
    }
    assert (
        client.get(
            "/api/v1/ml/personal/models/comparability", headers=other, params=params
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/ml/personal/models/comparisons",
            headers=other,
            json=_comparison_body(pair, operation_key="other-owner-0001"),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{pair['model_a']['model_version_id']}/select",
            headers=other,
            json={"rationale": "other owner attempt"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/ml/personal/records/{pair['record_id']}/rollback",
            headers=other,
            json={"rationale": "other owner attempt"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/ml/personal/records/{pair['record_id']}/selections",
            headers=other,
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/ml/personal/comparisons/{uuid.uuid4()}", headers=other
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# 6. Evaluation reads only the frozen snapshot; no live market data.
# ---------------------------------------------------------------------------


def test_comparison_reads_no_live_market_data(pair, client, auth_headers, monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("comparison must not read live market bars")

    monkeypatch.setattr(
        "app.services.personal_dataset._daily_bars_visible_by", _forbidden
    )
    monkeypatch.setattr(
        "app.services.personal_dataset.features_as_of", _forbidden
    )

    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key="cmp-no-live-0001"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    for role in ("a", "b"):
        evaluation_set = body["versions"][role]["evaluation_set"]
        start = evaluation_set["test_start_decision_instant"]
        end = evaluation_set["test_end_decision_instant"]
        assert start and end
        assert evaluation_set["date_range"]["start"] <= start[:10]
        assert end[:10] <= evaluation_set["date_range"]["end"]
        assert evaluation_set["data_cutoff_at"] <= evaluation_set["date_range"]["end"]


# ---------------------------------------------------------------------------
# 7. Small-sample / missing-class warnings are surfaced, not hidden.
# ---------------------------------------------------------------------------


def _manual_pair(client, auth_headers, tmp_path, monkeypatch):
    """Train two real artifacts on a 40-row frozen snapshot (small by design)."""
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers, title="Small comparison")
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    built = _build_snapshot(client, auth_headers, version_id)
    artifacts = _personal_artifacts(tmp_path, monkeypatch)

    db = TestSession()
    try:
        source = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.id
                == uuid.UUID(built["snapshot_id"])
            )
            .one()
        )
        rows = sorted(
            (dict(row) for row in source.rows_json),
            key=lambda row: (
                row["decision_instant"],
                row["decided_at"],
                row["decision_event_id"],
            ),
        )[:40]
        for index, row in enumerate(rows):
            # First 20 buy, next 5 sell, last 15 hold: the test segment (the
            # last two decision days) then contains one class only.
            row["action_label"] = (
                "buy" if index < 20 else ("sell" if index < 25 else "hold")
            )
        feature_names = list(source.feature_names_json)
        label_definition = {
            **LABEL_DEFINITION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        }
        checksum = _canonical_checksum(rows, feature_names, LABEL_DEFINITION)
        snapshot = PersonalizationDatasetSnapshot(
            owner_id=owner_id,
            strategy_version_id=uuid.UUID(version_id),
            consent_id=source.consent_id,
            consent_version=source.consent_version,
            status=PersonalizationSnapshotStatus.ready,
            rows_json=rows,
            feature_names_json=feature_names,
            label_definition_json=label_definition,
            exclusion_breakdown_json={},
            class_distribution_json={
                "buy": 20,
                "sell": 5,
                "hold": 15,
            },
            included_count=len(rows),
            excluded_count=0,
            date_range_start=rows[0]["snapshot_date"],
            date_range_end=rows[-1]["snapshot_date"],
            data_cutoff_at=rows[-1]["data_cutoff_at"],
            checksum=checksum,
        )
        db.add(snapshot)
        db.flush()

        record = ModelResearchRecord(
            owner_id=owner_id,
            title="個人決策模型 · small-fixture",
            purpose="工程測試用小型凍結資料集。",
            status=ModelStatus.researching,
            allowed_research_actions=["predict_personal_decision"],
        )
        db.add(record)
        db.flush()

        model_ids = {}
        for number, model_type in enumerate(("logistic_regression", "baseline"), start=1):
            result = train_personal_model(
                rows,
                feature_names,
                owner_id=str(owner_id),
                snapshot_id=str(snapshot.id),
                snapshot_checksum=checksum,
                strategy_version_id=version_id,
                label_definition=label_definition,
                model_type=model_type,
            )
            version = ModelVersion(
                model_research_record_id=record.id,
                version_number=number,
                model_type=ModelType.personal_decision_model,
                model_specification_json={
                    "task_type": PERSONAL_TASK_TYPE,
                    "artifact_id": result["model_id"],
                    "artifact_sha256": result["artifact_sha256"],
                    "snapshot_id": str(snapshot.id),
                    "snapshot_checksum": checksum,
                    "strategy_version_id": version_id,
                    "model_type": model_type,
                    "classes": result["classes"],
                    "feature_names": result["feature_names"],
                    "n_rows": result["n_rows"],
                    "split": result["split"],
                    "warnings": result["warnings"],
                    "not_for": [
                        "市場方向預測",
                        "交易是否正確或有利潤",
                        "投資建議",
                    ],
                },
                linked_dataset_snapshot_ids=[str(snapshot.id)],
            )
            db.add(version)
            db.flush()
            model_ids[model_type] = str(version.id)
        db.commit()
        return {
            "owner_id": owner_id,
            "record_id": str(record.id),
            "artifacts": artifacts,
            "model_a": model_ids["logistic_regression"],
            "model_b": model_ids["baseline"],
        }
    finally:
        db.close()


def test_small_dataset_and_missing_class_warnings(client, auth_headers, tmp_path, monkeypatch):
    pair = _manual_pair(client, auth_headers, tmp_path, monkeypatch)
    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json={
            "version_a_id": pair["model_a"],
            "version_b_id": pair["model_b"],
            "operation_key": "cmp-small-0001",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["same_evaluation_set"] is True
    assert "small_test_set" in body["warnings"]
    assert "missing_class_in_test" in body["warnings"]
    assert "small_class_support" in body["warnings"]

    version_a = body["versions"]["a"]
    assert version_a["metrics"]["test_size"] < 30
    assert version_a["metrics"]["test_size"] > 0
    assert any(
        per_class["support"] == 0
        for per_class in version_a["metrics"]["per_class"].values()
    )
    assert any(window["insufficient"] for window in version_a["windows"])
    assert all(window["n_rows"] > 0 for window in version_a["windows"])
    assert len(version_a["windows"]) <= 3


# ---------------------------------------------------------------------------
# 8. A failed run writes no success metrics and releases the reservation.
# ---------------------------------------------------------------------------


def test_corrupted_artifact_fails_without_writing_metrics_or_charging(
    pair, client, auth_headers
):
    artifact_id = pair["model_b"]["artifact_id"]
    path = pair["artifacts"] / artifact_id / "model.joblib"
    path.write_bytes(b"corrupted-artifact")
    before = _quota_state(pair["owner_id"])["used"]

    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key="cmp-corrupt-0001"),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "artifact_checksum_mismatch"

    db = TestSession()
    try:
        assert db.query(PersonalModelComparisonRun).count() == 0
        assert (
            db.query(ModelEvaluation)
            .filter(ModelEvaluation.evaluation_type == EvaluationType.model_comparison)
            .count()
            == 0
        )
    finally:
        db.close()
    assert _quota_state(pair["owner_id"])["used"] == before


def test_comparing_a_version_with_itself_is_refused(pair, client, auth_headers):
    before = _quota_state(pair["owner_id"])["used"]
    resp = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json={
            "version_a_id": pair["model_a"]["model_version_id"],
            "version_b_id": pair["model_a"]["model_version_id"],
            "operation_key": "cmp-same-0001",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "same_version"
    assert _quota_state(pair["owner_id"])["used"] == before


# ---------------------------------------------------------------------------
# 9. Explicit selection keeps rationale and leaves history untouched.
# ---------------------------------------------------------------------------


def test_selection_records_rationale_and_leaves_history_unchanged(
    pair, client, auth_headers
):
    model_a_id = pair["model_a"]["model_version_id"]
    model_b_id = pair["model_b"]["model_version_id"]
    record_id = pair["record_id"]

    comparison = client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=auth_headers,
        json=_comparison_body(pair, operation_key="cmp-select-0001"),
    )
    assert comparison.status_code == 200, comparison.text
    run_id = comparison.json()["comparison_run_id"]

    db = TestSession()
    try:
        before_eval = {
            str(row.id): (row.metrics_json, row.evaluation_date)
            for row in db.query(ModelEvaluation)
            .filter(ModelEvaluation.model_version_id == uuid.UUID(model_b_id))
            .all()
        }
        before_versions = db.query(ModelVersion).count()
    finally:
        db.close()

    first = client.post(
        f"/api/v1/ml/personal/models/{model_b_id}/select",
        headers=auth_headers,
        json={"rationale": "同一評估集上 macro F1 較好，先試用。", "comparison_run_id": run_id},
    )
    assert first.status_code == 200, first.text
    assert first.json()["already_active"] is False
    assert first.json()["active_version"]["is_active"] is True
    assert first.json()["selection"]["previous_version_id"] is None
    assert first.json()["selection"]["comparison_run_id"] == run_id

    second = client.post(
        f"/api/v1/ml/personal/models/{model_a_id}/select",
        headers=auth_headers,
        json={"rationale": "改用另一個版本觀察差異。"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["selection"]["previous_version_id"] == model_b_id

    duplicate = client.post(
        f"/api/v1/ml/personal/models/{model_a_id}/select",
        headers=auth_headers,
        json={"rationale": "重複選同一個版本。"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["already_active"] is True
    assert duplicate.json()["selection"] is None

    db = TestSession()
    try:
        after_eval = {
            str(row.id): (row.metrics_json, row.evaluation_date)
            for row in db.query(ModelEvaluation)
            .filter(ModelEvaluation.model_version_id == uuid.UUID(model_b_id))
            .all()
        }
        assert after_eval == before_eval
        assert db.query(ModelVersion).count() == before_versions
        selections = (
            db.query(PersonalModelSelection)
            .filter(PersonalModelSelection.record_id == uuid.UUID(record_id))
            .all()
        )
        assert len(selections) == 2
    finally:
        db.close()

    history = client.get(
        f"/api/v1/ml/personal/records/{record_id}/selections", headers=auth_headers
    )
    assert history.status_code == 200, history.text
    rows = history.json()["selections"]
    assert [row["rationale"] for row in rows] == [
        "改用另一個版本觀察差異。",
        "同一評估集上 macro F1 較好，先試用。",
    ]
    assert history.json()["active_version_id"] == model_a_id


# ---------------------------------------------------------------------------
# 10. Rollback returns the previous version and refuses without history.
# ---------------------------------------------------------------------------


def test_rollback_returns_previous_version_and_requires_history(
    pair, client, auth_headers
):
    record_id = pair["record_id"]
    model_a_id = pair["model_a"]["model_version_id"]
    model_b_id = pair["model_b"]["model_version_id"]

    empty = client.post(
        f"/api/v1/ml/personal/records/{record_id}/rollback",
        headers=auth_headers,
        json={"rationale": "尚未切換過。"},
    )
    assert empty.status_code == 409, empty.text
    assert empty.json()["detail"]["code"] == "no_previous_version"

    assert (
        client.post(
            f"/api/v1/ml/personal/models/{model_a_id}/select",
            headers=auth_headers,
            json={"rationale": "先選 A。"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{model_b_id}/select",
            headers=auth_headers,
            json={"rationale": "再選 B。"},
        ).status_code
        == 200
    )

    rollback = client.post(
        f"/api/v1/ml/personal/records/{record_id}/rollback",
        headers=auth_headers,
        json={"rationale": "B 表現較差，回退。"},
    )
    assert rollback.status_code == 200, rollback.text
    body = rollback.json()
    assert body["selection"]["action"] == "rollback"
    assert body["active_version"]["model_version_id"] == model_a_id
    assert body["selection"]["previous_version_id"] == model_b_id
    assert body["selection"]["rationale"] == "B 表現較差，回退。"

    db = TestSession()
    try:
        record_versions = (
            db.query(ModelVersion)
            .filter(ModelVersion.model_research_record_id == uuid.UUID(record_id))
            .count()
        )
        assert record_versions == 2
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 11. Active-alias prediction follows the switch; old versions stay usable.
# ---------------------------------------------------------------------------


def test_prediction_alias_follows_switch_and_old_versions_stay_usable(
    pair, client, auth_headers
):
    record_id = pair["record_id"]
    model_a_id = pair["model_a"]["model_version_id"]
    model_b_id = pair["model_b"]["model_version_id"]
    as_of = BARS[80].isoformat()
    payload = {"instrument_id": INSTRUMENT, "as_of_date": as_of}

    assert (
        client.post(
            f"/api/v1/ml/personal/models/{model_a_id}/select",
            headers=auth_headers,
            json={"rationale": "先用 A。"},
        ).status_code
        == 200
    )
    first = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert first.status_code == 200, first.text
    assert first.json()["resolved_version"]["model_version_id"] == model_a_id
    assert first.json()["resolved_version"]["resolved_from_active_alias"] is True
    frozen_first = first.json()

    assert (
        client.post(
            f"/api/v1/ml/personal/models/{model_b_id}/select",
            headers=auth_headers,
            json={"rationale": "改用 B。"},
        ).status_code
        == 200
    )
    second = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert second.status_code == 200, second.text
    assert second.json()["resolved_version"]["model_version_id"] == model_b_id

    explicit_old = client.post(
        f"/api/v1/ml/personal/models/{model_a_id}/predict",
        headers=auth_headers,
        json=payload,
    )
    assert explicit_old.status_code == 200, explicit_old.text
    assert (
        explicit_old.json()["resolved_version"]["model_version_id"] == model_a_id
    )
    assert explicit_old.json()["resolved_version"]["resolved_from_active_alias"] is False
    assert explicit_old.json()["artifact_sha256"] == frozen_first["artifact_sha256"]
