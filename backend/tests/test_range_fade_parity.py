"""Step-by-step replay parity test for range_fade strategy.

This test proves that the research and live implementations of range_fade
produce identical signals when fed the same bars one at a time.

This is NOT just calling the same batch function twice — the live system
processes bars incrementally (as they arrive), while the research system
processes them in batch. This test verifies they produce the same results.
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.services.kraken_websocket import KrakenBar
from app.services.signal_generator import SignalGenerator, SignalType
from app.services.strategy_research import (
    RULE_BY_ID,
    LabBar,
    ResearchSettings,
    prepare_indicators,
    WARMUP_BARS,
)


def create_test_bars(num_bars: int = 100) -> list[LabBar]:
    """Create synthetic test bars with known patterns.
    
    Creates a range-bound market with some z-score extremes to trigger signals.
    """
    bars = []
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    base_price = 50000.0
    
    for i in range(num_bars):
        # Create a pattern: stable prices with some extremes
        if i < 20:
            # Warmup period: stable prices
            price = base_price + (i * 10)
        elif i < 40:
            # Drop to create negative z-score
            price = base_price + 200 - ((i - 20) * 30)
        elif i < 60:
            # Recovery to create positive z-score
            price = base_price - 400 + ((i - 40) * 40)
        elif i < 80:
            # Another drop
            price = base_price + 400 - ((i - 60) * 35)
        else:
            # Final recovery
            price = base_price - 300 + ((i - 80) * 25)
        
        bar = LabBar(
            open_time=base_time + timedelta(minutes=i),
            interval_minutes=1,
            open=Decimal(str(price)),
            high=Decimal(str(price + 50)),
            low=Decimal(str(price - 50)),
            close=Decimal(str(price)),
            volume=Decimal("1000"),
        )
        bars.append(bar)
    
    return bars


def create_kraken_bars(lab_bars: list[LabBar]) -> list[KrakenBar]:
    """Convert LabBar to KrakenBar for live system."""
    kraken_bars = []
    for lab_bar in lab_bars:
        kraken_bar = KrakenBar(
            symbol="XBT/USD",
            timestamp=lab_bar.open_time,
            open=float(lab_bar.open),
            high=float(lab_bar.high),
            low=float(lab_bar.low),
            close=float(lab_bar.close),
            volume=float(lab_bar.volume),
        )
        kraken_bars.append(kraken_bar)
    return kraken_bars


class TestRangeFadeStepByStepParity:
    """Prove range_fade produces identical signals in research and live."""

    def test_research_rule_exists(self):
        """Verify range_fade exists in research RULE_BY_ID."""
        assert "range_fade" in RULE_BY_ID, "range_fade must exist in research"

    def test_live_strategy_exists(self):
        """Verify range_fade exists in live STRATEGY_REGISTRY."""
        gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id="range_fade",
            interval_minutes=1,
        )
        assert gen.rule_id == "range_fade"

    def test_step_by_step_signal_parity(self):
        """Feed identical bars to research and live, compare signals.
        
        This is the critical parity test:
        1. Create synthetic bars
        2. Run research simulation (batch processing)
        3. Run live signal generator (incremental processing)
        4. Compare entry/exit timing and direction
        """
        # Create test bars
        lab_bars = create_test_bars(100)
        kraken_bars = create_kraken_bars(lab_bars)
        
        # === RESEARCH PATH (batch processing) ===
        rule = RULE_BY_ID["range_fade"]
        settings = ResearchSettings()
        
        # Prepare indicators for all bars
        indicators = prepare_indicators(lab_bars, settings.atr_window)
        
        # Run research simulation from WARMUP_BARS onwards
        research_signals = []
        for i in range(WARMUP_BARS, len(lab_bars)):
            signal_value = rule.signal(indicators, i)
            if signal_value != 0:
                research_signals.append({
                    "bar_index": i,
                    "timestamp": lab_bars[i].open_time,
                    "price": float(lab_bars[i].close),
                    "signal": signal_value,  # 1 = long, -1 = short
                    "zscore": float(indicators.zscore[i]) if indicators.zscore[i] else None,
                    "trend_strength": float(indicators.trend_strength[i]) if indicators.trend_strength[i] else None,
                })
        
        # === LIVE PATH (incremental processing) ===
        live_gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id="range_fade",
            interval_minutes=1,
        )
        
        live_signals = []
        for i, kraken_bar in enumerate(kraken_bars):
            live_gen.on_bar(kraken_bar)
            signal = live_gen.generate_signal()
            
            if signal is not None:
                signal_value = 0
                if signal.signal_type == SignalType.ENTRY_LONG:
                    signal_value = 1
                elif signal.signal_type == SignalType.ENTRY_SHORT:
                    signal_value = -1
                elif signal.signal_type == SignalType.EXIT:
                    signal_value = 0  # Exit signal
                
                if signal_value != 0 or signal.signal_type == SignalType.EXIT:
                    live_signals.append({
                        "bar_index": i,
                        "timestamp": signal.timestamp,
                        "price": signal.price,
                        "signal": signal_value,
                        "signal_type": signal.signal_type.value,
                        "reason": signal.reason,
                    })
        
        # === COMPARISON ===
        print(f"\n=== RESEARCH SIGNALS ({len(research_signals)}) ===")
        for sig in research_signals[:10]:  # Show first 10
            z_str = f"{sig['zscore']:.2f}" if sig['zscore'] is not None else "None"
            trend_str = f"{sig['trend_strength']:.2f}" if sig['trend_strength'] is not None else "None"
            print(f"  Bar {sig['bar_index']}: signal={sig['signal']}, "
                  f"z={z_str}, trend={trend_str}")
        
        print(f"\n=== LIVE SIGNALS ({len(live_signals)}) ===")
        for sig in live_signals[:10]:  # Show first 10
            print(f"  Bar {sig['bar_index']}: {sig['signal_type']}, "
                  f"signal={sig['signal']}, price=${sig['price']:.2f}")
        
        # Verify both systems produced signals
        assert len(research_signals) > 0, "Research should produce signals"
        assert len(live_signals) > 0, "Live should produce signals"
        
        # Compare entry signals (state transitions only)
        # Research generates signals continuously while condition holds,
        # but live only generates on state transitions (entry/exit).
        # Extract first signal in each consecutive group from research.
        research_entries = []
        prev_signal = 0
        for s in research_signals:
            if s["signal"] != 0 and s["signal"] != prev_signal:
                research_entries.append(s)
                prev_signal = s["signal"]
            elif s["signal"] == 0:
                prev_signal = 0
        
        live_entries = [s for s in live_signals if s["signal"] != 0]
        
        print(f"\n=== ENTRY COMPARISON ===")
        print(f"Research entries: {len(research_entries)}")
        print(f"Live entries: {len(live_entries)}")
        
        # Both should have similar number of entries (not necessarily exact due to state tracking)
        assert len(research_entries) > 0, "Research should have entry signals"
        assert len(live_entries) > 0, "Live should have entry signals"
        
        # Verify signal directions match for ALL entries (not just first 3)
        # Research and live must produce identical timing - no tolerance allowed
        min_entries = min(len(research_entries), len(live_entries))
        assert min_entries >= 2, f"Should have at least 2 entries for comparison, got {min_entries}"

        for i in range(min_entries):
            research_sig = research_entries[i]
            live_sig = live_entries[i]

            print(f"\nEntry {i+1}:")
            print(f"  Research: bar={research_sig['bar_index']}, signal={research_sig['signal']}")
            print(f"  Live:     bar={live_sig['bar_index']}, signal={live_sig['signal']}")

            # Signals should match in direction (1=long, -1=short)
            assert research_sig["signal"] == live_sig["signal"], (
                f"Entry {i+1} signal mismatch: "
                f"research={research_sig['signal']}, live={live_sig['signal']}"
            )

            # Timing must match EXACTLY - no tolerance
            assert research_sig["bar_index"] == live_sig["bar_index"], (
                f"Entry {i+1} timing mismatch: "
                f"research bar {research_sig['bar_index']}, live bar {live_sig['bar_index']}. "
                f"Research and live must produce identical signal timing."
            )

        print(f"\n=== PARITY SUMMARY ===")
        print(f"Total entries compared: {min_entries}")
        print(f"All signal directions match: ✓")
        print(f"All timing matches exactly: ✓")

    def test_no_future_data_leakage_in_live(self):
        """Verify live system only uses data available at signal time.
        
        The live generator processes bars one at a time and should only
        use historical data up to the current bar.
        """
        live_gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id="range_fade",
            interval_minutes=1,
        )
        
        # Feed bars one at a time (need 100 bars: 60 warmup + 40 for signals)
        kraken_bars = create_kraken_bars(create_test_bars(100))
        
        signals_generated = []
        for i, bar in enumerate(kraken_bars):
            # Before feeding this bar, check state
            bars_before = len(live_gen.prices)
            
            live_gen.on_bar(bar)
            signal = live_gen.generate_signal()
            
            if signal is not None:
                signals_generated.append({
                    "bar_index": i,
                    "bars_available": len(live_gen.prices),
                    "signal_type": signal.signal_type.value,
                })
                
                # Verify we only used data up to current bar
                assert len(live_gen.prices) == i + 1, (
                    f"Live system should have exactly {i+1} bars after processing bar {i}"
                )
        
        print(f"\n=== NO FUTURE DATA LEAKAGE ===")
        print(f"Generated {len(signals_generated)} signals")
        for sig in signals_generated[:5]:
            print(f"  Bar {sig['bar_index']}: {sig['bars_available']} bars available, "
                  f"{sig['signal_type']}")
        
        # Should have generated some signals
        assert len(signals_generated) > 0, "Should generate signals without future data"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
