"""One risk policy, asked on the server, before any lane books a trade.

The D1 gap was not that the limits were wrong — it was that they were asked in
one place only. The direct lane ran ``_check_risk_limits``; the session lane
wrote ``risk_check_status=passed`` unconditionally, so a paused account accepted
a brand-new entry, and neither the daily entry budget nor the position cap
existed outside the direct lane. Switching lanes therefore switched the policy.

What this file pins, through the real HTTP routes on every lane that can touch a
paper portfolio:

* a paused account refuses *new exposure* and still accepts an exit — on the
  direct, session and rule lanes alike;
* a refusal never becomes an executable pending order, and never a row labelled
  ``passed``;
* the daily budget is counted per decision date, admits entries only, counts
  pending and cancelled entries, and a same-key retry spends one slot;
* the position cap is a share of the book valued at the decision-time price, and
  an unpriceable book under a binding cap is refused rather than passed;
* a policy allow-list narrows every lane, so a lane cannot widen it.

Figures are derived here from the authored bars, not from the implementation:
cash 100,000; a BUY 20 executed at B2's open books -2206.6044; a SELL 20 at B3's
open books +2233.2845. The direct lane prices off the demo fixture's last stored
bar, read from ``DEMO_DATA`` and converted with the convention stated in
``_expected_direct_fill`` — the same convention the journal reports.

Passing here proves server-side admission control on synthetic bars. It is not
evidence about a real broker or real market data, and the fill-time affordability
re-check that already exists is covered by the lifecycle suite rather than
duplicated here.
"""

import threading
import uuid
from datetime import date

import pytest

from tests.conftest import TEST_ENGINE, TestSession
from tests.test_manual_decision_books_linked_portfolio import (
    B1,
    B1_CLOSE,
    BUY20_B2_NET,
    INITIAL_CASH,
    PROVIDER,
    SYMBOL,
    _advance,
    _open_account,
    _submit,
    account,
    account_orders,
    fills,
    journal_json,
    open_qty,
    replay_decisions,
    seed_authored_bars,
)
from tests.test_rule_triggered_paper_execution import (
    _journal as rule_decisions,
    _link_portfolio as _rule_link_portfolio,
    _locked_version as _rule_locked_version,
    _orders as rule_orders,
    _portfolio as rule_portfolio,
    _start_session as _rule_start_session,
    _step_to_entry,
)

# SELL 20 at B3's open, by the convention declared in the lifecycle suite:
#   booked price = round(112.0 * (1 - 0.002), 4) = 111.776
#   gross        = round(20 * 111.776, 4)        = 2235.52
#   commission   = round(2235.52 * 0.001, 4)     = 2.2355
#   net cash     = gross - commission            = +2233.2845
SELL20_B3_PRICE = 111.776
SELL20_B3_NET = 2233.2845

# The account's entry price at submit time: B1's close, the bar the user watched.
DECISION_PRICE = B1_CLOSE

# 100,000 x 0.5 / 105 = 476.19 shares. 476 fits, 477 does not.
CAP_HALF_FITS = 476
CAP_HALF_REFUSED = 477

RULE_INITIAL_CASH = 10_000.0

_IS_SQLITE = TEST_ENGINE.dialect.name == "sqlite"
postgres_only = pytest.mark.skipif(
    _IS_SQLITE,
    reason="row-lock serialisation requires a real PostgreSQL backend",
)


@pytest.fixture(autouse=True)
def _seed_datasets(setup_db):
    """Both datasets this file's lanes read.

    The session lane walks the five authored February bars; the rule lane's
    session creation resolves its provider from the January demo rows. Written
    from the fixtures themselves, never invented.
    """
    from datetime import date, datetime

    from app.data.demo_data import DEMO_DATA
    from app.models.market_data import MarketDataBar

    db = TestSession()
    try:
        seed_authored_bars(db)
        for date_str, prices in DEMO_DATA[SYMBOL].items():
            if not date_str.startswith("2024-01"):
                continue
            day = date.fromisoformat(date_str)
            db.add(MarketDataBar(
                instrument_id=SYMBOL,
                provider_id=PROVIDER,
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=prices["open"],
                high=prices["high"],
                low=prices["low"],
                close=prices["close"],
                volume=1_000_000,
            ))
        db.commit()
    finally:
        db.close()


