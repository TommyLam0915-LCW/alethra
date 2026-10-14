"""Deterministic explanation templates for method checks."""
from typing import Any, Dict, Tuple


def generate_explanation(rule_id: str, status: str, evidence: Dict[str, Any]) -> str:
    """Generate deterministic explanation for a check result.

    Format: A. Observation: ... B. Interpretation: ... C. Limitation: ...
    """
    if rule_id == "RC-001":
        return _explain_rc001(status, evidence)
    elif rule_id == "RC-002":
        return _explain_rc002(status, evidence)
    elif rule_id == "RC-003":
        return _explain_rc003(status, evidence)
    elif rule_id == "RC-004":
        return _explain_rc004(status, evidence)
    elif rule_id == "RC-005":
        return _explain_rc005(status, evidence)
    elif rule_id == "RC-006":
        return _explain_rc006(status, evidence)
    elif rule_id == "RC-007":
        return _explain_rc007(status, evidence)
    elif rule_id == "RC-008":
        return _explain_rc008(status, evidence)
    elif rule_id == "RC-009":
        return _explain_rc009(status, evidence)
    elif rule_id == "RC-010":
        return _explain_rc010(status, evidence)
    return ""


def _explain_rc001(status: str, evidence: Dict[str, Any]) -> str:
    if status == "PASS":
        return (
            "A. Observation: Research hypothesis or original idea is present.\n"
            "B. Interpretation: The experiment has a documented research question.\n"
            "C. Limitation: This does not evaluate hypothesis quality or scientific rigor."
        )
    return (
        "A. Observation: No research hypothesis or original idea found.\n"
        "B. Interpretation: The experiment lacks a documented research question.\n"
        "C. Limitation: This check only verifies presence, not quality."
    )


def _explain_rc002(status: str, evidence: Dict[str, Any]) -> str:
    if status == "PASS":
        return (
            "A. Observation: Falsification criteria are present.\n"
            "B. Interpretation: The experiment defines conditions for rejection.\n"
            "C. Limitation: This does not evaluate criterion appropriateness."
        )
    return (
        "A. Observation: Falsification criteria are missing.\n"
        "B. Interpretation: The experiment lacks explicit rejection conditions.\n"
        "C. Limitation: This check only verifies presence, not quality."
    )


def _explain_rc003(status: str, evidence: Dict[str, Any]) -> str:
    start = evidence.get("start_date", "N/A")
    end = evidence.get("end_date", "N/A")
    trading_days = evidence.get("trading_days", "N/A")

    if status == "PASS":
        return (
            f"A. Observation: Date range {start} to {end} covers {trading_days} trading days.\n"
            "B. Interpretation: At or above the engine's 252-day threshold; annualized metrics are computed under the current engine rule.\n"
            "C. Limitation: The 252-day threshold does not establish statistical validity, data quality, or predictive performance."
        )
    elif status == "WARNING":
        return (
            f"A. Observation: Date range {start} to {end} covers only {trading_days} trading days (< 252).\n"
            "B. Interpretation: Below the engine's 252-day threshold; annualized metrics are not provided under that rule.\n"
            "C. Limitation: The 252-day threshold does not establish statistical validity, data quality, or predictive performance."
        )
    return (
        "A. Observation: Date range or trading days data is missing or invalid.\n"
        "B. Interpretation: Cannot verify date range validity.\n"
        "C. Limitation: This check requires valid dates and trading day count."
    )


def _explain_rc004(status: str, evidence: Dict[str, Any]) -> str:
    commission = evidence.get("commission_rate")
    slippage = evidence.get("slippage_rate")

    if status == "PASS":
        return (
            f"A. Observation: Transaction costs configured (commission: {commission}, slippage: {slippage}).\n"
            "B. Interpretation: Cost configuration is present.\n"
            "C. Limitation: This verifies configuration values only, not that costs were applied in execution, are realistic, or are complete."
        )
    elif status == "WARNING":
        return (
            f"A. Observation: Both commission ({commission}) and slippage ({slippage}) are zero.\n"
            "B. Interpretation: Strategy does not account for trading costs. Real trading incurs costs.\n"
            "C. Limitation: This check only verifies configuration values."
        )
    return (
        "A. Observation: Transaction cost data is missing or invalid.\n"
        "B. Interpretation: Cannot evaluate cost configuration.\n"
        "C. Limitation: This check requires both commission_rate and slippage_rate to be present as valid non-negative finite numbers."
    )


def _explain_rc005(status: str, evidence: Dict[str, Any]) -> str:
    total_trades = evidence.get("total_trades", "N/A")

    if status == "PASS":
        return (
            f"A. Observation: {total_trades} trades were executed.\n"
            "B. Interpretation: Strategy generated trade activity.\n"
            "C. Limitation: Trade count does not indicate strategy quality."
        )
    elif status == "WARNING":
        return (
            "A. Observation: Zero trades were executed.\n"
            "B. Interpretation: Strategy did not generate any trades.\n"
            "C. Limitation: This may indicate strategy issues or unsuitable parameters."
        )
    return (
        "A. Observation: Trade count data is not available.\n"
        "B. Interpretation: Cannot evaluate trade activity.\n"
        "C. Limitation: This check requires a completed run with metrics."
    )


