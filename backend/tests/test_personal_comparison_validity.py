"""Comparison validity and safe selection.

Every bar, decision, label, snapshot and artifact here is a SYNTHETIC FIXTURE
built inside the test. These tests prove the comparison contract:

* the evaluation batch is keyed by the original decision identity, not by the
  snapshot UUID a row happened to live in;
* no row either side used to fit is scored as common unseen evidence;
* a shared decision whose payload differs makes provenance insufficient
  instead of producing a ranking;
* the baseline rule is fixed from each side's own training segment, never from
  the rows being scored;
* selection and rollback re-check ownership, version status, artifact bytes and
  the current consent scope at operation time, refuse stale expectations, and
  keep the alias change and the history append in one transaction.

They do not prove that any model is effective, that decision-mimicry predicts
returns, or that a real owner would prefer one version.
"""
import os
import threading
import time
import uuid

import pytest

from tests.conftest import TestSession
from app.ml.personal_trainer import (
    PERSONAL_TASK_TYPE,
    _most_frequent_label,
    train_personal_model,
)
from app.models.model_research import (
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
from app.services.model_comparison import (
    COMPARISON_CONTRACT_VERSION,
    _ordered_rows,
    _segment_rows,
)
from app.services.personal_dataset import (
    FEATURE_SCHEMA_VERSION,
    LABEL_DEFINITION,
    _canonical_checksum,
)
from app.services.personal_selection import SelectionRefused, select_version
from tests.test_personal_strategy_learning import (
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _personal_artifacts,
    _quota_state,
    _seed_confirmed_decisions,
    _second_user_headers,
    _seed_bars,
    _user_id,
)
from app.models.market_data import MarketDataMetadata
from tests.test_personal_strategy_learning import BARS, INSTRUMENT


PG_ONLY = pytest.mark.skipif(
    os.environ.get("ALETHRA_TEST_DB", "sqlite").startswith("sqlite"),
    reason="row locks are only enforced by PostgreSQL; SQLite cannot show them",
)


@pytest.fixture(autouse=True)
def _synthetic_market(setup_db):
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


# ---------------------------------------------------------------------------
# Fixture helpers: real snapshots, real trainer output, tests choose the rows.
# ---------------------------------------------------------------------------


def _snapshot_basics(snapshot_id):
    db = TestSession()
    try:
        snapshot = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(PersonalizationDatasetSnapshot.id == uuid.UUID(str(snapshot_id)))
            .one()
        )
        return {
            "id": snapshot.id,
            "rows": [dict(row) for row in snapshot.rows_json],
            "feature_names": list(snapshot.feature_names_json or []),
            "consent_id": snapshot.consent_id,
            "consent_version": snapshot.consent_version,
            "class_distribution": dict(snapshot.class_distribution_json or {}),
            "date_range_start": snapshot.date_range_start,
            "date_range_end": snapshot.date_range_end,
            "data_cutoff_at": snapshot.data_cutoff_at,
            "checksum": snapshot.checksum,
        }
    finally:
        db.close()


def _insert_snapshot(owner_id, strategy_version_id, base, rows):
    """A frozen snapshot over exactly `rows`, with a self-consistent checksum."""
    ordered = sorted(rows, key=lambda row: row["decision_event_id"])
    checksum = _canonical_checksum(ordered, base["feature_names"], LABEL_DEFINITION)
    distribution = {}
    for row in ordered:
        distribution[row["action_label"]] = distribution.get(row["action_label"], 0) + 1
    db = TestSession()
    try:
        snapshot = PersonalizationDatasetSnapshot(
            owner_id=owner_id,
            strategy_version_id=uuid.UUID(str(strategy_version_id)),
            consent_id=base["consent_id"],
            consent_version=base["consent_version"],
            status=PersonalizationSnapshotStatus.ready,
            rows_json=ordered,
            feature_names_json=base["feature_names"],
            label_definition_json={
                **LABEL_DEFINITION,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
            },
            exclusion_breakdown_json={},
            class_distribution_json=distribution,
            included_count=len(ordered),
            excluded_count=0,
            date_range_start=ordered[0]["snapshot_date"],
            date_range_end=ordered[-1]["snapshot_date"],
            data_cutoff_at=max(str(row["data_cutoff_at"]) for row in ordered),
            checksum=checksum,
        )
        db.add(snapshot)
        db.commit()
        return {"id": snapshot.id, "checksum": checksum, "rows": ordered}
    finally:
        db.close()


