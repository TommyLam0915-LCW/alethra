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


def create_test_bars(num_bars: int = 200) -> list[LabBar]:
    """Create synthetic test bars with known patterns.
    
    Creates a range-bound market with multiple z-score extremes to trigger
    multiple entry/exit cycles, including re-entry in same direction.
    """
    bars = []
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    base_price = 50000.0
    
    for i in range(num_bars):
        # Create a pattern with multiple cycles:
        # - Stable warmup (0-19)
        # - Drop to trigger short entry (20-39)
        # - Recovery to trigger short exit + long entry (40-59)
        # - Drop to trigger long exit + short re-entry (60-79)
        # - Recovery to trigger short exit + long re-entry (80-99)
        # - Continue cycles to cover >100 bars
        
        cycle = (i - 20) // 20 if i >= 20 else -1
        
        if i < 20:
            # Warmup period: stable prices
            price = base_price + (i * 10)
        elif cycle % 2 == 0:
            # Even cycles: drop (trigger short or exit long)
            price = base_price + 200 - ((i - 20 - cycle * 20) * 30)
        else:
            # Odd cycles: recovery (trigger long or exit short)
            price = base_price - 400 + ((i - 20 - cycle * 20) * 40)
        
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
        1. Create synthetic bars (>100 to cover re-entry scenarios)
        2. Run research simulation (batch processing)
        3. Run live signal generator (incremental processing)
        4. Compare ALL events (entry + exit), not just entries
        5. Verify exact timing, direction, and event count
        """
        # Create test bars - use 200 bars to cover multiple entry/exit cycles
        lab_bars = create_test_bars(200)
        kraken_bars = create_kraken_bars(lab_bars)
        
        # === RESEARCH PATH (batch processing) ===
        rule = RULE_BY_ID["range_fade"]
        settings = ResearchSettings()
        
        # Prepare indicators for all bars
        indicators = prepare_indicators(lab_bars, settings.atr_window)
        
        # Run research simulation from WARMUP_BARS onwards
        # Capture ALL signals (including zeros for exit detection)
        research_events = []
        prev_signal = 0
        for i in range(WARMUP_BARS, len(lab_bars)):
            signal_value = rule.signal(indicators, i)
            
            # Detect state transitions: entry (0→non-zero), exit (non-zero→0), or direction change
            is_entry = (signal_value != 0 and prev_signal == 0)
            is_exit = (signal_value == 0 and prev_signal != 0)
            is_direction_change = (signal_value != 0 and prev_signal != 0 and signal_value != prev_signal)
            
            if is_entry or is_exit or is_direction_change:
                research_events.append({
                    "bar_index": i,
                    "timestamp": lab_bars[i].open_time,
                    "price": float(lab_bars[i].close),
                    "signal": signal_value,  # 1 = long, -1 = short, 0 = exit
                    "event_type": "entry" if is_entry else ("exit" if is_exit else "direction_change"),
                    "zscore": float(indicators.zscore[i]) if indicators.zscore[i] else None,
                    "trend_strength": float(indicators.trend_strength[i]) if indicators.trend_strength[i] else None,
                })
            
            prev_signal = signal_value
        
        # === LIVE PATH (incremental processing) ===
        live_gen = SignalGenerator(
            symbol="XBT/USD",
            rule_id="range_fade",
            interval_minutes=1,
        )
        
        live_events = []
        for i, kraken_bar in enumerate(kraken_bars):
            live_gen.on_bar(kraken_bar)
            signal = live_gen.generate_signal()
            
            if signal is not None:
                signal_value = 0
                event_type = "unknown"
                if signal.signal_type == SignalType.ENTRY_LONG:
                    signal_value = 1
                    event_type = "entry"
                elif signal.signal_type == SignalType.ENTRY_SHORT:
                    signal_value = -1
                    event_type = "entry"
                elif signal.signal_type == SignalType.EXIT:
                    signal_value = 0
                    event_type = "exit"
                
                live_events.append({
                    "bar_index": i,
                    "timestamp": signal.timestamp,
                    "price": signal.price,
                    "signal": signal_value,
                    "event_type": event_type,
                    "signal_type": signal.signal_type.value,
                    "reason": signal.reason,
                })
        
        # === COMPARISON ===
        print(f"\n=== RESEARCH EVENTS ({len(research_events)}) ===")
        for evt in research_events[:10]:  # Show first 10
            z_str = f"{evt['zscore']:.2f}" if evt['zscore'] is not None else "None"
            trend_str = f"{evt['trend_strength']:.2f}" if evt['trend_strength'] is not None else "None"
            print(f"  Bar {evt['bar_index']}: {evt['event_type']}, signal={evt['signal']}, "
                  f"z={z_str}, trend={trend_str}")
        
        print(f"\n=== LIVE EVENTS ({len(live_events)}) ===")
        for evt in live_events[:10]:  # Show first 10
            print(f"  Bar {evt['bar_index']}: {evt['event_type']}, "
                  f"signal={evt['signal']}, price=${evt['price']:.2f}")
        
        # Verify both systems produced events
        assert len(research_events) > 0, "Research should produce events"
        assert len(live_events) > 0, "Live should produce events"
        
        # CRITICAL: Event counts must match exactly (no truncation)
        assert len(research_events) == len(live_events), (
            f"Event count mismatch: research={len(research_events)}, live={len(live_events)}. "
            f"Both systems must produce identical event sequences."
        )
        
        # Compare ALL events (entry + exit + direction changes)
        print(f"\n=== FULL EVENT COMPARISON ===")
        print(f"Total events: {len(research_events)}")
        
        for i, (research_evt, live_evt) in enumerate(zip(research_events, live_events)):
            print(f"\nEvent {i+1}:")
            print(f"  Research: bar={research_evt['bar_index']}, type={research_evt['event_type']}, signal={research_evt['signal']}")
            print(f"  Live:     bar={live_evt['bar_index']}, type={live_evt['event_type']}, signal={live_evt['signal']}")
            
            # Event types must match
            assert research_evt["event_type"] == live_evt["event_type"], (
                f"Event {i+1} type mismatch: "
                f"research={research_evt['event_type']}, live={live_evt['event_type']}"
            )
            
            # Signals must match in direction (1=long, -1=short, 0=exit)
            assert research_evt["signal"] == live_evt["signal"], (
                f"Event {i+1} signal mismatch: "
                f"research={research_evt['signal']}, live={live_evt['signal']}"
            )
            
            # Timing must match EXACTLY - no tolerance
            assert research_evt["bar_index"] == live_evt["bar_index"], (
                f"Event {i+1} timing mismatch: "
                f"research bar {research_evt['bar_index']}, live bar {live_evt['bar_index']}. "
                f"Research and live must produce identical event timing."
            )
        
        print(f"\n=== PARITY SUMMARY ===")
        print(f"Total events compared: {len(research_events)}")
        print(f"All event types match: ✓")
        print(f"All signal directions match: ✓")
        print(f"All timing matches exactly: ✓")
        print(f"Event counts identical: ✓")

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
