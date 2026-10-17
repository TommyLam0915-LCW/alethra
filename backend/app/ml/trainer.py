"""ML model trainer with temporal train/validation/test split.

Trains a scikit-learn classifier to predict next-day return direction.
All splits are temporal (no future data leakage).
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from sklearn.preprocessing import StandardScaler
from sklearn.dummy import DummyClassifier

from app.ml.features import build_feature_matrix, build_target_direction
from app.lib.timeutil import utcnow


ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")


def _safe_model_dir(model_id: str) -> str:
    """Resolve model_id to a directory path within ARTIFACTS_DIR.

    Raises ValueError if model_id attempts path traversal.
    """
    if os.sep in model_id or "/" in model_id or ".." in model_id:
        raise ValueError(f"Invalid model ID: {model_id}")
    model_dir = os.path.realpath(os.path.join(ARTIFACTS_DIR, model_id))
    artifacts_real = os.path.realpath(ARTIFACTS_DIR)
    if not model_dir.startswith(artifacts_real + os.sep) and model_dir != artifacts_real:
        raise ValueError(f"Invalid model ID: {model_id}")
    return model_dir


def temporal_split(
    n: int,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> Tuple[List[int], List[int], List[int]]:
    """Split indices temporally: train → val → test (no shuffle)."""
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train_idx = list(range(0, train_end))
    val_idx = list(range(train_end, val_end))
    test_idx = list(range(val_end, n))

    return train_idx, val_idx, test_idx


def train_model(
    bars: List[Dict],
    instrument_id: str,
    model_type: str = "logistic_regression",
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Train a model on OHLCV bar data.

    Args:
        bars: List of OHLCV bar dicts (must have date, open, high, low, close).
        instrument_id: Instrument identifier for provenance.
        model_type: "logistic_regression" or "gradient_boosting" or "baseline".
        train_ratio: Fraction of data for training.
        val_ratio: Fraction of data for validation.

    Returns:
        Dict with model artifact path, evaluation metrics, and metadata.
    """
    if len(bars) < 60:
        raise ValueError(f"Need at least 60 bars for training, got {len(bars)}")

    features, feature_names, valid_dates = build_feature_matrix(bars)
    targets = build_target_direction(bars, valid_dates)

    n_samples = min(len(features), len(targets))
    features = features[:n_samples]
    targets = targets[:n_samples]
    valid_dates = valid_dates[:n_samples]

    if n_samples < 30:
        raise ValueError(f"Need at least 30 valid feature rows, got {n_samples}")

    train_idx, val_idx, test_idx = temporal_split(n_samples, train_ratio, val_ratio)

    if len(train_idx) < 10 or len(val_idx) < 5 or len(test_idx) < 5:
        raise ValueError(
            f"Temporal split too small: train={len(train_idx)}, "
            f"val={len(val_idx)}, test={len(test_idx)}"
        )

    X = np.array(features)
    y = np.array(targets)

    X_train, y_train = X[train_idx], y[train_idx]
    X_val, y_val = X[val_idx], y[val_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    if model_type == "logistic_regression":
        model = LogisticRegression(
            max_iter=1000,
            C=1.0,
            random_state=42,
            solver="lbfgs",
        )
    elif model_type == "gradient_boosting":
        model = GradientBoostingClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            random_state=42,
        )
    elif model_type == "baseline":
        model = DummyClassifier(strategy="most_frequent")
    else:
        raise ValueError(f"Unknown model_type: {model_type}")

    model.fit(X_train_scaled, y_train)

    train_metrics = _evaluate(model, X_train_scaled, y_train, "train")
    val_metrics = _evaluate(model, X_val_scaled, y_val, "val")
    test_metrics = _evaluate(model, X_test_scaled, y_test, "test")

    baseline_model = DummyClassifier(strategy="most_frequent")
    baseline_model.fit(X_train_scaled, y_train)
    baseline_test = _evaluate(baseline_model, X_test_scaled, y_test, "test")

    model_id = str(uuid.uuid4())
    artifact_path = _save_artifact(
        model_id=model_id,
        model=model,
        scaler=scaler,
        feature_names=feature_names,
        metadata={
            "instrument_id": instrument_id,
            "model_type": model_type,
            "user_id": user_id,
            "trained_at": utcnow().isoformat(),
            "n_train": len(train_idx),
            "n_val": len(val_idx),
            "n_test": len(test_idx),
            "n_features": len(feature_names),
            "feature_names": feature_names,
            "date_range": {
                "start": valid_dates[0],
                "end": valid_dates[-1],
                "train_end": valid_dates[train_idx[-1]],
                "val_end": valid_dates[val_idx[-1]] if val_idx else None,
            },
        },
    )

    return {
        "model_id": model_id,
        "artifact_path": artifact_path,
        "model_type": model_type,
        "instrument_id": instrument_id,
        "feature_names": feature_names,
        "n_features": len(feature_names),
        "split": {
            "train_size": len(train_idx),
            "val_size": len(val_idx),
            "test_size": len(test_idx),
        },
        "metrics": {
            "train": train_metrics,
            "val": val_metrics,
            "test": test_metrics,
            "baseline_test": baseline_test,
        },
        "date_range": {
            "data_start": valid_dates[0],
            "data_end": valid_dates[-1],
            "train_end": valid_dates[train_idx[-1]],
            "val_end": valid_dates[val_idx[-1]] if val_idx else None,
            "test_end": valid_dates[test_idx[-1]] if test_idx else None,
        },
    }


