"""Synthetic forex data provider for testing multi-market support.

This provider generates realistic forex price data for testing purposes.
For production use, replace with OANDA, FXCM, or similar.
"""
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from app.providers.data_provider import DataProvider, AssetInfo, OHLCVBar
from app.services.crypto_series import RawCandle, CandleSeries, build_series
from app.providers.instruments import Market, Instrument
from app.data.forex_instruments import (
    FOREX_INSTRUMENTS,
    get_forex_instrument,
    FOREX_INTERVAL_MINUTES,
)

logger = logging.getLogger(__name__)


class SyntheticForexError(Exception):
    """Base class for synthetic forex provider failures."""


class SyntheticForexProvider(DataProvider):
    """Generates realistic synthetic forex data for testing."""

    def __init__(self, clock: Optional[Any] = None):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._request_count = 0
        self._last_success_at: Optional[str] = None
        self._consecutive_failures = 0

    @property
    def provider_id(self) -> str:
        return "synthetic_forex"

    @property
    def provider_name(self) -> str:
        return "Synthetic Forex Data (Testing)"

    @property
    def feed_mode(self) -> str:
        return "synthetic"

    def is_available(self) -> bool:
        return True

    def list_assets(self) -> List[AssetInfo]:
        return [
            AssetInfo(
                symbol=inst.symbol,
                name=inst.name,
                description=f"{inst.name} (synthetic data for testing)",
                exchange=inst.exchange.value,
                currency=inst.currency.value,
                asset_type=inst.asset_type.value,
                instrument=inst,
            )
            for inst in FOREX_INSTRUMENTS
        ]

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        inst = get_forex_instrument(instrument_id)
        if inst is None:
            return None
        return AssetInfo(
            symbol=inst.symbol,
            name=inst.name,
            description=f"{inst.name} (synthetic data for testing)",
            exchange=inst.exchange.value,
            currency=inst.currency.value,
            asset_type=inst.asset_type.value,
            instrument=inst,
        )

    def get_candle_series(
        self,
        instrument_id: str,
        interval_minutes: int = 1440,
        limit: Optional[int] = None,
    ) -> CandleSeries:
        """Generate synthetic candle data."""
        if interval_minutes not in FOREX_INTERVAL_MINUTES:
            raise SyntheticForexError(
                f"Unsupported interval {interval_minutes}m; "
                f"choose one of {list(FOREX_INTERVAL_MINUTES.keys())}"
            )

        instrument = self._require_instrument(instrument_id)
        limit = limit or 720

        try:
            rows = self._generate_synthetic_candles(
                instrument, interval_minutes, limit
            )
            self._record_success()
            return self._build_series(instrument_id, interval_minutes, rows)
        except Exception as exc:
            self._record_failure(str(exc))
            raise

    def get_candle_series_paginated(
        self,
        instrument_id: str,
        interval_minutes: int = 1440,
        target_bars: int = 2000,
        max_pages: int = 10,
    ) -> CandleSeries:
        """Generate synthetic candle data with pagination support."""
        if target_bars < 120 or target_bars > 10000:
            raise SyntheticForexError(
                f"target_bars must be in [120, 10000], got {target_bars}"
            )
        if max_pages < 1 or max_pages > 50:
            raise SyntheticForexError(
                f"max_pages must be in [1, 50], got {max_pages}"
            )

        instrument = self._require_instrument(instrument_id)
        rows = self._generate_synthetic_candles(
            instrument, interval_minutes, target_bars
        )
        self._record_success()
        return self._build_series(instrument_id, interval_minutes, rows)

    def _generate_synthetic_candles(
        self,
        instrument: Instrument,
        interval_minutes: int,
        count: int,
    ) -> List[RawCandle]:
        """Generate realistic synthetic OHLCV data for forex."""
        now = self._clock()
        base_price = self._get_base_price(instrument.symbol)
        volatility = self._get_volatility(instrument.symbol)

        candles = []
        price = base_price

        # Align timestamps to the interval grid
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        interval = timedelta(minutes=interval_minutes)
        
        # Round down to the nearest interval boundary
        now_ts = now.timestamp()
        interval_secs = interval_minutes * 60
        aligned_now_ts = (int(now_ts) // interval_secs) * interval_secs
        aligned_now = datetime.fromtimestamp(aligned_now_ts, tz=timezone.utc)

        for i in range(count):
            open_time = aligned_now - interval * (count - i)

            # Generate realistic price movement
            import random
            random.seed(self._get_seed(instrument.symbol, i))
            change_pct = random.gauss(0, volatility)
            price = price * (1 + change_pct)

            # Generate OHLCV
            high_pct = abs(random.gauss(0, volatility * 0.5))
            low_pct = abs(random.gauss(0, volatility * 0.5))
            open_pct = random.gauss(0, volatility * 0.3)
            close_pct = random.gauss(0, volatility * 0.3)

            open_price = price * (1 + open_pct)
            close_price = price * (1 + close_pct)
            high_price = max(open_price, close_price) * (1 + high_pct)
            low_price = min(open_price, close_price) * (1 - low_pct)
            
            # Forex volume is typically measured in lots (100,000 units)
            volume = random.randint(100, 10000) * 100000

            candles.append(
                RawCandle(
                    open_time=open_time,
                    open=Decimal(f"{open_price:.5f}"),
                    high=Decimal(f"{high_price:.5f}"),
                    low=Decimal(f"{low_price:.5f}"),
                    close=Decimal(f"{close_price:.5f}"),
                    volume=Decimal(str(volume)),
                    trade_count=None,
                )
            )

        return candles

    def _get_base_price(self, symbol: str) -> float:
        """Get a realistic base price for the forex pair."""
        prices = {
            "EUR/USD": 1.0850,
            "GBP/USD": 1.2650,
            "USD/JPY": 149.50,
        }
        return prices.get(symbol, 1.0)

    def _get_volatility(self, symbol: str) -> float:
        """Get realistic daily volatility for the forex pair."""
        # Forex is typically less volatile than stocks
        volatilities = {
            "EUR/USD": 0.005,  # 0.5% daily
            "GBP/USD": 0.007,  # 0.7% daily
            "USD/JPY": 0.006,  # 0.6% daily
        }
        return volatilities.get(symbol, 0.005)

    def _get_seed(self, symbol: str, index: int) -> int:
        """Generate a deterministic seed for reproducibility."""
        hash_input = f"{symbol}:{index}"
        return int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)

    def _require_instrument(self, instrument_id: str) -> Instrument:
        instrument = get_forex_instrument(instrument_id)
        if instrument is None:
            raise SyntheticForexError(
                f"Instrument {instrument_id!r} is not registered"
            )
        return instrument

    def _build_series(
        self,
        instrument_id: str,
        interval_minutes: int,
        rows: List[RawCandle],
    ) -> CandleSeries:
        """Build a CandleSeries from raw candles using the standard builder."""
        return build_series(instrument_id, interval_minutes, rows)

    def _record_success(self) -> None:
        self._request_count += 1
        self._last_success_at = self._clock().isoformat()
        self._consecutive_failures = 0

    def _record_failure(self, reason: str) -> None:
        self._request_count += 1
        self._consecutive_failures += 1
        logger.warning(
            "Synthetic forex provider failure #%d: %s",
            self._consecutive_failures,
            reason,
        )

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        return {
            "data_nature": "synthetic",
            "adjustment": "not_applicable",
            "timezone": "UTC",
            "note": "Synthetic data for testing. Replace with real provider for production.",
            "note_zh": "合成資料僅供測試。生產環境請使用真實資料來源。",
        }

    # Abstract methods from DataProvider
    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        raise NotImplementedError("get_bars not implemented for synthetic provider")

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        raise NotImplementedError("get_latest_bar not implemented for synthetic provider")

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "start": "2020-01-01",
            "end": self._clock().strftime("%Y-%m-%d"),
            "note": "Synthetic data range",
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        hasher = hashlib.sha256()
        for symbol in sorted(symbols):
            hasher.update(f"{symbol}:{start_date}:{end_date}".encode())
        return hasher.hexdigest()
