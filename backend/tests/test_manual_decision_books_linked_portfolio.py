"""One paper trade is a lifecycle, not a request.

The account used to book a fill inside the request that created the order: cash,
position and a journal row appeared at submit time. Three things followed from
that — a pending intent read as a holding, a decision made after the last bar the
user watched got crammed into that same bar, and a retry or a restart could book
one intent twice. The contract the owner set instead:

    交易提交 → pending／等待 → 市場開市／可執行 → 真實執行 → fills／settlement
             → portfolio cash／position → journal／CSV

Every test here drives the real HTTP routes — ``POST /manual-decision`` to submit,
``POST /advance`` as the market clock, ``POST /paper-portfolio/{id}/orders/{id}/cancel``
to withdraw — and then reads *both* books back: the replay's own decision rows and
the account's orders, fills, positions, cash, journal and CSV. What is asserted is
not "a row exists" but that a submission moves nothing, that execution moves each
book exactly once at the bar the market actually reached, and that an operation
which cannot complete on both halves completes on neither.

The dataset is authored here rather than borrowed, on purpose: five stored daily
bars with ``open != previous close`` and a weekend gap, so a path that priced a
fill off the bar the user was watching, or off a fixture nobody bound, fails
visibly instead of matching by coincidence.

    2024-02-01 (Thu)  open 100.00  close 105.00   B1
    2024-02-02 (Fri)  open 110.00  close 108.00   B2  <- first window after B1
    2024-02-05 (Mon)  open 112.00  close 115.00   B3  <- first window after B2,
                                                        settlement skips the weekend
    2024-02-06 (Tue)  open 118.00  close 117.00   B4
    2024-02-07 (Wed)  open 120.00  close 121.00   B5  (kept for settlement lookups)

The strategy is SMA(2,60) over five bars, which never fires: warm-up needs 60
bars. So every order in this file is a human intent, and a rule trade cannot be
mistaken for one.

Prices and costs are stated as literals derived from the fixture, using one
convention declared once in this file's comments: slippage is inside the booked
price, commission is charged on the gross at that price. The expected figures
therefore come from the test's own arithmetic, not from a helper that mirrors the
product.

Passing here proves the lifecycle mechanics work on synthetic data. It is NOT
evidence about real markets or a real broker: bars are authored, fills are
simulated, and live paper trading remains separately BLOCKED on provider and
broker access.
"""

import csv
import io
import threading
import uuid
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.order_lifecycle import SETTLEMENT_POLICY_LABEL
from tests.conftest import TEST_ENGINE, TestSession

SYMBOL = "DEMO-ALPHA"
PROVIDER = "demo"
INITIAL_CASH = 100_000.0
COMMISSION_RATE = 0.001
SLIPPAGE_RATE = 0.002

B1, B1_OPEN, B1_CLOSE = "2024-02-01", 100.0, 105.0
B2, B2_OPEN, B2_CLOSE = "2024-02-02", 110.0, 108.0
B3, B3_OPEN, B3_CLOSE = "2024-02-05", 112.0, 115.0
B4, B4_OPEN, B4_CLOSE = "2024-02-06", 118.0, 117.0
B5, B5_OPEN, B5_CLOSE = "2024-02-07", 120.0, 121.0
BARS = [
    (B1, B1_OPEN, B1_CLOSE),
    (B2, B2_OPEN, B2_CLOSE),
    (B3, B3_OPEN, B3_CLOSE),
    (B4, B4_OPEN, B4_CLOSE),
    (B5, B5_OPEN, B5_CLOSE),
]

# A BUY of 20 executed at B2's open, priced by this file's declared convention:
#   booked price = round(110.0 * (1 + 0.002), 4)          = 110.22
#   gross        = round(20 * 110.22, 4)                  = 2204.40
#   commission   = round(2204.40 * 0.001, 4)              = 2.2044
#   net cash     = -(gross + commission)                  = -2206.6044
BUY20_B2_PRICE = 110.22
BUY20_B2_GROSS = 2204.40
BUY20_B2_COMMISSION = 2.2044
BUY20_B2_SLIPPAGE = 4.40
BUY20_B2_NET = -2206.6044

# A SELL of 8 at B3's open: 112 * (1 - 0.002) = 111.776, gross 894.208,
# commission 0.8942, net +(gross - commission) = 893.3138.
SELL8_B3_PRICE = 111.776
SELL8_B3_GROSS = 894.208
SELL8_B3_COMMISSION = 0.8942
SELL8_B3_NET = 893.3138

# A SELL of the remaining 12 at B4's open: 118 * 0.998 = 117.764, gross
# 1413.168, commission 1.4132, net 1411.7548.
SELL12_B4_PRICE = 117.764
SELL12_B4_GROSS = 1413.168
SELL12_B4_COMMISSION = 1.4132
SELL12_B4_NET = 1411.7548

# A BUY of 20 that waited past the weekend and filled at B3's open: 112 * 1.002
# = 112.224, gross 2244.48, commission 2.2445, net -2246.7245.
BUY20_B3_PRICE = 112.224
BUY20_B3_GROSS = 2244.48
BUY20_B3_COMMISSION = 2.2445
BUY20_B3_NET = -2246.7245

# The cost a submit-time estimate quotes off B1's close: 20 * 105 * 1.002 * 1.001
# = 2106.3042. It is an estimate — B2's open books 2206.6044 for the same order.
BUY20_B1_ESTIMATE_COST = 2106.3042

# A BUY of 20 that filled on B5, the dataset's last bar: 120 * 1.002 = 120.24,
# gross 2404.80, commission 2.4048, net -2407.2048.
BUY20_B5_PRICE = 120.24
BUY20_B5_GROSS = 2404.80
BUY20_B5_COMMISSION = 2.4048
BUY20_B5_NET = -2407.2048

# Columns the export writes as decimals. "110" and "110.0" are the same money, so
# these compare by value (exactly — a truncating formatter must still fail); every
# other column must match the JSON cell character for character.
_MONEY_COLUMNS = {
    "observed_price",
    "execution_price_before_costs",
    "slippage_amount",
    "commission_amount",
    "quantity",
    "gross_amount",
    "net_cash_change",
}

# SQLite cannot prove row-level locking: it has no concurrent writers. These cases
# skip there rather than pretending to pass.
_IS_SQLITE = TEST_ENGINE.dialect.name == "sqlite"
postgres_only = pytest.mark.skipif(
    _IS_SQLITE,
    reason="row-lock serialisation requires a real PostgreSQL backend",
)


def seed_authored_bars(db):
    """Write the five stored bars this suite's sessions bind to.

    Exposed as a function rather than only a fixture so the PostgreSQL-only
    integration file drives the same dataset through the same session lane;
    duplicating the prices there would let the two files drift apart on the very
    numbers their assertions are derived from.
    """
    from app.models.market_data import MarketDataBar

    for date_str, open_price, close_price in BARS:
        day = date.fromisoformat(date_str)
        db.add(MarketDataBar(
            instrument_id=SYMBOL,
            provider_id=PROVIDER,
            bar_date=day,
            bar_timestamp=datetime.combine(day, datetime.min.time()),
            open=open_price,
            high=max(open_price, close_price) + 0.5,
            low=min(open_price, close_price) - 0.5,
            close=close_price,
            volume=1_000_000,
        ))
    db.commit()