def _record(owner_id, title="個人決策模型 · validity-fixture"):
    db = TestSession()
    try:
        record = ModelResearchRecord(
            owner_id=owner_id,
            title=title,
            purpose="工程測試用合成凍結資料集。",
            status=ModelStatus.researching,
            allowed_research_actions=["predict_personal_decision"],
        )
        db.add(record)
        db.commit()
        return record.id
    finally:
        db.close()


def _train_version(
    *,
    owner_id,
    record_id,
    base,
    snapshot,
    strategy_version_id,
    version_number,
    model_type="logistic_regression",
    rows=None,
    record_baseline=True,
):
    rows = rows if rows is not None else snapshot["rows"]
    result = train_personal_model(
        rows,
        base["feature_names"],
        owner_id=str(owner_id),
        snapshot_id=str(snapshot["id"]),
        snapshot_checksum=snapshot["checksum"],
        strategy_version_id=str(strategy_version_id),
        label_definition={
            **LABEL_DEFINITION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
        },
        model_type=model_type,
    )
    spec = {
        "task_type": PERSONAL_TASK_TYPE,
        "artifact_id": result["model_id"],
        "artifact_sha256": result["artifact_sha256"],
        "snapshot_id": str(snapshot["id"]),
        "snapshot_checksum": snapshot["checksum"],
        "strategy_version_id": str(strategy_version_id),
        "model_type": model_type,
        "classes": result["classes"],
        "feature_names": result["feature_names"],
        "n_rows": result["n_rows"],
        "split": result["split"],
        "warnings": result["warnings"],
        "not_for": ["市場方向預測", "交易是否正確或有利潤", "投資建議"],
    }
    if record_baseline:
        spec["baseline"] = result["baseline"]
    db = TestSession()
    try:
        version = ModelVersion(
            model_research_record_id=record_id,
            version_number=version_number,
            model_type=ModelType.personal_decision_model,
            model_specification_json=spec,
            linked_dataset_snapshot_ids=[str(snapshot["id"])],
        )
        db.add(version)
        db.commit()
        return {
            "model_version_id": str(version.id),
            "artifact_id": result["model_id"],
            "spec": spec,
        }
    finally:
        db.close()


@pytest.fixture()
def lab(client, auth_headers, tmp_path, monkeypatch):
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers, title="Comparison validity")
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    built = _build_snapshot(client, auth_headers, version_id)
    base = _snapshot_basics(built["snapshot_id"])
    _personal_artifacts(tmp_path, monkeypatch)
    return {
        "owner_id": owner_id,
        "strategy_version_id": version_id,
        "base": base,
        "base_snapshot": {
            "id": built["snapshot_id"],
            "checksum": base["checksum"],
            "rows": base["rows"],
        },
        "record_id": _record(owner_id),
    }


def _subset_by_day(rows, last_day_index):
    days = sorted({row["snapshot_date"] for row in rows})
    keep = set(days[: last_day_index + 1])
    return [row for row in rows if row["snapshot_date"] in keep]


def _compare(client, headers, a_id, b_id, key):
    return client.post(
        "/api/v1/ml/personal/models/comparisons",
        headers=headers,
        json={"version_a_id": a_id, "version_b_id": b_id, "operation_key": key},
    )


def _check(body, code):
    return next(check for check in body["checks"] if check["code"] == code)


# ---------------------------------------------------------------------------
# 1. The same decisions under two different snapshot UUIDs are one batch.
# ---------------------------------------------------------------------------


