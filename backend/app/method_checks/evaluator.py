"""Evaluator orchestrates rule evaluation."""
from typing import Any, Dict, List, Optional, Tuple
from app.method_checks.schemas import CheckResult
from app.method_checks.rules import (
    check_rc001_hypothesis,
    check_rc002_falsification,
    check_rc003_date_range,
    check_rc004_transaction_costs,
    check_rc005_trade_count,
    check_rc006_benchmark,
    check_rc007_result_completeness,
    check_rc008_engine_version,
    check_rc009_zero_cost_warnings,
    check_rc010_config_hash,
)
from app.method_checks.explanations import generate_explanation


def evaluate_checks(
    experiment: Any,
    version: Optional[Any],
    run: Optional[Any],
    strategy_spec: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Tuple[List[CheckResult], Optional[bool]]:
    """Evaluate all 10 rules and return ordered list of CheckResult.

    Args:
        experiment: Experiment object
        version: ExperimentVersion object (nullable)
        run: ResearchRun object (nullable)
        strategy_spec: Strategy specification dict
        metrics: Run metrics dict (may be empty if no run)

    Returns:
        List of 10 CheckResult objects in rule order
    """
    checks = []

    # RC-001: Research Hypothesis Present
    status, evidence = check_rc001_hypothesis(experiment)
    checks.append(CheckResult(
        rule_id="RC-001",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-001", status, evidence),
        limitations=["Does not evaluate hypothesis quality"],
    ))

    # RC-002: Falsification Criteria Present
    status, evidence = check_rc002_falsification(experiment)
    checks.append(CheckResult(
        rule_id="RC-002",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-002", status, evidence),
        limitations=["Does not evaluate criterion quality"],
    ))

    # RC-003: Date Range Validity
    status, evidence = check_rc003_date_range(strategy_spec, metrics)
    checks.append(CheckResult(
        rule_id="RC-003",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-003", status, evidence),
        limitations=["Trading day count does not guarantee data quality"],
    ))

    # RC-004: Transaction Cost Disclosure
    status, evidence = check_rc004_transaction_costs(strategy_spec)
    checks.append(CheckResult(
        rule_id="RC-004",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-004", status, evidence),
        limitations=["Verifies configuration, not actual cost application"],
    ))

    # RC-005: Trade Count Available
    status, evidence = check_rc005_trade_count(run)
    checks.append(CheckResult(
        rule_id="RC-005",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-005", status, evidence),
        limitations=["Trade count does not indicate strategy quality"],
    ))

    # RC-006: Benchmark Comparison Available
    status, evidence = check_rc006_benchmark(strategy_spec, run)
    checks.append(CheckResult(
        rule_id="RC-006",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-006", status, evidence),
        limitations=["Benchmark availability does not guarantee appropriate comparison"],
    ))

    # RC-007: Result Completeness
    status, evidence = check_rc007_result_completeness(run)
    checks.append(CheckResult(
        rule_id="RC-007",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-007", status, evidence),
        limitations=["Completeness does not indicate metric accuracy"],
    ))

    # RC-008: Engine Version Recorded
    status, evidence = check_rc008_engine_version(run)
    checks.append(CheckResult(
        rule_id="RC-008",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-008", status, evidence),
        limitations=["Engine version does not guarantee correctness"],
    ))

    # RC-009: Zero-Cost Warning Consistency
    status, evidence = check_rc009_zero_cost_warnings(strategy_spec, run)
    checks.append(CheckResult(
        rule_id="RC-009",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-009", status, evidence),
        limitations=["Checks warning presence, not user comprehension"],
    ))

    # RC-010: Config Hash Integrity
    status, config_hash_match, evidence = check_rc010_config_hash(version, run)
    checks.append(CheckResult(
        rule_id="RC-010",
        rule_version="1.0",
        status=status,
        evidence=evidence,
        explanation=generate_explanation("RC-010", status, evidence),
        limitations=["Hash equality confirms stored-hash consistency only"],
    ))

    return checks, config_hash_match
