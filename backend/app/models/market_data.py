import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, DateTime, Date, Float, Integer, BigInteger, Numeric,
    Boolean, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.lib.timeutil import utcnow


class MarketDataBar(Base):
    """OHLCV bar for a specific instrument and timestamp.

    Stores market data fetched from providers (demo, LSEG, etc.) to avoid
    repeated API calls and enable historical analysis. Each bar represents
    one time period (daily, hourly, etc.) for one instrument.

    Data quality metadata is stored at the instrument level in MarketDataMetadata.
    """
    __tablename__ = "market_data_bars"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(String(50), nullable=False, index=True)
    provider_id = Column(String(50), nullable=False)
    bar_date = Column(Date, nullable=False)
    bar_timestamp = Column(DateTime, nullable=False)

    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    # Equity bars carry whole shares in ``volume``. Crypto trades fractions of a
    # coin, so those rows leave ``volume`` NULL and put the exact amount in
    # ``volume_decimal``; the two are never mixed in one row.
    volume = Column(BigInteger, nullable=True)
    volume_decimal = Column(Numeric(30, 12), nullable=True)

    adjustment_factor = Column(Float, nullable=True)
    is_adjusted = Column(String(20), nullable=True)

    fetched_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('instrument_id', 'bar_timestamp', 'provider_id', name='uq_instrument_timestamp_provider'),
        Index('ix_market_data_instrument_date', 'instrument_id', 'bar_date'),
    )

    data_metadata = relationship(
        "MarketDataMetadata",
        primaryjoin="and_(MarketDataBar.instrument_id==foreign(MarketDataMetadata.instrument_id), MarketDataBar.provider_id==foreign(MarketDataMetadata.provider_id))",
        uselist=False,
        viewonly=True,
    )


class MarketDataMetadata(Base):
    """Metadata about market data quality and provenance for an instrument.

    Tracks data source, quality indicators, staleness, and adjustment status.
    One metadata record per instrument per provider.
    """
    __tablename__ = "market_data_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(String(50), nullable=False, index=True)
    provider_id = Column(String(50), nullable=False)

    data_nature = Column(String(20), nullable=False)
    adjustment_status = Column(String(50), nullable=True)
    timezone = Column(String(50), nullable=True)
    currency = Column(String(10), nullable=True)

    provider_note = Column(String(500), nullable=True)
    provider_note_zh = Column(String(500), nullable=True)

    first_bar_date = Column(Date, nullable=True)
    last_bar_date = Column(Date, nullable=True)
    total_bars = Column(Integer, default=0)

    last_success_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    consecutive_errors = Column(Integer, default=0)
    health_status = Column(String(20), default="ok")

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    instrument_bars = relationship(
        "MarketDataBar",
        primaryjoin="and_(MarketDataBar.instrument_id==foreign(MarketDataMetadata.instrument_id), MarketDataBar.provider_id==foreign(MarketDataMetadata.provider_id))",
        viewonly=True,
    )

    __table_args__ = (
        UniqueConstraint('instrument_id', 'provider_id', name='uq_instrument_provider_metadata'),
    )


class IntradayMarketDataBar(Base):
    """One sub-daily bar polled from a live provider.

    Kept in its own table on purpose. ``market_data_bars`` backs research
    datasets and locked experiment versions, so a live poll must never be able
    to rewrite what a locked result was computed from. Splitting the tables
    makes that a structural guarantee rather than a promise.

    Identity is the full (instrument, provider, timeframe, timestamp) tuple:
    1-minute and 5-minute series for one symbol cannot overwrite each other,
    two providers cannot collide, and a forming bar updates its own row until
    it closes instead of spawning a duplicate candle.
    """
    __tablename__ = "market_data_intraday_bars"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(String(50), nullable=False, index=True)
    provider_id = Column(String(50), nullable=False)
    timeframe = Column(String(10), nullable=False)
    bar_timestamp = Column(DateTime, nullable=False)
    bar_date = Column(Date, nullable=False)

    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=True)

    # False while the bucket is still open; a closed bar is only ever updated
    # in place, never turned back into a forming bar.
    is_final = Column(Boolean, nullable=False, default=False)

    fetched_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            'instrument_id', 'provider_id', 'timeframe', 'bar_timestamp',
            name='uq_intraday_instrument_provider_timeframe_timestamp',
        ),
        Index(
            'ix_market_data_intraday_instrument_timeframe_ts',
            'instrument_id', 'timeframe', 'bar_timestamp',
        ),
    )