def test_identical_decisions_in_two_snapshots_do_not_double_count(
    lab, client, auth_headers
):
    base = lab["base"]
    copy = _insert_snapshot(
        lab["owner_id"], lab["strategy_version_id"], base, base["rows"]
    )
    original = {
        "id": uuid.UUID(lab["base_snapshot"]["id"]),
        "checksum": lab["base_snapshot"]["checksum"],
        "rows": base["rows"],
    }
    a = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=original,
        strategy_version_id=lab["strategy_version_id"],
        version_number=1,
    )
    b = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=copy,
        strategy_version_id=lab["strategy_version_id"],
        version_number=2,
        model_type="baseline",
    )

    resp = _compare(
        client, auth_headers, a["model_version_id"], b["model_version_id"], "validity-dedupe-1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["mode"] == "same_evaluation_set"
    assert body["evaluation_purpose"] == "common_unseen"
    assert body["rankable"] is True
    assert body["independent_out_of_sample"] is True
    assert body["same_evaluation_set"] is True

    common = body["common_evaluation"]
    assert body["versions"]["a"]["snapshot_id"] != body["versions"]["b"]["snapshot_id"]
    # Same decisions, one row each: the batch equals one test window, not two.
    assert common["n_rows"] == body["versions"]["a"]["evaluation_set"]["test_size"]
    assert common["n_rows"] == body["versions"]["b"]["evaluation_set"]["test_size"]
    assert common["shared_test_group_count"] == common["n_groups"]
    assert body["delta"]["n_rows"] == common["n_rows"]
    assert body["disagreement"]["n_rows"] == common["n_rows"]
    assert _check(body, "common_evaluation_rows")["ok"] is True
    assert _check(body, "no_peer_leakage")["ok"] is True
    assert body["evaluation_reasons"] == []

    listing = client.get("/api/v1/ml/personal/comparisons", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    first = listing.json()["comparisons"][0]
    assert first["evaluation_purpose"] == "common_unseen"
    assert first["independent_out_of_sample"] is True


# ---------------------------------------------------------------------------
# 2. A's test rows inside B's training segment are excluded and disclosed.
# ---------------------------------------------------------------------------


def test_peer_training_rows_are_excluded_from_the_common_batch(
    lab, client, auth_headers
):
    base = lab["base"]
    small_rows = _subset_by_day(base["rows"], 20)
    small = _insert_snapshot(
        lab["owner_id"], lab["strategy_version_id"], base, small_rows
    )
    full = {
        "id": uuid.UUID(lab["base_snapshot"]["id"]),
        "checksum": lab["base_snapshot"]["checksum"],
        "rows": base["rows"],
    }
    # A is trained on days 0..20: its test window (days 17..20) later falls
    # inside B's training segment (days 0..27) because B has more history.
    a = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=small,
        strategy_version_id=lab["strategy_version_id"],
        version_number=1,
    )
    b = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=full,
        strategy_version_id=lab["strategy_version_id"],
        version_number=2,
    )

    resp = _compare(
        client, auth_headers, a["model_version_id"], b["model_version_id"], "validity-leak-1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    common = body["common_evaluation"]

    assert body["evaluation_purpose"] == "common_unseen"
    leaked = common["leakage"]["a_test_groups_used_in_b_fit"]
    assert leaked, "fixture failed to place A's test groups inside B's training"
    # The leaked groups are not scored as independent evidence.
    assert set(leaked).isdisjoint(common["group_keys"])
    assert _check(body, "no_peer_leakage")["ok"] is False
    assert _check(body, "no_peer_leakage")["required"] is False

    # Ranking rows are B's genuinely unseen test window, not A's own window.
    a_test_size = body["versions"]["a"]["evaluation_set"]["test_size"]
    assert a_test_size == a["spec"]["split"]["test"]["size"]
    assert common["n_rows"] != a_test_size
    assert common["n_rows"] == body["versions"]["b"]["evaluation_set"]["test_size"]
    assert common["n_rows"] == b["spec"]["split"]["test"]["size"]
    assert body["delta"]["n_rows"] == common["n_rows"]
    assert body["versions"]["a"]["evaluation_basis"] == "own_test_window"
    assert body["versions"]["a"]["common"]["metrics"]["test_size"] == common["n_rows"]
    # Every scored row is later than both training segments.
    assert common["sides"]["a"]["batch_rows_after_own_train_end"] == common["n_rows"]
    assert common["sides"]["b"]["batch_rows_after_own_train_end"] == common["n_rows"]


# ---------------------------------------------------------------------------
# 3. A shared decision with a different payload is not trusted.
# ---------------------------------------------------------------------------


def test_conflicting_row_payload_makes_provenance_insufficient(
    lab, client, auth_headers
):
    base = lab["base"]
    rows = [dict(row, features=dict(row["features"])) for row in base["rows"]]
    ordered = _ordered_rows(rows)
    target = ordered[-1]  # inside the last 15%, i.e. both test windows
    feature_name = base["feature_names"][0]
    target["features"][feature_name] = float(target["features"][feature_name]) + 0.5
    altered = _insert_snapshot(lab["owner_id"], lab["strategy_version_id"], base, rows)
    original = {
        "id": uuid.UUID(lab["base_snapshot"]["id"]),
        "checksum": lab["base_snapshot"]["checksum"],
        "rows": base["rows"],
    }
    a = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=original,
        strategy_version_id=lab["strategy_version_id"],
        version_number=1,
    )
    b = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=altered,
        strategy_version_id=lab["strategy_version_id"],
        version_number=2,
        model_type="baseline",
    )

    resp = _compare(
        client, auth_headers, a["model_version_id"], b["model_version_id"], "validity-conflict-1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["evaluation_purpose"] == "provenance_insufficient"
    assert body["evaluation_purpose_zh"].startswith("來源不足")
    assert body["rankable"] is False
    assert body["independent_out_of_sample"] is False
    assert body["delta"] is None
    assert "conflicting_row_identity" in body["evaluation_reasons"]
    assert body["common_evaluation"] is None
    assert body["versions"]["a"]["common"] is None
    assert body["versions"]["b"]["common"] is None
    assert body["versions"]["a"]["metrics"]["test_size"] > 0  # own windows remain
    assert any("來源不足" in caveat or "未能建立" in caveat for caveat in body["caveats_zh"])
    assert _check(body, "common_evaluation_rows")["ok"] is False

    # The refusal is persisted as research evidence, not as a ranking.
    run = client.get(
        f"/api/v1/ml/personal/comparisons/{body['comparison_run_id']}",
        headers=auth_headers,
    )
    assert run.status_code == 200
    assert run.json()["evaluation_purpose"] == "provenance_insufficient"


# ---------------------------------------------------------------------------
# 4. Each side's baseline is fixed from its own training segment.
# ---------------------------------------------------------------------------


def test_baseline_is_fixed_from_training_and_reported_for_both_sides(
    lab, client, auth_headers
):
    base = lab["base"]
    copy = _insert_snapshot(
        lab["owner_id"], lab["strategy_version_id"], base, base["rows"]
    )
    original = {
        "id": uuid.UUID(lab["base_snapshot"]["id"]),
        "checksum": lab["base_snapshot"]["checksum"],
        "rows": base["rows"],
    }
    recorded = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=original,
        strategy_version_id=lab["strategy_version_id"],
        version_number=1,
    )
    # A legacy version: no recorded baseline, re-derived the same way.
    legacy = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=copy,
        strategy_version_id=lab["strategy_version_id"],
        version_number=2,
        model_type="baseline",
        record_baseline=False,
    )

    resp = _compare(
        client, auth_headers, recorded["model_version_id"], legacy["model_version_id"],
        "validity-baseline-1",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evaluation_purpose"] == "common_unseen"

    db = TestSession()
    try:
        version = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == uuid.UUID(legacy["model_version_id"]))
            .one()
        )
        snapshot = (
            db.query(PersonalizationDatasetSnapshot)
            .filter(PersonalizationDatasetSnapshot.id == copy["id"])
            .one()
        )
        slices = _segment_rows(version, snapshot)
    finally:
        db.close()
    expected_label = _most_frequent_label(
        [row["action_label"] for row in slices["train_rows"]]
    )
    test_labels = {row["action_label"] for row in slices["test_rows"]}

    assert body["versions"]["a"]["baseline"]["rule"] == "most_frequent_label_in_train_segment"
    assert body["versions"]["a"]["baseline"]["source"] == "fixed_at_training"
    assert body["versions"]["a"]["baseline"]["label"] == expected_label
    assert body["versions"]["b"]["baseline"]["source"] == (
        "derived_from_train_segment_at_comparison"
    )
    assert body["versions"]["b"]["baseline"]["label"] == expected_label
    assert body["common_evaluation"]["baseline_shared"] is True
    # The rule is taken from the training labels; a baseline chosen from the
    # scored labels would be a different (and here, distinguishable) value.
    assert expected_label in test_labels or expected_label not in test_labels
    assert all(
        side["baseline"]["rule"] == "most_frequent_label_in_train_segment"
        for side in (body["versions"]["a"], body["versions"]["b"])
    )