def _pause(client, headers, portfolio_id):
    resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _resume(client, headers, portfolio_id):
    resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cancel(client, headers, portfolio_id, order_id):
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/cancel", headers=headers
    )


def _direct_portfolio(client, headers, *, cash=INITIAL_CASH):
    resp = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _direct_order(client, headers, portfolio_id, *, side, quantity, key=None):
    return client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers, json={
        "asset_id": SYMBOL,
        "side": side,
        "quantity": quantity,
        "order_type": "market",
        "rationale": "risk policy regression",
        "idempotency_key": key or f"direct-{uuid.uuid4()}",
    })


def _demo_last_open():
    from app.data.demo_data import DEMO_DATA

    bars = DEMO_DATA[SYMBOL]
    return float(bars[max(bars)]["open"])


def _expected_direct_fill(side, quantity, open_price):
    """The booked price and net cash the journal must report.

    Slippage is inside the booked price; commission is charged on the gross at
    that price and rounded to four places, exactly as ``fill_math`` states.
    """
    rate = 0.001
    execution_price = round(open_price * (1 + 0.001 if side == "BUY" else 1 - 0.001), 4)
    gross = round(quantity * execution_price, 4)
    commission = round(gross * rate, 4)
    net = -(gross + commission) if side == "BUY" else gross - commission
    return execution_price, round(net, 4)


def _set_policy(monkeypatch, **fields):
    """Replace the module-level policy with a narrower one for this test."""
    from app.services import risk_policy

    monkeypatch.setattr(
        risk_policy, "DEFAULT_RISK_POLICY", risk_policy.RiskPolicy(**fields)
    )
    return risk_policy


def _paper_order_count():
    from app.models.paper_portfolio import PaperOrder

    db = TestSession()
    try:
        return db.query(PaperOrder).count()
    finally:
        db.close()


def _delete_bar(bar_date):
    from app.models.market_data import MarketDataBar

    db = TestSession()
    try:
        deleted = (
            db.query(MarketDataBar)
            .filter(
                MarketDataBar.instrument_id == SYMBOL,
                MarketDataBar.provider_id == PROVIDER,
                MarketDataBar.bar_date == bar_date,
            )
            .delete()
        )
        db.commit()
        return deleted
    finally:
        db.close()


