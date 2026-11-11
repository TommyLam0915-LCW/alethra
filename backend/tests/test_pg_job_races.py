"""PostgreSQL-only races in the quota and training-job lane.

SQLite serializes writers, so it can never show the row-lock behaviour these
cases assert. Every test here runs two independent sessions — or two HTTP
requests, each with its own dependency-injected session — against the
disposable PostgreSQL database selected by ``ALETHRA_TEST_DB``, and starts
both sides from a ``threading.Barrier``.

Already proven elsewhere and deliberately not repeated here:

* active-version CAS — ``test_personal_comparison_validity.py::
  test_postgres_two_sessions_block_on_the_record_lock``
* reservation settle/settle, settle/release, counter-creation races —
  ``test_quota_concurrency.py``, which is excluded from the default PostgreSQL
  pass and skips until ``ALETHRA_PG_QUOTA_CONCURRENCY_ENABLE=1`` is set against
  a migration-built dedicated database; on the default pass those cases are
  skipped, not covered
* paper order idempotency and ledger-failure rollback —
  ``test_manual_decision_books_linked_portfolio.py``
* stale-claim reclaim and cancel-after-claim, sequentially —
  ``test_ml_worker_integration.py``
"""

import threading
import uuid

import pytest
from fastapi import HTTPException

from app.auth.entitlements import reserve_quota
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.training import TrainingJob, TrainingJobStatus
from tests.conftest import TEST_ENGINE, TestSession
from tests.test_ml_worker_integration import _drain_worker
from tests.test_personal_strategy_learning import (
    _build_snapshot,
    _seed_bars,
    _user_id,
)
from tests.test_training_idempotency import _personal_dataset  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(
    TEST_ENGINE.dialect.name == "sqlite",
    reason="row-level concurrency requires PostgreSQL (set ALETHRA_TEST_DB)",
)

_QS = (
    "instrument_id=US:AAPL&model_type=baseline"
    "&start_date=2024-01-02&end_date=2024-12-31"
)


def _run_pair(first, second):
    """Run two callables that both wait on one barrier, then join them."""
    barrier = threading.Barrier(2)
    results = {}
    errors = {}

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results[name] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised with context
            errors[name] = exc

    threads = [
        threading.Thread(target=run, args=("a", first)),
        threading.Thread(target=run, args=("b", second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, {name: repr(exc) for name, exc in errors.items()}
    assert len(results) == 2, "both sides must finish"
    return results["a"], results["b"]


def _jobs_for_key(user_id, key):
    db = TestSession()
    try:
        rows = db.query(TrainingJob).filter(TrainingJob.user_id == user_id).all()
        return [
            job.id
            for job in rows
            if (job.strategy_spec_snapshot or {}).get("idempotency_key") == key
        ]
    finally:
        db.close()


def _reservations(user_id):
    db = TestSession()
    try:
        return (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.quota_type == "ai_usage",
            )
            .all()
        )
    finally:
        db.close()


def _used_count(user_id):
    db = TestSession()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            )
            .first()
        )
        return counter.used if counter else 0
    finally:
        db.close()


def _job(user_id, job_id):
    db = TestSession()
    try:
        return (
            db.query(TrainingJob)
            .filter(
                TrainingJob.id == uuid.UUID(str(job_id)),
                TrainingJob.user_id == user_id,
            )
            .first()
        )
    finally:
        db.close()


def _grant_ai_limit(user_id, limit):
    db = TestSession()
    try:
        db.query(PilotEntitlement).filter(
            PilotEntitlement.user_id == user_id
        ).delete()
        db.add(
            PilotEntitlement(
                user_id=user_id,
                ai_usage_limit=limit,
                pilot_access=True,
            )
        )
        db.commit()
    finally:
        db.close()


