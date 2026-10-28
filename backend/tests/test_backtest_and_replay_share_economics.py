"""Backtest and replay must be one strategy, over one dataset, with one cost rule.

The product runs the same three templates through two separate engines: the batch
backtester (``app/engine/strategies.py``, reached through
``POST /api/v1/versions/{id}/runs``) and the per-bar forward replay
(``app/engine/simulation.py``, reached through ``POST /api/v1/simulations/versions/{id}``
plus ``advance``). A user who backtests a locked spec and then replays that same
spec is reading the same idea twice, so the two paths may not disagree about what
a trade cost — not by a rounding step, and not by which bar the fill priced off.

These tests pin the shared semantics by driving ONE locked spec over ONE stored
dataset through BOTH routes and comparing only economic columns (side, quantity,
execution date, observed price, execution price, commission, slippage, net cash
movement). Run ids and row ids are deliberately never compared: they differ every
time and prove nothing.

The dataset is a synthetic, clearly-labelled price ladder written into
``MarketDataBar`` at prices the demo fixture does not contain, so a path that read
the fixture instead of the bound dataset fails visibly. The rates are odd numbers
(0.0011 / 0.0017): they make ``open * (1 + slippage_rate)`` run past four decimal
places, which is exactly where an engine that rounds the price but books money
from the unrounded price starts to diverge from one that does not.

Passing here means the two engines are one arithmetic on synthetic data. It is
not evidence of profitability, and not evidence about real market data: live
replay and live paper trading remain separately BLOCKED on provider credentials.
"""

from datetime import date, datetime

import pytest

from app.models.market_data import MarketDataBar
from tests.conftest import TestSession

INSTRUMENT = "US:AAPL"
PROVIDER = "demo"
COMMISSION_RATE = 0.0011
SLIPPAGE_RATE = 0.0017
INITIAL_CASH = 10_000.0
# The human account starts larger than the spec's 10 000 so a strategy-sized
# order is never refused for affordability — a rejection would silently compare
# one lane against nothing. Fill arithmetic does not read the balance.
MANUAL_CASH = 100_000.0

TRADE_TYPES = {"simulated_entry", "simulated_exit", "forced_exit"}

MONEY_COLS = (
    "observed_price",
    "execution_price",
    "commission_amount",
    "slippage_amount",
    "net_cash_change",
)

# buy_and_hold: five bars, no declared risk plan, so the only legs are the entry
# at the first bar's open and the end-of-data liquidation at the last close.
BH_WINDOW = ["2025-09-01", "2025-09-02", "2025-09-03", "2025-09-04", "2025-09-05"]
BH_PRICES = [101.23, 102.31, 103.17, 104.79, 105.67]

# sma_crossover(3,5): a golden cross confirmed at index 6 (fills bar 7) and a
# death cross confirmed at index 10 (fills bar 11). Twelve bars, so both engines
# have room for the signal bar and its execution bar.
SMA_WINDOW = [f"2025-10-{day:02d}" for day in range(1, 13)]
SMA_PRICES = [50.00, 49.00, 48.00, 47.00, 46.00, 47.50,
              49.50, 51.50, 50.00, 47.00, 44.00, 42.00]

# price_change_fixed_hold(lookback 3, threshold 5%, hold 2): a -6% drop at index 3
# (fills bar 4) and the holding-period exit on bar 6's close.
PC_WINDOW = [f"2025-11-{day:02d}" for day in range(3, 11)]
PC_PRICES = [100.00, 101.00, 100.50, 94.00, 95.00, 96.00, 97.00, 98.00]


def _seed(window, prices, provider_id=PROVIDER, instrument=INSTRUMENT):
    """Write a stored daily ladder: open == close, ±0.9 intrabar range."""
    db = TestSession()
    try:
        for day_str, price in zip(window, prices):
            day = date.fromisoformat(day_str)
            db.add(MarketDataBar(
                instrument_id=instrument,
                provider_id=provider_id,
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=price,
                high=price + 0.9,
                low=price - 0.9,
                close=price,
                volume=1_000_000,
            ))
        db.commit()
    finally:
        db.close()