class TestAPausedAccountRefusesExposureOnly:
    """Pausing stops new risk. It must not trap an account in an open position."""

    def test_the_session_lane_refuses_a_new_entry_before_it_becomes_pending(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        _pause(client, auth_headers, portfolio_id)

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["fill"] is None
        assert body["order"] is None, "a refused entry must not become a pending order"
        assert body["execution"] is None
        assert body["rejection"]["reason"] == "portfolio_paused"
        assert body["rejection"]["booked_at_submit"] is False
        assert body["decision"]["decision_type"] == "blocked_action"

        assert account_orders(client, auth_headers, portfolio_id) == []
        assert _paper_order_count() == 0
        assert open_qty(client, auth_headers, portfolio_id) == 0
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH)

        refusals = [
            d for d in replay_decisions(client, auth_headers, session_id)
            if (d.get("decision_detail") or {}).get("rejection", {}).get("reason")
            == "portfolio_paused"
        ]
        assert len(refusals) == 1, "the replay must show the refusal, not hide it"

        # Resume admits a fresh entry; the refused one is not resurrected.
        _resume(client, auth_headers, portfolio_id)
        assert account_orders(client, auth_headers, portfolio_id) == []

        again = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert again.status_code == 200, again.text
        assert again.json()["order"]["status"] == "pending"

        assert _advance(client, auth_headers, session_id).status_code == 200
        filled = account_orders(client, auth_headers, portfolio_id)
        assert len(filled) == 1
        assert filled[0]["status"] == "filled"
        assert filled[0]["risk_check_status"] == "passed"
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

    def test_the_session_lane_still_lets_a_paused_account_close(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        entry = _submit(client, auth_headers, session_id, action="buy", quantity=20)
        assert entry.json()["order"]["status"] == "pending"
        assert _advance(client, auth_headers, session_id).status_code == 200
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET, abs=1e-6)

        _pause(client, auth_headers, portfolio_id)
        exit_resp = _submit(client, auth_headers, session_id, action="sell", quantity=20)
        assert exit_resp.status_code == 200, exit_resp.text
        body = exit_resp.json()
        assert body["order"] is not None, "pausing must not block closing risk"
        assert body["order"]["status"] == "pending"
        assert body["rejection"]["reason"] == "awaiting_execution_window"

        assert _advance(client, auth_headers, session_id).status_code == 200
        sells = [o for o in account_orders(client, auth_headers, portfolio_id)
                 if o["side"] == "SELL"]
        assert len(sells) == 1
        assert sells[0]["status"] == "filled"
        assert sells[0]["risk_check_status"] == "passed"

        legs = fills(client, auth_headers, portfolio_id, sells[0]["id"])
        assert len(legs) == 1
        assert float(legs[0]["execution_price_before_costs"]) == pytest.approx(
            SELL20_B3_PRICE
        )
        assert float(legs[0]["net_cash_change"]) == pytest.approx(SELL20_B3_NET)

        assert open_qty(client, auth_headers, portfolio_id) == 0
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + BUY20_B2_NET + SELL20_B3_NET, abs=1e-6)

    def test_a_paused_account_can_still_close_on_the_direct_lane(
        self, client, auth_headers
    ):
        portfolio_id = _direct_portfolio(client, auth_headers)
        open_price = _demo_last_open()

        bought = _direct_order(client, auth_headers, portfolio_id, side="BUY", quantity=10)
        assert bought.status_code == 201, bought.text
        assert bought.json()["status"] == "filled"
        buy_price, buy_net = _expected_direct_fill("BUY", 10, open_price)

        _pause(client, auth_headers, portfolio_id)
        sold = _direct_order(client, auth_headers, portfolio_id, side="SELL", quantity=10)
        assert sold.status_code == 201, sold.text
        assert sold.json()["status"] == "filled"
        assert sold.json()["risk_check_status"] == "passed"

        sell_price, sell_net = _expected_direct_fill("SELL", 10, open_price)
        legs = fills(client, auth_headers, portfolio_id, sold.json()["id"])
        assert len(legs) == 1
        assert float(legs[0]["execution_price_before_costs"]) == pytest.approx(sell_price)
        assert float(legs[0]["net_cash_change"]) == pytest.approx(sell_net)

        assert open_qty(client, auth_headers, portfolio_id) == 0
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH + buy_net + sell_net, abs=1e-6)

        buy_legs = fills(client, auth_headers, portfolio_id, bought.json()["id"])
        assert len(buy_legs) == 1
        assert float(buy_legs[0]["execution_price_before_costs"]) == pytest.approx(buy_price)
        assert float(buy_legs[0]["net_cash_change"]) == pytest.approx(buy_net)


class TestTheRuleLaneIsUnderTheSamePolicy:
    def test_a_paused_account_does_not_let_a_rule_open_a_position(
        self, client, auth_headers
    ):
        version_id = _rule_locked_version(client, auth_headers)
        session_id = _rule_start_session(client, auth_headers, version_id)
        portfolio_id = _rule_link_portfolio(client, auth_headers, session_id)
        _pause(client, auth_headers, portfolio_id)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None, "the replay reached no entry at all"

        assert rule_orders(client, auth_headers, portfolio_id) == []
        blocked = [
            d for d in rule_decisions(client, auth_headers, portfolio_id)
            if (d.get("risk_flags") or {}).get("blocked_reason") == "portfolio_paused"
        ]
        assert len(blocked) == 1, rule_decisions(client, auth_headers, portfolio_id)
        assert rule_portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] \
            == pytest.approx(RULE_INITIAL_CASH)

    def test_a_policy_allow_list_stops_a_rule_entry(
        self, client, auth_headers, monkeypatch
    ):
        _set_policy(monkeypatch, allowed_instruments=frozenset({"DEMO-BETA"}))

        version_id = _rule_locked_version(client, auth_headers)
        session_id = _rule_start_session(client, auth_headers, version_id)
        portfolio_id = _rule_link_portfolio(client, auth_headers, session_id)

        decision = _step_to_entry(client, auth_headers, session_id)
        assert decision is not None

        assert rule_orders(client, auth_headers, portfolio_id) == []
        blocked = [
            d for d in rule_decisions(client, auth_headers, portfolio_id)
            if (d.get("risk_flags") or {}).get("blocked_reason") == "instrument_not_allowed"
        ]
        assert len(blocked) == 1, rule_decisions(client, auth_headers, portfolio_id)
        assert rule_portfolio(client, auth_headers, portfolio_id)["current_virtual_cash"] \
            == pytest.approx(RULE_INITIAL_CASH)


