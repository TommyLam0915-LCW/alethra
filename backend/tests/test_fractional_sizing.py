"""Fractional sizing: a cash-constrained account can hold part of a coin.

A $10,000 account cannot afford one BTC at ~$88,000. Whole-unit sizing turned
that into a zero-trade run rather than a smaller position, so these tests pin
the instrument's tradable precision into the shared sizing arithmetic, into
both engines, into the benchmark curve and into the API serialization that
carries the quantity back out. Share markets must still trade whole units.

Prices are synthetic and clearly labelled as such: these tests prove the
arithmetic, not profitability or anything about live market data.
"""

from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

import pandas as pd
import pytest

from app.api.simulations import _apply_manual_fills, _serialized_quantity
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.engine.backtest import run_backtest
from app.engine.fill_math import (
    quantity_precision_for,
    round_quantity,
    size_position,
)
from app.engine.metrics import build_benchmark_equity_curve
from app.engine.simulation import ForwardSimulator
from app.engine.strategies import run_buy_and_hold
from app.models.market_data import MarketDataBar
from tests.conftest import TestSession

CRYPTO = KRAKEN_BTC_USD.instrument_id
BTC_PRICE = 88_000.0
INITIAL_CASH = 10_000.0
COMMISSION = 0.001
SLIPPAGE = 0.0005

WINDOW = [f"2026-09-{day:02d}" for day in range(1, 6)]
PRICES = [88_000.0, 88_500.0, 89_250.0, 87_750.0, 90_000.0]


def _frame(window=None, prices=None):
    window = window or WINDOW
    prices = prices or PRICES
    return pd.DataFrame(
        {
            "date": pd.to_datetime(window),
            "open": prices,
            "high": [p + 100.0 for p in prices],
            "low": [p - 100.0 for p in prices],
            "close": prices,
            "volume": [100.0] * len(prices),
        }
    )


def _spec(**overrides):
    spec = {
        "template_type": "buy_and_hold",
        "asset_ids": [CRYPTO],
        "start_date": WINDOW[0],
        "end_date": WINDOW[-1],
        "initial_cash": INITIAL_CASH,
        "commission_rate": COMMISSION,
        "slippage_rate": SLIPPAGE,
    }
    spec.update(overrides)
    return spec


def _seed_crypto(window=None, prices=None, provider_id="demo"):
    window = window or WINDOW
    prices = prices or PRICES
    db = TestSession()
    try:
        for day_str, price in zip(window, prices):
            day = date.fromisoformat(day_str)
            db.add(
                MarketDataBar(
                    instrument_id=CRYPTO,
                    provider_id=provider_id,
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, datetime.min.time()),
                    open=price,
                    high=price + 100.0,
                    low=price - 100.0,
                    close=price,
                    volume=100.0,
                )
            )
        db.commit()
    finally:
        db.close()


class TestSharedSizingArithmetic:
    def test_share_market_sizing_stays_whole_units(self):
        quantity = size_position(INITIAL_CASH, 101.23, COMMISSION)
        assert isinstance(quantity, int)
        assert quantity == int(
            Decimal("10000") / (Decimal("101.23") * Decimal("1.001"))
        )

    def test_crypto_sizing_keeps_the_instruments_decimals(self):
        quantity = size_position(INITIAL_CASH, BTC_PRICE, COMMISSION, 8)
        assert isinstance(quantity, float)
        assert 0 < quantity < 1
        assert quantity == round(quantity, 8)
        # Affordable, and maximal at that precision: the next 1e-8 unit would
        # spend more cash than the account was handed.
        assert quantity * BTC_PRICE * (1 + COMMISSION) <= INITIAL_CASH
        assert (quantity + 1e-8) * BTC_PRICE * (1 + COMMISSION) > INITIAL_CASH

    @pytest.mark.parametrize("precision", [0, 2, 4, 8])
    def test_sizing_never_overspends(self, precision):
        quantity = size_position(INITIAL_CASH, BTC_PRICE, COMMISSION, precision)
        assert quantity * BTC_PRICE * (1 + COMMISSION) <= INITIAL_CASH + 1e-9

    def test_precision_comes_from_the_instrument_catalog(self):
        assert quantity_precision_for(CRYPTO) == 8
        assert quantity_precision_for("US:AAPL") == 0
        assert quantity_precision_for("US:NOT-A-REAL-SYMBOL") == 0

    def test_round_quantity_keeps_the_callers_shape(self):
        assert round_quantity(100.0, 0) == 100
        assert isinstance(round_quantity(100.0, 0), int)
        assert round_quantity(0.11352265, 8) == 0.11352265
        assert round_quantity(0.113522655, 8) == 0.11352266

    def test_benchmark_curve_needs_the_pairs_precision(self):
        frame = _frame()
        whole_units = build_benchmark_equity_curve(
            frame, INITIAL_CASH, COMMISSION, SLIPPAGE, 0
        )
        assert whole_units == [], "whole-unit sizing cannot afford one BTC"

        fractional = build_benchmark_equity_curve(
            frame, INITIAL_CASH, COMMISSION, SLIPPAGE, 8
        )
        assert len(fractional) == len(frame)
        assert fractional[0] < INITIAL_CASH  # commission was actually charged


