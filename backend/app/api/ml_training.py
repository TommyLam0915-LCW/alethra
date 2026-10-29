"""API endpoints for ML model training and inference."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.auth.jwt import get_current_user
from app.auth.entitlements import (
    require_entitlement,
    reserve_quota,
    release_reservation,
    settle_reservation,
    get_quota_status,
)
from app.auth.api_keys import check_api_key_scope
from app.auth.rate_limit import api_key_rate_limit_dependency
from app.models.training import TrainingJob
from app.models.user import User
from app.services.ml_training import (
    create_ml_training_job,
    get_recent_bars_for_prediction,
)
from app.ml.trainer import list_models, load_model, delete_model
from app.ml.predictor import predict, predict_latest
from app.providers import get_data_provider

_settings = get_settings()

# The personal model API is reachable with an API key; budget each key the same
# way the personal paper API does so one script cannot starve the others.
router = APIRouter(
    prefix="/ml",
    tags=["ml-training"],
    dependencies=[
        Depends(
            api_key_rate_limit_dependency(
                max_requests=_settings.api_key_rate_limit_requests,
                window_seconds=_settings.api_key_rate_limit_window_seconds,
            )
        )
    ],
)


def _caller_via(request: Request) -> str:
    """Who invoked this route: an interactive session or a programmatic key."""
    scopes = getattr(request.state, "api_key_scopes", None)
    return "jwt" if scopes is None else "api_key"


@router.post("/train")
def start_ml_training(
    instrument_id: str,
    model_type: str = "logistic_regression",
    experiment_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(require_entitlement(quota_type="ai_usage")),
    _scope_check=Depends(check_api_key_scope("write", "training")),
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


@router.get("/entitlement")
def get_ml_entitlement(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read", "inference", "training")),
    db: Session = Depends(get_db),
):
    """AI-usage quota state for the caller.

    Training and inference reserve/settle ``ai_usage`` units and refuse with
    429 once the allowance is spent; the UI reads this endpoint to show the
    remaining allowance before the user hits that refusal.
    """
    return get_quota_status(db, current_user.id, "ai_usage")


@router.get("/models")
def list_trained_models(
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read", "inference", "training")),
    db: Session = Depends(get_db),
):
    """List all trained model artifacts owned by the current user.

    Artifacts trained before metrics/provenance were written into
    ``metadata.json`` are backfilled from their training job's recorded
    results so the model card can show evaluation instead of "-".
    """
    models = list_models(user_id=str(current_user.id))

    legacy = [
        m for m in models
        if not m.get("metrics") and m.get("training_job_id")
    ]
    if legacy:
        job_ids = []
        for m in legacy:
            try:
                job_ids.append(uuid.UUID(str(m["training_job_id"])))
            except ValueError:
                continue
        jobs = (
            db.query(TrainingJob).filter(
                TrainingJob.id.in_(job_ids),
                TrainingJob.user_id == current_user.id,
            ).all()
            if job_ids else []
        )
        by_id = {str(job.id): job for job in jobs}
        for m in legacy:
            job = by_id.get(str(m["training_job_id"]))
            if job is None or not job.results:
                continue
            m["metrics"] = job.results.get("metrics")
            params = job.best_params or {}
            if not m.get("data_provenance") and params.get("data_nature"):
                m["data_provenance"] = {
                    "data_source": params.get("data_source"),
                    "data_nature": params.get("data_nature"),
                    "is_synthetic": params.get("data_nature") == "synthetic",
                }

    return {
        "models": models,
        "total": len(models),
    }


@router.get("/models/{model_id}")
def get_model_detail(
    model_id: str,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read", "inference", "training")),
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
    _scope_check=Depends(check_api_key_scope("write", "training")),
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
    request: Request,
    instrument_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write", "inference")),
    db: Session = Depends(get_db),
):
    """Run prediction using a trained model.

    Fetches data for the instrument and generates signals. When no date
    range is given, uses the most recent bars available (stored data first).
    
    Quota: 1 ai_usage unit is reserved before prediction and settled on success;
    failures release the reservation (no charge).
    """
    try:
        _, _, metadata, _ = load_model(model_id, user_id=str(current_user.id))
    except ValueError:
        raise HTTPException(400, f"Invalid model ID: {model_id}")
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    # Reserve quota for inference
    reservation = reserve_quota(db, current_user.id, "ai_usage")

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
        # Release reservation on validation failure
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(400, f"Need at least 30 bars for prediction, got {len(bars)}")

    bar_dicts = [
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ]

    try:
        result = predict(model_id, bar_dicts)
    except Exception as e:
        # Release reservation on prediction failure
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(422, f"Prediction failed: {e}")

    # Settle quota on success
    settle_reservation(db, reservation.id)

    # Record inference usage (no commit - will be committed with quota settlement)
    from app.services.billing import record_api_call_no_commit
    record_api_call_no_commit(
        db,
        user_id=current_user.id,
        endpoint="/api/v1/ml/models/{model_id}/predict",
        model=model_id,
        tokens_used=0,  # sklearn inference - no token counting
        cost=0,  # No provider charge for local inference
        metadata={
            "instrument_id": pred_instrument,
            "data_nature": data_nature,
            "bars_count": len(bars),
            "prediction_type": "predict",
            "via": _caller_via(request),
            # Lineage: this inference -> artifact -> training job.
            "model_id": model_id,
            "training_job_id": metadata.get("training_job_id"),
            "model_instrument_id": metadata.get("instrument_id"),
        },
    )

    db.commit()

    return {
        **result,
        "data_nature": data_nature,
        "training_job_id": metadata.get("training_job_id"),
        "disclaimer": "Predictions are for research purposes only. Not trading advice.",
        "disclaimer_zh": "預測僅供研究用途。非交易建議。",
    }


@router.post("/models/{model_id}/predict-latest")
def run_latest_prediction(
    model_id: str,
    request: Request,
    instrument_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write", "inference")),
    db: Session = Depends(get_db),
):
    """Get the latest prediction from a trained model.
    
    Quota: 1 ai_usage unit is reserved before prediction and settled on success;
    failures release the reservation (no charge).
    """
    try:
        _, _, metadata, _ = load_model(model_id, user_id=str(current_user.id))
    except ValueError:
        raise HTTPException(400, f"Invalid model ID: {model_id}")
    except FileNotFoundError:
        raise HTTPException(404, f"Model not found: {model_id}")
    except PermissionError:
        raise HTTPException(403, f"You do not have access to model {model_id}")

    # Reserve quota for inference
    reservation = reserve_quota(db, current_user.id, "ai_usage")

    pred_instrument = instrument_id or metadata.get("instrument_id")
    if not pred_instrument:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(400, "instrument_id required")

    bars, _, data_nature = get_recent_bars_for_prediction(db, pred_instrument)
    if len(bars) < 30:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(400, f"Need at least 30 bars, got {len(bars)}")

    bar_dicts = [
        {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume}
        for b in bars
    ]

    try:
        result = predict_latest(model_id, bar_dicts)
    except Exception as e:
        release_reservation(db, reservation.id)
        db.commit()
        raise HTTPException(422, f"Prediction failed: {e}")

    # Settle quota on success
    settle_reservation(db, reservation.id)

    # Record inference usage (no commit - will be committed with quota settlement)
    from app.services.billing import record_api_call_no_commit
    record_api_call_no_commit(
        db,
        user_id=current_user.id,
        endpoint="/api/v1/ml/models/{model_id}/predict-latest",
        model=model_id,
        tokens_used=0,  # sklearn inference - no token counting
        cost=0,  # No provider charge for local inference
        metadata={
            "instrument_id": pred_instrument,
            "data_nature": data_nature,
            "bars_count": len(bars),
            "prediction_type": "predict-latest",
            "via": _caller_via(request),
            # Lineage: this inference -> artifact -> training job.
            "model_id": model_id,
            "training_job_id": metadata.get("training_job_id"),
            "model_instrument_id": metadata.get("instrument_id"),
        },
    )

    db.commit()

    return {
        **result,
        "data_nature": data_nature,
        "training_job_id": metadata.get("training_job_id"),
    }
