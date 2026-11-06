"""Real-time signal generator for live paper trading.

Generates trading signals based on technical indicators calculated
from real-time market bars.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np

from app.services.kraken_websocket import KrakenBar

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal types."""
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    EXIT = "exit"
    HOLD = "hold"


class StrategyType(Enum):
    """Registered strategy types with stable IDs."""
    MEAN_REVERSION_RSI_BB = "mean_reversion_rsi_bb_v1"
    BB_MEAN_REVERSION = "bb_mean_reversion_v1"
    MOMENTUM_RSI = "momentum_rsi_v1"
    RSI_BASIC = "rsi_basic_v1"
    # Research-aligned strategy (same rule_id as strategy_research.py)
    RANGE_FADE = "range_fade"


STRATEGY_REGISTRY = {
    StrategyType.MEAN_REVERSION_RSI_BB.value: {
        "display_name_zh": "RSI+布林帶均值回歸",
        "generator_method": "_mean_reversion_signal",
    },
    StrategyType.BB_MEAN_REVERSION.value: {
        "display_name_zh": "布林帶均值回歸",
        "generator_method": "_bb_mean_reversion_signal",
    },
    StrategyType.MOMENTUM_RSI.value: {
        "display_name_zh": "RSI動量策略",
        "generator_method": "_momentum_signal",
    },
    StrategyType.RSI_BASIC.value: {
        "display_name_zh": "基礎RSI策略",
        "generator_method": "_rsi_signal",
    },
    # Research-aligned strategy
    StrategyType.RANGE_FADE.value: {
        "display_name_zh": "區間均值回歸",
        "generator_method": "_range_fade_signal",
        "research_rule_id": "range_fade",  # Explicit mapping to research rule
    },
}

# Live-supported subset: strategies that have been validated for live trading
# This is a subset of all research rules (RULE_BY_ID in strategy_research.py)
LIVE_SUPPORTED_STRATEGIES = {
    StrategyType.RANGE_FADE.value,  # First research-aligned strategy
}


