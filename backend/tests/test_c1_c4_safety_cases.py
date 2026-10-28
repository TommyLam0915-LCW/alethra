"""C1-C4 safety cases for manual replay decisions, run offline through TestClient.

C1 concurrent duplicate (same key, same payload, two threads)
C2 sequential submissions with different keys
C3 retry after a lost response returns the original decision
C4 invalid payload leaves no partial state

A submission no longer executes: it records a pending intent against the bar the
user was watching, and the market clock (``advance``) is what turns that intent
into one fill and one cash movement. Each case therefore checks the duplicate
where the money actually moves — two racing or retrying requests must produce one
order, and after the clock runs, one fill and one cash movement, never two.

The fixture was written against a stale schema (``User.hashed_password``,
``Experiment.symbol``, string primary keys on UUID columns), so all four tests
errored at setup and none of these safety properties were actually being
checked. They now seed the real columns, an observed bar (a submission without a
market time is refused rather than guessed at), and the paper account the intent
settles into.

Limitation, stated rather than implied: these run on SQLite. Threads now hold
independent connections, so C1 exercises the idempotency and conditional-update
branches, but it cannot demonstrate real row-level locking; that belongs to the
PostgreSQL-gated suite.
"""
import os
import threading
from datetime import date, datetime
from tempfile import mkdtemp
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.auth.jwt import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import PaperFill, PaperOrder, PaperPortfolio
from app.models.simulation import SimulationDecision, SimulationSession
from app.models.user import User

# File-backed SQLite on NullPool: every session gets its own connection. The
# earlier StaticPool/:memory: setup handed all sessions one shared DBAPI
# connection, so the C1 threads interleaved on a single cursor and one
# thread's rollback discarded the other's uncommitted work — both requests
# returned 409 because of the harness, not the product.
_DB_DIR = mkdtemp(prefix="c1c4-")
_DB_FILE = os.path.join(_DB_DIR, "c1c4.db")
TEST_DB_URL = f"sqlite:///{_DB_FILE}"

engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=NullPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

client = TestClient(app)

SYMBOL = "US:AAPL"
USER_ID = uuid4()
EXPERIMENT_ID = uuid4()
VERSION_ID = uuid4()

# A decision needs a snapshot bar plus a later executable bar, so the series has
# to hold consecutive trading days.
BARS = [
    ("2024-01-02", 185.64, 186.0, 182.63, 185.64, 50000000),
    ("2024-01-03", 184.22, 185.0, 183.0, 184.25, 51000000),
    ("2024-01-04", 182.15, 183.0, 181.0, 182.0, 52000000),
    ("2024-01-05", 181.18, 182.0, 180.0, 181.0, 53000000),
    ("2024-01-08", 182.09, 183.0, 181.5, 182.5, 54000000),
]

# idempotency_key is unique across the table, not per session, so each case uses
# its own key and teardown clears exactly those.
IDEMPOTENCY_KEYS = (
    "c1_test_key_001", "c2_key_001", "c2_key_002",
    "c3_retry_key_001", "c4_invalid_key",
)

ACCOUNT_CASH = 100_000.0
FIRST_BAR = "2024-01-02"


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def isolated_app_db():
    """Own the dependency override for this module and restore it afterwards.

    conftest's ``client`` fixture clears app.dependency_overrides when it
    finishes, so an import-time override would silently disappear partway through
    a full-suite run and send these requests to the shared test database.
    """
    previous = dict(app.dependency_overrides)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    app.dependency_overrides[get_db] = override_get_db
    _seed_owner_and_version()
    try:
        yield
    finally:
        _clear_decisions()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _seed_owner_and_version():
    db = TestingSessionLocal()
    try:
        if db.query(User).filter(User.id == USER_ID).first() is None:
            db.add(User(
                id=USER_ID,
                email="test_c1_c4@example.com",
                password_hash="$2b$12$placeholderplaceholderplaceholderplaceholder00000",
                display_name="C1-C4 Test User",
                is_active=True,
            ))
            db.add(Experiment(
                id=EXPERIMENT_ID,
                owner_id=USER_ID,
                title="C1-C4 Test Experiment",
                current_version_id=VERSION_ID,
            ))
            db.add(ExperimentVersion(
                id=VERSION_ID,
                experiment_id=EXPERIMENT_ID,
                version_number=1,
                created_by=USER_ID,
                dataset_cutoff_at="2024-01-08",
                strategy_spec={
                    # SMA(2,60) over five bars never leaves warm-up, so nothing the
                    # rule engine books can be mistaken for the human path these
                    # cases exist to guard.
                    "template_type": "sma_crossover",
                    "asset_ids": [SYMBOL],
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-08",
                    "initial_cash": 100000,
                    "signal_parameters": {"short_window": 2, "long_window": 60},
                    "commission_rate": 0.001,
                    "slippage_rate": 0.002,
                },
            ))
            for d, o, h, l, c, v in BARS:
                bar_date = date.fromisoformat(d)
                db.add(MarketDataBar(
                    id=uuid4(),
                    instrument_id=SYMBOL,
                    provider_id="twelvedata",
                    bar_date=bar_date,
                    bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                    open=o, high=h, low=l, close=c, volume=v,
                ))
        db.commit()
    finally:
        db.close()