class TestEnginesTradeFractions:
    def test_backtest_buys_a_fraction_of_a_coin(self):
        batch, warnings = run_buy_and_hold(
            symbol=CRYPTO,
            ohlcv=_frame(),
            initial_cash=INITIAL_CASH,
            commission_rate=COMMISSION,
            slippage_rate=SLIPPAGE,
            quantity_precision=quantity_precision_for(CRYPTO),
        )
        assert [t["side"] for t in batch] == ["BUY", "SELL"]
        buy, sell = batch
        assert 0 < buy["quantity"] < 1
        assert buy["quantity"] == sell["quantity"]
        assert buy["shares_after"] == buy["quantity"]
        assert sell["shares_after"] == 0
        assert not any("Insufficient cash" in w for w in warnings)
        assert buy["cash_after"] >= 0

    def test_share_market_backtest_still_trades_whole_shares(self):
        batch, _ = run_buy_and_hold(
            symbol="US:AAPL",
            ohlcv=_frame(window=WINDOW, prices=[100.0] * len(WINDOW)),
            initial_cash=INITIAL_CASH,
            commission_rate=COMMISSION,
            slippage_rate=SLIPPAGE,
            quantity_precision=quantity_precision_for("US:AAPL"),
        )
        quantity = batch[0]["quantity"]
        assert isinstance(quantity, int)
        assert quantity == 99

    def test_stored_backtest_holds_fractions_and_prices_a_benchmark(self):
        _seed_crypto()
        db = TestSession()
        try:
            result = run_backtest(
                _spec(benchmark_id=CRYPTO),
                db_session=db,
                data_source="stored",
                provider_id="demo",
            )
        finally:
            db.close()

        assert result["run_status"] == "completed", result.get("errors")
        transactions = result["transactions"]
        assert [t["side"] for t in transactions] == ["BUY", "SELL"]
        assert 0 < transactions[0]["quantity"] < 1
        assert result["benchmark_equity_curve"], (
            "the crypto benchmark curve is empty — sizing ignored precision"
        )

    def test_forward_replay_holds_and_serializes_a_fraction(self):
        simulator = ForwardSimulator(
            _spec(), INITIAL_CASH, COMMISSION, SLIPPAGE, WINDOW[0], "1.0.0"
        )
        result = simulator.step(_frame(window=WINDOW[:1], prices=PRICES[:1]), WINDOW[0])

        assert result.decision_type == "simulated_entry"
        quantity = result.decision_detail["quantity"]
        assert 0 < quantity < 1
        position = result.paper_position
        assert position["shares"] == quantity
        assert isinstance(position["shares"], float)
        assert result.paper_cash >= 0

    def test_manual_fills_preserve_fractional_shares(self):
        simulator = ForwardSimulator(
            _spec(), INITIAL_CASH, COMMISSION, SLIPPAGE, WINDOW[0], "1.0.0"
        )
        _apply_manual_fills(
            simulator,
            [
                {
                    "side": "BUY",
                    "quantity": 0.05,
                    "price": 90_000.0,
                    "net_cash_change": -4_504.5,
                    "execution_date": WINDOW[0],
                }
            ],
        )
        assert simulator.shares == 0.05
        assert simulator.cash == round(INITIAL_CASH - 4_504.5, 4)

        _apply_manual_fills(
            simulator,
            [
                {
                    "side": "SELL",
                    "quantity": 0.02,
                    "price": 91_000.0,
                    "net_cash_change": 1_818.18,
                    "execution_date": WINDOW[1],
                }
            ],
        )
        assert simulator.shares == 0.03
        assert simulator.entry_price is not None

        _apply_manual_fills(
            simulator,
            [
                {
                    "side": "SELL",
                    "quantity": 0.03,
                    "price": 91_000.0,
                    "net_cash_change": 2_727.27,
                    "execution_date": WINDOW[2],
                }
            ],
        )
        assert simulator.shares == 0
        assert simulator.entry_price is None
        assert simulator.entry_date is None


class TestApiQuantitySerialization:
    def test_serialized_quantity_keeps_decimals_only_when_fractional(self):
        assert _serialized_quantity("0.11352265") == 0.11352265
        assert _serialized_quantity(0.11352265) == 0.11352265
        whole = _serialized_quantity(3.0)
        assert whole == 3
        assert isinstance(whole, int)

    def test_rounding_decimal_floor_is_exact(self):
        floored = (
            Decimal("10000") / (Decimal("88000") * Decimal("1.001"))
        ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        assert size_position(INITIAL_CASH, BTC_PRICE, COMMISSION, 8) == float(floored)