@pytest.fixture(autouse=True)
def _seed_bound_dataset(setup_db):
    """Write the five stored bars every session here is bound to."""
    db = TestSession()
    try:
        seed_authored_bars(db)
    finally:
        db.close()


def _locked_version(client, headers, *, commission_rate=COMMISSION_RATE,
                    slippage_rate=SLIPPAGE_RATE):
    """Draft and lock an SMA(2,60) spec over the whole authored window.

    The rates travel in the frozen spec: the replay, the executor and the account
    all price from it, so a test states its cost assumption once.
    """
    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": "Order lifecycle over a bound dataset",
        "research_type": "quantitative",
        "strategy_spec": {
            "template_type": "sma_crossover",
            "asset_ids": [SYMBOL],
            "start_date": B1,
            "end_date": B5,
            "initial_cash": INITIAL_CASH,
            "commission_rate": commission_rate,
            "slippage_rate": slippage_rate,
            "signal_parameters": {"short_window": 2, "long_window": 60},
        },
    })
    assert resp.status_code == 201, resp.text
    experiment = resp.json()
    lock = client.put(
        f"/api/v1/experiments/{experiment['id']}", headers=headers,
        json={"scope": {"status": "locked"}},
    )
    assert lock.status_code == 200, lock.text
    return experiment["current_version_id"]


def _start_session(client, headers, version_id, *, cash=INITIAL_CASH):
    resp = client.post(
        f"/api/v1/simulations/versions/{version_id}", headers=headers,
        json={"initial_virtual_cash": cash, "provider_id": PROVIDER},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link_portfolio(client, headers, session_id, *, cash=INITIAL_CASH):
    resp = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
        "simulation_session_id": session_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _advance(client, headers, session_id):
    return client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)


def _submit(client, headers, session_id, *, action, quantity=None, key=None,
            rationale="a stated thesis"):
    return client.post(
        f"/api/v1/simulations/{session_id}/manual-decision", headers=headers,
        json={
            "action": action,
            "quantity": quantity,
            "rationale": rationale,
            "idempotency_key": key or f"sub-{uuid.uuid4()}",
        },
    )


def _open_account(client, headers, *, observed=1, portfolio_cash=INITIAL_CASH,
                  replay_cash=INITIAL_CASH, **version_kwargs):
    """Version → session → bound account → watch ``observed`` bars.

    Every lifecycle test needs a session that has observed at least one bar,
    because a submission without an observed market time is refused rather than
    guessed at.
    """
    version_id = _locked_version(client, headers, **version_kwargs)
    session_id = _start_session(client, headers, version_id, cash=replay_cash)
    portfolio_id = _link_portfolio(client, headers, session_id, cash=portfolio_cash)
    for _ in range(observed):
        resp = _advance(client, headers, session_id)
        assert resp.status_code == 200, resp.text
    return {"version_id": version_id, "session_id": session_id, "portfolio_id": portfolio_id}