def _post_train(client, auth_headers, key=None, model_type="baseline"):
    headers = dict(auth_headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    query = _QS.replace("model_type=baseline", f"model_type={model_type}")
    return client.post(f"/api/v1/ml/train?{query}", headers=headers)


@pytest.fixture()
def stored_bars(setup_db, client, auth_headers):
    """Persist bars up front so a concurrent request has nothing to write.

    Without this, both sides of a no-key race would call ``store_bars`` and
    could collide on the bar table — a storage-write race that is not the
    quota race under test.
    """
    db = TestSession()
    try:
        _seed_bars(db)
        db.commit()
    finally:
        db.close()


class TestConcurrentJobCreation:
    """Two simultaneous POSTs must not queue two jobs or reserve twice."""

    def test_same_key_two_requests_create_one_job(self, client, auth_headers):
        key = "pg-race-same-" + uuid.uuid4().hex

        def post():
            resp = _post_train(client, auth_headers, key=key)
            return resp.status_code, resp.json().get("job_id")

        first, second = _run_pair(post, post)

        assert [first[0], second[0]] == [200, 200], (first, second)
        assert first[1] == second[1], "both retries must report the same job"

        user_id = _user_id()
        assert len(_jobs_for_key(user_id, key)) == 1
        assert len(_reservations(user_id)) == 1

    def test_same_key_different_payload_one_wins_one_conflicts(
        self, client, auth_headers
    ):
        key = "pg-race-conflict-" + uuid.uuid4().hex

        def post_baseline():
            resp = _post_train(
                client, auth_headers, key=key, model_type="baseline"
            )
            return resp.status_code, resp.json()

        def post_boosting():
            resp = _post_train(
                client, auth_headers, key=key, model_type="gradient_boosting"
            )
            return resp.status_code, resp.json()

        first, second = _run_pair(post_baseline, post_boosting)

        assert sorted([first[0], second[0]]) == [200, 409], (first, second)
        conflict = first if first[0] == 409 else second
        assert conflict[1]["detail"]["error"] == "idempotency_conflict"

        user_id = _user_id()
        assert len(_jobs_for_key(user_id, key)) == 1
        assert len(_reservations(user_id)) == 1

    def test_last_quota_unit_admits_exactly_one_job(
        self, client, auth_headers, stored_bars
    ):
        """used=0, limit=1, two simultaneous distinct requests: one is queued."""
        user_id = _user_id()
        _grant_ai_limit(user_id, 1)
        db = TestSession()
        try:
            db.add(
                QuotaCounter(
                    user_id=user_id,
                    quota_type="ai_usage",
                    period="lifetime",
                    used=0,
                )
            )
            db.commit()
        finally:
            db.close()

        def post():
            resp = _post_train(client, auth_headers)
            return resp.status_code, resp.json()

        first, second = _run_pair(post, post)

        assert sorted([first[0], second[0]]) == [200, 429], (first, second)
        refusal = first if first[0] == 429 else second
        assert refusal[1]["detail"]["error"] == "quota_exceeded"

        reservations = _reservations(user_id)
        assert len(reservations) == 1
        assert sum(r.amount for r in reservations) == 1
        assert _used_count(user_id) == 0


class TestQuotaLastUnitReservation:
    """reserve_quota itself under a last-unit race, with and without a counter."""

    def _reserve_once(self, user_id):
        def reserve():
            db = TestSession()
            try:
                reservation = reserve_quota(db, user_id, "ai_usage")
                db.commit()
                return "reserved"
            except HTTPException as exc:
                db.rollback()
                return exc.status_code
            finally:
                db.close()

        return reserve

    def test_existing_counter_row_admits_one(self, client, auth_headers):
        user_id = _user_id()
        _grant_ai_limit(user_id, 1)
        db = TestSession()
        try:
            db.add(
                QuotaCounter(
                    user_id=user_id,
                    quota_type="ai_usage",
                    period="lifetime",
                    used=0,
                )
            )
            db.commit()
        finally:
            db.close()

        reserve = self._reserve_once(user_id)
        first, second = _run_pair(reserve, reserve)

        assert sorted([first, second], key=str) == [429, "reserved"], (first, second)
        assert sum(r.amount for r in _reservations(user_id)) == 1

    def test_first_use_without_counter_row_admits_one(self, client, auth_headers):
        """A brand-new payer has no counter row yet; the last unit is still one."""
        user_id = _user_id()
        _grant_ai_limit(user_id, 1)
        db = TestSession()
        try:
            db.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id
            ).delete()
            db.commit()
        finally:
            db.close()

        reserve = self._reserve_once(user_id)
        first, second = _run_pair(reserve, reserve)

        assert sorted([first, second], key=str) == [429, "reserved"], (first, second)
        assert sum(r.amount for r in _reservations(user_id)) == 1


