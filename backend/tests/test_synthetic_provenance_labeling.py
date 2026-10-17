"""Synthetic-data provenance labeling through storage → backtest → ML.

Owner-directive item 5: bars fetched from a synthetic source must carry a
synthetic marker into storage metadata and never be presented as real
market data when consumed by the backtest engine or ML
training/prediction.
"""
import random
from datetime import date, timedelta
from types import SimpleNamespace

from app.engine.backtest import run_backtest
from app.models.user import User
from app.services.market_data_storage import MarketDataStorage


def _synthetic_bars(start="2024-01-02", n=90, base_price=150.0):
    rng = random.Random(7)
    bars = []
    price = base_price
    d0 = date.fromisoformat(start)
    for i in range(n):
        d = d0 + timedelta(days=i)
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


def _store(db, instrument_id, provider_id, data_nature, bars):
    MarketDataStorage(db).store_bars(
        instrument_id=instrument_id,
        provider_id=provider_id,
        bars=bars,
        data_nature=data_nature,
    )


def _test_user_id(db):
    user = db.query(User).filter(User.email == "test@alethra.io").first()
    return user.id


class TestStoredBarsCarrySyntheticMarker:
    def test_demo_sourced_bars_store_synthetic_metadata(self):
        from tests.conftest import TestSession

        db = TestSession()
        try:
            _store(db, "US:PROV1", "demo", "synthetic", _synthetic_bars())
            meta = MarketDataStorage(db).get_metadata("US:PROV1", "demo")
        finally:
            db.close()
        assert meta is not None
        assert meta.data_nature == "synthetic"

    def test_resolve_nature_single_mixed_unknown(self):
        assert MarketDataStorage.resolve_nature(
            {"US:X": {"demo": "synthetic"}}, "US:X", {"demo"}
        ) == "synthetic"
        assert MarketDataStorage.resolve_nature(
            {"US:X": {"demo": "synthetic", "lseg": "live"}}, "US:X", {"demo", "lseg"}
        ) == "mixed"
        assert MarketDataStorage.resolve_nature(
            {}, "US:X", {"demo"}
        ) == "unknown"


class TestBacktestSurfacesDataNature:
    def _spec(self, asset_id, start="2024-01-02", end="2024-04-01"):
        return {
            "template_type": "buy_and_hold",
            "asset_ids": [asset_id],
            "start_date": start,
            "end_date": end,
            "initial_cash": 100000,
        }

    def test_stored_synthetic_bars_labeled_synthetic(self):
        from tests.conftest import TestSession

        db = TestSession()
        try:
            _store(db, "US:PROVBT", "demo", "synthetic", _synthetic_bars())
            result = run_backtest(self._spec("US:PROVBT"), db_session=db, data_source="stored")
        finally:
            db.close()

        assert result["run_status"] == "completed"
        assert result["data_nature"] == {"US:PROVBT": "synthetic"}
        assert any(
            "Synthetic data used" in w and "US:PROVBT" in w for w in result["warnings"]
        )

    def test_live_stored_bars_not_labeled_synthetic(self):
        from tests.conftest import TestSession

        db = TestSession()
        try:
            _store(db, "US:PROVLIVE", "lseg", "live", _synthetic_bars())
            result = run_backtest(self._spec("US:PROVLIVE"), db_session=db, data_source="stored")
        finally:
            db.close()

        assert result["run_status"] == "completed"
        assert result["data_nature"] == {"US:PROVLIVE": "live"}
        assert not any("Synthetic data used" in w for w in result["warnings"])

    def test_mixed_provider_natures_reported_mixed(self):
        from tests.conftest import TestSession

        db = TestSession()
        try:
            _store(db, "US:PROVMIX", "demo", "synthetic", _synthetic_bars(n=40))
            _store(
                db, "US:PROVMIX", "lseg", "live",
                _synthetic_bars(start="2024-03-01", n=40),
            )
            result = run_backtest(self._spec("US:PROVMIX"), db_session=db, data_source="stored")
        finally:
            db.close()

        assert result["run_status"] == "completed"
        assert result["data_nature"]["US:PROVMIX"] == "mixed"

    def test_demo_fallback_labeled_synthetic(self):
        spec = self._spec("DEMO-ALPHA", start="2024-01-02", end="2024-03-01")
        result = run_backtest(spec, data_source="provider")

        assert result["run_status"] == "completed"
        assert result["data_nature"]["DEMO-ALPHA"] == "synthetic"
        assert any("Synthetic data used" in w for w in result["warnings"])


