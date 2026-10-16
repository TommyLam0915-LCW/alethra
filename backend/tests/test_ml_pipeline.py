"""Tests for ML training and inference pipeline.

Covers:
- Feature engineering (returns, SMA, RSI, volatility)
- Temporal split (no data leakage)
- Model training (logistic regression, gradient boosting, baseline)
- Model artifact save/load
- Prediction (full and latest)
- API endpoints (train, list, predict)
- Data leakage prevention (temporal ordering)
"""

import os
import shutil
import json
import pytest
import numpy as np
from datetime import datetime
from fastapi.testclient import TestClient

from app.ml.features import (
    compute_returns,
    compute_sma,
    compute_volatility,
    compute_rsi,
    build_feature_matrix,
    build_target_direction,
)
from app.ml.trainer import (
    train_model,
    load_model,
    list_models,
    delete_model,
    temporal_split,
    ARTIFACTS_DIR,
)
from app.ml.predictor import predict, predict_latest
from app.providers.demo_provider import DemoDataProvider


def _make_bars(n=200, base_price=100.0, seed=42):
    """Generate synthetic OHLCV bars for testing."""
    rng = np.random.RandomState(seed)
    bars = []
    price = base_price
    for i in range(n):
        daily_return = rng.normal(0.001, 0.02)
        close = round(price * (1 + daily_return), 4)
        high = round(close * (1 + abs(rng.normal(0, 0.01))), 4)
        low = round(close * (1 - abs(rng.normal(0, 0.01))), 4)
        open_price = round(price * (1 + rng.normal(0, 0.005)), 4)
        open_price = max(0.01, open_price)
        close = max(0.01, close)
        high = max(open_price, close, high)
        low = max(0.01, min(open_price, close, low))

        date_str = f"2024-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}"
        bars.append({
            "date": date_str,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": int(rng.uniform(500000, 2000000)),
        })
        price = close
    return bars


class TestFeatureEngineering:
    def test_compute_returns(self):
        closes = [100, 102, 101, 103]
        returns = compute_returns(closes)
        assert returns[0] is None
        assert len(returns) == 4
        assert returns[1] is not None
        assert returns[1] > 0

    def test_compute_sma(self):
        values = [1, 2, 3, 4, 5]
        sma = compute_sma(values, 3)
        assert sma[0] is None
        assert sma[1] is None
        assert sma[2] == 2.0
        assert sma[3] == 3.0
        assert sma[4] == 4.0

    def test_compute_volatility(self):
        returns = [None, 0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03]
        vol = compute_volatility(returns, 3)
        assert vol[0] is None
        assert vol[1] is None
        assert vol[2] is not None
        assert vol[2] > 0

    def test_compute_rsi(self):
        closes = [44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                  46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
                  46.22, 45.64]
        rsi = compute_rsi(closes, 14)
        assert rsi[13] is None
        assert rsi[14] is not None
        assert 0 <= rsi[14] <= 100

    def test_build_feature_matrix(self):
        bars = _make_bars(200)
        features, names, dates = build_feature_matrix(bars)
        assert len(features) > 0
        assert len(names) > 0
        assert len(features) == len(dates)
        assert all(len(row) == len(names) for row in features)

    def test_build_feature_matrix_too_short(self):
        bars = _make_bars(10)
        features, names, dates = build_feature_matrix(bars)
        assert len(features) == 0

    def test_build_target_direction(self):
        bars = _make_bars(100)
        _, _, dates = build_feature_matrix(bars)
        targets = build_target_direction(bars, dates)
        assert len(targets) > 0
        assert all(t in (0, 1) for t in targets)

    def test_target_alignment(self):
        bars = _make_bars(100)
        features, _, dates = build_feature_matrix(bars)
        targets = build_target_direction(bars, dates)
        assert len(features) == len(targets) + 1 or len(features) == len(targets)


