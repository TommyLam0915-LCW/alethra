"""Personal model API: per-endpoint permission matrix.

Every endpoint of the personal learning API is exercised against each
credential kind the product can present:

* no credentials            → 401
* JWT session               → allowed (the browser UI path)
* API key scoped ``read``    → snapshot/model reads only
* API key scoped ``inference`` → reads + predictions
* API key scoped ``training``  → reads + snapshots + training jobs + activate
* API key scoped ``write``     → every endpoint
* API key with no scopes     → refused everywhere
* revoked API key            → 401 everywhere
* another owner's credential → refused by ownership, never by scope

The matrix also pins the two properties that are easy to lose while adding
scopes: a refusal must not consume an ``ai_usage`` unit, create a training
job, create a model version, or write an artifact; and an active-alias
prediction must report the immutable version it actually resolved to, before
and after the active version is switched.

All market data and decisions here are SYNTHETIC FIXTURES built by the seeded
personal-learning helpers. They prove access control and side-effect
suppression, not model efficacy.
"""

import uuid

import pytest

from app.models.billing import UsageRecord
from app.models.model_research import ModelResearchRecord, ModelVersion
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.training import TrainingJob
from tests.conftest import TestSession
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _personal_artifacts,
    _run_worker_job,
    _seed_confirmed_decisions,
    _synthetic_market,
    _user_id,
)

AS_OF = BARS[120].isoformat()


# ── helpers ───────────────────────────────────────────────────────────


def _make_key(client, jwt_headers, scopes, label="matrix"):
    resp = client.post(
        "/api/v1/api-keys",
        json={"name": f"{label}-{uuid.uuid4().hex[:8]}", "scopes": scopes},
        headers=jwt_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["raw_key"], body["id"]


def _key_headers(raw_key):
    return {"Authorization": f"Bearer {raw_key}"}


def _second_account(client):
    payload = {
        "email": "personal-matrix-other@alethra.io",
        "password": "otherpass123",
        "display_name": "Other Owner",
        "user_type": "individual",
    }
    client.post("/api/auth/register", json=payload)
    login = client.post(
        "/api/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _snapshot_state(owner_id):
    db = TestSession()
    try:
        record_ids = [
            row.id
            for row in db.query(ModelResearchRecord.id).filter(
                ModelResearchRecord.owner_id == owner_id
            )
        ]
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == owner_id,
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            )
            .first()
        )
        return {
            "used": counter.used if counter else 0,
            "reserved": db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == owner_id,
                QuotaReservation.status == "reserved",
            )
            .count(),
            "usage_records": db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .count(),
            "snapshots": db.query(PersonalizationDatasetSnapshot)
            .filter(PersonalizationDatasetSnapshot.owner_id == owner_id)
            .count(),
            "jobs": db.query(TrainingJob)
            .filter(TrainingJob.user_id == owner_id)
            .count(),
            "versions": db.query(ModelVersion)
            .filter(ModelVersion.model_research_record_id.in_(record_ids))
            .count()
            if record_ids
            else 0,
        }
    finally:
        db.close()


def _artifact_count(path):
    return len([entry for entry in path.iterdir() if entry.is_dir()])


def _predict_bodies(stack):
    predict = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    return (
        f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/predict",
        f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
        predict,
    )


