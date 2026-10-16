"""ML training service — integrates ML training with the training job system.

Provides an alternative to parameter sweep: actual model training using
scikit-learn classifiers on OHLCV data with temporal splits.
"""

import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.training import TrainingJob, TrainingJobStatus
from app.providers import get_data_provider
from app.services.market_data_storage import MarketDataStorage
from app.ml.trainer import train_model, load_model, list_models
from app.lib.timeutil import utcnow


def _get_bars_with_storage(
    db: Session,
    provider: Any,
    instrument_id: str,
    start_date: str,
    end_date: str,
) -> tuple[List[Any], str]:
    """Fetch bars with storage integration.

    Returns (bars, data_source_used) where data_source_used is 'stored' or 'provider'.
    If stored data is available and sufficient, uses it. Otherwise fetches from provider
    and stores for future use.
    """
    storage = MarketDataStorage(db)
    start_date_obj = date.fromisoformat(start_date)
    end_date_obj = date.fromisoformat(end_date)

    stored_bars = storage.get_bars(
        instrument_id=instrument_id,
        start_date=start_date_obj,
        end_date=end_date_obj,
    )

    if len(stored_bars) >= 60:
        bar_list = [
            type('Bar', (), {
                'date': b.bar_date.isoformat(),
                'open': b.open,
                'high': b.high,
                'low': b.low,
                'close': b.close,
                'volume': b.volume,
            })()
            for b in stored_bars
        ]
        return bar_list, "stored"

    bars = provider.get_bars(instrument_id, start_date, end_date)

    if bars:
        try:
            quality = provider.get_data_quality(instrument_id)
            storage.store_bars(
                instrument_id=instrument_id,
                provider_id=provider.provider_id,
                bars=bars,
                data_nature=quality.get("data_nature", "unknown"),
                adjustment_status=quality.get("adjustment"),
                provider_note=quality.get("note"),
                provider_note_zh=quality.get("note_zh"),
            )
        except Exception as e:
            import logging
            logging.warning(f"Failed to store bars for {instrument_id}: {e}")

    return bars, "provider"


def create_ml_training_job(
    db: Session,
    user_id: uuid.UUID,
    experiment_id: Optional[uuid.UUID],
    instrument_id: str,
    model_type: str = "logistic_regression",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> TrainingJob:
    """Create an ML training job.

    Unlike parameter sweep, this trains an actual ML model (classifier)
    to predict next-day return direction.

    experiment_id is optional: ML jobs started from the training page are
    not tied to an experiment. The column is nullable — never fabricate a
    random experiment UUID, it violates the FK on PostgreSQL.

    The training_type is stored in strategy_spec_snapshot as a marker.
    """
    provider = get_data_provider()

    if not start_date or not end_date:
        date_range = provider.get_date_range()
        real_range = date_range.get("real_instruments_range", {})
        start_date = start_date or real_range.get("min", "2024-01-02")
        end_date = end_date or "2024-12-31"

    bars, data_source = _get_bars_with_storage(db, provider, instrument_id, start_date, end_date)
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
            "data_source": data_source,
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
        bars, data_source = _get_bars_with_storage(db, provider, instrument_id, start_date, end_date)
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
        )

        job.results = result
        job.best_params = {"model_id": result["model_id"], "model_type": model_type, "data_source": data_source}
        job.status = TrainingJobStatus.completed

    except Exception as e:
        job.status = TrainingJobStatus.failed
        job.error_details = str(e)

    job.completed_at = utcnow()
    db.flush()
    return job
