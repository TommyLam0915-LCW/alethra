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
    """Supported strategy types with stable rule_id.
    
    Strategy matching must use rule_id, not display names.
    Display names (Chinese or English) are for presentation only.
    """
    MEAN_REVERSION_RSI_BB = "mean_reversion_rsi_bb_v1"
    BB_MEAN_REVERSION = "bb_mean_reversion_v1"
    MOMENTUM_RSI = "momentum_rsi_v1"
    # Note: Arbitrage (套利) requires multi-leg execution and is NOT
    # supported by this single-instrument signal generator.


# Registry mapping rule_id to strategy metadata
STRATEGY_REGISTRY = {
    StrategyType.MEAN_REVERSION_RSI_BB.value: {
        "display_name_zh": "RSI+布林帶均值回歸",
        "display_name_en": "RSI+Bollinger Band Mean Reversion",
        "description": "Mean reversion using RSI oversold/overbought with Bollinger Band confirmation",
        "generator_method": "_mean_reversion_signal",
    },
    StrategyType.BB_MEAN_REVERSION.value: {
        "display_name_zh": "布林帶均值回歸",
        "display_name_en": "Bollinger Band Mean Reversion",
        "description": "Mean reversion using Bollinger Band touches",
        "generator_method": "_bb_mean_reversion_signal",
    },
    StrategyType.MOMENTUM_RSI.value: {
        "display_name_zh": "RSI動量",
        "display_name_en": "RSI Momentum",
        "description": "Trend following using RSI momentum",
        "generator_method": "_momentum_signal",
    },
}


@dataclass
class TradingSignal:
    """Generated trading signal.
    
    Attributes:
        signal_type: Type of signal (ENTRY_LONG, ENTRY_SHORT, EXIT, HOLD)
        symbol: Trading pair symbol
        timestamp: Signal generation time
        price: Price at signal generation
        rule_score: Uncalibrated score from rule-based logic (NOT a probability).
            This is a heuristic indicator of signal strength based on technical
            conditions, not a statistical probability of profit. No calibration
            or backtesting evidence supports this as a win rate.
        reason: Human-readable explanation of signal trigger
        stop_loss: Optional stop loss price
        take_profit: Optional take profit price
    """
    signal_type: SignalType
    symbol: str
    timestamp: datetime
    price: float
    rule_score: float  # Renamed from confidence - NOT a probability
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


class SignalGenerator:
    """Generate trading signals from market bars.
    
    Strategy matching uses stable rule_id from STRATEGY_REGISTRY.
    Display names (skill parameter) are for logging only, not for strategy selection.
    """

    def __init__(
        self,
        symbol: str,
        rule_id: str,
        interval_minutes: int,
        skill: Optional[str] = None,  # Display name only, not used for matching
        max_bars: int = 100,
    ):
        """
        Args:
            symbol: Trading pair symbol
            rule_id: Stable strategy identifier from STRATEGY_REGISTRY
            interval_minutes: Bar interval in minutes
            skill: Optional display name for logging (NOT used for strategy matching)
            max_bars: Maximum bars to keep in memory
        
        Raises:
            ValueError: If rule_id is not in STRATEGY_REGISTRY
        """
        # Validate rule_id
        if rule_id not in STRATEGY_REGISTRY:
            supported = ", ".join(STRATEGY_REGISTRY.keys())
            raise ValueError(
                f"Unknown strategy rule_id: {rule_id}. "
                f"Supported strategies: {supported}. "
                f"Unknown strategies are rejected, not defaulted."
            )
        
        self.symbol = symbol
        self.rule_id = rule_id
        self.skill = skill or STRATEGY_REGISTRY[rule_id]["display_name_zh"]
        self.interval_minutes = interval_minutes
        self.max_bars = max_bars
        self.strategy_meta = STRATEGY_REGISTRY[rule_id]

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
        
        logger.info(
            f"Initialized signal generator: rule_id={rule_id}, "
            f"display={self.skill}, symbol={symbol}"
        )

    def on_bar(self, bar: KrakenBar):
        """Process new bar and update history."""
        self.bars.append(bar)
        self.prices.append(bar.close)
        self.highs.append(bar.high)
        self.lows.append(bar.low)

    def generate_signal(self) -> Optional[TradingSignal]:
        """Generate trading signal based on current market state.
        
        Uses rule_id to select strategy method from STRATEGY_REGISTRY.
        Does NOT match by display name or Chinese characters.
        """
        if len(self.prices) < 20:
            if len(self.prices) % 5 == 0:  # Log every 5 bars
                logger.info(f"📈 {self.skill} on {self.symbol}: {len(self.prices)}/20 bars collected")
            return None  # Not enough data

        current_price = self.prices[-1]
        timestamp = self.bars[-1].timestamp
        
        logger.info(f"🔍 {self.skill} (rule_id={self.rule_id}) on {self.symbol}: Generating signal with {len(self.prices)} bars, price=${current_price:.2f}")

        # Calculate indicators
        rsi = TechnicalIndicators.rsi(list(self.prices), period=14)
        bb = TechnicalIndicators.bollinger_bands(list(self.prices), period=20)
        atr = TechnicalIndicators.atr(
            list(self.highs), list(self.lows), list(self.prices), period=14
        )

        if rsi is None or bb is None or atr is None:
            return None

        bb_upper, bb_middle, bb_lower = bb

        # Strategy selection by rule_id, NOT by display name
        generator_method_name = self.strategy_meta["generator_method"]
        generator_method = getattr(self, generator_method_name, None)
        
        if generator_method is None:
            logger.error(f"Generator method {generator_method_name} not found for rule_id {self.rule_id}")
            return None

        # Call the appropriate strategy method
        if generator_method_name == "_mean_reversion_signal":
            return generator_method(current_price, rsi, bb_lower, bb_upper, atr, timestamp)
        elif generator_method_name == "_bb_mean_reversion_signal":
            return generator_method(current_price, bb_lower, bb_upper, bb_middle, atr, timestamp)
        elif generator_method_name == "_momentum_signal":
            return generator_method(current_price, rsi, atr, timestamp)
        else:
            logger.error(f"Unknown generator method: {generator_method_name}")
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
                rule_score=0.7,  # Uncalibrated rule score, NOT probability
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
                rule_score=0.7,  # Uncalibrated rule score, NOT probability
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
                    rule_score=0.8,  # Uncalibrated rule score, NOT probability
                    reason="Mean reversion complete (long)",
                )
            elif self.position_side == "short" and (rsi < 50 or price <= bb_lower):
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.8,  # Uncalibrated rule score, NOT probability
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
                rule_score=0.65,  # Uncalibrated rule score, NOT probability
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
                rule_score=0.65,  # Uncalibrated rule score, NOT probability
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
                    rule_score=0.75,  # Uncalibrated rule score, NOT probability
                    reason="Reached middle Bollinger Band (long)",
                )
            elif self.position_side == "short" and price <= bb_middle:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.75,  # Uncalibrated rule score, NOT probability
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
                rule_score=0.6,  # Uncalibrated rule score, NOT probability
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
                rule_score=0.6,  # Uncalibrated rule score, NOT probability
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
                    rule_score=0.7,  # Uncalibrated rule score, NOT probability
                    reason="Momentum reversal (long)",
                )
            elif self.position_side == "short" and rsi < 25:
                self.in_position = False
                return TradingSignal(
                    signal_type=SignalType.EXIT,
                    symbol=self.symbol,
                    timestamp=timestamp,
                    price=price,
                    rule_score=0.7,  # Uncalibrated rule score, NOT probability
                    reason="Momentum reversal (short)",
                )

        return None