class TestWorkerClaimRace:
    """One pending job, two workers; and a cancel arriving around the claim."""

    def test_two_workers_claim_one_job(self, client, auth_headers):
        resp = _post_train(client, auth_headers)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        user_id = _user_id()

        def claim():
            from app.workers.training_worker import claim_pending_job

            db = TestSession()
            try:
                job = claim_pending_job(db)
                return str(job.id) if job is not None else None
            finally:
                db.close()

        first, second = _run_pair(claim, claim)

        claimed = [value for value in (first, second) if value is not None]
        assert claimed == [job_id], (first, second)

        job = _job(user_id, job_id)
        assert job.status == TrainingJobStatus.running
        spec = job.strategy_spec_snapshot or {}
        assert spec.get("claimed_at"), "the winning claim must stamp claimed_at"

    def test_cancel_racing_claim_never_runs_training(
        self, client, auth_headers, monkeypatch
    ):
        train_calls = []

        def fail_if_trained(*args, **kwargs):
            train_calls.append(kwargs)
            raise AssertionError("train_model must not run for a cancelled job")

        monkeypatch.setattr("app.services.ml_training.train_model", fail_if_trained)

        resp = _post_train(client, auth_headers)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        user_id = _user_id()

        def claim():
            from app.workers.training_worker import claim_pending_job

            db = TestSession()
            try:
                job = claim_pending_job(db)
                return str(job.id) if job is not None else None
            finally:
                db.close()

        def cancel():
            resp = client.post(
                f"/api/v1/training/jobs/{job_id}/cancel", headers=auth_headers
            )
            return resp.status_code

        claimed, cancel_status = _run_pair(claim, cancel)
        assert cancel_status == 200, cancel_status

        # A worker that already claimed the row still runs it; it must notice
        # the cancellation committed by the API process and stop.
        if claimed is not None:
            from app.workers.training_worker import run_claimed_job

            db = TestSession()
            try:
                run_claimed_job(db, uuid.UUID(job_id))
            finally:
                db.close()

        job = _job(user_id, job_id)
        assert job.cancelled_at is not None
        assert job.status == TrainingJobStatus.cancelled, (
            f"cancel lost to the claim: status={job.status} "
            f"error={job.error_details!r}"
        )

        assert train_calls == []
        reservations = _reservations(user_id)
        assert [r.status for r in reservations] == ["released"]
        assert _used_count(user_id) == 0

    def test_worker_skips_a_job_cancelled_between_claim_and_execution(
        self, client, auth_headers, monkeypatch
    ):
        """Fault injection: the API decided 'pending' from a pre-claim read.

        The cancel then committed ``cancelled`` over the claim. The worker's
        later execution step must treat that as terminal instead of raising
        and turning the cancellation into a failure.
        """
        train_calls = []
        monkeypatch.setattr(
            "app.services.ml_training.train_model",
            lambda *args, **kwargs: train_calls.append(kwargs),
        )

        resp = _post_train(client, auth_headers)
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]
        user_id = _user_id()

        from app.workers.training_worker import claim_pending_job, run_claimed_job

        db = TestSession()
        try:
            assert claim_pending_job(db) is not None
        finally:
            db.close()

        # Reproduce the losing interleaving exactly: the cancel endpoint read
        # the row while it was still pending, then committed its status change
        # after the claim had already committed ``running``.
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(job_id)).one()
            job.status = TrainingJobStatus.cancelled
            job.cancelled_at = job.cancelled_at or job.created_at
            job.completed_at = job.created_at
            db.commit()
        finally:
            db.close()

        db = TestSession()
        try:
            run_claimed_job(db, uuid.UUID(job_id))
        finally:
            db.close()

        assert train_calls == []
        job = _job(user_id, job_id)
        assert job.status == TrainingJobStatus.cancelled
        assert job.cancelled_at is not None
        reservations = _reservations(user_id)
        assert [r.status for r in reservations] == ["released"]
        assert _used_count(user_id) == 0


class TestPersonalJobCreationRace:
    """The personal endpoint has the same one-job guarantee for one key."""

    def test_same_key_two_requests_create_one_personal_job(
        self, client, auth_headers, _personal_dataset
    ):
        snapshot = _build_snapshot(
            client, auth_headers, _personal_dataset["version_id"]
        )
        key = "pg-race-personal-" + uuid.uuid4().hex
        payload = {
            "snapshot_id": snapshot["snapshot_id"],
            "model_type": "logistic_regression",
        }

        def post():
            resp = client.post(
                "/api/v1/ml/personal/training-jobs",
                headers={**auth_headers, "Idempotency-Key": key},
                json=payload,
            )
            return resp.status_code, resp.json().get("job_id")

        first, second = _run_pair(post, post)

        assert [first[0], second[0]] == [202, 202], (first, second)
        assert first[1] == second[1], "both retries must report the same job"

        owner_id = _personal_dataset["owner_id"]
        assert len(_jobs_for_key(owner_id, key)) == 1
        assert len(_reservations(owner_id)) == 1


class TestRetryAgainstRunningAndFinishedJobs:
    """A retry after a claim or after completion must reuse, not requeue."""

    def test_retry_while_job_is_claimed_reuses_the_running_job(
        self, client, auth_headers
    ):
        key = "pg-race-running-" + uuid.uuid4().hex
        first = _post_train(client, auth_headers, key=key)
        assert first.status_code == 200, first.text
        job_id = first.json()["job_id"]

        from app.workers.training_worker import claim_pending_job

        db = TestSession()
        try:
            claimed = claim_pending_job(db)
            assert claimed is not None and str(claimed.id) == job_id
        finally:
            db.close()

        retry = _post_train(client, auth_headers, key=key)
        assert retry.status_code == 200, retry.text
        assert retry.json()["job_id"] == job_id
        assert retry.json()["status"] == "running"

        user_id = _user_id()
        assert len(_jobs_for_key(user_id, key)) == 1
        assert len(_reservations(user_id)) == 1
        assert _used_count(user_id) == 0

    def test_retry_after_completion_reuses_the_finished_job(
        self, client, auth_headers
    ):
        key = "pg-race-done-" + uuid.uuid4().hex
        first = _post_train(client, auth_headers, key=key)
        assert first.status_code == 200, first.text
        job_id = first.json()["job_id"]

        _drain_worker()

        job = _job(_user_id(), job_id)
        assert job.status == TrainingJobStatus.completed, job.error_details

        retry = _post_train(client, auth_headers, key=key)
        assert retry.status_code == 200, retry.text
        assert retry.json()["job_id"] == job_id
        assert retry.json()["status"] == "completed"

        user_id = _user_id()
        assert len(_jobs_for_key(user_id, key)) == 1
        reservations = _reservations(user_id)
        assert [r.status for r in reservations] == ["settled"]
        assert _used_count(user_id) == 1