def _clear_decisions():
    db = TestingSessionLocal()
    try:
        db.query(SimulationDecision).filter(
            SimulationDecision.idempotency_key.in_(IDEMPOTENCY_KEYS)
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def get_auth_headers():
    token = create_access_token(data={"sub": str(USER_ID)})
    return {"Authorization": f"Bearer {token}"}


def create_test_session(provider_id="twelvedata", confirmed=True):
    """A fresh pending session plus the paper account its intents settle into.

    Returns ``(session_id, portfolio_id)``. Both are needed: a submission with no
    bound account has no book to settle against and the product refuses it by name
    rather than silently choosing or inventing one.
    """
    db = TestingSessionLocal()
    try:
        session_id = uuid4()
        portfolio_id = uuid4()
        db.add(SimulationSession(
            id=session_id,
            experiment_version_id=VERSION_ID,
            owner_id=USER_ID,
            initial_virtual_cash=ACCOUNT_CASH,
            data_cutoff_at="2024-01-08",
            provider_id=provider_id,
            provider_confirmed=confirmed,
            status="pending",
            paper_equity=ACCOUNT_CASH,
            total_decisions=0,
        ))
        db.add(PaperPortfolio(
            id=portfolio_id,
            owner_id=USER_ID,
            simulation_session_id=session_id,
            currency="USD",
            initial_virtual_cash=ACCOUNT_CASH,
            current_virtual_cash=ACCOUNT_CASH,
        ))
        db.commit()
        return session_id, portfolio_id
    finally:
        db.close()


def observe_first_bar(session_id, headers):
    """Run the market clock onto the dataset's first bar.

    A decision needs a market time: submitting before any bar has been observed is
    refused (400) rather than priced off a bar nobody has seen.
    """
    resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)
    assert resp.status_code == 200, resp.text
    # advance returns {session, decision}: the cursor lives on the session.
    assert resp.json()["session"]["current_snapshot_date"] == FIRST_BAR


def _fills_for(db, portfolio_id):
    """Every fill booked on one account, in execution order.

    Booked fills are what a duplicate would double-charge, so each case counts
    them through the account they belong to rather than through a single request's
    response body.
    """
    return (
        db.query(PaperFill)
        .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
        .filter(PaperOrder.portfolio_id == portfolio_id)
        .order_by(PaperFill.execution_date)
        .all()
    )


class TestC1ConcurrentDuplicate:
    """C1: Concurrent duplicate - same key, same payload, simultaneous requests."""

    def test_concurrent_duplicate_same_key(self):
        """One idempotency key yields one order, and one order yields one fill.

        The race used to be caught where the money moved, because submitting
        executed. Now it has two halves: a duplicate must not become a second
        pending order, and one order must not become two bookings once the market
        clock reaches it.
        """
        session_id, portfolio_id = create_test_session()
        headers = get_auth_headers()
        observe_first_bar(session_id, headers)

        payload = {
            "action": "buy",
            "quantity": 100,
            "rationale": "C1 concurrent test",
            "idempotency_key": "c1_test_key_001"
        }

        results = []
        errors = []

        def make_request():
            try:
                resp = client.post(
                    f"/api/v1/simulations/{session_id}/manual-decision",
                    json=payload,
                    headers=headers
                )
                results.append((resp.status_code, resp.json()))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=make_request) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 2

        # A duplicate must never surface as a 5xx or a 422: either the replay
        # (200) or a genuine key conflict (409).
        statuses = sorted({r[0] for r in results})
        assert all(s in (200, 201, 409) for s in statuses), f"Unexpected statuses: {results}"
        success_results = [r for r in results if r[0] in (200, 201)]
        assert len(success_results) >= 1, f"No successful results: {results}"

        # Neither winner may claim it executed: a submission is an intent, and an
        # intent must not read as a holding.
        for _, body in success_results:
            assert body["fill"] is None
            assert body["order"]["status"] == "pending"
            assert body["order"]["booked_at_submit"] is False
        order_ids = {body["order"]["order_id"] for _, body in success_results}
        assert len(order_ids) == 1, f"one key produced {len(order_ids)} orders: {order_ids}"

        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id,
                SimulationDecision.idempotency_key == "c1_test_key_001"
            ).all()
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"

            orders = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == portfolio_id
            ).all()
            assert len(orders) == 1, f"the duplicate became {len(orders)} orders"
            assert _fills_for(db, portfolio_id) == [], "a submission booked a fill"

            session = db.query(SimulationSession).filter(
                SimulationSession.id == session_id
            ).first()
            # One observed bar plus one accepted intent; the loser wrote nothing.
            assert session.total_decisions == 2, f"got {session.total_decisions}"
            # Nothing about the race moved money: the account still reads as the
            # cash the user started with, and the replay book is unchanged too.
            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH, abs=1e-9
            )
            assert float(session.paper_equity) == pytest.approx(ACCOUNT_CASH, abs=1e-9)
        finally:
            db.close()

        # The market clock is where a duplicate would actually cost money.
        resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)
        assert resp.status_code == 200, resp.text

        db = TestingSessionLocal()
        try:
            fills = _fills_for(db, portfolio_id)
            assert len(fills) == 1, f"{len(fills)} fills for one intent"
            fill = fills[0]
            assert float(fill.quantity) == 100
            assert fill.execution_date == "2024-01-03"

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            # Exactly one cash movement: the balance is the starting cash plus this
            # fill's net, with no second debit hiding in it.
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH + float(fill.net_cash_change), abs=1e-6
            )
            order = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == portfolio_id
            ).one()
            assert order.status.value == "filled"
        finally:
            db.close()


