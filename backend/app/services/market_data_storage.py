"""Market data storage service.

Persists OHLCV bars and metadata to the database, enabling:
- Avoiding repeated provider API calls
- Historical analysis and backtesting
- Data quality tracking and provenance
"""
import logging
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.providers.data_provider import OHLCVBar
from app.lib.timeutil import utcnow

logger = logging.getLogger(__name__)


class MarketDataStorage:
    """Storage layer for market data bars and metadata."""

    def __init__(self, db: Session):
        self.db = db

    def store_bars(
        self,
        instrument_id: str,
        provider_id: str,
        bars: List[OHLCVBar],
        data_nature: str = "unknown",
        adjustment_status: Optional[str] = None,
        timezone: Optional[str] = None,
        currency: Optional[str] = None,
        provider_note: Optional[str] = None,
        provider_note_zh: Optional[str] = None,
    ) -> int:
        """Store OHLCV bars for an instrument.

        Uses upsert logic: if a bar with the same instrument_id, bar_timestamp,
        and provider_id exists, it's updated; otherwise it's inserted.

        Returns the number of bars stored.
        """
        if not bars:
            return 0

        stored_count = 0
        bar_dates = []

        for bar in bars:
            if isinstance(bar.date, str):
                bar_date_obj = date.fromisoformat(bar.date)
            else:
                bar_date_obj = bar.date

            bar_timestamp = datetime.combine(bar_date_obj, datetime.min.time())

            existing = self.db.query(MarketDataBar).filter(
                and_(
                    MarketDataBar.instrument_id == instrument_id,
                    MarketDataBar.bar_timestamp == bar_timestamp,
                    MarketDataBar.provider_id == provider_id,
                )
            ).first()

            if existing:
                existing.open = bar.open
                existing.high = bar.high
                existing.low = bar.low
                existing.close = bar.close
                existing.volume = bar.volume
                existing.adjustment_factor = getattr(bar, 'adjustment_factor', None)
                existing.is_adjusted = getattr(bar, 'is_adjusted', None)
                existing.fetched_at = utcnow()
            else:
                new_bar = MarketDataBar(
                    instrument_id=instrument_id,
                    provider_id=provider_id,
                    bar_date=bar_date_obj,
                    bar_timestamp=bar_timestamp,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    adjustment_factor=getattr(bar, 'adjustment_factor', None),
                    is_adjusted=getattr(bar, 'is_adjusted', None),
                )
                self.db.add(new_bar)

            bar_dates.append(bar_date_obj)
            stored_count += 1

        self._update_metadata(
            instrument_id=instrument_id,
            provider_id=provider_id,
            data_nature=data_nature,
            adjustment_status=adjustment_status,
            timezone=timezone,
            currency=currency,
            provider_note=provider_note,
            provider_note_zh=provider_note_zh,
            bar_dates=bar_dates,
        )

        self.db.commit()
        logger.info(f"Stored {stored_count} bars for {instrument_id} from {provider_id}")
        return stored_count

    def _update_metadata(
        self,
        instrument_id: str,
        provider_id: str,
        data_nature: str,
        adjustment_status: Optional[str],
        timezone: Optional[str],
        currency: Optional[str],
        provider_note: Optional[str],
        provider_note_zh: Optional[str],
        bar_dates: List[date],
    ):
        """Update or create metadata record for an instrument."""
        metadata = self.db.query(MarketDataMetadata).filter(
            and_(
                MarketDataMetadata.instrument_id == instrument_id,
                MarketDataMetadata.provider_id == provider_id,
            )
        ).first()

        if not metadata:
            metadata = MarketDataMetadata(
                instrument_id=instrument_id,
                provider_id=provider_id,
            )
            self.db.add(metadata)

        metadata.data_nature = data_nature
        metadata.adjustment_status = adjustment_status
        metadata.timezone = timezone
        metadata.currency = currency
        metadata.provider_note = provider_note
        metadata.provider_note_zh = provider_note_zh
        metadata.last_success_at = utcnow()
        metadata.consecutive_errors = 0
        metadata.health_status = "ok"

        if bar_dates:
            min_date = min(bar_dates)
            max_date = max(bar_dates)

            if metadata.first_bar_date is None or min_date < metadata.first_bar_date:
                metadata.first_bar_date = min_date
            if metadata.last_bar_date is None or max_date > metadata.last_bar_date:
                metadata.last_bar_date = max_date

            bar_count = self.db.query(MarketDataBar).filter(
                and_(
                    MarketDataBar.instrument_id == instrument_id,
                    MarketDataBar.provider_id == provider_id,
                )
            ).count()
            metadata.total_bars = bar_count

    def get_bars(
        self,
        instrument_id: str,
        provider_id: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
    ) -> List[MarketDataBar]:
        """Retrieve bars for an instrument with optional filters."""
        query = self.db.query(MarketDataBar).filter(
            MarketDataBar.instrument_id == instrument_id
        )

        if provider_id:
            query = query.filter(MarketDataBar.provider_id == provider_id)

        if start_date:
            query = query.filter(MarketDataBar.bar_date >= start_date)

        if end_date:
            query = query.filter(MarketDataBar.bar_date <= end_date)

        query = query.order_by(MarketDataBar.bar_timestamp)

        if limit:
            query = query.limit(limit)

        return query.all()

    def get_metadata(
        self,
        instrument_id: str,
        provider_id: Optional[str] = None,
    ) -> Optional[MarketDataMetadata]:
        """Retrieve metadata for an instrument."""
        query = self.db.query(MarketDataMetadata).filter(
            MarketDataMetadata.instrument_id == instrument_id
        )

        if provider_id:
            query = query.filter(MarketDataMetadata.provider_id == provider_id)

        return query.first()

    def mark_error(self, instrument_id: str, provider_id: str, error_msg: str):
        """Record a fetch error for an instrument."""
        metadata = self.db.query(MarketDataMetadata).filter(
            and_(
                MarketDataMetadata.instrument_id == instrument_id,
                MarketDataMetadata.provider_id == provider_id,
            )
        ).first()

        if not metadata:
            metadata = MarketDataMetadata(
                instrument_id=instrument_id,
                provider_id=provider_id,
                data_nature="unknown",
                consecutive_errors=0,
            )
            self.db.add(metadata)

        metadata.last_error_at = utcnow()
        metadata.consecutive_errors += 1

        if metadata.consecutive_errors >= 3:
            metadata.health_status = "degraded"

        self.db.commit()

    def get_stale_instruments(
        self,
        max_age_days: int = 7,
        provider_id: Optional[str] = None,
    ) -> List[MarketDataMetadata]:
        """Find instruments with stale data (not updated recently)."""
        cutoff = utcnow() - timedelta(days=max_age_days)

        query = self.db.query(MarketDataMetadata).filter(
            MarketDataMetadata.last_success_at < cutoff
        )

        if provider_id:
            query = query.filter(MarketDataMetadata.provider_id == provider_id)

        return query.all()