def _order_view(client, headers, session_id, order_id):
    """The lifecycle view of one order, read from the replay's own endpoint."""
    resp = client.get(f"/api/v1/simulations/{session_id}/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        if item["order"]["id"] == order_id:
            return item
    raise AssertionError(f"order {order_id} not in {resp.json()}")


def session_orders(client, headers, session_id):
    resp = client.get(f"/api/v1/simulations/{session_id}/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def account_orders(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def fills(client, headers, portfolio_id, order_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def all_fills(client, headers, portfolio_id):
    legs = []
    for order in account_orders(client, headers, portfolio_id):
        legs.extend(fills(client, headers, portfolio_id, order["id"]))
    return legs


def positions(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def open_qty(client, headers, portfolio_id):
    return sum(float(p["quantity"]) for p in positions(client, headers, portfolio_id)
               if p["status"] == "open")


def account(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def replay_decisions(client, headers, session_id):
    resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def journal_json(client, headers, portfolio_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/journal?format=json", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def journal_csv_rows(client, headers, portfolio_id):
    from app.api.paper_portfolio import JOURNAL_COLUMNS

    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/journal?format=csv", headers=headers
    )
    assert resp.status_code == 200, resp.text
    parsed = list(csv.DictReader(io.StringIO(resp.text)))
    assert parsed and list(parsed[0].keys()) == JOURNAL_COLUMNS, parsed[0].keys()
    return parsed


def events_for(event_type, entity_id=None):
    from app.models.audit_event import AuditEvent

    db = TestSession()
    try:
        query = db.query(AuditEvent).filter(AuditEvent.event_type == event_type)
        if entity_id is not None:
            query = query.filter(AuditEvent.entity_id == str(entity_id))
        return query.all()
    finally:
        db.close()


def db_counts(**models):
    db = TestSession()
    try:
        return {name: db.query(model).count() for name, model in models.items()}
    finally:
        db.close()


def cash_moved_by(legs):
    return sum(float(leg["net_cash_change"]) for leg in legs)


class TestSubmissionIsPendingNotExecuted:
    """A submitted order is an intent the market has not answered yet."""

    def test_a_buy_writes_a_pending_order_and_moves_no_money_anywhere(
        self, client, auth_headers
    ):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # The response itself must say pending, not show a fill.
        assert body["fill"] is None, body["fill"]
        assert body["order"]["status"] == "pending"
        assert body["order"]["booked_at_submit"] is False
        assert body["order"]["decided_on_cutoff"] == B1
        assert body["rejection"]["reason"] == "awaiting_execution_window"
        assert body["rejection"]["booked_at_submit"] is False
        assert body["execution"]["state"] == "pending"
        assert body["execution"]["filled_quantity"] == 0
        assert body["execution"]["fill_count"] == 0
        assert body["execution"]["blockers"] == []
        # ...and name the window it waits for, instead of leaving the user to infer
        # it from a missing position.
        assert body["execution"]["next_window"]["date"] == B2
        assert body["execution"]["next_window"]["open"] == B2_OPEN

        orders = account_orders(client, auth_headers, portfolio_id)
        assert [o["status"] for o in orders] == ["pending"]
        assert orders[0]["actor_type"] == "human"
        assert orders[0]["side"] == "BUY"
        assert orders[0]["quantity"] == 20
        assert orders[0]["data_cutoff_at"] == B1
        assert orders[0]["model_or_strategy_version_id"] == ctx["version_id"]

        assert fills(client, auth_headers, portfolio_id, orders[0]["id"]) == []
        assert positions(client, auth_headers, portfolio_id) == []
        # The user must not be able to read this as a holding.
        assert open_qty(client, auth_headers, portfolio_id) == 0
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

        # One submission, one of each row — and no fill row anywhere.
        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 1, "fills": 0, "journal": 1
        }

    def test_a_submit_before_any_bar_is_observed_is_refused(
        self, client, auth_headers
    ):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        # No market time observed ⇒ no decision clock. Inventing the start date
        # would stamp a decision on a bar the replay then treats as consumed.
        assert "advance" in resp.json()["detail"] or "先推進重放" in resp.json()["detail"]

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

    def test_a_hold_is_a_decision_and_not_an_account_event(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)

        resp = _submit(client, auth_headers, ctx["session_id"], action="hold")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["order"] is None
        assert body["rejection"] is None
        assert body["fill"] is None
        assert body["decision"]["decision_type"] == "no_action"

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        assert session_orders(client, auth_headers, ctx["session_id"]) == []
        assert account(client, auth_headers, ctx["portfolio_id"])["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

    def test_the_replay_book_is_copied_by_a_submission_not_moved(
        self, client, auth_headers
    ):
        """Neither book moves at submit — the replay's figures included.

        The replay used to write a filled paper position into its own decision the
        moment a user clicked, so the workspace showed a holding the ledger had
        never bought. The submission now copies the book as it stands.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        before = account(client, auth_headers, ctx["portfolio_id"])["current_virtual_cash"]

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        decision = resp.json()["decision"]

        assert decision["decision_detail"]["book"]["moved_by_this_submission"] is False
        assert decision["decision_detail"]["booked_at_submit"] is False
        assert float(decision["paper_cash"]) == pytest.approx(INITIAL_CASH, abs=1e-6)
        assert float(decision["paper_cash"]) == pytest.approx(
            float(decision["paper_equity"]), abs=1e-6
        )
        assert decision["paper_position"] in (None, {})
        assert account(client, auth_headers, ctx["portfolio_id"])["current_virtual_cash"] == \
            pytest.approx(before, abs=1e-9)

        # And the cursor never moved: the submission is not an advance.
        session = resp.json()["session"]
        assert session["current_snapshot_date"] == B1
        assert session["observed_days"] == 1


class TestDecisionAndExecutionClocksAreSeparate:
    """When the user decided and when the market executed are two quantities."""

    def test_an_order_waits_for_the_first_bar_after_the_one_the_user_watched(
        self, client, auth_headers
    ):
        """A Friday decision executes on Monday's open, not Friday's.

        B2 is the last bar observed and B3 the next stored one, with a weekend in
        between: the window comes from the bound dataset's own calendar, so it
        cannot be squeezed into the bar the user already watched and cannot land on
        a calendar date the data has no bar for.
        """
        ctx = _open_account(client, auth_headers, observed=2)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["execution"]["next_window"]["date"] == B3
        assert body["execution"]["next_window"]["open"] == B3_OPEN

        advanced = _advance(client, auth_headers, session_id)
        assert advanced.status_code == 200, advanced.text

        order_id = body["order"]["order_id"]
        fill, = fills(client, auth_headers, portfolio_id, order_id)
        assert fill["execution_date"] == B3
        # Priced off the window the order actually waited for, not B2's close and
        # not the bar the user was watching.
        assert fill["observed_price"] == pytest.approx(B3_OPEN, abs=1e-9)
        assert fill["execution_price_before_costs"] == pytest.approx(BUY20_B3_PRICE, abs=1e-9)

        view = _order_view(client, auth_headers, session_id, order_id)["execution"]
        assert view["state"] == "filled"
        assert view["executed"]["execution_date"] == B3
        # Settlement is one *stored* session later — Monday's fill settles Tuesday.
        assert view["executed"]["settlement_date"] == B4

    def test_the_four_clocks_are_recorded_apart(self, client, auth_headers):
        """Decision, order creation, execution and settlement each have a stamp.

        A single timestamp for all four is what made a pending order indistinguish-
        able from a settled trade in an export. They are four separate values here,
        and they order.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]
        created_at = resp.json()["order"]["order_created_at_utc"]
        assert created_at, resp.json()["order"]

        assert _advance(client, auth_headers, session_id).status_code == 200

        executed_events = events_for("paper_order_executed", portfolio_id)
        assert len(executed_events) == 1
        payload = executed_events[0].payload_json
        submitted = events_for("paper_order_submitted", portfolio_id)[0].payload_json

        decided_on = submitted["decided_on_cutoff"]
        execution_date = payload["execution_date"]
        settlement_date = payload["settlement_date"]
        executed_at = payload["executed_at_utc"]

        assert decided_on == B1
        assert execution_date == B2
        assert settlement_date == B3
        assert {decided_on, execution_date, settlement_date} == {B1, B2, B3}, (
            "the decision, trade and settlement dates collapsed onto one another"
        )
        # The execution instant is a real instant, strictly after the submission.
        assert datetime.fromisoformat(executed_at) > datetime.fromisoformat(created_at)
        assert executed_at != created_at

        fill, = fills(client, auth_headers, portfolio_id, order_id)
        assert fill["execution_date"] == execution_date
        # A daily fill carries no intraday bar time — null, not a fabricated one.
        assert fill["bar_timestamp"] is None
        assert payload["bar_timestamp"] is None

        decision_rows = replay_decisions(client, auth_headers, session_id)
        step = [d for d in decision_rows if (d["decision_detail"] or {}).get("manual_fills")]
        assert len(step) == 1
        assert step[0]["snapshot_date"] == B2


class TestExecutionMovesBothBooksOnce:
    """The market clock runs one ledger, not two books describing one trade."""

    def test_the_fill_at_the_next_open_moves_the_account_and_the_replay(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        order, = account_orders(client, auth_headers, portfolio_id)
        assert order["id"] == order_id
        assert order["status"] == "filled"
        assert order["decision_id"] == resp.json()["order"]["decision_id"]

        fill, = fills(client, auth_headers, portfolio_id, order_id)
        assert fill["execution_date"] == B2
        assert fill["observed_price"] == pytest.approx(B2_OPEN, abs=1e-9)
        assert fill["execution_price_before_costs"] == pytest.approx(BUY20_B2_PRICE, abs=1e-9)
        assert fill["gross_amount"] == pytest.approx(BUY20_B2_GROSS, abs=1e-6)
        assert fill["commission_amount"] == pytest.approx(BUY20_B2_COMMISSION, abs=1e-9)
        assert fill["slippage_amount"] == pytest.approx(BUY20_B2_SLIPPAGE, abs=1e-6)
        assert fill["net_cash_change"] == pytest.approx(BUY20_B2_NET, abs=1e-6)
        # A human decision is not a rule firing.
        assert fill["simulated_fill_reason"] == "manual_buy"

        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

        position, = positions(client, auth_headers, portfolio_id)
        assert position["status"] == "open"
        assert position["quantity"] == 20
        assert position["average_entry_price"] == pytest.approx(BUY20_B2_PRICE, abs=1e-6)

        # The replay's own book spent the ledger's figure, not a recomputed one.
        step = [d for d in replay_decisions(client, auth_headers, session_id)
                if (d["decision_detail"] or {}).get("manual_fills")][0]
        booked = step["decision_detail"]["manual_fills"][0]
        assert booked["order_id"] == order_id
        assert booked["net_cash_change"] == pytest.approx(fill["net_cash_change"], abs=1e-9)
        assert float(step["paper_cash"]) == pytest.approx(
            account(client, auth_headers, portfolio_id)["current_virtual_cash"], abs=1e-6
        ), "the replay and the account report different cash for one trade"
        assert step["paper_position"]["shares"] == open_qty(
            client, auth_headers, portfolio_id
        )

    def test_the_estimate_at_submit_is_an_estimate_and_never_a_booking(
        self, client, auth_headers
    ):
        """Submit quotes a cost off the bar already seen; the bar that fills it decides.

        B1's close (105) and B2's open (110) are deliberately different, so a path
        that booked money from the estimate, or called the estimate the fill price,
        shows up as a mismatch instead of a coincidence.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        decision = replay_decisions(client, auth_headers, session_id)[-1]
        journal_record = [r for r in
                          client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
                                     headers=auth_headers).json()
                          if r["actor_type"] == "human"][0]
        estimate = journal_record["risk_flags"]["cost_estimate"]

        assert estimate["basis"] == "observed_close_estimate_only"
        assert estimate["observed_close"] == pytest.approx(B1_CLOSE, abs=1e-9)
        assert estimate["estimated_cost"] == pytest.approx(BUY20_B1_ESTIMATE_COST, abs=1e-9)
        assert estimate["account_cash"] == pytest.approx(INITIAL_CASH, abs=1e-6)

        # Nothing was booked from it.
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

        assert _advance(client, auth_headers, session_id).status_code == 200
        fill, = fills(client, auth_headers, portfolio_id,
                      account_orders(client, auth_headers, portfolio_id)[0]["id"])
        # The fill price came from the execution bar, and differs from the estimate.
        assert fill["observed_price"] == pytest.approx(B2_OPEN, abs=1e-9)
        assert float(fill["gross_amount"]) != pytest.approx(
            float(estimate["estimated_cost"]), abs=1e-3
        )
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + float(fill["net_cash_change"]), abs=1e-6)

    def test_repeated_processing_of_the_same_bar_books_no_second_fill(
        self, client, auth_headers
    ):
        """Re-running the executor is a no-op, because only pending orders book.

        A retried worker, a replayed request or a second advance landing on the
        same window must not be able to debit the account twice for one intent.
        """
        from app.services.order_lifecycle import execute_due_orders

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        cash_after_route = account(client, auth_headers, portfolio_id)["current_virtual_cash"]

        db = TestSession()
        try:
            from app.models.paper_portfolio import PaperPortfolio

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == uuid.UUID(portfolio_id)
            ).one()
            rerun = execute_due_orders(
                db, portfolio=portfolio, instrument_id=SYMBOL, execution_date=B2,
                observed_open=B2_OPEN, commission_rate=COMMISSION_RATE,
                slippage_rate=SLIPPAGE_RATE, provider_id=PROVIDER,
            )
            db.commit()
        finally:
            db.close()

        assert rerun == [], "the executor booked an order that was no longer pending"
        assert len(fills(client, auth_headers, portfolio_id, order_id)) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(cash_after_route, abs=1e-9)
        assert open_qty(client, auth_headers, portfolio_id) == 20

    def test_a_round_trip_reconciles_from_entry_to_final_settlement(
        self, client, auth_headers
    ):
        """Entry, partial exit and full exit on one clock, with the money closing.

        Three separate execution windows (B2, B3, B4) and the settlement rule at
        the end of the data: the last fill has no later stored session, so its
        settlement is unknown rather than guessed.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        entry = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        entry_id = entry.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        trim = _submit(client, auth_headers, session_id, action="sell", quantity=8)
        assert trim.status_code == 200, trim.text
        assert trim.json()["order"]["quantity"] == 8
        trim_id = trim.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        position, = positions(client, auth_headers, portfolio_id)
        assert position["status"] == "open"
        assert position["quantity"] == 12
        # A partial exit does not restate what the remainder cost.
        assert position["average_entry_price"] == pytest.approx(BUY20_B2_PRICE, abs=1e-6)

        trim_fill, = fills(client, auth_headers, portfolio_id, trim_id)
        assert trim_fill["execution_date"] == B3
        assert trim_fill["execution_price_before_costs"] == pytest.approx(SELL8_B3_PRICE, abs=1e-9)
        assert trim_fill["gross_amount"] == pytest.approx(SELL8_B3_GROSS, abs=1e-6)
        assert trim_fill["commission_amount"] == pytest.approx(SELL8_B3_COMMISSION, abs=1e-9)
        assert trim_fill["net_cash_change"] == pytest.approx(SELL8_B3_NET, abs=1e-6)
        assert trim_fill["simulated_fill_reason"] == "manual_sell"

        # A sell without a quantity means the whole holding, taken from the account.
        full = _submit(client, auth_headers, session_id, action="sell")
        assert full.status_code == 200, full.text
        assert full.json()["order"]["quantity"] == 12
        full_id = full.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        full_fill, = fills(client, auth_headers, portfolio_id, full_id)
        assert full_fill["execution_date"] == B4
        assert full_fill["execution_price_before_costs"] == pytest.approx(SELL12_B4_PRICE, abs=1e-9)
        assert full_fill["net_cash_change"] == pytest.approx(SELL12_B4_NET, abs=1e-6)
        # B5 is still inside the bound dataset, so this fill has a real settlement
        # session; a fill on the last bar has none, proved separately below.
        assert _order_view(client, auth_headers, session_id, full_id)["execution"]["executed"][
            "settlement_date"
        ] == B5

        position, = positions(client, auth_headers, portfolio_id)
        assert position["status"] == "closed"
        assert position["quantity"] == 0
        assert open_qty(client, auth_headers, portfolio_id) == 0

        legs = all_fills(client, auth_headers, portfolio_id)
        assert len(legs) == 3
        closing_cash = account(client, auth_headers, portfolio_id)["current_virtual_cash"]
        assert closing_cash == pytest.approx(INITIAL_CASH + cash_moved_by(legs), abs=1e-6)
        # With no exposure left, the whole cash movement IS realized P&L: two
        # books, one answer, and commission on every leg counted once.
        realized = sum(float(p["realized_pnl"] or 0)
                       for p in positions(client, auth_headers, portfolio_id))
        assert closing_cash - INITIAL_CASH == pytest.approx(realized, abs=1e-6)

    def test_a_fill_on_the_last_bar_leaves_settlement_unknown(
        self, client, auth_headers
    ):
        """No later stored session means no settlement date — not an invented one.

        The only trading calendar the product has is the bound dataset, so a fill
        on its final bar is settled on a day nobody knows. It reads back as null in
        the lifecycle view and an empty cell in the export, with the policy named on
        the row so a reader can tell "unknown" from "zero".
        """
        ctx = _open_account(client, auth_headers, observed=4)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        fill, = fills(client, auth_headers, portfolio_id, order_id)
        assert fill["execution_date"] == B5
        assert fill["execution_price_before_costs"] == pytest.approx(BUY20_B5_PRICE, abs=1e-9)
        assert fill["net_cash_change"] == pytest.approx(BUY20_B5_NET, abs=1e-6)

        executed = _order_view(client, auth_headers, session_id, order_id)["execution"]
        assert executed["executed"]["settlement_date"] is None
        assert executed["settlement_policy"] == SETTLEMENT_POLICY_LABEL

        row = journal_json(client, auth_headers, portfolio_id)["records"][0]
        assert row["settlement_date"] is None
        assert row["settlement_policy"] == SETTLEMENT_POLICY_LABEL
        assert journal_csv_rows(client, auth_headers, portfolio_id)[0]["settlement_date"] == ""


class TestCancelExpiryAndBlockers:
    """An intent can be withdrawn, can outlive its window, and can be blocked."""

    def test_a_cancelled_order_never_fills_on_a_later_bar(self, client, auth_headers):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]

        cancelled = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/cancel",
            headers=auth_headers,
        )
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["order"]["status"] == "cancelled"
        assert body["execution"]["state"] == "cancelled"
        assert body["execution"]["fill_count"] == 0
        assert "next_window" not in body["execution"]

        events = events_for("paper_order_cancelled", portfolio_id)
        assert len(events) == 1
        assert events[0].payload_json["cash_moved"] is False
        assert events[0].payload_json["fill_created"] is False
        assert events[0].payload_json["reason"] == "user_cancelled"

        # The clock keeps running and never resurrects the withdrawn intent.
        for _ in range(3):
            assert _advance(client, auth_headers, session_id).status_code == 200

        assert fills(client, auth_headers, portfolio_id, order_id) == []
        assert positions(client, auth_headers, portfolio_id) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)
        assert open_qty(client, auth_headers, portfolio_id) == 0

    def test_only_a_pending_order_can_be_cancelled(self, client, auth_headers):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        refused = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/cancel",
            headers=auth_headers,
        )
        assert refused.status_code == 400, refused.text
        # Erasing a settled trade is not a cancellation; it needs a counter-order.
        assert "pending" in refused.json()["detail"]

        assert _order_view(client, auth_headers, session_id, order_id)["execution"]["state"] == \
            "filled"
        assert len(fills(client, auth_headers, portfolio_id, order_id)) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

    def test_a_pending_order_with_no_window_left_expires_and_says_so(
        self, client, auth_headers
    ):
        """Dataset exhausted: cancelled, expired on the record, money untouched.

        ``OrderStatus`` has no ``expired`` member, so the order reports ``cancelled``
        while the *expiry* lives on the decision outcome — the two states are kept
        apart rather than blurring an unfilled order into a user-cancelled one.
        """
        from app.models.paper_portfolio import DecisionRecord, OutcomeStatus

        ctx = _open_account(client, auth_headers, observed=5)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["order"]["decided_on_cutoff"] == B5
        assert body["execution"]["blockers"] == ["no_quote_on_window"]
        assert "next_window" not in body["execution"]
        assert "過期" in body["execution"]["waiting"]["reason"]

        exhausted = _advance(client, auth_headers, session_id)
        assert exhausted.status_code == 400, exhausted.text
        assert "simulation complete" in exhausted.json()["detail"]

        view = _order_view(client, auth_headers, session_id, body["order"]["order_id"])
        assert view["order"]["status"] == "cancelled"

        expired_events = events_for("paper_orders_expired", portfolio_id)
        assert len(expired_events) == 1
        assert expired_events[0].payload_json["reason"] == "dataset_exhausted"
        assert expired_events[0].payload_json["cash_moved"] is False
        assert expired_events[0].payload_json["orders"] == [body["order"]["order_id"]]

        db = TestSession()
        try:
            record = db.query(DecisionRecord).filter(
                DecisionRecord.paper_portfolio_id == uuid.UUID(portfolio_id)
            ).one()
            assert record.outcome_status == OutcomeStatus.expired
            assert record.risk_flags["expired_reason"] == "dataset_exhausted"
        finally:
            db.close()

        assert fills(client, auth_headers, portfolio_id, body["order"]["order_id"]) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)
        assert _order_view(client, auth_headers, session_id,
                           body["order"]["order_id"])["execution"]["state"] == "cancelled"

    def test_a_paused_account_holds_a_buy_pending_and_executes_it_after_resume(
        self, client, auth_headers
    ):
        """Pausing stops new exposure; it neither fills nor loses the intent.

        The order survives the paused bar untouched, is reported as blocked with a
        named reason, and executes at the next window once trading resumes — at
        that later bar's price, not the paused bar's.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]

        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers
        ).status_code == 200

        blocked = _advance(client, auth_headers, session_id)
        assert blocked.status_code == 200, blocked.text

        view = _order_view(client, auth_headers, session_id, order_id)
        assert view["order"]["status"] == "pending"
        assert view["execution"]["blockers"] == ["portfolio_paused"]
        assert view["execution"]["next_window"]["date"] == B3
        assert fills(client, auth_headers, portfolio_id, order_id) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

        blocked_events = events_for("manual_orders_blocked", session_id)
        assert len(blocked_events) == 1
        assert blocked_events[0].payload_json["blocked"][0]["blocker"] == "portfolio_paused"

        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/resume", headers=auth_headers
        ).status_code == 200
        assert _advance(client, auth_headers, session_id).status_code == 200

        fill, = fills(client, auth_headers, portfolio_id, order_id)
        assert fill["execution_date"] == B3
        assert fill["observed_price"] == pytest.approx(B3_OPEN, abs=1e-9)
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B3_NET, abs=1e-6)


class TestExecutionRefusalsAreAllOrNothing:
    """A trade that cannot complete on both halves completes on neither."""

    def test_an_unaffordable_buy_is_rejected_at_the_bar_not_at_the_desk(
        self, client, auth_headers
    ):
        """Affordability is execution-time truth, and its failure is silent about nothing.

        The account holds 500 while the replay holds 100000: submit can only
        estimate, so it warns and keeps the intent. When the window opens the
        ledger is the one that says no — and neither book moves.
        """
        ctx = _open_account(client, auth_headers, observed=1, portfolio_cash=500.0)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["order"]["order_id"]
        assert resp.json()["order"]["status"] == "pending"
        decision = replay_decisions(client, auth_headers, session_id)[-1]
        assert decision["warnings"] and any("估算" in w for w in decision["warnings"])
        # The estimate is against the observed close, and it is not a rejection.
        assert decision["decision_detail"]["book"]["paper_cash"] == pytest.approx(
            INITIAL_CASH, abs=1e-6
        )

        assert _advance(client, auth_headers, session_id).status_code == 200

        view = _order_view(client, auth_headers, session_id, order_id)
        assert view["order"]["status"] == "rejected"
        assert view["execution"]["state"] == "rejected"
        assert view["execution"]["fill_count"] == 0
        assert "executed" not in view["execution"]
        assert view["execution"]["not_executed_reason"] == "failed"

        assert fills(client, auth_headers, portfolio_id, order_id) == []
        assert positions(client, auth_headers, portfolio_id) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(500.0, abs=1e-9)
        rejected = [d for d in replay_decisions(client, auth_headers, session_id)
                    if (d["decision_detail"] or {}).get("manual_fills")]
        assert rejected == [], "the replay booked a fill the ledger refused"

        # The refusal is on the record, with its reason, rather than vanishing.
        blocked = events_for("manual_orders_blocked", session_id)
        assert len(blocked) == 1
        assert blocked[0].payload_json["blocked"][0]["blocked_reason"] == \
            "Insufficient cash for order"

    def test_buying_a_second_position_is_refused_at_submit(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        assert _submit(client, auth_headers, session_id, action="buy", quantity=20).status_code == 200
        assert _advance(client, auth_headers, session_id).status_code == 200

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=30)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["order"] is None
        assert body["fill"] is None
        assert body["rejection"]["reason"] == "already_in_position"
        assert body["rejection"]["booked_at_submit"] is False
        assert body["rejection"]["current_shares"] == 20

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 1, "fills": 1, "journal": 1
        }
        assert open_qty(client, auth_headers, portfolio_id) == 20
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

    def test_selling_without_a_position_is_refused_at_submit(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)

        resp = _submit(client, auth_headers, ctx["session_id"], action="sell")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rejection"]["reason"] == "no_position_to_sell"
        assert resp.json()["order"] is None
        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }

    def test_overselling_is_refused_before_the_clock_runs(self, client, auth_headers):
        """Selling 30 of 20 held cannot reach an execution window at all.

        Refusing at submit is not a convenience: at the window the ledger would
        raise ``Cannot sell`` anyway, but the intent would have looked accepted for
        a bar, and a partial sale is legal — so the refusal names the holding.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        assert _submit(client, auth_headers, session_id, action="buy", quantity=20).status_code == 200
        assert _advance(client, auth_headers, session_id).status_code == 200

        resp = _submit(client, auth_headers, session_id, action="sell", quantity=30)
        assert resp.status_code == 200, resp.text
        rejection = resp.json()["rejection"]
        assert rejection["reason"] == "quantity_exceeds_position"
        assert rejection["requested_quantity"] == 30
        assert rejection["current_shares"] == 20
        assert resp.json()["order"] is None

        assert len(account_orders(client, auth_headers, portfolio_id)) == 1
        assert open_qty(client, auth_headers, portfolio_id) == 20

        # A legal partial sell on the same account still books.
        partial = _submit(client, auth_headers, session_id, action="sell", quantity=20)
        assert partial.status_code == 200, partial.text
        assert partial.json()["order"]["status"] == "pending"


