"""Additive storage for the crypto paper lane.

Two tables, both new; no existing table is altered.

``CryptoQuoteBar`` is the quote contract the paper lane is allowed to price
against. It is separate from ``market_data_bars`` because that table has no
interval identity — a stored row cannot prove whether it is a 1-minute or a
5-minute bar, and a paper fill must name the exact completed bar it used. The
row keeps the venue-provided OHLCV as Decimal, the provider, the interval, the
bar's own open/close time and the series fingerprint that was computed over the
completed bars.

``CryptoPaperExecution`` is the exact receipt for one simulated fill. The shared
paper ledger stores money as Float for historical reasons; this receipt keeps
the same trade at Decimal precision together with the quote it came from, so a
research record can be reconstructed without re-deriving prices. It also states
which ledger row it mirrors, so nobody reads the receipt as proof that the
Float ledger itself is Decimal.

No table here stores a private account, an API key or a venue order id: the
lane never sends an order to Kraken.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.lib.timeutil import utcnow

QUOTE_TABLE = "crypto_quote_bars"
EXECUTION_TABLE = "crypto_paper_executions"


class CryptoQuoteBar(Base):
    __tablename__ = QUOTE_TABLE
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "provider_id",
            "interval_minutes",
            "open_time",
            name="uq_crypto_quote_instrument_provider_interval_open",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id = Column(String(50), nullable=False, index=True)
    provider_id = Column(String(50), nullable=False)
    interval_minutes = Column(Integer, nullable=False)
    # UTC, naive for the same reason the rest of storage is: one clock, one zone.
    open_time = Column(DateTime, nullable=False)
    close_time = Column(DateTime, nullable=False)
    open = Column(Numeric(30, 12), nullable=False)
    high = Column(Numeric(30, 12), nullable=False)
    low = Column(Numeric(30, 12), nullable=False)
    close = Column(Numeric(30, 12), nullable=False)
    volume = Column(Numeric(30, 12), nullable=True)
    is_final = Column(Boolean, nullable=False, default=True)
    # "live" or "fixture". A synthetic row must never be priced as a live quote
    # without its label reaching the receipt.
    data_nature = Column(String(16), nullable=False)
    price_source = Column(String(100), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class CryptoPaperExecution(Base):
    __tablename__ = EXECUTION_TABLE

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False, index=True
    )
    paper_order_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_orders.id"), nullable=False, unique=True
    )
    paper_fill_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_fills.id"), nullable=False, unique=True
    )
    quote_bar_id = Column(
        UUID(as_uuid=True), ForeignKey("crypto_quote_bars.id"), nullable=False
    )

    venue = Column(String(20), nullable=False)
    instrument_id = Column(String(50), nullable=False)
    base_asset = Column(String(16), nullable=False)
    quote_asset = Column(String(8), nullable=False)
    currency = Column(String(3), nullable=False)
    interval_minutes = Column(Integer, nullable=False)
    side = Column(String(8), nullable=False)

    quantity = Column(Numeric(38, 18), nullable=False)
    observed_price = Column(Numeric(30, 12), nullable=False)
    execution_price = Column(Numeric(30, 12), nullable=False)
    # The lane's own Decimal arithmetic. Kept beside the ledger mirror so the
    # difference between "what this trade costs at the lane's precision" and
    # "what the shared Float ledger actually booked" is visible, not averaged.
    gross_amount = Column(Numeric(30, 12), nullable=False)
    commission_amount = Column(Numeric(30, 12), nullable=False)
    slippage_amount = Column(Numeric(30, 12), nullable=False)
    net_cash_change = Column(Numeric(30, 12), nullable=False)
    ledger_gross_amount = Column(Numeric(30, 12), nullable=False)
    ledger_commission_amount = Column(Numeric(30, 12), nullable=False)
    ledger_slippage_amount = Column(Numeric(30, 12), nullable=False)
    ledger_net_cash_change = Column(Numeric(30, 12), nullable=False)
    commission_rate = Column(Numeric(18, 10), nullable=False)
    slippage_rate = Column(Numeric(18, 10), nullable=False)

    price_source = Column(String(100), nullable=False)
    data_nature = Column(String(16), nullable=False)
    data_as_of = Column(DateTime, nullable=False)
    bar_open_time = Column(DateTime, nullable=False)
    snapshot_fingerprint = Column(String(64), nullable=False)
    execution_assumption = Column(String(200), nullable=False)
    cost_model = Column(Text, nullable=False)
    # Which ledger row this receipt mirrors and at what precision. The shared
    # ledger's money columns are Float; saying so on every receipt prevents the
    # receipt from being read as evidence that the ledger became exact.
    ledger_mirror = Column(String(60), nullable=False)
    actor_type = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)

    quote_bar = relationship("CryptoQuoteBar")
    order = relationship("PaperOrder")
