"""Comprehensive tests for Phase 2B method checks."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4, UUID
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
from app.method_checks.evaluator import evaluate_checks
from app.method_checks.explanations import generate_explanation
from app.models.experiment import Experiment, ExperimentStatus
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User


# ============================================================================
# Unit tests for individual rules
# ============================================================================

class TestRC001Hypothesis:
    def test_pass_with_original_idea(self):
        exp = Experiment(original_idea="Test idea", hypothesis=None)
        status, evidence = check_rc001_hypothesis(exp)
        assert status == "PASS"
        assert evidence["original_idea_present"] is True
        assert evidence["hypothesis_present"] is False

    def test_pass_with_hypothesis(self):
        exp = Experiment(original_idea=None, hypothesis="Test hypothesis")
        status, evidence = check_rc001_hypothesis(exp)
        assert status == "PASS"
        assert evidence["original_idea_present"] is False
        assert evidence["hypothesis_present"] is True

    def test_pass_with_both(self):
        exp = Experiment(original_idea="Idea", hypothesis="Hypothesis")
        status, evidence = check_rc001_hypothesis(exp)
        assert status == "PASS"
        assert evidence["original_idea_present"] is True
        assert evidence["hypothesis_present"] is True

    def test_insufficient_both_null(self):
        exp = Experiment(original_idea=None, hypothesis=None)
        status, evidence = check_rc001_hypothesis(exp)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_both_empty(self):
        exp = Experiment(original_idea="   ", hypothesis="")
        status, evidence = check_rc001_hypothesis(exp)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC002Falsification:
    def test_pass(self):
        exp = Experiment(falsification_conditions="Drawdown > 20%")
        status, evidence = check_rc002_falsification(exp)
        assert status == "PASS"
        assert evidence["falsification_conditions_present"] is True

    def test_insufficient_null(self):
        exp = Experiment(falsification_conditions=None)
        status, evidence = check_rc002_falsification(exp)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_empty(self):
        exp = Experiment(falsification_conditions="   ")
        status, evidence = check_rc002_falsification(exp)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC003DateRange:
    def test_pass(self):
        spec = {"start_date": "2023-01-01", "end_date": "2024-01-01"}
        metrics = {"trading_days": 252}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "PASS"

    def test_warning_less_than_252(self):
        spec = {"start_date": "2023-01-01", "end_date": "2023-06-01"}
        metrics = {"trading_days": 100}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "WARNING"

    def test_insufficient_missing_dates(self):
        spec = {"start_date": None, "end_date": "2024-01-01"}
        metrics = {"trading_days": 252}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_invalid_date_format(self):
        spec = {"start_date": "invalid", "end_date": "2024-01-01"}
        metrics = {"trading_days": 252}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_missing_trading_days(self):
        spec = {"start_date": "2023-01-01", "end_date": "2024-01-01"}
        metrics = {}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_end_before_start(self):
        spec = {"start_date": "2024-01-01", "end_date": "2023-01-01"}
        metrics = {"trading_days": 252}
        status, evidence = check_rc003_date_range(spec, metrics)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC004TransactionCosts:
    def test_pass_with_commission(self):
        spec = {"commission_rate": 0.001, "slippage_rate": 0.0}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "PASS"

    def test_pass_with_slippage(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "PASS"

    def test_pass_both_positive(self):
        spec = {"commission_rate": 0.001, "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "PASS"

    def test_warning_both_zero(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "WARNING"

    def test_insufficient_empty_strategy_spec(self):
        spec = {}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"
        assert evidence["commission_present"] is False
        assert evidence["slippage_present"] is False

    def test_insufficient_missing_commission(self):
        spec = {"slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"
        assert evidence["commission_present"] is False

    def test_insufficient_missing_slippage(self):
        spec = {"commission_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"
        assert evidence["slippage_present"] is False

    def test_insufficient_null_commission(self):
        spec = {"commission_rate": None, "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_negative_commission(self):
        spec = {"commission_rate": -0.001, "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_boolean_commission(self):
        spec = {"commission_rate": True, "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_nan_commission(self):
        import math
        spec = {"commission_rate": float("nan"), "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_inf_slippage(self):
        spec = {"commission_rate": 0.001, "slippage_rate": float("inf")}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_string_cost(self):
        spec = {"commission_rate": "0.001", "slippage_rate": 0.001}
        status, evidence = check_rc004_transaction_costs(spec)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC005TradeCount:
    def test_pass(self):
        run = ResearchRun(status=RunStatus.completed, metrics={"total_trades": 100})
        status, evidence = check_rc005_trade_count(run)
        assert status == "PASS"
        assert evidence["total_trades"] == 100

    def test_warning_zero_trades(self):
        run = ResearchRun(status=RunStatus.completed, metrics={"total_trades": 0})
        status, evidence = check_rc005_trade_count(run)
        assert status == "WARNING"

    def test_insufficient_not_completed(self):
        run = ResearchRun(status=RunStatus.pending, metrics={"total_trades": 100})
        status, evidence = check_rc005_trade_count(run)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_no_run(self):
        status, evidence = check_rc005_trade_count(None)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_missing_metric(self):
        run = ResearchRun(status=RunStatus.completed, metrics={})
        status, evidence = check_rc005_trade_count(run)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC006Benchmark:
    def test_pass(self):
        spec = {"benchmark_id": "DEMO-BENCHMARK"}
        run = ResearchRun(status=RunStatus.completed, metrics={"benchmark_total_return_pct": 10.5})
        status, evidence = check_rc006_benchmark(spec, run)
        assert status == "PASS"

    def test_warning_benchmark_configured_but_missing(self):
        spec = {"benchmark_id": "DEMO-BENCHMARK"}
        run = ResearchRun(status=RunStatus.completed, metrics={})
        status, evidence = check_rc006_benchmark(spec, run)
        assert status == "WARNING"

    def test_not_applicable_no_benchmark(self):
        spec = {"benchmark_id": None}
        run = ResearchRun(status=RunStatus.completed, metrics={"benchmark_total_return_pct": 10.5})
        status, evidence = check_rc006_benchmark(spec, run)
        assert status == "NOT_APPLICABLE"

    def test_insufficient_not_completed(self):
        spec = {"benchmark_id": "DEMO-BENCHMARK"}
        run = ResearchRun(status=RunStatus.pending, metrics={})
        status, evidence = check_rc006_benchmark(spec, run)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC007ResultCompleteness:
    def test_pass(self):
        metrics = {
            "initial_cash": 100000.0,
            "final_equity": 110000.0,
            "total_return_pct": 10.0,
            "benchmark_total_return_pct": 8.0,
            "excess_return_pct": 2.0,
            "max_drawdown_pct": 5.0,
            "total_trades": 100,
            "total_commissions": 50.0,
            "total_slippage_cost": 30.0,
            "time_in_market_pct": 85.0,
            "trading_days": 252,
            "annualized_return_pct": 8.0,
            "annualized_volatility_pct": 15.0,
        }
        run = ResearchRun(status=RunStatus.completed, metrics=metrics)
        status, evidence = check_rc007_result_completeness(run)
        assert status == "PASS"

    def test_warning_missing_keys(self):
        metrics = {"total_trades": 100}
        run = ResearchRun(status=RunStatus.completed, metrics=metrics)
        status, evidence = check_rc007_result_completeness(run)
        assert status == "WARNING"
        assert len(evidence["missing_keys"]) > 0

    def test_insufficient_not_completed(self):
        run = ResearchRun(status=RunStatus.pending, metrics={})
        status, evidence = check_rc007_result_completeness(run)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC008EngineVersion:
    def test_pass(self):
        run = ResearchRun(engine_version="1.0.0")
        status, evidence = check_rc008_engine_version(run)
        assert status == "PASS"

    def test_insufficient_null(self):
        run = ResearchRun(engine_version=None)
        status, evidence = check_rc008_engine_version(run)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_empty(self):
        run = ResearchRun(engine_version="   ")
        status, evidence = check_rc008_engine_version(run)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC009ZeroCostWarnings:
    def test_pass_both_positive(self):
        spec = {"commission_rate": 0.001, "slippage_rate": 0.001}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "PASS"
        assert evidence["required_warnings"] == []

    def test_pass_commission_zero_with_warning(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.001}
        warnings = ["Zero commission selected — real trading incurs costs."]
        run = ResearchRun(status=RunStatus.completed, warnings=warnings)
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "PASS"
        assert len(evidence["required_warnings"]) == 1
        assert evidence["missing_warnings"] == []

    def test_warning_commission_zero_missing_warning(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.001}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "WARNING"
        assert len(evidence["missing_warnings"]) == 1

    def test_pass_slippage_zero_with_warning(self):
        spec = {"commission_rate": 0.001, "slippage_rate": 0.0}
        warnings = ["Zero slippage selected — real execution incurs slippage."]
        run = ResearchRun(status=RunStatus.completed, warnings=warnings)
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "PASS"
        assert len(evidence["required_warnings"]) == 1

    def test_warning_slippage_zero_missing_warning(self):
        spec = {"commission_rate": 0.001, "slippage_rate": 0.0}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "WARNING"
        assert len(evidence["missing_warnings"]) == 1

    def test_pass_both_zero_all_warnings(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        warnings = [
            "Zero commission selected — real trading incurs costs.",
            "Zero slippage selected — real execution incurs slippage.",
        ]
        run = ResearchRun(status=RunStatus.completed, warnings=warnings)
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "PASS"
        assert len(evidence["required_warnings"]) == 2
        assert evidence["missing_warnings"] == []

    def test_warning_both_zero_one_warning_missing(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        warnings = ["Zero commission selected — real trading incurs costs."]
        run = ResearchRun(status=RunStatus.completed, warnings=warnings)
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "WARNING"
        assert len(evidence["required_warnings"]) == 2
        assert len(evidence["missing_warnings"]) == 1

    def test_warning_both_zero_no_warnings(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "WARNING"
        assert len(evidence["missing_warnings"]) == 2

    def test_not_applicable_not_completed(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        run = ResearchRun(status=RunStatus.pending, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "NOT_APPLICABLE"

    def test_not_applicable_no_run(self):
        spec = {"commission_rate": 0.0, "slippage_rate": 0.0}
        status, evidence = check_rc009_zero_cost_warnings(spec, None)
        assert status == "NOT_APPLICABLE"

    def test_insufficient_missing_costs(self):
        spec = {}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "INSUFFICIENT_INFORMATION"

    def test_insufficient_invalid_costs(self):
        spec = {"commission_rate": None, "slippage_rate": 0.001}
        run = ResearchRun(status=RunStatus.completed, warnings=[])
        status, evidence = check_rc009_zero_cost_warnings(spec, run)
        assert status == "INSUFFICIENT_INFORMATION"


class TestRC010ConfigHash:
    def test_pass_hashes_match(self):
        version = ExperimentVersion(config_hash="abc123")
        run = ResearchRun(config_hash="abc123")
        status, match, evidence = check_rc010_config_hash(version, run)
        assert status == "PASS"
        assert match is True

    def test_warning_hashes_differ(self):
        version = ExperimentVersion(config_hash="abc123")
        run = ResearchRun(config_hash="def456")
        status, match, evidence = check_rc010_config_hash(version, run)
        assert status == "WARNING"
        assert match is False

    def test_insufficient_version_null(self):
        version = ExperimentVersion(config_hash=None)
        run = ResearchRun(config_hash="abc123")
        status, match, evidence = check_rc010_config_hash(version, run)
        assert status == "INSUFFICIENT_INFORMATION"
        assert match is None

    def test_insufficient_run_null(self):
        version = ExperimentVersion(config_hash="abc123")
        run = ResearchRun(config_hash=None)
        status, match, evidence = check_rc010_config_hash(version, run)
        assert status == "INSUFFICIENT_INFORMATION"
        assert match is None


# ============================================================================
# API tests
# ============================================================================

class TestMethodReviewAPI:
    def test_unauthenticated(self, client):
        exp_id = uuid4()
        response = client.get(f"/api/v1/experiments/{exp_id}/method-review")
        assert response.status_code == 401

    def test_nonexistent_experiment(self, client, auth_headers):
        exp_id = uuid4()
        response = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        assert response.status_code == 404

    def test_ownership_check(self, client, auth_headers):
        # Create experiment as different user
        other_user_headers = auth_headers
        resp = client.post("/api/auth/register", json={
            "email": "other@alethra.io",
            "password": "testpass123",
            "display_name": "Other User",
            "user_type": "individual",
        })
        login = client.post("/api/auth/login", json={
            "email": "other@alethra.io",
            "password": "testpass123",
        })
        token = login.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {token}"}

        # Create experiment as other user
        resp = client.post("/api/v1/experiments/", json={
            "title": "Test Experiment",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "initial_cash": 100000,
            }
        }, headers=other_headers)
        exp_id = resp.json()["id"]

        # Try to access as original user
        response = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        assert response.status_code == 404

    def test_no_runs(self, client, auth_headers):
        # Create experiment
        resp = client.post("/api/v1/experiments/", json={
            "title": "Test Experiment",
            "original_idea": "Test idea",
            "hypothesis": "Test hypothesis",
            "falsification_conditions": "Drawdown > 20%",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.001,
            }
        }, headers=auth_headers)
        exp_id = resp.json()["id"]

        # Get method review
        response = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rule_set_version"] == "1.0"
        assert data["experiment_id"] == exp_id
        assert data["run_id"] is None
        assert data["completed_at_available"] is None
        assert len(data["checks"]) == 10

    def test_with_completed_run(self, client, auth_headers, setup_db):
        from app.database import get_db
        from app.main import app

        # Create experiment
        resp = client.post("/api/v1/experiments/", json={
            "title": "Test Experiment",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "benchmark_id": "DEMO-BENCHMARK",
            }
        }, headers=auth_headers)
        exp_id = resp.json()["id"]

        # Create a completed run directly in DB
        from tests.conftest import override_get_db
        db_gen = override_get_db()
        db = next(db_gen)

        version_id = UUID(resp.json()["current_version_id"])
        run = ResearchRun(
            experiment_version_id=version_id,
            engine_version="1.0.0",
            config_hash="abc123",
            status=RunStatus.completed,
            metrics={
                "total_trades": 100,
                "win_rate": 0.6,
                "total_return_pct": 10.0,
                "annualized_return_pct": 8.0,
                "annualized_volatility_pct": 15.0,
                "sharpe_ratio": 1.2,
                "max_drawdown_pct": 5.0,
                "total_commissions": 50.0,
                "total_slippage": 30.0,
                "net_pnl": 9000.0,
                "benchmark_total_return_pct": 8.0,
                "alpha_pct": 2.0,
                "trading_days": 252,
            },
            warnings=[
                "Zero commission selected — real trading incurs costs.",
                "Zero slippage selected — real execution incurs slippage.",
            ],
            completed_at=datetime.utcnow(),
        )
        db.add(run)
        db.commit()

        db_gen.close()

        # Get method review
        response = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] is not None
        assert data["config_hash_match"] is False  # Different hashes
        assert data["completed_at_available"] is True

    def test_determinism(self, client, auth_headers):
        # Create experiment
        resp = client.post("/api/v1/experiments/", json={
            "title": "Test Experiment",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "initial_cash": 100000,
            }
        }, headers=auth_headers)
        exp_id = resp.json()["id"]

        # Call twice
        response1 = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        response2 = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )

        data1 = response1.json()
        data2 = response2.json()

        # generated_at may differ, but checks should be identical
        assert data1["checks"] == data2["checks"]
        assert data1["rule_set_version"] == data2["rule_set_version"]
        assert data1["experiment_id"] == data2["experiment_id"]

    def test_completed_at_available_false_when_run_has_no_timestamp(self, client, auth_headers, setup_db):
        from tests.conftest import override_get_db
        from app.models.research_run import ResearchRun, RunStatus

        resp = client.post("/api/v1/experiments/", json={
            "title": "No Timestamp Test",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "initial_cash": 100000,
            }
        }, headers=auth_headers)
        exp_id = resp.json()["id"]
        version_id = UUID(resp.json()["current_version_id"])

        db_gen = override_get_db()
        db = next(db_gen)

        run = ResearchRun(
            experiment_version_id=version_id,
            engine_version="1.0.0",
            config_hash="abc123",
            status=RunStatus.completed,
            metrics={"total_trades": 10},
            warnings=[],
            completed_at=None,
        )
        db.add(run)
        db.commit()
        db_gen.close()

        response = client.get(
            f"/api/v1/experiments/{exp_id}/method-review",
            headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] is not None
        assert data["completed_at_available"] is False


# ============================================================================
# Explanation tests
# ============================================================================

class TestExplanations:
    def test_all_rules_have_explanations(self):
        rules = ["RC-001", "RC-002", "RC-003", "RC-004", "RC-005",
                 "RC-006", "RC-007", "RC-008", "RC-009", "RC-010"]
        statuses = ["PASS", "WARNING", "INSUFFICIENT_INFORMATION", "NOT_APPLICABLE"]

        for rule in rules:
            for status in statuses:
                explanation = generate_explanation(rule, status, {})
                assert explanation, f"Missing explanation for {rule} {status}"
                assert "A. Observation:" in explanation
                assert "B. Interpretation:" in explanation
                assert "C. Limitation:" in explanation