class TestRecoveryAndIdempotency:
    """Recoverable, and never twice."""

    def test_a_restarted_process_executes_the_stored_intent(self, client, auth_headers):
        """The lifecycle lives in rows, so a new connection resumes it.

        The submission and the execution are two requests by design; nothing about
        the intent is held in the submitting process. A fresh TestClient — as a
        restarted worker or a reopened tab would be — finds the pending order, and
        its advance fills it from the stored state.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]

        restarted = TestClient(client.app)
        with restarted as fresh:
            pending = fresh.get(
                f"/api/v1/simulations/{session_id}/orders", headers=auth_headers
            ).json()
            assert [p["order"]["id"] for p in pending] == [order_id]
            assert pending[0]["execution"]["state"] == "pending"
            assert pending[0]["execution"]["next_window"]["date"] == B2

            advanced = fresh.post(
                f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
            )
            assert advanced.status_code == 200, advanced.text

        assert _order_view(client, auth_headers, session_id, order_id)["execution"]["state"] == \
            "filled"
        assert len(fills(client, auth_headers, portfolio_id, order_id)) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

    def test_the_same_key_replays_the_original_submission_without_a_second_order(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        payload = {"action": "buy", "quantity": 20, "rationale": "same",
                   "idempotency_key": "dup-1"}
        first = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers, json=payload,
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers, json=payload,
        )
        assert second.status_code == 200, second.text
        assert second.json()["decision"]["id"] == first.json()["decision"]["id"]
        assert second.json()["order"]["order_id"] == first.json()["order"]["order_id"]
        # A replay is still not a fill.
        assert second.json()["fill"] is None
        assert second.json()["execution"]["state"] == "pending"

        assert len(account_orders(client, auth_headers, portfolio_id)) == 1

        # And the replayed intent executes exactly once when the clock reaches it.
        assert _advance(client, auth_headers, session_id).status_code == 200
        order_id = first.json()["order"]["order_id"]
        assert len(fills(client, auth_headers, portfolio_id, order_id)) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

    def test_the_same_key_with_a_different_payload_is_refused(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        payload = {"action": "buy", "quantity": 20, "rationale": "one",
                   "idempotency_key": "conf-1"}
        assert client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers, json=payload,
        ).status_code == 200

        conflicting = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision", headers=auth_headers,
            json={**payload, "quantity": 40},
        )
        assert conflicting.status_code == 409, conflicting.text

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 1, "fills": 0, "journal": 1
        }
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

    def test_a_refusal_replayed_on_its_key_returns_the_same_refusal(
        self, client, auth_headers
    ):
        """A refused operation keeps its own answer.

        The refusal is on the record even though no order exists, so replaying the
        identical request must return that recorded refusal — not a fresh,
        unrelated error from whatever the session looks like now, and not a second
        recorded decision. The refusal is a replay-history decision: it writes no
        paper-portfolio journal row, because nothing was booked.
        """
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder
        from app.models.simulation import SimulationDecision

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        # The bar this session just observed may already have written its own
        # decision, so the refusal is measured against the state it started from.
        before = db_counts(journal=DecisionRecord, session_decisions=SimulationDecision)

        first = _submit(client, auth_headers, session_id, action="sell",
                        key="refused-1", rationale="nothing held yet")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["order"] is None
        assert body["rejection"]["reason"] == "no_position_to_sell"

        replay = _submit(client, auth_headers, session_id, action="sell",
                         key="refused-1", rationale="nothing held yet")
        assert replay.status_code == 200, replay.text
        echoed = replay.json()
        assert echoed["decision"]["id"] == body["decision"]["id"]
        assert echoed["rejection"] == body["rejection"], (
            "the replay answered a different refusal than the recorded one"
        )
        assert echoed["order"] is None
        assert echoed["fill"] is None

        after = db_counts(
            orders=PaperOrder,
            fills=PaperFill,
            journal=DecisionRecord,
            session_decisions=SimulationDecision,
        )
        assert after["orders"] == before.get("orders", 0) == 0
        assert after["fills"] == before.get("fills", 0) == 0
        assert after["journal"] == before["journal"], (
            "a refusal wrote an account journal row"
        )
        assert after["session_decisions"] == before["session_decisions"] + 1, (
            "the refusal and its replay together must record one decision, not two"
        )
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

        # A refusal does not age into a different error. The session is now paused,
        # so a NEW intent is refused outright — but this key's recorded answer is
        # still the original one, because the idempotency check runs before the
        # mutable-state checks.
        assert client.post(
            f"/api/v1/simulations/{session_id}/pause", headers=auth_headers
        ).status_code == 200
        after_state_change = _submit(client, auth_headers, session_id, action="sell",
                                     key="refused-1", rationale="nothing held yet")
        assert after_state_change.status_code == 200, after_state_change.text
        assert after_state_change.json()["rejection"] == body["rejection"]
        assert client.post(
            f"/api/v1/simulations/{session_id}/manual-decision", headers=auth_headers,
            json={"action": "sell", "quantity": None, "rationale": "new intent",
                  "idempotency_key": "a-different-key"},
        ).status_code == 400

    def test_a_ledger_failure_leaves_no_half_applied_operation(
        self, client, auth_headers, monkeypatch
    ):
        """If the money mover blows up, the advance writes nothing at all.

        The failure is injected into the executor's own dependency, so the whole
        request transaction has to roll back: no fill, no moved balance, no
        advanced cursor, and no lost intent — the order is still pending and the
        next advance runs it.
        """
        from app.services import order_lifecycle

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]
        cursor_before = client.get(
            f"/api/v1/simulations/{session_id}", headers=auth_headers
        ).json()["current_snapshot_date"]

        def explode(*args, **kwargs):
            raise RuntimeError("injected ledger failure")

        monkeypatch.setattr(order_lifecycle, "book_fill", explode)

        strict = TestClient(client.app, raise_server_exceptions=False)
        failed = strict.post(
            f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
        )
        assert failed.status_code == 500, failed.text

        assert fills(client, auth_headers, portfolio_id, order_id) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)
        assert positions(client, auth_headers, portfolio_id) == []
        session = client.get(f"/api/v1/simulations/{session_id}", headers=auth_headers).json()
        assert session["current_snapshot_date"] == cursor_before
        assert _order_view(client, auth_headers, session_id, order_id)["execution"]["state"] == \
            "pending"

        # Recovery: the same intent runs on the next advance once the ledger works.
        monkeypatch.undo()
        assert _advance(client, auth_headers, session_id).status_code == 200
        assert len(fills(client, auth_headers, portfolio_id, order_id)) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)


class TestBindingAndProvenance:
    """Which account settles, and who did what to it."""

    def test_a_session_with_no_account_says_so_instead_of_looking_booked(
        self, client, auth_headers
    ):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        assert _advance(client, auth_headers, session_id).status_code == 200

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        assert "沒有連接的模擬帳戶" in resp.json()["detail"]

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        assert session_orders(client, auth_headers, session_id) == []
        # A pure replay stays a pure replay: the cursor advanced, nothing else did.
        assert client.get(
            f"/api/v1/simulations/{session_id}", headers=auth_headers
        ).json()["current_snapshot_date"] == B1

    def test_a_session_bound_to_two_accounts_is_refused_rather_than_guessed(
        self, client, auth_headers
    ):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        first = _link_portfolio(client, auth_headers, session_id)
        second = _link_portfolio(client, auth_headers, session_id)
        assert first != second
        assert _advance(client, auth_headers, session_id).status_code == 200

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        assert "不會替你選一個" in resp.json()["detail"]

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        for portfolio_id in (first, second):
            assert account_orders(client, auth_headers, portfolio_id) == []
            assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
                pytest.approx(INITIAL_CASH, abs=1e-9)

    def test_a_human_submits_and_the_market_clock_executes(self, client, auth_headers):
        """Provenance separates the intent from the fill.

        Counting a human's trade as a rule firing would let the account claim the
        strategy did something it never did, so the actor is recorded on each half
        and read back from both books.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        order_id = resp.json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        order, = account_orders(client, auth_headers, portfolio_id)
        assert order["actor_type"] == "human"
        assert order["id"] == order_id

        records = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers
        ).json()
        assert [r["actor_type"] for r in records] == ["human"]
        assert records[0]["decision_type"] == "entry"
        assert records[0]["simulation_session_id"] == session_id
        assert records[0]["linked_experiment_version_id"] == ctx["version_id"]
        assert records[0]["data_cutoff_at"] == B1
        assert records[0]["risk_flags"]["manual_submission"] is True
        assert records[0]["risk_flags"]["auto_execution"] is False
        assert records[0]["risk_flags"]["booked_at_submit"] is False

        assert events_for("paper_order_submitted", portfolio_id)[0].actor_type == "human"
        executed = events_for("paper_order_executed", portfolio_id)
        assert len(executed) == 1
        assert executed[0].actor_type == "system"
        # The clock stamped the outcome back onto the decision that made the order.
        assert executed[0].payload_json["order_id"] == order_id
        db = TestSession()
        try:
            from app.models.paper_portfolio import DecisionRecord

            record = db.query(DecisionRecord).filter(
                DecisionRecord.paper_portfolio_id == uuid.UUID(portfolio_id)
            ).one()
            assert record.risk_flags["order_status"] == "filled"
            assert record.risk_flags["executed_or_closed_at_utc"]
        finally:
            db.close()

        # No rule fired anywhere in this file's runs: the account holds no system rows.
        assert [o["actor_type"] for o in account_orders(client, auth_headers, portfolio_id)] \
            == ["human"]

    def test_a_retired_account_refuses_new_intents(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        assert client.post(
            f"/api/v1/paper-portfolio/{ctx['portfolio_id']}/retire", headers=auth_headers
        ).status_code == 200

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        assert "不能再提交掛單" in resp.json()["detail"]
        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }

    def test_a_paused_replay_refuses_new_intents(self, client, auth_headers):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        assert client.post(
            f"/api/v1/simulations/{ctx['session_id']}/pause", headers=auth_headers
        ).status_code == 200

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        assert "paused" in resp.json()["detail"]
        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        # And the market clock stops with it: no bar passes while paused.
        assert _advance(client, auth_headers, ctx["session_id"]).status_code == 400

        assert client.post(
            f"/api/v1/simulations/{ctx['session_id']}/resume", headers=auth_headers
        ).status_code == 200
        resumed = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["order"]["status"] == "pending"


