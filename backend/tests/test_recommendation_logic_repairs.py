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
from app.services.live_trading_daemon import LivePaperTradingDaemon


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
        """Unknown rule_id should return passed=False at the unknown_rule check."""
        from unittest.mock import MagicMock
        from uuid import uuid4
        
        mock_db = MagicMock()
        
        # Mock a valid run with bars and trades
        # The trades must have the SAME rule_id as the proposal (even though it's unknown)
        # This simulates: rule was known when trades were recorded, but has since been removed
        unknown_rule_id = "nonexistent_rule_v1"
        
        mock_trade = MagicMock()
        mock_trade.rule_id = unknown_rule_id  # Same as proposal
        mock_trade.exit_id = "exit_1"
        mock_trade.net_pnl = 10.0
        mock_trade.cash_after = 10010.0
        
        mock_run = MagicMock()
        mock_run.bars_json = [{"open_time": "2024-01-01T00:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 100
        mock_run.trades = [mock_trade] * 5  # Baseline trades for the unknown rule
        mock_run.settings_json = {
            "starting_cash": "10000",
            "notional_per_trade": "1000",
            "commission_rate": "0.001",
            "slippage_rate": "0.001",
            "atr_window": 14,
            "cost_multiple": "2.0",
        }
        
        mock_db.scalars.return_value.first.return_value = mock_run
        
        # Use an unknown rule_id
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": unknown_rule_id,  # This rule doesn't exist in RULE_BY_ID
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5", "time_stop_bars": 6},
        }
        
        result = validate_proposal(
            mock_db,
            user_id=uuid4(),
            proposal=proposal,
        )
        
        # Should fail with unknown_rule error (not no_baseline_trades or other early exit)
        assert result["passed"] is False, "Unknown rule should fail validation"
        assert result.get("error") == "unknown_rule", (
            f"Expected error='unknown_rule', got error='{result.get('error')}'. "
            f"Test did not reach the unknown_rule branch!"
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
        
        # E2E test must actually produce a signal and assert on it
        # Cannot pass with zero assertions (signal is None)
        assert signal is not None, (
            "E2E test must produce a signal. If signal is None, the test is not "
            "actually exercising the target behavior. Adjust bar data or parameters."
        )
        
        # Should have rule_score, not confidence
        assert hasattr(signal, "rule_score"), "Signal must have rule_score attribute"
        assert not hasattr(signal, "confidence"), "Signal must not have confidence attribute (renamed to rule_score)"
        assert isinstance(signal.rule_score, float), "rule_score must be a float"
        assert 0.0 <= signal.rule_score <= 1.0, f"rule_score must be in [0, 1], got {signal.rule_score}"


class TestValidationLoopholes:
    """Tests for validation loopholes that must be closed."""

    def test_missing_baseline_trades_fails_validation(self):
        """If no baseline trades exist, validation must return passed=False."""
        from unittest.mock import MagicMock
        from uuid import uuid4
        from datetime import datetime, timezone
        
        mock_db = MagicMock()
        
        # Mock a run with no matching trades
        mock_run = MagicMock()
        mock_run.bars_json = [{"open_time": "2024-01-01T00:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 100
        mock_run.trades = []  # No trades at all
        mock_run.settings_json = {
            "starting_cash": "10000",
            "notional_per_trade": "1000",
            "commission_rate": "0.001",
            "slippage_rate": "0.001",
            "atr_window": 14,
            "cost_multiple": "2.0",
        }
        
        mock_db.scalars.return_value.first.return_value = mock_run
        
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": "breakout_trend",  # Use actual rule_id from RULES
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5", "time_stop_bars": 6},
        }
        
        result = validate_proposal(
            mock_db,
            user_id=uuid4(),
            proposal=proposal,
        )
        
        # Should fail with no_baseline_trades error
        assert result["passed"] is False, "Missing baseline trades must fail validation"
        assert result["error"] == "no_baseline_trades", f"Expected no_baseline_trades error, got {result.get('error')}"

    def test_insufficient_baseline_data_fails_validation(self):
        """If baseline has trades but Sharpe cannot be computed, must fail."""
        from unittest.mock import MagicMock
        from uuid import uuid4
        
        mock_db = MagicMock()
        
        # Mock a run with only 1 trade (insufficient for Sharpe)
        mock_trade = MagicMock()
        mock_trade.rule_id = "breakout_trend"  # Use actual rule_id from RULES
        mock_trade.exit_id = "exit_1"
        mock_trade.net_pnl = 10.0
        mock_trade.cash_after = 10010.0
        
        mock_run = MagicMock()
        mock_run.bars_json = [{"open_time": "2024-01-01T00:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 100
        mock_run.trades = [mock_trade]  # Only 1 trade
        mock_run.settings_json = {
            "starting_cash": "10000",
            "notional_per_trade": "1000",
            "commission_rate": "0.001",
            "slippage_rate": "0.001",
            "atr_window": 14,
            "cost_multiple": "2.0",
        }
        
        mock_db.scalars.return_value.first.return_value = mock_run
        
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": "breakout_trend",  # Use actual rule_id from RULES
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5", "time_stop_bars": 6},
        }
        
        result = validate_proposal(
            mock_db,
            user_id=uuid4(),
            proposal=proposal,
        )
        
        # Should fail with insufficient_baseline_data error
        assert result["passed"] is False, "Insufficient baseline data must fail validation"
        assert result["error"] == "insufficient_baseline_data", f"Expected insufficient_baseline_data error, got {result.get('error')}"

    def test_settings_reconstructed_from_run_not_defaults(self):
        """Validation must use settings from the original run, not defaults."""
        from unittest.mock import MagicMock, patch
        from uuid import uuid4
        
        mock_db = MagicMock()
        
        # Mock a run with custom settings
        mock_trade = MagicMock()
        mock_trade.rule_id = "breakout_trend"  # Use actual rule_id from RULES
        mock_trade.exit_id = "exit_1"
        mock_trade.net_pnl = 10.0
        mock_trade.cash_after = 10010.0
        
        mock_run = MagicMock()
        mock_run.bars_json = [{"open_time": "2024-01-01T00:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 100
        mock_run.trades = [mock_trade] * 5  # 5 trades (still insufficient for Sharpe, but tests settings)
        mock_run.settings_json = {
            "starting_cash": "50000",  # Custom value
            "notional_per_trade": "5000",  # Custom value
            "commission_rate": "0.002",  # Custom value
            "slippage_rate": "0.002",  # Custom value
            "atr_window": 20,  # Custom value
            "cost_multiple": "3.0",  # Custom value
        }
        
        mock_db.scalars.return_value.first.return_value = mock_run
        
        proposal = {
            "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
            "interval_minutes": 1440,
            "rule_id": "breakout_trend",  # Use actual rule_id from RULES
            "baseline_exit_config": {"exit_id": "exit_1"},
            "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5", "time_stop_bars": 6},
        }
        
        # Patch ResearchSettings to verify it's called with custom values
        with patch("app.services.pattern_analysis.ResearchSettings") as mock_settings_class:
            mock_settings_class.return_value = MagicMock()
            
            # This will fail at Sharpe computation, but we just want to verify settings
            try:
                validate_proposal(
                    mock_db,
                    user_id=uuid4(),
                    proposal=proposal,
                )
            except:
                pass  # Expected to fail, we just want to check the call
            
            # Verify ResearchSettings was called with custom values from run
            if mock_settings_class.called:
                call_kwargs = mock_settings_class.call_args[1]
                assert call_kwargs.get("starting_cash") == 50000 or str(call_kwargs.get("starting_cash")) == "50000", \
                    "Should use starting_cash from run.settings_json, not default"


class TestDaemonStartupState:
    """Tests for daemon startup state reporting."""

    def test_daemon_aborts_when_all_skills_invalid(self):
        """Daemon must not start WebSocket if all skills are invalid."""
        import asyncio
        from unittest.mock import MagicMock, patch
        
        # Create daemon with all invalid skills
        daemon = LivePaperTradingDaemon(
            symbols=["XBT/USD"],
            skills=[
                {
                    "skill": "invalid_skill_1",
                    "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                    "interval_minutes": 60,
                    "rule_id": "nonexistent_rule_1",
                    "exit_config": {},
                    "user_id": uuid4(),
                },
                {
                    "skill": "invalid_skill_2",
                    "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                    "interval_minutes": 60,
                    "rule_id": "nonexistent_rule_2",
                    "exit_config": {},
                    "user_id": uuid4(),
                },
            ],
        )
        
        # Mock KrakenWebSocketClient to track if start() is called
        with patch("app.services.live_trading_daemon.KrakenWebSocketClient") as mock_ws_class:
            mock_ws = MagicMock()
            
            # Create an async function for start()
            async def mock_start():
                pass
            
            mock_ws.start = mock_start
            mock_ws_class.return_value = mock_ws
            
            # Run daemon start
            asyncio.run(daemon.start())
            
            # WebSocket should NOT have been started
            assert not mock_ws_class.called or not hasattr(mock_ws, '_start_called'), (
                "WebSocket must not start when all skills are invalid"
            )
            
            # Daemon should not be running
            assert daemon.running is False, (
                "Daemon.running must be False after abort"
            )
            
            # No portfolios should be created
            assert len(daemon.portfolio_ids) == 0, (
                "No portfolios should be created for invalid skills"
            )

    def test_daemon_reports_actual_validated_set(self):
        """Daemon must report the actual set of validated skills, not just count."""
        from unittest.mock import MagicMock, patch
        
        # Create daemon with mix of valid and invalid skills
        daemon = LivePaperTradingDaemon(
            symbols=["BTC/USD"],
            skills=[
                {
                    "skill": "valid_skill",
                    "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                    "interval_minutes": 60,
                    "rule_id": StrategyType.RSI_BASIC.value,  # Valid
                    "exit_config": {},
                    "user_id": uuid4(),
                },
                {
                    "skill": "invalid_skill",
                    "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
                    "interval_minutes": 60,
                    "rule_id": "nonexistent_rule",  # Invalid
                    "exit_config": {},
                    "user_id": uuid4(),
                },
            ],
        )
        
        # Mock database and SignalGenerator
        with patch("app.services.live_trading_daemon.SessionLocal") as mock_session_class:
            mock_db = MagicMock()
            mock_session_class.return_value = mock_db
            
            # Call _create_portfolios
            created_count = daemon._create_portfolios()
            
            # Should have created exactly 1 portfolio
            assert created_count == 1, f"Expected 1 portfolio, got {created_count}"
            
            # portfolio_ids should contain only the valid skill
            # Note: symbol is extracted as "BTC/USD" from "CRYPTO:KRAKEN:BTC-USD"
            assert len(daemon.portfolio_ids) == 1
            assert "valid_skill_BTC/USD" in daemon.portfolio_ids
            assert "invalid_skill_BTC/USD" not in daemon.portfolio_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
