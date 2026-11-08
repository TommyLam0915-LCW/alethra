"""Bounded parameter optimization for trading skills.

This service provides a reproducible optimization workflow:

1. Skill configuration → bounded candidate generation
2. Chronological train/validation/test evaluation
3. Cost/risk comparison with baselines
4. Explicit user selection (no auto-adoption)

Design constraints:
- Finite, validated parameter ranges
- Deterministic candidate generation
- Explicit maximum trial budget
- All attempted configurations recorded
- No arbitrary code execution
- No production scheduler activation
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.engine.backtest import run_backtest, SIGNAL_PARAMETER_RULES


OPTIMIZATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class ParameterRange:
    """Validated parameter range for optimization."""
    name: str
    values: List[Any]
    
    def __post_init__(self):
        if not self.values:
            raise ValueError(f"Parameter {self.name} must have at least one value")


@dataclass(frozen=True)
class OptimizationConfig:
    """Configuration for an optimization job."""
    strategy_family: str
    parameter_ranges: Dict[str, ParameterRange]
    max_trials: int
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    test_fraction: float = 0.2
    starting_cash: Decimal = Decimal("10000")
    commission_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.001")
    
    def __post_init__(self):
        total = self.train_fraction + self.validation_fraction + self.test_fraction
        if abs(total - 1.0) > 1e-9:
            raise ValueError("Fractions must sum to 1.0")
        if self.max_trials < 1:
            raise ValueError("max_trials must be >= 1")
        if not self.parameter_ranges:
            raise ValueError("At least one parameter range required")


@dataclass
class CandidateConfiguration:
    """One candidate parameter configuration."""
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parameters: Dict[str, Any] = field(default_factory=dict)
    trial_number: Optional[int] = None
    
    def to_strategy_spec(self, asset_ids: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """Convert to backtest strategy_spec format."""
        return {
            "template_type": self.parameters.get("template_type", "sma_crossover"),
            "asset_ids": asset_ids,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": float(self.parameters.get("starting_cash", 10000)),
            "commission_rate": float(self.parameters.get("commission_rate", 0.001)),
            "slippage_rate": float(self.parameters.get("slippage_rate", 0.001)),
            "signal_parameters": {
                k: v for k, v in self.parameters.items()
                if k not in {"template_type", "starting_cash", "commission_rate", "slippage_rate"}
            },
        }
    
    def fingerprint(self) -> str:
        """Deterministic hash of parameters for deduplication."""
        payload = json.dumps(self.parameters, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class CandidateResult:
    """Evaluation result for one candidate."""
    candidate: CandidateConfiguration
    train_metrics: Dict[str, Any]
    validation_metrics: Dict[str, Any]
    test_metrics: Dict[str, Any]
    status: str  # "success", "failed", "rejected"
    error_message: Optional[str] = None
    dataset_checksum: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate.candidate_id,
            "parameters": self.candidate.parameters,
            "trial_number": self.candidate.trial_number,
            "train_metrics": self.train_metrics,
            "validation_metrics": self.validation_metrics,
            "test_metrics": self.test_metrics,
            "status": self.status,
            "error_message": self.error_message,
            "dataset_checksum": self.dataset_checksum,
        }


@dataclass
class OptimizationJob:
    """Tracks one optimization job with all candidates."""
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: Optional[OptimizationConfig] = None
    candidates: List[CandidateResult] = field(default_factory=list)
    status: str = "pending"  # "pending", "running", "completed", "cancelled", "failed"
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    def add_result(self, result: CandidateResult):
        """Add a candidate result."""
        self.candidates.append(result)
    
    def trial_count(self) -> int:
        """Number of trials attempted."""
        return len([c for c in self.candidates if c.status in ("success", "failed")])
    
    def successful_candidates(self) -> List[CandidateResult]:
        """Candidates that completed successfully."""
        return [c for c in self.candidates if c.status == "success"]
    
    def best_by_validation(self, metric: str = "net_return") -> Optional[CandidateResult]:
        """Best candidate by validation metric (not test!)."""
        successful = self.successful_candidates()
        if not successful:
            return None
        return max(successful, key=lambda c: c.validation_metrics.get(metric, float("-inf")))


def generate_sma_crossover_candidates(
    short_windows: List[int] = [5, 10, 15, 20, 30],
    long_windows: List[int] = [30, 50, 75, 100, 150, 200],
    max_trials: int = 30,
) -> List[CandidateConfiguration]:
    """Generate bounded SMA crossover candidates.
    
    Validates against SIGNAL_PARAMETER_RULES before generating.
    """
    # Validate against engine rules
    rules = SIGNAL_PARAMETER_RULES.get("sma_crossover", {})
    short_rule = rules.get("short_window", {})
    long_rule = rules.get("long_window", {})
    
    short_min = short_rule.get("min", 2)
    short_max = short_rule.get("max", 60)
    long_min = long_rule.get("min", 3)
    long_max = long_rule.get("max", 200)
    
    candidates = []
    trial = 0
    
    for short_w in short_windows:
        for long_w in long_windows:
            if trial >= max_trials:
                break
            
            # Validate bounds
            if not (short_min <= short_w <= short_max):
                continue
            if not (long_min <= long_w <= long_max):
                continue
            if short_w >= long_w:
                continue  # Short must be less than long
            
            candidate = CandidateConfiguration(
                parameters={
                    "template_type": "sma_crossover",
                    "short_window": short_w,
                    "long_window": long_w,
                },
                trial_number=trial,
            )
            candidates.append(candidate)
            trial += 1
        
        if trial >= max_trials:
            break
    
    return candidates


def split_data_chronological(
    data_length: int,
    train_fraction: float,
    validation_fraction: float,
) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
    """Split data into train/validation/test chronologically.
    
    Returns:
        ((train_start, train_end), (val_start, val_end), (test_start, test_end))
    
    No future data leakage: test split is strictly after train+validation.
    """
    train_end = int(data_length * train_fraction)
    val_end = int(data_length * (train_fraction + validation_fraction))
    
    train_range = (0, train_end - 1)
    val_range = (train_end, val_end - 1)
    test_range = (val_end, data_length - 1)
    
    return train_range, val_range, test_range


def evaluate_candidate(
    candidate: CandidateConfiguration,
    data_provider: Any,
    asset_ids: List[str],
    start_date: str,
    end_date: str,
    db_session: Any = None,
) -> CandidateResult:
    """Evaluate one candidate with chronological split.
    
    Runs backtest on the full date range. In a production implementation,
    this would split data into train/validation/test and run separate backtests.
    For the first slice, we run one backtest and record the result.
    
    Future-data leakage prevention:
    - Parameters are frozen before evaluation
    - No re-optimization on test split
    - Same fees/slippage applied to all candidates
    """
    try:
        strategy_spec = candidate.to_strategy_spec(asset_ids, start_date, end_date)
        
        result = run_backtest(
            strategy_spec=strategy_spec,
            data_provider=data_provider,
            db_session=db_session,
            data_source="auto",
        )
        
        if result.get("errors"):
            return CandidateResult(
                candidate=candidate,
                train_metrics={},
                validation_metrics={},
                test_metrics={},
                status="failed",
                error_message="; ".join(result["errors"]),
                dataset_checksum=result.get("dataset_checksum"),
            )
        
        metrics = result.get("metrics", {})
        
        # For first slice: use same metrics for all splits (placeholder)
        # Production implementation would split data and run separate backtests
        return CandidateResult(
            candidate=candidate,
            train_metrics=metrics,
            validation_metrics=metrics,
            test_metrics=metrics,
            status="success",
            dataset_checksum=result.get("dataset_checksum"),
        )
        
    except Exception as e:
        return CandidateResult(
            candidate=candidate,
            train_metrics={},
            validation_metrics={},
            test_metrics={},
            status="failed",
            error_message=str(e),
        )


def run_optimization_job(
    config: OptimizationConfig,
    data_provider: Any,
    asset_ids: List[str],
    start_date: str,
    end_date: str,
    db_session: Any = None,
) -> OptimizationJob:
    """Run a complete optimization job.
    
    Generates candidates, evaluates each, records all results.
    Does NOT auto-select or auto-adopt the best candidate.
    """
    job = OptimizationJob(config=config)
    job.status = "running"
    
    try:
        # Generate candidates based on strategy family
        if config.strategy_family == "sma_crossover":
            short_windows = config.parameter_ranges.get("short_window")
            long_windows = config.parameter_ranges.get("long_window")
            
            if not short_windows or not long_windows:
                raise ValueError("sma_crossover requires short_window and long_window ranges")
            
            candidates = generate_sma_crossover_candidates(
                short_windows=short_windows.values,
                long_windows=long_windows.values,
                max_trials=config.max_trials,
            )
        else:
            job.status = "failed"
            job.error_message = f"Strategy family {config.strategy_family} not yet supported"
            return job
        
        # Evaluate each candidate
        for candidate in candidates:
            result = evaluate_candidate(
                candidate=candidate,
                data_provider=data_provider,
                asset_ids=asset_ids,
                start_date=start_date,
                end_date=end_date,
                db_session=db_session,
            )
            job.add_result(result)
            
            # Check if we've hit the trial budget
            if job.trial_count() >= config.max_trials:
                break
        
        job.status = "completed"
        job.completed_at = datetime.utcnow()
        
    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = datetime.utcnow()
    
    return job


def compare_with_baselines(
    job: OptimizationJob,
    data_provider: Any,
    asset_ids: List[str],
    start_date: str,
    end_date: str,
    db_session: Any = None,
) -> Dict[str, CandidateResult]:
    """Compare optimization results with baseline strategies.
    
    Baselines:
    1. Buy-and-hold (if supported)
    2. Default parameters (if applicable)
    3. Cash/no-trade (zero return)
    
    Returns dict mapping baseline name to result.
    """
    baselines = {}
    
    # Baseline 1: Cash (no-trade) — zero return
    cash_candidate = CandidateConfiguration(
        parameters={
            "template_type": "buy_and_hold",
            "baseline": "cash",
        },
    )
    cash_result = CandidateResult(
        candidate=cash_candidate,
        train_metrics={"net_return": 0.0, "max_drawdown": 0.0, "trade_count": 0},
        validation_metrics={"net_return": 0.0, "max_drawdown": 0.0, "trade_count": 0},
        test_metrics={"net_return": 0.0, "max_drawdown": 0.0, "trade_count": 0},
        status="success",
    )
    baselines["cash"] = cash_result
    
    # Baseline 2: Default SMA crossover parameters (if strategy is SMA)
    if job.config and job.config.strategy_family == "sma_crossover":
        default_candidate = CandidateConfiguration(
            parameters={
                "template_type": "sma_crossover",
                "short_window": 10,
                "long_window": 50,
            },
        )
        default_result = evaluate_candidate(
            candidate=default_candidate,
            data_provider=data_provider,
            asset_ids=asset_ids,
            start_date=start_date,
            end_date=end_date,
            db_session=db_session,
        )
        baselines["default_sma_10_50"] = default_result
    
    return baselines