def _evaluate(
    model: Any,
    X: np.ndarray,
    y: np.ndarray,
    split_name: str,
) -> Dict[str, float]:
    y_pred = model.predict(X)
    return {
        "split": split_name,
        "accuracy": round(accuracy_score(y, y_pred), 4),
        "precision": round(precision_score(y, y_pred, zero_division=0), 4),
        "recall": round(recall_score(y, y_pred, zero_division=0), 4),
        "f1": round(f1_score(y, y_pred, zero_division=0), 4),
        "n_samples": len(y),
        "positive_rate": round(float(y.mean()), 4),
    }


def _save_artifact(
    model_id: str,
    model: Any,
    scaler: Any,
    feature_names: List[str],
    metadata: Dict[str, Any],
) -> str:
    import joblib

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    model_dir = _safe_model_dir(model_id)
    os.makedirs(model_dir, exist_ok=True)

    joblib.dump(model, os.path.join(model_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(model_dir, "scaler.joblib"))

    with open(os.path.join(model_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    with open(os.path.join(model_dir, "feature_names.json"), "w") as f:
        json.dump(feature_names, f)

    return model_dir


def load_model(model_id: str, user_id: Optional[str] = None) -> Tuple[Any, Any, Dict[str, Any], List[str]]:
    """Load a saved model artifact.

    Returns: (model, scaler, metadata, feature_names)
    Raises: PermissionError if user_id is provided and doesn't match model owner
    """
    import joblib

    model_dir = _safe_model_dir(model_id)
    if not os.path.isdir(model_dir):
        raise FileNotFoundError(f"Model artifact not found: {model_id}")

    with open(os.path.join(model_dir, "metadata.json")) as f:
        metadata = json.load(f)

    if user_id is not None and metadata.get("user_id") != user_id:
        raise PermissionError(f"Model {model_id} is not owned by user {user_id}")

    model = joblib.load(os.path.join(model_dir, "model.joblib"))
    scaler = joblib.load(os.path.join(model_dir, "scaler.joblib"))

    with open(os.path.join(model_dir, "feature_names.json")) as f:
        feature_names = json.load(f)

    return model, scaler, metadata, feature_names


def list_models(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all saved model artifacts, optionally filtered by user_id."""
    if not os.path.isdir(ARTIFACTS_DIR):
        return []

    models = []
    for model_id in os.listdir(ARTIFACTS_DIR):
        try:
            model_dir = _safe_model_dir(model_id)
        except ValueError:
            continue
        meta_path = os.path.join(model_dir, "metadata.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                metadata = json.load(f)
            metadata["model_id"] = model_id
            if user_id is None or metadata.get("user_id") == user_id:
                models.append(metadata)

    models.sort(key=lambda m: m.get("trained_at", ""), reverse=True)
    return models