@pytest.fixture()
def stack(client, auth_headers, tmp_path, monkeypatch, _synthetic_market):
    artifacts = _personal_artifacts(tmp_path, monkeypatch)
    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    snapshot = _build_snapshot(client, auth_headers, version_id)

    job = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=auth_headers,
        json={"snapshot_id": snapshot["snapshot_id"]},
    )
    assert job.status_code == 202, job.text
    _run_worker_job(uuid.UUID(job.json()["job_id"]))

    models = client.get("/api/v1/ml/personal/models", headers=auth_headers).json()
    assert models["models"], "the fixture must produce one personal model"
    model = models["models"][0]
    activated = client.post(
        f"/api/v1/ml/personal/models/{model['model_version_id']}/activate",
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    return {
        "owner_id": owner_id,
        "jwt": auth_headers,
        "version_id": version_id,
        "snapshot_id": snapshot["snapshot_id"],
        "model": model,
        "artifacts": artifacts,
    }


# ── 1. Identity: no credentials at all ────────────────────────────────


def test_every_endpoint_refuses_missing_credentials(client, stack):
    model_version_id = stack["model"]["model_version_id"]
    record_id = stack["model"]["record_id"]
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    calls = [
        ("post", "/api/v1/ml/personal/snapshots/preview", {"strategy_version_id": stack["version_id"]}),
        ("post", "/api/v1/ml/personal/snapshots", {"strategy_version_id": stack["version_id"]}),
        ("get", "/api/v1/ml/personal/snapshots", None),
        ("get", f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", None),
        ("post", "/api/v1/ml/personal/training-jobs", {"snapshot_id": stack["snapshot_id"]}),
        ("get", "/api/v1/ml/personal/models", None),
        ("post", f"/api/v1/ml/personal/models/{model_version_id}/activate", None),
        ("post", f"/api/v1/ml/personal/models/{model_version_id}/predict", body),
        ("post", f"/api/v1/ml/personal/records/{record_id}/predict", body),
    ]
    for method, url, payload in calls:
        response = getattr(client, method)(url, json=payload) if payload else getattr(client, method)(url)
        assert response.status_code == 401, f"{method.upper()} {url} -> {response.status_code}"


# ── 2. Scope ceilings, per endpoint ───────────────────────────────────


def test_read_scope_covers_reads_only(client, stack):
    raw_key, _ = _make_key(client, stack["jwt"], ["read"], "read-matrix")
    headers = _key_headers(raw_key)

    assert client.get("/api/v1/ml/personal/snapshots", headers=headers).status_code == 200
    assert (
        client.get(
            f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", headers=headers
        ).status_code
        == 200
    )
    models = client.get("/api/v1/ml/personal/models", headers=headers)
    assert models.status_code == 200
    assert stack["model"]["model_version_id"] in [
        item["model_version_id"] for item in models.json()["models"]
    ]

    refused = [
        client.post(
            "/api/v1/ml/personal/snapshots/preview",
            headers=headers,
            json={"strategy_version_id": stack["version_id"]},
        ),
        client.post(
            "/api/v1/ml/personal/snapshots",
            headers=headers,
            json={"strategy_version_id": stack["version_id"]},
        ),
        client.post(
            "/api/v1/ml/personal/training-jobs",
            headers=headers,
            json={"snapshot_id": stack["snapshot_id"]},
        ),
        client.post(
            f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/activate",
            headers=headers,
        ),
        client.post(
            f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/predict",
            headers=headers,
            json={"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
        ),
        client.post(
            f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
            headers=headers,
            json={"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
        ),
    ]
    for response in refused:
        assert response.status_code == 403, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "insufficient_scope"
        assert detail["held_scopes"] == ["read"]
        assert detail["required_scope"] in detail["accepted_scopes"]


def test_inference_scope_reads_and_predicts_but_never_trains(client, stack):
    raw_key, _ = _make_key(client, stack["jwt"], ["inference"], "inference-matrix")
    headers = _key_headers(raw_key)
    before = _snapshot_state(stack["owner_id"])

    assert client.get("/api/v1/ml/personal/models", headers=headers).status_code == 200
    assert client.get("/api/v1/ml/personal/snapshots", headers=headers).status_code == 200

    model_version_id = stack["model"]["model_version_id"]
    record_id = stack["model"]["record_id"]
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    version_predict = client.post(
        f"/api/v1/ml/personal/models/{model_version_id}/predict",
        headers=headers,
        json=body,
    )
    assert version_predict.status_code == 200, version_predict.text
    record_predict = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict",
        headers=headers,
        json=body,
    )
    assert record_predict.status_code == 200, record_predict.text

    writes = [
        ("/api/v1/ml/personal/snapshots/preview", {"strategy_version_id": stack["version_id"]}),
        ("/api/v1/ml/personal/snapshots", {"strategy_version_id": stack["version_id"]}),
        ("/api/v1/ml/personal/training-jobs", {"snapshot_id": stack["snapshot_id"]}),
    ]
    for url, payload in writes:
        refused = client.post(url, headers=headers, json=payload)
        assert refused.status_code == 403, f"{url} -> {refused.text}"
        assert refused.json()["detail"]["accepted_scopes"] == ["write", "training"]
    activate = client.post(
        f"/api/v1/ml/personal/models/{model_version_id}/activate", headers=headers
    )
    assert activate.status_code == 403
    assert activate.json()["detail"]["accepted_scopes"] == ["write", "training"]

    after = _snapshot_state(stack["owner_id"])
    assert after["used"] == before["used"] + 2
    assert after["usage_records"] == before["usage_records"] + 2
    assert after["versions"] == before["versions"]
    assert after["jobs"] == before["jobs"]

    db = TestSession()
    try:
        latest = (
            db.query(UsageRecord)
            .filter(UsageRecord.user_id == stack["owner_id"])
            .order_by(UsageRecord.created_at.desc())
            .first()
        )
        assert latest.metadata_json["via"] == "api_key"
        assert latest.metadata_json["model_version_id"] == model_version_id
    finally:
        db.close()


def test_training_scope_builds_and_queues_but_cannot_predict(client, stack):
    raw_key, _ = _make_key(client, stack["jwt"], ["training"], "training-matrix")
    headers = _key_headers(raw_key)
    before = _snapshot_state(stack["owner_id"])

    preview = client.post(
        "/api/v1/ml/personal/snapshots/preview",
        headers=headers,
        json={"strategy_version_id": stack["version_id"]},
    )
    assert preview.status_code == 200, preview.text
    snapshot = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=headers,
        json={"strategy_version_id": stack["version_id"]},
    )
    assert snapshot.status_code == 201, snapshot.text

    queued = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=headers,
        json={"snapshot_id": snapshot.json()["snapshot_id"]},
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    reserved = _snapshot_state(stack["owner_id"])
    assert reserved["reserved"] == 1, "a queued job must hold exactly its unit"

    cancelled = client.post(
        f"/api/v1/training/jobs/{job_id}/cancel", headers=stack["jwt"]
    )
    assert cancelled.status_code == 200, cancelled.text
    released = _snapshot_state(stack["owner_id"])
    assert released["reserved"] == 0
    assert released["used"] == before["used"]

    assert client.get("/api/v1/ml/personal/models", headers=headers).status_code == 200
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/activate",
            headers=headers,
        ).status_code
        == 200
    )

    refused = [
        client.post(
            f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/predict",
            headers=headers,
            json={"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
        ),
        client.post(
            f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
            headers=headers,
            json={"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
        ),
    ]
    for response in refused:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["accepted_scopes"] == ["write", "inference"]

    after = _snapshot_state(stack["owner_id"])
    assert after["used"] == before["used"], "scope refusals must not consume a unit"
    assert after["usage_records"] == before["usage_records"]


def test_write_scope_reaches_every_mutation_but_not_reads(client, stack):
    """``write`` is a mutation scope, matching the rest of the key API.

    A write-only key must be refused on read endpoints; the documented
    ``read`` + ``write`` pair is what reaches the whole surface.
    """
    raw_key, _ = _make_key(client, stack["jwt"], ["write"], "write-matrix")
    headers = _key_headers(raw_key)
    model_version_id = stack["model"]["model_version_id"]
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    before = _snapshot_state(stack["owner_id"])

    for url in ("/api/v1/ml/personal/snapshots", "/api/v1/ml/personal/models"):
        refused = client.get(url, headers=headers)
        assert refused.status_code == 403, f"{url} -> {refused.text}"
        assert refused.json()["detail"]["accepted_scopes"] == [
            "read",
            "inference",
            "training",
        ]
    assert (
        client.get(
            f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", headers=headers
        ).status_code
        == 403
    )

    assert (
        client.post(
            "/api/v1/ml/personal/snapshots/preview",
            headers=headers,
            json={"strategy_version_id": stack["version_id"]},
        ).status_code
        == 200
    )
    snapshot = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=headers,
        json={"strategy_version_id": stack["version_id"]},
    )
    assert snapshot.status_code == 201, snapshot.text
    queued = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=headers,
        json={"snapshot_id": snapshot.json()["snapshot_id"]},
    )
    assert queued.status_code == 202, queued.text
    assert (
        client.post(
            f"/api/v1/training/jobs/{queued.json()['job_id']}/cancel", headers=stack["jwt"]
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{model_version_id}/activate", headers=headers
        ).status_code
        == 200
    )
    for url in (
        f"/api/v1/ml/personal/models/{model_version_id}/predict",
        f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
    ):
        predicted = client.post(url, headers=headers, json=body)
        assert predicted.status_code == 200, predicted.text
        assert predicted.json()["resolved_version"]["model_version_id"] == model_version_id

    after = _snapshot_state(stack["owner_id"])
    assert after["used"] == before["used"] + 2
    assert after["jobs"] == before["jobs"] + 1

    # The documented legacy pair reaches reads as well as writes.
    read_write_raw, _ = _make_key(client, stack["jwt"], ["read", "write"], "rw-matrix")
    rw_headers = _key_headers(read_write_raw)
    assert client.get("/api/v1/ml/personal/snapshots", headers=rw_headers).status_code == 200
    assert client.get("/api/v1/ml/personal/models", headers=rw_headers).status_code == 200


