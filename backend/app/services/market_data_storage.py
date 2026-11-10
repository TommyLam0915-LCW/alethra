"""Market data storage service.

Persists OHLCV bars and metadata to the database, enabling:
- Avoiding repeated provider API calls
- Historical analysis and backtesting
- Data quality tracking and provenance
"""
import json
import logging
from datetime import datetime, date, timedelta, timezone as dt_timezone
from typing import List, Optional, Dict, Any, Mapping, Sequence
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.market_data import (
    IntradayMarketDataBar,
    MarketDataBar,
    MarketDataMetadata,
)
from app.providers.data_provider import OHLCVBar
from app.services.crypto_series import Candle
from app.lib.timeutil import utcnow

logger = logging.getLogger(__name__)


def _naive_utc(value: datetime) -> datetime:
    """Storage keeps naive UTC; a tz-aware bound must be converted, not dropped."""
    if value.tzinfo is None:
        return value
    return value.astimezone(dt_timezone.utc).replace(tzinfo=None)


# ``MarketDataMetadata.provider_note`` is String(500).
PROVIDER_NOTE_MAX_CHARS = 500


def provenance_note(provenance: Mapping[str, Any]) -> str:
    """Serialize a source chain for ``provider_note``, refusing to truncate it.

    A silently shortened chain is no longer evidence: the hash or the commit
    would be the part that gets cut. An import that cannot fit its chain in the
    column has to say so before it writes, not after.
    """
    note = json.dumps(provenance, separators=(",", ":"), sort_keys=True)
    if len(note) > PROVIDER_NOTE_MAX_CHARS:
        raise ValueError(
            f"provenance note is {len(note)} characters, over provider_note's "
            f"{PROVIDER_NOTE_MAX_CHARS}-character column; refusing to truncate "
            "the source chain"
        )
    return note


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

        self.db.flush()
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

    def record_offline_provenance(
        self,
        *,
        instrument_id: str,
        provider_id: str,
        data_nature: str,
        timezone: Optional[str],
        currency: Optional[str],
        provenance: Mapping[str, Any],
        bar_dates: Sequence[date],
    ) -> str:
        """Attach an offline import's source chain to the instrument's metadata.

        A stored bar carries no record of the file it came from, so
        ``provider_note`` is where that chain is written: repository, commit,
        path, the file's SHA256, and the fingerprint the venue read itself
        produced. The chain is limited and says so — this deployment did not
        make the request, so the values are as authentic as the file, not
        independently authenticated against the venue.

        ``data_nature`` records where the numbers originated (``live`` means a
        venue produced them), not how well this import was witnessed. It is not
        a credential: the readers accept a fixed vocabulary, so a stronger token
        invented here is not believed anywhere else — it collapses to
        ``"unknown"`` and the row ends up claiming less than it appeared to.
        """
        note = provenance_note(provenance)
        self._update_metadata(
            instrument_id=instrument_id,
            provider_id=provider_id,
            data_nature=data_nature,
            adjustment_status="not_applicable",
            timezone=timezone,
            currency=currency,
            provider_note=note,
            provider_note_zh=(
                "離線匯入：來源鏈見 provider_note。本次匯入未執行行情讀取，"
                "數值未經本部署獨立認證。"
            ),
            bar_dates=list(bar_dates),
        )
        self.db.commit()
        return note

    def store_candles(
        self,
        *,
        instrument_id: str,
        provider_id: str,
        candles: Sequence[Candle],
        timezone: str,
        currency: Optional[str],
        data_nature: str = "live",
        adjustment_status: Optional[str] = "not_applicable",
        provider_note: Optional[str] = None,
        provider_note_zh: Optional[str] = None,
    ) -> Dict[str, int]:
        """Persist completed candles keyed by their own open time.

        ``store_bars`` collapses a bar to midnight because daily equity bars are
        day-granularity; an intraday candle must keep its timestamp or every
        minute of a session would overwrite one row. Forming candles are
        refused here — storage only holds bars that can no longer change.
        """
        stored = 0
        updated = 0
        skipped_forming = 0
        bar_dates: List[date] = []

        for candle in candles:
            if not candle.is_final:
                skipped_forming += 1
                continue

            open_time = candle.open_time
            if open_time.tzinfo is not None:
                open_time = open_time.astimezone(dt_timezone.utc).replace(tzinfo=None)
            bar_date_obj = open_time.date()

            existing = self.db.query(MarketDataBar).filter(
                and_(
                    MarketDataBar.instrument_id == instrument_id,
                    MarketDataBar.bar_timestamp == open_time,
                    MarketDataBar.provider_id == provider_id,
                )
            ).first()

            if existing:
                existing.bar_date = bar_date_obj
                existing.open = float(candle.open)
                existing.high = float(candle.high)
                existing.low = float(candle.low)
                existing.close = float(candle.close)
                existing.volume = None
                existing.volume_decimal = candle.volume
                existing.fetched_at = utcnow()
                updated += 1
            else:
                self.db.add(
                    MarketDataBar(
                        instrument_id=instrument_id,
                        provider_id=provider_id,
                        bar_date=bar_date_obj,
                        bar_timestamp=open_time,
                        open=float(candle.open),
                        high=float(candle.high),
                        low=float(candle.low),
                        close=float(candle.close),
                        volume=None,
                        volume_decimal=candle.volume,
                    )
                )
                stored += 1

            bar_dates.append(bar_date_obj)

        self.db.flush()
        if bar_dates:
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

        logger.info(
            "Stored %s new / %s updated candles for %s from %s (skipped forming=%s)",
            stored,
            updated,
            instrument_id,
            provider_id,
            skipped_forming,
        )
        return {
            "stored": stored,
            "updated": updated,
            "skipped_forming": skipped_forming,
        }

    def store_intraday_candles(
        self,
        *,
        instrument_id: str,
        provider_id: str,
        timeframe: str,
        candles: Sequence[Candle],
        volume_overrides: Optional[Mapping[datetime, Optional[float]]] = None,
    ) -> Dict[str, int]:
        """Upsert live intraday candles under the full identity tuple.

        The row key is (instrument, provider, timeframe, open time), so a
        1-minute series and a 5-minute series for one symbol are separate rows,
        and two providers can never overwrite each other.

        Closed bars are never rewritten. Re-fetching a final bar is a no-op
        (provider corrections are deliberately not applied here) and a response
        that calls an already-closed bucket "forming" is refused, so a clock
        skew or an out-of-order response cannot walk a settled bar backwards.

        Returns counts; nothing is inferred from a response with no rows.

        ``volume_overrides`` carries the provider's own volume for each open
        time. Series arithmetic needs a numeric volume, so a provider that did
        not report one arrives here as zero; the override lets storage keep
        "unknown" as NULL instead of inventing a trade of zero shares.
        """
        accepted: Dict[datetime, Candle] = {}
        for candle in candles:
            open_time = candle.open_time
            if open_time.tzinfo is not None:
                open_time = open_time.astimezone(dt_timezone.utc).replace(tzinfo=None)
            previous = accepted.get(open_time)
            # A single response can repeat a timestamp; keep the closed form so
            # a trailing open duplicate cannot replace a finished bar.
            if previous is None or (candle.is_final and not previous.is_final):
                accepted[open_time] = candle

        stored = 0
        updated = 0
        unchanged_final = 0
        skipped_forming_regression = 0
        forming_rows = 0

        for open_time in sorted(accepted):
            candle = accepted[open_time]
            existing = self.db.query(IntradayMarketDataBar).filter(
                and_(
                    IntradayMarketDataBar.instrument_id == instrument_id,
                    IntradayMarketDataBar.provider_id == provider_id,
                    IntradayMarketDataBar.timeframe == timeframe,
                    IntradayMarketDataBar.bar_timestamp == open_time,
                )
            ).first()

            if existing is not None and existing.is_final:
                if not candle.is_final:
                    skipped_forming_regression += 1
                else:
                    unchanged_final += 1
                continue

            volume = None if candle.volume is None else int(candle.volume)
            if volume_overrides is not None and open_time in volume_overrides:
                reported = volume_overrides[open_time]
                volume = None if reported is None else int(reported)
            values = dict(
                bar_date=open_time.date(),
                open=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
                volume=volume,
                is_final=bool(candle.is_final),
                fetched_at=utcnow(),
            )

            if existing is not None:
                for key, value in values.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                self.db.add(
                    IntradayMarketDataBar(
                        instrument_id=instrument_id,
                        provider_id=provider_id,
                        timeframe=timeframe,
                        bar_timestamp=open_time,
                        **values,
                    )
                )
                stored += 1

            if not candle.is_final:
                forming_rows += 1

        self.db.commit()
        logger.info(
            "Stored intraday %s/%s/%s: new=%s updated=%s unchanged_final=%s "
            "forming=%s skipped_forming_regression=%s",
            instrument_id,
            provider_id,
            timeframe,
            stored,
            updated,
            unchanged_final,
            forming_rows,
            skipped_forming_regression,
        )
        return {
            "stored": stored,
            "updated": updated,
            "unchanged_final": unchanged_final,
            "forming": forming_rows,
            "skipped_forming_regression": skipped_forming_regression,
        }

    def get_intraday_candles(
        self,
        instrument_id: str,
        provider_id: str,
        timeframe: str,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[IntradayMarketDataBar]:
        """Return stored intraday bars in ascending timestamp order.

        With only ``limit`` given, the newest ``limit`` rows are selected and
        then re-sorted ascending, so a chart never receives a reversed series.
        """
        query = self.db.query(IntradayMarketDataBar).filter(
            and_(
                IntradayMarketDataBar.instrument_id == instrument_id,
                IntradayMarketDataBar.provider_id == provider_id,
                IntradayMarketDataBar.timeframe == timeframe,
            )
        )

        if start is not None:
            query = query.filter(
                IntradayMarketDataBar.bar_timestamp >= _naive_utc(start)
            )
        if end is not None:
            query = query.filter(
                IntradayMarketDataBar.bar_timestamp <= _naive_utc(end)
            )

        if limit is not None and start is None and end is None:
            rows = query.order_by(
                IntradayMarketDataBar.bar_timestamp.desc()
            ).limit(limit).all()
            return list(reversed(rows))

        query = query.order_by(IntradayMarketDataBar.bar_timestamp)
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    def get_intraday_latest(
        self,
        instrument_id: str,
        provider_id: str,
        timeframe: str,
    ) -> Optional[IntradayMarketDataBar]:
        """Newest stored intraday bar for one series, or None."""
        return (
            self.db.query(IntradayMarketDataBar)
            .filter(
                and_(
                    IntradayMarketDataBar.instrument_id == instrument_id,
                    IntradayMarketDataBar.provider_id == provider_id,
                    IntradayMarketDataBar.timeframe == timeframe,
                )
            )
            .order_by(IntradayMarketDataBar.bar_timestamp.desc())
            .first()
        )

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

    def get_all_metadata(self, instrument_id: str) -> List[MarketDataMetadata]:
        """Every stored dataset for one instrument, one row per provider.

        Rows for the same instrument can come from more than one provider
        (e.g. a seeded synthetic fixture plus a fetched demo series). A caller
        that needs to explain a total row count must see all of them, not just
        ``get_metadata``'s first row.
        """
        return (
            self.db.query(MarketDataMetadata)
            .filter(MarketDataMetadata.instrument_id == instrument_id)
            .order_by(MarketDataMetadata.provider_id)
            .all()
        )

    def get_nature_map(
        self,
        instrument_ids: List[str],
    ) -> Dict[str, Dict[str, str]]:
        """Return {instrument_id: {provider_id: data_nature}} from metadata.

        Consumers of stored bars (backtest, ML training) use this to surface
        the provenance of the bars they actually loaded — synthetic-sourced
        bars must never be presented as real market data.
        """
        if not instrument_ids:
            return {}
        rows = (
            self.db.query(MarketDataMetadata)
            .filter(MarketDataMetadata.instrument_id.in_(instrument_ids))
            .all()
        )
        nature_map: Dict[str, Dict[str, str]] = {}
        for m in rows:
            nature_map.setdefault(m.instrument_id, {})[m.provider_id] = m.data_nature
        return nature_map

    @staticmethod
    def resolve_nature(
        nature_map: Dict[str, Dict[str, str]],
        instrument_id: str,
        provider_ids: set,
    ) -> str:
        """Resolve one nature string for the bars actually loaded.

        Single provider → its data_nature; several providers with differing
        natures → "mixed"; no metadata found → "unknown".
        """
        per_provider = nature_map.get(instrument_id, {})
        natures = {per_provider[p] for p in provider_ids if p in per_provider}
        if len(natures) == 1:
            return natures.pop()
        if len(natures) > 1:
            return "mixed"
        return "unknown"

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
            or_(
                MarketDataMetadata.last_success_at == None,  # noqa: E711
                MarketDataMetadata.last_success_at < cutoff,
            )
        )

        if provider_id:
            query = query.filter(MarketDataMetadata.provider_id == provider_id)

        return query.all()
