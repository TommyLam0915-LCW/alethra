"""Crypto paper trading on the shared paper ledger.

The lane reuses the same bookkeeping every other paper order uses:
``PaperOrder``/``PaperFill``/``DecisionRecord`` and ``paper_execution.book_fill``.
It does **not** re-implement cash or position arithmetic, and it does not touch
the DEMO-fixture pricing path in ``api/paper_portfolio.py`` — a crypto order is
priced from an explicitly identified, completed venue bar.

What is different, and why this module exists:

* **Quote contract.** A crypto fill must name the exact completed bar it used:
  venue, pair, interval, bar open/close time, price source, data nature and the
  fingerprint of the completed series. ``market_data_bars`` has no interval
  identity, so the lane keeps its own ``crypto_quote_bars`` rows.
* **Decimal money and quantity.** Prices are quantised to the instrument's
  declared precision, quantities to its quantity precision, and every amount is
  computed with ``Decimal``. The lane's exact figures are stored on
  ``CryptoPaperExecution`` next to the figures the shared Float ledger actually
  booked, so the difference stays visible instead of being silently rounded
  away.
* **Refusals are explicit.** Missing, forming, stale, incompatible or
  unlabelled quotes are refused before anything is booked. A refusal code is
  stable and machine-readable.

No function here fetches a price, sends an order to a venue, reads a private
account or needs a trading key. The caller supplies a database session and owns
the commit/rollback boundary.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.data.crypto_instruments import get_crypto_instrument, get_kraken_pair
from app.lib.timeutil import utcnow
from app.models.crypto_paper import CryptoPaperExecution, CryptoQuoteBar
from app.models.paper_portfolio import (
    ConfidenceLevel,
    DecisionRecord,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperDecisionType,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PortfolioStatus,
    RiskCheckStatus,
    decision_actor_type,
)
from app.services import emit_event
from app.services.crypto_series import Candle
from app.services.paper_execution import PaperExecutionError, book_fill

DEFAULT_PROVIDER_ID = "kraken"
# A one-minute bar that is older than its own interval plus a minute is not a
# quote any more. The API passes the same number it advertises in the UI.
DEFAULT_QUOTE_MAX_AGE_SECONDS = 300
# The shared ledger rounds money to four places (app.engine.fill_math). The
# lane's own arithmetic keeps more places; both are stored.
LEDGER_MONEY_PLACES = 4
EXACT_MONEY_PLACES = 8
DEFAULT_COMMISSION_RATE = Decimal("0.001")
DEFAULT_SLIPPAGE_RATE = Decimal("0.001")

EXECUTION_ASSUMPTION = (
    "Fills at the completed bar close; slippage and commission are simulated "
    "assumptions, not the user's Kraken fee schedule."
)
LEDGER_MIRROR_LABEL = "paper_portfolios/paper_fills float 4dp"
SUPPORTED_DATA_NATURES = ("live", "fixture")

_QUANT = {
    places: Decimal(1).scaleb(-places) for places in range(0, 19)
}


class CryptoPaperError(Exception):
    """A crypto paper action that must not be booked."""

    def __init__(self, message: str, code: str = "crypto_paper_refused"):
        super().__init__(message)
        self.message = message
        self.code = code


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperError(
            f"Quantity is not a decimal number: {value!r}", code="invalid_quantity"
        ) from exc


def _quantise(value: Decimal, places: int) -> Decimal:
    return value.quantize(_QUANT[places], rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return _quantise(value, EXACT_MONEY_PLACES)


def _stored(value, places: int) -> Decimal:
    """Re-quantise a value read back from a NUMERIC column.

    PostgreSQL returns exact ``Decimal`` from ``NUMERIC``; SQLite renders the
    same column through a float, so a reloaded 8-place amount can come back as
    ``105165.100000000006``. Rounding to the column's declared scale on read
    keeps receipts, retries and CSV output identical on both backends.
    """
    return _quantise(_as_decimal(value), places)


def _decimal_str(value) -> Optional[str]:
    if value is None:
        return None
    return format(_as_decimal(value), "f")


def _stored_str(value, places: int) -> Optional[str]:
    if value is None:
        return None
    return format(_stored(value, places), "f")


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def quote_fingerprint(
    *,
    instrument_id: str,
    provider_id: str,
    interval_minutes: int,
    candles: Iterable[Candle],
) -> str:
    """Stable identity for the exact completed bars a quote was built from."""
    parts = [f"{instrument_id}|{provider_id}|{interval_minutes}"]
    for candle in sorted(candles, key=lambda item: item.open_time):
        parts.append(
            "|".join(
                [
                    candle.open_time.isoformat(),
                    _decimal_str(candle.open),
                    _decimal_str(candle.high),
                    _decimal_str(candle.low),
                    _decimal_str(candle.close),
                    _decimal_str(candle.volume),
                ]
            )
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CryptoPaperQuote:
    quote_bar_id: object
    instrument_id: str
    provider_id: str
    venue: str
    base_asset: str
    quote_asset: str
    side: str
    quantity: Decimal
    quantity_precision: int
    price_precision: int
    observed_price: Decimal
    execution_price: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    slippage_amount: Decimal
    net_cash_change: Decimal
    commission_rate: Decimal
    slippage_rate: Decimal
    min_quantity: Optional[Decimal]
    min_notional: Optional[Decimal]
    price_source: str
    data_nature: str
    data_as_of: datetime
    bar_open_time: datetime
    interval_minutes: int
    snapshot_fingerprint: str
    execution_assumption: str
    cost_model: str

    @property
    def label(self) -> str:
        if self.data_nature == "fixture":
            return "Synthetic quote fixture — not live venue data"
        return "Completed venue bar"

    def as_dict(self) -> Dict[str, object]:
        return {
            "quote_bar_id": str(self.quote_bar_id),
            "instrument_id": self.instrument_id,
            "provider_id": self.provider_id,
            "venue": self.venue,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "side": self.side,
            "quantity": _decimal_str(self.quantity),
            "quantity_precision": self.quantity_precision,
            "price_precision": self.price_precision,
            "observed_price": _decimal_str(self.observed_price),
            "execution_price": _decimal_str(self.execution_price),
            "gross_amount": _decimal_str(self.gross_amount),
            "commission_amount": _decimal_str(self.commission_amount),
            "slippage_amount": _decimal_str(self.slippage_amount),
            "net_cash_change": _decimal_str(self.net_cash_change),
            "commission_rate": _decimal_str(self.commission_rate),
            "slippage_rate": _decimal_str(self.slippage_rate),
            "min_quantity": _decimal_str(self.min_quantity),
            "min_notional": _decimal_str(self.min_notional),
            "price_source": self.price_source,
            "data_nature": self.data_nature,
            "data_as_of": self.data_as_of.isoformat(),
            "bar_open_time": self.bar_open_time.isoformat(),
            "interval_minutes": self.interval_minutes,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "execution_assumption": self.execution_assumption,
            "cost_model": self.cost_model,
            "label": self.label,
            "simulated": True,
        }


@dataclass
class CryptoPaperOrderResult:
    order: PaperOrder
    fill: PaperFill
    receipt: CryptoPaperExecution
    quote: CryptoPaperQuote
    created: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "status": "filled",
            "created": self.created,
            "portfolio_id": str(self.receipt.portfolio_id),
            "order_id": str(self.order.id),
            "fill_id": str(self.fill.id),
            "receipt_id": str(self.receipt.id),
            "decision_id": str(self.order.decision_id) if self.order.decision_id else None,
            "venue": self.receipt.venue,
            "instrument_id": self.receipt.instrument_id,
            "base_asset": self.receipt.base_asset,
            "quote_asset": self.receipt.quote_asset,
            "currency": self.receipt.currency,
            "interval_minutes": self.receipt.interval_minutes,
            "side": self.receipt.side,
            "quantity": _stored_str(self.receipt.quantity, self.quote.quantity_precision),
            "observed_price": _stored_str(self.receipt.observed_price, self.quote.price_precision),
            "execution_price": _stored_str(
                self.receipt.execution_price, self.quote.price_precision
            ),
            "gross_amount": _stored_str(self.receipt.gross_amount, EXACT_MONEY_PLACES),
            "commission_amount": _stored_str(
                self.receipt.commission_amount, EXACT_MONEY_PLACES
            ),
            "slippage_amount": _stored_str(self.receipt.slippage_amount, EXACT_MONEY_PLACES),
            "net_cash_change": _stored_str(self.receipt.net_cash_change, EXACT_MONEY_PLACES),
            "ledger": {
                "gross_amount": _stored_str(
                    self.receipt.ledger_gross_amount, LEDGER_MONEY_PLACES
                ),
                "commission_amount": _stored_str(
                    self.receipt.ledger_commission_amount, LEDGER_MONEY_PLACES
                ),
                "slippage_amount": _stored_str(
                    self.receipt.ledger_slippage_amount, LEDGER_MONEY_PLACES
                ),
                "net_cash_change": _stored_str(
                    self.receipt.ledger_net_cash_change, LEDGER_MONEY_PLACES
                ),
                "mirror": self.receipt.ledger_mirror,
            },
            "commission_rate": _stored_str(self.receipt.commission_rate, 10),
            "slippage_rate": _stored_str(self.receipt.slippage_rate, 10),
            "price_source": self.receipt.price_source,
            "data_nature": self.receipt.data_nature,
            "data_as_of": self.receipt.data_as_of.isoformat(),
            "bar_open_time": self.receipt.bar_open_time.isoformat(),
            "snapshot_fingerprint": self.receipt.snapshot_fingerprint,
            "execution_assumption": self.receipt.execution_assumption,
            "cost_model": self.receipt.cost_model,
            "order_status": self.order.status.value,
            "label": (
                "Synthetic quote fixture — not live venue data"
                if self.receipt.data_nature == "fixture"
                else "Simulated fill from a completed venue bar"
            ),
            "simulated": True,
        }


def record_quote_bars(
    db: Session,
    *,
    instrument_id: str,
    provider_id: str,
    interval_minutes: int,
    candles: Sequence[Candle],
    price_source: str,
    data_nature: str = "live",
    fetched_at: Optional[datetime] = None,
) -> Dict[str, object]:
    """Upsert completed candles into the crypto quote contract.

    Forming candles are skipped, never stored: a paper fill must not use a
    value that can still change. The fingerprint always covers the completed
    rows in this call, in open-time order.
    """
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise CryptoPaperError(
            f"Unknown crypto instrument {instrument_id!r}", code="incompatible_instrument"
        )
    if provider_id != DEFAULT_PROVIDER_ID or get_kraken_pair(instrument_id) is None:
        raise CryptoPaperError(
            f"No {provider_id!r} symbol mapping for {instrument_id!r}",
            code="incompatible_provider",
        )
    if interval_minutes <= 0:
        raise CryptoPaperError(
            "interval_minutes must be positive", code="incompatible_quote"
        )
    if data_nature not in SUPPORTED_DATA_NATURES:
        raise CryptoPaperError(
            f"Unlabelled quote source {data_nature!r}",
            code="unverified_quote",
        )

    accepted: Dict[datetime, Candle] = {}
    skipped_forming = 0
    mismatched = 0
    for candle in candles:
        if candle.instrument_id != instrument_id or candle.interval_minutes != interval_minutes:
            mismatched += 1
            continue
        open_time = _naive_utc(candle.open_time)
        if not candle.is_final:
            skipped_forming += 1
            continue
        accepted[open_time] = candle

    fetched = _naive_utc(fetched_at) if fetched_at is not None else utcnow()
    fingerprint = quote_fingerprint(
        instrument_id=instrument_id,
        provider_id=provider_id,
        interval_minutes=interval_minutes,
        candles=accepted.values(),
    )

    stored = 0
    updated = 0
    for open_time in sorted(accepted):
        candle = accepted[open_time]
        row = (
            db.query(CryptoQuoteBar)
            .filter(
                and_(
                    CryptoQuoteBar.instrument_id == instrument_id,
                    CryptoQuoteBar.provider_id == provider_id,
                    CryptoQuoteBar.interval_minutes == interval_minutes,
                    CryptoQuoteBar.open_time == open_time,
                )
            )
            .first()
        )
        values = dict(
            close_time=open_time + timedelta(minutes=interval_minutes),
            open=_quantise(_as_decimal(candle.open), 12),
            high=_quantise(_as_decimal(candle.high), 12),
            low=_quantise(_as_decimal(candle.low), 12),
            close=_quantise(_as_decimal(candle.close), 12),
            volume=(
                _quantise(_as_decimal(candle.volume), 12)
                if candle.volume is not None
                else None
            ),
            is_final=True,
            data_nature=data_nature,
            price_source=price_source,
            fingerprint=fingerprint,
            fetched_at=fetched,
        )
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)
            updated += 1
        else:
            db.add(
                CryptoQuoteBar(
                    instrument_id=instrument_id,
                    provider_id=provider_id,
                    interval_minutes=interval_minutes,
                    open_time=open_time,
                    **values,
                )
            )
            stored += 1

    db.flush()
    return {
        "stored": stored,
        "updated": updated,
        "skipped_forming": skipped_forming,
        "skipped_incompatible": mismatched,
        "fingerprint": fingerprint,
        "data_nature": data_nature,
    }


def latest_quote_bar(
    db: Session,
    *,
    instrument_id: str,
    provider_id: str = DEFAULT_PROVIDER_ID,
    interval_minutes: int,
) -> Optional[CryptoQuoteBar]:
    return (
        db.query(CryptoQuoteBar)
        .filter(
            and_(
                CryptoQuoteBar.instrument_id == instrument_id,
                CryptoQuoteBar.provider_id == provider_id,
                CryptoQuoteBar.interval_minutes == interval_minutes,
                CryptoQuoteBar.is_final.is_(True),
            )
        )
        .order_by(CryptoQuoteBar.open_time.desc())
        .first()
    )


def _validate_quantity(instrument, raw) -> Decimal:
    quantity = _as_decimal(raw)
    if not quantity.is_finite() or quantity <= 0:
        raise CryptoPaperError(
            "Quantity must be a finite number greater than zero",
            code="invalid_quantity",
        )
    precision = instrument.quantity_precision
    if precision is None:
        raise CryptoPaperError(
            f"{instrument.instrument_id} has no declared quantity precision",
            code="incompatible_instrument",
        )
    quantised = _quantise(quantity, precision)
    if quantised != quantity:
        raise CryptoPaperError(
            f"Quantity {quantity} has more than {precision} decimal places accepted by "
            f"{instrument.instrument_id}",
            code="quantity_precision_exceeded",
        )
    if instrument.min_quantity is not None and quantised < instrument.min_quantity:
        raise CryptoPaperError(
            f"Quantity {quantised} is below the venue minimum {instrument.min_quantity}",
            code="below_min_quantity",
        )
    return quantised


def _execution_price(observed: Decimal, side: str, slippage_rate: Decimal, places: int) -> Decimal:
    if side == OrderSide.BUY.value:
        price = observed * (Decimal(1) + slippage_rate)
    elif side == OrderSide.SELL.value:
        price = observed * (Decimal(1) - slippage_rate)
    else:
        raise CryptoPaperError(f"Unsupported side {side!r}", code="invalid_side")
    price = _quantise(price, places)
    if price <= 0:
        raise CryptoPaperError(
            "Slippage assumption produced a non-positive execution price",
            code="invalid_price",
        )
    return price


def _economics(
    side: str,
    quantity: Decimal,
    observed: Decimal,
    execution: Decimal,
    commission_rate: Decimal,
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    gross = _money(quantity * execution)
    commission = _money(gross * commission_rate)
    slippage = _money(abs(execution - observed) * quantity)
    if side == OrderSide.BUY.value:
        net = _money(-(gross + commission))
    else:
        net = _money(gross - commission)
    return gross, commission, slippage, net


def build_quote(
    db: Session,
    *,
    instrument_id: str,
    interval_minutes: int,
    side: str,
    quantity,
    provider_id: str = DEFAULT_PROVIDER_ID,
    now: Optional[datetime] = None,
    max_age_seconds: int = DEFAULT_QUOTE_MAX_AGE_SECONDS,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
    slippage_rate: Decimal = DEFAULT_SLIPPAGE_RATE,
) -> CryptoPaperQuote:
    """Price a manual paper order from the latest completed quote bar.

    Every refusal happens before the caller books anything.
    """
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise CryptoPaperError(
            f"Unknown crypto instrument {instrument_id!r}", code="incompatible_instrument"
        )
    if provider_id != DEFAULT_PROVIDER_ID or get_kraken_pair(instrument_id) is None:
        raise CryptoPaperError(
            f"No {provider_id!r} symbol mapping for {instrument_id!r}",
            code="incompatible_provider",
        )
    if interval_minutes <= 0:
        raise CryptoPaperError(
            "interval_minutes must be positive", code="incompatible_quote"
        )
    side_value = str(side).upper()
    if side_value not in (OrderSide.BUY.value, OrderSide.SELL.value):
        raise CryptoPaperError(f"Unsupported side {side!r}", code="invalid_side")

    amount = _validate_quantity(instrument, quantity)

    bar = latest_quote_bar(
        db,
        instrument_id=instrument_id,
        provider_id=provider_id,
        interval_minutes=interval_minutes,
    )
    if bar is None:
        raise CryptoPaperError(
            "No completed quote bar is stored for this pair and interval. Nothing is "
            "priced from a forming bar or a substitute series.",
            code="missing_quote",
        )
    if bar.interval_minutes != interval_minutes:
        raise CryptoPaperError(
            "Stored quote bar interval does not match the requested interval",
            code="incompatible_quote",
        )
    if not bar.is_final:
        raise CryptoPaperError(
            "The stored quote bar is not final", code="forming_quote"
        )
    if bar.data_nature not in SUPPORTED_DATA_NATURES:
        raise CryptoPaperError(
            f"Quote bar is labelled {bar.data_nature!r}, not a usable source",
            code="unverified_quote",
        )

    data_as_of = _naive_utc(bar.close_time)
    clock = _naive_utc(now) if now is not None else utcnow()
    age_seconds = int((clock - data_as_of).total_seconds())
    if age_seconds > max_age_seconds:
        raise CryptoPaperError(
            f"Latest completed {interval_minutes}m bar closed {age_seconds}s ago, older than "
            f"the {max_age_seconds}s quote window. A stale quote is refused, not priced.",
            code="stale_quote",
        )

    price_places = instrument.price_precision
    if price_places is None:
        raise CryptoPaperError(
            f"{instrument_id} has no declared price precision",
            code="incompatible_instrument",
        )
    observed = _quantise(_as_decimal(bar.close), price_places)
    execution = _execution_price(observed, side_value, slippage_rate, price_places)
    gross, commission, slippage, net = _economics(
        side_value, amount, observed, execution, commission_rate
    )

    if instrument.min_notional is not None and gross < instrument.min_notional:
        raise CryptoPaperError(
            f"Order notional {gross} {instrument.quote_asset} is below the venue minimum "
            f"{instrument.min_notional}",
            code="below_min_notional",
        )

    cost_model = (
        f"Simulated: commission {(_decimal_str(commission_rate))} of gross and "
        f"slippage {(_decimal_str(slippage_rate))} applied against the trader. "
        "Not the user's Kraken fee schedule."
    )

    return CryptoPaperQuote(
        quote_bar_id=bar.id,
        instrument_id=instrument_id,
        provider_id=provider_id,
        venue=instrument.venue or instrument.exchange.value,
        base_asset=instrument.base_asset or "",
        quote_asset=instrument.quote_asset,
        side=side_value,
        quantity=amount,
        quantity_precision=instrument.quantity_precision,
        price_precision=price_places,
        observed_price=observed,
        execution_price=execution,
        gross_amount=gross,
        commission_amount=commission,
        slippage_amount=slippage,
        net_cash_change=net,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
        min_quantity=instrument.min_quantity,
        min_notional=instrument.min_notional,
        price_source=bar.price_source,
        data_nature=bar.data_nature,
        data_as_of=data_as_of,
        bar_open_time=_naive_utc(bar.open_time),
        interval_minutes=interval_minutes,
        snapshot_fingerprint=bar.fingerprint,
        execution_assumption=EXECUTION_ASSUMPTION,
        cost_model=cost_model,
    )


def _matching_order(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    idempotency_key: str,
    instrument_id: str,
    side: str,
    quantity: Decimal,
) -> Optional[PaperOrder]:
    existing = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.portfolio_id == portfolio.id,
            PaperOrder.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing is None:
        return None
    same = (
        existing.asset_id == instrument_id
        and existing.side.value == side
        and _as_decimal(existing.quantity) == quantity
    )
    if not same:
        raise CryptoPaperError(
            "idempotency_key was already used with a different order",
            code="idempotency_conflict",
        )
    return existing


def _quote_from_receipt(receipt: CryptoPaperExecution) -> CryptoPaperQuote:
    """The quote a retried request already priced against.

    A retry must return the original fill, not re-price it. Re-reading the
    newest bar would let a later (or stale) quote change the answer to an
    identical request, so the receipt is the source here.
    """
    instrument = get_crypto_instrument(receipt.instrument_id)
    if instrument is None:
        raise CryptoPaperError(
            f"Receipt names unknown instrument {receipt.instrument_id!r}",
            code="incompatible_instrument",
        )
    return CryptoPaperQuote(
        quote_bar_id=receipt.quote_bar_id,
        instrument_id=receipt.instrument_id,
        provider_id=DEFAULT_PROVIDER_ID,
        venue=receipt.venue,
        base_asset=receipt.base_asset,
        quote_asset=receipt.quote_asset,
        side=receipt.side,
        quantity=_stored(receipt.quantity, instrument.quantity_precision),
        quantity_precision=instrument.quantity_precision,
        price_precision=instrument.price_precision,
        observed_price=_stored(receipt.observed_price, instrument.price_precision),
        execution_price=_stored(receipt.execution_price, instrument.price_precision),
        gross_amount=_stored(receipt.gross_amount, EXACT_MONEY_PLACES),
        commission_amount=_stored(receipt.commission_amount, EXACT_MONEY_PLACES),
        slippage_amount=_stored(receipt.slippage_amount, EXACT_MONEY_PLACES),
        net_cash_change=_stored(receipt.net_cash_change, EXACT_MONEY_PLACES),
        commission_rate=_stored(receipt.commission_rate, 10),
        slippage_rate=_stored(receipt.slippage_rate, 10),
        min_quantity=instrument.min_quantity,
        min_notional=instrument.min_notional,
        price_source=receipt.price_source,
        data_nature=receipt.data_nature,
        data_as_of=_naive_utc(receipt.data_as_of),
        bar_open_time=_naive_utc(receipt.bar_open_time),
        interval_minutes=receipt.interval_minutes,
        snapshot_fingerprint=receipt.snapshot_fingerprint,
        execution_assumption=receipt.execution_assumption,
        cost_model=receipt.cost_model,
    )


def _existing_result(db: Session, order: PaperOrder) -> CryptoPaperOrderResult:
    fill = db.query(PaperFill).filter(PaperFill.paper_order_id == order.id).first()
    receipt = (
        db.query(CryptoPaperExecution)
        .filter(CryptoPaperExecution.paper_order_id == order.id)
        .first()
    )
    if fill is None or receipt is None:
        raise CryptoPaperError(
            "An order with this idempotency_key exists but has no complete fill record",
            code="idempotency_conflict",
        )
    return CryptoPaperOrderResult(
        order=order,
        fill=fill,
        receipt=receipt,
        quote=_quote_from_receipt(receipt),
        created=False,
    )


def submit_manual_order(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    instrument_id: str,
    interval_minutes: int,
    side: str,
    quantity,
    actor_id,
    idempotency_key: Optional[str] = None,
    rationale: Optional[str] = None,
    expected_outcome: Optional[str] = None,
    expected_timeframe_days: Optional[int] = None,
    self_reported_confidence: str = ConfidenceLevel.not_provided.value,
    falsification_condition: Optional[str] = None,
    actor_type: str = "unknown",
    provider_id: str = DEFAULT_PROVIDER_ID,
    now: Optional[datetime] = None,
    max_age_seconds: int = DEFAULT_QUOTE_MAX_AGE_SECONDS,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
    slippage_rate: Decimal = DEFAULT_SLIPPAGE_RATE,
) -> CryptoPaperOrderResult:
    """Book one simulated crypto order on the shared paper ledger.

    The caller owns commit/rollback. ``book_fill`` is the only place that moves
    cash and positions; this function adds the exact receipt and the journal
    event around it. If ``book_fill`` refuses (insufficient cash or position),
    the exception propagates as ``CryptoPaperError`` and the caller must roll
    back — nothing half-applied may be committed.
    """
    if portfolio.status != PortfolioStatus.active:
        raise CryptoPaperError(
            f"Cannot trade in a '{portfolio.status.value}' portfolio",
            code="portfolio_not_active",
        )
    instrument = get_crypto_instrument(instrument_id)
    if instrument is None:
        raise CryptoPaperError(
            f"Unknown crypto instrument {instrument_id!r}", code="incompatible_instrument"
        )
    if (portfolio.currency or "").upper() != instrument.quote_asset.upper():
        raise CryptoPaperError(
            f"Portfolio currency {portfolio.currency!r} does not match the quote asset "
            f"{instrument.quote_asset!r}",
            code="incompatible_portfolio",
        )

    side_value = str(side).upper()
    if side_value not in (OrderSide.BUY.value, OrderSide.SELL.value):
        raise CryptoPaperError(f"Unsupported side {side!r}", code="invalid_side")
    amount = _validate_quantity(instrument, quantity)

    # Idempotency is decided before the quote is re-read: an identical retry
    # must return the fill that was already booked even if the quote has since
    # gone stale. Re-pricing a retry would also make the same key book twice.
    if idempotency_key:
        existing = _matching_order(
            db,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            instrument_id=instrument_id,
            side=side_value,
            quantity=amount,
        )
        if existing is not None:
            return _existing_result(db, existing)

    quote = build_quote(
        db,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        side=side_value,
        quantity=amount,
        provider_id=provider_id,
        now=now,
        max_age_seconds=max_age_seconds,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )

    if idempotency_key:
        existing = _matching_order(
            db,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            instrument_id=instrument_id,
            side=quote.side,
            quantity=quote.quantity,
        )
        if existing is not None:
            return _existing_result(db, existing)

    # A position added by an earlier order in this same session is not attached
    # to the loaded relationship; reload so book_fill sees the real holding.
    db.flush()
    db.refresh(portfolio)

    decision = DecisionRecord(
        owner_id=actor_id,
        paper_portfolio_id=portfolio.id,
        actor_type=decision_actor_type(actor_type),
        decision_type=(
            PaperDecisionType.entry
            if quote.side == OrderSide.BUY.value
            else PaperDecisionType.exit
        ),
        rationale=rationale,
        expected_outcome=expected_outcome,
        expected_timeframe_days=expected_timeframe_days,
        self_reported_confidence=ConfidenceLevel(
            self_reported_confidence or ConfidenceLevel.not_provided.value
        ),
        falsification_condition=falsification_condition,
    )
    db.add(decision)
    db.flush()

    order = PaperOrder(
        portfolio_id=portfolio.id,
        decision_id=decision.id,
        asset_id=instrument_id,
        side=OrderSide(quote.side),
        quantity=float(quote.quantity),
        order_type=OrderType.market,
        status=OrderStatus.pending,
        rationale=rationale,
        risk_check_status=RiskCheckStatus.passed,
        idempotency_key=idempotency_key,
        actor_type=actor_type,
        data_cutoff_at=quote.data_as_of.date().isoformat(),
    )
    db.add(order)
    db.flush()

    try:
        fill = book_fill(
            db,
            order=order,
            portfolio=portfolio,
            execution_date=quote.data_as_of.date().isoformat(),
            observed_price=float(quote.observed_price),
            execution_price=float(quote.execution_price),
            quantity=float(quote.quantity),
            commission_rate=float(quote.commission_rate),
            fill_reason="crypto_paper_manual_completed_bar",
            bar_timestamp=quote.bar_open_time,
        )
    except PaperExecutionError as exc:
        raise CryptoPaperError(exc.message, code=exc.code) from exc

    db.flush()

    receipt = CryptoPaperExecution(
        portfolio_id=portfolio.id,
        paper_order_id=order.id,
        paper_fill_id=fill.id,
        quote_bar_id=quote.quote_bar_id,
        venue=quote.venue,
        instrument_id=instrument_id,
        base_asset=quote.base_asset,
        quote_asset=quote.quote_asset,
        currency=portfolio.currency,
        interval_minutes=quote.interval_minutes,
        side=quote.side,
        quantity=quote.quantity,
        observed_price=quote.observed_price,
        execution_price=quote.execution_price,
        gross_amount=quote.gross_amount,
        commission_amount=quote.commission_amount,
        slippage_amount=quote.slippage_amount,
        net_cash_change=quote.net_cash_change,
        ledger_gross_amount=_as_decimal(fill.gross_amount),
        ledger_commission_amount=_as_decimal(fill.commission_amount),
        ledger_slippage_amount=_as_decimal(fill.slippage_amount),
        ledger_net_cash_change=_as_decimal(fill.net_cash_change),
        commission_rate=quote.commission_rate,
        slippage_rate=quote.slippage_rate,
        price_source=quote.price_source,
        data_nature=quote.data_nature,
        data_as_of=quote.data_as_of,
        bar_open_time=quote.bar_open_time,
        snapshot_fingerprint=quote.snapshot_fingerprint,
        execution_assumption=quote.execution_assumption,
        cost_model=quote.cost_model,
        ledger_mirror=LEDGER_MIRROR_LABEL,
        actor_type=actor_type,
    )
    db.add(receipt)
    db.flush()

    emit_event(
        db,
        entity_type="paper_portfolio",
        entity_id=str(portfolio.id),
        event_type="order_created",
        actor_id=actor_id,
        actor_type=actor_type,
        data_cutoff_at=order.data_cutoff_at,
        payload={
            "lane": "crypto",
            "venue": quote.venue,
            "instrument_id": instrument_id,
            "order_id": str(order.id),
            "fill_id": str(fill.id),
            "receipt_id": str(receipt.id),
            "decision_id": str(decision.id),
            "side": quote.side,
            "quantity": _decimal_str(quote.quantity),
            "execution_price": _decimal_str(quote.execution_price),
            "price_source": quote.price_source,
            "data_nature": quote.data_nature,
            "data_as_of": quote.data_as_of.isoformat(),
            "snapshot_fingerprint": quote.snapshot_fingerprint,
            "idempotency_key": idempotency_key,
            "actor_type": actor_type,
            "simulated": True,
        },
    )

    return CryptoPaperOrderResult(
        order=order, fill=fill, receipt=receipt, quote=quote, created=True
    )


def receipts_for_portfolio(
    db: Session, portfolio_id
) -> List[CryptoPaperExecution]:
    return (
        db.query(CryptoPaperExecution)
        .filter(CryptoPaperExecution.portfolio_id == portfolio_id)
        .order_by(CryptoPaperExecution.created_at)
        .all()
    )


RECEIPT_COLUMNS = [
    "receipt_id",
    "created_at",
    "portfolio_id",
    "order_id",
    "fill_id",
    "decision_id",
    "order_status",
    "venue",
    "instrument_id",
    "base_asset",
    "quote_asset",
    "currency",
    "interval_minutes",
    "side",
    "quantity",
    "observed_price",
    "execution_price",
    "gross_amount",
    "commission_amount",
    "slippage_amount",
    "net_cash_change",
    "ledger_gross_amount",
    "ledger_commission_amount",
    "ledger_slippage_amount",
    "ledger_net_cash_change",
    "ledger_mirror",
    "commission_rate",
    "slippage_rate",
    "price_source",
    "data_nature",
    "data_as_of",
    "bar_open_time",
    "snapshot_fingerprint",
    "execution_assumption",
    "cost_model",
]


def receipt_as_dict(receipt: CryptoPaperExecution) -> Dict[str, object]:
    order = receipt.order
    instrument = get_crypto_instrument(receipt.instrument_id)
    price_places = instrument.price_precision if instrument and instrument.price_precision is not None else EXACT_MONEY_PLACES
    quantity_places = (
        instrument.quantity_precision
        if instrument and instrument.quantity_precision is not None
        else EXACT_MONEY_PLACES
    )
    return {
        "receipt_id": str(receipt.id),
        "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
        "portfolio_id": str(receipt.portfolio_id),
        "order_id": str(receipt.paper_order_id),
        "fill_id": str(receipt.paper_fill_id),
        "decision_id": str(order.decision_id) if order and order.decision_id else None,
        "order_status": order.status.value if order else None,
        "venue": receipt.venue,
        "instrument_id": receipt.instrument_id,
        "base_asset": receipt.base_asset,
        "quote_asset": receipt.quote_asset,
        "currency": receipt.currency,
        "interval_minutes": receipt.interval_minutes,
        "side": receipt.side,
        "quantity": _stored_str(receipt.quantity, quantity_places),
        "observed_price": _stored_str(receipt.observed_price, price_places),
        "execution_price": _stored_str(receipt.execution_price, price_places),
        "gross_amount": _stored_str(receipt.gross_amount, EXACT_MONEY_PLACES),
        "commission_amount": _stored_str(receipt.commission_amount, EXACT_MONEY_PLACES),
        "slippage_amount": _stored_str(receipt.slippage_amount, EXACT_MONEY_PLACES),
        "net_cash_change": _stored_str(receipt.net_cash_change, EXACT_MONEY_PLACES),
        "ledger_gross_amount": _stored_str(receipt.ledger_gross_amount, LEDGER_MONEY_PLACES),
        "ledger_commission_amount": _stored_str(
            receipt.ledger_commission_amount, LEDGER_MONEY_PLACES
        ),
        "ledger_slippage_amount": _stored_str(
            receipt.ledger_slippage_amount, LEDGER_MONEY_PLACES
        ),
        "ledger_net_cash_change": _stored_str(
            receipt.ledger_net_cash_change, LEDGER_MONEY_PLACES
        ),
        "ledger_mirror": receipt.ledger_mirror,
        "commission_rate": _stored_str(receipt.commission_rate, 10),
        "slippage_rate": _stored_str(receipt.slippage_rate, 10),
        "price_source": receipt.price_source,
        "data_nature": receipt.data_nature,
        "data_as_of": receipt.data_as_of.isoformat(),
        "bar_open_time": receipt.bar_open_time.isoformat(),
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "execution_assumption": receipt.execution_assumption,
        "cost_model": receipt.cost_model,
        "label": (
            "Synthetic quote fixture — not live venue data"
            if receipt.data_nature == "fixture"
            else "Simulated fill from a completed venue bar"
        ),
        "simulated": True,
    }


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return format(_as_decimal(value), "f")
    text = str(value)
    # Spreadsheet formula injection: an external string must not start a formula.
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def receipts_csv(receipts: Sequence[CryptoPaperExecution]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(RECEIPT_COLUMNS)
    for receipt in receipts:
        row = receipt_as_dict(receipt)
        writer.writerow([_csv_cell(row.get(column)) for column in RECEIPT_COLUMNS])
    return buf.getvalue()
