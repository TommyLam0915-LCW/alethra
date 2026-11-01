"""A rule that fires must trade the account, not only the replay log.

Every test here drives the loop through the HTTP API: lock a version, start a
replay over the synthetic demo bars, link a paper portfolio to that session,
then advance. What is asserted afterwards is the *account* — order, fill,
position, cash and journal — because a step that wrote only a
``SimulationDecision`` row would satisfy the replay's own assertions while
leaving the portfolio untouched. That was the gap these tests close.

Prices come from ``app/data/demo_data.py`` (DEMO-ALPHA, synthetic, deterministic)
so an expected fill price can be stated exactly rather than approximately.
"""

import uuid
from types import SimpleNamespace

import pytest

from tests.conftest import TestSession


# DEMO-ALPHA bars for the sessions these tests replay, straight from the demo
# fixture: the sharp January decline is what makes a declared stop fire, and the
# early-January rise is what makes a declared target fire.
ENTRY_SESSION = "2024-01-02"          # open 50.0
TARGET_SESSION = "2024-01-04"         # high 52.5, open 51.0, close 52.0
STOP_SESSION = "2024-01-19"           # low 44.5, open 45.5 (gaps through 47.5)
LAST_SESSION = "2024-01-31"           # close 52.0
ENTRY_PRICE = 50.0
TARGET_LEVEL = 52.5
STOP_LEVEL = 47.5
GAPPED_STOP_OPEN = 45.5
STOP_SESSION_CLOSE = 45.0
LAST_CLOSE = 52.0


@pytest.fixture(autouse=True)
def _seed_demo_market_data(setup_db):
    """Store demo bars so simulation-session creation can resolve a provider.

    Session creation 400s without stored bars in the version range. The replay
    itself prices off ``get_demo_ohlcv``, so these rows only select the provider;
    they are seeded from the same fixture the engine reads, never invented.
    """
    from app.data.demo_data import DEMO_DATA
    from app.models.market_data import MarketDataBar
    from datetime import date, datetime

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
    yield


def _locked_version(client, headers, *, risk_parameters=None, end_date=LAST_SESSION, **spec_extra):
    strategy_spec = {
        "template_type": "buy_and_hold",
        "asset_ids": ["DEMO-ALPHA"],
        "start_date": ENTRY_SESSION,
        "end_date": end_date,
        "initial_cash": 10000,
        **spec_extra,
    }
    if risk_parameters is not None:
        strategy_spec["risk_parameters"] = risk_parameters

    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": "Rule-triggered execution",
        "research_type": "quantitative",
        "strategy_spec": strategy_spec,
    })
    assert resp.status_code == 201, resp.text
    exp = resp.json()
    lock = client.put(
        f"/api/v1/experiments/{exp['id']}", headers=headers,
        json={"scope": {"status": "locked"}},
    )
    assert lock.status_code == 200, lock.text
    return exp["current_version_id"]


def _start_session(client, headers, version_id, cash=10000.0):
    resp = client.post(
        f"/api/v1/simulations/versions/{version_id}", headers=headers,
        json={"initial_virtual_cash": cash},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link_portfolio(client, headers, session_id, cash=10000.0):
    resp = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
        "simulation_session_id": session_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _advance(client, headers, session_id):
    """One replay step. A 400 means the replay is complete, not a test error."""
    return client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)


def _advance_until(client, headers, session_id, predicate, limit=40):
    """Advance until ``predicate`` accepts a decision, or the replay completes.

    No sleeping: each step is a completed request over a fixed bar series, so the
    loop is deterministic and terminates on the replay's own end-of-data 400.
    """
    for _ in range(limit):
        resp = _advance(client, headers, session_id)
        if resp.status_code == 400:
            return None
        assert resp.status_code == 200, resp.text
        decision = resp.json()["decision"]
        if predicate(decision):
            return decision
    raise AssertionError(f"replay did not reach the expected step within {limit} advances")


