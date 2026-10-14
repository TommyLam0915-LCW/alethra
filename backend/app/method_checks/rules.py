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


def check_rc004_transaction_costs(strategy_spec: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """RC-004: Transaction Cost Disclosure.

    Check if commission_rate or slippage_rate > 0.
    """
    commission_rate = strategy_spec.get("commission_rate", 0.0)
    slippage_rate = strategy_spec.get("slippage_rate", 0.0)

    evidence = {
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
    }

    if not isinstance(commission_rate, (int, float)) or not isinstance(slippage_rate, (int, float)):
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


def check_rc006_benchmark(strategy_spec: Dict[str, Any], run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-006: Benchmark Comparison Available.

    Check benchmark_id and metrics.benchmark_total_return_pct.
    """
    benchmark_id = strategy_spec.get("benchmark_id")

    evidence = {"benchmark_id": benchmark_id}

    if benchmark_id is None:
        return "NOT_APPLICABLE", evidence

    if not run or getattr(run, "status", None) != "completed":
        return "INSUFFICIENT_INFORMATION", evidence

    metrics = getattr(run, "metrics", None) or {}
    benchmark_return = metrics.get("benchmark_total_return_pct")

    evidence["benchmark_total_return_pct"] = benchmark_return

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

    Check if engine_version is present.
    """
    engine_version = getattr(run, "engine_version", None) if run else None

    evidence = {"engine_version": engine_version}

    if engine_version and str(engine_version).strip():
        return "PASS", evidence

    return "INSUFFICIENT_INFORMATION", evidence


def check_rc009_zero_cost_warnings(strategy_spec: Dict[str, Any], run: Any) -> Tuple[str, Dict[str, Any]]:
    """RC-009: Zero-Cost Warning Consistency.

    If both costs are 0, warnings must contain specific strings.
    """
    commission_rate = strategy_spec.get("commission_rate", 0.0)
    slippage_rate = strategy_spec.get("slippage_rate", 0.0)

    evidence = {
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
    }

    if not run or getattr(run, "status", None) != "completed":
        return "NOT_APPLICABLE", evidence

    if commission_rate != 0.0 or slippage_rate != 0.0:
        return "PASS", evidence

    warnings = getattr(run, "warnings", None) or []
    if not isinstance(warnings, list):
        warnings = []

    evidence["warnings"] = warnings

    required_warnings = [
        "Zero commission selected — real trading incurs costs.",
        "Zero slippage selected — real execution incurs slippage.",
    ]

    has_all = all(rw in warnings for rw in required_warnings)

    if has_all:
        return "PASS", evidence

    evidence["warning"] = "Zero costs but warnings missing"
    return "WARNING", evidence


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
