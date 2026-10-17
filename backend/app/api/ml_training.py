"""API endpoints for ML model training and inference."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.jwt import get_current_user
from app.auth.entitlements import require_entitlement, reserve_quota
from app.auth.api_keys import check_api_key_scope
from app.models.user import User
from app.services.ml_training import (
    create_ml_training_job,
    get_recent_bars_for_prediction,
)
from app.ml.trainer import list_models, load_model, delete_model
from app.ml.predictor import predict, predict_latest
from app.providers import get_data_provider

router = APIRouter(prefix="/ml", tags=["ml-training"])


@router.post("/train")
def start_ml_training(
    instrument_id: str,
    model_type: str = "logistic_regression",
    experiment_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(require_entitlement(quota_type="ai_usage")),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    """Queue an ML training job for the durable training worker.

    Trains a classifier to predict next-day return direction using
    technical features from OHLCV data. Uses temporal train/val/test split.

    The job is created in `pending` status and executed by the training
    worker (`python -m app.workers.training_worker`), which settles the
    quota reservation on success and releases it on failure. Poll
    `/api/v1/training/jobs/{job_id}` for status.

    model_type: "logistic_regression" | "gradient_boosting" | "baseline"
    experiment_id: optional — links the job to an experiment you own.
    """
    if model_type not in ("logistic_regression", "gradient_boosting", "baseline"):
        raise HTTPException(400, f"Unknown model_type: {model_type}")

    experiment_uuid = None
    if experiment_id:
        from app.models.experiment import Experiment

        try:
            experiment_uuid = uuid.UUID(experiment_id)
        except ValueError:
            raise HTTPException(400, f"Invalid experiment_id format: {experiment_id}")

        experiment = db.query(Experiment).filter(
            Experiment.id == experiment_uuid,
            Experiment.owner_id == current_user.id,
        ).first()
        if not experiment:
            raise HTTPException(404, "Experiment not found")

    reservation = reserve_quota(db, current_user.id, "ai_usage")

    try:
        job = create_ml_training_job(
            db=db,
            user_id=current_user.id,
            experiment_id=experiment_uuid,
            instrument_id=instrument_id,
            model_type=model_type,
            start_date=start_date,
            end_date=end_date,
            quota_reservation_id=reservation.id,
        )
    except ValueError as e:
        # Reservation and job were flushed but never committed — rolling back
        # discards both atomically, so no reservation is left dangling.
        db.rollback()
        raise HTTPException(400, str(e))

    db.commit()

    return {
        "job_id": str(job.id),
        "status": job.status.value,
        "model_type": model_type,
        "instrument_id": instrument_id,
        "poll_url": f"/api/v1/training/jobs/{job.id}",
        "message": "Training job queued for the durable training worker.",
    }


@router.get("/models")
def list_trained_models(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    """List all trained model artifacts owned by the current user."""
    models = list_models(user_id=str(current_user.id))
    return {
        "models": models,
        "total": len(models),
    }


@router.get("/models/{model_id}")
def get_model_detail(
    model_id: str,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    """Get detail for a specific trained model."""
    try:
        _, _, metadata, feature_names = load_model(model_id, user_id=str(current_user.id))
    except ValueError:
        raise HTTPException(400, f"Invalid model ID: {model_id}")
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    return {
        "model_id": model_id,
        "metadata": metadata,
        "feature_names": feature_names,
    }


@router.delete("/models/{model_id}")
def delete_trained_model(
    model_id: str,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Delete a trained model artifact owned by the current user."""
    # Model IDs are UUIDs — rejecting non-UUID strings keeps the artifact
    # directory lookup (and rmtree) from ever resolving a traversal path.
    try:
        uuid.UUID(model_id)
    except ValueError:
        raise HTTPException(404, f"Model not found: {model_id}")

    try:
        delete_model(model_id, user_id=str(current_user.id))
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    return {"deleted": model_id}


@router.post("/models/{model_id}/predict")
def run_prediction(
    model_id: str,
    instrument_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    """Run prediction using a trained model.

    Fetches data for the instrument and generates signals. When no date
    range is given, uses the most recent bars available (stored data first).
    """
    try:
        _, _, metadata, _ = load_model(model_id, user_id=str(current_user.id))
    except ValueError:
        raise HTTPException(400, f"Invalid model ID: {model_id}")
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    pred_instrument = instrument_id or metadata.get("instrument_id")
    if not pred_instrument:
        raise HTTPException(400, "instrument_id required (model has no default instrument)")

    if not start_date or not end_date:
        bars, _, data_nature = get_recent_bars_for_prediction(db, pred_instrument)
    else:
        provider = get_data_provider()
        bars = provider.get_bars(pred_instrument, start_date, end_date)
        quality = provider.get_data_quality(pred_instrument)
        data_nature = quality.get("data_nature", "unknown") if isinstance(quality, dict) else "unknown"

    if len(bars) < 30:
        raise HTTPException(400, f"Need at least 30 bars for prediction, got {len(bars)}")

    bar_dicts = [
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ]

    try:
        result = predict(model_id, bar_dicts)
    except Exception as e:
        raise HTTPException(422, f"Prediction failed: {e}")

    return {
        **result,
        "data_nature": data_nature,
        "disclaimer": "Predictions are for research purposes only. Not trading advice.",
        "disclaimer_zh": "預測僅供研究用途。非交易建議。",
    }


@router.post("/models/{model_id}/predict-latest")
def run_latest_prediction(
    model_id: str,
    instrument_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
    db: Session = Depends(get_db),
):
    """Get the latest prediction from a trained model."""
    try:
        _, _, metadata, _ = load_model(model_id, user_id=str(current_user.id))
    except ValueError:
        raise HTTPException(400, f"Invalid model ID: {model_id}")
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    pred_instrument = instrument_id or metadata.get("instrument_id")
    if not pred_instrument:
        raise HTTPException(400, "instrument_id required")

    bars, _, data_nature = get_recent_bars_for_prediction(db, pred_instrument)
    if len(bars) < 30:
        raise HTTPException(400, f"Need at least 30 bars, got {len(bars)}")

    bar_dicts = [
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ]

    try:
        result = predict_latest(model_id, bar_dicts)
    except Exception as e:
        raise HTTPException(422, f"Prediction failed: {e}")

    return {**result, "data_nature": data_nature}