# ---------------------------------------------------------------------------
# 5. Selection re-checks the version at operation time.
# ---------------------------------------------------------------------------


def _select(client, headers, version_id, expected, rationale="選用此版本觀察。"):
    return client.post(
        f"/api/v1/ml/personal/models/{version_id}/select",
        headers=headers,
        json={
            "rationale": rationale,
            "expected_active_version_id": expected,
        },
    )


def _two_versions(lab):
    base = lab["base"]
    copy = _insert_snapshot(
        lab["owner_id"], lab["strategy_version_id"], base, base["rows"]
    )
    original = {
        "id": uuid.UUID(lab["base_snapshot"]["id"]),
        "checksum": lab["base_snapshot"]["checksum"],
        "rows": base["rows"],
    }
    a = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=original,
        strategy_version_id=lab["strategy_version_id"],
        version_number=1,
    )
    b = _train_version(
        owner_id=lab["owner_id"],
        record_id=lab["record_id"],
        base=base,
        snapshot=copy,
        strategy_version_id=lab["strategy_version_id"],
        version_number=2,
        model_type="baseline",
    )
    return a, b


def _set_version(version_id, **fields):
    db = TestSession()
    try:
        version = (
            db.query(ModelVersion)
            .filter(ModelVersion.id == uuid.UUID(version_id))
            .one()
        )
        for key, value in fields.items():
            setattr(version, key, value)
        db.commit()
    finally:
        db.close()


