"""HTTP contract for the simulated crypto paper lane.

The fixture endpoint writes explicitly labelled synthetic quotes. The capture
endpoint is exercised against an in-memory Kraken transport, so no test opens a
socket or spends upstream budget. Together they prove the permission split, the
refusal statuses, the live/fixture labels and that a booked order is visible
through the receipt export — not just that a route answers.
"""

from datetime import datetime, timedelta, timezone
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
from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import KrakenProvider, KrakenUnavailableError
from app.services.crypto_paper_fixture import FIXTURE_PRICE_SOURCE
from app.services.upstream_budget import Reservation

BASE = "/api/v1/crypto-paper"
INSTRUMENT = KRAKEN_BTC_USD.instrument_id
LIVE_PRICE_SOURCE = "kraken:public_rest:completed_bars"


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


# -- live capture (in-memory transport) -------------------------------------


class _FakeKrakenTransport:
    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        entry = self._routes[path]
        if isinstance(entry, Exception):
            raise entry
        return entry


def _minute_floor():
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _ohlc_rows(now, interval_minutes=1, completed=3):
    """Completed rows closing at or before ``now``, plus one forming row."""
    rows = []
    for offset in range(completed, 0, -1):
        open_time = now - timedelta(minutes=interval_minutes * offset)
        close = Decimal("100000") + Decimal(offset)
        rows.append(
            [
                int(open_time.timestamp()),
                str(close),
                str(close + 1),
                str(close - 1),
                str(close),
                str(close),
                "1.5",
                4,
            ]
        )
    forming_close = Decimal("100010")
    rows.append(
        [
            int(now.timestamp()),
            str(forming_close),
            str(forming_close + 1),
            str(forming_close - 1),
            str(forming_close),
            str(forming_close),
            "0.25",
            1,
        ]
    )
    return rows


def _ohlc_payload(rows):
    return {"error": [], "result": {"XXBTZUSD": rows, "last": "unused"}}


def _install_capture_provider(routes, budget_gate=None):
    transport = _FakeKrakenTransport(routes)
    provider = KrakenProvider(
        transport=transport,
        clock=_minute_floor,
        min_request_interval=0,
        budget_gate=budget_gate,
    )
    set_crypto_provider(provider)
    return transport


class _DenyingBudgetGate:
    def reserve(self, **_kwargs):
        return Reservation(
            allowed=False,
            denied_layer="provider_hour",
            reason="hour budget exhausted",
            provider_hour_used=60,
            provider_day_used=60,
            verify_run_used=0,
            verify_run_limit=0,
        )

    def snapshot(self, **_kwargs):  # pragma: no cover - capture never reads it
        raise AssertionError("a refused capture must not read the budget snapshot")


@pytest.fixture(autouse=True)
def _reset_crypto_provider_registry():
    reset_crypto_provider()
    yield
    reset_crypto_provider()


def test_capture_stores_completed_live_bars_and_prices_a_fill(client, auth_headers):
    transport = _install_capture_provider(
        {"/0/public/OHLC": _ohlc_payload(_ohlc_rows(_minute_floor()))}
    )

    resp = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "interval_minutes": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "captured"
    assert body["data_nature"] == "live"
    assert body["price_source"] == LIVE_PRICE_SOURCE
    assert body["stored"] == 3
    assert body["forming_excluded"] is True
    # The open candle never reaches the recorder, so nothing is skipped there.
    assert body["skipped_forming"] == 0
    assert body["latest_completed_bar"] is not None
    assert len(body["fingerprint"]) == 64

    # Exactly one upstream read, through the public OHLC route.
    assert transport.calls == [("/0/public/OHLC", {"pair": "XBTUSD", "interval": "1"})]

    db = TestSession()
    try:
        rows = db.query(CryptoQuoteBar).all()
        assert len(rows) == 3
        assert all(row.is_final for row in rows)
        assert all(row.data_nature == "live" for row in rows)
        assert all(row.price_source == LIVE_PRICE_SOURCE for row in rows)
        # The forming candle's open time is never stored.
        assert all(row.open_time < _minute_floor().replace(tzinfo=None) for row in rows)
    finally:
        db.close()

    quote = client.get(
        f"{BASE}/quote",
        params={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
        },
        headers=auth_headers,
    )
    assert quote.status_code == 200, quote.text
    qbody = quote.json()
    assert qbody["data_nature"] == "live"
    assert qbody["price_source"] == LIVE_PRICE_SOURCE
    assert qbody["snapshot_fingerprint"] == body["fingerprint"]
    assert qbody["label"] == "Completed venue bar"

    portfolio_id = _portfolio(client, auth_headers)
    order = client.post(
        f"{BASE}/portfolios/{portfolio_id}/orders",
        headers=auth_headers,
        json={
            "instrument_id": INSTRUMENT,
            "interval_minutes": 1,
            "side": "BUY",
            "quantity": "0.01",
            "idempotency_key": "live-capture-1",
        },
    )
    assert order.status_code == 201, order.text
    assert order.json()["data_nature"] == "live"
    assert order.json()["snapshot_fingerprint"] == body["fingerprint"]
    assert order.json()["label"] == "Simulated fill from a completed venue bar"