def _is_sell(decision):
    detail = decision.get("decision_detail") or {}
    return detail.get("side") == "SELL"


def _portfolio(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _orders(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _positions(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _journal(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _fills(client, headers, portfolio_id, order_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _only_fill(client, headers, portfolio_id):
    orders = _orders(client, headers, portfolio_id)
    assert len(orders) == 1, orders
    fills = _fills(client, headers, portfolio_id, orders[0]["id"])
    assert len(fills) == 1, fills
    return orders[0], fills[0]


def _step_to_entry(client, headers, session_id):
    return _advance_until(
        client, headers, session_id,
        lambda d: (d.get("decision_detail") or {}).get("side") == "BUY",
    )


class TestRuleEntryReachesTheAccount:
    def test_a_rule_entry_books_order_fill_position_cash_and_journal_together(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None
        assert decision["snapshot_date"] == ENTRY_SESSION

        order, fill = _only_fill(client, auth_headers, portfolio_id)

        # The order is the system's, points at the locked version, and carries the
        # replay position it came from rather than a client-supplied key.
        assert order["actor_type"] == "system"
        assert order["status"] == "filled"
        assert order["side"] == "BUY"
        assert order["quantity"] == 200
        assert order["asset_id"] == "DEMO-ALPHA"
        assert order["model_or_strategy_version_id"] == version_id
        assert order["data_cutoff_at"] == ENTRY_SESSION
        assert order["idempotency_key"] == f"sim-{session_id}-step-{decision['step_number']}"

        assert fill["execution_date"] == ENTRY_SESSION
        assert fill["observed_price"] == ENTRY_PRICE
        assert fill["execution_price_before_costs"] == ENTRY_PRICE
        assert fill["gross_amount"] == pytest.approx(200 * ENTRY_PRICE, abs=1e-6)
        assert fill["net_cash_change"] == pytest.approx(-200 * ENTRY_PRICE, abs=1e-6)
        assert fill["simulated_fill_reason"] == "rule_entry"

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(0.0, abs=1e-6)

        positions = _positions(client, auth_headers, portfolio_id)
        assert len(positions) == 1
        assert positions[0]["status"] == "open"
        assert positions[0]["quantity"] == 200
        assert positions[0]["average_entry_price"] == ENTRY_PRICE

        # The journal entry is the audit half of the same step: system actor,
        # attributed to this portfolio, this session and this locked version.
        journal = _journal(client, auth_headers, portfolio_id)
        assert len(journal) == 1
        record = journal[0]
        assert record["actor_type"] == "system"
        assert record["decision_type"] == "entry"
        assert record["simulation_session_id"] == session_id
        assert record["linked_experiment_version_id"] == version_id
        assert record["data_cutoff_at"] == ENTRY_SESSION
        assert record["risk_flags"]["auto_execution"] is True
        assert record["risk_flags"]["rules_locked"] is True

        # The account and the replay must describe the same trade, not two
        # similar-looking ones.
        assert decision["decision_detail"]["quantity"] == order["quantity"]
        assert decision["decision_detail"]["execution_price"] == fill["execution_price_before_costs"]
        assert decision["paper_cash"] == pytest.approx(account["current_virtual_cash"], abs=1e-6)

    def test_costs_are_charged_once_and_slippage_is_not_charged_again(
        self, client, auth_headers
    ):
        version_id = _locked_version(
            client, auth_headers, commission_rate=0.001, slippage_rate=0.001
        )
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        initial = _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"]

        assert _step_to_entry(client, auth_headers, session_id) is not None
        order, fill = _only_fill(client, auth_headers, portfolio_id)

        quantity = fill["quantity"]
        price = fill["execution_price_before_costs"]

        # Slippage lives in the execution price, so it must not also be deducted
        # from cash, and commission must be the rate applied to the stored gross
        # once.
        assert price == pytest.approx(round(ENTRY_PRICE * 1.001, 4), abs=1e-9)
        assert fill["observed_price"] == ENTRY_PRICE
        assert fill["slippage_amount"] == pytest.approx(abs(price - ENTRY_PRICE) * quantity, abs=1e-4)
        assert fill["gross_amount"] == pytest.approx(round(quantity * price, 4), abs=1e-6)
        assert fill["commission_amount"] == pytest.approx(
            round(fill["gross_amount"] * 0.001, 4), abs=1e-9
        )
        assert fill["net_cash_change"] == pytest.approx(
            -(fill["gross_amount"] + fill["commission_amount"]), abs=1e-6
        )

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(
            initial + fill["net_cash_change"], abs=1e-6
        )


class TestDeclaredLevelsActuallyExit:
    def test_a_declared_stop_exits_at_the_price_the_policy_evidenced(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers, risk_parameters={"stop_pct": 0.05})
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        assert _step_to_entry(client, auth_headers, session_id) is not None
        exit_decision = _advance_until(client, auth_headers, session_id, _is_sell)
        assert exit_decision is not None, "the declared stop never fired in the replay"

        assert exit_decision["snapshot_date"] == STOP_SESSION
        assert exit_decision["decision_detail"]["exit_reason"] == "stop"
        assert exit_decision["decision_detail"]["exit_level"] == pytest.approx(STOP_LEVEL, abs=1e-6)

        orders = _orders(client, auth_headers, portfolio_id)
        sells = [o for o in orders if o["side"] == "SELL"]
        assert len(sells) == 1, orders
        assert sells[0]["status"] == "filled"
        fill = _fills(client, auth_headers, portfolio_id, sells[0]["id"])[0]

        assert fill["execution_date"] == STOP_SESSION
        assert fill["simulated_fill_reason"] == "rule_stop"
        # The bar gapped through the level, so the honest fill is the worse open,
        # not the declared level and not that session's close.
        assert fill["observed_price"] == GAPPED_STOP_OPEN
        assert fill["execution_price_before_costs"] == GAPPED_STOP_OPEN
        assert fill["observed_price"] != STOP_LEVEL
        assert fill["observed_price"] != STOP_SESSION_CLOSE

        positions = _positions(client, auth_headers, portfolio_id)
        assert len(positions) == 1
        assert positions[0]["status"] == "closed"
        assert positions[0]["quantity"] == 0
        assert positions[0]["realized_pnl"] == pytest.approx(
            (GAPPED_STOP_OPEN - ENTRY_PRICE) * 200, abs=1e-6
        )

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(
            200 * GAPPED_STOP_OPEN, abs=1e-6
        )

        exits = [r for r in _journal(client, auth_headers, portfolio_id) if r["decision_type"] == "exit"]
        assert len(exits) == 1
        assert exits[0]["actor_type"] == "system"
        assert exits[0]["risk_flags"]["exit_reason"] == "stop"
        assert exits[0]["data_cutoff_at"] == STOP_SESSION

    def test_a_declared_target_fills_at_the_target_not_at_the_better_open(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers, risk_parameters={"target_pct": 0.05})
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        assert _step_to_entry(client, auth_headers, session_id) is not None
        exit_decision = _advance_until(client, auth_headers, session_id, _is_sell)
        assert exit_decision is not None, "the declared target never fired in the replay"

        assert exit_decision["snapshot_date"] == TARGET_SESSION
        assert exit_decision["decision_detail"]["exit_reason"] == "target"

        sells = [o for o in _orders(client, auth_headers, portfolio_id) if o["side"] == "SELL"]
        assert len(sells) == 1
        fill = _fills(client, auth_headers, portfolio_id, sells[0]["id"])[0]

        assert fill["execution_date"] == TARGET_SESSION
        assert fill["simulated_fill_reason"] == "rule_target"
        assert fill["execution_price_before_costs"] == TARGET_LEVEL
        # A gap up beyond the target does not earn the better open, and the fill
        # is not that session's close either.
        assert fill["execution_price_before_costs"] != 51.0
        assert fill["execution_price_before_costs"] != 52.0

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(200 * TARGET_LEVEL, abs=1e-6)
        assert _positions(client, auth_headers, portfolio_id)[0]["status"] == "closed"

    def test_an_open_position_is_force_closed_at_end_of_data(self, client, auth_headers):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        assert _step_to_entry(client, auth_headers, session_id) is not None
        # Run the replay out; the final advance reports completion with a 400.
        for _ in range(40):
            resp = _advance(client, auth_headers, session_id)
            if resp.status_code == 400:
                assert "complete" in resp.json()["detail"].lower()
                break
            assert resp.status_code == 200, resp.text
        else:
            raise AssertionError("replay never reported completion")

        replay = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers).json()
        last = replay[-1]
        assert last["decision_type"] == "forced_exit"
        assert last["decision_detail"]["exit_reason"] == "end_of_data"

        sells = [o for o in _orders(client, auth_headers, portfolio_id) if o["side"] == "SELL"]
        assert len(sells) == 1, "the end-of-data liquidation never reached the account"
        fill = _fills(client, auth_headers, portfolio_id, sells[0]["id"])[0]
        assert fill["execution_date"] == last["snapshot_date"]
        assert fill["simulated_fill_reason"] == "rule_end_of_data"
        assert fill["execution_price_before_costs"] == LAST_CLOSE

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(200 * LAST_CLOSE, abs=1e-6)
        positions = _positions(client, auth_headers, portfolio_id)
        assert positions[0]["status"] == "closed"
        assert positions[0]["realized_pnl"] == pytest.approx((LAST_CLOSE - ENTRY_PRICE) * 200, abs=1e-6)
        # A closed round trip leaves nothing unrealized, and that is a computed
        # zero rather than an unpriced one.
        assert account["total_unrealized_pnl"] == pytest.approx(0.0, abs=1e-6)


class TestFailureLeavesNothingHalfApplied:
    def test_a_rule_entry_the_account_cannot_afford_is_refused_whole(self, client, auth_headers):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id, cash=10000.0)
        # The replay sizes its own trade from its own cash; the account has less,
        # so the same signal must be refused rather than partially applied.
        portfolio_id = _link_portfolio(client, auth_headers, session_id, cash=100.0)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None
        assert decision["decision_detail"]["quantity"] == 200

        order, = _orders(client, auth_headers, portfolio_id)
        assert order["status"] == "rejected"
        assert order["risk_check_status"] == "failed"
        assert _fills(client, auth_headers, portfolio_id, order["id"]) == []
        assert _positions(client, auth_headers, portfolio_id) == []

        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(100.0, abs=1e-9)

        record, = _journal(client, auth_headers, portfolio_id)
        assert record["risk_flags"]["blocked_reason"] == "Insufficient cash for order"

    def test_a_rule_exit_cannot_sell_shares_the_account_does_not_hold(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers, risk_parameters={"stop_pct": 0.05})
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        # Pause across the entry so the account never takes the position, then
        # resume: the replay still holds 200 shares and its stop still fires.
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers
        ).status_code == 200
        assert _step_to_entry(client, auth_headers, session_id) is not None
        assert _orders(client, auth_headers, portfolio_id) == []
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/resume", headers=auth_headers
        ).status_code == 200

        exit_decision = _advance_until(client, auth_headers, session_id, _is_sell)
        assert exit_decision is not None
        assert exit_decision["decision_detail"]["quantity"] == 200

        sells = [o for o in _orders(client, auth_headers, portfolio_id) if o["side"] == "SELL"]
        assert len(sells) == 1
        assert sells[0]["status"] == "rejected"
        assert sells[0]["risk_check_status"] == "failed"
        assert _fills(client, auth_headers, portfolio_id, sells[0]["id"]) == []

        # No cash credited from nothing, and no short position invented.
        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(10000.0, abs=1e-9)
        assert _positions(client, auth_headers, portfolio_id) == []

        blocked = [
            r for r in _journal(client, auth_headers, portfolio_id)
            if "blocked_reason" in (r["risk_flags"] or {})
        ]
        assert len(blocked) == 2, blocked
        reasons = {r["risk_flags"]["blocked_reason"] for r in blocked}
        assert "portfolio_paused" in reasons
        oversell = next(r for r in reasons if r.startswith("Cannot sell"))
        assert oversell.endswith("only hold 0.0")


class TestStepIdempotency:
    def test_replaying_the_same_step_cannot_book_a_second_fill(self, client, auth_headers):
        from app.models.paper_portfolio import PaperPortfolio
        from app.models.simulation import SimulationDecision
        from app.services.paper_execution import apply_rule_decision

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None
        cash_before = _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"]
        order_before, fill_before = _only_fill(client, auth_headers, portfolio_id)

        # Re-apply the *stored* step, as a retry after an ambiguous failure or a
        # rebuilt worker would, using the row the replay itself wrote.
        db = TestSession()
        try:
            row = db.query(SimulationDecision).filter(
                SimulationDecision.session_id == uuid.UUID(session_id),
                SimulationDecision.step_number == decision["step_number"],
            ).one()
            replayed = SimpleNamespace(
                decision_type=getattr(row.decision_type, "value", row.decision_type),
                decision_detail=row.decision_detail,
                snapshot_date=row.snapshot_date,
            )
            portfolio_row = db.query(PaperPortfolio).filter(
                PaperPortfolio.id == uuid.UUID(portfolio_id)
            ).one()
            result = apply_rule_decision(
                db,
                portfolio=portfolio_row,
                session_id=row.session_id,
                step_number=row.step_number,
                strategy_version_id=uuid.UUID(version_id),
                result=replayed,
                commission_rate=0.0,
            )
            db.commit()
        finally:
            db.close()

        assert result["duplicate"] is True
        assert result["order_id"] == order_before["id"]
        assert result["status"] == "filled"

        order_after, fill_after = _only_fill(client, auth_headers, portfolio_id)
        assert order_after["id"] == order_before["id"]
        assert fill_after["id"] == fill_before["id"]
        assert _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] == pytest.approx(
            cash_before, abs=1e-9
        )
        assert len(_journal(client, auth_headers, portfolio_id)) == 1


class TestStatusGates:
    def test_a_paused_portfolio_records_the_blocked_entry_rather_than_dropping_it(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers
        ).status_code == 200

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None, "the replay itself should still have entered"

        # The replay traded its own book; the account took nothing and says why.
        assert _orders(client, auth_headers, portfolio_id) == []
        assert _positions(client, auth_headers, portfolio_id) == []
        assert _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] == pytest.approx(
            10000.0, abs=1e-9
        )
        record, = _journal(client, auth_headers, portfolio_id)
        assert record["actor_type"] == "system"
        assert record["decision_type"] == "entry"
        assert record["risk_flags"]["blocked_reason"] == "portfolio_paused"
        assert record["risk_flags"]["auto_execution"] is True

    def test_a_paused_portfolio_still_closes_risk(self, client, auth_headers):
        version_id = _locked_version(client, auth_headers, risk_parameters={"stop_pct": 0.05})
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        assert _step_to_entry(client, auth_headers, session_id) is not None
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers
        ).status_code == 200

        exit_decision = _advance_until(client, auth_headers, session_id, _is_sell)
        assert exit_decision is not None

        # Pausing stops new positions; it must not trap an existing one.
        sells = [o for o in _orders(client, auth_headers, portfolio_id) if o["side"] == "SELL"]
        assert len(sells) == 1
        assert sells[0]["status"] == "filled"
        fill = _fills(client, auth_headers, portfolio_id, sells[0]["id"])[0]
        assert fill["execution_price_before_costs"] == GAPPED_STOP_OPEN
        assert _positions(client, auth_headers, portfolio_id)[0]["status"] == "closed"
        assert _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] == pytest.approx(
            200 * GAPPED_STOP_OPEN, abs=1e-6
        )

    def test_a_retired_portfolio_trades_nothing(self, client, auth_headers):
        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        assert client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/retire", headers=auth_headers
        ).status_code == 200

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None

        assert _orders(client, auth_headers, portfolio_id) == []
        assert _positions(client, auth_headers, portfolio_id) == []
        assert _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] == pytest.approx(
            10000.0, abs=1e-9
        )
        record, = _journal(client, auth_headers, portfolio_id)
        assert record["risk_flags"]["blocked_reason"] == "portfolio_retired"


