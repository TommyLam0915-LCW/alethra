"""ML training service — integrates ML training with the training job system.

Provides an alternative to parameter sweep: actual model training using
scikit-learn classifiers on OHLCV data with temporal splits.
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.training import TrainingJob, TrainingJobStatus
from app.providers import get_data_provider
from app.ml.trainer import train_model, load_model, list_models
from app.lib.timeutil import utcnow


def build_data_provenance(
    provider: Any,
    instrument_id: str,
    requested_start: str,
    requested_end: str,
    bar_dicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Describe exactly where training bars came from and what they contain.

    Stored with every model artifact so results stay traceable to the data
    that produced them. is_synthetic marks demo/synthetic feeds explicitly —
    a real ticker served by the demo provider is still synthetic data.
    """
    fingerprint_payload = json.dumps(
        [
            [b.get("date"), b.get("open"), b.get("high"), b.get("low"), b.get("close")]
            for b in bar_dicts
        ],
        separators=(",", ":"),
    )
    return {
        "provider_id": provider.provider_id,
        "provider_name": provider.provider_name,
        "feed_mode": provider.feed_mode,
        "is_synthetic": provider.provider_id == "demo",
        "instrument_id": instrument_id,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "n_bars": len(bar_dicts),
        "first_bar_date": bar_dicts[0]["date"] if bar_dicts else None,
        "last_bar_date": bar_dicts[-1]["date"] if bar_dicts else None,
        "bars_sha256": hashlib.sha256(fingerprint_payload.encode()).hexdigest(),
    }


def create_ml_training_job(
    db: Session,
    user_id: uuid.UUID,
    experiment_id: uuid.UUID,
    instrument_id: str,
    model_type: str = "logistic_regression",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> TrainingJob:
    """Create an ML training job.

    Unlike parameter sweep, this trains an actual ML model (classifier)
    to predict next-day return direction.

    The training_type is stored in strategy_spec_snapshot as a marker.
    """
    provider = get_data_provider()

    if not start_date or not end_date:
        date_range = provider.get_date_range()
        real_range = date_range.get("real_instruments_range", {})
        start_date = start_date or real_range.get("min", "2024-01-02")
        end_date = end_date or "2024-12-31"

    bars = provider.get_bars(instrument_id, start_date, end_date)
    if len(bars) < 60:
        raise ValueError(
            f"Insufficient data for {instrument_id}: got {len(bars)} bars, "
            f"need at least 60 for ML training."
        )

    job = TrainingJob(
        user_id=user_id,
        experiment_id=experiment_id,
        status=TrainingJobStatus.pending,
        parameter_grid={"model_type": [model_type]},
        strategy_spec_snapshot={
            "training_type": "ml_model",
            "instrument_id": instrument_id,
            "model_type": model_type,
            "start_date": start_date,
            "end_date": end_date,
            "n_bars": len(bars),
        },
    )
    db.add(job)
    db.flush()

    return job


def execute_ml_training(
    db: Session,
    job_id: uuid.UUID,
) -> TrainingJob:
    """Execute ML training for a pending job."""
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise ValueError(f"Training job {job_id} not found")

    if job.status not in (TrainingJobStatus.pending, TrainingJobStatus.running):
        raise ValueError(f"Job must be pending or running, got {job.status}")

    job.status = TrainingJobStatus.running
    db.flush()

    try:
        spec = job.strategy_spec_snapshot or {}
        if spec.get("training_type") != "ml_model":
            raise ValueError("Job is not an ML training job")

        instrument_id = spec["instrument_id"]
        model_type = spec.get("model_type", "logistic_regression")
        start_date = spec["start_date"]
        end_date = spec["end_date"]

        provider = get_data_provider()
        bars = provider.get_bars(instrument_id, start_date, end_date)
        bar_dicts = [
            {"date": b.date, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume": b.volume}
            for b in bars
        ]

        result = train_model(
            bars=bar_dicts,
            instrument_id=instrument_id,
            model_type=model_type,
            user_id=str(job.user_id),
            data_provenance=build_data_provenance(
                provider, instrument_id, start_date, end_date, bar_dicts,
            ),
        )

        job.results = result
        job.best_params = {"model_id": result["model_id"], "model_type": model_type}
        job.status = TrainingJobStatus.completed

    except Exception as e:
        job.status = TrainingJobStatus.failed
        job.error_details = str(e)

    job.completed_at = utcnow()
    db.flush()
    return job
