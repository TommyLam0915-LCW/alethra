"""Tests for skill optimization service.

Focused tests for:
- Parameter validation
- Candidate generation with budget
- Chronological split integrity
- Baseline comparison
- Deterministic results
"""

import pytest
from decimal import Decimal

from app.services.skill_optimization import (
    ParameterRange,
    OptimizationConfig,
    CandidateConfiguration,
    generate_sma_crossover_candidates,
    split_data_chronological,
    compare_with_baselines,
    OptimizationJob,
)


class TestParameterRange:
    """Test parameter range validation."""
    
    def test_valid_range(self):
        """Valid parameter range accepted."""
        pr = ParameterRange(name="short_window", values=[5, 10, 15])
        assert pr.name == "short_window"
        assert pr.values == [5, 10, 15]
    
    def test_empty_range_rejected(self):
        """Empty parameter range rejected."""
        with pytest.raises(ValueError, match="at least one value"):
            ParameterRange(name="short_window", values=[])


class TestOptimizationConfig:
    """Test optimization configuration validation."""
    
    def test_valid_config(self):
        """Valid configuration accepted."""
        config = OptimizationConfig(
            strategy_family="sma_crossover",
            parameter_ranges={
                "short_window": ParameterRange("short_window", [5, 10]),
                "long_window": ParameterRange("long_window", [30, 50]),
            },
            max_trials=10,
        )
        assert config.strategy_family == "sma_crossover"
        assert config.max_trials == 10
    
    def test_fractions_must_sum_to_one(self):
        """Train/val/test fractions must sum to 1.0."""
        with pytest.raises(ValueError, match="sum to 1.0"):
            OptimizationConfig(
                strategy_family="sma_crossover",
                parameter_ranges={"short_window": ParameterRange("short_window", [5])},
                max_trials=10,
                train_fraction=0.5,
                validation_fraction=0.3,
                test_fraction=0.1,  # Sum = 0.9, not 1.0
            )
    
    def test_max_trials_must_be_positive(self):
        """max_trials must be >= 1."""
        with pytest.raises(ValueError, match="max_trials must be >= 1"):
            OptimizationConfig(
                strategy_family="sma_crossover",
                parameter_ranges={"short_window": ParameterRange("short_window", [5])},
                max_trials=0,
            )
    
    def test_requires_parameter_ranges(self):
        """At least one parameter range required."""
        with pytest.raises(ValueError, match="At least one parameter range"):
            OptimizationConfig(
                strategy_family="sma_crossover",
                parameter_ranges={},
                max_trials=10,
            )