class TestLinkBoundaries:
    def test_a_session_without_a_linked_portfolio_stays_a_pure_replay(
        self, client, auth_headers
    ):
        from app.models.paper_portfolio import DecisionRecord, PaperFill, PaperOrder

        version_id = _locked_version(client, auth_headers)
        session_id = _start_session(client, auth_headers, version_id)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None
        assert decision["decision_type"] == "simulated_entry"

        db = TestSession()
        try:
            assert db.query(PaperOrder).count() == 0
            assert db.query(PaperFill).count() == 0
            assert db.query(DecisionRecord).count() == 0
        finally:
            db.close()

    def test_linking_a_missing_or_foreign_session_is_refused_the_same_way(
        self, client, auth_headers
    ):
        missing = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 1000.0,
            "currency": "USD",
            "simulation_session_id": str(uuid.uuid4()),
        })
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Simulation session not found for this user"

        # A second account's session must not be linkable, and the refusal must
        # not reveal that the session exists.
        other = client.post("/api/auth/register", json={
            "risk_acknowledged": True,
            "email": "other@alethra.io",
            "password": "otherpass123",
            "display_name": "Other User",
            "user_type": "individual",
        })
        assert other.status_code in (200, 201), other.text
        other_headers = {
            "Authorization": "Bearer " + client.post("/api/auth/login", json={
                "email": "other@alethra.io", "password": "otherpass123",
            }).json()["access_token"]
        }
        other_version = _locked_version(client, other_headers)
        other_session = _start_session(client, other_headers, other_version)

        foreign = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 1000.0,
            "currency": "USD",
            "simulation_session_id": other_session,
        })
        assert foreign.status_code == 404
        assert foreign.json()["detail"] == missing.json()["detail"]

        # And the owner can link their own session, so the check is not a blanket
        # refusal that would make the feature untestable in production.
        own = client.post("/api/v1/paper-portfolio", headers=other_headers, json={
            "initial_virtual_cash": 1000.0,
            "currency": "USD",
            "simulation_session_id": other_session,
        })
        assert own.status_code == 201, own.text
        assert own.json()["simulation_session_id"] == other_session


