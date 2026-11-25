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

from app.engine.backtest import ENGINE_VERSION
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
            rationale="a stated thesis", linked_inference_id=None):
    body = {
        "action": action,
        "quantity": quantity,
        "rationale": rationale,
        "idempotency_key": key or f"sub-{uuid.uuid4()}",
    }
    if linked_inference_id is not None:
        body["linked_inference_id"] = linked_inference_id
    return client.post(
        f"/api/v1/simulations/{session_id}/manual-decision", headers=headers, json=body
    )


def _record_inference(portfolio_id, strategy_version_id):
    """The usage row a model execution leaves behind, for the account given.

    Written directly because no formal route manufactures one on demand here; the
    owner and the skill attribution are taken from the portfolio under test, so
    the lineage this file claims really was generated for the account that
    records it.

    ``strategy_version_id`` is required, and every caller passes the version the
    account's own session runs: what this helper stands for is a fresh estimate
    from the model behind the strategy under test. Both doors now compare the two
    halves of the lineage, so an origin that cannot say which version it came
    from is no longer a detail this fixture may leave out — and the row that
    *should* look like that legacy shape is built by
    ``_record_lineage_row`` instead, where the absence is the point of the case.
    """
    from app.models.billing import UsageRecord
    from app.models.paper_portfolio import PaperPortfolio

    db = TestSession()
    try:
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.id == uuid.UUID(portfolio_id)
        ).first()
        record = UsageRecord(
            user_id=portfolio.owner_id,
            subscription_id=None,
            endpoint="/api/v1/ml/personal/models/{version_id}/predict",
            model=None,
            tokens_used=0,
            cost=0,
            metadata_json={
                "prediction_type": "personal_decision",
                "skill_unit_id": str(portfolio.id),
                "strategy_version_id": str(strategy_version_id),
                "instrument_id": SYMBOL,
                "as_of_date": B1,
            },
        )
        db.add(record)
        db.commit()
        return str(record.id)
    finally:
        db.close()


def _record_lineage_row(portfolio_id, metadata):
    """A usage row carrying exactly the metadata one refusal case needs.

    The inference is written with the owner of the account given, so the only
    thing that varies between the cases below is the metadata the shared
    validator reads.
    """
    from app.models.billing import UsageRecord
    from app.models.paper_portfolio import PaperPortfolio

    db = TestSession()
    try:
        portfolio = db.query(PaperPortfolio).filter(
            PaperPortfolio.id == uuid.UUID(portfolio_id)
        ).first()
        row = UsageRecord(
            user_id=portfolio.owner_id,
            subscription_id=None,
            endpoint="/api/v1/ml/personal/models/{version_id}/predict",
            model=None,
            tokens_used=0,
            cost=0,
            metadata_json=metadata,
        )
        db.add(row)
        db.commit()
        return str(row.id)
    finally:
        db.close()


def _replay_events(session_id):
    """Every ``human_decision_events`` row this replay holds, as stored.

    Read out of the table rather than through a route on purpose: the storage
    contract is what this helper checks, and a response could invent a value the
    row never held. The door that renders the column is checked by its own node.
    """
    from app.models.human_decision import HumanDecisionEvent

    db = TestSession()
    try:
        rows = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.simulation_session_id == uuid.UUID(session_id))
            .all()
        )
        return [
            {
                "id": str(row.id),
                "action": row.action.value if row.action is not None else None,
                "linked_inference_id": (
                    str(row.linked_inference_id)
                    if row.linked_inference_id is not None
                    else None
                ),
                # The version this row says the decision was made under. The press
                # derives it from its own session rather than from the request, so
                # it is the second half of the lineage read-back: an origin accepted
                # by one store and contradicted by the other is the failure the
                # version leg exists to make impossible.
                "server_strategy_version_id": (
                    str(row.server_strategy_version_id)
                    if row.server_strategy_version_id is not None
                    else None
                ),
                "metadata_json": row.metadata_json,
            }
            for row in rows
        ]
    finally:
        db.close()


def _paper_decision(client, headers, portfolio_id, inference_id, version_id=None):
    """The other formal door: the paper desk records a decision, no order.

    ``version_id`` stays out of the body unless a case is specifically about a
    claimed version; with it omitted the desk has nothing to compare until the
    origin is read, which is the path those cases are about.
    """
    body = {
        "decision_type": "entry",
        "actor_type": "human",
        "rationale": "the same stated thesis",
        "expected_outcome": "test outcome",
        "expected_timeframe_days": 7,
        "self_reported_confidence": "medium",
        "linked_inference_id": inference_id,
    }
    if version_id is not None:
        body["linked_experiment_version_id"] = version_id
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
        headers=headers,
        json=body,
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


