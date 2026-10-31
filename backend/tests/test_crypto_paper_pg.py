"""PostgreSQL concurrency for the simulated crypto paper lane.

SQLite cannot prove row-level locking, so this module runs only when the suite
is executed against a disposable PostgreSQL database::

    ALETHRA_PG_TEST_URL=postgresql://... ./backend/scripts/run_tests.sh pg \
        tests/test_crypto_paper_pg.py

It exercises the same ``with_for_update`` claim the HTTP route makes before it
calls ``submit_manual_order``. Two connections contend for one portfolio row:

1. The same idempotency key twice must book one fill, not two.
2. Two different keys with cash for only one fill must book exactly one and
   refuse the other, leaving the shared ledger consistent.
"""

import os
import threading
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

if not os.environ.get("ALETHRA_TEST_DB", "").startswith("postgresql"):
    pytest.skip(
        "PostgreSQL not configured (run via scripts/run_tests.sh pg)",
        allow_module_level=True,
    )

from tests.conftest import TestSession
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.models.crypto_paper import CryptoPaperExecution
from app.models.paper_portfolio import PaperFill, PaperOrder, PaperPortfolio, PortfolioStatus
from app.models.user import User
from app.services.crypto_paper import CryptoPaperError, submit_manual_order
from app.services.crypto_paper_fixture import seed_crypto_paper_fixture

INSTRUMENT = KRAKEN_BTC_USD.instrument_id


def _anchor() -> datetime:
    # The fixture's last completed bar closes on this minute, so the quote the
    # service builds with the same clock is fresh regardless of wall time.
    return datetime.utcnow().replace(second=0, microsecond=0)


def _portfolio(cash: float):
    db = TestSession()
    try:
        user = User(
            email=f"crypto-pg-{uuid4().hex}@alethra.test",
            password_hash="x",
            display_name="Crypto PG",
        )
        db.add(user)
        db.flush()
        portfolio = PaperPortfolio(
            owner_id=user.id,
            currency="USD",
            initial_virtual_cash=cash,
            current_virtual_cash=cash,
            status=PortfolioStatus.active,
        )
        db.add(portfolio)
        db.flush()
        anchor = _anchor()
        seed_crypto_paper_fixture(db, interval_minutes=1, now=anchor)
        db.commit()
        return portfolio.id, user.id, anchor
    finally:
        db.close()


def _submit(portfolio_id, user_id, key, anchor, barrier, results, lock):
    session = TestSession()
    try:
        # Both connections must be ready before either takes the row lock:
        # waiting *inside* the transaction would make one connection hold the
        # lock while the other blocks acquiring it, deadlocking the barrier.
        barrier.wait(timeout=10)
        # The same claim the HTTP route takes before pricing anything.
        portfolio = (
            session.query(PaperPortfolio)
            .populate_existing()
            .filter(PaperPortfolio.id == portfolio_id)
            .with_for_update()
            .first()
        )
        assert portfolio is not None
        result = submit_manual_order(
            session,
            portfolio=portfolio,
            instrument_id=INSTRUMENT,
            interval_minutes=1,
            side="BUY",
            quantity=Decimal("0.01"),
            actor_id=user_id,
            idempotency_key=key,
            now=anchor,
        )
        session.commit()
        with lock:
            results.append(("ok", str(result.fill.id), result.created))
    except CryptoPaperError as exc:
        session.rollback()
        with lock:
            results.append(("refused", exc.code, None))
    except Exception as exc:  # surfaced as a failure, never swallowed
        session.rollback()
        with lock:
            results.append(("error", repr(exc), None))
    finally:
        session.close()


def _race(portfolio_id, user_id, keys, anchor):
    barrier = threading.Barrier(len(keys))
    results = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_submit,
            args=(portfolio_id, user_id, key, anchor, barrier, results, lock),
        )
        for key in keys
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_same_idempotency_key_books_one_fill_under_contention():
    portfolio_id, user_id, anchor = _portfolio(cash=10000.0)

    results = _race(portfolio_id, user_id, ["pg-same-key", "pg-same-key"], anchor)

    assert len(results) == 2, results
    errors = [row for row in results if row[0] == "error"]
    assert errors == [], errors
    ok = [row for row in results if row[0] == "ok"]
    assert len(ok) == 2, results
    assert sorted(row[2] for row in ok) == [False, True]
    # Both callers were told about the same fill.
    assert len({row[1] for row in ok}) == 1

    db = TestSession()
    try:
        assert db.query(PaperOrder).filter(PaperOrder.portfolio_id == portfolio_id).count() == 1
        assert db.query(PaperFill).count() == 1
        assert (
            db.query(CryptoPaperExecution)
            .filter(CryptoPaperExecution.portfolio_id == portfolio_id)
            .count()
            == 1
        )
        portfolio = db.get(PaperPortfolio, portfolio_id)
        assert float(portfolio.current_virtual_cash) == pytest.approx(
            10000.0 - 1052.7027, abs=1e-4
        )
    finally:
        db.close()


def test_cash_for_one_fill_books_exactly_one():
    portfolio_id, user_id, anchor = _portfolio(cash=1100.0)

    results = _race(portfolio_id, user_id, ["pg-key-a", "pg-key-b"], anchor)

    assert len(results) == 2, results
    assert [row for row in results if row[0] == "error"] == []
    ok = [row for row in results if row[0] == "ok"]
    refused = [row for row in results if row[0] == "refused"]
    assert len(ok) == 1, results
    assert len(refused) == 1, results
    assert refused[0][1] == "insufficient_cash"

    db = TestSession()
    try:
        assert db.query(PaperOrder).filter(PaperOrder.portfolio_id == portfolio_id).count() == 1
        assert db.query(PaperFill).count() == 1
        assert (
            db.query(CryptoPaperExecution)
            .filter(CryptoPaperExecution.portfolio_id == portfolio_id)
            .count()
            == 1
        )
        portfolio = db.get(PaperPortfolio, portfolio_id)
        assert float(portfolio.current_virtual_cash) == pytest.approx(
            1100.0 - 1052.7027, abs=1e-4
        )
    finally:
        db.close()
