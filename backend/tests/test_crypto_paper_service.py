"""Crypto paper lane: quote contract, refusals and shared-ledger booking.

These exercise ``app.services.crypto_paper`` directly against the isolated test
database. The lane is priced from labelled quote rows only, and every fill goes
through the shared ``book_fill`` path — these tests prove the refusal surface,
the idempotency behaviour and the receipt/ledger identity, not just the happy
path.

No network is used: the fixture module writes deterministic synthetic candles
with ``data_nature="fixture"``.
"""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func

from tests.conftest import TestSession
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.models.crypto_paper import CryptoPaperExecution, CryptoQuoteBar
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    DecisionRecord,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PortfolioStatus,
    PositionStatus,
)
from app.models.user import User
from app.services.crypto_paper import (
    CryptoPaperError,
    _csv_cell,
    build_quote,
    quote_fingerprint,
    receipt_as_dict,
    receipts_csv,
    receipts_for_portfolio,
    record_quote_bars,
    submit_manual_order,
)
from app.services.crypto_paper_fixture import (
    FIXTURE_LABEL,
    FIXTURE_PRICE_SOURCE,
    build_fixture_candles,
    seed_crypto_paper_fixture,
)
from app.services.crypto_series import Candle

INSTRUMENT = KRAKEN_BTC_USD.instrument_id
# The fixture's last completed candle closes exactly on this minute, so a quote
# built with the same clock has age 0.
ANCHOR = datetime(2026, 10, 31, 9, 0, 0)


def _make_portfolio(cash: float = 10000.0, currency: str = "USD"):
    db = TestSession()
    user = User(
        email=f"crypto-paper-{uuid.uuid4().hex}@alethra.test",
        password_hash="x",
        display_name="Crypto Paper",
    )
    db.add(user)
    db.flush()
    portfolio = PaperPortfolio(
        owner_id=user.id,
        currency=currency,
        initial_virtual_cash=cash,
        current_virtual_cash=cash,
        status=PortfolioStatus.active,
    )
    db.add(portfolio)
    db.flush()
    db.commit()
    return db, user, portfolio


def _seed(db, *, now: datetime = ANCHOR, interval_minutes: int = 1):
    return seed_crypto_paper_fixture(db, interval_minutes=interval_minutes, now=now)


def _order(db, portfolio, user, **overrides):
    kwargs = dict(
        portfolio=portfolio,
        instrument_id=INSTRUMENT,
        interval_minutes=1,
        side="BUY",
        quantity=Decimal("0.01"),
        actor_id=user.id,
        now=ANCHOR,
        rationale="manual crypto paper order",
        expected_outcome="accounting moves match the receipt",
    )
    kwargs.update(overrides)
    return submit_manual_order(db, **kwargs)


# -- quote contract ---------------------------------------------------------

def test_seed_stores_only_completed_bars_with_fingerprint():
    db, _, _ = _make_portfolio()
    try:
        counts = _seed(db)
        db.commit()

        assert counts["stored"] == 6
        assert counts["skipped_forming"] == 0
        assert counts["data_nature"] == "fixture"

        rows = (
            db.query(CryptoQuoteBar)
            .order_by(CryptoQuoteBar.open_time)
            .all()
        )
        assert len(rows) == 6
        assert all(row.is_final for row in rows)
        assert all(row.data_nature == "fixture" for row in rows)
        assert all(row.price_source == FIXTURE_PRICE_SOURCE for row in rows)
        assert all(row.fingerprint == counts["fingerprint"] for row in rows)
        # The last row closes exactly on the anchor minute.
        assert rows[-1].close_time == ANCHOR
    finally:
        db.close()


