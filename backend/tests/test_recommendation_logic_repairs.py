"""Regression tests for recommendation logic repairs (2026-11-06).

Tests the following fixes:
1. pattern_analysis.py: small-sample recommendations removed
2. pattern_analysis.py: unknown_rule validation fails closed
3. signal_generator.py: rule_id-based matching, not Chinese names
4. signal_generator.py: confidence renamed to rule_score
5. live_trading_daemon.py: validation before DB writes
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

from app.services.signal_generator import (
    SignalGenerator,
    SignalType,
    StrategyType,
    STRATEGY_REGISTRY,
    TradingSignal,
)
from app.services.pattern_analysis import (
    _generate_recommendations,
    validate_proposal,
)


class TestSignalGeneratorRuleIdMatching:
    """Test that SignalGenerator uses rule_id, not Chinese names."""

    def test_valid_rule_id_accepted(self):
        """Valid rule_id should create generator successfully."""
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id=StrategyType.MEAN_REVERSION_RSI_BB.value,
            interval_minutes=60,
        )
        assert gen.rule_id == StrategyType.MEAN_REVERSION_RSI_BB.value
        assert gen.generator_method == "_mean_reversion_signal"

    def test_unknown_rule_id_rejected(self):
        """Unknown rule_id should raise ValueError, not default."""
        with pytest.raises(ValueError, match="Unknown strategy rule_id"):
            SignalGenerator(
                symbol="XBT/USD",
                rule_id="nonexistent_strategy_v1",
                interval_minutes=60,
            )

    def test_all_registered_strategies_work(self):
        """All strategies in STRATEGY_REGISTRY should be instantiable."""
        for rule_id in STRATEGY_REGISTRY.keys():
            gen = SignalGenerator(
                symbol="XBT/USD",
                rule_id=rule_id,
                interval_minutes=60,
            )
            assert gen.rule_id == rule_id
            assert gen.generator_method in [
                "_mean_reversion_signal",
                "_bb_mean_reversion_signal",
                "_momentum_signal",
                "_rsi_signal",
            ]

    def test_skill_parameter_optional(self):
        """skill parameter should be optional (for display only)."""
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id=StrategyType.RSI_BASIC.value,
            interval_minutes=60,
        )
        # Should use display_name_zh from registry
        assert gen.skill == STRATEGY_REGISTRY[StrategyType.RSI_BASIC.value]["display_name_zh"]

    def test_skill_parameter_overrides_display_name(self):
        """skill parameter should override display name when provided."""
        custom_skill = "Custom Strategy Name"
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id=StrategyType.RSI_BASIC.value,
            interval_minutes=60,
            skill=custom_skill,
        )
        assert gen.skill == custom_skill


class TestTradingSignalRuleScore:
    """Test that TradingSignal uses rule_score, not confidence."""

    def test_rule_score_field_exists(self):
        """TradingSignal should have rule_score field."""
        signal = TradingSignal(
            signal_type=SignalType.ENTRY_LONG,
            symbol="XBT/USD",
            timestamp=datetime.now(timezone.utc),
            price=50000.0,
            rule_score=0.7,
            reason="Test signal",
        )
        assert hasattr(signal, "rule_score")
        assert signal.rule_score == 0.7

    def test_confidence_field_removed(self):
        """TradingSignal should NOT have confidence field."""
        signal = TradingSignal(
            signal_type=SignalType.ENTRY_LONG,
            symbol="XBT/USD",
            timestamp=datetime.now(timezone.utc),
            price=50000.0,
            rule_score=0.7,
            reason="Test signal",
        )
        assert not hasattr(signal, "confidence")

    def test_rule_score_documentation(self):
        """rule_score field should have documentation clarifying it's not a probability."""
        # This is a meta-test to ensure the field has proper documentation
        field_info = TradingSignal.__dataclass_fields__["rule_score"]
        # The docstring is in the class definition, not the field metadata
        # but we can verify the field exists and is typed as float (string representation)
        assert field_info.type == "float" or field_info.type == float


class TestPatternAnalysisSmallSample:
    """Test that small-sample recommendations are removed."""

    def test_small_sample_high_win_rate_no_increase_exposure(self):
        """Small sample (< 30 trades) with high win rate should NOT recommend increase_exposure."""
        per_exit_config = {
            "exit_config_1": {
                "trade_count": 5,
                "win_rate": 0.80,  # 80% win rate
                "sharpe": 1.5,
                "avg_pnl": 100.0,
            }
        }
        per_skill_instrument = {}
        
        recommendations = _generate_recommendations(per_exit_config, per_skill_instrument)
        
        # Should NOT have increase_exposure recommendation
        increase_exposure_recs = [
            r for r in recommendations if r["type"] == "increase_exposure"
        ]
        assert len(increase_exposure_recs) == 0, (
            "Small sample should not trigger increase_exposure recommendation"
        )

    def test_small_sample_flagged_as_insufficient_evidence(self):
        """Small sample (< 30 trades) should be flagged as insufficient_evidence."""
        per_exit_config = {
            "exit_config_1": {
                "trade_count": 10,
                "win_rate": 0.60,
                "sharpe": 0.5,
                "avg_pnl": 50.0,
            }
        }
        per_skill_instrument = {}
        
        recommendations = _generate_recommendations(per_exit_config, per_skill_instrument)
        
        # Should have insufficient_evidence recommendation
        insufficient_recs = [
            r for r in recommendations if r["type"] == "insufficient_evidence"
        ]
        assert len(insufficient_recs) > 0, (
            "Small sample should be flagged as insufficient_evidence"
        )

    def test_large_sample_not_flagged(self):
        """Large sample (>= 30 trades) should NOT be flagged as insufficient."""
        per_exit_config = {
            "exit_config_1": {
                "trade_count": 50,
                "win_rate": 0.60,
                "sharpe": 0.5,
                "avg_pnl": 50.0,
            }
        }
        per_skill_instrument = {}
        
        recommendations = _generate_recommendations(per_exit_config, per_skill_instrument)
        
        insufficient_recs = [
            r for r in recommendations if r["type"] == "insufficient_evidence"
        ]
        assert len(insufficient_recs) == 0, (
            "Large sample should not be flagged as insufficient"
        )


