"""Integration tests: ML training through the durable training worker.

Covers the owner-directive item 1 requirements:
- /ml/train enqueues (no sync execution in the HTTP request)
- Worker claims ML jobs, executes, settles/releases the quota reservation
- Restart recovery: stale running jobs are reclaimed and rerun
- predict-latest uses the latest available data (no hardcoded 2024 window)
- predict windows prefer stored data over the provider
- Artifact created by the worker is reloadable by its owner
"""

from datetime import date, timedelta
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient

from app.lib.timeutil import utcnow
from app.models.user import User
from app.models.training import TrainingJob, TrainingJobStatus
from app.models.quota import QuotaCounter, QuotaReservation


def _drain_worker(max_jobs=5):
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


def _make_provider_bars(n=200, end_date="2025-06-30", base_price=100.0):
    import random

    rng = random.Random(42)
    bars = []
    price = base_price
    end = date.fromisoformat(end_date)
    for i in range(n):
        d = end - timedelta(days=n - 1 - i)
        close = round(price * (1 + rng.gauss(0.001, 0.02)), 4)
        bars.append(SimpleNamespace(
            date=d.isoformat(),
            open=round(price, 4),
            high=round(max(price, close) * 1.005, 4),
            low=round(min(price, close) * 0.995, 4),
            close=close,
            volume=int(rng.uniform(500000, 2000000)),
        ))
        price = close
    return bars


class _FakeProvider:
    provider_id = "fake"
    provider_name = "Fake Provider"
    feed_mode = "offline"

    def __init__(self, bars, range_min="2024-01-02", range_max="2025-06-30"):
        self._bars = bars
        self._range = {"min": range_min, "max": range_max}
        self.bars_calls = []

    def get_date_range(self):
        return {
            "min": self._range["min"],
            "max": self._range["max"],
            "total_trading_days": 250,
            "real_instruments_range": dict(self._range),
        }

    def get_bars(self, instrument_id, start_date, end_date):
        self.bars_calls.append((instrument_id, start_date, end_date))
        return list(self._bars)

    def get_data_quality(self, instrument_id):
        return {"data_nature": "synthetic", "adjustment": "not_applicable"}