class TestRoundTripReconciliation:
    def test_a_completed_round_trip_reconciles_cash_fills_and_realized_pnl(
        self, client, auth_headers
    ):
        version_id = _locked_version(
            client, auth_headers,
            risk_parameters={"stop_pct": 0.05},
            commission_rate=0.001,
            slippage_rate=0.001,
        )
        session_id = _start_session(client, auth_headers, version_id)
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        initial = _portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"]

        assert _step_to_entry(client, auth_headers, session_id) is not None
        assert _advance_until(client, auth_headers, session_id, _is_sell) is not None

        orders = [o for o in _orders(client, auth_headers, portfolio_id) if o["status"] == "filled"]
        assert len(orders) == 2, orders
        fills = [
            _fills(client, auth_headers, portfolio_id, o["id"])[0] for o in orders
        ]
        fills.sort(key=lambda f: f["execution_date"])
        entry_fill, exit_fill = fills
        assert entry_fill["execution_date"] == ENTRY_SESSION
        assert exit_fill["execution_date"] == STOP_SESSION
        assert entry_fill["quantity"] == exit_fill["quantity"]

        account = _portfolio(client, auth_headers, portfolio_id)
        # Cash is the sum of the fill legs and nothing else: no cost appears
        # twice and no leg is missing.
        assert account["current_virtual_cash"] == pytest.approx(
            initial + entry_fill["net_cash_change"] + exit_fill["net_cash_change"], abs=1e-6
        )
        position = _positions(client, auth_headers, portfolio_id)[0]
        assert position["status"] == "closed"
        # Realised P&L carries both legs' commission, so it agrees with the cash
        # movement to within the entry-commission rounding.
        assert position["realized_pnl"] == pytest.approx(
            account["current_virtual_cash"] - initial, abs=1e-3
        )
        assert account["total_realized_pnl"] == pytest.approx(position["realized_pnl"], abs=1e-6)

        replay = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers).json()
        traded_steps = [
            d for d in replay
            if (d.get("decision_detail") or {}).get("side") in ("BUY", "SELL")
        ]
        assert len(traded_steps) == len(orders), (
            "every replay step that traded must have booked exactly one account order"
        )
        assert [d["snapshot_date"] for d in traded_steps] == [f["execution_date"] for f in fills]

        journal = _journal(client, auth_headers, portfolio_id)
        assert sorted(r["decision_type"] for r in journal) == ["entry", "exit"]
        assert all(r["simulation_session_id"] == session_id for r in journal)
        assert all(r["linked_experiment_version_id"] == version_id for r in journal)