def test_record_skips_forming_and_incompatible_candles():
    db, _, _ = _make_portfolio()
    try:
        final = build_fixture_candles(count=2, interval_minutes=1, anchor=ANCHOR)
        forming = Candle(
            instrument_id=INSTRUMENT,
            interval_minutes=1,
            open_time=ANCHOR,
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("1"),
            trade_count=1,
            is_final=False,
        )
        wrong_interval = Candle(
            instrument_id=INSTRUMENT,
            interval_minutes=5,
            open_time=ANCHOR,
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("1"),
            close=Decimal("2"),
            volume=Decimal("1"),
            trade_count=1,
            is_final=True,
        )
        counts = record_quote_bars(
            db,
            instrument_id=INSTRUMENT,
            provider_id="kraken",
            interval_minutes=1,
            candles=[*final, forming, wrong_interval],
            price_source=FIXTURE_PRICE_SOURCE,
            data_nature="fixture",
            fetched_at=ANCHOR,
        )
        db.commit()
        assert counts["stored"] == 2
        assert counts["skipped_forming"] == 1
        assert counts["skipped_incompatible"] == 1
    finally:
        db.close()


def test_unlabelled_source_is_refused():
    db, _, _ = _make_portfolio()
    try:
        with pytest.raises(CryptoPaperError) as excinfo:
            record_quote_bars(
                db,
                instrument_id=INSTRUMENT,
                provider_id="kraken",
                interval_minutes=1,
                candles=build_fixture_candles(count=1, anchor=ANCHOR),
                price_source="mystery",
                data_nature="synthetic",
            )
        assert excinfo.value.code == "unverified_quote"
    finally:
        db.close()


def test_quote_fingerprint_is_stable_and_sensitive_to_close():
    candles = build_fixture_candles(count=3, anchor=ANCHOR)
    first = quote_fingerprint(
        instrument_id=INSTRUMENT, provider_id="kraken", interval_minutes=1, candles=candles
    )
    again = quote_fingerprint(
        instrument_id=INSTRUMENT,
        provider_id="kraken",
        interval_minutes=1,
        candles=list(reversed(candles)),
    )
    assert first == again

    moved = [replace(candles[0], close=candles[0].close + Decimal("1"))] + candles[1:]
    assert (
        quote_fingerprint(
            instrument_id=INSTRUMENT, provider_id="kraken", interval_minutes=1, candles=moved
        )
        != first
    )


# -- refusals ---------------------------------------------------------------

def test_missing_quote_is_refused():
    db, _, _ = _make_portfolio()
    try:
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.01"),
                now=ANCHOR,
            )
        assert excinfo.value.code == "missing_quote"
    finally:
        db.close()


def test_stale_quote_is_refused():
    db, _, _ = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.01"),
                now=ANCHOR + timedelta(minutes=6),
            )
        assert excinfo.value.code == "stale_quote"
    finally:
        db.close()


def test_forming_row_is_refused_even_if_present():
    db, _, _ = _make_portfolio()
    try:
        db.add(
            CryptoQuoteBar(
                instrument_id=INSTRUMENT,
                provider_id="kraken",
                interval_minutes=1,
                open_time=ANCHOR - timedelta(minutes=1),
                close_time=ANCHOR,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                volume=Decimal("1"),
                is_final=False,
                data_nature="fixture",
                price_source=FIXTURE_PRICE_SOURCE,
                fingerprint="0" * 64,
                fetched_at=ANCHOR,
            )
        )
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.01"),
                now=ANCHOR,
            )
        # latest_quote_bar filters is_final, so a forming row alone is missing.
        assert excinfo.value.code == "missing_quote"
    finally:
        db.close()


def test_incompatible_provider_is_refused():
    db, _, _ = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.01"),
                provider_id="coinbase",
                now=ANCHOR,
            )
        assert excinfo.value.code == "incompatible_provider"
    finally:
        db.close()


def test_quantity_precision_exceeded():
    db, _, _ = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.000000001"),
                now=ANCHOR,
            )
        assert excinfo.value.code == "quantity_precision_exceeded"
    finally:
        db.close()


