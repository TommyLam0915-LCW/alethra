"""Module F: Training pipeline — parameter sweep engine."""
import copy
import itertools
import uuid
from datetime import datetime
from typing import Dict, List, Any

from sqlalchemy.orm import Session

from app.models.training import TrainingJob, TrainingJobStatus
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.engine.backtest import run_backtest


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


def run_parameter_sweep(db: Session, job_id: uuid.UUID) -> TrainingJob:
    """Execute a grid search over the backtest engine.

    For each combination in parameter_grid:
      1. Build a strategy_spec from the experiment's scope + combo parameters
      2. Run backtest
      3. Collect metrics

    Rank results by total_return_pct (primary), max_drawdown_pct (secondary, lower is better).
    Store results in TrainingJob.results, set best_params.
    """
    job = db.query(TrainingJob).filter(TrainingJob.id == job_id).first()
    if not job:
        raise ValueError(f"Training job {job_id} not found")

    if job.status != TrainingJobStatus.pending:
        raise ValueError(f"Training job must be pending, got {job.status}")

    # Transition to running
    job.status = TrainingJobStatus.running
    db.flush()

    try:
        experiment = db.query(Experiment).filter(Experiment.id == job.experiment_id).first()
        if not experiment:
            raise ValueError(f"Experiment {job.experiment_id} not found")

        # Get the base strategy spec from experiment's current version or scope
        base_strategy_spec = {}
        if experiment.current_version_id:
            current_version = db.query(ExperimentVersion).filter(
                ExperimentVersion.id == experiment.current_version_id
            ).first()
            if current_version:
                base_strategy_spec = current_version.strategy_spec or {}

        if not base_strategy_spec:
            scope = experiment.scope or {}
            base_strategy_spec = scope.get("strategy_spec", {})

        if not base_strategy_spec:
            raise ValueError("Experiment has no strategy_spec in current version or scope")

        # Validate grid
        grid_errors = _validate_grid(job.parameter_grid)
        if grid_errors:
            raise ValueError(f"Invalid parameter grid: {'; '.join(grid_errors)}")

        # Expand the grid
        combinations = _expand_grid(job.parameter_grid)

        results: List[Dict[str, Any]] = []

        for combo in combinations:
            # Build strategy_spec by overlaying combo parameters onto base
            strategy_spec = copy.deepcopy(base_strategy_spec)

            # Merge signal_parameters
            signal_params = strategy_spec.get("signal_parameters", {})
            signal_params.update(combo)
            strategy_spec["signal_parameters"] = signal_params

            # Run the backtest
            backtest_result = run_backtest(strategy_spec)

            metrics = backtest_result.get("metrics", {})
            errors = backtest_result.get("errors", [])

            result_entry = {
                "params": combo,
                "metrics": metrics,
                "backtest_errors": errors,
                "backtest_status": backtest_result.get("run_status", "unknown"),
            }
            results.append(result_entry)

        # Rank results: primary=total_return_pct (desc), secondary=max_drawdown_pct (asc)
        def sort_key(entry):
            m = entry.get("metrics", {})
            total_return = m.get("total_return_pct", 0) or 0
            max_dd = m.get("max_drawdown_pct", 0) or 0
            return (-total_return, max_dd)

        ranked = sorted(results, key=sort_key)
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

    job.completed_at = datetime.utcnow()
    db.flush()
    return job
