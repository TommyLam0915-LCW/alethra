"""HTTP contract for the simulated crypto paper lane.

The fixture endpoint writes explicitly labelled synthetic quotes, so these tests
need no venue access. They prove the permission split, the refusal statuses and
that a booked order is visible through the receipt export — not just that a
route answers.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from tests.conftest import TestSession
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.models.crypto_paper import CryptoPaperExecution, CryptoQuoteBar
from app.models.paper_portfolio import (
    DecisionRecord,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
)
from app.services.crypto_paper_fixture import FIXTURE_PRICE_SOURCE

BASE = "/api/v1/crypto-paper"
INSTRUMENT = KRAKEN_BTC_USD.instrument_id


def _portfolio(client, headers, cash=10000.0):
    resp = client.post(
        "/api/v1/paper-portfolio",
        headers=headers,
        json={"initial_virtual_cash": cash, "currency": "USD"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed(client, admin_headers, interval_minutes=1):
    resp = client.post(
        f"{BASE}/admin/fixture",
        headers=admin_headers,
        json={"interval_minutes": interval_minutes},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _second_account(client):
    email = f"crypto-paper-other-{uuid4().hex}@alethra.io"
    client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "otherpass123",
            "display_name": "Other",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "otherpass123"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_instruments_declare_precision_and_simulation(client, auth_headers):
    resp = client.get(f"{BASE}/instruments", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["lane"] == "crypto_paper_simulated"
    assert body["simulated"] is True
    assert body["provider_id"] == "kraken"
    entry = next(i for i in body["instruments"] if i["instrument_id"] == INSTRUMENT)
    assert entry["quantity_precision"] == 8
    assert entry["price_precision"] == 1
    assert entry["min_quantity"] == "0.00006"
    assert entry["min_notional"] == "0.5"


def test_quote_preview_writes_nothing_and_labels_fixture(client, auth_headers, admin_headers):
    _seed(client, admin_headers)
    resp = client.get(
        f"{BASE}/quote",
        params={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data_nature"] == "fixture"
    assert body["price_source"] == FIXTURE_PRICE_SOURCE
    assert body["simulated"] is True
    assert body["label"].startswith("Synthetic")
    assert body["execution_assumption"]
    assert Decimal(body["net_cash_change"]) < 0

    db = TestSession()
    try:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperFill).count() == 0
    finally:
        db.close()


def test_missing_quote_is_conflict(client, auth_headers):
    resp = client.get(
        f"{BASE}/quote",
        params={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "missing_quote"


def test_stale_quote_is_conflict(client, auth_headers):
    db = TestSession()
    try:
        stale_close = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=30)
        db.add(
            CryptoQuoteBar(
                instrument_id=INSTRUMENT,
                provider_id="kraken",
                interval_minutes=1,
                open_time=stale_close - timedelta(minutes=1),
                close_time=stale_close,
                open=Decimal("100000"),
                high=Decimal("100010"),
                low=Decimal("99990"),
                close=Decimal("100005"),
                volume=Decimal("1"),
                is_final=True,
                data_nature="fixture",
                price_source=FIXTURE_PRICE_SOURCE,
                fingerprint="f" * 64,
                fetched_at=stale_close,
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(
        f"{BASE}/quote",
        params={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "stale_quote"


def test_fixture_seed_requires_admin(client, auth_headers):
    resp = client.post(
        f"{BASE}/admin/fixture", headers=auth_headers, json={"interval_minutes": 1}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "admin_required"


def test_submit_order_books_and_exports_receipt(client, auth_headers, admin_headers):
    portfolio_id = _portfolio(client, auth_headers)
    seeded = _seed(client, admin_headers)

    resp = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders",
        headers=auth_headers,
        json={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
            "idempotency_key": "api-key-1",
            "rationale": "engineering the paper lane",
            "expected_outcome": "cash debits by the receipt's net",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["status"] == "filled"
    assert body["ledger"]["mirror"]
    assert body["snapshot_fingerprint"] == seeded["fingerprint"]
    assert body["data_nature"] == "fixture"
    assert body["simulated"] is True

    # The portfolio's cash really moved on the shared ledger.
    portfolio = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
    ).json()
    assert portfolio["current_virtual_cash"] == pytest.approx(10000.0 - 1052.7027, abs=1e-4)

    json_export = client.get(
        f"{BASE}/portfolios/{portfolio_id}/executions", headers=auth_headers
    )
    assert json_export.status_code == 200
    records = json_export.json()
    assert records["row_count"] == 1
    assert records["records"][0]["receipt_id"] == body["receipt_id"]
    assert records["records"][0]["snapshot_fingerprint"] == seeded["fingerprint"]

    csv_export = client.get(
        f"{BASE}/portfolios/{portfolio_id}/executions",
        params={"format": "csv"},
        headers=auth_headers,
    )
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_export.headers["content-disposition"]
    assert body["receipt_id"] in csv_export.text
    assert seeded["fingerprint"] in csv_export.text


def test_retry_with_same_key_does_not_double_book(client, auth_headers, admin_headers):
    portfolio_id = _portfolio(client, auth_headers)
    _seed(client, admin_headers)
    payload = {
        "instrument_id": INSTRUMENT,
        "interval_minutes": 1,
        "side": "BUY",
        "quantity": "0.01",
        "idempotency_key": "api-retry-1",
    }
    first = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders", headers=auth_headers, json=payload
    )
    second = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders", headers=auth_headers, json=payload
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["created"] is False
    assert second.json()["fill_id"] == first.json()["fill_id"]

    db = TestSession()
    try:
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperFill).count() == 1
    finally:
        db.close()


def test_sell_without_position_is_refused(client, auth_headers, admin_headers):
    portfolio_id = _portfolio(client, auth_headers)
    _seed(client, admin_headers)
    resp = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders",
        headers=auth_headers,
        json={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "SELL",
            "quantity": "0.01",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "insufficient_position"

    db = TestSession()
    try:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperFill).count() == 0
    finally:
        db.close()


def test_orders_and_receipts_are_owner_scoped(client, auth_headers, admin_headers):
    portfolio_id = _portfolio(client, auth_headers)
    _seed(client, admin_headers)
    other = _second_account(client)

    submit = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders",
        headers=other,
        json={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
    )
    assert submit.status_code == 403

    receipts = client.get(
        f"{BASE}/portfolios/{portfolio_id}/executions", headers=other
    )
    assert receipts.status_code == 403

    own = client.get(f"{BASE}/portfolios/{portfolio_id}/executions", headers=auth_headers)
    assert own.status_code == 200
    assert own.json()["row_count"] == 0


def test_quote_refuses_unlabelled_data(client, auth_headers):
    db = TestSession()
    try:
        close = datetime.utcnow().replace(microsecond=0)
        db.add(
            CryptoQuoteBar(
                instrument_id=INSTRUMENT,
                provider_id="kraken",
                interval_minutes=1,
                open_time=close - timedelta(minutes=1),
                close_time=close,
                open=Decimal("100000"),
                high=Decimal("100010"),
                low=Decimal("99990"),
                close=Decimal("100005"),
                volume=Decimal("1"),
                is_final=True,
                data_nature="mystery",
                price_source="unknown",
                fingerprint="e" * 64,
                fetched_at=close,
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get(
        f"{BASE}/quote",
        params={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "unverified_quote"


def test_failure_after_fill_rolls_back_the_whole_order(client, auth_headers, admin_headers, monkeypatch):
    """A failure after book_fill must not leave a half-applied order behind.

    The route commits only after the receipt and journal event exist; before
    that, any exception has to roll the whole unit of work back — including the
    cash movement book_fill already made.
    """
    import app.services.crypto_paper as crypto_paper_service

    portfolio_id = _portfolio(client, auth_headers)
    _seed(client, admin_headers)

    def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated failure after the fill was booked")

    monkeypatch.setattr(crypto_paper_service, "emit_event", _explode)

    with pytest.raises(RuntimeError):
        client.post(
            f"{BASE}/portfolios/{portfolio_id}/orders",
            headers=auth_headers,
            json={
                "instrument_id": INSTRUMENT,
                "interval_minutes": 1,
                "side": "BUY",
                "quantity": "0.01",
                "idempotency_key": "rollback-1",
            },
        )

    db = TestSession()
    try:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperFill).count() == 0
        assert db.query(CryptoPaperExecution).count() == 0
        assert db.query(DecisionRecord).count() == 0
        portfolio = db.get(PaperPortfolio, UUID(portfolio_id))
        assert float(portfolio.current_virtual_cash) == pytest.approx(10000.0)
    finally:
        db.close()


def test_receipt_export_names_the_journal_decision(client, auth_headers, admin_headers):
    portfolio_id = _portfolio(client, auth_headers)
    _seed(client, admin_headers)

    submit = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders",
        headers=auth_headers,
        json={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
            "rationale": "completed 1m bar breakout",
            "expected_outcome": "position matches the receipt",
        },
    )
    assert submit.status_code == 201, submit.text
    decision_id = submit.json()["decision_id"]
    assert decision_id

    export = client.get(
        f"{BASE}/portfolios/{portfolio_id}/executions", headers=auth_headers
    )
    record = export.json()["records"][0]
    assert record["decision_id"] == decision_id

    journal = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers
    )
    assert journal.status_code == 200, journal.text
    decisions = journal.json()
    matching = [row for row in decisions if row["id"] == decision_id]
    assert len(matching) == 1
    assert matching[0]["rationale"] == "completed 1m bar breakout"
    assert matching[0]["expected_outcome"] == "position matches the receipt"
    assert matching[0]["decision_type"] == "entry"

    csv_export = client.get(
        f"{BASE}/portfolios/{portfolio_id}/executions",
        params={"format": "csv"},
        headers=auth_headers,
    )
    assert decision_id in csv_export.text