class TestC2DifferentKeysStaleStep:
    """C2: Different keys against the same observed bar."""

    def test_different_keys_same_step(self):
        """Two keys on one bar make two decisions, one order, and one booking.

        This case used to forbid two decisions from sharing a snapshot date,
        because each decision executed and moved the cursor by itself. Under the
        lifecycle contract ``advance`` owns the cursor, so several intents
        legitimately sit on the bar the user is watching. What still must not
        happen twice is the money: the pair must produce one order, and the clock
        must book it once.
        """
        session_id, portfolio_id = create_test_session()
        headers = get_auth_headers()
        observe_first_bar(session_id, headers)

        resp1 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json={
                "action": "buy",
                "quantity": 50,
                "rationale": "C2 first request",
                "idempotency_key": "c2_key_001",
            },
            headers=headers,
        )
        assert resp1.status_code in (200, 201), resp1.text
        assert resp1.json()["fill"] is None
        assert resp1.json()["order"]["status"] == "pending"

        resp2 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json={"action": "hold", "idempotency_key": "c2_key_002"},
            headers=headers,
        )
        assert resp2.status_code in (200, 201), resp2.text
        assert resp2.json()["order"] is None, "a HOLD placed an order"

        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).order_by(SimulationDecision.step_number).all()

            # One observed bar plus the two intents, each on its own step.
            assert len(decisions) == 3, f"{len(decisions)} decisions recorded"
            assert [d.step_number for d in decisions] == [1, 2, 3], (
                f"steps not sequential: {[d.step_number for d in decisions]}"
            )

            session = db.query(SimulationSession).filter(
                SimulationSession.id == session_id
            ).first()
            assert session.total_decisions == len(decisions), "total_decisions mismatch"

            # Both intents were decided on the bar the user had watched, and
            # neither submission moved the clock: that is the pairing the old case
            # called a stale step, now explicit instead of forbidden.
            assert {d.snapshot_date for d in decisions[1:]} == {FIRST_BAR}
            assert session.current_snapshot_date == FIRST_BAR

            # One intent, one order; the hold placed none. Nothing booked yet.
            orders = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == portfolio_id
            ).all()
            assert len(orders) == 1, f"the pair produced {len(orders)} orders"
            assert _fills_for(db, portfolio_id) == []

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH, abs=1e-9
            )
        finally:
            db.close()

        # The window opens once, so the single order fills once.
        resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["session"]["current_snapshot_date"] == "2024-01-03"

        db = TestingSessionLocal()
        try:
            fills = _fills_for(db, portfolio_id)
            assert len(fills) == 1, f"{len(fills)} fills for one order"
            assert float(fills[0].quantity) == 50
            assert fills[0].execution_date == "2024-01-03"

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH + float(fills[0].net_cash_change), abs=1e-6
            )
        finally:
            db.close()


