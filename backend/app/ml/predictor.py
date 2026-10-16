"""ML model predictor — inference using saved model artifacts."""

import numpy as np
from typing import Any, Dict, List, Optional

from app.ml.features import build_feature_matrix
from app.ml.trainer import load_model


def predict(
    model_id: str,
    bars: List[Dict],
) -> Dict[str, Any]:
    """Run inference using a saved model.

    Args:
        model_id: ID of the saved model artifact.
        bars: Recent OHLCV bars (should be at least 30 bars for feature warmup).

    Returns:
        Dict with predictions, confidence, and metadata.
    """
    model, scaler, metadata, feature_names = load_model(model_id)

    features, actual_feature_names, dates = build_feature_matrix(
        bars,
        lookback_windows=[5, 10, 20],
    )

    if actual_feature_names != feature_names:
        raise ValueError(
            f"Feature mismatch: model expects {len(feature_names)} features, "
            f"got {len(actual_feature_names)}. Model may be incompatible with "
            f"this data."
        )

    if not features:
        return {
            "model_id": model_id,
            "predictions": [],
            "dates": [],
            "error": "No valid feature rows generated from input bars",
        }

    X = np.array(features)
    X_scaled = scaler.transform(X)

    predictions_proba = model.predict_proba(X_scaled)
    predictions_class = model.predict(X_scaled)

    up_probabilities = []
    if hasattr(model, "classes_"):
        up_idx = list(model.classes_).index(1) if 1 in model.classes_ else 0
        up_probabilities = predictions_proba[:, up_idx].tolist()
    else:
        up_probabilities = predictions_proba[:, 1].tolist() if predictions_proba.shape[1] > 1 else [0.5] * len(predictions_proba)

    results = []
    for i, date in enumerate(dates):
        pred = int(predictions_class[i])
        prob = float(up_probabilities[i]) if i < len(up_probabilities) else 0.5
        signal = "BUY" if pred == 1 else "SELL"
        confidence = abs(prob - 0.5) * 2

        results.append({
            "date": date,
            "signal": signal,
            "direction": "UP" if pred == 1 else "DOWN",
            "probability": round(prob, 4),
            "confidence": round(confidence, 4),
        })

    return {
        "model_id": model_id,
        "model_type": metadata.get("model_type", "unknown"),
        "instrument_id": metadata.get("instrument_id", "unknown"),
        "predictions": results,
        "n_predictions": len(results),
        "date_range": {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
        },
    }


def predict_latest(
    model_id: str,
    bars: List[Dict],
) -> Dict[str, Any]:
    """Get prediction for the most recent bar only."""
    result = predict(model_id, bars)
    if not result.get("predictions"):
        return result

    latest = result["predictions"][-1]
    return {
        "model_id": model_id,
        "model_type": result.get("model_type", "unknown"),
        "instrument_id": result.get("instrument_id", "unknown"),
        "latest_prediction": latest,
        "generated_at": latest["date"],
    }
