"""Paper-portfolio valuation honesty and partial-exit accounting.

Two defects are pinned here:

1. ``PaperPosition.unrealized_pnl`` was never written by any code path, so
   ``total_unrealized_pnl`` summed ``None or 0`` and every open position
   reported an unrealized P&L of exactly 0.00 - a fabricated number that reads
   as "flat" rather than "not marked". The endpoints now mark open positions
   from the newest bar actually present, and report ``None`` plus the unpriced
   asset ids when no mark exists.
2. A partial exit credited cash but booked no realised P&L, and the later full
   close computed P&L on the remaining quantity only, so the shares sold
   partially disappeared from the performance record.
3. Both realised and unrealized P&L were reported gross of commission while
   cash was debited for it on every leg. The headline 已實現損益 figure therefore
   could not be reconciled with the portfolio's own cash movement and was
   optimistically biased by exactly the fees paid. P&L now carries the entry
   leg's commission pro-rata plus the exit leg's commission, which makes
   ``total_realized_pnl == current_cash - initial_cash`` hold exactly for a
   portfolio that has closed every position.

The fixture prices are asserted explicitly so the expected numbers below stay
arithmetic facts instead of silently following the code under test.
"""

import pytest

from app.api.paper_portfolio import _latest_available_bar

ASSET = "DEMO-ALPHA"
# Demo fixture (synthetic, not market data): last bar 2024-02-13, open 54.0, close 54.5.
FIXTURE_OPEN = 54.0
FIXTURE_CLOSE = 54.5
SLIPPAGE = 0.001
COMMISSION = 0.001