class TestJournalAndCsvTellTheLifecycle:
    """The export describes the lifecycle, or it describes nothing."""

    def test_a_pending_order_is_an_open_record_with_no_fill_or_settlement(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        order_id = _submit(client, auth_headers, ctx["session_id"], action="buy",
                           quantity=20).json()["order"]["order_id"]

        rows = journal_json(client, auth_headers, ctx["portfolio_id"])["records"]
        assert len(rows) == 1
        row = rows[0]
        assert row["order_id"] == order_id
        assert row["record_type"] == "order"
        assert row["record_classification"] == "open"
        assert row["order_status"] == "pending"
        assert row["fill_id"] is None
        assert row["execution_date"] is None
        assert row["settlement_date"] is None
        # The policy still travels on every row, so the file reads without the app.
        from app.services.order_lifecycle import SETTLEMENT_POLICY_LABEL

        assert row["settlement_policy"] == SETTLEMENT_POLICY_LABEL
        assert row["data_cutoff_at"] == B1
        assert row["order_created_at_utc"]
        assert row["actor_type"] == "human"
        assert row["net_cash_change"] is None
        assert row["currency"] == "USD"

    def test_the_exported_journal_matches_the_account_after_execution(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        rows = journal_json(client, auth_headers, portfolio_id)["records"]
        assert len(rows) == 1
        row = rows[0]
        assert row["record_type"] == "fill"
        assert row["record_classification"] == "closed"
        assert row["order_status"] == "filled"
        assert row["outcome_status"] == "pending"
        assert row["execution_date"] == B2
        assert row["settlement_date"] == B3
        assert row["bar_timestamp"] is None
        assert row["simulated_fill_reason"] == "manual_buy"
        assert float(row["net_cash_change"]) == pytest.approx(BUY20_B2_NET, abs=1e-6)

        fill, = fills(client, auth_headers, portfolio_id, order_id)
        for column in ("execution_date", "quantity", "gross_amount", "net_cash_change",
                       "commission_amount", "slippage_amount",
                       "execution_price_before_costs"):
            if column == "execution_date":
                assert row[column] == fill[column], column
                continue
            assert float(row[column]) == pytest.approx(float(fill[column]), abs=1e-9), column
        assert row["fill_id"] == fill["id"]

        # The journal's own totals are the account's balance.
        legs = all_fills(client, auth_headers, portfolio_id)
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + cash_moved_by(legs), abs=1e-6)

    def test_the_csv_download_is_the_same_record_as_the_json(
        self, client, auth_headers
    ):
        """Downloaded cells are the same values, not a second description.

        Compares every exported column, including the lifecycle ones (settlement,
        order creation, cutoff) — the columns a reader uses to tell a pending
        intent from a settled trade.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]
        assert _advance(client, auth_headers, session_id).status_code == 200

        payload = journal_json(client, auth_headers, portfolio_id)
        row = payload["records"][0]
        parsed = journal_csv_rows(client, auth_headers, portfolio_id)
        same = [r for r in parsed if r["order_id"] == order_id]
        assert len(same) == 1, parsed

        for column in payload["columns"]:
            value = row[column]
            cell = same[0][column]
            if value is None:
                assert cell == "", column
            elif column in _MONEY_COLUMNS:
                assert float(cell) == float(value), column
            else:
                assert cell == str(value), column
        assert same[0]["settlement_date"] == B3
        assert same[0]["record_type"] == "fill"

    def test_a_cancelled_intent_still_reads_back_as_a_closed_record(
        self, client, auth_headers
    ):
        """Nothing silently vanishes from the journal, not even a withdrawal.

        A cancelled order has no fill, so its row is an ``order`` record with the
        money columns null — a reader can tell "withdrawn" from "never submitted".
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/cancel",
            headers=auth_headers,
        ).status_code == 200

        rows = journal_json(client, auth_headers, portfolio_id)["records"]
        assert len(rows) == 1
        assert rows[0]["record_type"] == "order"
        assert rows[0]["record_classification"] == "closed"
        assert rows[0]["order_status"] == "cancelled"
        assert rows[0]["settlement_date"] is None
        assert rows[0]["net_cash_change"] is None

        csv_row = [r for r in journal_csv_rows(client, auth_headers, portfolio_id)
                   if r["order_id"] == order_id][0]
        assert csv_row["order_status"] == "cancelled"
        assert csv_row["net_cash_change"] == ""