class TestTheDailyEntryBudget:
    def test_a_retry_spends_one_slot_and_the_eleventh_entry_is_refused(
        self, client, auth_headers
    ):
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        keys = [f"daily-{i}" for i in range(10)]

        order_ids = []
        for key in keys:
            resp = _submit(client, auth_headers, session_id, action="buy", quantity=1, key=key)
            assert resp.status_code == 200, resp.text
            assert resp.json()["order"] is not None, resp.text
            order_ids.append(resp.json()["order"]["order_id"])

        # Same key, same payload: the original intent is replayed, not a second
        # slot spent.
        retry = _submit(client, auth_headers, session_id, action="buy", quantity=1, key=keys[0])
        assert retry.status_code == 200, retry.text
        assert retry.json()["order"]["order_id"] == order_ids[0]
        assert len(account_orders(client, auth_headers, portfolio_id)) == 10

        # A withdrawn entry still counted against that decision date.
        cancelled = _cancel(client, auth_headers, portfolio_id, order_ids[1])
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["order"]["status"] == "cancelled"

        eleventh = _submit(
            client, auth_headers, session_id, action="buy", quantity=1, key="daily-10"
        )
        assert eleventh.status_code == 200, eleventh.text
        body = eleventh.json()
        assert body["order"] is None
        assert body["rejection"]["reason"] == "daily_entry_limit"
        assert len(account_orders(client, auth_headers, portfolio_id)) == 10
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH)

    def test_exits_do_not_consume_the_direct_lane_entry_budget(
        self, client, auth_headers
    ):
        portfolio_id = _direct_portfolio(client, auth_headers)

        bought = _direct_order(client, auth_headers, portfolio_id, side="BUY", quantity=10)
        assert bought.status_code == 201, bought.text

        for _ in range(10):
            sold = _direct_order(
                client, auth_headers, portfolio_id, side="SELL", quantity=1
            )
            assert sold.status_code == 201, sold.text
            assert sold.json()["status"] == "filled"

        # Ten closes happened today; the entry budget is untouched.
        entries = [o for o in account_orders(client, auth_headers, portfolio_id)
                   if o["side"] == "BUY"]
        assert len(entries) == 1

        again = _direct_order(client, auth_headers, portfolio_id, side="BUY", quantity=1)
        assert again.status_code == 201, again.text
        assert again.json()["status"] == "filled"

    def test_the_eleventh_direct_entry_is_refused_and_journalled(
        self, client, auth_headers
    ):
        portfolio_id = _direct_portfolio(client, auth_headers)
        for _ in range(10):
            resp = _direct_order(client, auth_headers, portfolio_id, side="BUY", quantity=1)
            assert resp.status_code == 201, resp.text

        eleventh = _direct_order(client, auth_headers, portfolio_id, side="BUY", quantity=1)
        assert eleventh.status_code == 400, eleventh.text
        detail = eleventh.json()["detail"]
        assert "daily_entry_limit" in detail, detail

        orders = account_orders(client, auth_headers, portfolio_id)
        assert len([o for o in orders if o["status"] != "rejected"]) == 10
        assert orders[-1]["status"] == "rejected"
        assert orders[-1]["risk_check_status"] == "failed"

        rows = [r for r in journal_json(client, auth_headers, portfolio_id)["records"]
                if r["order_id"] == orders[-1]["id"]]
        assert len(rows) == 1
        assert rows[0]["record_type"] == "order"
        assert rows[0]["order_status"] == "rejected"
        assert rows[0]["record_classification"] == "closed"