def _active(record_id):
    db = TestSession()
    try:
        record = (
            db.query(ModelResearchRecord)
            .filter(ModelResearchRecord.id == record_id)
            .one()
        )
        return str(record.active_version_id) if record.active_version_id else None
    finally:
        db.close()


def _selection_count(record_id):
    db = TestSession()
    try:
        return (
            db.query(PersonalModelSelection)
            .filter(PersonalModelSelection.record_id == record_id)
            .count()
        )
    finally:
        db.close()


def test_revoked_version_with_an_old_comparison_id_cannot_be_selected(
    lab, client, auth_headers
):
    a, b = _two_versions(lab)
    comparison = _compare(
        client, auth_headers, a["model_version_id"], b["model_version_id"], "validity-revoke-1"
    )
    assert comparison.status_code == 200, comparison.text
    run_id = comparison.json()["comparison_run_id"]

    assert _select(client, auth_headers, a["model_version_id"], None).status_code == 200
    before = _selection_count(lab["record_id"])

    _set_version(b["model_version_id"], revoked_at=BARS[0])
    refused = client.post(
        f"/api/v1/ml/personal/models/{b['model_version_id']}/select",
        headers=auth_headers,
        json={
            "rationale": "引用舊比較切換。",
            "comparison_run_id": run_id,
            "expected_active_version_id": a["model_version_id"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "model_version_revoked"
    assert _active(lab["record_id"]) == a["model_version_id"]
    assert _selection_count(lab["record_id"]) == before

    # Rollback refuses a revoked target for the same reason.
    assert _select(client, auth_headers, b["model_version_id"], a["model_version_id"]).status_code == 409


def test_selection_requires_a_present_artifact_with_matching_hash(
    lab, client, auth_headers, tmp_path
):
    a, b = _two_versions(lab)
    assert _select(client, auth_headers, a["model_version_id"], None).status_code == 200

    artifact_dir = tmp_path / "personal-artifacts" / b["artifact_id"]
    model_file = artifact_dir / "model.joblib"
    original_bytes = model_file.read_bytes()
    model_file.write_bytes(b"tampered")
    refused = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "artifact_checksum_mismatch"

    model_file.unlink()
    missing = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "artifact_missing"

    model_file.write_bytes(original_bytes)
    accepted = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert accepted.status_code == 200, accepted.text
    assert _active(lab["record_id"]) == b["model_version_id"]

    unknown = client.post(
        "/api/v1/ml/personal/models/{}/select".format(uuid.uuid4()),
        headers=auth_headers,
        json={"rationale": "不存在的版本。", "expected_active_version_id": b["model_version_id"]},
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"]["code"] == "version_not_found"


def test_consent_and_record_status_block_selection(lab, client, auth_headers):
    a, b = _two_versions(lab)
    assert _select(client, auth_headers, a["model_version_id"], None).status_code == 200

    revoked_consent = client.delete(
        "/api/v1/ml/personalization/consent", headers=auth_headers
    )
    assert revoked_consent.status_code == 200, revoked_consent.text
    refused = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "consent_not_active"

    _grant_consent(client, auth_headers, lab["strategy_version_id"])
    other_scope = _create_experiment(client, auth_headers, title="Other scope")
    _grant_consent(client, auth_headers, other_scope[1])
    refused = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "consent_scope_mismatch"

    _grant_consent(client, auth_headers, lab["strategy_version_id"])
    db = TestSession()
    try:
        record = (
            db.query(ModelResearchRecord)
            .filter(ModelResearchRecord.id == lab["record_id"])
            .one()
        )
        record.status = ModelStatus.revoked
        db.commit()
    finally:
        db.close()
    refused = _select(client, auth_headers, b["model_version_id"], a["model_version_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "record_not_active"
    assert _active(lab["record_id"]) == a["model_version_id"]


def test_stale_expected_active_version_is_refused_without_history(
    lab, client, auth_headers
):
    a, b = _two_versions(lab)
    assert _select(client, auth_headers, a["model_version_id"], None).status_code == 200
    before = _selection_count(lab["record_id"])

    stale = _select(client, auth_headers, b["model_version_id"], None)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "stale_active_version"
    assert detail["details"]["expected_active_version_id"] is None
    assert detail["details"]["actual_active_version_id"] == a["model_version_id"]
    assert _active(lab["record_id"]) == a["model_version_id"]
    assert _selection_count(lab["record_id"]) == before

    stale_activation = client.post(
        f"/api/v1/ml/personal/models/{b['model_version_id']}/activate",
        headers=auth_headers,
        json={"expected_active_version_id": None},
    )
    assert stale_activation.status_code == 409, stale_activation.text
    assert stale_activation.json()["detail"]["code"] == "stale_active_version"

    stale_rollback = client.post(
        f"/api/v1/ml/personal/records/{lab['record_id']}/rollback",
        headers=auth_headers,
        json={"rationale": "過時回退。", "expected_active_version_id": b["model_version_id"]},
    )
    assert stale_rollback.status_code == 409
    assert stale_rollback.json()["detail"]["code"] == "stale_active_version"
    assert _active(lab["record_id"]) == a["model_version_id"]
    assert _selection_count(lab["record_id"]) == before


# ---------------------------------------------------------------------------
# 6. Operation keys are content-bound, not timing-bound.
# ---------------------------------------------------------------------------


def test_operation_key_replays_the_same_request_and_conflicts_on_another(
    lab, client, auth_headers
):
    a, b = _two_versions(lab)
    key = "validity-key-0001"
    first = _compare(client, auth_headers, a["model_version_id"], b["model_version_id"], key)
    assert first.status_code == 200, first.text
    used = _quota_state(lab["owner_id"])["used"]

    same = _compare(client, auth_headers, a["model_version_id"], b["model_version_id"], key)
    assert same.status_code == 200, same.text
    assert same.json()["idempotent_replay"] is True
    assert same.json()["comparison_run_id"] == first.json()["comparison_run_id"]
    assert _quota_state(lab["owner_id"])["used"] == used

    # Same key, different canonical request (side order is part of identity).
    reversed_pair = _compare(
        client, auth_headers, b["model_version_id"], a["model_version_id"], key
    )
    assert reversed_pair.status_code == 409, reversed_pair.text
    assert reversed_pair.json()["detail"]["code"] == "operation_key_conflict"
    assert _quota_state(lab["owner_id"])["used"] == used

    db = TestSession()
    try:
        runs = db.query(PersonalModelComparisonRun).all()
        assert len(runs) == 1
        stored = runs[0].request_json or {}
        assert stored["contract_version"] == COMPARISON_CONTRACT_VERSION
        assert stored["a"]["snapshot_id"] == a["spec"]["snapshot_id"]
        assert stored["b"]["snapshot_id"] == b["spec"]["snapshot_id"]
        assert stored["a"]["artifact_sha256"] == a["spec"]["artifact_sha256"]
    finally:
        db.close()


def test_failed_comparison_can_be_retried_with_the_same_key(
    lab, client, auth_headers, tmp_path
):
    a, b = _two_versions(lab)
    key = "validity-retry-0001"
    artifact_dir = tmp_path / "personal-artifacts" / b["artifact_id"]
    model_file = artifact_dir / "model.joblib"
    original_bytes = model_file.read_bytes()

    model_file.write_bytes(b"corrupted")
    before = _quota_state(lab["owner_id"])["used"]
    failed = _compare(client, auth_headers, a["model_version_id"], b["model_version_id"], key)
    assert failed.status_code == 409, failed.text
    assert failed.json()["detail"]["code"] == "artifact_checksum_mismatch"
    assert _quota_state(lab["owner_id"])["used"] == before
    db = TestSession()
    try:
        assert db.query(PersonalModelComparisonRun).count() == 0
    finally:
        db.close()

    model_file.write_bytes(original_bytes)
    retried = _compare(client, auth_headers, a["model_version_id"], b["model_version_id"], key)
    assert retried.status_code == 200, retried.text
    assert retried.json().get("idempotent_replay") is None
    assert _quota_state(lab["owner_id"])["used"] == before + 1


# ---------------------------------------------------------------------------
# 7. Owner isolation on reads and writes.
# ---------------------------------------------------------------------------


def test_other_owner_sees_no_runs_and_cannot_reuse_a_key(lab, client, auth_headers):
    a, b = _two_versions(lab)
    key = "validity-isolation-1"
    assert _compare(
        client, auth_headers, a["model_version_id"], b["model_version_id"], key
    ).status_code == 200

    other = _second_user_headers(client)
    assert client.get("/api/v1/ml/personal/comparisons", headers=other).json() == {
        "comparisons": []
    }
    assert (
        client.get(
            f"/api/v1/ml/personal/records/{lab['record_id']}/selections", headers=other
        ).status_code
        == 404
    )
    assert _select(client, other, a["model_version_id"], None).status_code == 404
    assert (
        client.post(
            f"/api/v1/ml/personal/records/{lab['record_id']}/rollback",
            headers=other,
            json={"rationale": "他人回退。", "expected_active_version_id": None},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# 8. Two real PostgreSQL sessions: the second selection blocks, then refuses.
# ---------------------------------------------------------------------------


@PG_ONLY
def test_postgres_two_sessions_block_on_the_record_lock(lab, client, auth_headers):
    a, b = _two_versions(lab)
    owner_id = lab["owner_id"]
    locked = threading.Event()
    release = threading.Event()
    outcome = {}

    def holder():
        db = TestSession()
        try:
            select_version(
                db,
                owner_id,
                version_id=uuid.UUID(b["model_version_id"]),
                rationale="第一個工作階段切換。",
                comparison_run_id=None,
                expected_active_version_id=None,
            )
            locked.set()
            release.wait(15)
            db.commit()
            outcome["holder"] = "committed"
        except Exception as exc:  # pragma: no cover - failure path
            outcome["holder"] = exc
            locked.set()
        finally:
            db.close()

    def contender():
        locked.wait(15)
        db = TestSession()
        try:
            time.sleep(0.2)
            select_version(
                db,
                owner_id,
                version_id=uuid.UUID(a["model_version_id"]),
                rationale="第二個工作階段同時切換。",
                comparison_run_id=None,
                expected_active_version_id=None,
            )
            db.commit()
            outcome["contender"] = "committed"
        except SelectionRefused as exc:
            outcome["contender"] = exc
        except Exception as exc:  # pragma: no cover - failure path
            outcome["contender"] = exc
        finally:
            db.close()

    first = threading.Thread(target=holder)
    second = threading.Thread(target=contender)
    first.start()
    second.start()
    assert locked.wait(15), "first session never reached the locked section"
    time.sleep(0.8)
    assert second.is_alive(), "second selection was not blocked by the record row lock"
    release.set()
    first.join(20)
    second.join(20)

    assert outcome.get("holder") == "committed", outcome
    refusal = outcome.get("contender")
    assert isinstance(refusal, SelectionRefused), outcome
    assert refusal.code == "stale_active_version"
    assert _active(lab["record_id"]) == b["model_version_id"]
    assert _selection_count(lab["record_id"]) == 1