class TestOwnership:
    def test_another_user_cannot_drive_this_replay_or_read_this_book(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]

        client.post("/api/auth/register", json={
            "email": "intruder@alethra.io", "password": "intruderpass123",
            "display_name": "Intruder", "user_type": "individual",
        })
        token = client.post("/api/auth/login", json={
            "email": "intruder@alethra.io", "password": "intruderpass123",
        }).json()["access_token"]
        intruder = {"Authorization": f"Bearer {token}"}

        for method, path in (
            ("post", f"/api/v1/simulations/{session_id}/manual-decision"),
            ("post", f"/api/v1/simulations/{session_id}/advance"),
            ("get", f"/api/v1/simulations/{session_id}/orders"),
            ("get", f"/api/v1/paper-portfolio/{portfolio_id}"),
            ("get", f"/api/v1/paper-portfolio/{portfolio_id}/journal?format=json"),
            ("post", f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/cancel"),
        ):
            resp = getattr(client, method)(path, headers=intruder) if method == "get" else \
                getattr(client, method)(path, headers=intruder,
                                       json={"action": "buy", "quantity": 20,
                                             "rationale": "not mine",
                                             "idempotency_key": "intruder-1"})
            assert resp.status_code in (403, 404), (path, resp.status_code, resp.text)

        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)
        assert _order_view(client, auth_headers, session_id, order_id)["execution"]["state"] == \
            "pending"
        assert replay_decisions(client, auth_headers, session_id)[-1]["step_number"] == 2


