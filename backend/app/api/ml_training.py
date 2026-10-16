"""API endpoints for ML model training and inference."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.jwt import get_current_user
from app.auth.entitlements import require_entitlement, reserve_quota, settle_reservation, release_reservation
from app.models.user import User
from app.services.ml_training import create_ml_training_job, execute_ml_training
from app.ml.trainer import list_models, load_model
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
    db: Session = Depends(get_db),
):
    """Start an ML training job.

    Trains a classifier to predict next-day return direction using
    technical features from OHLCV data. Uses temporal train/val/test split.

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
        try:
            job = create_ml_training_job(
                db=db,
                user_id=current_user.id,
                experiment_id=experiment_uuid,
                instrument_id=instrument_id,
                model_type=model_type,
                start_date=start_date,
                end_date=end_date,
            )
            job_id = job.id
        except ValueError as e:
            release_reservation(db, reservation.id)
            raise HTTPException(400, str(e))

        try:
            job = execute_ml_training(db, job_id)
        except Exception as e:
            release_reservation(db, reservation.id)
            raise HTTPException(500, f"Training failed: {e}")

        if job.status.value == "failed":
            release_reservation(db, reservation.id)
            raise HTTPException(422, f"Training failed: {job.error_details}")

        settle_reservation(db, reservation.id)
        db.commit()

        return {
            "job_id": str(job.id),
            "status": job.status.value,
            "model_id": job.best_params.get("model_id") if job.best_params else None,
            "metrics": job.results.get("metrics") if job.results else None,
            "split": job.results.get("split") if job.results else None,
            "date_range": job.results.get("date_range") if job.results else None,
            "model_type": model_type,
            "instrument_id": instrument_id,
            "disclaimer": "Model is for research purposes only. Not suitable for trading decisions.",
            "disclaimer_zh": "模型僅供研究用途。不適合用於交易決策。",
        }

    except HTTPException:
        raise
    except Exception as e:
        release_reservation(db, reservation.id)
        raise HTTPException(500, f"Training failed: {e}")


@router.get("/models")
def list_trained_models(
    current_user: User = Depends(get_current_user),
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
):
    """Get detail for a specific trained model."""
    try:
        _, _, metadata, feature_names = load_model(model_id, user_id=str(current_user.id))
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    return {
        "model_id": model_id,
        "metadata": metadata,
        "feature_names": feature_names,
    }


@router.post("/models/{model_id}/predict")
def run_prediction(
    model_id: str,
    instrument_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Run prediction using a trained model.

    Fetches recent data for the instrument and generates signals.
    """
    try:
        _, _, metadata, _ = load_model(model_id, user_id=str(current_user.id))
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    pred_instrument = instrument_id or metadata.get("instrument_id")
    if not pred_instrument:
        raise HTTPException(400, "instrument_id required (model has no default instrument)")

    provider = get_data_provider()

    if not start_date or not end_date:
        end_date = "2024-12-31"
        start_date = "2024-06-01"

    bars = provider.get_bars(pred_instrument, start_date, end_date)
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
        "disclaimer": "Predictions are for research purposes only. Not trading advice.",
        "disclaimer_zh": "預測僅供研究用途。非交易建議。",
    }


@router.post("/models/{model_id}/predict-latest")
def run_latest_prediction(
    model_id: str,
    instrument_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """Get the latest prediction from a trained model."""
    try:
        _, _, metadata, _ = load_model(model_id)
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")

    pred_instrument = instrument_id or metadata.get("instrument_id")
    if not pred_instrument:
        raise HTTPException(400, "instrument_id required")

    provider = get_data_provider()
    bars = provider.get_bars(pred_instrument, "2024-01-02", "2024-12-31")
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

    return result