class TestThePositionCap:
    def test_the_cap_is_a_share_of_the_book_at_the_decision_price(
        self, client, auth_headers, monkeypatch
    ):
        _set_policy(monkeypatch, max_position_pct=0.5)
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        # 477 x 105 = 50,085 > 50% of 100,000; 476 x 105 = 49,980 fits.
        refused = _submit(
            client, auth_headers, session_id, action="buy", quantity=CAP_HALF_REFUSED
        )
        assert refused.status_code == 200, refused.text
        assert refused.json()["order"] is None
        assert refused.json()["rejection"]["reason"] == "position_limit"
        assert account_orders(client, auth_headers, portfolio_id) == []

        allowed = _submit(
            client, auth_headers, session_id, action="buy", quantity=CAP_HALF_FITS
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["order"]["status"] == "pending"
        assert allowed.json()["order"]["quantity"] == CAP_HALF_FITS
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH)

    def test_a_cap_that_cannot_be_priced_refuses_instead_of_passing(
        self, client, auth_headers, monkeypatch
    ):
        _set_policy(monkeypatch, max_position_pct=0.5)
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        # The bar the decision would be sized against is not there.
        assert _delete_bar(__import__("datetime").date.fromisoformat(B1)) == 1

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=10)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["order"] is None, "an unpriceable book must not pass the cap"
        assert body["rejection"]["reason"] == "risk_price_unavailable"
        assert account_orders(client, auth_headers, portfolio_id) == []
        assert _paper_order_count() == 0

    def test_a_cap_of_one_hundred_percent_is_inert_without_a_price(
        self, client, auth_headers
    ):
        """The default cap cannot bind a cash-funded buy, so it must not need a mark.

        The declared limit is 100% of cash + positions: an order paid from cash
        can never exceed that book. Requiring a mark here would refuse legal
        trades whenever an estimate was missing.
        """
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]
        assert _delete_bar(__import__("datetime").date.fromisoformat(B1)) == 1

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=10)
        assert resp.status_code == 200, resp.text
        assert resp.json()["order"]["status"] == "pending"

    def test_a_policy_allow_list_narrows_the_session_lane(
        self, client, auth_headers, monkeypatch
    ):
        _set_policy(monkeypatch, allowed_instruments=frozenset({"DEMO-BETA"}))
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        resp = _submit(client, auth_headers, session_id, action="buy", quantity=1)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["order"] is None
        assert body["rejection"]["reason"] == "instrument_not_allowed"
        assert account_orders(client, auth_headers, portfolio_id) == []


@postgres_only
class TestConcurrencyCannotExceedTheEntryBudget:
    def test_two_racing_submits_with_a_budget_of_one_book_one_order(
        self, client, auth_headers, monkeypatch
    ):
        """The budget is not a read-then-write: the session's CAS is the guard.

        Both requests can see zero entries today before either writes. What must
        hold is that exactly one order survives — the loser is refused and its
        transaction rolled back whole, so the day's budget is not spent twice.
        """
        _set_policy(monkeypatch, max_daily_entries=1)
        ctx = _open_account(client, auth_headers, observed=1)
        session_id, portfolio_id = ctx["session_id"], ctx["portfolio_id"]

        codes = {}

        def submit(tag):
            codes[tag] = client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={
                    "action": "buy",
                    "quantity": 1,
                    "rationale": "racing the budget",
                    "idempotency_key": f"race-budget-{tag}",
                },
            ).status_code

        threads = [threading.Thread(target=submit, args=(str(i),)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        assert set(codes.values()) <= {200, 409}, codes
        assert len(account_orders(client, auth_headers, portfolio_id)) == 1
        assert _paper_order_count() == 1
        assert account(client, auth_headers, portfolio_id)["current_virtual_cash"] == \
            pytest.approx(INITIAL_CASH)
