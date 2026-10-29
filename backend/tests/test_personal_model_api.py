"""Personal model API: domain-scope access, ownership and revocation.

The key UI offers domain scopes (``inference``, ``training``) rather than the
blanket ``read``/``write`` pair. Before this slice those names were stored on
the key but rejected by every model route, so a key minted for model inference
could not call a single endpoint. These tests drive the real HTTP routes with
real keys:

* an ``inference`` key can list, read and predict, and the prediction is
  metered and recorded as coming from an API key;
* a ``training`` key can queue, poll and cancel training but cannot predict;
* a ``read`` or scope-less key is refused on writes;
* ownership, revocation and key material survive independently of scopes.

JWT sessions stay unrestricted; the browser UI authenticates with a JWT and
would break if a scope ceiling leaked onto it.
"""

import pytest

from app.config import get_settings

TRAIN_QS = (
    "instrument_id=US:AAPL&model_type=baseline"
    "&start_date=2024-01-02&end_date=2024-12-31"
)

PREDICT_URL = "/api/v1/ml/models/{model_id}/predict-latest"
TRAIN_URL = f"/api/v1/ml/train?{TRAIN_QS}"


# ── helpers ───────────────────────────────────────────────────────────


def _register_and_login(client, email):
    client.post("/api/auth/register", json={
        "email": email,
        "password": "testpass123",
        "display_name": email.split("@")[0],
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    # The login body carries the access token, so only the status is asserted.
    login_status = login.status_code
    assert login_status == 200, f"login for {email} -> {login_status}"
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _make_key(client, jwt_headers, name, scopes):
    resp = client.post(
        "/api/v1/api-keys", json={"name": name, "scopes": scopes}, headers=jwt_headers
    )
    # The creation body carries the raw key — the one value that must not leak.
    create_status = resp.status_code
    assert create_status == 201, f"create key {name!r} -> {create_status}"
    body = resp.json()
    return body["raw_key"], body["id"]


def _key_headers(raw_key):
    return {"Authorization": f"Bearer {raw_key}"}


def _user_id(jwt_headers):
    from jose import jwt

    token = jwt_headers["Authorization"].split(" ", 1)[1]
    payload = jwt.decode(
        token, get_settings().jwt_secret_key, algorithms=[get_settings().jwt_algorithm]
    )
    return payload["sub"]


def _run_worker_until_done(max_jobs=5):
    from app.workers.training_worker import claim_pending_job, run_claimed_job
    from tests.conftest import TestSession

    for _ in range(max_jobs):
        db = TestSession()
        try:
            job = claim_pending_job(db)
            if job is None:
                break
            job_id = job.id
        finally:
            db.close()

        db = TestSession()
        try:
            run_claimed_job(db, job_id)
        finally:
            db.close()


def _train_model(client, jwt_headers):
    resp = client.post(TRAIN_URL, headers=jwt_headers)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    _run_worker_until_done()

    detail = client.get(f"/api/v1/training/jobs/{job_id}", headers=jwt_headers)
    assert detail.status_code == 200, detail.text
    job = detail.json()
    assert job["status"] == "completed", job
    return job["best_params"]["model_id"]


def _usage_records(user_id):
    from uuid import UUID

    from app.models.billing import UsageRecord
    from tests.conftest import TestSession

    db = TestSession()
    try:
        return (
            db.query(UsageRecord)
            .filter(UsageRecord.user_id == UUID(user_id))
            .order_by(UsageRecord.created_at.desc())
            .all()
        )
    finally:
        db.close()


@pytest.fixture()
def owner(client):
    return _register_and_login(client, "personal-model-api@alethra.io")


@pytest.fixture()
def trained_model(client, owner):
    model_id = _train_model(client, owner)
    user_id = _user_id(owner)
    yield {"model_id": model_id, "headers": owner, "user_id": user_id}

    # Artifacts live outside the test DB; remove the one this test created.
    from app.ml.trainer import delete_model

    try:
        delete_model(model_id, user_id=user_id)
    except FileNotFoundError:
        pass


class TestInferenceScope:
    def test_inference_key_lists_reads_and_predicts(self, client, trained_model):
        model_id = trained_model["model_id"]
        jwt_headers = trained_model["headers"]
        raw_key, _ = _make_key(client, jwt_headers, "inference", ["inference"])
        headers = _key_headers(raw_key)

        listed = client.get("/api/v1/ml/models", headers=headers)
        listed_status = listed.status_code
        assert listed_status == 200, listed.text
        assert model_id in [m["model_id"] for m in listed.json()["models"]]

        detail = client.get(f"/api/v1/ml/models/{model_id}", headers=headers)
        detail_status = detail.status_code
        assert detail_status == 200, detail.text
        assert detail.json()["model_id"] == model_id

        before = client.get("/api/v1/ml/entitlement", headers=jwt_headers).json()["used"]

        predicted = client.post(PREDICT_URL.format(model_id=model_id), headers=headers)
        predict_status = predicted.status_code
        assert predict_status == 200, predicted.text
        assert "latest_prediction" in predicted.json()

        after = client.get("/api/v1/ml/entitlement", headers=jwt_headers).json()["used"]
        assert after == before + 1, "a programmatic prediction must consume exactly one unit"

        latest = _usage_records(trained_model["user_id"])[0]
        assert latest.metadata_json["prediction_type"] == "predict-latest"
        assert latest.metadata_json["via"] == "api_key", (
            "usage must record that the call came from a key, not the browser"
        )

    def test_write_scope_still_reaches_prediction(self, client, trained_model):
        model_id = trained_model["model_id"]
        raw_key, _ = _make_key(client, trained_model["headers"], "legacy-writer", ["read", "write"])

        predicted = client.post(
            PREDICT_URL.format(model_id=model_id), headers=_key_headers(raw_key)
        )
        predict_status = predicted.status_code
        assert predict_status == 200, predicted.text

    def test_training_key_can_read_but_cannot_predict(self, client, trained_model):
        model_id = trained_model["model_id"]
        raw_key, _ = _make_key(client, trained_model["headers"], "training-only", ["training"])
        headers = _key_headers(raw_key)

        list_status = client.get("/api/v1/ml/models", headers=headers).status_code
        assert list_status == 200

        refused = client.post(PREDICT_URL.format(model_id=model_id), headers=headers)
        refused_status = refused.status_code
        assert refused_status == 403, refused.text
        detail = refused.json()["detail"]
        assert detail["error"] == "insufficient_scope"
        assert detail["required_scope"] == "write"
        assert detail["accepted_scopes"] == ["write", "inference"]
        assert detail["held_scopes"] == ["training"]

    def test_read_key_cannot_predict(self, client, trained_model):
        model_id = trained_model["model_id"]
        raw_key, _ = _make_key(client, trained_model["headers"], "read-only", ["read"])

        refused = client.post(
            PREDICT_URL.format(model_id=model_id), headers=_key_headers(raw_key)
        )
        refused_status = refused.status_code
        assert refused_status == 403, refused.text
        assert refused.json()["detail"]["accepted_scopes"] == ["write", "inference"]

    def test_scope_less_key_is_refused_even_on_reads(self, client, trained_model):
        raw_key, _ = _make_key(client, trained_model["headers"], "no-scopes", [])

        refused = client.get("/api/v1/ml/models", headers=_key_headers(raw_key))
        refused_status = refused.status_code
        assert refused_status == 403, refused.text
        detail = refused.json()["detail"]
        assert detail["held_scopes"] == []
        assert detail["accepted_scopes"] == ["read", "inference", "training"]


class TestTrainingScope:
    def test_training_key_can_queue_poll_and_cancel(self, client, owner):
        raw_key, _ = _make_key(client, owner, "trainer", ["training"])
        headers = _key_headers(raw_key)

        queued = client.post(TRAIN_URL, headers=headers)
        queued_status = queued.status_code
        assert queued_status == 200, queued.text
        job_id = queued.json()["job_id"]
        assert queued.json()["status"] == "pending"

        polled = client.get(f"/api/v1/training/jobs/{job_id}", headers=headers)
        poll_status = polled.status_code
        assert poll_status == 200, polled.text
        assert polled.json()["status"] == "pending"

        # The unit is reserved while the job waits for the worker.
        quota = client.get("/api/v1/ml/entitlement", headers=owner).json()
        assert quota["used"] == 0
        assert quota["reserved"] == 1

        cancelled = client.post(f"/api/v1/training/jobs/{job_id}/cancel", headers=headers)
        cancel_status = cancelled.status_code
        assert cancel_status == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        released = client.get("/api/v1/ml/entitlement", headers=owner).json()
        assert released["used"] == 0
        assert released["reserved"] == 0, "cancelling a queued job must return its unit"

    def test_inference_key_cannot_queue_or_cancel_training(self, client, owner):
        raw_key, _ = _make_key(client, owner, "inference-only", ["inference"])
        headers = _key_headers(raw_key)

        queued = client.post(TRAIN_URL, headers=headers)
        queue_status = queued.status_code
        assert queue_status == 403, queued.text
        assert queued.json()["detail"]["accepted_scopes"] == ["write", "training"]

        sweep = client.post(
            "/api/v1/training/jobs",
            json={"experiment_id": "11111111-1111-4111-8111-111111111111", "parameter_grid": {}},
            headers=headers,
        )
        sweep_status = sweep.status_code
        assert sweep_status == 403, sweep.text

        cancel = client.post(
            "/api/v1/training/jobs/22222222-2222-4222-8222-222222222222/cancel",
            headers=headers,
        )
        cancel_status = cancel.status_code
        assert cancel_status == 403, cancel.text

    def test_job_listing_requires_a_read_capable_scope(self, client, owner):
        raw_key, _ = _make_key(client, owner, "no-scopes-trainer", [])

        refused = client.get("/api/v1/training/jobs", headers=_key_headers(raw_key))
        refused_status = refused.status_code
        assert refused_status == 403, refused.text
        assert refused.json()["detail"]["accepted_scopes"] == ["read", "training", "inference"]


class TestOwnershipRevocationAndLeaks:
    def test_another_users_inference_key_cannot_reach_the_model(self, client, trained_model):
        model_id = trained_model["model_id"]

        other = _register_and_login(client, "personal-model-other@alethra.io")
        other_key, _ = _make_key(client, other, "other-inference", ["inference"])
        other_headers = _key_headers(other_key)

        predicted = client.post(PREDICT_URL.format(model_id=model_id), headers=other_headers)
        predict_status = predicted.status_code
        assert predict_status == 403, "a valid key must still be stopped by ownership"

        detail = client.get(f"/api/v1/ml/models/{model_id}", headers=other_headers)
        detail_status = detail.status_code
        assert detail_status == 403

        listed = client.get("/api/v1/ml/models", headers=other_headers)
        listed_status = listed.status_code
        assert listed_status == 200
        assert model_id not in [m["model_id"] for m in listed.json()["models"]]

    def test_a_revoked_inference_key_is_rejected(self, client, trained_model):
        model_id = trained_model["model_id"]
        jwt_headers = trained_model["headers"]
        raw_key, key_id = _make_key(client, jwt_headers, "doomed-inference", ["inference"])
        headers = _key_headers(raw_key)

        before_status = client.get("/api/v1/ml/models", headers=headers).status_code
        assert before_status == 200

        revoke_status = client.delete(f"/api/v1/api-keys/{key_id}", headers=jwt_headers).status_code
        assert revoke_status == 204

        read_status = client.get("/api/v1/ml/models", headers=headers).status_code
        assert read_status == 401
        predict_status = client.post(
            PREDICT_URL.format(model_id=model_id), headers=headers
        ).status_code
        assert predict_status == 401

    def test_the_refusal_never_echoes_the_key_it_refused(self, client, trained_model):
        model_id = trained_model["model_id"]
        raw_key, _ = _make_key(client, trained_model["headers"], "leak-probe", ["read"])

        refused = client.post(PREDICT_URL.format(model_id=model_id), headers=_key_headers(raw_key))
        assert refused.status_code == 403

        # Bound to locals first: pytest rewrites assertions by repr-ing their
        # operands, and an operand holding the raw key would print it into the log.
        in_body = raw_key in refused.text
        in_headers = any(raw_key in str(value) for value in refused.headers.values())
        in_detail = raw_key in str(refused.json()["detail"])
        assert not in_body, "the raw key must not appear in a 403 body"
        assert not in_headers, "the raw key must not appear in a 403 header"
        assert not in_detail, "the raw key must not appear in a 403 detail"

    def test_a_jwt_session_keeps_full_access_and_no_key_budget(self, client, trained_model):
        model_id = trained_model["model_id"]
        headers = trained_model["headers"]

        predicted = client.post(PREDICT_URL.format(model_id=model_id), headers=headers)
        predict_status = predicted.status_code
        assert predict_status == 200, predicted.text
        assert "x-ratelimit-remaining" not in {k.lower() for k in predicted.headers}


class TestScopeDependency:
    def _request_with(self, scopes):
        from types import SimpleNamespace

        return SimpleNamespace(state=SimpleNamespace(api_key_scopes=scopes))

    def test_an_accepted_domain_scope_passes(self):
        from app.auth.api_keys import check_api_key_scope

        request = self._request_with(["inference"])
        assert check_api_key_scope("write", "inference")(request) is request

    def test_a_held_scope_outside_the_accepted_set_is_refused_with_the_list(self):
        from fastapi import HTTPException

        from app.auth.api_keys import check_api_key_scope

        request = self._request_with(["training"])
        with pytest.raises(HTTPException) as exc_info:
            check_api_key_scope("write", "inference")(request)
        detail = exc_info.value.detail
        assert detail["accepted_scopes"] == ["write", "inference"]
        assert detail["held_scopes"] == ["training"]

    def test_a_jwt_request_is_never_scope_checked(self):
        from app.auth.api_keys import check_api_key_scope

        request = self._request_with(None)
        assert check_api_key_scope("write", "training")(request) is request


class TestPerApiKeyRateLimit:
    LIMIT = get_settings().api_key_rate_limit_requests

    def test_a_model_key_is_refused_once_its_budget_is_spent(self, client, trained_model):
        raw_key, _ = _make_key(client, trained_model["headers"], "budgeted-reader", ["inference"])
        headers = _key_headers(raw_key)

        statuses = [client.get("/api/v1/ml/models", headers=headers).status_code
                    for _ in range(self.LIMIT)]
        assert statuses == [200] * self.LIMIT

        exhausted = client.get("/api/v1/ml/models", headers=headers)
        exhausted_status = exhausted.status_code
        assert exhausted_status == 429
        assert exhausted.headers["X-RateLimit-Remaining"] == "0"

        other_key, _ = _make_key(client, trained_model["headers"], "second-reader", ["inference"])
        other = client.get("/api/v1/ml/models", headers=_key_headers(other_key))
        other_status = other.status_code
        assert other_status == 200, "budgets are per key, not per user"
        assert other.headers["X-RateLimit-Remaining"] == str(self.LIMIT - 1)