@dataclass
class TradingSignal:
    """Generated trading signal."""
    signal_type: SignalType
    symbol: str
    timestamp: datetime
    price: float
    rule_score: float
    """Uncalibrated score from rule-based logic (NOT a probability).

    This is a heuristic confidence indicator from the strategy rules,
    not a statistically calibrated probability. Do not interpret as
    P(profitable) or P(success).
    """
    reason: str
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class TechnicalIndicators:
    """Calculate technical indicators from price bars."""

    @staticmethod
    def sma(prices: list[float], period: int) -> Optional[float]:
        """Simple Moving Average."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def ema(prices: list[float], period: int) -> Optional[float]:
        """Exponential Moving Average."""
        if len(prices) < period:
            return None
        
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema

    @staticmethod
    def rsi(prices: list[float], period: int = 14) -> Optional[float]:
        """Relative Strength Index."""
        if len(prices) < period + 1:
            return None
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)

    @staticmethod
    def atr(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        period: int = 14,
    ) -> Optional[float]:
        """Average True Range."""
        if len(closes) < period + 1:
            return None
        
        true_ranges = []
        for i in range(1, len(closes)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i - 1])
            low_close = abs(lows[i] - closes[i - 1])
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            return None
        
        return sum(true_ranges[-period:]) / period

    @staticmethod
    def bollinger_bands(
        prices: list[float],
        period: int = 20,
        std_dev: float = 2.0,
    ) -> Optional[tuple[float, float, float]]:
        """Bollinger Bands (upper, middle, lower)."""
        if len(prices) < period:
            return None
        
        recent_prices = prices[-period:]
        middle = sum(recent_prices) / period
        variance = sum((p - middle) ** 2 for p in recent_prices) / period
        std = variance ** 0.5
        
        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)
        
        return (upper, middle, lower)

    @staticmethod
    def zscore(prices: list[float], period: int = 20) -> Optional[float]:
        """Calculate z-score for the most recent price."""
        if len(prices) < period:
            return None
        
        recent_prices = prices[-period:]
        mean = sum(recent_prices) / period
        variance = sum((p - mean) ** 2 for p in recent_prices) / period
        std = variance ** 0.5
        
        if std == 0:
            return 0.0
        
        current_price = prices[-1]
        return (current_price - mean) / std

    @staticmethod
    def trend_strength(
        prices: list[float],
        highs: list[float],
        lows: list[float],
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        atr_period: int = 14,
    ) -> Optional[float]:
        """Calculate trend strength: abs(EMA_fast - EMA_slow) / ATR."""
        ema_fast = TechnicalIndicators.ema(prices, ema_fast_period)
        ema_slow = TechnicalIndicators.ema(prices, ema_slow_period)
        atr = TechnicalIndicators.atr(highs, lows, prices, atr_period)
        
        if ema_fast is None or ema_slow is None or atr is None or atr == 0:
            return None
        
        return abs(ema_fast - ema_slow) / atr


class SignalGenerator:
    """Generate trading signals from market bars."""

    def __init__(
        self,
        symbol: str,
        rule_id: str,
        interval_minutes: int,
        skill: Optional[str] = None,
        max_bars: int = 100,
    ):
        """
        Args:
            symbol: Trading pair symbol
            rule_id: Stable strategy identifier (must be in STRATEGY_REGISTRY)
            interval_minutes: Bar interval in minutes
            skill: Optional display name (for logging only)
            max_bars: Maximum bars to keep in memory

        Raises:
            ValueError: If rule_id is not in STRATEGY_REGISTRY
        """
        if rule_id not in STRATEGY_REGISTRY:
            raise ValueError(
                f"Unknown strategy rule_id: {rule_id}. "
                f"Valid IDs: {list(STRATEGY_REGISTRY.keys())}. "
                f"Unknown strategies are rejected, not defaulted."
            )
        
        self.symbol = symbol
        self.rule_id = rule_id
        self.skill = skill or STRATEGY_REGISTRY[rule_id]["display_name_zh"]
        self.interval_minutes = interval_minutes
        self.max_bars = max_bars
        self.generator_method = STRATEGY_REGISTRY[rule_id]["generator_method"]
        
        # Strategy-specific warmup periods (must match research WARMUP_BARS for parity)
        # range_fade requires 60 bars to match research system
        self.warmup_bars = 60 if rule_id == "range_fade" else 20

        # Bar history
        self.bars: deque[KrakenBar] = deque(maxlen=max_bars)

        # Price history for indicators
        self.prices: deque[float] = deque(maxlen=max_bars)
        self.highs: deque[float] = deque(maxlen=max_bars)
        self.lows: deque[float] = deque(maxlen=max_bars)

        # Position tracking
        self.in_position = False
        self.position_side: Optional[str] = None
        self.entry_price: Optional[float] = None

    def on_bar(self, bar: KrakenBar):
        """Process new bar and update history."""
        self.bars.append(bar)
        self.prices.append(bar.close)
        self.highs.append(bar.high)
        self.lows.append(bar.low)

    def generate_signal(self) -> Optional[TradingSignal]:
        """Generate trading signal based on current market state."""
        if len(self.prices) < self.warmup_bars:
            if len(self.prices) % 5 == 0:
                logger.info(f"📈 {self.skill} on {self.symbol}: {len(self.prices)}/{self.warmup_bars} bars collected")
            return None

        current_price = self.prices[-1]
        timestamp = self.bars[-1].timestamp
        
        logger.info(f"🔍 {self.skill} ({self.rule_id}) on {self.symbol}: Generating signal with {len(self.prices)} bars, price=${current_price:.2f}")

        # Calculate indicators
        rsi = TechnicalIndicators.rsi(list(self.prices), period=14)
        bb = TechnicalIndicators.bollinger_bands(list(self.prices), period=20)
        atr = TechnicalIndicators.atr(
            list(self.highs), list(self.lows), list(self.prices), period=14
        )
        
        # For range_fade strategy
        zscore_val = TechnicalIndicators.zscore(list(self.prices), period=20)
        trend_strength_val = TechnicalIndicators.trend_strength(
            list(self.prices), list(self.highs), list(self.lows)
        )

        if rsi is None or bb is None or atr is None:
            return None

        bb_upper, bb_middle, bb_lower = bb

        # Dispatch to the registered generator method
        method = getattr(self, self.generator_method)
        if self.generator_method == "_mean_reversion_signal":
            return method(current_price, rsi, bb_lower, bb_upper, atr, timestamp)
        elif self.generator_method == "_bb_mean_reversion_signal":
            return method(current_price, bb_lower, bb_upper, bb_middle, atr, timestamp)
        elif self.generator_method == "_momentum_signal":
            return method(current_price, rsi, atr, timestamp)
        elif self.generator_method == "_rsi_signal":
            return method(current_price, rsi, atr, timestamp)
        elif self.generator_method == "_range_fade_signal":
            if zscore_val is None or trend_strength_val is None:
                return None
            return method(current_price, zscore_val, trend_strength_val, atr, timestamp)
        else:
            logger.error(f"Unknown generator method: {self.generator_method}")
            return None

    def _mean_reversion_signal(
        self,
        price: float,
        rsi: float,
        bb_lower: float,
        bb_upper: float,
        atr: float,
        timestamp: datetime,
    ) -> Optional[TradingSignal]:
        """Generate mean reversion signal."""
        # Entry long when oversold
        if not self.in_position and rsi < 30 and price <= bb_lower:
            self.in_position = True
            self.position_side = "long"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.7,
                reason=f"RSI oversold ({rsi:.1f}) at lower BB",
                stop_loss=price - (2 * atr),
                take_profit=price + (3 * atr),
            )

        # Entry short when overbought
        elif not self.in_position and rsi > 70 and price >= bb_upper:
            self.in_position = True
            self.position_side = "short"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.7,
                reason=f"RSI overbought ({rsi:.1f}) at upper BB",
                stop_loss=price + (2 * atr),
                take_profit=price - (3 * atr),
            )

        # Exit when mean reversion complete
        elif self.in_position:
            if self.position_side == "long" and (rsi > 50 or price >= bb_upper):
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.8,
                    reason="Mean reversion complete (long)",
                )
            elif self.position_side == "short" and (rsi < 50 or price <= bb_lower):
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.8,
                    reason="Mean reversion complete (short)",
                )

        return None

    def _bb_mean_reversion_signal(
        self,
        price: float,
        bb_lower: float,
        bb_upper: float,
        bb_middle: float,
        atr: float,
        timestamp: datetime,
    ) -> Optional[TradingSignal]:
        """Generate Bollinger Band mean reversion signal."""
        # Entry long at lower band
        if not self.in_position and price <= bb_lower:
            self.in_position = True
            self.position_side = "long"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.65,
                reason="Price at lower Bollinger Band",
                stop_loss=price - (2 * atr),
                take_profit=bb_middle,
            )

        # Entry short at upper band
        elif not self.in_position and price >= bb_upper:
            self.in_position = True
            self.position_side = "short"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.65,
                reason="Price at upper Bollinger Band",
                stop_loss=price + (2 * atr),
                take_profit=bb_middle,
            )

        # Exit at middle band
        elif self.in_position:
            if self.position_side == "long" and price >= bb_middle:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.75,
                    reason="Reached middle Bollinger Band (long)",
                )
            elif self.position_side == "short" and price <= bb_middle:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.75,
                    reason="Reached middle Bollinger Band (short)",
                )

        return None

    def _momentum_signal(
        self,
        price: float,
        rsi: float,
        atr: float,
        timestamp: datetime,
    ) -> Optional[TradingSignal]:
        """Generate momentum/trend following signal."""
        # Entry long on strong momentum
        if not self.in_position and 50 < rsi < 70:
            self.in_position = True
            self.position_side = "long"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.6,
                reason=f"Momentum entry (RSI {rsi:.1f})",
                stop_loss=price - (2 * atr),
                take_profit=price + (4 * atr),
            )

        # Entry short on weak momentum
        elif not self.in_position and 30 < rsi < 50:
            self.in_position = True
            self.position_side = "short"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.6,
                reason=f"Weak momentum entry (RSI {rsi:.1f})",
                stop_loss=price + (2 * atr),
                take_profit=price - (4 * atr),
            )

        # Exit on reversal
        elif self.in_position:
            if self.position_side == "long" and rsi > 75:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.7,
                    reason="Momentum reversal (long)",
                )
            elif self.position_side == "short" and rsi < 25:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.7,
                    reason="Momentum reversal (short)",
                )

        return None

    def _rsi_signal(
        self,
        price: float,
        rsi: float,
        atr: float,
        timestamp: datetime,
    ) -> Optional[TradingSignal]:
        """Generate basic RSI signal."""
        # Entry long when oversold
        if not self.in_position and rsi < 30:
            self.in_position = True
            self.position_side = "long"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.6,
                reason=f"RSI oversold ({rsi:.1f})",
                stop_loss=price - (2 * atr),
                take_profit=price + (3 * atr),
            )

        # Entry short when overbought
        elif not self.in_position and rsi > 70:
            self.in_position = True
            self.position_side = "short"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.6,
                reason=f"RSI overbought ({rsi:.1f})",
                stop_loss=price + (2 * atr),
                take_profit=price - (3 * atr),
            )

        # Exit when RSI normalizes
        elif self.in_position:
            if self.position_side == "long" and rsi > 50:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.7,
                    reason="RSI normalized (long)",
                )
            elif self.position_side == "short" and rsi < 50:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.7,
                    reason="RSI normalized (short)",
                )

        return None

    def _range_fade_signal(
        self,
        price: float,
        zscore: float,
        trend_strength: float,
        atr: float,
        timestamp: datetime,
    ) -> Optional[TradingSignal]:
        """Generate range fade signal (research-aligned).
        
        Matches research rule 'range_fade' in strategy_research.py:
        - Only trade when trend_strength <= 0.25 (range-bound market)
        - Long when zscore <= -1.5 (oversold relative to 20-day mean)
        - Short when zscore >= 1.5 (overbought relative to 20-day mean)
        - Exit when signal returns to 0 (zscore between -1.5 and 1.5, OR trend > 0.25)
        """
        # Check if we should exit existing position
        # Exit when: zscore crosses back toward zero (between -1.5 and 1.5) OR trend becomes too strong
        if self.in_position:
            should_exit = False
            exit_reason = ""
            
            if self.position_side == "long":
                # Exit long when zscore >= -1.5 (mean reversion progressing) OR trend > 0.25
                if zscore >= -1.5 or trend_strength > 0.25:
                    should_exit = True
                    exit_reason = f"z={zscore:.2f}, trend={trend_strength:.2f}"
            elif self.position_side == "short":
                # Exit short when zscore <= 1.5 (mean reversion progressing) OR trend > 0.25
                if zscore <= 1.5 or trend_strength > 0.25:
                    should_exit = True
                    exit_reason = f"z={zscore:.2f}, trend={trend_strength:.2f}"
            
            if should_exit:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.8,
                    reason=f"Range fade exit {self.position_side}: {exit_reason}",
                )
        
        # No new entries in trending markets
        if trend_strength > 0.25:
            return None
        
        # Entry long when oversold in range-bound market
        if not self.in_position and zscore <= -1.5:
            self.in_position = True
            self.position_side = "long"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.7,
                reason=f"Range fade long: z={zscore:.2f}, trend={trend_strength:.2f}",
                stop_loss=price - (2 * atr),
                take_profit=price + (2 * atr),  # Target: mean reversion to z=0
            )

        # Entry short when overbought in range-bound market
        if not self.in_position and zscore >= 1.5:
            self.in_position = True
            self.position_side = "short"
            self.entry_price = price
            return TradingSignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=self.symbol,
                timestamp=timestamp,
                price=price,
                rule_score=0.7,
                reason=f"Range fade short: z={zscore:.2f}, trend={trend_strength:.2f}",
                stop_loss=price + (2 * atr),
                take_profit=price - (2 * atr),  # Target: mean reversion to z=0
            )

        return None