def _make_portfolio(client, headers, cash: float) -> str:
    resp = client.post(
        "/api/v1/paper-portfolio",
        headers=headers,
        json={"initial_virtual_cash": cash},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _order(client, headers, portfolio_id, side, quantity):
    return client.post(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders",
        headers=headers,
        json={
            "asset_id": ASSET,
            "side": side,
            "quantity": quantity,
            "rationale": "valuation regression",
            "expected_outcome": "accounting must stay consistent",
            "self_reported_confidence": "medium",
        },
    )


def _fills(client, headers, portfolio_id, order_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def fixture_guard():
    """Fail loudly if the demo fixture moves, so the numbers below stay meaningful."""
    bar = _latest_available_bar(ASSET)
    assert bar is not None, "demo fixture must have bars for the paper portfolio asset"
    assert bar["open"] == pytest.approx(FIXTURE_OPEN)
    assert bar["close"] == pytest.approx(FIXTURE_CLOSE)
    return bar


class TestUnrealizedValuation:
    def test_open_position_is_marked_to_market_instead_of_reporting_zero(
        self, client, auth_headers, fixture_guard
    ):
        portfolio_id = _make_portfolio(client, auth_headers, 100_000)
        created = _order(client, auth_headers, portfolio_id, "BUY", 10)
        assert created.status_code == 201, created.text

        exec_price = round(FIXTURE_OPEN * (1 + SLIPPAGE), 4)
        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert len(positions) == 1
        position = positions[0]
        assert position["status"] == "open"
        assert position["quantity"] == pytest.approx(10)
        assert position["average_entry_price"] == pytest.approx(exec_price)

        # Net of the entry commission already paid: cash was debited for it when
        # the position opened, so a mark that ignores it overstates the holding.
        expected = round(
            (FIXTURE_CLOSE - exec_price) * 10 - exec_price * 10 * COMMISSION, 4
        )
        assert expected != 0, "the fixture must produce a non-zero mark so this is not vacuous"
        assert position["unrealized_pnl"] == pytest.approx(expected)

        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert portfolio["total_unrealized_pnl"] == pytest.approx(expected)

        # Cash + holdings-at-mark, less the starting cash, must equal the P&L the
        # API reports. Recomputed here from the fixture, not read back from code.
        holdings_at_mark = 10 * FIXTURE_CLOSE
        expected_total_pnl = round(
            portfolio["current_virtual_cash"] + holdings_at_mark - 100_000.0, 4
        )
        reported = round(
            portfolio["total_realized_pnl"] + portfolio["total_unrealized_pnl"], 4
        )
        assert reported == pytest.approx(expected_total_pnl, abs=1e-6), (
            f"reported P&L {reported} does not reconcile with cash plus holdings "
            f"{expected_total_pnl}"
        )
        assert expected_total_pnl != 0, "the identity must be checked on a non-zero number"

    def test_valuation_basis_discloses_synthetic_source_and_as_of_date(
        self, client, auth_headers, fixture_guard
    ):
        portfolio_id = _make_portfolio(client, auth_headers, 100_000)
        assert _order(client, auth_headers, portfolio_id, "BUY", 10).status_code == 201

        valuation = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()["valuation"]
        assert valuation["mode"] == "synthetic_fixture"
        assert valuation["mode_label_zh"] == "合成數據"
        assert valuation["provider"] == "Demo (Synthetic)"
        assert valuation["data_as_of"] == fixture_guard["date"]
        assert valuation["unpriced_assets"] == []
        assert "非即時行情" in valuation["note"]

    def test_missing_mark_price_reports_null_and_names_the_asset(
        self, client, auth_headers, fixture_guard, monkeypatch
    ):
        portfolio_id = _make_portfolio(client, auth_headers, 100_000)
        assert _order(client, auth_headers, portfolio_id, "BUY", 10).status_code == 201

        # The asset legitimately has data at order time; drop the mark afterwards
        # to exercise the path where a position cannot be valued.
        monkeypatch.setattr(
            "app.api.paper_portfolio._latest_available_bar", lambda asset_id: None
        )

        position = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()[0]
        assert position["unrealized_pnl"] is None, "an unmarkable position must not read as 0.00"

        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert portfolio["total_unrealized_pnl"] is None
        assert portfolio["valuation"]["unpriced_assets"] == [ASSET]
        assert portfolio["valuation"]["data_as_of"] is None
        assert ASSET in portfolio["valuation"]["note"]

    def test_portfolio_without_positions_reports_zero_unrealized(
        self, client, auth_headers
    ):
        portfolio_id = _make_portfolio(client, auth_headers, 50_000)
        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        # No positions is a real zero, not an unavailable one.
        assert portfolio["total_unrealized_pnl"] == 0.0
        assert portfolio["total_realized_pnl"] == 0.0
        assert portfolio["valuation"]["unpriced_assets"] == []


class TestPartialExitAccounting:
    def test_partial_exit_books_realized_pnl_and_full_exit_completes_it(
        self, client, auth_headers, fixture_guard
    ):
        portfolio_id = _make_portfolio(client, auth_headers, 100_000)
        buy = _order(client, auth_headers, portfolio_id, "BUY", 10)
        assert buy.status_code == 201, buy.text
        entry = round(FIXTURE_OPEN * (1 + SLIPPAGE), 4)

        partial = _order(client, auth_headers, portfolio_id, "SELL", 4)
        assert partial.status_code == 201, partial.text
        sell_exec = round(FIXTURE_OPEN * (1 - SLIPPAGE), 4)
        # Realised P&L carries both legs' commission, matching what cash did.
        exit_commission_4 = round(round(4 * sell_exec, 4) * COMMISSION, 4)
        expected_partial = round(
            (sell_exec - entry) * 4 - exit_commission_4 - entry * 4 * COMMISSION, 4
        )

        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert len(positions) == 1
        assert positions[0]["status"] == "open"
        assert positions[0]["quantity"] == pytest.approx(6)
        assert positions[0]["realized_pnl"] == pytest.approx(expected_partial)
        assert expected_partial != 0, "the partial exit must book a real number"

        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert portfolio["total_realized_pnl"] == pytest.approx(expected_partial), (
            "realised P&L taken on a still-open position must reach the portfolio total"
        )

        full = _order(client, auth_headers, portfolio_id, "SELL", 6)
        assert full.status_code == 201, full.text
        exit_commission_6 = round(round(6 * sell_exec, 4) * COMMISSION, 4)
        expected_total = round(
            expected_partial
            + round(
                (sell_exec - entry) * 6 - exit_commission_6 - entry * 6 * COMMISSION, 4
            ),
            4,
        )

        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert positions[0]["status"] == "closed"
        assert positions[0]["quantity"] == 0
        assert positions[0]["realized_pnl"] == pytest.approx(expected_total)
        assert positions[0]["unrealized_pnl"] is None, "a closed position has no open mark"

        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert portfolio["total_realized_pnl"] == pytest.approx(expected_total)

        # Every position is closed, so the reported P&L is not an estimate: it
        # must equal the cash the portfolio actually lost, to the cent.
        assert portfolio["total_realized_pnl"] == pytest.approx(
            portfolio["current_virtual_cash"] - 100_000.0, abs=1e-6
        ), "realised P&L does not reconcile with the cash movement"
        assert portfolio["total_realized_pnl"] != 0, (
            "the round trip must lose real money, otherwise the identity above "
            "would also hold for a portfolio that did nothing"
        )

    def test_cash_identity_holds_across_buys_and_exits(
        self, client, auth_headers, fixture_guard
    ):
        """initial cash - current cash must equal the fills' cash legs exactly.

        This is the order/fill/position/cash consistency check: commission and
        slippage are charged once per fill, and nothing leaks or doubles.
        """
        initial = 100_000.0
        portfolio_id = _make_portfolio(client, auth_headers, initial)
        order_ids = []
        for side, qty in (("BUY", 10), ("BUY", 5), ("SELL", 4), ("SELL", 11)):
            resp = _order(client, auth_headers, portfolio_id, side, qty)
            assert resp.status_code == 201, resp.text
            order_ids.append((resp.json()["id"], side, qty))

        fills = []
        for order_id, side, qty in order_ids:
            order_fills = _fills(client, auth_headers, portfolio_id, order_id)
            assert len(order_fills) == 1, "each filled order carries exactly one fill"
            fill = order_fills[0]
            assert fill["quantity"] == pytest.approx(qty)
            fills.append(fill)

        expected_cash_delta = round(sum(f["net_cash_change"] for f in fills), 4)
        portfolio = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        assert round(initial - portfolio["current_virtual_cash"], 4) == pytest.approx(
            -expected_cash_delta, abs=1e-6
        )

        # Per-fill arithmetic, recomputed from the stored observed price.
        for fill, (order_id, side, qty) in zip(fills, order_ids):
            observed = fill["observed_price"]
            if side == "BUY":
                exec_price = round(observed * (1 + SLIPPAGE), 4)
                gross = round(qty * exec_price, 4)
                commission = round(gross * COMMISSION, 4)
                assert fill["net_cash_change"] == pytest.approx(-(gross + commission))
            else:
                exec_price = round(observed * (1 - SLIPPAGE), 4)
                gross = round(qty * exec_price, 4)
                commission = round(gross * COMMISSION, 4)
                assert fill["net_cash_change"] == pytest.approx(gross - commission)
            assert fill["execution_price_before_costs"] == pytest.approx(exec_price)
            assert fill["gross_amount"] == pytest.approx(gross)
            assert fill["commission_amount"] == pytest.approx(commission)
            assert fill["slippage_amount"] == pytest.approx(
                round(abs(exec_price - observed) * qty, 4)
            )

        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert positions[0]["status"] == "closed"
        assert positions[0]["quantity"] == 0
