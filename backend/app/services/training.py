"""Module F: Training pipeline — parameter sweep engine."""
import copy
import itertools
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session

from app.models.training import TrainingJob, TrainingJobStatus
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.engine.backtest import run_backtest
from app.lib.timeutil import utcnow


# Maximum number of grid combinations allowed per training job.
MAX_GRID_SIZE = 1000

# In-memory cancellation flags, checked by the sweep loop between iterations.
# The cancel endpoint sets a flag here; the sweep loop reads it.
# This avoids relying on DB session visibility across threads.
_cancellation_flags: Dict[uuid.UUID, bool] = {}


def _expand_grid(parameter_grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Expand a parameter grid into all combinations.

    Example:
        {"short_window": [5, 10], "long_window": [20, 30]}
        -> [{"short_window": 5, "long_window": 20},
            {"short_window": 5, "long_window": 30},
            {"short_window": 10, "long_window": 20},
            {"short_window": 10, "long_window": 30}]
    """
    if not parameter_grid:
        return [{}]

    keys = sorted(parameter_grid.keys())
    value_lists = [parameter_grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def _validate_grid(parameter_grid: Dict[str, List[Any]]) -> List[str]:
    """Validate that parameter_grid is a dict of lists."""
    errors = []
    if not isinstance(parameter_grid, dict):
        errors.append("parameter_grid must be a dict")
        return errors

    for key, values in parameter_grid.items():
        if not isinstance(values, (list, tuple)):
            errors.append(f"parameter_grid['{key}'] must be a list of values")
            continue
        if len(values) == 0:
            errors.append(f"parameter_grid['{key}'] must contain at least one value")

    return errors


def run_parameter_sweep(
    db: Session,
    job_id: uuid.UUID,
    combinations: Optional[List[Dict[str, Any]]] = None,
) -> TrainingJob:
    """Execute a grid search over the backtest engine.

    For each combination in the parameter grid:
      1. Build a strategy_spec by overlaying combo parameters onto the
         captured strategy_spec_snapshot
      2. Run backtest
      3. Collect metrics

    The strategy_spec_snapshot is read from the TrainingJob record (set at
    job-creation time) so that changes to the experiment after the job was
    created do not affect the sweep.

    Rank results by total_return_pct (primary), max_drawdown_pct (secondary,
    lower is better).  Only backtests with run_status == 'completed' are
    included in the ranking.

    NOTE: This function currently runs synchronously inside the HTTP request
    handler.  It should be moved to a background worker (e.g. Celery / ARQ)
    before deployment to production.
    """
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise ValueError(f"Training job {job_id} not found")

    if job.status not in (TrainingJobStatus.pending, TrainingJobStatus.running):
        raise ValueError(f"Training job must be pending or running, got {job.status}")

    # Transition to running (if not already running — worker may have claimed it)
    if job.status == TrainingJobStatus.pending:
        job.status = TrainingJobStatus.running
        db.flush()

    try:
        # Use the strategy_spec snapshot captured at job-creation time.
        base_strategy_spec = job.strategy_spec_snapshot
        if not base_strategy_spec:
            raise ValueError(
                "Training job has no strategy_spec_snapshot; "
                "cannot execute parameter sweep"
            )

        # Fall back to expanding the grid here if pre-expanded combinations
        # were not supplied by the caller.
        if combinations is None:
            grid_errors = _validate_grid(job.parameter_grid)
            if grid_errors:
                raise ValueError(f"Invalid parameter grid: {'; '.join(grid_errors)}")
            combinations = _expand_grid(job.parameter_grid)
            if len(combinations) > MAX_GRID_SIZE:
                raise ValueError(
                    f"Parameter grid produces {len(combinations)} combinations, "
                    f"which exceeds the maximum of {MAX_GRID_SIZE}"
                )

        results: List[Dict[str, Any]] = []

        for combo in combinations:
            # ── Check cancellation flag between iterations ──────────
            if _cancellation_flags.get(job_id):
                job.status = TrainingJobStatus.cancelled
                job.cancelled_at = utcnow()
                job.completed_at = utcnow()
                job.error_details = "Job cancelled by user"
                _cancellation_flags.pop(job_id, None)
                db.flush()
                return job

            # Build strategy_spec by overlaying combo parameters onto base
            strategy_spec = copy.deepcopy(base_strategy_spec)

            # Merge signal_parameters
            signal_params = strategy_spec.get("signal_parameters", {})
            signal_params.update(combo)
            strategy_spec["signal_parameters"] = signal_params

            # Run the backtest
            backtest_result = run_backtest(strategy_spec, db_session=db)

            metrics = backtest_result.get("metrics", {})
            errors = backtest_result.get("errors", [])

            result_entry = {
                "params": combo,
                "metrics": metrics,
                "backtest_errors": errors,
                "backtest_status": backtest_result.get("run_status", "unknown"),
            }
            results.append(result_entry)

        # ── Filter to only successful backtests before ranking ──────
        successful_results = [
            r for r in results if r.get("backtest_status") == "completed"
        ]

        if not successful_results:
            job.status = TrainingJobStatus.failed
            job.error_details = (
                "No valid backtest results: all parameter combinations "
                "failed or produced errors"
            )
            job.results = results  # keep raw results for diagnostics
            job.completed_at = utcnow()
            db.flush()
            return job

        # Rank results: primary=total_return_pct (desc), secondary=max_drawdown_pct (asc)
        def sort_key(entry):
            m = entry.get("metrics", {})
            total_return = m.get("total_return_pct", 0) or 0
            max_dd = m.get("max_drawdown_pct", 0) or 0
            return (-total_return, max_dd)

        ranked = sorted(successful_results, key=sort_key)
        for rank, entry in enumerate(ranked, 1):
            entry["rank"] = rank

        job.results = ranked

        # Best params = top-ranked combination
        if ranked:
            job.best_params = ranked[0].get("params")

        job.status = TrainingJobStatus.completed

    except Exception as e:
        job.status = TrainingJobStatus.failed
        job.error_details = str(e)

    job.completed_at = utcnow()
    db.flush()
    return job


def cancel_running_job(job_id: uuid.UUID) -> None:
    """Request cancellation of a running training job.

    Sets an in-memory flag that the sweep loop checks between iterations.
    The actual status transition to 'cancelled' happens inside the sweep
    loop when it detects the flag.
    """
    _cancellation_flags[job_id] = True
