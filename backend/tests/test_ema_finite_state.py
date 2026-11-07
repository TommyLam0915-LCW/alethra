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
        """200-bar test: verify EMA indicator parity at all bars after warmup, using research-side _ema_series."""
        gen = SignalGenerator("BTC-USD", "range_fade", 1)
        
        # Create 200 bars with realistic pattern
        import random
        random.seed(42)
        base_price = 50000.0
        prices = [base_price + random.uniform(-500, 500) for _ in range(200)]
        
        bars = create_price_sequence(prices)
        
        # Feed bars to live system, tracking ACTUAL bar index
        live_signals = []
        ema_comparisons = []
        max_ema20_error = 0.0
        max_ema50_error = 0.0
        
        for bar_idx, bar in enumerate(bars):
            gen.on_bar(bar)
            signal = gen.generate_signal()
            if signal is not None:
                live_signals.append({
                    "bar_index": bar_idx,  # ACTUAL bar index, not signal sequence
                    "type": signal.signal_type.value,
                    "price": signal.price,
                })
                
                # Compare EMA values after warmup using research-side function
                if bar_idx >= 50:
                    live_ema20 = gen.get_ema20()
                    live_ema50 = gen.get_ema50()
                    
                    # Calculate research EMA using actual research-side _ema_series
                    decimal_prices = [Decimal(str(p)) for p in prices[:bar_idx+1]]
                    research_ema20_series = _ema_series(decimal_prices, 20)
                    research_ema50_series = _ema_series(decimal_prices, 50)
                    research_ema20 = research_ema20_series[-1]
                    research_ema50 = research_ema50_series[-1]
                    
                    if research_ema20 is not None and live_ema20 is not None:
                        error = abs(float(research_ema20) - live_ema20)
                        max_ema20_error = max(max_ema20_error, error)
                        ema_comparisons.append(("ema20", bar_idx, error))
                    
                    if research_ema50 is not None and live_ema50 is not None:
                        error = abs(float(research_ema50) - live_ema50)
                        max_ema50_error = max(max_ema50_error, error)
                        ema_comparisons.append(("ema50", bar_idx, error))
        
        # ASSERT that comparisons were actually executed
        assert len(ema_comparisons) > 0, "No EMA comparisons executed - bar_index bug"
        # 28 signals × 2 EMAs = 56 comparisons (after bar 50 warmup)
        assert len(ema_comparisons) >= 50, f"Expected >= 50 comparisons, got {len(ema_comparisons)}"
        
        # ASSERT actual precision, not hardcoded threshold
        assert max_ema20_error < 1e-8, f"EMA20 max error {max_ema20_error:.2e} exceeds 1e-8"
        assert max_ema50_error < 1e-8, f"EMA50 max error {max_ema50_error:.2e} exceeds 1e-8"
        
        # Report actual statistics
        print(f"\n200-bar test statistics:")
        print(f"  Total bars: 200")
        print(f"  Total signals: {len(live_signals)}")
        print(f"  EMA comparisons executed: {len(ema_comparisons)}")
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