def test_below_min_quantity():
    db, _, _ = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.00001"),
                now=ANCHOR,
            )
        assert excinfo.value.code == "below_min_quantity"
    finally:
        db.close()


def test_below_min_notional(monkeypatch):
    db, _, _ = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        priced = replace(KRAKEN_BTC_USD, min_notional=Decimal("2000"))
        monkeypatch.setattr(
            "app.services.crypto_paper.get_crypto_instrument", lambda _id: priced
        )
        with pytest.raises(CryptoPaperError) as excinfo:
            build_quote(
                db,
                instrument_id=INSTRUMENT,
                interval_minutes=1,
                side="BUY",
                quantity=Decimal("0.01"),
                now=ANCHOR,
            )
        assert excinfo.value.code == "below_min_notional"
    finally:
        db.close()


# -- booking ----------------------------------------------------------------

def test_buy_fill_moves_shared_ledger_and_records_exact_receipt():
    db, user, portfolio = _make_portfolio(cash=10000.0)
    try:
        counts = _seed(db)
        db.commit()

        result = _order(db, portfolio, user)

        assert result.created is True
        assert result.order.status.value == "filled"
        assert result.fill.paper_order_id == result.order.id

        # Exact Decimal arithmetic. The in-memory result is read before commit:
        # after a commit the attributes reload through the column type, and
        # SQLite renders NUMERIC through float (PostgreSQL would stay exact).
        payload = result.as_dict()
        assert payload["gross_amount"] == "1051.65100000"
        assert payload["commission_amount"] == "1.05165100"
        assert payload["net_cash_change"] == "-1052.70265100"
        assert payload["observed_price"] == "105060.0"
        assert payload["execution_price"] == "105165.1"
        assert payload["ledger"]["net_cash_change"] == "-1052.7027"
        assert payload["ledger"]["mirror"] == result.receipt.ledger_mirror
        assert payload["label"] == FIXTURE_LABEL

        db.commit()
        db.refresh(result.receipt)

        # Reloaded from the DB, the receipt is re-quantised to its declared
        # scale, so float-backed columns do not leak noise into the record.
        assert Decimal(receipt_as_dict(result.receipt)["net_cash_change"]) == Decimal(
            "-1052.70265100"
        )
        assert Decimal(receipt_as_dict(result.receipt)["execution_price"]) == Decimal(
            "105165.1"
        )

        db.refresh(portfolio)
        assert portfolio.current_virtual_cash == pytest.approx(10000.0 - 1052.7027, abs=1e-4)
        position = (
            db.query(PaperPosition)
            .filter(PaperPosition.portfolio_id == portfolio.id)
            .one()
        )
        assert position.quantity == pytest.approx(0.01)
        assert position.average_entry_price == pytest.approx(105165.1)

        # Every identity the receipt names really exists and points back.
        assert db.get(PaperFill, result.receipt.paper_fill_id) is not None
        assert result.receipt.paper_order_id == result.order.id
        assert result.receipt.snapshot_fingerprint == counts["fingerprint"]
        assert result.receipt.data_nature == "fixture"
        assert result.receipt.quote_bar_id is not None

        payload = result.as_dict()
        assert payload["simulated"] is True
        assert payload["ledger"]["mirror"] == result.receipt.ledger_mirror
        assert payload["label"] == FIXTURE_LABEL
    finally:
        db.close()


def test_sell_without_position_is_refused_and_books_nothing():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        with pytest.raises(CryptoPaperError) as excinfo:
            _order(db, portfolio, user, side="SELL")
        assert excinfo.value.code == "insufficient_position"
        db.rollback()

        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperFill).count() == 0
        assert db.query(CryptoPaperExecution).count() == 0
        db.refresh(portfolio)
        assert portfolio.current_virtual_cash == pytest.approx(10000.0)
    finally:
        db.close()