class TestTemporalSplit:
    def test_temporal_split_proportions(self):
        train, val, test = temporal_split(100, 0.7, 0.15)
        assert len(train) == 70
        assert len(val) == 15
        assert len(test) == 15

    def test_temporal_split_no_overlap(self):
        train, val, test = temporal_split(100)
        assert set(train) & set(val) == set()
        assert set(train) & set(test) == set()
        assert set(val) & set(test) == set()

    def test_temporal_split_ordered(self):
        train, val, test = temporal_split(100)
        assert max(train) < min(val)
        assert max(val) < min(test)

    def test_temporal_split_no_shuffle(self):
        train, val, test = temporal_split(200)
        assert train == list(range(140))
        assert val == list(range(140, 170))
        assert test == list(range(170, 200))


class TestModelTraining:
    def test_train_logistic_regression(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST", model_type="logistic_regression")
        assert result["model_id"]
        assert result["model_type"] == "logistic_regression"
        assert result["metrics"]["test"]["accuracy"] > 0
        assert result["split"]["train_size"] > 0
        assert result["split"]["val_size"] > 0
        assert result["split"]["test_size"] > 0

    def test_train_gradient_boosting(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST", model_type="gradient_boosting")
        assert result["model_type"] == "gradient_boosting"
        assert result["metrics"]["test"]["accuracy"] > 0

    def test_train_baseline(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST", model_type="baseline")
        assert result["model_type"] == "baseline"
        assert result["metrics"]["test"]["accuracy"] > 0

    def test_train_too_few_bars(self):
        bars = _make_bars(30)
        with pytest.raises(ValueError, match="at least 60"):
            train_model(bars, "US:TEST")

    def test_model_artifact_saved(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST")
        model_id = result["model_id"]
        assert os.path.isdir(os.path.join(ARTIFACTS_DIR, model_id))
        assert os.path.isfile(os.path.join(ARTIFACTS_DIR, model_id, "model.joblib"))
        assert os.path.isfile(os.path.join(ARTIFACTS_DIR, model_id, "scaler.joblib"))
        assert os.path.isfile(os.path.join(ARTIFACTS_DIR, model_id, "metadata.json"))
        assert os.path.isfile(os.path.join(ARTIFACTS_DIR, model_id, "feature_names.json"))

    def test_model_load_roundtrip(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST")
        model_id = result["model_id"]

        model, scaler, metadata, feature_names = load_model(model_id)
        assert metadata["instrument_id"] == "US:TEST"
        assert metadata["model_type"] == "logistic_regression"
        assert len(feature_names) == result["n_features"]

    def test_baseline_comparison(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST")
        baseline_acc = result["metrics"]["baseline_test"]["accuracy"]
        test_acc = result["metrics"]["test"]["accuracy"]
        assert baseline_acc > 0
        assert test_acc > 0

    def test_no_data_leakage(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST")
        date_range = result["date_range"]
        assert date_range["train_end"] < date_range["val_end"]
        if date_range["val_end"] and date_range["test_end"]:
            assert date_range["val_end"] <= date_range["test_end"]

    def test_scaler_fit_only_on_training_data(self):
        """Verify scaler is fit only on training data, not validation or test."""
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST")
        model_id = result["model_id"]

        # Load the saved scaler
        model, scaler, metadata, feature_names = load_model(model_id)

        # The scaler should have been fit on training data only
        # We can verify this by checking that the scaler's mean and scale
        # match what we'd get from fitting on training data only
        from app.ml.features import build_feature_matrix, build_target_direction
        features, feature_names_check, valid_dates = build_feature_matrix(bars)
        targets = build_target_direction(bars, valid_dates)

        n_samples = min(len(features), len(targets))
        features = features[:n_samples]
        targets = targets[:n_samples]

        train_idx, val_idx, test_idx = temporal_split(n_samples)

        X_train = np.array(features)[train_idx]

        # Fit a new scaler on training data only
        from sklearn.preprocessing import StandardScaler
        test_scaler = StandardScaler()
        test_scaler.fit(X_train)

        # The saved scaler should have the same mean and scale
        np.testing.assert_array_almost_equal(scaler.mean_, test_scaler.mean_)
        np.testing.assert_array_almost_equal(scaler.scale_, test_scaler.scale_)

    def test_features_no_future_data_leakage(self):
        """Verify feature engineering doesn't use future data."""
        from app.ml.features import compute_sma, compute_rsi

        # Create a simple price series
        prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109]

        # SMA should only use past and current values
        sma = compute_sma(prices, 3)
        # sma[2] should be average of prices[0:3] = [100, 102, 101]
        assert sma[2] == (100 + 102 + 101) / 3
        # sma[3] should be average of prices[1:4] = [102, 101, 103]
        assert sma[3] == (102 + 101 + 103) / 3

        # RSI should only use past and current values
        rsi = compute_rsi(prices, 5)
        # RSI at index i should only depend on prices[0:i+1]
        # We can't easily verify the exact value, but we can verify
        # that changing a future price doesn't affect past RSI values
        prices_modified = prices.copy()
        prices_modified[8] = 200  # Change a future price
        rsi_modified = compute_rsi(prices_modified, 5)

        # RSI values before the modification should be unchanged
        for i in range(7):
            if rsi[i] is not None and rsi_modified[i] is not None:
                assert abs(rsi[i] - rsi_modified[i]) < 0.0001


class TestPrediction:
    @pytest.fixture
    def trained_model_id(self):
        bars = _make_bars(200)
        result = train_model(bars, "US:TEST", model_type="logistic_regression")
        yield result["model_id"]

    def test_predict_returns_signals(self, trained_model_id):
        bars = _make_bars(100, seed=99)
        result = predict(trained_model_id, bars)
        assert result["model_id"] == trained_model_id
        assert len(result["predictions"]) > 0
        for p in result["predictions"]:
            assert p["signal"] in ("BUY", "SELL")
            assert p["direction"] in ("UP", "DOWN")
            assert 0 <= p["probability"] <= 1
            assert 0 <= p["confidence"] <= 1

    def test_predict_latest(self, trained_model_id):
        bars = _make_bars(100, seed=99)
        result = predict_latest(trained_model_id, bars)
        assert result["model_id"] == trained_model_id
        assert "latest_prediction" in result
        pred = result["latest_prediction"]
        assert pred["signal"] in ("BUY", "SELL")

    def test_predict_model_not_found(self):
        bars = _make_bars(100)
        with pytest.raises(FileNotFoundError):
            predict("nonexistent-model-id", bars)

    def test_predict_too_few_bars(self, trained_model_id):
        bars = _make_bars(10)
        result = predict(trained_model_id, bars)
        assert result.get("error") or len(result.get("predictions", [])) == 0


class TestMLAPIEndpoints:
    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def auth_token(self, client):
        client.post("/api/auth/register", json={
            "email": "ml-test@example.com",
            "password": "testpassword123",
            "display_name": "ML Test User",
        })
        resp = client.post("/api/auth/login", json={
            "email": "ml-test@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_train_model_api(self, client, auth_token):
        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=logistic_regression&start_date=2024-01-02&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["model_id"] is not None
        assert data["metrics"] is not None
        assert "test" in data["metrics"]

    def test_train_model_invalid_type(self, client, auth_token):
        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=invalid",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 400

    def test_list_models_api(self, client, auth_token):
        client.post(
            "/api/v1/ml/train?instrument_id=US:MSFT&model_type=baseline&start_date=2024-01-02&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        resp = client.get("/api/v1/ml/models", headers=self._headers(auth_token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 0

    def test_predict_api(self, client, auth_token):
        train_resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=logistic_regression&start_date=2024-01-02&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        model_id = train_resp.json()["model_id"]

        resp = client.post(
            f"/api/v1/ml/models/{model_id}/predict?start_date=2024-06-01&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_id"] == model_id
        assert len(data["predictions"]) > 0

    def test_predict_latest_api(self, client, auth_token):
        train_resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=logistic_regression&start_date=2024-01-02&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        model_id = train_resp.json()["model_id"]

        resp = client.post(
            f"/api/v1/ml/models/{model_id}/predict-latest",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "latest_prediction" in data

    def test_unauthenticated_access(self, client):
        resp = client.get("/api/v1/ml/models")
        assert resp.status_code == 401

    def test_model_not_found(self, client, auth_token):
        resp = client.post(
            "/api/v1/ml/models/nonexistent/predict",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 404


class TestMLDataProvenance:
    """Every trained model must carry traceable data provenance.

    The demo provider serves synthetic data under real ticker IDs, so
    is_synthetic must be true even when instrument_id looks like a real
    stock — models trained on synthetic data must be identifiable.
    """

    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def auth_token(self, client):
        client.post("/api/auth/register", json={
            "email": "ml-provenance@example.com",
            "password": "testpassword123",
            "display_name": "ML Provenance User",
        })
        resp = client.post("/api/auth/login", json={
            "email": "ml-provenance@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _train(self, client, auth_token, model_type="baseline"):
        resp = client.post(
            f"/api/v1/ml/train?instrument_id=US:AAPL&model_type={model_type}&start_date=2024-01-02&end_date=2024-12-31",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        return resp.json()

    def test_train_response_contains_provenance(self, client, auth_token):
        data = self._train(client, auth_token)
        prov = data["data_provenance"]
        assert prov["provider_id"] == "demo"
        assert prov["is_synthetic"] is True, (
            "Demo provider serves synthetic data — must be flagged even for real tickers"
        )
        assert prov["feed_mode"] == "offline"
        assert prov["instrument_id"] == "US:AAPL"
        assert prov["n_bars"] > 0
        assert len(prov["bars_sha256"]) == 64
        assert prov["first_bar_date"] is not None
        assert prov["last_bar_date"] is not None

    def test_model_detail_metadata_contains_provenance_and_hyperparameters(self, client, auth_token):
        data = self._train(client, auth_token, model_type="logistic_regression")
        model_id = data["model_id"]

        resp = client.get(f"/api/v1/ml/models/{model_id}", headers=self._headers(auth_token))
        assert resp.status_code == 200
        metadata = resp.json()["metadata"]
        prov = metadata["data_provenance"]
        assert prov["provider_id"] == "demo"
        assert prov["is_synthetic"] is True
        assert prov["bars_sha256"] == data["data_provenance"]["bars_sha256"]

        hyper = metadata["hyperparameters"]
        assert isinstance(hyper, dict) and len(hyper) > 0
        assert hyper["max_iter"] == 1000
        assert hyper["random_state"] == 42

    def test_provenance_is_deterministic_for_same_data(self, client, auth_token):
        """Same instrument + date range must produce identical bars_sha256."""
        data1 = self._train(client, auth_token)
        data2 = self._train(client, auth_token)
        assert data1["data_provenance"]["bars_sha256"] == \
            data2["data_provenance"]["bars_sha256"]
        assert data1["model_id"] != data2["model_id"]

    def test_predict_response_includes_provenance(self, client, auth_token):
        data = self._train(client, auth_token)
        model_id = data["model_id"]

        resp = client.post(
            f"/api/v1/ml/models/{model_id}/predict-latest",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        prov = resp.json()["data_provenance"]
        assert prov["is_synthetic"] is True
        assert prov["provider_id"] == "demo"

    def test_train_model_unit_level_provenance(self):
        """train_model stores an explicitly-passed provenance verbatim."""
        bars = _make_bars(200)
        provenance = {
            "provider_id": "lseg",
            "provider_name": "LSEG (Refinitiv)",
            "feed_mode": "live",
            "is_synthetic": False,
        }
        result = train_model(bars, "US:TEST", model_type="baseline",
                             data_provenance=provenance)
        assert result["data_provenance"] == provenance

        _, _, metadata, _ = load_model(result["model_id"])
        assert metadata["data_provenance"] == provenance
        assert metadata["hyperparameters"]["strategy"] == "most_frequent"


class TestModelOwnership:
    """Model ownership and user isolation tests."""

    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def user1_token(self, client):
        client.post("/api/auth/register", json={
            "email": "user1@example.com",
            "password": "testpassword123",
            "display_name": "User 1",
        })
        resp = client.post("/api/auth/login", json={
            "email": "user1@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    @pytest.fixture
    def user2_token(self, client):
        client.post("/api/auth/register", json={
            "email": "user2@example.com",
            "password": "testpassword123",
            "display_name": "User 2",
        })
        resp = client.post("/api/auth/login", json={
            "email": "user2@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_user_cannot_list_others_models(self, client, user1_token, user2_token):
        """User A's models should not appear in User B's list."""
        resp1 = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(user1_token),
        )
        assert resp1.status_code == 200
        model_id = resp1.json()["model_id"]

        resp2 = client.get("/api/v1/ml/models", headers=self._headers(user2_token))
        assert resp2.status_code == 200
        models = resp2.json()["models"]
        model_ids = [m["model_id"] for m in models]
        assert model_id not in model_ids

    def test_user_cannot_access_others_model_detail(self, client, user1_token, user2_token):
        """User B should not be able to get details of User A's model."""
        resp1 = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(user1_token),
        )
        model_id = resp1.json()["model_id"]

        resp2 = client.get(f"/api/v1/ml/models/{model_id}", headers=self._headers(user2_token))
        assert resp2.status_code == 403

    def test_user_cannot_predict_with_others_model(self, client, user1_token, user2_token):
        """User B should not be able to use User A's model for prediction."""
        resp1 = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(user1_token),
        )
        model_id = resp1.json()["model_id"]

        resp2 = client.post(
            f"/api/v1/ml/models/{model_id}/predict",
            headers=self._headers(user2_token),
        )
        assert resp2.status_code == 403

    def test_user_cannot_predict_latest_with_others_model(self, client, user1_token, user2_token):
        """User B should not be able to use User A's model for latest prediction."""
        resp1 = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(user1_token),
        )
        model_id = resp1.json()["model_id"]

        resp2 = client.post(
            f"/api/v1/ml/models/{model_id}/predict-latest",
            headers=self._headers(user2_token),
        )
        assert resp2.status_code == 403

    def test_user_can_access_own_model(self, client, user1_token):
        """User A should be able to access their own model."""
        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(user1_token),
        )
        model_id = resp.json()["model_id"]

        resp_detail = client.get(f"/api/v1/ml/models/{model_id}", headers=self._headers(user1_token))
        assert resp_detail.status_code == 200
        assert resp_detail.json()["model_id"] == model_id

        resp_predict = client.post(
            f"/api/v1/ml/models/{model_id}/predict",
            headers=self._headers(user1_token),
        )
        assert resp_predict.status_code == 200

    def test_path_traversal_rejected(self, client, user1_token):
        """Path traversal attempts in model_id should be rejected."""
        traversal_ids = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32",
            "model/../../../etc/passwd",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        ]
        for bad_id in traversal_ids:
            resp = client.get(f"/api/v1/ml/models/{bad_id}", headers=self._headers(user1_token))
            assert resp.status_code in (400, 404), f"Path traversal {bad_id} should be rejected"

    def test_invalid_model_id_format(self, client, user1_token):
        """Invalid model ID formats should return 404."""
        invalid_ids = [
            "not-a-uuid",
            "12345",
            "model-with-special-chars!@#",
        ]
        for bad_id in invalid_ids:
            resp = client.get(f"/api/v1/ml/models/{bad_id}", headers=self._headers(user1_token))
            assert resp.status_code == 404


class TestMLModelDeletion:
    """Model deletion lifecycle with ownership enforcement."""

    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    @pytest.fixture
    def user1_token(self, client):
        client.post("/api/auth/register", json={
            "email": "user1-delete@example.com",
            "password": "testpassword123",
            "display_name": "User 1 Delete",
        })
        resp = client.post("/api/auth/login", json={
            "email": "user1-delete@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    @pytest.fixture
    def user2_token(self, client):
        client.post("/api/auth/register", json={
            "email": "user2-delete@example.com",
            "password": "testpassword123",
            "display_name": "User 2 Delete",
        })
        resp = client.post("/api/auth/login", json={
            "email": "user2-delete@example.com",
            "password": "testpassword123",
        })
        return resp.json()["access_token"]

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def _train(self, client, auth_token):
        resp = client.post(
            "/api/v1/ml/train?instrument_id=US:AAPL&model_type=baseline",
            headers=self._headers(auth_token),
        )
        assert resp.status_code == 200
        return resp.json()

    def test_owner_can_delete_own_model(self, client, user1_token):
        """Owner deletes their model: 200, then gone from list/detail/predict."""
        data = self._train(client, user1_token)
        model_id = data["model_id"]

        resp = client.delete(f"/api/v1/ml/models/{model_id}", headers=self._headers(user1_token))
        assert resp.status_code == 200
        assert resp.json()["deleted"] == model_id

        resp_list = client.get("/api/v1/ml/models", headers=self._headers(user1_token))
        assert model_id not in [m["model_id"] for m in resp_list.json()["models"]]

        resp_detail = client.get(f"/api/v1/ml/models/{model_id}", headers=self._headers(user1_token))
        assert resp_detail.status_code == 404

        resp_predict = client.post(
            f"/api/v1/ml/models/{model_id}/predict-latest",
            headers=self._headers(user1_token),
        )
        assert resp_predict.status_code == 404

    def test_user_cannot_delete_others_model(self, client, user1_token, user2_token):
        """User B's DELETE on User A's model → 403 and the model survives."""
        data = self._train(client, user1_token)
        model_id = data["model_id"]

        resp = client.delete(f"/api/v1/ml/models/{model_id}", headers=self._headers(user2_token))
        assert resp.status_code == 403

        resp_detail = client.get(f"/api/v1/ml/models/{model_id}", headers=self._headers(user1_token))
        assert resp_detail.status_code == 200, "Model must survive a foreign delete attempt"

    def test_delete_unknown_model_404(self, client, user1_token):
        unknown_uuid = "00000000-0000-0000-0000-000000000000"
        resp = client.delete(f"/api/v1/ml/models/{unknown_uuid}", headers=self._headers(user1_token))
        assert resp.status_code == 404

    def test_delete_invalid_model_id_format(self, client, user1_token):
        """DELETE must not probe the filesystem for malformed IDs."""
        for bad_id in ["not-a-uuid", "12345", "model-with-special-chars!@#"]:
            resp = client.delete(f"/api/v1/ml/models/{bad_id}", headers=self._headers(user1_token))
            assert resp.status_code == 404

    def test_delete_path_traversal_rejected(self, client, user1_token):
        """DELETE must never rmtree a path outside the artifacts dir."""
        sentinel_dir = os.path.join(ARTIFACTS_DIR, "..", "sentinel-keep-me")
        os.makedirs(sentinel_dir, exist_ok=True)

        traversal_ids = [
            "../sentinel-keep-me",
            "../../../etc/passwd",
            "..%2F..%2Fsentinel-keep-me",
        ]
        try:
            for bad_id in traversal_ids:
                resp = client.delete(f"/api/v1/ml/models/{bad_id}", headers=self._headers(user1_token))
                assert resp.status_code == 404, f"Traversal {bad_id} must be rejected"
            assert os.path.isdir(sentinel_dir), "Traversal must not delete directories"
        finally:
            shutil.rmtree(sentinel_dir, ignore_errors=True)

    def test_delete_refuses_artifact_without_metadata(self):
        """A directory without metadata.json proves no ownership — refuse to delete."""
        fake_id = "10000000-0000-0000-0000-000000000000"
        fake_dir = os.path.join(ARTIFACTS_DIR, fake_id)
        os.makedirs(fake_dir, exist_ok=True)
        try:
            with pytest.raises(FileNotFoundError):
                delete_model(fake_id, user_id="someone")
            assert os.path.isdir(fake_dir), "Artifact without metadata must not be deleted"
        finally:
            shutil.rmtree(fake_dir, ignore_errors=True)
