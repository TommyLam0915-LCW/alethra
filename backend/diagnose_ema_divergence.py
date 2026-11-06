"""Diagnostic: EMA divergence between research and live systems.

This script demonstrates how the deque(maxlen=100) in SignalGenerator
causes EMA calculations to diverge from the research system's continuous
EMA series.
"""

from collections import deque
from decimal import Decimal
from typing import List, Optional, Sequence


def research_ema_series(values: Sequence[Decimal], window: int) -> List[Optional[Decimal]]:
    """Research system: continuous EMA from fixed starting point."""
    out: List[Optional[Decimal]] = [None] * len(values)
    if len(values) < window:
        return out
    weight = Decimal(2) / Decimal(window + 1)
    previous: Optional[Decimal] = None
    for index, value in enumerate(values):
        if previous is None:
            if index + 1 >= window:
                previous = sum(values[index + 1 - window : index + 1], Decimal(0)) / Decimal(window)
                out[index] = previous
            continue
        previous = value * weight + previous * (Decimal(1) - weight)
        out[index] = previous
    return out


def live_ema(prices: list[float], period: int) -> Optional[float]:
    """Live system: EMA calculated from current buffer (re-seeds each call)."""
    if len(prices) < period:
        return None
    
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    
    return ema


def diagnose_ema_divergence():
    """Compare EMA calculations bar-by-bar with realistic price patterns."""
    # Generate 200 bars with the same pattern as the parity test
    # This includes drops and recoveries that trigger z-score extremes
    prices = []
    base_price = 50000.0
    
    for i in range(200):
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
        
        prices.append(Decimal(str(price)))
    
    window = 20
    
    print("=" * 80)
    print("EMA DIVERGENCE DIAGNOSTIC")
    print("=" * 80)
    print(f"Total bars: {len(prices)}")
    print(f"EMA window: {window}")
    print(f"Deque maxlen: 100")
    print()
    
    # Research system: calculate EMA for entire series
    research_ema = research_ema_series(prices, window)
    
    # Live system: simulate deque(maxlen=100)
    live_buffer = deque(maxlen=100)
    
    print(f"{'Bar':<6} {'Price':<12} {'Research EMA':<15} {'Live EMA':<15} {'Difference':<12} {'Match?':<8}")
    print("-" * 80)
    
    divergence_start = None
    for i, price in enumerate(prices):
        live_buffer.append(float(price))
        
        # Calculate research EMA
        r_ema = research_ema[i]
        
        # Calculate live EMA
        l_ema = live_ema(list(live_buffer), window)
        
        # Compare
        if r_ema is not None and l_ema is not None:
            diff = abs(float(r_ema) - l_ema)
            match = "✓" if diff < 0.01 else "✗"
            
            if divergence_start is None and diff >= 0.01:
                divergence_start = i
            
            print(f"{i:<6} {float(price):<12.2f} {float(r_ema):<15.2f} {l_ema:<15.2f} {diff:<12.2f} {match:<8}")
    
    print()
    print("=" * 80)
    if divergence_start:
        print(f"DIVERGENCE STARTS AT BAR {divergence_start}")
        print(f"Reason: deque(maxlen=100) dropped bar 0 when bar 100 was added")
        print(f"Live EMA re-seeded from bars [81:101] instead of [0:20]")
    else:
        print("NO DIVERGENCE DETECTED")
    print("=" * 80)


if __name__ == "__main__":
    diagnose_ema_divergence()