def test_capture_upserts_without_duplicating_the_same_bar(client, auth_headers):
    _install_capture_provider(
        {"/0/public/OHLC": _ohlc_payload(_ohlc_rows(_minute_floor()))}
    )
    payload = {"instrument_id": INSTRUMENT, "interval_minutes": 1}

    first = client.post(f"{BASE}/quote/capture", headers=auth_headers, json=payload)
    second = client.post(f"{BASE}/quote/capture", headers=auth_headers, json=payload)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["stored"] == 3
    assert second.json()["stored"] == 0
    assert second.json()["updated"] == 3

    db = TestSession()
    try:
        assert db.query(CryptoQuoteBar).count() == 3
    finally:
        db.close()


def test_capture_rejects_unknown_instrument_and_unsupported_interval(client, auth_headers):
    transport = _install_capture_provider({})

    unknown = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": "CRYPTO:KRAKEN:DOGE-USD", "interval_minutes": 1},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "incompatible_instrument"

    unsupported = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "interval_minutes": 15},
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"]["code"] == "unsupported_interval"
    assert unsupported.json()["detail"]["supported"] == [1, 5]

    assert transport.calls == []


def test_capture_requires_authentication(client):
    resp = client.post(
        f"{BASE}/quote/capture",
        json={"instrument_id": INSTRUMENT, "interval_minutes": 1},
    )
    assert resp.status_code == 401


def test_capture_maps_a_venue_outage_without_falling_back(client, auth_headers):
    _install_capture_provider(
        {"/0/public/OHLC": KrakenUnavailableError("simulated outage")}
    )
    resp = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "interval_minutes": 1},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["reason"] == "venue_unreachable"
    assert detail["available"] is False
    assert "never substituted" in detail["no_fallback"]

    db = TestSession()
    try:
        assert db.query(CryptoQuoteBar).count() == 0
    finally:
        db.close()


def test_capture_reports_budget_exhaustion_as_its_own_state(client, auth_headers):
    transport = _install_capture_provider(
        {"/0/public/OHLC": _ohlc_payload(_ohlc_rows(_minute_floor()))},
        budget_gate=_DenyingBudgetGate(),
    )
    resp = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "interval_minutes": 1},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["reason"] == "budget_exhausted"
    assert detail["denied_layer"] == "provider_hour"
    assert detail["stored_available"] == "/market/crypto/stored"
    assert detail["retry_safe"] is False
    assert transport.calls == []

    db = TestSession()
    try:
        assert db.query(CryptoQuoteBar).count() == 0
    finally:
        db.close()


def test_capture_without_completed_bars_reports_no_data(client, auth_headers):
    now = _minute_floor()
    forming_only = _ohlc_rows(now, completed=0)
    _install_capture_provider({"/0/public/OHLC": _ohlc_payload(forming_only)})

    resp = client.post(
        f"{BASE}/quote/capture",
        headers=auth_headers,
        json={"instrument_id": INSTRUMENT, "interval_minutes": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"

    db = TestSession()
    try:
        assert db.query(CryptoQuoteBar).count() == 0
    finally:
        db.close()


def _second_account(client):
    email = f"crypto-paper-other-{uuid4().hex}@alethra.io"
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
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