@postgres_only
class TestConcurrentLifecycle:
    """Row-lock serialisation on a real PostgreSQL, with independent sessions.

    SQLite has no concurrent writers, so these skip there rather than claim a
    locking property it cannot show.
    """

    def test_two_requests_on_one_idempotency_key_leave_exactly_one_order(
        self, client, auth_headers
    ):
        """The read-side check is not the guard; the unique constraint is.

        Both requests can clear ``find_existing_by_key`` before either writes, so
        the proof is that the duplicate is refused whole — one order, one cash
        movement, no unhandled 500.
        """
        from app.models.paper_portfolio import PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        payload = {"action": "buy", "quantity": 20, "rationale": "racy",
                   "idempotency_key": "race-key"}

        codes = {}

        def submit(tag):
            codes[tag] = client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers, json=payload,
            ).status_code

        threads = [threading.Thread(target=submit, args=(str(i),)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        assert set(codes.values()) <= {200, 409}, codes

        orders = account_orders(client, auth_headers, portfolio_id)
        assert len(orders) == 1, f"one key booked {len(orders)} orders: {codes}"

        assert _advance(client, auth_headers, session_id).status_code == 200
        assert len(fills(client, auth_headers, portfolio_id, orders[0]["id"])) == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

        db = TestSession()
        try:
            assert db.query(PaperOrder).count() == 1
        finally:
            db.close()

    def test_a_stale_account_read_cannot_overwrite_a_committed_booking(
        self, client, auth_headers
    ):
        """Lost-update proof: the executor writes from the balance actually stored.

        One connection loads the account, another submits and advances a fill
        through the HTTP routes and commits, then the first executes its own
        pending order. Both cash movements must survive; if the second writer
        publishes the balance it read before the first committed, one trade
        vanishes from the money while its rows stay behind.
        """
        from app.models.paper_portfolio import OrderStatus, PaperFill, PaperOrder, PaperPortfolio
        from app.services.order_lifecycle import execute_due_orders

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        first_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]

        stale = TestSession()
        try:
            stale_portfolio = stale.query(PaperPortfolio).populate_existing().filter(
                PaperPortfolio.id == uuid.UUID(portfolio_id)
            ).one()
            assert float(stale_portfolio.current_virtual_cash) == \
                pytest.approx(INITIAL_CASH, abs=1e-9)

            # Another connection completes and commits a fill at B2's open.
            assert _advance(client, auth_headers, session_id).status_code == 200

            # The stale connection now books its own intent, for a different bar.
            second = stale.query(PaperOrder).filter(
                PaperOrder.id == uuid.UUID(first_id)
            ).one()
            second.idempotency_key = None  # this writer's own intent, not a replay
            second.status = OrderStatus.pending
            second.data_cutoff_at = B2
            stale.flush()
            execute_due_orders(
                stale, portfolio=stale_portfolio, instrument_id=SYMBOL,
                execution_date=B3, observed_open=B3_OPEN,
                commission_rate=COMMISSION_RATE, slippage_rate=SLIPPAGE_RATE,
                provider_id=PROVIDER,
            )
            stale.commit()
        finally:
            stale.close()

        legs = all_fills(client, auth_headers, portfolio_id)
        assert len(legs) == 2, legs
        expected = INITIAL_CASH + cash_moved_by(legs)
        balance = float(account(client, auth_headers, portfolio_id)["current_virtual_cash"])
        assert balance == pytest.approx(expected, abs=1e-6), (
            "a committed booking was overwritten by a stale balance: "
            f"{balance} != {expected}"
        )
        assert open_qty(client, auth_headers, portfolio_id) == 40

        db = TestSession()
        try:
            assert db.query(PaperOrder).count() == 1
            assert db.query(PaperFill).count() == 2
        finally:
            db.close()

    def test_a_manual_submit_racing_an_advance_stays_consistent(
        self, client, auth_headers
    ):
        """Either may win the replay's optimistic lock; neither may corrupt the book.

        The invariant is not which one ran first: no operation booked twice, no
        order with two fills, and the published balance equal to the sum of the
        fills the account itself carries.
        """
        from app.models.paper_portfolio import OrderStatus, PaperFill, PaperOrder

        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        results = {}

        def run_submit():
            results["submit"] = _submit(
                client, auth_headers, session_id, action="buy", quantity=20,
                key="race-submit",
            ).status_code

        def run_advance():
            results["advance"] = _advance(client, auth_headers, session_id).status_code

        threads = [threading.Thread(target=run_submit), threading.Thread(target=run_advance)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        db = TestSession()
        try:
            orders = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == uuid.UUID(portfolio_id)
            ).all()
            keys = [o.idempotency_key for o in orders]
            assert len(keys) == len(set(keys)), f"one operation booked twice: {keys} {results}"

            for order in orders:
                assert len([f for f in order.fills]) <= 1, (
                    f"order {order.id} carries {len(order.fills)} fills: {results}"
                )

            filled = [o for o in orders if o.status == OrderStatus.filled]
            expected = INITIAL_CASH + sum(
                float(f.net_cash_change) for o in filled for f in o.fills
            )
            balance = float(account(client, auth_headers, portfolio_id)["current_virtual_cash"])
            assert balance == pytest.approx(expected, abs=1e-6), (
                f"account cash does not reconcile with its own fills: "
                f"{balance} != {expected} ({results})"
            )
            assert db.query(PaperFill).count() == len(filled)
        finally:
            db.close()