class TestReplaySessionListing:
    """The dashboard holds the replay only in component state.

    Without a listing endpoint a reload silently dropped the session, so the
    account a locked strategy was trading could no longer be reached from the
    replay that produced its fills.
    """

    def test_a_versions_sessions_are_listed_and_only_the_live_one_is_resumable(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers)
        retired = _start_session(client, auth_headers, version_id, cash=5000.0)
        live = _start_session(client, auth_headers, version_id, cash=7000.0)

        resp = client.post(
            f"/api/v1/simulations/{retired}/retire", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "retired"

        listing = client.get(
            f"/api/v1/simulations/versions/{version_id}/sessions", headers=auth_headers
        )
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert {r["id"] for r in rows} == {retired, live}
        assert all(r["experiment_version_id"] == version_id for r in rows)
        # Non-strict: both sessions are created within one clock tick, so the
        # assertion is about the contract (newest first), not tie-breaking.
        assert rows[0]["created_at"] >= rows[-1]["created_at"]

        resumable = [
            r for r in rows if r["status"] in ("pending", "active", "paused")
        ]
        assert [r["id"] for r in resumable] == [live], (
            "a retired replay must not be restored as if it could still trade"
        )
        assert {r["initial_virtual_cash"] for r in rows} == {5000.0, 7000.0}

    def test_another_account_cannot_list_this_versions_sessions(
        self, client, auth_headers
    ):
        version_id = _locked_version(client, auth_headers)
        _start_session(client, auth_headers, version_id)

        other = client.post("/api/auth/register", json={
            "risk_acknowledged": True,
            "email": "lister@alethra.io",
            "password": "listerpass123",
            "display_name": "Lister",
            "user_type": "individual",
        })
        assert other.status_code in (200, 201), other.text
        other_headers = {
            "Authorization": "Bearer " + client.post("/api/auth/login", json={
                "email": "lister@alethra.io", "password": "listerpass123",
            }).json()["access_token"]
        }

        foreign = client.get(
            f"/api/v1/simulations/versions/{version_id}/sessions", headers=other_headers
        )
        assert foreign.status_code == 403
        assert foreign.json()["detail"] == "Not authorized"

        anonymous = client.get(f"/api/v1/simulations/versions/{version_id}/sessions")
        assert anonymous.status_code == 401