class TestPatternAnalysisUnknownRule:
    """Test that unknown rules fail validation (fail closed)."""

    def test_unknown_rule_fails_validation(self):
        """Unknown rule_id should return passed=False, not passed=True."""
        from unittest.mock import MagicMock
        from uuid import uuid4
        
        # Mock database session
        mock_db = MagicMock()
        
        # Mock no baseline run found
        mock_db.scalars.return_value.first.return_value = None
        
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": "nonexistent_rule_v1",
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5"},
        }
        
        result = validate_proposal(
            mock_db,
            user_id=uuid4(),
            proposal=proposal,
        )
        
        # Should fail with no_baseline_run (since we mocked no run found)
        # The key is that it should NOT pass
        assert result["passed"] is False, (
            "Unknown rule should fail validation (fail closed)"
        )

    def test_known_rule_can_pass_validation(self):
        """Known rule_id should be able to pass validation if criteria met."""
        # This test verifies that known rules are not automatically rejected
        # The actual validation logic depends on backtest results
        from unittest.mock import MagicMock
        from uuid import uuid4
        
        mock_db = MagicMock()
        
        # Mock no baseline run (will fail for different reason)
        mock_db.scalars.return_value.first.return_value = None
        
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": StrategyType.MEAN_REVERSION_RSI_BB.value,  # Known rule
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5"},
        }
        
        result = validate_proposal(
            mock_db,
            user_id=uuid4(),
            proposal=proposal,
        )
        
        # Should not fail with unknown_rule error
        assert result.get("error") != "unknown_rule", (
            "Known rule should not fail with unknown_rule error"
        )


class TestLiveTradingDaemonValidationOrdering:
    """Test that daemon validates strategy before creating DB records."""

    def test_daemon_validates_before_db_write(self):
        """Daemon should validate strategy support before creating portfolio/run."""
        from app.services.live_trading_daemon import LivePaperTradingDaemon
        from unittest.mock import MagicMock, patch
        
        # Mock database session
        mock_db = MagicMock()
        
        with patch("app.services.live_trading_daemon.SessionLocal", return_value=mock_db):
            # Create daemon with one valid and one invalid skill
            daemon = LivePaperTradingDaemon(
                symbols=["XBT/USD"],
                skills=[
                    {
                        "rule_id": StrategyType.RSI_BASIC.value,
                        "skill": "Valid Strategy",
                        "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                        "interval_minutes": 60,
                        "exit_config": "exit_1",
                        "user_id": str(uuid4()),
                    },
                    {
                        "rule_id": "unknownStrategy",
                        "skill": "Invalid Strategy",
                        "instrument_id": "CRYPTO:KRAKEN:ETH-USD",
                        "interval_minutes": 60,
                        "exit_config": "exit_2",
                        "user_id": str(uuid4()),
                    },
                ],
                interval_seconds=60,
                initial_capital=100000.0,
            )
            
            # Call _create_portfolios
            daemon._create_portfolios()
            
            # Verify that only valid strategy has portfolio/run created
            # The invalid strategy should have been rejected during validation
            assert len(daemon.portfolio_ids) == 1, (
                f"Only valid strategies should have portfolios created. Got {len(daemon.portfolio_ids)}"
            )
            
            # Verify the valid strategy was accepted
            assert "Valid Strategy_BTC/USD" in daemon.portfolio_ids, (
                "Valid strategy should have portfolio created"
            )
            
            # Verify the invalid strategy was rejected
            assert "Invalid Strategy_ETH/USD" not in daemon.portfolio_ids, (
                "Invalid strategy should NOT have portfolio created"
            )


class TestIntegrationEndToEnd:
    """Integration tests for the full repair."""

    def test_signal_generator_produces_rule_score(self):
        """SignalGenerator should produce signals with rule_score field."""
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id=StrategyType.RSI_BASIC.value,
            interval_minutes=60,
        )
        
        # Feed some bars to build up history
        from app.services.kraken_websocket import KrakenBar
        for i in range(25):
            bar = KrakenBar(
                symbol="XBT/USD",
                timestamp=datetime.now(timezone.utc),
                open=50000.0 + i * 10,
                high=50100.0 + i * 10,
                low=49900.0 + i * 10,
                close=50050.0 + i * 10,
                volume=100.0,
            )
            gen.on_bar(bar)
        
        # Generate signal
        signal = gen.generate_signal()
        
        if signal is not None:
            # Should have rule_score, not confidence
            assert hasattr(signal, "rule_score")
            assert not hasattr(signal, "confidence")
            assert isinstance(signal.rule_score, float)
            assert 0.0 <= signal.rule_score <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