def test_scope_less_key_is_refused_on_every_endpoint(client, stack):
    raw_key, _ = _make_key(client, stack["jwt"], [], "no-scope-matrix")
    headers = _key_headers(raw_key)
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    calls = [
        ("post", "/api/v1/ml/personal/snapshots/preview", {"strategy_version_id": stack["version_id"]}),
        ("post", "/api/v1/ml/personal/snapshots", {"strategy_version_id": stack["version_id"]}),
        ("get", "/api/v1/ml/personal/snapshots", None),
        ("get", f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", None),
        ("post", "/api/v1/ml/personal/training-jobs", {"snapshot_id": stack["snapshot_id"]}),
        ("get", "/api/v1/ml/personal/models", None),
        ("post", f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/activate", None),
        ("post", f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/predict", body),
        ("post", f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict", body),
    ]
    for method, url, payload in calls:
        response = (
            getattr(client, method)(url, headers=headers, json=payload)
            if payload
            else getattr(client, method)(url, headers=headers)
        )
        assert response.status_code == 403, f"{method.upper()} {url} -> {response.status_code}"
        assert response.json()["detail"]["held_scopes"] == []


def test_revoked_key_is_rejected_on_every_endpoint(client, stack):
    raw_key, key_id = _make_key(client, stack["jwt"], ["write"], "revoked-matrix")
    headers = _key_headers(raw_key)
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/activate",
            headers=headers,
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/api-keys/{key_id}", headers=stack["jwt"]).status_code == 204

    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}
    calls = [
        ("post", "/api/v1/ml/personal/snapshots/preview", {"strategy_version_id": stack["version_id"]}),
        ("post", "/api/v1/ml/personal/snapshots", {"strategy_version_id": stack["version_id"]}),
        ("get", "/api/v1/ml/personal/snapshots", None),
        ("get", f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", None),
        ("post", "/api/v1/ml/personal/training-jobs", {"snapshot_id": stack["snapshot_id"]}),
        ("get", "/api/v1/ml/personal/models", None),
        ("post", f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/activate", None),
        ("post", f"/api/v1/ml/personal/models/{stack['model']['model_version_id']}/predict", body),
        ("post", f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict", body),
    ]
    for method, url, payload in calls:
        response = (
            getattr(client, method)(url, headers=headers, json=payload)
            if payload
            else getattr(client, method)(url, headers=headers)
        )
        assert response.status_code == 401, f"{method.upper()} {url} -> {response.status_code}"


# ── 3. Refusals leave no product side effects ─────────────────────────


def test_refusals_consume_no_usage_and_create_nothing(client, stack):
    before = _snapshot_state(stack["owner_id"])
    artifacts_before = _artifact_count(stack["artifacts"])
    model_version_id = stack["model"]["model_version_id"]
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}

    read_raw, _ = _make_key(client, stack["jwt"], ["read"], "refusal-read")
    training_raw, _ = _make_key(client, stack["jwt"], ["training"], "refusal-training")
    other = _second_account(client)
    other_write_raw, _ = _make_key(client, other, ["write"], "refusal-other")

    attempts = [
        # scope refusals
        client.post(
            "/api/v1/ml/personal/snapshots",
            headers=_key_headers(read_raw),
            json={"strategy_version_id": stack["version_id"]},
        ),
        client.post(
            "/api/v1/ml/personal/training-jobs",
            headers=_key_headers(read_raw),
            json={"snapshot_id": stack["snapshot_id"]},
        ),
        client.post(
            f"/api/v1/ml/personal/models/{model_version_id}/predict",
            headers=_key_headers(training_raw),
            json=body,
        ),
        client.post(
            f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
            headers=_key_headers(training_raw),
            json=body,
        ),
        # ownership refusals with a fully scoped credential
        client.get(
            f"/api/v1/ml/personal/snapshots/{stack['snapshot_id']}", headers=other
        ),
        client.post(
            f"/api/v1/ml/personal/models/{model_version_id}/activate", headers=other
        ),
        client.post(
            f"/api/v1/ml/personal/models/{model_version_id}/predict",
            headers=other,
            json=body,
        ),
        client.post(
            f"/api/v1/ml/personal/records/{stack['model']['record_id']}/predict",
            headers=other,
            json=body,
        ),
        client.post(
            "/api/v1/ml/personal/training-jobs",
            headers=other,
            json={"snapshot_id": stack["snapshot_id"]},
        ),
    ]
    statuses = [response.status_code for response in attempts]
    assert statuses[0] == 403, attempts[0].text
    assert statuses[1] == 403, attempts[1].text
    assert statuses[2] == 403, attempts[2].text
    assert statuses[3] == 403, attempts[3].text
    assert statuses[4] == 404, attempts[4].text
    assert statuses[5] == 404, attempts[5].text
    assert statuses[6] == 404, attempts[6].text
    assert statuses[7] == 404, attempts[7].text
    assert statuses[8] == 400, attempts[8].text
    assert attempts[8].json()["detail"]["code"] == "snapshot_not_found"

    # The other owner's write key can still do its own work: the refusals above
    # were ownership, not a blanket scope ceiling.
    own = client.post(
        "/api/v1/ml/personal/snapshots",
        headers=_key_headers(other_write_raw),
        json={"strategy_version_id": stack["version_id"]},
    )
    assert own.status_code == 400, own.text
    assert own.json()["detail"]["code"] == "strategy_version_not_found"

    after = _snapshot_state(stack["owner_id"])
    assert after == before, f"refusals changed product state: {before} -> {after}"
    assert _artifact_count(stack["artifacts"]) == artifacts_before


def test_other_owner_sees_empty_lists_and_cannot_predict(client, stack):
    other = _second_account(client)
    assert client.get("/api/v1/ml/personal/models", headers=other).json() == {"models": []}
    assert client.get("/api/v1/ml/personal/snapshots", headers=other).json() == {
        "snapshots": []
    }
    assert (
        client.post(
            "/api/v1/ml/personal/models/{}/activate".format(
                stack["model"]["model_version_id"]
            ),
            headers=other,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/ml/personal/records/{}/predict".format(stack["model"]["record_id"]),
            headers=other,
            json={"instrument_id": INSTRUMENT, "as_of_date": AS_OF},
        ).status_code
        == 404
    )


# ── 4. Active alias resolves an immutable version, before and after ───


def test_active_alias_is_traceable_across_a_version_switch(client, stack):
    jwt_headers = stack["jwt"]
    owner_id = stack["owner_id"]
    write_raw, _ = _make_key(client, jwt_headers, ["write"], "alias-write")
    headers = _key_headers(write_raw)

    first = stack["model"]
    record_id = first["record_id"]
    body = {"instrument_id": INSTRUMENT, "as_of_date": AS_OF}

    assert (
        client.post(
            f"/api/v1/ml/personal/models/{first['model_version_id']}/activate",
            headers=jwt_headers,
        ).status_code
        == 200
    )
    served_before = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict", headers=headers, json=body
    )
    assert served_before.status_code == 200, served_before.text
    resolved_before = served_before.json()["resolved_version"]
    assert resolved_before["model_version_id"] == first["model_version_id"]
    assert resolved_before["version_number"] == first["version_number"]
    assert resolved_before["resolved_from_active_alias"] is True
    assert served_before.json()["artifact_sha256"] == first["artifact_sha256"]

    # Second version: one label excluded, new snapshot, new training job.
    review = client.get(
        f"/api/v1/decision-review?strategy_version_id={stack['version_id']}",
        headers=jwt_headers,
    )
    assert review.status_code == 200, review.text
    event_id = review.json()["decisions"][0]["decision_event_id"]
    excluded = client.post(
        f"/api/v1/decision-review/{event_id}/labels",
        headers=jwt_headers,
        json={"state": "excluded", "exclusion_reason": "outside_strategy"},
    )
    assert excluded.status_code == 201, excluded.text
    second_snapshot = _build_snapshot(client, jwt_headers, stack["version_id"])
    second_job = client.post(
        "/api/v1/ml/personal/training-jobs",
        headers=headers,
        json={"snapshot_id": second_snapshot["snapshot_id"]},
    )
    assert second_job.status_code == 202, second_job.text
    _run_worker_job(uuid.UUID(second_job.json()["job_id"]))

    models = client.get("/api/v1/ml/personal/models", headers=jwt_headers).json()[
        "models"
    ]
    assert len(models) == 2, models
    second = next(
        item for item in models if item["model_version_id"] != first["model_version_id"]
    )
    assert (
        client.post(
            f"/api/v1/ml/personal/models/{second['model_version_id']}/activate",
            headers=headers,
        ).status_code
        == 200
    )
    served_after = client.post(
        f"/api/v1/ml/personal/records/{record_id}/predict", headers=headers, json=body
    )
    assert served_after.status_code == 200, served_after.text
    resolved_after = served_after.json()["resolved_version"]
    assert resolved_after["model_version_id"] == second["model_version_id"]
    assert resolved_after["version_number"] == second["version_number"]
    assert resolved_after["resolved_from_active_alias"] is True
    assert served_after.json()["snapshot"]["snapshot_id"] == second_snapshot["snapshot_id"]
    assert served_after.json()["artifact_sha256"] == second["artifact_sha256"]

    db = TestSession()
    try:
        latest = (
            db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .order_by(UsageRecord.created_at.desc())
            .first()
        )
        assert latest.metadata_json["model_version_id"] == second["model_version_id"]
        assert latest.metadata_json["snapshot_id"] == second_snapshot["snapshot_id"]
        assert latest.metadata_json["training_job_id"] == second_job.json()["job_id"]
    finally:
        db.close()
