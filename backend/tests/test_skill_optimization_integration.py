"""Integration test for skill optimization workflow.

Demonstrates the complete optimization path:
skill configuration → bounded candidate generation → evaluation → comparison → selection
"""

import pytest
from decimal import Decimal

from app.services.skill_optimization import (
    ParameterRange,
    OptimizationConfig,
    generate_sma_crossover_candidates,
    OptimizationJob,
    CandidateResult,
    compare_with_baselines,
)


class TestOptimizationWorkflow:
    """End-to-end optimization workflow tests."""
    
    def test_complete_sma_optimization_workflow(self):
        """Complete workflow: generate → evaluate → compare → select.
        
        This test demonstrates the full optimization path without auto-adoption.
        """
        # Step 1: Configure optimization
        config = OptimizationConfig(
            strategy_family="sma_crossover",
            parameter_ranges={
                "short_window": ParameterRange("short_window", [5, 10, 15]),
                "long_window": ParameterRange("long_window", [30, 50, 75]),
            },
            max_trials=9,  # 3 × 3 = 9 combinations
            train_fraction=0.6,
            validation_fraction=0.2,
            test_fraction=0.2,
        )
        
        # Step 2: Generate bounded candidates
        candidates = generate_sma_crossover_candidates(
            short_windows=config.parameter_ranges["short_window"].values,
            long_windows=config.parameter_ranges["long_window"].values,
            max_trials=config.max_trials,
        )
        
        assert len(candidates) > 0
        assert len(candidates) <= config.max_trials
        
        # Step 3: Create optimization job
        job = OptimizationJob(config=config)
        assert job.status == "pending"
        
        # Step 4: Evaluate candidates (simulated)
        for candidate in candidates:
            # In production, this would call evaluate_candidate with real data
            # For this test, we simulate results
            result = CandidateResult(
                candidate=candidate,
                train_metrics={
                    "net_return": 0.05 + (candidate.parameters["short_window"] * 0.001),
                    "max_drawdown": -0.10,
                    "trade_count": 15,
                },
                validation_metrics={
                    "net_return": 0.03 + (candidate.parameters["short_window"] * 0.001),
                    "max_drawdown": -0.08,
                    "trade_count": 10,
                },
                test_metrics={
                    "net_return": 0.02 + (candidate.parameters["short_window"] * 0.001),
                    "max_drawdown": -0.12,
                    "trade_count": 8,
                },
                status="success",
            )
            job.add_result(result)
        
        job.status = "completed"
        
        # Step 5: Verify all candidates recorded
        assert job.trial_count() == len(candidates)
        assert len(job.successful_candidates()) == len(candidates)
        
        # Step 6: Compare with baselines
        baselines = compare_with_baselines(
            job=job,
            data_provider=None,
            asset_ids=["DEMO-ALPHA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        
        assert "cash" in baselines
        assert baselines["cash"].test_metrics["net_return"] == 0.0
        
        # Step 7: Select best by validation (NOT test!)
        best = job.best_by_validation("net_return")
        assert best is not None
        
        # Step 8: Verify selection is explicit (not auto-adopted)
        # The job records the best candidate but does NOT activate it
        assert job.status == "completed"
        assert best.candidate.parameters["short_window"] in [5, 10, 15]
        assert best.candidate.parameters["long_window"] in [30, 50, 75]
        
        # Step 9: Verify test metrics are separate from selection
        # Best by validation may not be best by test (no overfitting to test)
        test_returns = [c.test_metrics["net_return"] for c in job.successful_candidates()]
        validation_returns = [c.validation_metrics["net_return"] for c in job.successful_candidates()]
        
        # Selection was based on validation, not test
        assert best.validation_metrics["net_return"] == max(validation_returns)
    
    def test_optimization_respects_budget(self):
        """Optimization does not exceed max_trials budget."""
        config = OptimizationConfig(
            strategy_family="sma_crossover",
            parameter_ranges={
                "short_window": ParameterRange("short_window", [5, 10, 15, 20, 25, 30]),
                "long_window": ParameterRange("long_window", [50, 100, 150, 200]),
            },
            max_trials=5,  # Budget: only 5 trials
        )
        
        candidates = generate_sma_crossover_candidates(
            short_windows=config.parameter_ranges["short_window"].values,
            long_windows=config.parameter_ranges["long_window"].values,
            max_trials=config.max_trials,
        )
        
        # Should generate at most 5 candidates, not all 24 (6 × 4)
        assert len(candidates) <= 5
        assert len(candidates) > 0
    
    def test_optimization_records_all_attempts(self):
        """All attempted configurations are recorded, not just winners."""
        job = OptimizationJob()
        
        # Add successful candidate
        from app.services.skill_optimization import CandidateConfiguration
        c1 = CandidateConfiguration(parameters={"short_window": 5, "long_window": 30})
        job.add_result(CandidateResult(c1, {}, {"net_return": 0.05}, {}, "success"))
        
        # Add failed candidate
        c2 = CandidateConfiguration(parameters={"short_window": 10, "long_window": 50})
        job.add_result(CandidateResult(c2, {}, {}, {}, "failed", error_message="Insufficient data"))
        
        # Add rejected candidate
        c3 = CandidateConfiguration(parameters={"short_window": 15, "long_window": 75})
        job.add_result(CandidateResult(c3, {}, {}, {}, "rejected"))
        
        # All recorded
        assert len(job.candidates) == 3
        
        # Trial count includes success + failed, not rejected
        assert job.trial_count() == 2
        
        # Only successful candidates considered for selection
        successful = job.successful_candidates()
        assert len(successful) == 1
        assert successful[0].candidate.parameters["short_window"] == 5
    
    def test_no_auto_adoption(self):
        """Optimization does NOT auto-adopt the best candidate."""
        config = OptimizationConfig(
            strategy_family="sma_crossover",
            parameter_ranges={
                "short_window": ParameterRange("short_window", [5, 10]),
                "long_window": ParameterRange("long_window", [30, 50]),
            },
            max_trials=4,
        )
        
        job = OptimizationJob(config=config)
        
        # Simulate evaluation
        from app.services.skill_optimization import CandidateConfiguration
        for short_w in [5, 10]:
            for long_w in [30, 50]:
                candidate = CandidateConfiguration(
                    parameters={"short_window": short_w, "long_window": long_w}
                )
                result = CandidateResult(
                    candidate=candidate,
                    train_metrics={},
                    validation_metrics={"net_return": 0.05},
                    test_metrics={"net_return": 0.03},
                    status="success",
                )
                job.add_result(result)
        
        job.status = "completed"
        
        # Find best candidate
        best = job.best_by_validation("net_return")
        assert best is not None
        
        # Job is completed, but NO candidate is "adopted" or "activated"
        # The user must explicitly select and deploy
        assert job.status == "completed"
        assert not hasattr(job, "adopted_candidate_id")
        assert not hasattr(job, "activated_parameters")
        
        # The optimization service provides information, not action
        # User workflow: review → compare → explicitly select → deploy separately
    
    def test_chronological_split_integrity(self):
        """Train/validation/test splits are chronological, no future leakage."""
        from app.services.skill_optimization import split_data_chronological
        
        train, val, test = split_data_chronological(
            data_length=1000,
            train_fraction=0.6,
            validation_fraction=0.2,
        )
        
        # Chronological order: train < val < test
        assert train[1] < val[0], "Train must end before validation starts"
        assert val[1] < test[0], "Validation must end before test starts"
        
        # No overlap
        assert train[1] < test[0], "Train must end before test starts"
        
        # Test is strictly held-out (comes after train+val)
        # Parameters frozen on train+val, evaluated on test
        # Test data cannot influence parameter selection
    
    def test_baseline_comparison_honest(self):
        """Baselines include cash (no-trade) and default parameters."""
        job = OptimizationJob()
        
        baselines = compare_with_baselines(
            job=job,
            data_provider=None,
            asset_ids=["DEMO-ALPHA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        
        # Cash baseline always present
        assert "cash" in baselines
        cash = baselines["cash"]
        assert cash.test_metrics["net_return"] == 0.0
        assert cash.test_metrics["trade_count"] == 0
        assert cash.status == "success"
        
        # Cash baseline represents "do nothing" — a valid comparison
        # Any candidate must beat cash to be worthwhile
    
    def test_candidate_fingerprint_deduplication(self):
        """Same parameters produce same fingerprint for deduplication."""
        from app.services.skill_optimization import CandidateConfiguration
        
        c1 = CandidateConfiguration(parameters={"short_window": 10, "long_window": 50})
        c2 = CandidateConfiguration(parameters={"short_window": 10, "long_window": 50})
        c3 = CandidateConfiguration(parameters={"short_window": 10, "long_window": 75})
        
        # Same parameters → same fingerprint
        assert c1.fingerprint() == c2.fingerprint()
        
        # Different parameters → different fingerprint
        assert c1.fingerprint() != c3.fingerprint()
        
        # Fingerprint enables deduplication in optimization loop
