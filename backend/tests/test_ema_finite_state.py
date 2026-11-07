"""EMA finite state verification tests.

Proves that the live SignalGenerator uses finite seed buffer (max 50 bars)
and continuous EMA recursion, not unbounded history or re-seeding.

These tests do NOT require database access.
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.services.kraken_websocket import KrakenBar
from app.services.signal_generator import SignalGenerator, TechnicalIndicators
from app.services.strategy_research import LabBar, _ema_series, prepare_indicators


def create_price_sequence(prices: list[float]) -> list[KrakenBar]:
    """Convert price list to KrakenBar sequence."""
    bars = []
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, price in enumerate(prices):
        bar = KrakenBar(
            symbol="BTC-USD",
            timestamp=base_time + timedelta(minutes=i),
            open=price,
            high=price + 10,
            low=price - 10,
            close=price,
            volume=100.0,
        )
        bars.append(bar)
    return bars


class TestEMAFiniteState:
    """Verify EMA uses finite seed buffer, not unbounded history."""

    def test_seed_buffer上限50bars(self):
        """After 1000 bars, seed_prices should be capped at 50."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Feed 1000 bars
        prices = [50000.0 + i for i in range(1000)]
        bars = create_price_sequence(prices)
        for bar in bars:
            gen.on_bar(bar)
        
        assert gen.bars_seen == 1000, f"Expected 1000 bars seen, got {gen.bars_seen}"
        assert len(gen.seed_prices) == 50, f"Expected seed buffer size 50, got {len(gen.seed_prices)}"
        assert gen.ema20_state is not None, "EMA20 should be set after 1000 bars"
        assert gen.ema50_state is not None, "EMA50 should be set after 1000 bars"

    def test_ema_連續遞推唔重新seed(self):
        """EMA should continue recursively, not reset from buffer."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Create 100 bars with linear increase
        prices = [50000.0 + i * 10 for i in range(100)]
        bars = create_price_sequence(prices)
        
        ema20_before_wrap = None
        ema50_before_wrap = None
        
        for i, bar in enumerate(bars):
            gen.on_bar(bar)
            
            # Capture EMA just before buffer wraps at 50
            if i == 49:
                ema20_before_wrap = gen.get_ema20()
                ema50_before_wrap = gen.get_ema50()
            
            # Check EMA just after buffer wraps
            elif i == 50:
                ema20_after_wrap = gen.get_ema20()
                ema50_after_wrap = gen.get_ema50()
                
                # EMA should continue smoothly, not jump
                ema20_diff = abs(ema20_after_wrap - ema20_before_wrap)
                ema50_diff = abs(ema50_after_wrap - ema50_before_wrap)
                
                # With linear increase of 10 per bar, EMA change should be small
                assert ema20_diff < 100, f"EMA20 jumped {ema20_diff:.2f} at buffer wrap"
                assert ema50_diff < 100, f"EMA50 jumped {ema50_diff:.2f} at buffer wrap"

    def test_ema20_parity_with_research(self):
        """Live EMA20 should match research _ema_series exactly."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Use 25 bars to get EMA20 after warmup
        prices = [100.0, 102, 101, 103, 105, 104, 106, 108, 107, 109,
                  111, 110, 112, 114, 113, 115, 117, 116, 118, 120,
                  119, 121, 123, 122, 124]
        
        bars = create_price_sequence(prices)
        for bar in bars:
            gen.on_bar(bar)
        
        # Calculate research EMA20 using actual research-side function
        decimal_prices = [Decimal(str(p)) for p in prices]
        research_ema20_series = _ema_series(decimal_prices, 20)
        research_ema20_final = research_ema20_series[-1]
        live_ema20 = gen.get_ema20()
        
        # Compare with actual precision, not formatted output
        max_error = abs(float(research_ema20_final) - live_ema20)
        
        assert max_error < 1e-10, (
            f"EMA20 mismatch: research={research_ema20_final}, "
            f"live={live_ema20:.15f}, max_error={max_error:.2e}"
        )

    def test_ema50_parity_with_research(self):
        """Live EMA50 should match research _ema_series exactly."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Use 60 bars to get EMA50 after warmup
        prices = [100.0 + i * 0.5 for i in range(60)]
        
        bars = create_price_sequence(prices)
        for bar in bars:
            gen.on_bar(bar)
        
        # Calculate research EMA50 using actual research-side function
        decimal_prices = [Decimal(str(p)) for p in prices]
        research_ema50_series = _ema_series(decimal_prices, 50)
        research_ema50_final = research_ema50_series[-1]
        live_ema50 = gen.get_ema50()
        
        # Compare with actual precision
        max_error = abs(float(research_ema50_final) - live_ema50)
        
        assert max_error < 1e-10, (
            f"EMA50 mismatch: research={research_ema50_final}, "
            f"live={live_ema50:.15f}, max_error={max_error:.2e}"
        )

    def test_ema_indicator_parity_all_bars(self):
        """200-bar test: verify EMA indicator parity at ALL bars after warmup, not just signal bars."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Create 200 bars with realistic pattern
        import random
        random.seed(42)
        base_price = 50000.0
        prices = [base_price + random.uniform(-500, 500) for _ in range(200)]
        
        bars = create_price_sequence(prices)
        
        # Calculate research EMA series ONCE for all bars
        decimal_prices = [Decimal(str(p)) for p in prices]
        research_ema20_series = _ema_series(decimal_prices, 20)
        research_ema50_series = _ema_series(decimal_prices, 50)
        
        # Feed bars to live system, comparing EMA at ALL bars after warmup
        live_signals = []
        ema20_comparisons = []
        ema50_comparisons = []
        max_ema20_error = 0.0
        max_ema50_error = 0.0
        
        for bar_idx, bar in enumerate(bars):
            gen.on_bar(bar)
            signal = gen.generate_signal()
            
            # Record signals independently
            if signal is not None:
                live_signals.append({
                    "bar_index": bar_idx,
                    "type": signal.signal_type.value,
                    "price": signal.price,
                })
            
            # Compare EMA at ALL bars after warmup (bar_idx >= 50)
            # This is OUTSIDE the signal check
            if bar_idx >= 50:
                live_ema20 = gen.get_ema20()
                live_ema50 = gen.get_ema50()
                research_ema20 = research_ema20_series[bar_idx]
                research_ema50 = research_ema50_series[bar_idx]
                
                # Assert both are not None
                assert live_ema20 is not None, f"Live EMA20 is None at bar {bar_idx}"
                assert live_ema50 is not None, f"Live EMA50 is None at bar {bar_idx}"
                assert research_ema20 is not None, f"Research EMA20 is None at bar {bar_idx}"
                assert research_ema50 is not None, f"Research EMA50 is None at bar {bar_idx}"
                
                # Compare and track errors
                error20 = abs(float(research_ema20) - live_ema20)
                error50 = abs(float(research_ema50) - live_ema50)
                
                ema20_comparisons.append((bar_idx, error20))
                ema50_comparisons.append((bar_idx, error50))
                
                max_ema20_error = max(max_ema20_error, error20)
                max_ema50_error = max(max_ema50_error, error50)
        
        # ASSERT exact comparison count
        # 200 bars, warmup at bar 50, so bars 50-199 = 150 bars
        expected_comparison_count = 150
        assert len(ema20_comparisons) == expected_comparison_count, (
            f"Expected {expected_comparison_count} EMA20 comparisons, got {len(ema20_comparisons)}"
        )
        assert len(ema50_comparisons) == expected_comparison_count, (
            f"Expected {expected_comparison_count} EMA50 comparisons, got {len(ema50_comparisons)}"
        )
        
        # ASSERT comparison indices match expected range
        expected_indices = list(range(50, 200))
        actual_ema20_indices = [idx for idx, _ in ema20_comparisons]
        actual_ema50_indices = [idx for idx, _ in ema50_comparisons]
        assert actual_ema20_indices == expected_indices, "EMA20 comparison indices mismatch"
        assert actual_ema50_indices == expected_indices, "EMA50 comparison indices mismatch"
        
        # ASSERT actual precision
        assert max_ema20_error < 1e-8, f"EMA20 max error {max_ema20_error:.2e} exceeds 1e-8"
        assert max_ema50_error < 1e-8, f"EMA50 max error {max_ema50_error:.2e} exceeds 1e-8"
        
        # Report actual statistics
        print(f"\n200-bar ALL BARS test statistics:")
        print(f"  Total bars: 200")
        print(f"  Warmup threshold: bar_idx >= 50")
        print(f"  Comparison range: bars 50-199 (150 bars)")
        print(f"  Total signals: {len(live_signals)}")
        print(f"  EMA20 comparisons: {len(ema20_comparisons)}")
        print(f"  EMA50 comparisons: {len(ema50_comparisons)}")
        print(f"  EMA20 max error: {max_ema20_error:.2e}")
        print(f"  EMA50 max error: {max_ema50_error:.2e}")
        print(f"  Seed buffer size: {len(gen.seed_prices)}")
        print(f"  Bars seen: {gen.bars_seen}")

    def test_重複generate_signal唔重設EMA(self):
        """Calling generate_signal() multiple times should not reset EMA state."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Feed 100 bars
        prices = [50000.0 + i for i in range(100)]
        bars = create_price_sequence(prices)
        for bar in bars:
            gen.on_bar(bar)
        
        ema20_before = gen.get_ema20()
        ema50_before = gen.get_ema50()
        
        # Call generate_signal multiple times
        for _ in range(10):
            gen.generate_signal()
        
        ema20_after = gen.get_ema20()
        ema50_after = gen.get_ema50()
        
        # EMA should not change
        assert ema20_before == ema20_after, (
            f"EMA20 changed after generate_signal: {ema20_before} -> {ema20_after}"
        )
        assert ema50_before == ema50_after, (
            f"EMA50 changed after generate_signal: {ema50_before} -> {ema50_after}"
        )

    def test_方向翻轉測試(self):
        """Test EMA behavior when price direction flips."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Create price sequence with direction flip
        prices = [50000.0 + i * 10 for i in range(50)]  # Upward
        prices += [50000.0 + 500 - (i * 10) for i in range(50)]  # Downward
        
        bars = create_price_sequence(prices)
        for bar in bars:
            gen.on_bar(bar)
        
        # EMA should track the direction change
        ema20_final = gen.get_ema20()
        ema50_final = gen.get_ema50()
        
        # After upward then downward, EMA20 should be below the peak
        assert ema20_final < 50500, f"EMA20 {ema20_final:.2f} should be below peak after reversal"
        
        # Verify memory is still bounded
        assert len(gen.seed_prices) == 50, f"Seed buffer should be 50, got {len(gen.seed_prices)}"
        assert gen.bars_seen == 100, f"Bars seen should be 100, got {gen.bars_seen}"