def test_insufficient_cash_is_refused_and_books_nothing():
    db, user, portfolio = _make_portfolio(cash=10.0)
    try:
        _seed(db)
        db.commit()

        with pytest.raises(CryptoPaperError) as excinfo:
            _order(db, portfolio, user)
        assert excinfo.value.code == "insufficient_cash"
        db.rollback()

        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperFill).count() == 0
        assert db.query(CryptoPaperExecution).count() == 0
        assert db.query(PaperPosition).count() == 0
        db.refresh(portfolio)
        assert portfolio.current_virtual_cash == pytest.approx(10.0)
    finally:
        db.close()


def test_inactive_portfolio_is_refused():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        portfolio.status = PortfolioStatus.paused
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            _order(db, portfolio, user)
        assert excinfo.value.code == "portfolio_not_active"
    finally:
        db.close()


def test_currency_mismatch_is_refused():
    db, user, portfolio = _make_portfolio(currency="HKD")
    try:
        _seed(db)
        db.commit()
        with pytest.raises(CryptoPaperError) as excinfo:
            _order(db, portfolio, user)
        assert excinfo.value.code == "incompatible_portfolio"
    finally:
        db.close()


# -- idempotency ------------------------------------------------------------

def test_retry_with_same_key_returns_original_fill():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        first = _order(db, portfolio, user, idempotency_key="key-1")
        db.commit()

        second = _order(db, portfolio, user, idempotency_key="key-1")
        db.commit()

        assert first.created is True
        assert second.created is False
        assert second.fill.id == first.fill.id
        assert second.receipt.id == first.receipt.id
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperFill).count() == 1
        assert db.query(CryptoPaperExecution).count() == 1
        db.refresh(portfolio)
        assert portfolio.current_virtual_cash == pytest.approx(10000.0 - 1052.7027, abs=1e-4)
    finally:
        db.close()


def test_retry_after_quote_expired_still_returns_original_fill():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        first = _order(db, portfolio, user, idempotency_key="key-2")
        db.commit()

        # Hours later the quote is stale, but the retry must not re-price it.
        second = _order(
            db,
            portfolio,
            user,
            idempotency_key="key-2",
            now=ANCHOR + timedelta(hours=3),
        )
        db.commit()

        assert second.created is False
        assert second.fill.id == first.fill.id
        assert second.quote.execution_price == first.quote.execution_price
        assert db.query(PaperFill).count() == 1
    finally:
        db.close()


def test_same_key_with_different_order_is_refused():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        _order(db, portfolio, user, idempotency_key="key-3")
        db.commit()

        with pytest.raises(CryptoPaperError) as excinfo:
            _order(
                db,
                portfolio,
                user,
                idempotency_key="key-3",
                quantity=Decimal("0.02"),
            )
        assert excinfo.value.code == "idempotency_conflict"
        db.rollback()
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperFill).count() == 1
    finally:
        db.close()


# -- journal / CSV / isolation ----------------------------------------------

def test_receipts_are_scoped_to_their_portfolio():
    db_a, user_a, portfolio_a = _make_portfolio()
    db_b, user_b, portfolio_b = _make_portfolio()
    try:
        _seed(db_a)
        db_a.commit()
        _seed(db_b)
        db_b.commit()

        _order(db_a, portfolio_a, user_a, idempotency_key="a-1")
        db_a.commit()

        assert len(receipts_for_portfolio(db_a, portfolio_a.id)) == 1
        assert len(receipts_for_portfolio(db_a, portfolio_b.id)) == 0
        assert len(receipts_for_portfolio(db_b, portfolio_b.id)) == 0
    finally:
        db_a.close()
        db_b.close()