class TestWorkerQuotaLifecycle:
    """Quota reserve (API) → settle (worker, success) / release (worker, failure)."""

    def _get_user_id(self):
        from tests.conftest import TestSession
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            return user.id
        finally:
            db.close()

    def _train(self, client, auth_headers, qs="instrument_id=US:AAPL&model_type=baseline&start_date=2024-01-02&end_date=2024-12-31"):
        resp = client.post(f"/api/v1/ml/train?{qs}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _used_count(self, user_id):
        from tests.conftest import TestSession
        db = TestSession()
        try:
            row = db.query(QuotaCounter).filter(
                QuotaCounter.user_id == user_id,
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            ).first()
            return row.used if row else 0
        finally:
            db.close()

    def _reservation(self, user_id):
        from tests.conftest import TestSession
        db = TestSession()
        try:
            return db.query(QuotaReservation).filter(
                QuotaReservation.user_id == user_id,
                QuotaReservation.quota_type == "ai_usage",
            ).order_by(QuotaReservation.created_at.desc()).first()
        finally:
            db.close()

    def test_enqueue_creates_pending_job_and_reservation(self, client, auth_headers):
        data = self._train(client, auth_headers)

        assert data["status"] == "pending"
        user_id = self._get_user_id()

        from tests.conftest import TestSession
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job is not None
            assert job.status == TrainingJobStatus.pending
            assert job.strategy_spec_snapshot["training_type"] == "ml_model"
            assert job.strategy_spec_snapshot["quota_reservation_id"]
        finally:
            db.close()

        reservation = self._reservation(user_id)
        assert reservation is not None
        assert reservation.status == "reserved"

    def test_worker_completes_ml_job_and_settles_quota(self, client, auth_headers):
        data = self._train(client, auth_headers)
        user_id = self._get_user_id()

        assert self._used_count(user_id) == 0
        _drain_worker()

        from tests.conftest import TestSession
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job.status == TrainingJobStatus.completed, job.error_details
        finally:
            db.close()

        reservation = self._reservation(user_id)
        assert reservation.status == "settled"
        assert reservation.settled_at is not None
        assert self._used_count(user_id) == 1

    def test_worker_releases_quota_on_training_failure(self, client, auth_headers, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("simulated training crash")

        monkeypatch.setattr("app.services.ml_training.train_model", boom)

        data = self._train(client, auth_headers)
        user_id = self._get_user_id()

        _drain_worker()

        from tests.conftest import TestSession
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job.status == TrainingJobStatus.failed
            assert "simulated training crash" in job.error_details
        finally:
            db.close()

        reservation = self._reservation(user_id)
        assert reservation.status == "released"
        assert self._used_count(user_id) == 0

    def test_reclaim_stale_running_job_after_restart(self, client, auth_headers):
        data = self._train(client, auth_headers)

        from app.workers.training_worker import (
            claim_pending_job,
            reclaim_stale_running_jobs,
        )
        from tests.conftest import TestSession

        # Simulate a worker claiming the job, then crashing before execution.
        db = TestSession()
        try:
            claimed = claim_pending_job(db)
            assert claimed is not None
            assert str(claimed.id) == data["job_id"]
            assert claimed.status == TrainingJobStatus.running
        finally:
            db.close()

        # Fresh claim must NOT be reclaimed.
        db = TestSession()
        try:
            assert reclaim_stale_running_jobs(db, stale_after_seconds=900) == 0
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job.status == TrainingJobStatus.running
        finally:
            db.close()

        # Age the claim past the staleness window (crash aftermath).
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            spec = dict(job.strategy_spec_snapshot)
            spec["claimed_at"] = (utcnow() - timedelta(seconds=1200)).isoformat()
            job.strategy_spec_snapshot = spec
            db.commit()
        finally:
            db.close()

        db = TestSession()
        try:
            reclaimed = reclaim_stale_running_jobs(db, stale_after_seconds=900)
            assert reclaimed == 1
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job.status == TrainingJobStatus.pending
            assert "reclaimed_at" in job.strategy_spec_snapshot
        finally:
            db.close()

        # A restarted worker picks the job up and completes it.
        _drain_worker()

        user_id = self._get_user_id()
        db = TestSession()
        try:
            job = db.query(TrainingJob).filter(TrainingJob.id == uuid.UUID(data["job_id"])).first()
            assert job.status == TrainingJobStatus.completed, job.error_details
        finally:
            db.close()
        reservation = self._reservation(user_id)
        assert reservation.status == "settled"


class TestLatestDataWindows:
    """predict-latest / prediction windows must use latest available data."""

    def test_latest_end_date_from_provider_range(self):
        from app.services.ml_training import _latest_available_end_date
        from tests.conftest import TestSession

        fake = _FakeProvider(_make_provider_bars(), range_max="2027-03-15")
        db = TestSession()
        try:
            end = _latest_available_end_date(db, fake, "US:NOSTORE")
        finally:
            db.close()
        assert end == date(2027, 3, 15)

    def test_recent_bars_window_ends_at_latest(self, monkeypatch):
        from app.services.ml_training import get_recent_bars_for_prediction
        from tests.conftest import TestSession

        fake = _FakeProvider(_make_provider_bars(n=120), range_max="2027-03-15")
        monkeypatch.setattr("app.services.ml_training.get_data_provider", lambda: fake)
        db = TestSession()
        try:
            bars, source = get_recent_bars_for_prediction(db, "US:NOSTORE")
        finally:
            db.close()

        assert source == "provider"
        assert len(bars) >= 30
        (inst, start, end) = fake.bars_calls[-1]
        assert end == "2027-03-15"
        assert (date.fromisoformat(end) - date.fromisoformat(start)).days == 380

    def test_recent_bars_prefers_stored_data(self):
        from app.services.market_data_storage import MarketDataStorage
        from app.services.ml_training import get_recent_bars_for_prediction
        from tests.conftest import TestSession

        stored_bars = _make_provider_bars(n=80, end_date="2025-08-15")
        db = TestSession()
        try:
            MarketDataStorage(db).store_bars(
                instrument_id="US:STORED",
                provider_id="fake",
                bars=stored_bars,
                data_nature="synthetic",
                adjustment_status="not_applicable",
            )
            bars, source = get_recent_bars_for_prediction(db, "US:STORED")
        finally:
            db.close()

        assert source == "stored"
        assert len(bars) >= 30

    def test_predict_latest_api_uses_worker_trained_model(self, client, auth_headers, monkeypatch):
        fake = _FakeProvider(_make_provider_bars(n=200, end_date="2025-06-30"))
        monkeypatch.setattr("app.services.ml_training.get_data_provider", lambda: fake)

        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:FAKE&model_type=baseline&start_date=2025-01-02&end_date=2025-06-30",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        _drain_worker()

        detail = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        assert detail.status_code == 200
        job = detail.json()
        assert job["status"] == "completed", job.get("error_details")
        model_id = job["best_params"]["model_id"]

        resp = client.post(f"/api/v1/ml/models/{model_id}/predict-latest", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert "latest_prediction" in resp.json()


class TestArtifactReload:
    """Model artifact produced by the worker must be reloadable by its owner."""

    def test_worker_artifact_loads_for_owner(self, client, auth_headers):
        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline&start_date=2024-01-02&end_date=2024-12-31",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        _drain_worker()

        from app.ml.trainer import load_model, list_models

        detail = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        model_id = detail.json()["best_params"]["model_id"]

        model, scaler, metadata, feature_names = load_model(
            model_id, user_id=str(self._owner_id(client, auth_headers))
        )
        assert metadata["instrument_id"] == "US:AAPL"
        assert feature_names

        models = list_models(user_id=str(self._owner_id(client, auth_headers)))
        assert model_id in [m["model_id"] for m in models]

    def _owner_id(self, client, auth_headers):
        from tests.conftest import TestSession
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == "test@alethra.io").first()
            return user.id
        finally:
            db.close()