def _explain_rc006(status: str, evidence: Dict[str, Any]) -> str:
    benchmark_id = evidence.get("benchmark_id")

    if status == "PASS":
        benchmark_return = evidence.get("benchmark_total_return_pct", "N/A")
        return (
            f"A. Observation: Benchmark {benchmark_id} configured with return {benchmark_return}%.\n"
            "B. Interpretation: Benchmark comparison is available.\n"
            "C. Limitation: Benchmark availability does not guarantee appropriate comparison."
        )
    elif status == "WARNING":
        return (
            f"A. Observation: Benchmark {benchmark_id} configured but metric is missing.\n"
            "B. Interpretation: Benchmark comparison cannot be performed.\n"
            "C. Limitation: This indicates incomplete run results."
        )
    elif status == "NOT_APPLICABLE":
        return (
            "A. Observation: No benchmark configured.\n"
            "B. Interpretation: Benchmark comparison is not applicable.\n"
            "C. Limitation: Benchmark comparison requires explicit configuration."
        )
    return (
        "A. Observation: Benchmark data is not available.\n"
        "B. Interpretation: Cannot evaluate benchmark comparison.\n"
        "C. Limitation: This check requires a completed run with benchmark data."
    )


def _explain_rc007(status: str, evidence: Dict[str, Any]) -> str:
    run_status = evidence.get("run_status")

    if status == "PASS":
        return (
            "A. Observation: Run completed with all 13 expected metric keys present.\n"
            "B. Interpretation: Result data is complete.\n"
            "C. Limitation: Completeness does not indicate metric accuracy."
        )
    elif status == "WARNING":
        missing = evidence.get("missing_keys", [])
        return (
            f"A. Observation: Run completed but {len(missing)} metric keys are missing.\n"
            "B. Interpretation: Result data is incomplete.\n"
            "C. Limitation: Missing metrics may affect analysis."
        )
    return (
        f"A. Observation: Run status is {run_status}, not completed.\n"
        "B. Interpretation: Cannot evaluate result completeness.\n"
        "C. Limitation: This check requires a completed run."
    )


def _explain_rc008(status: str, evidence: Dict[str, Any]) -> str:
    engine_version = evidence.get("engine_version")

    if status == "PASS":
        return (
            f"A. Observation: Engine version {engine_version} is recorded.\n"
            "B. Interpretation: Run was executed with a specific engine version.\n"
            "C. Limitation: Engine version does not guarantee correctness."
        )
    return (
        "A. Observation: Engine version is not recorded.\n"
        "B. Interpretation: Cannot verify engine version.\n"
        "C. Limitation: This should not occur given database constraints."
    )


def _explain_rc009(status: str, evidence: Dict[str, Any]) -> str:
    commission = evidence.get("commission_rate")
    slippage = evidence.get("slippage_rate")
    required = evidence.get("required_warnings", [])
    missing = evidence.get("missing_warnings", [])

    if status == "PASS":
        if required:
            return (
                f"A. Observation: Zero-cost components (commission: {commission}, slippage: {slippage}) have matching warnings.\n"
                "B. Interpretation: Zero-cost disclosure is consistent with configuration.\n"
                "C. Limitation: This checks warning presence only, not that warnings are adequate, understood, or that trading costs were actually applied."
            )
        return (
            f"A. Observation: Transaction costs are positive (commission: {commission}, slippage: {slippage}).\n"
            "B. Interpretation: Zero-cost warnings are not required.\n"
            "C. Limitation: This check only applies to zero-cost components."
        )
    elif status == "WARNING":
        missing_desc = "; ".join(missing) if missing else "unknown"
        return (
            f"A. Observation: Zero-cost components configured (commission: {commission}, slippage: {slippage}) but required warnings missing: {missing_desc}.\n"
            "B. Interpretation: Engine warning inconsistency detected.\n"
            "C. Limitation: This indicates a potential issue with warning generation."
        )
    elif status == "INSUFFICIENT_INFORMATION":
        return (
            "A. Observation: Transaction cost data is missing or invalid.\n"
            "B. Interpretation: Cannot evaluate zero-cost warning consistency.\n"
            "C. Limitation: This check requires valid cost configuration and a completed run."
        )
    return (
        "A. Observation: Run is not completed.\n"
        "B. Interpretation: Zero-cost warning check not applicable.\n"
        "C. Limitation: This check requires a completed run."
    )


def _explain_rc010(status: str, evidence: Dict[str, Any]) -> str:
    version_hash = evidence.get("version_config_hash")
    run_hash = evidence.get("run_config_hash")

    if status == "PASS":
        return (
            "A. Observation: Version and run config hashes match.\n"
            "B. Interpretation: Run was executed with the version's stored configuration.\n"
            "C. Limitation: Hash equality confirms stored-hash consistency only."
        )
    elif status == "WARNING":
        return (
            f"A. Observation: Version hash ({version_hash}) differs from run hash ({run_hash}).\n"
            "B. Interpretation: Run may not reflect current configuration.\n"
            "C. Limitation: This indicates potential configuration drift."
        )
    return (
        "A. Observation: One or both config hashes are missing.\n"
        "B. Interpretation: Cannot verify config hash integrity.\n"
        "C. Limitation: Hash comparison requires both values to be present."
    )