def _spec(template_type, window, signal_parameters=None):
    spec = {
        "template_type": template_type,
        "asset_ids": [INSTRUMENT],
        "start_date": window[0],
        "end_date": window[-1],
        "initial_cash": INITIAL_CASH,
        "commission_rate": COMMISSION_RATE,
        "slippage_rate": SLIPPAGE_RATE,
    }
    if signal_parameters:
        spec["signal_parameters"] = signal_parameters
    return spec


def _locked_version(client, headers, template_type, window, signal_parameters=None):
    """Draft → lock, the same two calls the strategy authoring UI makes."""
    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": f"P1-B parity {template_type}",
        "research_type": "quantitative",
        "strategy_spec": _spec(template_type, window, signal_parameters),
    })
    assert resp.status_code == 201, resp.text
    experiment = resp.json()

    lock = client.put(
        f"/api/v1/experiments/{experiment['id']}",
        headers=headers,
        json={"scope": {"status": "locked"}},
    )
    assert lock.status_code == 200, lock.text
    return experiment["current_version_id"]


def _run_backtest(client, headers, version_id):
    """The batch route over stored bars, returning (run, transactions)."""
    created = client.post(
        f"/api/v1/versions/{version_id}/runs",
        headers=headers,
        params={"data_source": "stored"},
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["status"] == "completed", run.get("error_details")
    return run, run["result_json"]["transactions"]


def _replay(client, headers, version_id):
    """Walk the stored calendar one bar per advance, then return the decisions."""
    created = client.post(
        f"/api/v1/simulations/versions/{version_id}",
        headers=headers,
        json={"initial_virtual_cash": INITIAL_CASH, "provider_id": PROVIDER},
    )
    assert created.status_code == 201, created.text
    session = created.json()

    for _ in range(40):
        resp = client.post(
            f"/api/v1/simulations/{session['id']}/advance", headers=headers
        )
        if resp.status_code == 400:
            assert "No more snapshots available" in resp.text, resp.text
            break
        assert resp.status_code == 200, resp.text
    else:
        pytest.fail("replay did not exhaust its stored dataset")

    decisions = client.get(
        f"/api/v1/simulations/{session['id']}/decisions", headers=headers
    ).json()
    session = client.get(f"/api/v1/simulations/{session['id']}", headers=headers).json()
    return session, decisions


def _backtest_legs(transactions):
    return [
        {
            "side": t["side"],
            "quantity": t["quantity"],
            "execution_date": t["execution_date"],
            "observed_price": t["observed_price"],
            "execution_price": t["execution_price_before_costs"],
            "commission_amount": t["commission_amount"],
            "slippage_amount": t["slippage_amount"],
            "net_cash_change": t["net_cash_change"],
        }
        for t in transactions
    ]


def _replay_legs(decisions):
    """The replay's legs, with net cash read from the stored cash progression."""
    legs = []
    previous_cash = INITIAL_CASH
    for decision in decisions:
        if decision["decision_type"] not in TRADE_TYPES:
            continue
        detail = decision["decision_detail"]
        cash = decision["paper_cash"]
        legs.append({
            "side": detail["side"],
            "quantity": detail["quantity"],
            "execution_date": decision["snapshot_date"],
            "observed_price": detail["observed_price"],
            "execution_price": detail["execution_price"],
            "commission_amount": detail["commission"],
            "slippage_amount": detail.get("slippage_amount"),
            "net_cash_change": round(cash - previous_cash, 4),
        })
        previous_cash = cash
    return legs


def _assert_same_legs(backtest_legs, replay_legs):
    assert [leg["side"] for leg in backtest_legs] == [leg["side"] for leg in replay_legs], (
        "the two engines traded different sides"
    )
    assert len(backtest_legs) == len(replay_legs), (
        f"backtest booked {len(backtest_legs)} legs, replay booked {len(replay_legs)}"
    )
    for index, (batch, replay) in enumerate(zip(backtest_legs, replay_legs)):
        assert batch["quantity"] == replay["quantity"], (
            f"leg {index}: sizing diverged ({batch['quantity']} vs {replay['quantity']}) — "
            "the two engines are not computing the share count from the same price"
        )
        assert batch["execution_date"] == replay["execution_date"], (
            f"leg {index}: execution timing diverged "
            f"({batch['execution_date']} vs {replay['execution_date']})"
        )
        for column in MONEY_COLS:
            assert batch[column] is not None, f"leg {index}: backtest lost {column}"
            assert replay[column] is not None, f"leg {index}: replay lost {column}"
            assert batch[column] == pytest.approx(replay[column], abs=1e-9), (
                f"leg {index} ({batch['side']} on {batch['execution_date']}): "
                f"{column} differs — backtest {batch[column]!r}, replay {replay[column]!r}"
            )


def _run_template_parity(client, headers, template_type, window, signal_parameters=None):
    version_id = _locked_version(
        client, headers, template_type, window, signal_parameters
    )
    _, transactions = _run_backtest(client, headers, version_id)
    _, decisions = _replay(client, headers, version_id)
    _assert_same_legs(_backtest_legs(transactions), _replay_legs(decisions))
    return transactions, decisions


class TestBacktestAndReplayPriceTheSameLegs:
    """Every template: one spec, one dataset, the same legs on both routes."""

    def test_buy_and_hold(self, client, auth_headers):
        _seed(BH_WINDOW, BH_PRICES)
        transactions, decisions = _run_template_parity(
            client, auth_headers, "buy_and_hold", BH_WINDOW
        )
        # Non-vacuity: the parity is not two empty trade lists agreeing.
        assert [t["side"] for t in transactions] == ["BUY", "SELL"]
        assert [d["decision_type"] for d in decisions
                if d["decision_type"] in TRADE_TYPES] == ["simulated_entry", "forced_exit"]

    def test_sma_crossover(self, client, auth_headers):
        _seed(SMA_WINDOW, SMA_PRICES)
        transactions, decisions = _run_template_parity(
            client,
            auth_headers,
            "sma_crossover",
            SMA_WINDOW,
            {"short_window": 3, "long_window": 5},
        )
        assert [t["side"] for t in transactions] == ["BUY", "SELL"]
        # Both engines must fill the cross on the bar AFTER the signal, never on
        # the signal bar itself.
        buy, sell = transactions
        assert buy["signal_date"] == SMA_WINDOW[6]
        assert buy["execution_date"] == SMA_WINDOW[7]
        assert sell["signal_date"] == SMA_WINDOW[10]
        assert sell["execution_date"] == SMA_WINDOW[11]

    def test_price_change_fixed_hold(self, client, auth_headers):
        _seed(PC_WINDOW, PC_PRICES)
        transactions, _ = _run_template_parity(
            client,
            auth_headers,
            "price_change_fixed_hold",
            PC_WINDOW,
            {"lookback_days": 3, "threshold_pct": 0.05, "holding_days": 2},
        )
        assert [t["side"] for t in transactions] == ["BUY", "SELL"]
        assert transactions[0]["exit_reason"] is None
        assert transactions[1]["exit_reason"] == "time"


class TestTheReportedPriceIsThePriceTheMoneyUsed:
    """A leg's cash must reconcile against the prices that leg reports.

    Rounding the fill price for display while booking money off the unrounded
    price leaves a journal row that cannot reproduce its own cash movement, and
    it puts a price in front of the user that nobody actually traded at.
    """

    DATASETS = [
        ("buy_and_hold", BH_WINDOW, BH_PRICES, None),
        ("sma_crossover", SMA_WINDOW, SMA_PRICES, {"short_window": 3, "long_window": 5}),
        ("price_change_fixed_hold", PC_WINDOW, PC_PRICES,
         {"lookback_days": 3, "threshold_pct": 0.05, "holding_days": 2}),
    ]

    @pytest.mark.parametrize("template_type,window,prices,signal_parameters", DATASETS)
    def test_backtest_legs_reconcile(
        self, client, auth_headers, template_type, window, prices, signal_parameters
    ):
        _seed(window, prices)
        version_id = _locked_version(
            client, auth_headers, template_type, window, signal_parameters
        )
        _, transactions = _run_backtest(client, auth_headers, version_id)
        assert transactions, "no legs to reconcile — the dataset produced no trades"

        for leg in transactions:
            gross = round(leg["quantity"] * leg["execution_price_before_costs"], 4)
            assert leg["gross_amount"] == pytest.approx(gross, abs=1e-9), (
                f"{leg['side']} gross does not follow from the reported fill price: "
                f"{leg['gross_amount']} vs {gross}"
            )
            assert leg["commission_amount"] == pytest.approx(
                round(gross * COMMISSION_RATE, 4), abs=1e-9
            )
            assert leg["slippage_amount"] == pytest.approx(
                round(abs(leg["execution_price_before_costs"] - leg["observed_price"])
                      * leg["quantity"], 4), abs=1e-9
            )
            expected_net = (
                -(gross + leg["commission_amount"])
                if leg["side"] == "BUY"
                else gross - leg["commission_amount"]
            )
            assert leg["net_cash_change"] == pytest.approx(expected_net, abs=1e-9)

    @pytest.mark.parametrize("template_type,window,prices,signal_parameters", DATASETS)
    def test_replay_legs_reconcile(
        self, client, auth_headers, template_type, window, prices, signal_parameters
    ):
        _seed(window, prices)
        version_id = _locked_version(
            client, auth_headers, template_type, window, signal_parameters
        )
        _, decisions = _replay(client, auth_headers, version_id)

        for leg in _replay_legs(decisions):
            gross = round(leg["quantity"] * leg["execution_price"], 4)
            commission = round(gross * COMMISSION_RATE, 4)
            assert leg["commission_amount"] == pytest.approx(commission, abs=1e-9)
            assert leg["slippage_amount"] == pytest.approx(
                round(abs(leg["execution_price"] - leg["observed_price"])
                      * leg["quantity"], 4), abs=1e-9
            )
            expected_net = (
                -(gross + commission) if leg["side"] == "BUY" else gross - commission
            )
            assert leg["net_cash_change"] == pytest.approx(expected_net, abs=1e-9), (
                f"{leg['side']} on {leg['execution_date']}: the replay's cash movement "
                f"({leg['net_cash_change']}) is not what its own reported fill "
                f"({leg['execution_price']}) implies ({expected_net})"
            )


class TestBothEnginesEndOnTheSameEquity:
    """The books must close at the same number, not just trade the same legs."""

    def test_final_equity_matches(self, client, auth_headers):
        _seed(BH_WINDOW, BH_PRICES)
        version_id = _locked_version(client, auth_headers, "buy_and_hold", BH_WINDOW)
        run, _ = _run_backtest(client, auth_headers, version_id)
        session, _ = _replay(client, auth_headers, version_id)

        assert run["result_json"]["final_equity"] == pytest.approx(
            session["paper_equity"], abs=1e-6
        )


class TestBothRoutesBindOneDataset:
    """A dataset two providers share is not a dataset either route may pick up.

    The replay refuses it at session creation. The backtest loaded every
    provider's rows into one frame, so the same spec ran on a series with each
    date twice — a different strategy, on different data, from the one the user
    replays.
    """

    def test_backtest_refuses_an_ambiguous_dataset(self, client, auth_headers):
        _seed(BH_WINDOW, BH_PRICES, provider_id=PROVIDER)
        _seed(BH_WINDOW, [p * 2 for p in BH_PRICES], provider_id="twelvedata")

        version_id = _locked_version(client, auth_headers, "buy_and_hold", BH_WINDOW)

        created = client.post(
            f"/api/v1/versions/{version_id}/runs",
            headers=auth_headers,
            params={"data_source": "stored"},
        )
        assert created.status_code == 201, created.text
        run = created.json()

        session = client.post(
            f"/api/v1/simulations/versions/{version_id}",
            headers=auth_headers,
            json={"initial_virtual_cash": INITIAL_CASH},
        )
        assert session.status_code == 400, session.text
        assert "Multiple providers" in session.json()["detail"]

        assert run["status"] == "failed", (
            "an ambiguous stored dataset must not report a completed backtest"
        )
        assert not (run["result_json"] or {}).get("transactions"), (
            "a refused backtest must not publish trades"
        )
        assert PROVIDER in run["error_details"]
        assert "twelvedata" in run["error_details"]


class TestTheManualLanePricesTheSameLeg:
    """The human's book is the third consumer of the same fill arithmetic.

    ``book_fill`` (through ``POST /manual-decision`` + ``advance``) is a different
    code path from both strategy engines, so the shared-helper claim has to be
    paid for on the numbers: same stored bar, same side, same quantity, same
    rates ⇒ every economic column equal, leg for leg.

    The sequence is deliberately not forced to be the strategy's own. The human
    watches one bar more than the batch needed and trades the entry bar the
    crossover itself picked, so the two lanes meet on identical execution inputs
    while answering to different prompts.

    The manual session is bound to a SECOND locked spec on the same dataset whose
    rule never fires (a 50% drop threshold on a ±6% ladder). That separation is
    what makes the comparison mean something: if the strategy also traded on that
    bar, the account would hold two fills and the numbers would no longer say
    whose arithmetic produced which.
    """

    def _open_manual_account(self, client, headers, version_id):
        created = client.post(
            f"/api/v1/simulations/versions/{version_id}",
            headers=headers,
            json={"initial_virtual_cash": MANUAL_CASH, "provider_id": PROVIDER},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]

        portfolio = client.post("/api/v1/paper-portfolio", headers=headers, json={
            "initial_virtual_cash": MANUAL_CASH,
            "currency": "USD",
            "simulation_session_id": session_id,
        })
        assert portfolio.status_code == 201, portfolio.text
        return session_id, portfolio.json()["id"]

    def _cursor(self, client, headers, session_id):
        body = client.get(f"/api/v1/simulations/{session_id}", headers=headers).json()
        return body["current_snapshot_date"]

    def _advance(self, client, headers, session_id):
        resp = client.post(
            f"/api/v1/simulations/{session_id}/advance", headers=headers
        )
        assert resp.status_code == 200, resp.text
        return self._cursor(client, headers, session_id)

    def _manual_leg(self, client, headers, session_id, portfolio_id, *, side, quantity, key):
        """Submit one human intent, let the clock execute it, return its leg."""
        submitted = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision", headers=headers,
            json={"action": side.lower(), "quantity": quantity,
                  "rationale": f"cross-lane parity {side.lower()}",
                  "idempotency_key": key},
        )
        assert submitted.status_code == 200, submitted.text
        order = submitted.json()["order"]
        assert order is not None and order["status"] == "pending", submitted.text
        assert submitted.json()["fill"] is None

        executed = self._advance(client, headers, session_id)
        order_row = next(
            o for o in client.get(
                f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers
            ).json() if o["id"] == order["order_id"]
        )
        assert order_row["status"] == "filled", order_row

        legs = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order['order_id']}/fills",
            headers=headers,
        ).json()
        assert len(legs) == 1, legs
        fill = legs[0]
        return {
            "side": side,
            "quantity": fill["quantity"],
            "execution_date": fill["execution_date"],
            "observed_price": fill["observed_price"],
            "execution_price": fill["execution_price_before_costs"],
            "commission_amount": fill["commission_amount"],
            "slippage_amount": fill["slippage_amount"],
            "net_cash_change": fill["net_cash_change"],
        }, executed

    def test_manual_entry_matches_the_batch_entry_leg(self, client, auth_headers):
        _seed(SMA_WINDOW, SMA_PRICES)
        trading_version = _locked_version(
            client, auth_headers, "sma_crossover", SMA_WINDOW,
            {"short_window": 3, "long_window": 5},
        )
        _, transactions = _run_backtest(client, auth_headers, trading_version)
        batch_legs = _backtest_legs(transactions)
        assert [leg["side"] for leg in batch_legs] == ["BUY", "SELL"], batch_legs

        passive_version = _locked_version(
            client, auth_headers, "price_change_fixed_hold", SMA_WINDOW,
            {"lookback_days": 3, "threshold_pct": 0.50, "holding_days": 2},
        )
        _, passive_transactions = _run_backtest(client, auth_headers, passive_version)
        assert passive_transactions == [], (
            "the manual account's own spec must not trade, or the human fill is "
            "not the only thing in the book"
        )

        session_id, portfolio_id = self._open_manual_account(
            client, auth_headers, passive_version
        )
        # Watch the crossover's signal bar, then trade the bar it executes on.
        for _ in range(7):
            self._advance(client, auth_headers, session_id)
        assert self._cursor(client, auth_headers, session_id) == SMA_WINDOW[6]

        batch_buy = batch_legs[0]
        manual_buy, executed_on = self._manual_leg(
            client, auth_headers, session_id, portfolio_id,
            side="BUY", quantity=int(batch_buy["quantity"]), key="parity-buy",
        )
        assert executed_on == batch_buy["execution_date"] == SMA_WINDOW[7]
        _assert_same_legs([batch_buy], [manual_buy])

    def test_manual_round_trip_matches_the_batch_round_trip_net(self, client, auth_headers):
        """Buy and sell the same quantity on the same two bars, both lanes."""
        _seed(SMA_WINDOW, SMA_PRICES)
        trading_version = _locked_version(
            client, auth_headers, "sma_crossover", SMA_WINDOW,
            {"short_window": 3, "long_window": 5},
        )
        _, transactions = _run_backtest(client, auth_headers, trading_version)
        batch_legs = _backtest_legs(transactions)
        batch_buy, batch_sell = batch_legs

        passive_version = _locked_version(
            client, auth_headers, "price_change_fixed_hold", SMA_WINDOW,
            {"lookback_days": 3, "threshold_pct": 0.50, "holding_days": 2},
        )
        session_id, portfolio_id = self._open_manual_account(
            client, auth_headers, passive_version
        )
        for _ in range(7):
            self._advance(client, auth_headers, session_id)

        quantity = int(batch_buy["quantity"])
        manual_buy, _ = self._manual_leg(
            client, auth_headers, session_id, portfolio_id,
            side="BUY", quantity=quantity, key="trip-buy",
        )
        # Walk to the death cross's signal bar and close the same size there.
        for _ in range(3):
            self._advance(client, auth_headers, session_id)
        assert self._cursor(client, auth_headers, session_id) == SMA_WINDOW[10]
        manual_sell, executed_on = self._manual_leg(
            client, auth_headers, session_id, portfolio_id,
            side="SELL", quantity=quantity, key="trip-sell",
        )
        assert executed_on == batch_sell["execution_date"] == SMA_WINDOW[11]

        _assert_same_legs([batch_buy, batch_sell], [manual_buy, manual_sell])

        batch_net = sum(leg["net_cash_change"] for leg in (batch_buy, batch_sell))
        manual_net = sum(leg["net_cash_change"] for leg in (manual_buy, manual_sell))
        assert manual_net == pytest.approx(batch_net, abs=1e-9), (
            f"round-trip net differs — batch {batch_net}, manual {manual_net}"
        )

        account_row = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert account_row["current_virtual_cash"] == pytest.approx(
            MANUAL_CASH + batch_net, abs=1e-6
        )
        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert [p for p in positions if p["asset_id"] == INSTRUMENT and p["status"] == "open"] == []