def decision_history_json(client, headers, portfolio_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/decision-history?format=json",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def decision_history_csv_rows(client, headers, portfolio_id):
    from app.api.paper_portfolio import DECISION_HISTORY_COLUMNS

    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/decision-history?format=csv",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    parsed = list(csv.DictReader(io.StringIO(resp.text)))
    assert parsed and list(parsed[0].keys()) == DECISION_HISTORY_COLUMNS, parsed[0].keys()
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


def _book_counts(db, portfolio_id):
    """One node's own order and fill rows, counted on the account it opened.

    ``paper_fills`` has no portfolio column, so the fills' range is the order ids
    this portfolio owns (``app/models/paper_portfolio.py:185``). Counting the two
    tables instead asks this node to share a verdict with every other node whose
    rows survive in the same database — which is what the isolated PostgreSQL run
    measured on the concurrency class below.
    """
    from app.models.paper_portfolio import PaperFill, PaperOrder

    own_orders = db.query(PaperOrder.id).filter(
        PaperOrder.portfolio_id == uuid.UUID(str(portfolio_id))
    )
    return {
        "orders": own_orders.count(),
        "fills": db.query(PaperFill).filter(
            PaperFill.paper_order_id.in_(own_orders)
        ).count(),
    }


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
        assert "Advance the replay" in resp.json()["detail"]

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

        # The market clock and the manual intent are the same engine on one
        # session; two labels here would make a reader think the book was priced
        # by two different versions.
        assert {d["engine_version"] for d in decision_rows} == {ENGINE_VERSION}


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
        assert "expired" in body["execution"]["waiting"]["reason"]

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

    def test_a_retired_account_never_fills_the_order_it_left_pending(
        self, client, auth_headers
    ):
        """Retirement is terminal, so an intent queued before it is not a trade.

        The order was pending when the account closed. The next bar must not turn
        it into a position, and the reason has to be visible on the order rather
        than inferred from a missing fill.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["order"]["order_id"]

        retired = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/retire", headers=auth_headers
        )
        assert retired.status_code == 200, retired.text
        assert retired.json()["status"] == "retired"
        assert retired.json()["closed_at"] is not None

        moved = _advance(client, auth_headers, session_id)
        assert moved.status_code == 200, moved.text

        view = _order_view(client, auth_headers, session_id, order_id)
        assert view["order"]["status"] == "pending"
        assert view["execution"]["blockers"] == ["portfolio_retired"]
        assert "terminal" in view["execution"]["waiting"]["reason"]
        assert fills(client, auth_headers, portfolio_id, order_id) == []
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH, abs=1e-9)

        blocked_events = events_for("manual_orders_blocked", session_id)
        assert len(blocked_events) == 1
        assert blocked_events[0].payload_json["blocked"][0]["blocker"] == "portfolio_retired"

        refused = _submit(client, auth_headers, session_id, action="buy", quantity=1)
        assert refused.status_code == 400, refused.text
        assert "cannot accept new orders" in refused.json()["detail"]

        again = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/retire", headers=auth_headers
        )
        assert again.status_code == 400
        assert again.json()["detail"] == "Cannot retire a 'retired' portfolio"


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
        assert decision["warnings"] and any(
            "Estimated from the observed close" in w for w in decision["warnings"]
        )
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
        assert "has no linked paper portfolio" in resp.json()["detail"]

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
        assert "will not choose one to settle against" in resp.json()["detail"]

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        for portfolio_id in (first, second):
            assert account_orders(client, auth_headers, portfolio_id) == []
            assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
                pytest.approx(INITIAL_CASH, abs=1e-9)

    def test_a_binding_to_another_owners_account_is_refused_as_a_forged_link(
        self, client, auth_headers
    ):
        """A session pointing at an account is not the same as owning that account.

        ``paper_portfolios.simulation_session_id`` is one column pointing at one
        row, and a column can be written by anything that reaches the table. Both
        doors that create this pair check that the session belongs to the caller
        (``require_session_for_owner`` in the portfolio create route and the
        decisions door), so no route produces a mismatched pair today — this node
        writes the state the routes refuse and asks what the submit door does with
        it. The answer has to be a refusal: an order that settled here would put
        one owner's trade into another owner's ledger, and the balance the first
        owner reads back would not be their own.
        """
        from app.models.paper_portfolio import (
            DecisionRecord,
            PaperFill,
            PaperOrder,
            PaperPortfolio,
        )

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        assert _advance(client, auth_headers, session_id).status_code == 200

        client.post("/api/auth/register", json={
            "risk_acknowledged": True,
            "email": "other-ledger@alethra.io", "password": "otherpass123",
            "display_name": "Other Ledger", "user_type": "individual",
        })
        token = client.post("/api/auth/login", json={
            "email": "other-ledger@alethra.io", "password": "otherpass123",
        }).json()["access_token"]
        other = {"Authorization": f"Bearer {token}"}
        foreign_portfolio = client.post("/api/v1/paper-portfolio", headers=other, json={
            "initial_virtual_cash": INITIAL_CASH, "currency": "USD",
        })
        assert foreign_portfolio.status_code == 201, foreign_portfolio.text
        foreign_id = foreign_portfolio.json()["id"]

        # The forged half: someone else's account now names this session. Only a
        # write under the application reaches this, which is exactly why the
        # submit door cannot treat the binding as an authorisation.
        db = TestSession()
        try:
            row = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == uuid.UUID(foreign_id)
            ).one()
            row.simulation_session_id = uuid.UUID(session_id)
            db.commit()
        finally:
            db.close()

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "whose owner is" in detail, detail
        assert foreign_id in detail, detail

        assert db_counts(orders=PaperOrder, fills=PaperFill, journal=DecisionRecord) == {
            "orders": 0, "fills": 0, "journal": 0
        }
        assert _replay_events(session_id) == []
        assert session_orders(client, auth_headers, session_id) == []
        assert account_orders(client, other, foreign_id) == []
        assert account(client, other, foreign_id)["current_virtual_cash"] == \
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
        assert "cannot accept new orders" in resp.json()["detail"]
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


class TestLineageTravelsFromSubmitToExport:
    """The inference a submission names is the inference the export shows.

    Lineage is only worth recording if one identity survives the whole chain: the
    value sent, the row stored, the value the decisions route reads back and the
    cell the export writes are the same id — and a value that fails the checks is
    refused on every route that accepts it, not recorded by some of them.

    The press writes two decision stores, and both now hold it: ``decision_records``
    for the account's paper rows and ``human_decision_events`` for the replay's own
    history, which also keeps holds and withdrawn intents and is therefore the only
    place a model-driven decision that never became an order is still attributable.
    """

    @staticmethod
    def decisions(client, headers, portfolio_id):
        resp = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_the_linked_inference_reads_back_from_every_door(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        inference_id = _record_inference(portfolio_id, ctx["version_id"])

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20,
                       linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["order"]["order_id"]

        # A pending intent already carries its origin. Recording lineage only once
        # a fill exists would lose the inference behind orders that expire, get
        # cancelled or are never executed.
        rows = journal_json(client, auth_headers, portfolio_id)["records"]
        assert len(rows) == 1
        assert rows[0]["record_type"] == "order"
        assert rows[0]["linked_inference_id"] == inference_id
        assert rows[0]["linked_experiment_version_id"] == ctx["version_id"]

        decisions = self.decisions(client, auth_headers, portfolio_id)
        assert len(decisions) == 1
        assert decisions[0]["id"] == resp.json()["order"]["decision_id"]
        assert decisions[0]["linked_inference_id"] == inference_id
        assert decisions[0]["linked_experiment_version_id"] == ctx["version_id"]

        # The same order after the market has answered it: the lineage travels
        # with the row, it does not belong to one lifecycle stage.
        assert _advance(client, auth_headers, session_id).status_code == 200
        rows = journal_json(client, auth_headers, portfolio_id)["records"]
        assert len(rows) == 1, rows
        assert rows[0]["record_type"] == "fill"
        assert rows[0]["linked_inference_id"] == inference_id
        assert rows[0]["linked_experiment_version_id"] == ctx["version_id"]

        csv_row = [r for r in journal_csv_rows(client, auth_headers, portfolio_id)
                   if r["order_id"] == order_id][0]
        assert csv_row["linked_inference_id"] == inference_id
        assert csv_row["linked_experiment_version_id"] == ctx["version_id"]

    def test_the_simulation_press_reads_back_from_the_decision_history_file(
        self, client, auth_headers
    ):
        """The customer's second export, fed by this page's own press.

        ``journal`` is built from orders, so it cannot answer for a decision that
        never carried one; ``decision-history`` is the file that can, and both read
        the same ``decision_records`` row this submission writes. If lineage reached
        only the first file, the two exports would disagree about which model
        execution a decision was made on — so the lineage column, the source state
        the export copies off the usage row, and the session attribution are all
        read here rather than inferred from the payload the press sent.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        inference_id = _record_inference(ctx["portfolio_id"], ctx["version_id"])

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy",
                       quantity=20, linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text
        decision_id = resp.json()["order"]["decision_id"]

        rows = decision_history_json(
            client, auth_headers, ctx["portfolio_id"]
        )["records"]
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["record_type"] == "decision"
        assert row["decision_id"] == decision_id
        assert row["decision_scope"] == "this_portfolio"
        assert row["linked_inference_id"] == inference_id
        assert row["linked_experiment_version_id"] == ctx["version_id"]
        assert row["inference_source_state"] == "present"
        assert row["simulation_session_id"] == ctx["session_id"]

        csv_row = [
            r for r in decision_history_csv_rows(
                client, auth_headers, ctx["portfolio_id"]
            )
            if r["decision_id"] == decision_id
        ][0]
        assert csv_row["linked_inference_id"] == inference_id
        assert csv_row["inference_source_state"] == "present"
        assert csv_row["simulation_session_id"] == ctx["session_id"]

    def test_a_human_decision_with_no_inference_exports_empty_lineage(
        self, client, auth_headers
    ):
        """Blank in the export means "no model execution", not "unknown".

        The same submission without a claimed inference is the ordinary human
        trade this suite books thousands of; it must not pick up a value.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        resp = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resp.status_code == 200, resp.text

        row = journal_json(client, auth_headers, ctx["portfolio_id"])["records"][0]
        assert row["linked_inference_id"] is None
        assert row["linked_experiment_version_id"] == ctx["version_id"]

        csv_row = journal_csv_rows(client, auth_headers, ctx["portfolio_id"])[0]
        assert csv_row["linked_inference_id"] == ""
        assert csv_row["decision_id"] == row["decision_id"]

    def test_an_origin_that_records_no_version_is_admitted_and_stays_silent(
        self, client, auth_headers
    ):
        """The compatibility path: absence on both sides is recorded as absence.

        A usage row written before the predict route began recording
        ``strategy_version_id`` cannot say which strategy it came from, and a desk
        call that names no version has nothing to compare it against. Refusing
        that pair would make every pre-existing decision unbookable, so the door
        admits it — what it must not do is let the admitted row read back as
        though a version had been confirmed. Every version cell here is empty,
        and the export's own column description has to say so.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        unnamed = _record_lineage_row(ctx["portfolio_id"], {
            "prediction_type": "personal_decision",
            "skill_unit_id": ctx["portfolio_id"],
            "instrument_id": SYMBOL,
            "as_of_date": B1,
        })

        created = _paper_decision(client, auth_headers, ctx["portfolio_id"], unnamed)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["linked_inference_id"] == unnamed
        assert body["linked_experiment_version_id"] is None

        rows = decision_history_json(client, auth_headers, ctx["portfolio_id"])
        row = [r for r in rows["records"] if r["decision_id"] == body["id"]][0]
        assert row["inference_source_state"] == "present", row
        assert row["linked_experiment_version_id"] is None
        assert row["inference_strategy_version_id"] is None
        assert row["inference_model_version_id"] is None

        # The same row in the file the customer opens in a spreadsheet.
        as_csv = [
            r
            for r in decision_history_csv_rows(client, auth_headers, ctx["portfolio_id"])
            if r["decision_id"] == body["id"]
        ][0]
        assert as_csv["linked_inference_id"] == unnamed
        assert as_csv["linked_experiment_version_id"] == ""
        assert as_csv["inference_strategy_version_id"] == ""

        # The gate on the narration, not just the cells: the document a customer
        # reads alongside an empty version column may not let them fill it in.
        semantics = rows["column_semantics"]["linked_experiment_version_id"]
        assert "not a verified absence" in semantics, semantics
        assert "never means the version was confirmed" in semantics, semantics

    def test_an_inference_generated_for_another_account_is_refused(
        self, client, auth_headers
    ):
        """The replay route refuses what the paper desk refuses — same reason.

        Two entry points, one contract: an order submitted against account B must
        not be able to claim an inference the model produced for account A just
        because the submission went through the replay.
        """
        mine = _open_account(client, auth_headers, observed=1)
        theirs = _open_account(client, auth_headers, observed=1)
        foreign_inference = _record_inference(theirs["portfolio_id"], theirs["version_id"])

        resp = _submit(client, auth_headers, mine["session_id"], action="buy",
                       quantity=20, linked_inference_id=foreign_inference)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), f"refusal names no reason: {detail!r}"
        assert detail.get("reason") == "inference_skill_mismatch", resp.text
        assert detail["portfolio_id"] == mine["portfolio_id"]

        # Refused means nothing was submitted, not an order with the lineage
        # quietly dropped — a trade recorded as human that was claimed as model-
        # driven is the failure this whole chain exists to prevent.
        assert account_orders(client, auth_headers, mine["portfolio_id"]) == []
        assert self.decisions(client, auth_headers, mine["portfolio_id"]) == []
        assert journal_json(client, auth_headers, mine["portfolio_id"])["records"] == []

    def test_a_hold_has_no_decision_row_to_carry_an_inference(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        inference_id = _record_inference(ctx["portfolio_id"], ctx["version_id"])

        resp = _submit(client, auth_headers, ctx["session_id"], action="hold",
                       linked_inference_id=inference_id)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), f"refusal names no reason: {detail!r}"
        assert detail.get("reason") == "inference_needs_decision_row", resp.text

        # A hold is still a recordable decision — it is the lineage it has no row
        # to store that is refused.
        ok = _submit(client, auth_headers, ctx["session_id"], action="hold")
        assert ok.status_code == 200, ok.text
        assert self.decisions(client, auth_headers, ctx["portfolio_id"]) == []

    def test_one_key_cannot_be_replayed_under_a_different_inference(
        self, client, auth_headers
    ):
        """The two replay branches, taken separately, on a lineage-carrying press.

        An already-committed key short-circuits at the decision row and returns the
        original order: the lineage is not re-adjudicated, because the request is
        the same one that was adjudicated the first time. The ledger event is the
        second branch, and it has its own conflict rule below the decision lookup,
        so this node reads both stores back rather than treating one response as
        proof about the other.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        first = _record_inference(portfolio_id, ctx["version_id"])
        second = _record_inference(portfolio_id, ctx["version_id"])
        key = f"lineage-{uuid.uuid4()}"

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20,
                       key=key, linked_inference_id=first)
        assert resp.status_code == 200, resp.text

        replay = _submit(client, auth_headers, session_id, action="buy", quantity=20,
                         key=key, linked_inference_id=first)
        assert replay.status_code == 200, replay.text
        assert replay.json()["order"]["order_id"] == resp.json()["order"]["order_id"]

        # Decision branch: one replayed press is one decision, still attributed to
        # the origin the first press named.
        decisions = self.decisions(client, auth_headers, portfolio_id)
        assert [d["linked_inference_id"] for d in decisions] == [first]

        # Human-event branch: the replay never reaches the event write, so the
        # ledger holds the single row the accepted press created — with both halves
        # of its lineage intact.
        events = _replay_events(session_id)
        assert [e["linked_inference_id"] for e in events] == [first], events
        assert events[0]["server_strategy_version_id"] == ctx["version_id"]

        swapped = _submit(client, auth_headers, session_id, action="buy", quantity=20,
                          key=key, linked_inference_id=second)
        assert swapped.status_code == 409, swapped.text

        orders = account_orders(client, auth_headers, portfolio_id)
        assert len(orders) == 1
        decisions = self.decisions(client, auth_headers, portfolio_id)
        assert [d["linked_inference_id"] for d in decisions] == [first]
        # A request refused on the key changes neither store: no second event, and
        # the first one still names the inference it was accepted with.
        assert [e["linked_inference_id"] for e in _replay_events(session_id)] == [first]

    def test_both_formal_doors_answer_one_lineage_corpus_identically(
        self, client, auth_headers
    ):
        """Parity is measured at the two entries, not at the shared helper.

        ``validate_linked_inference`` is one function, so testing it once proves
        the rules; it does not prove either door reaches them. A door that
        stopped passing ``linked_inference_id`` through would keep the helper
        green and start accepting, on one route only, a lineage the other
        refuses. Both doors are driven with the same row, in the same test.

        The desk is driven with the version the replay's own session runs, which
        is the only claim the two doors can hold in common: the replay derives
        its version from the session and cannot be told otherwise, so without a
        stated version on the desk the two are not comparing the same decision
        against the same strategy. The desk's derived path — the request names an
        origin and no version — is the other door-local behavior and gets its own
        nodes in ``test_trained_inference_lineage_chain.py``.

        The last two rows are the version leg. ``origin-names-no-version`` is the
        legacy usage row, written before the prediction recorded which frozen
        strategy it came from, and it stays in the corpus as the countercontrol:
        an origin that cannot answer must not be read as agreeing with whatever
        version the decision happens to name.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        version_id = ctx["version_id"]
        mine = _record_inference(portfolio_id, version_id)

        corpus = [
            ("absent", str(uuid.uuid4())),
            ("metadata-is-a-list", _record_lineage_row(portfolio_id, ["not", "a", "dict"])),
            ("not-a-model-execution", _record_lineage_row(
                portfolio_id,
                {"prediction_type": "market_direction", "skill_unit_id": portfolio_id},
            )),
            ("generated-for-another-skill", _record_lineage_row(
                portfolio_id,
                {"prediction_type": "personal_decision",
                 "skill_unit_id": str(uuid.uuid4())},
            )),
            ("origin-names-no-version", _record_lineage_row(
                portfolio_id,
                {"prediction_type": "personal_decision", "skill_unit_id": portfolio_id},
            )),
            ("origin-names-another-version", _record_lineage_row(
                portfolio_id,
                {"prediction_type": "personal_decision", "skill_unit_id": portfolio_id,
                 "strategy_version_id": str(uuid.uuid4())},
            )),
            ("admitted", mine),
        ]

        for name, inference_id in corpus:
            replay = _submit(client, auth_headers, session_id, action="buy",
                             quantity=20, linked_inference_id=inference_id)
            desk = _paper_decision(client, auth_headers, portfolio_id, inference_id,
                                   version_id)

            def answered(resp):
                # Success is not comparable across the doors: the desk creates a
                # row (201) and the replay answers with the order it queued (200).
                # What must be identical is which lineages each refuses, and why.
                if resp.status_code < 400:
                    return ("accepted", None)
                detail = resp.json()["detail"]
                reason = detail.get("reason") if isinstance(detail, dict) else detail
                return (resp.status_code, reason)

            assert answered(replay) == answered(desk), (
                f"{name}: the two doors answered differently — "
                f"replay={answered(replay)} desk={answered(desk)}"
            )
            if name == "admitted":
                assert answered(replay) == ("accepted", None), replay.text
            else:
                assert answered(replay)[1] is not None, f"{name} refused without a reason"

            # Refused on both doors means nothing written on either: the row this
            # corpus rejects may not survive as a decision, an order, a ledger
            # event or an audit entry on the door whose check ran second.
            if name != "admitted":
                assert account_orders(client, auth_headers, portfolio_id) == [], name
                assert _replay_events(session_id) == [], name
                assert journal_json(client, auth_headers, portfolio_id)["records"] == []

    def test_a_lineage_refusal_leaves_no_record_behind_on_either_door(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        foreign = _record_lineage_row(
            portfolio_id,
            {"prediction_type": "personal_decision", "skill_unit_id": str(uuid.uuid4())},
        )

        assert _submit(client, auth_headers, session_id, action="buy", quantity=20,
                       linked_inference_id=foreign).status_code == 400
        assert _paper_decision(client, auth_headers, portfolio_id, foreign).status_code == 400

        assert account_orders(client, auth_headers, portfolio_id) == []
        assert self.decisions(client, auth_headers, portfolio_id) == []
        assert journal_json(client, auth_headers, portfolio_id)["records"] == []
        # The replay's own ledger stays empty as well: a press refused for its
        # lineage must not survive as an event whose claim was quietly dropped,
        # which is the same trade recorded as human that was offered as model-made.
        assert _replay_events(session_id) == []

    def test_the_lineage_gate_says_nothing_about_which_instrument_the_origin_named(
        self, client, auth_headers
    ):
        """Recorded product behavior, not coverage: the instrument is not checked.

        The lineage compares owner, prediction type, skill account and now the
        strategy version. It does not compare the instrument the estimate was made
        on with the one the account trades, so an origin about AAPL can stand
        behind a decision on this file's symbol, and the journal then shows the
        pair. Both sides of the inequality are named, because an empty cell would
        satisfy a bare ``!=`` and prove nothing. If the owner rules that a lineage
        must be instrument-scoped, this is the node that changes.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        inference_id = _record_lineage_row(
            ctx["portfolio_id"],
            {"prediction_type": "personal_decision",
             "skill_unit_id": ctx["portfolio_id"],
             "strategy_version_id": ctx["version_id"],
             "instrument_id": "US:AAPL",
             "as_of_date": B1},
        )

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy",
                       quantity=20, linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text

        rows = journal_json(client, auth_headers, ctx["portfolio_id"])["records"]
        assert [r["linked_inference_id"] for r in rows] == [inference_id]
        assert rows[0]["asset_id"] == SYMBOL != "US:AAPL"
        history = decision_history_json(
            client, auth_headers, ctx["portfolio_id"]
        )["records"]
        assert history[0]["inference_skill_unit_id"] == ctx["portfolio_id"]
        assert history[0]["inference_strategy_version_id"] == ctx["version_id"]

    def test_the_replay_ledger_row_holds_the_inference_the_request_named(
        self, client, auth_headers
    ):
        """``human_decision_events`` carries the lineage too, in its own column.

        The submission validated an inference and wrote it to the paper row only.
        The event the same request created for the replay's decision history had
        no column to hold it, so that history — the one store that keeps holds,
        cancelled intents and fills alike — could not tell a model-driven call
        from a human one. ``d4e5f6g7h8i9`` adds the column and the route fills it
        with the value it had already accepted.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        inference_id = _record_inference(ctx["portfolio_id"], ctx["version_id"])

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy",
                       quantity=20, linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text

        events = _replay_events(ctx["session_id"])
        assert [e["action"] for e in events] == ["buy"], events
        assert events[0]["linked_inference_id"] == inference_id
        # One identity, one place. ``metadata_json`` is where a second copy would
        # go, and a second copy is how the two start disagreeing.
        assert "linked_inference_id" not in events[0]["metadata_json"]
        assert events[0]["metadata_json"]["source"] == "manual_decision"
        # The paper row of the same request says the same thing, because it is the
        # same value on both writes rather than two values that happen to match.
        assert [d["linked_inference_id"] for d in
                self.decisions(client, auth_headers, ctx["portfolio_id"])] == [inference_id]
        # Both stores also agree on the *version* half of the lineage. The event
        # derives it from its own session, the decision row from the version the
        # door compared the origin against, and the door only accepted the claim
        # because those are the same id — so reading one of them back and not the
        # other would leave the disagreement this leg exists to prevent unobserved.
        assert events[0]["server_strategy_version_id"] == ctx["version_id"]
        assert self.decisions(
            client, auth_headers, ctx["portfolio_id"]
        )[0]["linked_experiment_version_id"] == ctx["version_id"]
        # …and so does the replay's own step readback, which is what the page
        # renders. Three stores named for one press; a node that only checked the
        # door's response would not see the ledger and the step drift apart.
        steps = replay_decisions(client, auth_headers, ctx["session_id"])
        claimed = [detail["linked_inference_id"]
                   for detail in (s["decision_detail"] or {} for s in steps)
                   if detail.get("linked_inference_id")]
        assert claimed == [inference_id]

    def test_a_press_that_named_no_inference_records_one_in_the_ledger_as_null(
        self, client, auth_headers
    ):
        """The column holds nothing, not the account's most recent estimate.

        An ordinary human press is the common case in this suite. A write that
        helped itself to a nearby inference would make every human decision in the
        replay history read as model-driven.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        _record_inference(ctx["portfolio_id"], ctx["version_id"])

        resp = _submit(client, auth_headers, ctx["session_id"], action="buy", quantity=20)
        assert resp.status_code == 200, resp.text

        events = _replay_events(ctx["session_id"])
        assert [e["linked_inference_id"] for e in events] == [None]
        assert "linked_inference_id" not in events[0]["metadata_json"]
        # The readback stays empty too: a door that filled in the account's latest
        # estimate would be indistinguishable, at the UI, from one the user chose.
        steps = replay_decisions(client, auth_headers, ctx["session_id"])
        blank = [detail.get("linked_inference_id")
                 for detail in (s["decision_detail"] or {} for s in steps)
                 if "linked_inference_id" in detail]
        assert blank == [None]

    def test_the_ledger_keeps_the_lineage_through_the_market_and_the_cancel(
        self, client, auth_headers
    ):
        """The event is written at the press, so it outlives every later state.

        The row is created before the market answers, and nothing later rewrites
        its lineage: an order that fills and an order the owner withdraws both
        stay attributed to the inference the decision named, because the event is
        the record of the decision rather than of the execution.
        """
        filled = _open_account(client, auth_headers, observed=1)
        inference_id = _record_inference(filled["portfolio_id"], filled["version_id"])
        resp = _submit(client, auth_headers, filled["session_id"], action="buy",
                       quantity=20, linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text
        order_id = resp.json()["order"]["order_id"]

        withdrawn = _open_account(client, auth_headers, observed=1)
        other = _record_inference(withdrawn["portfolio_id"], withdrawn["version_id"])
        cancelled = _submit(client, auth_headers, withdrawn["session_id"],
                            action="buy", quantity=20, linked_inference_id=other)
        assert cancelled.status_code == 200, cancelled.text
        assert client.post(
            f"/api/v1/paper-portfolio/{withdrawn['portfolio_id']}/orders/"
            f"{cancelled.json()['order']['order_id']}/cancel",
            headers=auth_headers,
        ).status_code == 200

        assert _advance(client, auth_headers, filled["session_id"]).status_code == 200

        assert [e["linked_inference_id"] for e in _replay_events(filled["session_id"])] == \
            [inference_id]
        assert [e["linked_inference_id"] for e in _replay_events(withdrawn["session_id"])] == \
            [other]

    def test_the_events_own_door_shows_the_lineage_and_a_correction_keeps_it(
        self, client, auth_headers
    ):
        """The ledger is readable without the database, and re-narrating keeps origin.

        ``GET /api/v1/human-decisions/{id}`` rendered this row while leaving the
        ``linked_inference_id`` column out of its response: the press stored the
        inference, the door omitted it, so the one book that still holds a hold or
        a withdrawn intent could not tell a model-driven decision from a human one
        to anyone without a DB session. The correction path had the mirror hole —
        it deliberately reuses the original's server context and as-of instant but
        wrote a blank lineage, which reads as "no model execution" for a decision
        that named one. Neither fix accepts an inference: both read the value an
        earlier write validated and stored.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        inference_id = _record_inference(ctx["portfolio_id"], ctx["version_id"])
        resp = _submit(client, auth_headers, ctx["session_id"], action="buy",
                       quantity=20, linked_inference_id=inference_id)
        assert resp.status_code == 200, resp.text
        event_id = _replay_events(ctx["session_id"])[0]["id"]

        read = client.get(
            f"/api/v1/human-decisions/{event_id}", headers=auth_headers
        )
        assert read.status_code == 200, read.text
        assert read.json()["linked_inference_id"] == inference_id

        corrected = client.post(
            f"/api/v1/human-decisions/{event_id}/correct",
            headers=auth_headers,
            json={"action": "hold", "rationale": "同一個決定，改寫理由。",
                  "correction_reason": "理由寫錯了，血緣不變。"},
        )
        assert corrected.status_code == 201, corrected.text
        assert corrected.json()["linked_inference_id"] == inference_id
        assert corrected.json()["supersedes_event_id"] == event_id

        # A correction is another row, not a rewrite: both readings now name the
        # inference the press accepted, and neither one invented it later.
        assert [e["linked_inference_id"] for e in _replay_events(ctx["session_id"])] == \
            [inference_id, inference_id]
        listed = client.get(
            f"/api/v1/human-decisions?simulation_session_id={ctx['session_id']}",
            headers=auth_headers,
        )
        assert listed.status_code == 200, listed.text
        assert [entry["linked_inference_id"] for entry in listed.json()] == \
            [inference_id, inference_id]


class TestLineageFromAServedPrediction:
    """The head of the chain is a prediction this application actually ran.

    Every other lineage node in this file takes its id from ``_record_inference``,
    which inserts a usage row directly. That is the right tool for a refusal
    corpus — the metadata can be shaped exactly wrong — but it cannot answer the
    two questions the owner asked about the real path: whether the id the predict
    route hands back is the id the decision doors accept, and what happens to an
    origin whose version the owner has since taken back. Here the model, the
    artifact, the selection, the billing row and the id all come from the routes.

    What the answers are, since the version leg closed: this fixture's prediction
    was trained from the personal-learning experiment's own version, which is
    never frozen and is not the version the replay session runs. Both doors now
    compare the two halves of a lineage, so on this fixture they refuse, and the
    nodes below say which refusal each door reaches and prove nothing was written.
    The accepted path — a served origin whose version and decision version are one
    frozen id, read back from the decision, the ledger and the export — is
    ``tests/test_trained_inference_lineage_chain.py``, whose fixture freezes the
    version it trains from and replays that same version. Leaving this fixture
    mismatched is deliberate: making the two agree here would delete the only case
    in the suite where a real prediction names research the decision does not.

    Still synthetic data: fixture bars, a fixture artifact, a test owner. The
    layer proved is the wiring between entry points, not whether any model is
    useful, and no browser is involved — the two screens that press this route
    are checked in ``frontend/tests/dailyEstimate.test.mjs``.
    """

    @pytest.fixture()
    def served(self, client, auth_headers, tmp_path, monkeypatch):
        """One model execution served to the very account the replay submits into."""
        from tests.test_personal_model_comparison import _model_for_job, _start_training
        from tests.test_personal_strategy_learning import (
            BARS,
            INSTRUMENT,
            _build_snapshot,
            _create_experiment,
            _grant_consent,
            _personal_artifacts,
            _seed_bars,
            _seed_confirmed_decisions,
            _select_for_skill,
            _user_id,
        )

        db = TestSession()
        try:
            _seed_bars(db)
            db.commit()
        finally:
            db.close()

        owner_id = _user_id()
        learning_experiment_id, strategy_version_id = _create_experiment(
            client, auth_headers, title="Lineage from a served prediction"
        )
        _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))
        _grant_consent(client, auth_headers, strategy_version_id)
        snapshot = _build_snapshot(client, auth_headers, strategy_version_id)
        _personal_artifacts(tmp_path, monkeypatch)
        job = _start_training(
            client, auth_headers, snapshot["snapshot_id"], "logistic_regression"
        )
        model = _model_for_job(client, auth_headers, job, strategy_version_id)

        # The skill account is not a freshly opened portfolio: it is the account
        # this session is bound to, because that is the id the doors verify the
        # inference against.
        account = _open_account(client, auth_headers, observed=1)
        _select_for_skill(
            client, auth_headers, model["model_version_id"], account["portfolio_id"]
        )
        predicted = client.post(
            f"/api/v1/ml/personal/models/{model['model_version_id']}/predict",
            headers=auth_headers,
            json={
                "instrument_id": INSTRUMENT,
                "as_of_date": BARS[80].isoformat(),
                "skill_unit_id": account["portfolio_id"],
            },
        )
        assert predicted.status_code == 200, predicted.text
        return {
            **account,
            "owner_id": str(owner_id),
            # The version this model was trained from, named separately from
            # ``account["version_id"]`` because they are different ids: the replay
            # runs a frozen strategy, the prediction came out of an unfrozen
            # learning experiment. Which one a decision records is what the doors
            # now compare, so the fixture has to say both.
            "learning_version_id": strategy_version_id,
            "learning_experiment_id": learning_experiment_id,
            "instrument": INSTRUMENT,
            "as_of": BARS[80].isoformat(),
            "model_version_id": model["model_version_id"],
            "record_id": model["record_id"],
            "inference_id": predicted.json()["inference_id"],
            "predicted": predicted.json(),
        }

    @staticmethod
    def paper_decisions(client, headers, portfolio_id):
        resp = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_a_served_prediction_names_the_version_it_was_trained_from(
        self, client, auth_headers, served
    ):
        """A real prediction, and the two doors' answer to a version it cannot share.

        The id here comes out of ``POST /ml/personal/models/{id}/predict``, the
        artifact and the selection behind it out of the training worker, so this
        is the shape the customer's own estimate arrives in. What it is *not* is an
        origin of this replay: the prediction was trained from the personal-learning
        experiment's version, while the replay runs a different, frozen one. The
        version leg answers that with a refusal on both doors rather than recording
        a lineage whose two halves name different research.
        """
        from app.models.billing import UsageRecord

        inference_id = served["inference_id"]

        db = TestSession()
        try:
            usage = (
                db.query(UsageRecord)
                .filter(UsageRecord.id == uuid.UUID(inference_id))
                .one()
            )
            assert str(usage.user_id) == served["owner_id"]
            assert usage.endpoint == "/api/v1/ml/personal/models/{version_id}/predict"
            assert usage.metadata_json["prediction_type"] == "personal_decision"
            assert usage.metadata_json["skill_unit_id"] == served["portfolio_id"]
            assert usage.metadata_json["model_version_id"] == served["model_version_id"]
            assert usage.metadata_json["instrument_id"] == served["instrument"]
            # The version leg reads this value, so the mismatch below is a fact
            # about the product's own row and not about a column this fixture owns.
            assert usage.metadata_json["strategy_version_id"] == \
                served["learning_version_id"]
        finally:
            db.close()

        assert served["learning_version_id"] != served["version_id"]

        # Replay door: the session's version is the claim, and it is not the
        # origin's. Both halves are named back so the screen can say which two
        # ids disagreed.
        replay = _submit(client, auth_headers, served["session_id"], action="buy",
                         quantity=20, linked_inference_id=inference_id)
        assert replay.status_code == 400, replay.text
        detail = replay.json()["detail"]
        assert detail["reason"] == "inference_version_mismatch", detail
        assert detail["inference_strategy_version_id"] == served["learning_version_id"]
        assert detail["decision_version"] == served["version_id"]
        assert len(detail["message"]) > 20

        # Desk door, same row, claiming the same version the replay holds: one
        # rule, one answer.
        desk_claimed = _paper_decision(
            client, auth_headers, served["portfolio_id"], inference_id,
            served["version_id"],
        )
        assert desk_claimed.status_code == 400, desk_claimed.text
        assert desk_claimed.json()["detail"]["reason"] == "inference_version_mismatch"

        # Desk door, version omitted: the origin's own version is what gets used,
        # and it is held to the same eligibility check a stated version is. The
        # request named no version at all, so the unfrozen version this refusal is
        # about can only have come from the inference.
        desk_derived = _paper_decision(
            client, auth_headers, served["portfolio_id"], inference_id
        )
        assert desk_derived.status_code == 400, desk_derived.text
        derived = desk_derived.json()["detail"]
        assert derived["reason"] == "version_not_usable", derived
        assert derived["state"] == "unlocked"

        # Nothing was written by any of the three refusals, on either door.
        assert account_orders(client, auth_headers, served["portfolio_id"]) == []
        assert self.paper_decisions(
            client, auth_headers, served["portfolio_id"]
        ) == []
        assert journal_json(client, auth_headers, served["portfolio_id"])["records"] == []
        assert decision_history_json(
            client, auth_headers, served["portfolio_id"]
        )["records"] == []
        assert _replay_events(served["session_id"]) == []

        # The refusal is about the version, and only about the version: freeze that
        # same learning version through its own formal entry, change nothing else
        # about the prediction, and the desk now takes the identical request and
        # records the version it derived from the origin.
        frozen = client.post(
            f"/api/v1/experiments/{served['learning_experiment_id']}/versions/"
            f"{served['learning_version_id']}/lock",
            headers=auth_headers,
            json={"confirm": True, "reason": "測試：凍結後該推論才能成為決策的來源"},
        )
        assert frozen.status_code == 200, frozen.text

        accepted = _paper_decision(
            client, auth_headers, served["portfolio_id"], inference_id
        )
        assert accepted.status_code == 201, accepted.text
        recorded = self.paper_decisions(
            client, auth_headers, served["portfolio_id"]
        )
        assert [d["id"] for d in recorded] == [accepted.json()["id"]]
        assert recorded[0]["linked_inference_id"] == inference_id
        assert recorded[0]["linked_experiment_version_id"] == served["learning_version_id"]

        row = decision_history_json(
            client, auth_headers, served["portfolio_id"]
        )["records"][0]
        assert row["linked_experiment_version_id"] == served["learning_version_id"]
        assert row["inference_strategy_version_id"] == served["learning_version_id"]
        assert row["inference_source_state"] == "present"

        # The audit says which of the two paths this was. The row itself cannot
        # tell a version the user named from one the door read out of the origin,
        # and those are different claims about who decided what.
        audit = events_for("decision_recorded", accepted.json()["id"])
        assert len(audit) == 1, audit
        assert audit[0].payload_json["version_source"] == "inference_origin"

        # The replay still refuses the same origin after the freeze: its session
        # runs a different version, and a version becoming eligible does not make
        # it the session's.
        still = _submit(client, auth_headers, served["session_id"], action="buy",
                        quantity=20, linked_inference_id=inference_id)
        assert still.status_code == 400, still.text
        assert still.json()["detail"]["reason"] == "inference_version_mismatch"
        assert _replay_events(served["session_id"]) == []

    def test_an_origin_whose_version_was_taken_back_answers_the_same_refusal(
        self, client, auth_headers, served
    ):
        """Withdrawal closes the inference door; it does not move the version leg.

        ``revoke`` sets ``revoked_at``, clears the record alias and stops that
        version serving new predictions. It rewrites no usage row, so the lineage a
        decision claims is still a statement about which execution the owner acted
        on. What that statement is *worth* at a door is decided by the two halves
        agreeing, and on this fixture they never did: so the withdrawal changes the
        answer about new predictions and changes nothing about the refusal.

        That an accepted origin keeps originating its decision after its model
        version is withdrawn — the claim this node carried before the version leg
        was wired, when nothing compared the two versions — needs a lane where the
        decision and the origin name one frozen version. It is asserted there:
        ``tests/test_trained_inference_lineage_chain.py``.
        """
        revoked = client.post(
            f"/api/v1/model-research/{served['record_id']}/versions/"
            f"{served['model_version_id']}/revoke",
            headers=auth_headers,
            params={"reason": "customer_withdrawal"},
        )
        assert revoked.status_code == 200, revoked.text

        # The same version now refuses a fresh prediction…
        again = client.post(
            f"/api/v1/ml/personal/models/{served['model_version_id']}/predict",
            headers=auth_headers,
            json={
                "instrument_id": served["instrument"],
                "as_of_date": served["as_of"],
                "skill_unit_id": served["portfolio_id"],
            },
        )
        assert again.status_code == 409, again.text
        assert again.json()["detail"]["code"] == "model_version_revoked", again.text

        # …and the execution it already served is refused for the reason it was
        # refused before: the version it names is not the version this replay runs.
        replay = _submit(client, auth_headers, served["session_id"], action="buy",
                         quantity=20, linked_inference_id=served["inference_id"])
        assert replay.status_code == 400, replay.text
        assert replay.json()["detail"]["reason"] == "inference_version_mismatch"
        desk = _paper_decision(
            client, auth_headers, served["portfolio_id"], served["inference_id"]
        )
        assert desk.status_code == 400, desk.text
        assert desk.json()["detail"]["reason"] == "version_not_usable"

        assert account_orders(client, auth_headers, served["portfolio_id"]) == []
        assert self.paper_decisions(
            client, auth_headers, served["portfolio_id"]
        ) == []
        assert decision_history_json(
            client, auth_headers, served["portfolio_id"]
        )["records"] == []
        assert _replay_events(served["session_id"]) == []

    def test_every_lineage_refusal_arrives_as_a_code_and_a_sentence_with_no_write(
        self, client, auth_headers, served
    ):
        """Seven refusals, one shape each: formal code, readable message, nothing stored.

        ``reason`` is what the screen branches on and what the tests name; it is
        not free text. The message is what the owner reads. A refusal that answers
        500, or that carries only a prose string, or that leaves the order behind
        with the lineage quietly dropped, fails this node.

        The last two rows are the version leg. Each is measured at the desk against
        the same version the replay holds, because that is the only claim the two
        doors can compare; what the desk does when the request names no version is
        its own contract, and ``test_a_served_prediction_names_the_version_it_was_
        trained_from`` answers for it on a real prediction.
        """
        portfolio_id, session_id = served["portfolio_id"], served["session_id"]
        corpus = [
            # reason, status, origin, action, version the desk is driven with
            ("inference_not_found", 404, str(uuid.uuid4()), "buy", None),
            ("inference_metadata_unreadable", 400, _record_lineage_row(
                portfolio_id, ["not", "a", "dict"]), "buy", None),
            ("inference_not_model_execution", 400, _record_lineage_row(
                portfolio_id,
                {"prediction_type": "market_direction", "skill_unit_id": portfolio_id},
            ), "buy", None),
            ("inference_skill_mismatch", 400, _record_lineage_row(
                portfolio_id,
                {"prediction_type": "personal_decision",
                 "skill_unit_id": str(uuid.uuid4())},
            ), "buy", None),
            ("inference_version_unnamed", 400, _record_lineage_row(
                portfolio_id,
                {"prediction_type": "personal_decision", "skill_unit_id": portfolio_id},
            ), "buy", served["version_id"]),
            ("inference_version_mismatch", 400, served["inference_id"], "buy",
             served["version_id"]),
            # The same valid inference, on the one action this door cannot carry a
            # lineage for: refused for the row, not for the origin.
            ("inference_needs_decision_row", 400, served["inference_id"], "hold", None),
        ]

        for reason, status, inference_id, action, desk_version in corpus:
            resp = _submit(client, auth_headers, session_id, action=action,
                           quantity=20 if action == "buy" else None,
                           linked_inference_id=inference_id)
            assert resp.status_code == status, f"{reason}: {resp.status_code} {resp.text}"
            assert resp.status_code < 500, f"{reason} surfaced as a server error"
            detail = resp.json()["detail"]
            assert isinstance(detail, dict), f"{reason}: no structured refusal: {detail!r}"
            assert detail["reason"] == reason, detail
            message = detail.get("message")
            assert isinstance(message, str) and len(message) > 20, (
                f"{reason}: refused without a sentence the owner can read: {detail!r}"
            )

            # The desk door answers the same refusal for the same row, except for
            # the hold case, which is this door's own shape (the desk creates the
            # row a hold has here).
            if reason != "inference_needs_decision_row":
                desk = _paper_decision(client, auth_headers, portfolio_id, inference_id,
                                       desk_version)
                assert desk.status_code == status, f"{reason} desk={desk.status_code}: {desk.text}"
                assert desk.json()["detail"]["reason"] == reason, desk.text

        # Refused means nothing was written, on either door, for any of the seven.
        assert account_orders(client, auth_headers, portfolio_id) == []
        assert self.paper_decisions(client, auth_headers, portfolio_id) == []
        assert journal_json(client, auth_headers, portfolio_id)["records"] == []
        assert decision_history_json(client, auth_headers, portfolio_id)["records"] == []
        assert _replay_events(session_id) == []


class TestOwnership:
    def test_another_user_cannot_drive_this_replay_or_read_this_book(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        order_id = _submit(client, auth_headers, session_id, action="buy",
                           quantity=20).json()["order"]["order_id"]

        client.post("/api/auth/register", json={
            "risk_acknowledged": True,
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
            assert _book_counts(db, portfolio_id)["orders"] == 1, (
                f"one key booked more than one order in this account: {codes}"
            )
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
        from app.models.paper_portfolio import OrderStatus, PaperOrder, PaperPortfolio
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
            book = _book_counts(db, portfolio_id)
            assert book == {"orders": 1, "fills": 2}, (
                f"this account did not end with one order and two fills: {book}"
            )
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
        from app.models.paper_portfolio import OrderStatus, PaperOrder

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
            assert _book_counts(db, portfolio_id)["fills"] == len(filled), (
                f"fills exist outside the orders that earned them: {results}"
            )
        finally:
            db.close()
