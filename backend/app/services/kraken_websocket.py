"""Kraken WebSocket client for real-time market data.

Connects to Kraken's WebSocket API, subscribes to ticker feeds,
and aggregates ticks into bars for signal generation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class KrakenBar:
    """Single OHLCV bar."""

    def __init__(
        self,
        symbol: str,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ):
        self.symbol = symbol
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class KrakenWebSocketClient:
    """WebSocket client for Kraken market data."""

    KRAKEN_WS_URL = "wss://ws.kraken.com"

    def __init__(
        self,
        symbols: list[str],
        on_bar: Callable[[KrakenBar], None],
        interval_seconds: int = 60,
    ):
        """
        Args:
            symbols: List of Kraken symbol pairs (e.g., ["XBT/USD", "ETH/USD"])
            on_bar: Callback function called when a bar closes
            interval_seconds: Bar interval in seconds (default 60 = 1 minute)
        """
        self.symbols = symbols
        self.on_bar = on_bar
        self.interval_seconds = interval_seconds
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60

        # Tick aggregation state
        self.current_bars: dict[str, dict] = {}
        self.last_bar_time: dict[str, float] = {}

    async def connect(self):
        """Establish WebSocket connection with retry logic."""
        while self.running:
            try:
                logger.info(f"Connecting to Kraken WebSocket: {self.KRAKEN_WS_URL}")
                self.ws = await websockets.connect(
                    self.KRAKEN_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                )
                logger.info("✅ WebSocket connected")
                self.reconnect_delay = 1  # Reset backoff

                # Subscribe to ticker data for all symbols
                await self._subscribe()

                # Start message processing loop
                await self._process_messages()

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self.running:
                logger.info(f"Reconnecting in {self.reconnect_delay}s...")
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(
                    self.reconnect_delay * 2, self.max_reconnect_delay
                )

    async def _subscribe(self):
        """Subscribe to ticker data for all symbols."""
        for symbol in self.symbols:
            subscribe_msg = {
                "event": "subscribe",
                "pair": [symbol],
                "subscription": {"name": "ticker"},
            }
            await self.ws.send(json.dumps(subscribe_msg))
            logger.info(f"📡 Subscribed to {symbol}")

    async def _process_messages(self):
        """Process incoming WebSocket messages."""
        async for message in self.ws:
            if not self.running:
                break

            try:
                data = json.loads(message)

                # Kraken sends arrays for ticker data
                if isinstance(data, list) and len(data) >= 4:
                    channel_name = data[-2]
                    if channel_name == "ticker":
                        await self._handle_ticker(data)

                # Handle subscription confirmations and heartbeats
                elif isinstance(data, dict):
                    event = data.get("event")
                    if event == "subscriptionStatus":
                        status = data.get("status")
                        pair = data.get("pair")
                        logger.info(f"Subscription {status} for {pair}")
                    elif event == "heartbeat":
                        pass  # Ignore heartbeats
                    elif event == "error":
                        logger.error(f"Kraken error: {data.get('errorMessage')}")

            except json.JSONDecodeError:
                logger.warning(f"Failed to decode message: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_ticker(self, data: list):
        """Handle incoming ticker data and aggregate into bars."""
        try:
            # Kraken ticker format: [channelID, {ticker data}, channelName, pair]
            ticker_data = data[1]
            pair = data[3]

            # Extract price (use 'a' ask price for consistency)
            ask_prices = ticker_data.get("a", [])
            if not ask_prices:
                return

            price = float(ask_prices[0])
            timestamp = time.time()
            
            # Debug: Log first few ticks
            if not hasattr(self, '_tick_count'):
                self._tick_count = {}
            self._tick_count[pair] = self._tick_count.get(pair, 0) + 1
            if self._tick_count[pair] <= 3:
                logger.info(f"📥 Received tick #{self._tick_count[pair]} for {pair}: ${price:.2f}")

            # Initialize bar if needed
            if pair not in self.current_bars:
                self.current_bars[pair] = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                }
                self.last_bar_time[pair] = timestamp

            # Update current bar
            bar = self.current_bars[pair]
            bar["high"] = max(bar["high"], price)
            bar["low"] = min(bar["low"], price)
            bar["close"] = price
            bar["volume"] += 1  # Count ticks as volume proxy

            # Check if bar should close
            elapsed = timestamp - self.last_bar_time[pair]
            if elapsed >= self.interval_seconds:
                # Bar closed, create KrakenBar object
                bar_timestamp = datetime.fromtimestamp(
                    self.last_bar_time[pair], tz=timezone.utc
                )
                kraken_bar = KrakenBar(
                    symbol=pair,
                    timestamp=bar_timestamp,
                    open=bar["open"],
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    volume=bar["volume"],
                )

                logger.info(
                    f"📊 Bar closed for {pair}: O={kraken_bar.open:.2f} H={kraken_bar.high:.2f} "
                    f"L={kraken_bar.low:.2f} C={kraken_bar.close:.2f} V={kraken_bar.volume}"
                )

                # Call callback
                self.on_bar(kraken_bar)

                # Reset for next bar
                self.current_bars[pair] = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                }
                self.last_bar_time[pair] = timestamp

        except Exception as e:
            logger.error(f"Error handling ticker: {e}")

    async def start(self):
        """Start the WebSocket client."""
        self.running = True
        logger.info(f"🚀 Starting Kraken WebSocket client for {len(self.symbols)} symbols")
        await self.connect()

    async def stop(self):
        """Stop the WebSocket client."""
        self.running = False
        if self.ws:
            await self.ws.close()
        logger.info("🛑 Kraken WebSocket client stopped")