class TestMLTrainingSurfacesDataNature:
    def test_job_spec_records_synthetic_nature_for_stored_bars(
        self, client, auth_headers
    ):
        from tests.conftest import TestSession
        from app.services.ml_training import create_ml_training_job

        db = TestSession()
        try:
            _store(db, "US:PROVML", "demo", "synthetic", _synthetic_bars(n=90))
            user_id = _test_user_id(db)
            job = create_ml_training_job(
                db,
                user_id=user_id,
                experiment_id=None,
                instrument_id="US:PROVML",
                start_date="2024-01-02",
                end_date="2024-04-01",
            )
            spec = dict(job.strategy_spec_snapshot)
        finally:
            db.close()

        assert spec["data_source"] == "stored"
        assert spec["data_nature"] == "synthetic"

    def test_provider_fallback_nature_from_quality(
        self, client, auth_headers, monkeypatch
    ):
        from tests.conftest import TestSession
        from app.services import ml_training as ml

        class _FakeProvider:
            provider_id = "fake"
            provider_name = "Fake Provider"
            feed_mode = "offline"

            def get_date_range(self):
                return {
                    "min": "2024-01-02",
                    "max": "2025-06-30",
                    "real_instruments_range": {"min": "2024-01-02", "max": "2025-06-30"},
                }

            def get_bars(self, instrument_id, start_date, end_date):
                return _synthetic_bars(n=90)

            def get_data_quality(self, instrument_id):
                return {"data_nature": "synthetic", "adjustment": "not_applicable"}

        monkeypatch.setattr(ml, "get_data_provider", lambda: _FakeProvider())

        db = TestSession()
        try:
            user_id = _test_user_id(db)
            job = ml.create_ml_training_job(
                db,
                user_id=user_id,
                experiment_id=None,
                instrument_id="US:PROVFB",
                start_date="2024-01-02",
                end_date="2024-04-01",
            )
            spec = dict(job.strategy_spec_snapshot)
        finally:
            db.close()

        assert spec["data_source"] == "provider"
        assert spec["data_nature"] == "synthetic"


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


class TestPredictionSurfacesDataNature:
    def test_predict_latest_reports_synthetic_nature(self, client, auth_headers, monkeypatch):
        fake_bars = _synthetic_bars(n=200, start="2025-01-02")

        class _FakeProvider:
            provider_id = "fake"
            provider_name = "Fake Provider"
            feed_mode = "offline"

            def get_date_range(self):
                return {
                    "min": "2025-01-02",
                    "max": "2025-06-30",
                    "real_instruments_range": {"min": "2025-01-02", "max": "2025-06-30"},
                }

            def get_bars(self, instrument_id, start_date, end_date):
                return list(fake_bars)

            def get_data_quality(self, instrument_id):
                return {"data_nature": "synthetic", "adjustment": "not_applicable"}

        monkeypatch.setattr(
            "app.services.ml_training.get_data_provider", lambda: _FakeProvider()
        )

        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:PROVPRED&model_type=baseline&start_date=2025-01-02&end_date=2025-06-30",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        _drain_worker()

        detail = client.get(f"/api/v1/training/jobs/{job_id}", headers=auth_headers)
        assert detail.status_code == 200
        job = detail.json()
        assert job["status"] == "completed", job.get("error_details")
        assert job["best_params"]["data_nature"] == "synthetic"
        model_id = job["best_params"]["model_id"]

        resp = client.post(f"/api/v1/ml/models/{model_id}/predict-latest", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data_nature"] == "synthetic"