class TestCandidateConfiguration:
    """Test candidate configuration."""
    
    def test_candidate_has_unique_id(self):
        """Each candidate gets a unique ID."""
        c1 = CandidateConfiguration(parameters={"short_window": 5})
        c2 = CandidateConfiguration(parameters={"short_window": 5})
        assert c1.candidate_id != c2.candidate_id
    
    def test_fingerprint_deterministic(self):
        """Same parameters produce same fingerprint."""
        c1 = CandidateConfiguration(parameters={"short_window": 5, "long_window": 30})
        c2 = CandidateConfiguration(parameters={"short_window": 5, "long_window": 30})
        assert c1.fingerprint() == c2.fingerprint()
    
    def test_fingerprint_differs_for_different_params(self):
        """Different parameters produce different fingerprint."""
        c1 = CandidateConfiguration(parameters={"short_window": 5})
        c2 = CandidateConfiguration(parameters={"short_window": 10})
        assert c1.fingerprint() != c2.fingerprint()
    
    def test_to_strategy_spec(self):
        """Candidate converts to backtest strategy_spec."""
        candidate = CandidateConfiguration(
            parameters={
                "template_type": "sma_crossover",
                "short_window": 10,
                "long_window": 50,
            }
        )
        spec = candidate.to_strategy_spec(
            asset_ids=["DEMO-ALPHA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert spec["template_type"] == "sma_crossover"
        assert spec["signal_parameters"]["short_window"] == 10
        assert spec["signal_parameters"]["long_window"] == 50


class TestGenerateSmaCrossoverCandidates:
    """Test SMA crossover candidate generation."""
    
    def test_generates_valid_candidates(self):
        """Generates candidates within parameter bounds."""
        candidates = generate_sma_crossover_candidates(
            short_windows=[5, 10],
            long_windows=[30, 50],
            max_trials=10,
        )
        assert len(candidates) > 0
        for c in candidates:
            assert c.parameters["template_type"] == "sma_crossover"
            assert "short_window" in c.parameters
            assert "long_window" in c.parameters
    
    def test_respects_max_trials(self):
        """Does not generate more than max_trials candidates."""
        candidates = generate_sma_crossover_candidates(
            short_windows=[5, 10, 15, 20],
            long_windows=[30, 50, 75, 100],
            max_trials=5,
        )
        assert len(candidates) <= 5
    
    def test_short_less_than_long(self):
        """Short window must be less than long window."""
        candidates = generate_sma_crossover_candidates(
            short_windows=[5, 10, 50],
            long_windows=[30, 50, 100],
            max_trials=100,
        )
        for c in candidates:
            assert c.parameters["short_window"] < c.parameters["long_window"]
    
    def test_validates_against_engine_rules(self):
        """Candidates respect engine parameter bounds."""
        candidates = generate_sma_crossover_candidates(
            short_windows=[1, 5, 10, 100],  # 1 and 100 outside bounds
            long_windows=[2, 30, 50, 300],  # 2 and 300 outside bounds
            max_trials=100,
        )
        for c in candidates:
            short = c.parameters["short_window"]
            long = c.parameters["long_window"]
            # Engine rules: short_window 2-60, long_window 3-200
            assert 2 <= short <= 60
            assert 3 <= long <= 200
    
    def test_trial_numbers_sequential(self):
        """Trial numbers are sequential starting from 0."""
        candidates = generate_sma_crossover_candidates(
            short_windows=[5, 10],
            long_windows=[30, 50],
            max_trials=10,
        )
        trial_numbers = [c.trial_number for c in candidates]
        assert trial_numbers == list(range(len(candidates)))


class TestSplitDataChronological:
    """Test chronological data splitting."""
    
    def test_split_produces_three_ranges(self):
        """Split produces train, validation, test ranges."""
        train, val, test = split_data_chronological(
            data_length=100,
            train_fraction=0.6,
            validation_fraction=0.2,
        )
        assert len(train) == 2
        assert len(val) == 2
        assert len(test) == 2
    
    def test_no_overlap(self):
        """Train, validation, test do not overlap."""
        train, val, test = split_data_chronological(
            data_length=100,
            train_fraction=0.6,
            validation_fraction=0.2,
        )
        assert train[1] < val[0]
        assert val[1] < test[0]
    
    def test_chronological_order(self):
        """Test split is strictly after train+validation."""
        train, val, test = split_data_chronological(
            data_length=100,
            train_fraction=0.6,
            validation_fraction=0.2,
        )
        # Test starts after validation ends
        assert test[0] > val[1]
        # Validation starts after train ends
        assert val[0] > train[1]
    
    def test_covers_all_data(self):
        """Split covers all data points."""
        train, val, test = split_data_chronological(
            data_length=100,
            train_fraction=0.6,
            validation_fraction=0.2,
        )
        assert train[0] == 0
        assert test[1] == 99


class TestOptimizationJob:
    """Test optimization job tracking."""
    
    def test_job_starts_pending(self):
        """New job starts in pending status."""
        job = OptimizationJob()
        assert job.status == "pending"
        assert len(job.candidates) == 0
    
    def test_add_result(self):
        """Can add candidate results to job."""
        job = OptimizationJob()
        from app.services.skill_optimization import CandidateResult
        
        candidate = CandidateConfiguration(parameters={"short_window": 5})
        result = CandidateResult(
            candidate=candidate,
            train_metrics={"net_return": 0.1},
            validation_metrics={"net_return": 0.05},
            test_metrics={"net_return": 0.03},
            status="success",
        )
        job.add_result(result)
        assert len(job.candidates) == 1
        assert job.candidates[0].status == "success"
    
    def test_trial_count(self):
        """Trial count includes success and failed, not rejected."""
        job = OptimizationJob()
        from app.services.skill_optimization import CandidateResult
        
        # Add success
        c1 = CandidateConfiguration(parameters={"short_window": 5})
        job.add_result(CandidateResult(c1, {}, {}, {}, "success"))
        
        # Add failed
        c2 = CandidateConfiguration(parameters={"short_window": 10})
        job.add_result(CandidateResult(c2, {}, {}, {}, "failed"))
        
        # Add rejected
        c3 = CandidateConfiguration(parameters={"short_window": 15})
        job.add_result(CandidateResult(c3, {}, {}, {}, "rejected"))
        
        assert job.trial_count() == 2  # success + failed, not rejected
    
    def test_successful_candidates(self):
        """Returns only successful candidates."""
        job = OptimizationJob()
        from app.services.skill_optimization import CandidateResult
        
        c1 = CandidateConfiguration(parameters={"short_window": 5})
        job.add_result(CandidateResult(c1, {}, {}, {}, "success"))
        
        c2 = CandidateConfiguration(parameters={"short_window": 10})
        job.add_result(CandidateResult(c2, {}, {}, {}, "failed"))
        
        successful = job.successful_candidates()
        assert len(successful) == 1
        assert successful[0].candidate.parameters["short_window"] == 5
    
    def test_best_by_validation(self):
        """Returns best candidate by validation metric."""
        job = OptimizationJob()
        from app.services.skill_optimization import CandidateResult
        
        c1 = CandidateConfiguration(parameters={"short_window": 5})
        job.add_result(CandidateResult(
            c1, {}, {"net_return": 0.05}, {}, "success"
        ))
        
        c2 = CandidateConfiguration(parameters={"short_window": 10})
        job.add_result(CandidateResult(
            c2, {}, {"net_return": 0.10}, {}, "success"
        ))
        
        best = job.best_by_validation("net_return")
        assert best is not None
        assert best.candidate.parameters["short_window"] == 10


class TestCompareWithBaselines:
    """Test baseline comparison."""
    
    def test_cash_baseline_exists(self):
        """Cash (no-trade) baseline always present."""
        job = OptimizationJob()
        baselines = compare_with_baselines(
            job=job,
            data_provider=None,
            asset_ids=["DEMO-ALPHA"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert "cash" in baselines
        assert baselines["cash"].test_metrics["net_return"] == 0.0
        assert baselines["cash"].test_metrics["trade_count"] == 0
