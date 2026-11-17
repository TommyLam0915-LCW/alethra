"""Rule evaluation functions for Phase 2B method checks."""
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Expected metric keys for completed runs (from backend/app/engine/metrics.py)
EXPECTED_METRIC_KEYS = {
    "initial_cash",
    "final_equity",
    "total_return_pct",
    "benchmark_total_return_pct",
    "excess_return_pct",
    "max_drawdown_pct",
    "total_trades",
    "total_commissions",
    "total_slippage_cost",
    "time_in_market_pct",
    "trading_days",
    "annualized_return_pct",
    "annualized_volatility_pct",
}


def check_rc001_hypothesis(experiment: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-001: Research Hypothesis Present.

    Check if experiment has original_idea or hypothesis.
    """
    original_idea = getattr(experiment, "original_idea", None)
    hypothesis = getattr(experiment, "hypothesis", None)

    has_original = original_idea and str(original_idea).strip()
    has_hypothesis = hypothesis and str(hypothesis).strip()

    evidence = {
        "original_idea_present": bool(has_original),
        "hypothesis_present": bool(has_hypothesis),
    }

    if has_original or has_hypothesis:
        return "PASS", evidence
    return "INSUFFICIENT_INFORMATION", evidence


def check_rc002_falsification(experiment: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-002: Falsification Criteria Present.

    Check if experiment has falsification_conditions.
    """
    falsification = getattr(experiment, "falsification_conditions", None)
    has_falsification = falsification and str(falsification).strip()

    evidence = {
        "falsification_conditions_present": bool(has_falsification),
    }

    if has_falsification:
        return "PASS", evidence
    return "INSUFFICIENT_INFORMATION", evidence


def check_rc003_date_range(strategy_spec: Dict[str, Any], metrics: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """RC-003: Date Range Validity.

    Parse dates and check trading_days >= 252.
    """
    start_date = strategy_spec.get("start_date")
    end_date = strategy_spec.get("end_date")
    trading_days = metrics.get("trading_days") if metrics else None

    evidence = {
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": trading_days,
    }

    if not start_date or not end_date:
        return "INSUFFICIENT_INFORMATION", evidence

    try:
        start_dt = datetime.fromisoformat(str(start_date))
        end_dt = datetime.fromisoformat(str(end_date))

        if end_dt <= start_dt:
            evidence["date_error"] = "end_date not after start_date"
            return "INSUFFICIENT_INFORMATION", evidence

        if trading_days is None:
            return "INSUFFICIENT_INFORMATION", evidence

        if not isinstance(trading_days, (int, float)):
            return "INSUFFICIENT_INFORMATION", evidence

        if trading_days < 252:
            evidence["warning"] = "Less than 252 trading days"
            return "WARNING", evidence

        return "PASS", evidence

    except (ValueError, TypeError) as e:
        evidence["parse_error"] = str(e)
        return "INSUFFICIENT_INFORMATION", evidence


def _is_valid_cost(value: Any) -> bool:
    """Check if a cost value is a valid non-negative finite number.

    Rejects: None, booleans, strings, negative, non-finite (NaN/inf).
    Accepts: int or float that is >= 0 and finite.
    Canonical numeric strings are NOT accepted — callers must pre-convert.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    import math
    if math.isnan(value) or math.isinf(value):
        return False
    if value < 0:
        return False
    return True


def check_rc004_transaction_costs(strategy_spec: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """RC-004: Transaction Cost Disclosure.

    Check if commission_rate or slippage_rate are present and valid.
    - Missing or invalid: INSUFFICIENT_INFORMATION
    - Both valid zero: WARNING
    - At least one valid positive: PASS (configuration disclosure only)
    """
    commission_present = "commission_rate" in strategy_spec
    slippage_present = "slippage_rate" in strategy_spec

    commission_rate = strategy_spec.get("commission_rate")
    slippage_rate = strategy_spec.get("slippage_rate")

    evidence = {
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
        "commission_present": commission_present,
        "slippage_present": slippage_present,
    }

    if not commission_present or not slippage_present:
        evidence["reason"] = "Cost field(s) missing from strategy_spec"
        return "INSUFFICIENT_INFORMATION", evidence

    commission_valid = _is_valid_cost(commission_rate)
    slippage_valid = _is_valid_cost(slippage_rate)

    if not commission_valid or not slippage_valid:
        evidence["reason"] = "Cost value(s) invalid (negative, non-finite, boolean, or non-numeric)"
        return "INSUFFICIENT_INFORMATION", evidence

    if commission_rate == 0.0 and slippage_rate == 0.0:
        evidence["warning"] = "Both commission and slippage are zero"
        return "WARNING", evidence

    return "PASS", evidence


def check_rc005_trade_count(run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-005: Trade Count Available.

    Check if metrics.total_trades is present and >= 0.
    """
    if not run or getattr(run, "status", None) != "completed":
        return "INSUFFICIENT_INFORMATION", {"run_status": getattr(run, "status", None) if run else None}

    metrics = getattr(run, "metrics", None) or {}
    total_trades = metrics.get("total_trades")

    evidence = {"total_trades": total_trades}

    if total_trades is None:
        return "INSUFFICIENT_INFORMATION", evidence

    if not isinstance(total_trades, (int, float)):
        return "INSUFFICIENT_INFORMATION", evidence

    if total_trades == 0:
        evidence["warning"] = "No trades executed"
        return "WARNING", evidence

    if total_trades >= 0:
        return "PASS", evidence

    return "INSUFFICIENT_INFORMATION", evidence


def _benchmark_intent(strategy_spec: Dict[str, Any]) -> str:
    """Classify the stored benchmark_id against the StrategySpec contract.

    "not_requested" — key absent, None or "". app/api/schemas.py:77 special-cases
    "" so the UI's explicit "no benchmark" is valid input, and
    app/engine/backtest.py:579 treats the same value as falsy; frontend
    benchmarkState.ts:72 renders it as "None requested". The rule must agree.
    "malformed" — present but not a usable identifier (whitespace-only or a
    non-string). StrategySpec rejects these, so seeing one means the value
    arrived outside the create/run contract; it is neither a valid request nor a
    legitimate absence, and reporting it as either would hide the inconsistency.
    "configured" — a non-empty identifier.
    """
    if "benchmark_id" not in strategy_spec:
        return "not_requested"
    benchmark_id = strategy_spec["benchmark_id"]
    if benchmark_id is None:
        return "not_requested"
    if isinstance(benchmark_id, str):
        if benchmark_id == "":
            return "not_requested"
        if not benchmark_id.strip():
            return "malformed"
        return "configured"
    return "malformed"


def check_rc006_benchmark(strategy_spec: Dict[str, Any], run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-006: Benchmark Comparison Available.

    Absent, None and "" all mean no benchmark was requested and make the check
    not applicable. A benchmark that really was requested still reports WARNING
    when its metric is missing on a completed run.
    """
    intent = _benchmark_intent(strategy_spec)
    benchmark_id = strategy_spec.get("benchmark_id")

    evidence = {"benchmark_id": benchmark_id, "benchmark_intent": intent}

    metrics = getattr(run, "metrics", None) or {}
    benchmark_return = metrics.get("benchmark_total_return_pct")
    evidence["benchmark_total_return_pct"] = benchmark_return

    if intent == "not_requested":
        if benchmark_return is not None:
            evidence["anomaly"] = (
                "No benchmark was requested, yet the run records a benchmark return"
            )
        return "NOT_APPLICABLE", evidence

    if intent == "malformed":
        evidence["reason"] = (
            "benchmark_id is present but is not a usable benchmark identifier; "
            "StrategySpec would reject this value, so it did not arrive through the create path"
        )
        return "INSUFFICIENT_INFORMATION", evidence

    if not run or getattr(run, "status", None) != "completed":
        return "INSUFFICIENT_INFORMATION", evidence

    if benchmark_return is None:
        evidence["warning"] = "Benchmark configured but metric missing"
        return "WARNING", evidence

    return "PASS", evidence


def check_rc007_result_completeness(run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-007: Result Completeness.

    Check run status and 13 expected metric keys.
    """
    run_status = getattr(run, "status", None) if run else None

    evidence = {"run_status": run_status}

    if run_status != "completed":
        return "INSUFFICIENT_INFORMATION", evidence

    metrics = getattr(run, "metrics", None) or {}
    present_keys = set(metrics.keys())
    missing_keys = EXPECTED_METRIC_KEYS - present_keys

    evidence["present_keys"] = sorted(list(present_keys))
    evidence["missing_keys"] = sorted(list(missing_keys))

    if missing_keys:
        evidence["warning"] = f"Missing {len(missing_keys)} expected metric keys"
        return "WARNING", evidence

    return "PASS", evidence


def check_rc008_engine_version(run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-008: Engine Version Recorded.

    Only a completed run can carry an engine version: the review loader selects
    ``status == completed``, and the run path writes ``engine_version`` when it
    inserts the row. So an absent run is the pre-first-run state, while a run
    with a blank version is a row the current run path did not create.
    """
    engine_version = getattr(run, "engine_version", None) if run else None

    evidence = {
        "engine_version": engine_version,
        "run_present": run is not None,
    }

    if engine_version and str(engine_version).strip():
        return "PASS", evidence

    return "INSUFFICIENT_INFORMATION", evidence


COMMISSION_ZERO_WARNING = "Zero commission selected — real trading incurs costs."
SLIPPAGE_ZERO_WARNING = "Zero slippage selected — real execution incurs slippage."


def check_rc009_zero_cost_warnings(strategy_spec: Dict[str, Any], run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-009: Zero-Cost Warning Consistency (per-component).

    For a completed run with valid cost data:
    - commission=0 requires the exact commission warning
    - slippage=0 requires the exact slippage warning
    - both zero require both warnings
    - both positive require neither (PASS)
    - missing/invalid costs → INSUFFICIENT_INFORMATION
    - no completed run → NOT_APPLICABLE
    """
    commission_present = "commission_rate" in strategy_spec
    slippage_present = "slippage_rate" in strategy_spec

    commission_rate = strategy_spec.get("commission_rate")
    slippage_rate = strategy_spec.get("slippage_rate")

    evidence = {
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
    }

    if not run or getattr(run, "status", None) != "completed":
        return "NOT_APPLICABLE", evidence

    if not commission_present or not slippage_present:
        evidence["reason"] = "Cost field(s) missing from strategy_spec"
        return "INSUFFICIENT_INFORMATION", evidence

    if not _is_valid_cost(commission_rate) or not _is_valid_cost(slippage_rate):
        evidence["reason"] = "Cost value(s) invalid"
        return "INSUFFICIENT_INFORMATION", evidence

    warnings = getattr(run, "warnings", None) or []
    if not isinstance(warnings, list):
        warnings = []

    evidence["warnings"] = warnings

    required = []
    missing = []

    if commission_rate == 0.0:
        required.append(COMMISSION_ZERO_WARNING)
        if COMMISSION_ZERO_WARNING not in warnings:
            missing.append(COMMISSION_ZERO_WARNING)

    if slippage_rate == 0.0:
        required.append(SLIPPAGE_ZERO_WARNING)
        if SLIPPAGE_ZERO_WARNING not in warnings:
            missing.append(SLIPPAGE_ZERO_WARNING)

    evidence["required_warnings"] = required
    evidence["missing_warnings"] = missing

    if not required:
        return "PASS", evidence

    if missing:
        return "WARNING", evidence

    return "PASS", evidence


def check_rc010_config_hash(version: Any, run: Any) -> Tuple[str, Optional[bool], Dict[str, Any]]:
    """RC-010: Config Hash Integrity.

    Compare ExperimentVersion.config_hash and ResearchRun.config_hash.
    Returns (status, config_hash_match, evidence).
    """
    version_hash = getattr(version, "config_hash", None) if version else None
    run_hash = getattr(run, "config_hash", None) if run else None

    evidence = {
        "version_config_hash": version_hash,
        "run_config_hash": run_hash,
    }

    if version_hash is None or run_hash is None:
        return "INSUFFICIENT_INFORMATION", None, evidence

    if version_hash == run_hash:
        return "PASS", True, evidence

    evidence["warning"] = "Config hashes do not match"
    return "WARNING", False, evidence