def test_receipt_csv_carries_identity_and_guards_formulas():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        result = _order(db, portfolio, user)
        db.commit()

        receipt = result.receipt
        text = receipts_csv([receipt])
        lines = text.splitlines()
        assert lines[0].startswith("receipt_id,created_at,portfolio_id")
        assert result.receipt.snapshot_fingerprint in text
        assert str(result.order.id) in text
        assert "\r\n" in text  # CRLF for spreadsheet consumers

        row = receipt_as_dict(receipt)
        assert row["order_status"] == "filled"
        assert row["decision_id"] == str(result.order.decision_id)
        assert row["ledger_mirror"] == receipt.ledger_mirror

        assert _csv_cell("=SUM(A1)") == "'=SUM(A1)"
        assert _csv_cell("+1") == "'+1"
        assert _csv_cell("@cmd") == "'@cmd"
        assert _csv_cell(None) == ""
    finally:
        db.close()


def test_crypto_lane_never_writes_research_market_data():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()
        _order(db, portfolio, user)
        db.commit()

        assert db.query(MarketDataBar).count() == 0
        # The quote rows are the lane's own table, keyed by interval.
        assert db.query(func.count(CryptoQuoteBar.id)).scalar() == 6
    finally:
        db.close()


def test_booked_snapshot_is_immutable_when_new_quotes_arrive():
    """A booked fill pins the bar it used; later quotes never rewrite it."""
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        result = _order(db, portfolio, user, idempotency_key="snapshot-1")
        db.commit()
        db.refresh(result.receipt)

        booked = receipt_as_dict(result.receipt)
        booked_fingerprint = booked["snapshot_fingerprint"]
        booked_bar_open = booked["bar_open_time"]
        booked_execution = booked["execution_price"]
        booked_net = booked["net_cash_change"]

        # Ten minutes later the venue (here: the fixture) has newer bars. The
        # newest quote must differ, otherwise this test would prove nothing.
        _seed(db, now=ANCHOR + timedelta(minutes=10))
        db.commit()
        newer = build_quote(
            db,
            instrument_id=INSTRUMENT,
            interval_minutes=1,
            side="BUY",
            quantity=Decimal("0.01"),
            now=ANCHOR + timedelta(minutes=10),
        )
        assert newer.snapshot_fingerprint != booked_fingerprint
        assert newer.bar_open_time.isoformat() != booked_bar_open

        # The receipt and its CSV row still name the original bar and prices.
        db.refresh(result.receipt)
        after = receipt_as_dict(result.receipt)
        assert after["snapshot_fingerprint"] == booked_fingerprint
        assert after["bar_open_time"] == booked_bar_open
        assert after["execution_price"] == booked_execution
        assert after["net_cash_change"] == booked_net
        assert booked_fingerprint in receipts_csv([result.receipt])

        # An identical retry returns the pinned fill, not the newer quote.
        retry = _order(
            db,
            portfolio,
            user,
            idempotency_key="snapshot-1",
            now=ANCHOR + timedelta(minutes=10),
        )
        db.commit()
        assert retry.created is False
        assert retry.fill.id == result.fill.id
        assert retry.receipt.snapshot_fingerprint == booked_fingerprint
        assert db.query(PaperFill).count() == 1
    finally:
        db.close()


def test_order_decision_is_written_to_the_shared_journal():
    db, user, portfolio = _make_portfolio()
    try:
        _seed(db)
        db.commit()

        result = _order(
            db,
            portfolio,
            user,
            rationale="buy the completed 1m bar",
            expected_outcome="position size matches the receipt",
        )
        db.commit()

        decision = db.get(DecisionRecord, result.order.decision_id)
        assert decision is not None
        assert decision.paper_portfolio_id == portfolio.id
        assert decision.owner_id == user.id
        assert decision.decision_type.value == "entry"
        assert decision.rationale == "buy the completed 1m bar"
        assert decision.expected_outcome == "position size matches the receipt"

        exported = receipt_as_dict(result.receipt)
        assert exported["decision_id"] == str(decision.id)

        sell = _order(
            db,
            portfolio,
            user,
            side="SELL",
            rationale="exit the position",
        )
        db.commit()
        exit_decision = db.get(DecisionRecord, sell.order.decision_id)
        assert exit_decision is not None
        assert exit_decision.decision_type.value == "exit"
    finally:
        db.close()
