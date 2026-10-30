"""Idempotent training-job creation on both training endpoints.

A retried POST (network retry, double click, a script that re-runs) must not
queue a second job or reserve a second unit of ``ai_usage``. Reusing a key for
a different payload is a client error, not a second job.

These tests exercise the real endpoints and read the database directly, so a
response that merely repeats an id is not enough: the job count and the
reservation count are asserted too.
"""

import uuid

import pytest

from app.models.market_data import MarketDataMetadata
from app.models.quota import QuotaReservation
from app.models.training import TrainingJob
from app.models.user import User
from tests.conftest import TestSession
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _seed_bars,
    _seed_confirmed_decisions,
    _user_id,
)


def _jobs_with_key(user_id, key):
    db = TestSession()
    try:
        rows = (
            db.query(TrainingJob)
            .filter(TrainingJob.user_id == user_id)
            .all()
        )
        return [
            job.id
            for job in rows
            if (job.strategy_spec_snapshot or {}).get("idempotency_key") == key
        ]
    finally:
        db.close()


def _reservation_count(user_id):
    db = TestSession()
    try:
        return (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.quota_type == "ai_usage",
            )
            .count()
        )
    finally:
        db.close()


class TestMLTrainingIdempotency:
    _QS = (
        "instrument_id=US:AAPL&model_type=baseline"
        "&start_date=2024-01-02&end_date=2024-12-31"
    )

    def test_same_key_and_payload_reuses_job_and_reservation(self, client, auth_headers):
        key = "ml-retry-" + uuid.uuid4().hex
        first = client.post(
            f"/api/v1/ml/train?{self._QS}",
            headers={**auth_headers, "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        user_id = _user_id()

        second = client.post(
            f"/api/v1/ml/train?{self._QS}",
            headers={**auth_headers, "Idempotency-Key": key},
        )
        assert second.status_code == 200, second.text
        assert second.json()["job_id"] == first.json()["job_id"]

        assert len(_jobs_with_key(user_id, key)) == 1
        assert _reservation_count(user_id) == 1

    def test_same_key_different_payload_conflicts(self, client, auth_headers):
        key = "ml-conflict-" + uuid.uuid4().hex
        first = client.post(
            f"/api/v1/ml/train?{self._QS}",
            headers={**auth_headers, "Idempotency-Key": key},
        )
        assert first.status_code == 200, first.text
        user_id = _user_id()

        second = client.post(
            f"/api/v1/ml/train?{self._QS.replace('model_type=baseline', 'model_type=gradient_boosting')}",
            headers={**auth_headers, "Idempotency-Key": key},
        )
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["error"] == "idempotency_conflict"

        assert len(_jobs_with_key(user_id, key)) == 1
        assert _reservation_count(user_id) == 1

    def test_short_key_is_rejected(self, client, auth_headers):
        resp = client.post(
            f"/api/v1/ml/train?{self._QS}",
            headers={**auth_headers, "Idempotency-Key": "short"},
        )
        assert resp.status_code == 400, resp.text
        assert _reservation_count(_user_id()) == 0


@pytest.fixture()
def _personal_dataset(setup_db, client, auth_headers):
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

    owner_id = _user_id()
    _, version_id = _create_experiment(client, auth_headers)
    _seed_confirmed_decisions(owner_id, uuid.UUID(version_id))
    _grant_consent(client, auth_headers, version_id)
    return {"owner_id": owner_id, "version_id": version_id}


class TestPersonalTrainingIdempotency:
    def test_same_key_and_payload_reuses_job_and_reservation(
        self, client, auth_headers, _personal_dataset
    ):
        snapshot = _build_snapshot(client, auth_headers, _personal_dataset["version_id"])
        key = "personal-retry-" + uuid.uuid4().hex
        payload = {"snapshot_id": snapshot["snapshot_id"], "model_type": "logistic_regression"}

        first = client.post(
            "/api/v1/ml/personal/training-jobs",
            headers={**auth_headers, "Idempotency-Key": key},
            json=payload,
        )
        assert first.status_code == 202, first.text

        second = client.post(
            "/api/v1/ml/personal/training-jobs",
            headers={**auth_headers, "Idempotency-Key": key},
            json=payload,
        )
        assert second.status_code == 202, second.text
        assert second.json()["job_id"] == first.json()["job_id"]

        owner_id = _personal_dataset["owner_id"]
        assert len(_jobs_with_key(owner_id, key)) == 1
        assert _reservation_count(owner_id) == 1

    def test_same_key_different_model_type_conflicts(
        self, client, auth_headers, _personal_dataset
    ):
        snapshot = _build_snapshot(client, auth_headers, _personal_dataset["version_id"])
        key = "personal-conflict-" + uuid.uuid4().hex

        first = client.post(
            "/api/v1/ml/personal/training-jobs",
            headers={**auth_headers, "Idempotency-Key": key},
            json={"snapshot_id": snapshot["snapshot_id"], "model_type": "logistic_regression"},
        )
        assert first.status_code == 202, first.text

        second = client.post(
            "/api/v1/ml/personal/training-jobs",
            headers={**auth_headers, "Idempotency-Key": key},
            json={"snapshot_id": snapshot["snapshot_id"], "model_type": "baseline"},
        )
        assert second.status_code == 409, second.text
        assert second.json()["detail"]["error"] == "idempotency_conflict"

        owner_id = _personal_dataset["owner_id"]
        assert len(_jobs_with_key(owner_id, key)) == 1
        assert _reservation_count(owner_id) == 1