class TestC3ResponseLostAfterCommit:
    """C3: Response lost after commit - retry with same key."""

    def test_retry_after_commit(self):
        """A retry replays the original intent instead of placing a second one.

        The retry used to be caught by comparing the book each response claimed,
        because submitting executed. Now the intent is still pending at retry time,
        so the replay has to return the same order as well as the same decision, and
        the double-charge check belongs on the market clock: one order may only
        become one fill, in both books.
        """
        session_id, portfolio_id = create_test_session()
        headers = get_auth_headers()
        observe_first_bar(session_id, headers)

        payload = {
            "action": "buy",
            "quantity": 75,
            "rationale": "C3 retry test",
            "idempotency_key": "c3_retry_key_001"
        }

        resp1 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp1.status_code in (200, 201), resp1.text
        body1 = resp1.json()

        resp2 = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )
        assert resp2.status_code in (200, 201), resp2.text
        body2 = resp2.json()

        assert body2["decision"]["id"] == body1["decision"]["id"], (
            "retry should return the same decision ID"
        )
        assert body2["order"]["order_id"] == body1["order"]["order_id"], (
            "retry placed a second order"
        )
        # The replay must not read as an execution either: an intent the user sees
        # twice is still an intent, not a holding.
        assert body2["fill"] is None
        assert body2["order"]["status"] == "pending"
        assert body2["order"]["booked_at_submit"] is False

        db = TestingSessionLocal()
        try:
            decisions = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id,
                SimulationDecision.idempotency_key == "c3_retry_key_001"
            ).all()
            assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"

            decision = decisions[0]
            detail = decision.decision_detail or {}
            assert detail["booked_at_submit"] is False
            assert detail.get("fill") is None, "the recorded intent carried a fill"
            # A submission copies the book rather than moving it, and it was the
            # first accepted intent on this account: nothing had been booked yet.
            assert float(decision.paper_cash) == pytest.approx(ACCOUNT_CASH, abs=1e-9)
            assert float(decision.paper_equity) == pytest.approx(ACCOUNT_CASH, abs=1e-9)
            assert decision.paper_position is None

            session = db.query(SimulationSession).filter(
                SimulationSession.id == session_id
            ).first()
            assert session.total_decisions == 2, f"got {session.total_decisions}"

            orders = db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == portfolio_id
            ).all()
            assert len(orders) == 1, f"the retry became {len(orders)} orders"
            assert _fills_for(db, portfolio_id) == [], "a submission booked a fill"

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH, abs=1e-9
            )
        finally:
            db.close()

        # The clock is where a duplicated intent would charge twice.
        resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)
        assert resp.status_code == 200, resp.text

        db = TestingSessionLocal()
        try:
            fills = _fills_for(db, portfolio_id)
            assert len(fills) == 1, f"{len(fills)} fills for one intent"
            assert float(fills[0].quantity) == 75
            assert fills[0].execution_date == "2024-01-03"

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH + float(fills[0].net_cash_change), abs=1e-6
            )

            # The replay's own book saw the fill exactly once: 75 shares, not 150.
            step = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id,
                SimulationDecision.snapshot_date == "2024-01-03",
            ).one()
            manual_fills = (step.decision_detail or {}).get("manual_fills") or []
            assert len(manual_fills) == 1, f"{len(manual_fills)} manual fills in one step"
            assert sum(float(f["quantity"]) for f in manual_fills) == 75

            session = db.query(SimulationSession).filter(
                SimulationSession.id == session_id
            ).first()
            assert (session.current_position_json or {}).get("shares") == 75, (
                f"position applied more than once: {session.current_position_json}"
            )
        finally:
            db.close()


class TestC4TransactionFailure:
    """C4: Transaction failure / rollback."""

    def test_invalid_payload_no_partial_state(self):
        """Invalid payload should not leave partial state."""
        session_id, portfolio_id = create_test_session()
        headers = get_auth_headers()

        db = TestingSessionLocal()
        try:
            session_before = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            equity_before = session_before.paper_equity
            decisions_before = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).count()
        finally:
            db.close()

        payload = {
            "action": "buy",
            "quantity": -10,  # Invalid
            "idempotency_key": "c4_invalid_key"
        }

        resp = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            json=payload,
            headers=headers
        )

        # Should fail validation
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

        # Verify no partial state
        db = TestingSessionLocal()
        try:
            session_after = db.query(SimulationSession).filter(SimulationSession.id == session_id).first()
            decisions_after = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == session_id
            ).count()

            assert session_after.paper_equity == equity_before, "Equity should not change"
            assert session_after.total_decisions == 0, "total_decisions should not change"
            assert session_after.current_snapshot_date is None, "snapshot date must not advance"
            assert decisions_after == decisions_before, "Decision count should not change"

            # The rows a lifecycle submission writes are the ones that must not
            # survive: an order nobody submitted, a decision nobody made, or a key
            # that would then refuse the user's corrected retry with 409.
            assert db.query(PaperOrder).filter(
                PaperOrder.portfolio_id == portfolio_id
            ).count() == 0, "a rejected payload left an order"
            assert _fills_for(db, portfolio_id) == []
            assert db.query(SimulationDecision).filter(
                SimulationDecision.idempotency_key == "c4_invalid_key"
            ).count() == 0, "a rejected payload claimed the idempotency key"

            portfolio = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == portfolio_id
            ).first()
            assert float(portfolio.current_virtual_cash) == pytest.approx(
                ACCOUNT_CASH, abs=1e-9
            )
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
