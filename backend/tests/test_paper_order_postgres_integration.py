"""PostgreSQL-only integration tests for the paper order money path.

These exist because the defects they cover are invisible on SQLite:

* a numpy scalar reaching a ``Float`` column renders as ``np.float64(...)`` in
  psycopg2 and aborts the UPDATE, which SQLite accepts silently;
* ``SELECT ... FOR UPDATE`` is a no-op on SQLite, so concurrent cash mutation
  cannot be exercised there.

Run against a disposable PostgreSQL database, never against a review database:

    ALETHRA_PG_DATABASE_URL=postgresql://user:pw@host:5432/alethra_pgtest \\
        python scripts/verify_paper_orders_on_postgres.py
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier

import pytest
from sqlalchemy import func

from tests.conftest import TestSession
from app.data.demo_data import DEMO_DATA
from app.models.api_key import ApiKey
from app.models.audit_event import AuditEvent
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    OrderStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    PositionStatus,
)
from app.models.simulation import SimulationDecision, SimulationSession
# The session lane (submit -> pending -> advance -> fill -> journal) and the price
# arithmetic its assertions are derived from already live in the lifecycle suite.
# They are imported rather than copied: duplicating the bar prices or the costs here
# would let the two files drift apart on the very numbers this file's assertions
# depend on, and a drifted constant silently changes what "unaffordable" means.
from tests.test_manual_decision_books_linked_portfolio import (
    B2,
    BUY20_B2_NET,
    BUY20_B1_ESTIMATE_COST,
    INITIAL_CASH,
    _advance,
    _open_account,
    _order_view,
    _submit,
    account_orders,
    events_for,
    fills,
    journal_csv_rows,
    journal_json,
    open_qty,
    replay_decisions,
    seed_authored_bars,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("ALETHRA_PG_DATABASE_URL"),
    reason="requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL)",
)

ORDERS = "/api/v1/paper-portfolio/{}/orders"


@pytest.fixture()
def pg_url():
    return os.environ["ALETHRA_PG_DATABASE_URL"]


@pytest.fixture()
def engine_dialect():
    from tests.conftest import TEST_ENGINE

    return TEST_ENGINE.dialect.name


def _portfolio(client, headers, cash):
    resp = client.post(
        "/api/v1/paper-portfolio",
        headers=headers,
        json={"initial_virtual_cash": cash, "currency": "USD"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _order(client, headers, portfolio_id, **overrides):
    body = {
        "asset_id": "DEMO-ALPHA",
        "side": "BUY",
        "quantity": 1,
        "order_type": "market",
        "rationale": "integration test",
        "expected_outcome": "filled",
    }
    body.update(overrides)
    return client.post(ORDERS.format(portfolio_id), headers=headers, json=body)


def _cash(portfolio_id):
    db = TestSession()
    try:
        return float(db.query(PaperPortfolio).filter(PaperPortfolio.id == portfolio_id).one().current_virtual_cash)
    finally:
        db.close()


def _counts(portfolio_id=None):
    db = TestSession()
    try:
        q = db.query(PaperOrder)
        fq = db.query(PaperFill)
        if portfolio_id:
            q = q.filter(PaperOrder.portfolio_id == portfolio_id)
            fq = fq.join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id).filter(
                PaperOrder.portfolio_id == portfolio_id
            )
        return q.count(), fq.count()
    finally:
        db.close()


class TestGuardAgainstHarnessMisconfiguration:
    """A PostgreSQL-only file must not silently run on SQLite."""

    def test_engine_is_postgres(self, pg_url, engine_dialect):
        assert engine_dialect == "postgresql", (
            "ALETHRA_PG_DATABASE_URL is set but the test engine is "
            f"{engine_dialect}; these tests would prove nothing about locking"
        )


class TestFillPersistsOnPostgres:
    """Regression for HTTP 500 InvalidSchemaName on every simulated fill."""

    def test_buy_fill_is_stored_and_priced_from_the_newest_bar(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)

        resp = _order(client, auth_headers, portfolio_id, quantity=10)
        assert resp.status_code == 201, resp.text
        order = resp.json()

        resp = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order['id']}/fills",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        fill = resp.json()[0]

        # 2024-02-13 is the newest bar in the demo fixture. Stamping today's
        # wall-clock date would claim a session that has no data behind it.
        assert fill["execution_date"] == "2024-02-13"
        assert order["data_cutoff_at"] == "2024-02-13"
        assert order["actor_type"] == "human"

        # DEMO-ALPHA open on 2024-02-13 is 54.0
        assert fill["observed_price"] == pytest.approx(54.0)
        assert fill["execution_price_before_costs"] == pytest.approx(54.054)
        gross = 10 * 54.054
        assert fill["gross_amount"] == pytest.approx(round(gross, 4), abs=0.01)
        assert fill["commission_amount"] == pytest.approx(round(gross * 0.001, 4), abs=0.01)
        assert fill["slippage_amount"] == pytest.approx(round(0.054 * 10, 4), abs=0.01)

        expected_cash = round(10000.0 - (fill["gross_amount"] + fill["commission_amount"]), 4)
        assert _cash(portfolio_id) == pytest.approx(expected_cash, abs=0.01)

        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert len(positions) == 1
        assert positions[0]["quantity"] == pytest.approx(10.0)
        assert positions[0]["status"] == "open"

    def test_no_client_supplied_price_is_accepted(self, client, auth_headers, pg_url):
        """The executor prices from the bar; an extra field must not move it."""
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        resp = client.post(
            ORDERS.format(portfolio_id),
            headers=auth_headers,
            json={
                "asset_id": "DEMO-ALPHA",
                "side": "BUY",
                "quantity": 10,
                "order_type": "market",
                "execution_price": 0.01,
                "price": 0.01,
            },
        )
        assert resp.status_code == 201, resp.text
        fill = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{resp.json()['id']}/fills",
            headers=auth_headers,
        ).json()[0]
        assert fill["observed_price"] == pytest.approx(54.0)


class TestIdempotency:
    def test_retry_with_same_key_replays_the_order(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        cash_before = _cash(portfolio_id)

        first = _order(client, auth_headers, portfolio_id, quantity=5, idempotency_key="req-abc")
        assert first.status_code == 201, first.text

        second = _order(client, auth_headers, portfolio_id, quantity=5, idempotency_key="req-abc")
        assert second.status_code == 200, second.text
        assert second.json()["id"] == first.json()["id"]

        orders, fills = _counts(portfolio_id)
        assert orders == 1
        assert fills == 1
        assert _cash(portfolio_id) == pytest.approx(
            cash_before - fills_cash(portfolio_id), abs=0.01
        )

    def test_same_key_different_payload_conflicts(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        first = _order(client, auth_headers, portfolio_id, quantity=5, idempotency_key="req-xyz")
        assert first.status_code == 201

        conflict = _order(client, auth_headers, portfolio_id, quantity=7, idempotency_key="req-xyz")
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["error"] == "idempotency_key_conflict"

        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (1, 1)

    def test_orders_without_a_key_are_never_deduplicated(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        assert _order(client, auth_headers, portfolio_id, quantity=1).status_code == 201
        assert _order(client, auth_headers, portfolio_id, quantity=1).status_code == 201
        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (2, 2)


def fills_cash(portfolio_id):
    db = TestSession()
    try:
        total = (
            db.query(func.sum(PaperFill.net_cash_change))
            .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
            .filter(PaperOrder.portfolio_id == portfolio_id)
            .scalar()
        )
        return -float(total or 0.0)
    finally:
        db.close()


class TestConcurrency:
    def test_parallel_orders_cannot_over_spend(self, client, auth_headers, pg_url):
        """Cash 11000 funds exactly two 100-share BUYs at ~5411 each."""
        portfolio_id = _portfolio(client, auth_headers, 11000.0)
        start = Barrier(5, timeout=60)

        def place(_):
            start.wait()
            return _order(client, auth_headers, portfolio_id, quantity=100).status_code

        with ThreadPoolExecutor(max_workers=5) as pool:
            codes = list(pool.map(place, range(5)))

        filled = codes.count(201)
        rejected = codes.count(400)
        assert filled == 2, f"expected 2 fills, got {filled} (codes={codes})"
        assert rejected == 3

        cash = _cash(portfolio_id)
        assert cash >= 0, f"cash went negative: {cash}"

        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (2, 2)

        db = TestSession()
        try:
            position = (
                db.query(PaperPosition)
                .filter(PaperPosition.portfolio_id == portfolio_id)
                .one()
            )
            assert float(position.quantity) == pytest.approx(200.0)
            # No lost update: cash movement equals the two recorded fills.
            expected = 11000.0 + float(
                db.query(func.sum(PaperFill.net_cash_change))
                .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
                .filter(PaperOrder.portfolio_id == portfolio_id)
                .scalar()
            )
            assert cash == pytest.approx(expected, abs=0.01)
        finally:
            db.close()

    def test_parallel_idempotent_retries_create_one_order(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 20000.0)
        start = Barrier(4)

        def place(_):
            start.wait()
            return _order(
                client, auth_headers, portfolio_id, quantity=10, idempotency_key="race-1"
            ).status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(place, range(4)))

        assert set(codes) <= {200, 201}, codes
        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (1, 1)


class TestConcurrentCashLockIsLoadBearing:
    """The same no-overspend guarantee, reached through the money path only.

    ``create_order`` is called directly, one session per thread, because an HTTP
    request first increments the shared rate-limit counter inside its own
    transaction. That counter row lock serialises the requests before they ever
    reach the portfolio, so the HTTP-level test cannot observe a race even with
    the row lock removed. Here the only remaining serialisation is
    ``SELECT ... FOR UPDATE`` on the portfolio row.
    """

    def test_parallel_direct_calls_cannot_over_spend(self, client, auth_headers, pg_url):
        from types import SimpleNamespace
        from uuid import UUID

        from fastapi import HTTPException, Response

        from app.api.paper_portfolio import create_order
        from app.api.schemas import PaperOrderCreate
        from app.models.user import User

        portfolio_id = UUID(_portfolio(client, auth_headers, 11000.0))
        session = TestSession()
        try:
            user = session.query(User).filter(User.email == "test@alethra.io").one()
        finally:
            session.close()

        start = Barrier(5, timeout=60)

        def place(_):
            session = TestSession()
            try:
                start.wait()
                create_order(
                    portfolio_id,
                    PaperOrderCreate(
                        asset_id="DEMO-ALPHA",
                        side="BUY",
                        quantity=100,
                        rationale="race",
                        expected_outcome="filled",
                    ),
                    SimpleNamespace(state=SimpleNamespace(api_key_scopes=None)),
                    Response(),
                    db=session,
                    current_user=user,
                )
                return "filled"
            except HTTPException as exc:
                return exc.status_code
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=5) as pool:
            outcomes = list(pool.map(place, range(5)))

        assert outcomes.count("filled") == 2, f"expected 2 fills, got {outcomes}"
        assert set(o for o in outcomes if o != "filled") == {400}
        assert _cash(portfolio_id) >= 0, "cash went negative"
        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (2, 2)


class TestFailedTradeLeavesNoPartialState:
    def test_fill_failure_rolls_back_decision_order_and_fill(self, client, auth_headers, pg_url):
        """DEMO-BETA 2024-02-13: open 100.0 > close 99.5.

        Cash 99.8 passes the position-size risk check (valued at close) but
        cannot pay the slippage-and-commission-adjusted fill, so the order fails
        inside the fill. Nothing may survive.
        """
        portfolio_id = _portfolio(client, auth_headers, 99.8)

        resp = _order(client, auth_headers, portfolio_id, asset_id="DEMO-BETA", quantity=1)
        assert resp.status_code == 400, resp.text
        assert "insufficient cash" in resp.json()["detail"].lower()

        orders, fills = _counts(portfolio_id)
        assert (orders, fills) == (0, 0)
        assert _cash(portfolio_id) == pytest.approx(99.8)

        db = TestSession()
        try:
            assert db.query(PaperPosition).filter(
                PaperPosition.portfolio_id == portfolio_id
            ).count() == 0
            assert db.query(AuditEvent).filter(
                AuditEvent.entity_id == str(portfolio_id),
                AuditEvent.event_type == "order_created",
            ).count() == 0
        finally:
            db.close()


class TestOrderTypeAndExitPaths:
    def test_limit_order_is_rejected_not_filled_as_market(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        resp = _order(client, auth_headers, portfolio_id, quantity=5, order_type="limit")
        assert resp.status_code == 400, resp.text
        assert "unsupported order_type" in resp.json()["detail"].lower()
        assert _counts(portfolio_id) == (0, 0)

    def test_oversell_and_short_are_rejected(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        # No position at all: a SELL would be a short.
        resp = _order(client, auth_headers, portfolio_id, side="SELL", quantity=1)
        assert resp.status_code == 400, resp.text

        assert _order(client, auth_headers, portfolio_id, quantity=10).status_code == 201
        resp = _order(client, auth_headers, portfolio_id, side="SELL", quantity=11)
        assert resp.status_code == 400, resp.text

        orders, fills = _counts(portfolio_id)
        assert orders == 3  # two rejected attempts are recorded, not executed
        assert fills == 1

    def test_partial_then_full_exit(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        assert _order(client, auth_headers, portfolio_id, quantity=10).status_code == 201

        assert _order(client, auth_headers, portfolio_id, side="SELL", quantity=4).status_code == 201
        db = TestSession()
        try:
            pos = db.query(PaperPosition).filter(PaperPosition.portfolio_id == portfolio_id).one()
            assert float(pos.quantity) == pytest.approx(6.0)
            assert pos.status == PositionStatus.open
        finally:
            db.close()

        assert _order(client, auth_headers, portfolio_id, side="SELL", quantity=6).status_code == 201
        db = TestSession()
        try:
            pos = db.query(PaperPosition).filter(PaperPosition.portfolio_id == portfolio_id).one()
            assert pos.status == PositionStatus.closed
            assert float(pos.quantity) == 0.0
            # 10 buys at 54.054, 4+6 sells at 53.946 → loss on the round trip.
            assert float(pos.realized_pnl) < 0

            fills = (
                db.query(PaperFill)
                .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
                .filter(PaperOrder.portfolio_id == portfolio_id)
                .all()
            )
            assert len(fills) == 3
            # Commission charged once per fill, never double-counted.
            assert sum(float(f.commission_amount) for f in fills) == pytest.approx(
                sum(float(f.gross_amount) * 0.001 for f in fills), abs=0.01
            )
        finally:
            db.close()

        cash = _cash(portfolio_id)
        db = TestSession()
        try:
            net = float(
                db.query(func.sum(PaperFill.net_cash_change))
                .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
                .filter(PaperOrder.portfolio_id == portfolio_id)
                .scalar()
            )
        finally:
            db.close()
        assert cash == pytest.approx(10000.0 + net, abs=0.01)


class TestPersonalApiProvenance:
    """§6 — scoped key, revocation, no escalation, actor recorded as agent."""

    def _make_key(self, client, headers, scopes):
        resp = client.post(
            "/api/v1/api-keys",
            headers=headers,
            json={"name": "poc-26", "scopes": scopes},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["raw_key"], resp.json()["id"]

    def test_write_key_orders_and_is_recorded_as_agent(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        raw_key, _ = self._make_key(client, auth_headers, ["read", "write"])

        resp = _order(
            client, {"Authorization": f"Bearer {raw_key}"}, portfolio_id, quantity=5,
            idempotency_key="api-1",
        )
        assert resp.status_code == 201, resp.text
        # A verified API key is an agent credential; "api_key" was the legacy label.
        assert resp.json()["actor_type"] == "agent"

        db = TestSession()
        try:
            event = (
                db.query(AuditEvent)
                .filter(AuditEvent.event_type == "order_created")
                .one()
            )
            assert event.actor_type == "agent"
            assert event.data_cutoff_at == "2024-02-13"
            assert event.payload_json["idempotency_key"] == "api-1"
        finally:
            db.close()

    def test_read_only_key_cannot_place_orders(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        raw_key, _ = self._make_key(client, auth_headers, ["read"])

        resp = _order(client, {"Authorization": f"Bearer {raw_key}"}, portfolio_id)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["error"] == "insufficient_scope"
        assert resp.json()["detail"]["required_scope"] == "write"
        assert _counts(portfolio_id) == (0, 0)

    def test_revoked_key_is_rejected(self, client, auth_headers, pg_url):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        raw_key, key_id = self._make_key(client, auth_headers, ["read", "write"])

        revoke = client.delete(f"/api/v1/api-keys/{key_id}", headers=auth_headers)
        assert revoke.status_code == 204, revoke.text

        resp = _order(client, {"Authorization": f"Bearer {raw_key}"}, portfolio_id)
        assert resp.status_code == 401, resp.text

    def test_key_cannot_mint_another_key(self, client, auth_headers, pg_url):
        raw_key, _ = self._make_key(client, auth_headers, ["read"])
        resp = client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={"name": "escalation", "scopes": ["read", "write", "admin"]},
        )
        assert resp.status_code == 403, resp.text
        assert "interactive login" in resp.json()["detail"]

    def test_keys_are_stored_hashed_not_plain(self, client, auth_headers, pg_url):
        raw_key, key_id = self._make_key(client, auth_headers, ["read"])
        db = TestSession()
        try:
            stored = db.query(ApiKey).filter(ApiKey.id == key_id).one()
            assert stored.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
            assert raw_key not in stored.key_hash
            assert stored.key_prefix == raw_key[:8]
        finally:
            db.close()

    def test_second_user_cannot_write_into_first_users_portfolio(
        self, client, auth_headers, pg_url
    ):
        portfolio_id = _portfolio(client, auth_headers, 10000.0)
        cash_before = _cash(portfolio_id)

        client.post("/api/auth/register", json={
            "risk_acknowledged": True,
            "email": "intruder@alethra.io",
            "password": "anotherpass123",
            "display_name": "Intruder",
            "user_type": "individual",
        })
        login = client.post("/api/auth/login", json={
            "email": "intruder@alethra.io",
            "password": "anotherpass123",
        })
        intruder = {"Authorization": f"Bearer {login.json()['access_token']}"}

        resp = _order(client, intruder, portfolio_id, quantity=5)
        assert resp.status_code == 403, resp.text
        assert _cash(portfolio_id) == pytest.approx(cash_before)
        assert _counts(portfolio_id) == (0, 0)


class TestSimulationAdvancePersistsOnPostgres:
    """Regression for the numpy-scalar leak on the rule-triggered advance path.

    ``simulator.step`` prices off a pandas DataFrame, so paper_cash / paper_equity
    and the position dict arrive as ``np.float64``. SQLite coerces them silently;
    psycopg2 renders ``np.float64(...)`` literally and aborts the session UPDATE
    with ``InvalidSchemaName: schema "np" does not exist``. The paper-ORDER path
    is covered above; this covers the SIMULATION-advance path, which is a separate
    write and was not caught by the SQLite-only rule-execution regression.
    """

    def _seed_january_bars(self):
        db = TestSession()
        try:
            for date_str, prices in DEMO_DATA["DEMO-ALPHA"].items():
                if not date_str.startswith("2024-01"):
                    continue
                d = date.fromisoformat(date_str)
                db.add(MarketDataBar(
                    instrument_id="DEMO-ALPHA",
                    provider_id="demo",
                    bar_date=d,
                    bar_timestamp=datetime.combine(d, datetime.min.time()),
                    open=prices["open"],
                    high=prices["high"],
                    low=prices["low"],
                    close=prices["close"],
                    volume=1_000_000,
                ))
            db.commit()
        finally:
            db.close()

    def _locked_version(self, client, headers):
        resp = client.post("/api/v1/experiments/", headers=headers, json={
            "title": "PG rule-triggered advance",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
                "risk_parameters": {"stop_pct": 0.05},
            },
        })
        assert resp.status_code == 201, resp.text
        exp = resp.json()
        lock = client.put(
            f"/api/v1/experiments/{exp['id']}", headers=headers,
            json={"scope": {"status": "locked"}},
        )
        assert lock.status_code == 200, lock.text
        return exp["id"], exp["current_version_id"]

    def test_rule_stop_advance_writes_float_equity_not_numpy(self, client, auth_headers, pg_url):
        self._seed_january_bars()
        _exp_id, version_id = self._locked_version(client, auth_headers)

        sess = client.post(
            f"/api/v1/simulations/versions/{version_id}", headers=auth_headers,
            json={"initial_virtual_cash": 10000},
        )
        assert sess.status_code == 201, sess.text
        session_id = sess.json()["id"]

        pf = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000, "currency": "USD",
            "simulation_session_id": session_id,
        })
        assert pf.status_code == 201, pf.text
        portfolio_id = pf.json()["id"]

        # Advance until the declared stop fires (or the replay completes). A 500
        # here is exactly the numpy leak: on PostgreSQL the session UPDATE aborts.
        saw_stop = False
        for _ in range(40):
            adv = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
            if adv.status_code == 400:
                break
            assert adv.status_code == 200, adv.text
            detail = (adv.json().get("decision") or {}).get("decision_detail") or {}
            if detail.get("side") == "SELL" and detail.get("exit_reason") == "stop":
                saw_stop = True
                break
        assert saw_stop, "the declared 5% stop must fire over the January decline"

        # The session row persisted with a finite plain-float equity, not np.float64.
        db = TestSession()
        try:
            session = db.query(SimulationSession).filter(SimulationSession.id == session_id).one()
            assert isinstance(float(session.paper_equity), float)
            assert session.paper_equity == pytest.approx(float(session.paper_equity))
            assert session.total_decisions >= 1
            assert db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).count() >= 1

            # The stop traded the account as a system actor, with a rule_stop fill.
            orders = (
                db.query(PaperOrder).filter(PaperOrder.portfolio_id == portfolio_id).all()
            )
            sells = [o for o in orders if o.side == "SELL"]
            assert sells, "the stop must book a SELL order on the linked portfolio"
            assert all(o.actor_type == "system" for o in orders)
            sell_fills = (
                db.query(PaperFill)
                .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
                .filter(PaperOrder.portfolio_id == portfolio_id)
                .all()
            )
            assert any(f.simulated_fill_reason == "rule_stop" for f in sell_fills), (
                [f.simulated_fill_reason for f in sell_fills]
            )
        finally:
            db.close()


@pytest.fixture()
def _authored_session_bars(setup_db):
    """Seed the five stored bars the imported session-lane helpers bind to.

    Scoped to the class below rather than autouse for the whole file: the rest of
    this file drives the direct lane off the demo fixture, and this suite's own
    five February bars are a different dataset.
    """
    db = TestSession()
    try:
        seed_authored_bars(db)
    finally:
        db.close()


def _due_sequence(portfolio_id):
    """``(order_id, created_at)`` in the order ``execute_due_orders`` will walk them.

    Read before the market clock runs, so which trade gets the cash is reported by
    the product's own sort key (``created_at, id``) instead of being assumed from
    the order the HTTP requests happened to arrive in. The timestamp travels with
    the id so the caller can prove the two submissions are separable by it: were
    they ever stamped the same instant, the tie-break would be the random UUID and
    the recorded winner would be luck rather than the contract.
    """
    db = TestSession()
    try:
        return [
            (str(o.id), o.created_at)
            for o in db.query(PaperOrder)
            .filter(
                PaperOrder.portfolio_id == portfolio_id,
                PaperOrder.status == OrderStatus.pending,
            )
            .order_by(PaperOrder.created_at, PaperOrder.id)
            .all()
        ]
    finally:
        db.close()


@pytest.mark.usefixtures("_authored_session_bars")
class TestTwoDueOrdersShareOneCashBalance:
    """Two human intents reach one cash balance at the same bar; only one fits.

    The expected behaviour is written out from the product's own code before any
    assertion, because the outcome below follows from it rather than from a guess:

    1. Submission reserves nothing. ``_submit_manual_order`` books no cash, no
       position and no fill (backend/app/api/simulations.py:1650-1660), and its
       cash check is an *estimate* against the balance the account holds right now
       (simulations.py:1747-1762) - never against the balance other pending orders
       have already promised. Two BUYs that are each affordable on their own are
       therefore both accepted, by design.
    2. Affordability is execution-time truth. The route states it in the warning it
       chooses not to raise when the estimate passes: the order is priced at the
       executing bar's open, and 「現金不足時掛單會被拒絕（rejected），不會部分成交」
       (simulations.py:1757-1762).
    3. The market clock re-reads the account under ``FOR UPDATE`` and walks the due
       orders in ``created_at, id`` order, mutating ``current_virtual_cash`` in
       place between them (backend/app/services/order_lifecycle.py:273-289, 295-329).
       There is no batch pre-check against the opening balance: the second order is
       judged against the cash the first one actually left behind.
    4. A refusal belongs to one order. ``book_fill`` raises before touching cash or
       a position (backend/app/services/paper_execution.py:90-92), and
       ``execute_due_orders`` turns that into ``rejected`` +
       ``risk_check_status=failed`` for that order alone and keeps going
       (order_lifecycle.py:330-345). The earlier fill is not rolled back, and the
       refused order is neither cancelled nor silently dropped.

    So with 3000.0 in the account and two BUY-20 intents due at B2's open
    (2206.6044 each), exactly one can trade and the other must be refused - which
    one is decided by the sequence in (3), read here from the database rather than
    hardcoded. What is asserted is the invariants: no negative cash, no second copy
    of the trade, and both books plus the journal agreeing with what happened.
    """

    CASH = 3000.0
    QUANTITY = 20

    def test_the_second_due_order_is_refused_against_the_remaining_cash(
        self, client, auth_headers, pg_url
    ):
        ctx = _open_account(client, auth_headers, observed=1, portfolio_cash=self.CASH)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        first = _submit(client, auth_headers, session_id, action="buy",
                        quantity=self.QUANTITY, key="pg-two-first")
        assert first.status_code == 200, first.text
        second = _submit(client, auth_headers, session_id, action="buy",
                         quantity=self.QUANTITY, key="pg-two-second")
        assert second.status_code == 200, second.text

        # (1) Both intents were accepted and neither warned about cash: each was
        # estimated at 2106.3042 against the same untouched 3000.0, because nothing
        # had been reserved for the first.
        assert BUY20_B1_ESTIMATE_COST * 2 > self.CASH, (
            "the fixture no longer funds only one order - this case proves nothing"
        )
        submitted = [d for d in replay_decisions(client, auth_headers, session_id)
                     if (d["decision_detail"] or {}).get("order")]
        assert len(submitted) == 2
        assert all(not d["warnings"] for d in submitted), [d["warnings"] for d in submitted]
        assert [o["status"] for o in account_orders(client, auth_headers, portfolio_id)] == \
            ["pending", "pending"]
        # Submitting moved no money on either book.
        assert _cash(portfolio_id) == pytest.approx(self.CASH, abs=1e-9)

        due_rows = _due_sequence(portfolio_id)
        assert len(due_rows) == 2
        assert len({stamp for _, stamp in due_rows}) == 2, (
            f"both orders share one created_at ({due_rows}); the executor's ordering "
            "would then fall to the UUID tie-break, not to submission order"
        )
        due = [order_id for order_id, _ in due_rows]

        advanced = _advance(client, auth_headers, session_id)
        assert advanced.status_code == 200, advanced.text

        # (2) No negative cash, and the refusal is arithmetic rather than a choice:
        # the balance left is one fill's net, and it is short of a second copy.
        cash = _cash(portfolio_id)
        assert cash >= 0.0, f"cash went negative: {cash}"
        assert cash == pytest.approx(self.CASH + BUY20_B2_NET, abs=1e-9)
        assert cash < -BUY20_B2_NET, (
            f"{cash} should not afford a second {BUY20_B2_NET} order"
        )

        # (3) No duplicate execution: two orders, one fill row, and a position of
        # 20 shares - not the 40 two fills would have produced.
        assert _counts(portfolio_id) == (2, 1)
        assert open_qty(client, auth_headers, portfolio_id) == pytest.approx(
            float(self.QUANTITY)
        )

        # (4) Order state matches the result, and the winner is the order the clock
        # walked first - recorded, not presupposed.
        statuses = {o["id"]: o["status"] for o in
                    account_orders(client, auth_headers, portfolio_id)}
        assert statuses == {due[0]: "filled", due[1]: "rejected"}, (
            f"executor walked {due} against {self.CASH} and produced {statuses}"
        )

        winner, loser = due[0], due[1]
        fill, = fills(client, auth_headers, portfolio_id, winner)
        assert fill["execution_date"] == B2
        assert fill["quantity"] == pytest.approx(float(self.QUANTITY))
        assert fill["net_cash_change"] == pytest.approx(BUY20_B2_NET, abs=1e-9)
        assert fills(client, auth_headers, portfolio_id, loser) == []

        # Both books say the same thing about each order.
        assert _order_view(client, auth_headers, session_id, winner)["execution"]["state"] == \
            "filled"
        rejected_view = _order_view(client, auth_headers, session_id, loser)["execution"]
        assert rejected_view["state"] == "rejected"
        assert rejected_view["fill_count"] == 0
        assert rejected_view["not_executed_reason"] == "failed"

        # The refusal is on the record with its reason, and only for that order.
        blocked = events_for("manual_orders_blocked", session_id)
        assert len(blocked) == 1
        assert [b["order_id"] for b in blocked[0].payload_json["blocked"]] == [loser]
        assert blocked[0].payload_json["blocked"][0]["blocked_reason"] == \
            "Insufficient cash for order"
        assert blocked[0].payload_json["blocked"][0]["executed"] is False

        # The trade that happened emitted exactly one execution event, carrying the
        # balance the row now holds - so no batch rollback undid it either.
        executed = events_for("paper_order_executed", portfolio_id)
        assert len(executed) == 1
        assert executed[0].payload_json["order_id"] == winner
        assert executed[0].payload_json["cash_after"] == pytest.approx(cash, abs=1e-9)

        # (5) The journal matches the outcome: a fill row for the trade that moved
        # money, an order row with no fill columns for the trade that did not.
        journal = journal_json(client, auth_headers, portfolio_id)
        assert journal["row_count"] == len(journal["records"]) == 2
        by_order = {}
        for row in journal["records"]:
            by_order.setdefault(row["order_id"], []).append(row)
        assert sorted(r["record_type"] for r in by_order[winner]) == ["fill"]
        assert sorted(r["record_type"] for r in by_order[loser]) == ["order"]
        assert by_order[winner][0]["order_status"] == "filled"
        assert by_order[winner][0]["net_cash_change"] == pytest.approx(
            BUY20_B2_NET, abs=1e-9
        )
        assert by_order[loser][0]["order_status"] == "rejected"
        assert by_order[loser][0]["net_cash_change"] is None, (
            "the refused order must not carry money in the journal"
        )
        assert sum(float(r["net_cash_change"] or 0.0) for r in journal["records"]) == \
            pytest.approx(BUY20_B2_NET, abs=1e-9)

        csv_rows = journal_csv_rows(client, auth_headers, portfolio_id)
        assert {r["order_id"] for r in csv_rows} == {winner, loser}
        assert {r["record_type"] for r in csv_rows} == {"fill", "order"}

        # (6) The replay adopted the same single trade, by the ledger's own figure.
        steps = [d for d in replay_decisions(client, auth_headers, session_id)
                 if (d["decision_detail"] or {}).get("manual_fills")]
        assert len(steps) == 1
        assert [f["order_id"] for f in steps[0]["decision_detail"]["manual_fills"]] == \
            [winner]
        assert float(steps[0]["paper_cash"]) == pytest.approx(
            INITIAL_CASH + BUY20_B2_NET, abs=1e-6
        )

        # Which order won is an observation, so print it: a passing assertion alone
        # would leave the recorded sequence out of the run log.
        print(
            "[affordability] walked "
            + ", ".join(f"{oid}@{stamp.isoformat()}" for oid, stamp in due_rows)
            + f" | filled={winner} refused={loser}"
            + f" | cash {self.CASH} -> {cash} (one net of {BUY20_B2_NET})"
        )
