"""Yahoo Finance provider for stock market data."""
import logging
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple
from app.providers.data_provider import DataProvider, AssetInfo, OHLCVBar
from app.services.crypto_series import RawCandle, CandleSeries, build_series
from app.providers.instruments import Market, Instrument
from app.data.stock_instruments import (
    YAHOO_STOCK_INSTRUMENTS,
    get_stock_instrument,
    YAHOO_INTERVAL_MINUTES,
)

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed. Stock market data will not be available.")


class YahooFinanceError(Exception):
    """Base class for Yahoo Finance adapter failures."""


class YahooFinanceUnavailableError(YahooFinanceError):
    """Yahoo Finance could not be reached."""


class YahooFinanceApiError(YahooFinanceError):
    """Yahoo Finance returned an error."""


class YahooFinanceProvider(DataProvider):
    """Read-only adapter for Yahoo Finance stock market data."""

    def __init__(self, clock: Optional[Any] = None):
        if not YFINANCE_AVAILABLE:
            raise YahooFinanceUnavailableError(
                "yfinance is not installed. Run: pip install yfinance"
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._request_count = 0
        self._last_success_at: Optional[str] = None
        self._consecutive_failures = 0

    @property
    def provider_id(self) -> str:
        return "yahoo_finance"

    @property
    def provider_name(self) -> str:
        return "Yahoo Finance"

    @property
    def feed_mode(self) -> str:
        return "live" if self.is_available() else "disconnected"

    def is_available(self) -> bool:
        return self._consecutive_failures < 3 and self._last_success_at is not None

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._last_success_at = self._now().isoformat()
        self._request_count += 1

    def _record_failure(self, message: str) -> None:
        self._consecutive_failures += 1
        self._request_count += 1
        logger.warning("Yahoo Finance request failed: %s", message)

    def list_assets(self) -> List[AssetInfo]:
        return [
            AssetInfo.from_instrument(
                instrument,
                description=(
                    f"{instrument.name} — {instrument.exchange.value} equity, "
                    f"quoted in {instrument.quote_asset}"
                ),
            )
            for instrument in YAHOO_STOCK_INSTRUMENTS
        ]

    def get_instrument(self, instrument_id: str) -> Optional[AssetInfo]:
        instrument = get_stock_instrument(instrument_id)
        if instrument is None:
            return None
        return AssetInfo.from_instrument(
            instrument,
            description=f"{instrument.name} — {instrument.exchange.value} equity",
        )

    def _require_instrument(self, instrument_id: str) -> Instrument:
        instrument = get_stock_instrument(instrument_id)
        if instrument is None:
            raise YahooFinanceError(f"Unknown stock instrument: {instrument_id}")
        if instrument.market != Market.US:
            raise YahooFinanceError(f"{instrument_id!r} is not a US stock market instrument")
        return instrument

    def get_candle_series(
        self,
        instrument_id: str,
        interval_minutes: int = 1440,
        limit: Optional[int] = None,
    ) -> CandleSeries:
        """Fetch candles from Yahoo Finance."""
        if interval_minutes not in YAHOO_INTERVAL_MINUTES:
            raise YahooFinanceError(
                f"Unsupported interval {interval_minutes}m for Yahoo Finance; "
                f"choose one of {list(YAHOO_INTERVAL_MINUTES.keys())}"
            )

        instrument = self._require_instrument(instrument_id)
        yahoo_symbol = instrument.symbol
        interval = YAHOO_INTERVAL_MINUTES[interval_minutes]

        try:
            ticker = yf.Ticker(yahoo_symbol)
            period = "max"
            df = ticker.history(period=period, interval=interval)

            if df.empty:
                raise YahooFinanceApiError(f"No data returned for {yahoo_symbol}")

            self._record_success()

            rows = []
            for idx, row in df.iterrows():
                open_time = idx.to_pydatetime()
                if open_time.tzinfo is None:
                    open_time = open_time.replace(tzinfo=timezone.utc)

                rows.append(
                    RawCandle(
                        open_time=open_time,
                        open=Decimal(str(row["Open"])),
                        high=Decimal(str(row["High"])),
                        low=Decimal(str(row["Low"])),
                        close=Decimal(str(row["Close"])),
                        volume=Decimal(str(row["Volume"])),
                        trade_count=None,
                    )
                )

            if limit is not None:
                rows = rows[-limit:]

            return self._build_series(instrument_id, interval_minutes, rows)

        except Exception as exc:
            self._record_failure(str(exc))
            raise YahooFinanceApiError(f"Yahoo Finance API error: {exc}") from exc

    def get_candle_series_paginated(
        self,
        instrument_id: str,
        interval_minutes: int = 1440,
        target_bars: int = 2000,
        max_pages: int = 10,
    ) -> CandleSeries:
        """Fetch extended historical data using multiple requests."""
        if interval_minutes not in YAHOO_INTERVAL_MINUTES:
            raise YahooFinanceError(
                f"Unsupported interval {interval_minutes}m for Yahoo Finance"
            )

        instrument = self._require_instrument(instrument_id)
        yahoo_symbol = instrument.symbol
        interval = YAHOO_INTERVAL_MINUTES[interval_minutes]

        all_rows: List[RawCandle] = []
        end_date = self._now()
        pages_fetched = 0

        while pages_fetched < max_pages and len(all_rows) < target_bars:
            try:
                ticker = yf.Ticker(yahoo_symbol)
                start_date = end_date - timedelta(days=730)
                df = ticker.history(
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                    interval=interval,
                )

                if df.empty:
                    break

                self._record_success()
                pages_fetched += 1

                page_rows = []
                for idx, row in df.iterrows():
                    open_time = idx.to_pydatetime()
                    if open_time.tzinfo is None:
                        open_time = open_time.replace(tzinfo=timezone.utc)

                    page_rows.append(
                        RawCandle(
                            open_time=open_time,
                            open=Decimal(str(row["Open"])),
                            high=Decimal(str(row["High"])),
                            low=Decimal(str(row["Low"])),
                            close=Decimal(str(row["Close"])),
                            volume=Decimal(str(row["Volume"])),
                            trade_count=None,
                        )
                    )

                all_rows.extend(page_rows)
                end_date = start_date

                if len(all_rows) >= target_bars:
                    break

            except Exception as exc:
                self._record_failure(str(exc))
                if pages_fetched == 0:
                    raise YahooFinanceApiError(f"Yahoo Finance API error: {exc}") from exc
                break

        all_rows = sorted(all_rows, key=lambda r: r.open_time)
        if len(all_rows) > target_bars:
            all_rows = all_rows[-target_bars:]

        return self._build_series(instrument_id, interval_minutes, all_rows)

    def _build_series(
        self,
        instrument_id: str,
        interval_minutes: int,
        rows: List[RawCandle],
    ) -> CandleSeries:
        """Build a CandleSeries from raw rows."""
        return build_series(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            rows=rows,
            now=self._now(),
        )

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> List[OHLCVBar]:
        """Completed daily bars inside [start_date, end_date]."""
        instrument_id = self._instrument_id_for(symbol)
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise YahooFinanceError(
                "start_date and end_date must be ISO dates (YYYY-MM-DD)"
            ) from exc

        series = self.get_candle_series(instrument_id, interval_minutes=1440)
        bars: List[OHLCVBar] = []
        for candle in series.completed:
            bar_day = candle.open_time.date()
            if start <= bar_day <= end:
                bars.append(
                    OHLCVBar(
                        date=bar_day.isoformat(),
                        open=float(candle.open),
                        high=float(candle.high),
                        low=float(candle.low),
                        close=float(candle.close),
                        volume=float(candle.volume),
                    )
                )
        return bars

    def get_latest_bar(self, symbol: str) -> Optional[OHLCVBar]:
        instrument_id = self._instrument_id_for(symbol)
        series = self.get_candle_series(instrument_id, interval_minutes=1440, limit=2)
        if not series.completed:
            return None
        candle = series.completed[-1]
        return OHLCVBar(
            date=candle.open_time.date().isoformat(),
            open=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=float(candle.volume),
        )

    def _instrument_id_for(self, symbol: str) -> str:
        if ":" in symbol:
            return symbol
        for instrument in YAHOO_STOCK_INSTRUMENTS:
            if instrument.symbol == symbol:
                return instrument.instrument_id
        raise YahooFinanceError(f"Unknown symbol {symbol!r}; pass a stock instrument id")

    def get_date_range(self) -> Dict[str, Any]:
        return {
            "min": None,
            "max": self._now().date().isoformat(),
            "total_trading_days": None,
            "note": "Yahoo Finance provides up to 730 days of intraday data and 20+ years of daily data.",
        }

    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        return {
            "data_nature": "live",
            "adjustment": "adjusted",
            "timezone": "America/New_York",
            "note": "Yahoo Finance provides adjusted close prices for splits and dividends.",
            "note_zh": "Yahoo Finance 提供已調整分割和股息的收盤價。",
        }

    def compute_checksum(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> str:
        import hashlib
        content = f"{','.join(sorted(symbols))}:{start_date}:{end_date}:{self._request_count}"
        return hashlib.sha256(content.encode()).hexdigest()
