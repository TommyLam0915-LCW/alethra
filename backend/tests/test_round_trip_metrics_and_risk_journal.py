"""§5 round-trip metrics and per-trade risk journal.

Every expected number below is hand-derived so the tests can fail if the
attribution math drifts. The recurring theme: an undefined quantity stays
undefined — it is never reported as zero.
"""

import json

import pandas as pd
import pytest

from app.engine.attribution import annotate_and_attribute, day_of_week_breakdown
from app.engine.backtest import run_backtest
from app.engine.metrics import compute_metrics
from app.providers.data_provider import OHLCVBar


def _buy(date, qty, price, commission=0.0):
    return {
        "transaction_id": f"b-{date}",
        "trade_date": date,
        "signal_date": None,
        "execution_date": date,
        "symbol": "TEST",
        "side": "BUY",
        "quantity": qty,
        "observed_price": price,
        "execution_price_before_costs": price,
        "slippage_amount": 0.0,
        "commission_amount": commission,
        "gross_amount": round(qty * price, 4),
        "net_cash_change": round(-(qty * price + commission), 4),
        "cash_after": 0.0,
        "shares_after": qty,
        "rationale": "test entry",
    }


def _sell(date, qty, price, commission=0.0):
    return {
        "transaction_id": f"s-{date}",
        "trade_date": date,
        "signal_date": date,
        "execution_date": date,
        "symbol": "TEST",
        "side": "SELL",
        "quantity": qty,
        "observed_price": price,
        "execution_price_before_costs": price,
        "slippage_amount": 0.0,
        "commission_amount": commission,
        "gross_amount": round(qty * price, 4),
        "net_cash_change": round(qty * price - commission, 4),
        "cash_after": 0.0,
        "shares_after": 0,
        "rationale": "test exit",
    }


class TestRoundTripMath:
    def test_cost_basis_and_net_proceeds_drive_realized_pnl(self):
        # Buy 10 @ 100 + 1 commission  -> cost/share 100.1
        # Sell 10 @ 110 - 1.1 commission -> proceeds/share 109.89
        # Realized = (109.89 - 100.1) * 10 = 97.9
        result = annotate_and_attribute([_buy("2024-01-02", 10, 100.0, 1.0),
                                        _sell("2024-01-09", 10, 110.0, 1.1)])
        assert result["closed_trade_count"] == 1
        assert result["net_realized_pnl"] == pytest.approx(97.9)
        assert result["win_count"] == 1
        assert result["open_shares"] == 0

    def test_losing_close_is_counted_as_a_loss_not_absorbed(self):
        result = annotate_and_attribute([_buy("2024-01-02", 10, 100.0),
                                         _sell("2024-01-09", 10, 90.0)])
        assert result["loss_count"] == 1
        assert result["gross_profit"] == 0.0
        assert result["gross_loss"] == pytest.approx(100.0)
        assert result["net_realized_pnl"] == pytest.approx(-100.0)

    def test_partial_exit_matches_earliest_lot_first(self):
        result = annotate_and_attribute([
            _buy("2024-01-02", 10, 100.0),
            _buy("2024-01-03", 10, 120.0),
            _sell("2024-01-10", 15, 130.0),
        ])
        trades = result["closed_trades"]
        assert len(trades) == 2
        assert trades[0]["shares"] == 10
        assert trades[0]["realized_pnl"] == pytest.approx(300.0)
        assert trades[1]["shares"] == 5
        assert trades[1]["realized_pnl"] == pytest.approx(50.0)
        assert result["open_shares"] == 5
        assert result["net_realized_pnl"] == pytest.approx(350.0)

    def test_oversell_has_unknown_pnl_instead_of_a_fabricated_number(self):
        result = annotate_and_attribute([_buy("2024-01-02", 10, 100.0),
                                         _sell("2024-01-09", 15, 110.0)])
        assert result["closed_trade_count"] == 1
        assert result["unmatched_sell_count"] == 1
        unmatched = [c for c in result["closed_trades"] if c["realized_pnl"] is None]
        assert len(unmatched) == 1
        assert unmatched[0]["shares"] == 5
        assert "note" in unmatched[0]

    def test_sell_is_stamped_with_the_pnl_it_closed_and_buy_stays_open(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)]
        annotate_and_attribute(transactions)
        assert transactions[0]["realized_pnl"] is None
        assert transactions[1]["realized_pnl"] == pytest.approx(100.0)


class TestRiskPlan:
    SPEC_WITH_STOP = {"risk_parameters": {"stop_price": 95.0, "target_price": 115.0}}

    def test_undeclared_stop_leaves_r_undefined(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)]
        annotate_and_attribute(transactions, strategy_spec={}, config_hash="abc")
        for txn in transactions:
            assert txn["stop_price"] is None
            assert txn["planned_risk"] is None
            assert txn["realized_r"] is None
            assert txn["actor"] == "strategy"
            assert txn["strategy_config_hash"] == "abc"

    def test_declared_stop_and_target_produce_risk_and_r_multiple(self):
        # entry 100, stop 95 -> risk/share 5, 10 shares -> planned_risk 50
        # target 115 -> planned_r = 15/5 = 3.0
        # exit 110 -> realized 100 -> realized_r = 2.0
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)]
        attribution = annotate_and_attribute(transactions, strategy_spec=self.SPEC_WITH_STOP, config_hash="abc")
        assert transactions[0]["planned_risk"] == pytest.approx(50.0)
        assert transactions[0]["planned_r"] == pytest.approx(3.0)
        assert transactions[1]["stop_price"] == 95.0
        assert transactions[1]["planned_risk"] == pytest.approx(50.0)
        assert transactions[1]["realized_r"] == pytest.approx(2.0)
        assert attribution["trades_with_planned_risk"] == 1
        assert attribution["mean_realized_r"] == pytest.approx(2.0)

    def test_percentage_stop_is_resolved_against_the_actual_entry(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 92.0)]
        annotate_and_attribute(transactions, strategy_spec={"risk_parameters": {"stop_pct": 0.05}})
        assert transactions[0]["stop_price"] == pytest.approx(95.0)
        assert transactions[0]["planned_risk"] == pytest.approx(50.0)
        # Loss of 80 against 50 at risk = -1.6R
        assert transactions[1]["realized_r"] == pytest.approx(-1.6)

    def test_stop_at_or_above_entry_is_not_a_risk_unit(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)]
        annotate_and_attribute(transactions, strategy_spec={"risk_parameters": {"stop_price": 100.0}})
        assert transactions[0]["stop_price"] is None
        assert transactions[0]["planned_risk"] is None

    def test_target_alone_still_leaves_planned_r_undefined(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)]
        annotate_and_attribute(transactions, strategy_spec={"risk_parameters": {"target_price": 120.0}})
        assert transactions[0]["target_price"] == 120.0
        assert transactions[0]["planned_r"] is None


class TestMetrics:
    def _metrics(self, transactions, *, strategy_spec=None, initial_cash=10000.0):
        attribution = annotate_and_attribute(transactions, strategy_spec=strategy_spec or {})
        # Equity here is cash + fully liquidated proceeds, i.e. initial cash + realized.
        final_equity = initial_cash + attribution["net_realized_pnl"]
        return compute_metrics(
            initial_cash=initial_cash,
            final_equity=final_equity,
            transactions=transactions,
            daily_equity=[initial_cash, final_equity],
            benchmark_equity=None,
            trading_days=20,
            attribution=attribution,
            trading_dates=["2024-01-02", "2024-01-09"],
        )

    def test_win_rate_and_profit_factor_from_mixed_closes(self):
        transactions = [
            _buy("2024-01-02", 10, 100.0), _sell("2024-01-03", 10, 110.0),   # +100
            _buy("2024-01-04", 10, 100.0), _sell("2024-01-05", 10, 80.0),    # -200
        ]
        metrics = self._metrics(transactions)
        assert metrics["closed_trade_count"] == 2
        assert metrics["win_rate_pct"] == pytest.approx(50.0)
        assert metrics["profit_factor"] == pytest.approx(0.5)
        assert metrics["net_pnl"] == pytest.approx(-100.0)

    def test_profit_factor_is_undefined_without_a_loss(self):
        metrics = self._metrics([_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)])
        profit_factor = metrics["profit_factor"]
        assert isinstance(profit_factor, dict)
        assert profit_factor["value"] is None
        assert "denominator is zero" in profit_factor["reason"]
        assert profit_factor["gross_profit"] == pytest.approx(100.0)

    def test_average_realized_r_stays_undefined_without_a_declared_stop(self):
        metrics = self._metrics([_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)])
        average = metrics["average_realized_r"]
        assert isinstance(average, dict)
        assert average["value"] is None
        assert "no stop" in average["reason"]

    def test_average_realized_r_computes_once_a_stop_is_declared(self):
        metrics = self._metrics(
            [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)],
            strategy_spec={"risk_parameters": {"stop_price": 95.0}},
        )
        assert metrics["average_realized_r"] == pytest.approx(2.0)

    def test_no_trades_reports_zero_pnl_and_undefined_rates(self):
        metrics = compute_metrics(
            initial_cash=1000.0,
            final_equity=1000.0,
            transactions=[],
            daily_equity=[1000.0],
            benchmark_equity=None,
            trading_days=10,
            attribution=annotate_and_attribute([]),
            trading_dates=["2024-01-02"],
        )
        assert metrics["net_pnl"] == 0.0
        assert metrics["closed_trade_count"] == 0
        assert metrics["win_rate_pct"]["value"] is None
        assert metrics["profit_factor"]["value"] is None
        assert metrics["average_realized_r"]["value"] is None

    def test_legacy_callers_without_attribution_get_structs_not_crash(self):
        metrics = compute_metrics(
            initial_cash=1000.0,
            final_equity=1100.0,
            transactions=[],
            daily_equity=[1000.0, 1100.0],
            benchmark_equity=None,
            trading_days=10,
        )
        assert metrics["net_pnl"] == pytest.approx(100.0)
        assert metrics["win_rate_pct"]["value"] is None
        assert metrics["day_of_week_breakdown"]["available"] is False

    def test_pnl_cross_check_reconciles_equity_against_fifo(self):
        metrics = self._metrics([_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)])
        cross = metrics["pnl_cross_check"]
        assert cross["consistent"] is True
        assert cross["difference"] == pytest.approx(0.0)

    def test_pnl_cross_check_refuses_to_reconcile_an_open_position(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 5, 110.0)]
        attribution = annotate_and_attribute(transactions)
        metrics = compute_metrics(
            initial_cash=10000.0,
            final_equity=10000.0 + attribution["net_realized_pnl"],
            transactions=transactions,
            daily_equity=[10000.0, 10050.0],
            benchmark_equity=None,
            trading_days=20,
            attribution=attribution,
            trading_dates=["2024-01-02", "2024-01-09"],
        )
        cross = metrics["pnl_cross_check"]
        assert cross["consistent"] is None
        assert "remain open" in cross["reason"]

    def test_time_of_day_breakdown_is_declared_unavailable_not_empty(self):
        metrics = self._metrics([_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 10, 110.0)])
        breakdown = metrics["time_of_day_breakdown"]
        assert breakdown["available"] is False
        assert breakdown["value"] is None
        assert "intraday" in breakdown["reason"]


class TestDayOfWeekBreakdown:
    def test_closes_land_on_the_weekday_of_their_exit_session(self):
        # 2024-01-02 is a Tuesday, 2024-01-06 a Saturday, 2024-01-09 a Tuesday.
        transactions = [
            _buy("2024-01-02", 10, 100.0), _sell("2024-01-03", 10, 110.0),  # Wed +100
            _buy("2024-01-04", 10, 100.0), _sell("2024-01-05", 10, 90.0),   # Fri -100
        ]
        attribution = annotate_and_attribute(transactions)
        breakdown = day_of_week_breakdown(attribution, [t["execution_date"] for t in transactions])
        rows = {r["weekday"]: r for r in breakdown["rows"]}
        assert breakdown["date_semantics"] == "exchange_local_trading_day"
        assert rows["Wednesday"]["realized_pnl"] == pytest.approx(100.0)
        assert rows["Wednesday"]["win_rate_pct"] == pytest.approx(100.0)
        assert rows["Friday"]["realized_pnl"] == pytest.approx(-100.0)
        assert rows["Friday"]["losses"] == 1
        assert rows["Tuesday"]["closed_trades"] == 0
        assert rows["Tuesday"]["mean_realized_pnl"] is None
        assert breakdown["unattributed_trades"] == 0

    def test_weekend_only_calendar_never_drops_a_close_silently(self):
        transactions = [_buy("2024-01-06", 10, 100.0), _sell("2024-01-07", 10, 90.0)]
        attribution = annotate_and_attribute(transactions)
        breakdown = day_of_week_breakdown(attribution, ["2024-01-06", "2024-01-07"])
        rows = {r["weekday"]: r for r in breakdown["rows"]}
        assert list(rows) == ["Saturday", "Sunday"]
        assert rows["Sunday"]["realized_pnl"] == pytest.approx(-100.0)

    def test_unmatched_close_is_counted_as_unattributed(self):
        transactions = [_buy("2024-01-02", 10, 100.0), _sell("2024-01-09", 15, 90.0)]
        attribution = annotate_and_attribute(transactions)
        breakdown = day_of_week_breakdown(attribution, ["2024-01-02", "2024-01-09"])
        assert breakdown["unattributed_trades"] == 1
        tuesday = next(r for r in breakdown["rows"] if r["weekday"] == "Tuesday")
        assert tuesday["closed_trades"] == 1
        assert tuesday["realized_pnl"] == pytest.approx(-100.0)


def _base_spec(**overrides):
    spec = {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": ["DEMO-ALPHA"],
        "frequency": "1d",
        "start_date": "2024-01-02",
        "end_date": "2024-02-14",
        "initial_cash": 10000,
        "signal_parameters": {},
        "commission_rate": 0.001,
        "slippage_rate": 0.0005,
        "currency": "USD",
    }
    spec.update(overrides)
    return spec


class _SeriesProvider:
    """Deterministic oscillating daily series fed through the engine's provider path.

    The shipped demo fixture is ~29 sessions and only produces a single round
    trip, so multi-trade attribution needs a longer series. Open is the previous
    close, so nothing here depends on randomness or real market data.
    """

    provider_name = "Deterministic fixture"
    feed_mode = "offline"

    def __init__(self, symbol: str, bars):
        self._bars = {symbol: bars}

    def get_available_symbols(self):
        return list(self._bars)

    def get_bars(self, symbol, start_date, end_date):
        return [b for b in self._bars[symbol] if start_date <= b.date <= end_date]

    def get_data_quality(self, symbol):
        return {"data_nature": "synthetic", "feed_mode": self.feed_mode}


def _oscillating_bars(symbol: str, cycles: int = 3):
    # Steeper short down-legs against shallower long up-legs: SMA(3) really does
    # fall below and then back above SMA(8), so the crossover engine alternates
    # between opening and closing instead of staying permanently above it.
    legs = [(3, -0.04), (6, 0.02), (3, -0.02), (6, 0.05)]

    closes = [100.0]
    for _ in range(cycles):
        for length, pct in legs:
            for _ in range(length):
                closes.append(round(closes[-1] * (1 + pct), 4))

    dates = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    bars = []
    for i, d in enumerate(dates):
        close = closes[i]
        open_ = closes[i - 1] if i else close
        bars.append(OHLCVBar(
            date=d.strftime("%Y-%m-%d"),
            open=open_,
            high=max(open_, close) * 1.002,
            low=min(open_, close) * 0.998,
            close=close,
            volume=1000.0,
        ))
    return bars


class TestEngineIntegration:
    def test_buy_and_hold_journal_carries_provenance_and_risk_fields(self):
        result = run_backtest(_base_spec())
        assert result["run_status"] == "completed"
        for txn in result["transactions"]:
            assert txn["actor"] == "strategy"
            assert txn["strategy_config_hash"] == result["config_hash"]
            for key in ("stop_price", "target_price", "planned_risk", "planned_r", "realized_pnl", "realized_r"):
                assert key in txn
        assert result["transactions"][0]["realized_pnl"] is None

    def test_equity_pnl_and_fifo_pnl_agree_on_a_liquidated_run(self):
        result = run_backtest(_base_spec())
        metrics = result["metrics"]
        assert metrics["closed_trade_count"] == 1
        assert metrics["pnl_cross_check"]["consistent"] is True
        assert metrics["net_pnl"] == pytest.approx(
            result["final_equity"] - result["initial_cash"], abs=0.01
        )
        assert sum(t["realized_pnl"] or 0.0 for t in result["transactions"]) == pytest.approx(
            metrics["net_pnl"], abs=0.01
        )

    def test_undeclared_stop_keeps_r_undefined_in_the_engine(self):
        metrics = run_backtest(_base_spec())["metrics"]
        assert isinstance(metrics["average_realized_r"], dict)
        assert metrics["average_realized_r"]["value"] is None

    def test_declared_stop_makes_realized_r_computable(self):
        result = run_backtest(_base_spec(risk_parameters={"stop_pct": 0.05, "target_pct": 0.10}))
        buy, sell = result["transactions"]
        assert buy["stop_price"] is not None
        assert buy["planned_r"] == pytest.approx(2.0)
        assert sell["planned_risk"] is not None
        assert isinstance(result["metrics"]["average_realized_r"], float)
        assert result["metrics"]["average_realized_r"] == pytest.approx(sell["realized_r"])

    def test_round_trip_records_are_returned_and_sum_to_net_pnl(self):
        result = run_backtest(_base_spec())
        trips = result["round_trips"]
        assert len(trips) == 1
        assert trips[0]["direction"] == "long"
        assert trips[0]["entry_date"] == result["transactions"][0]["execution_date"]
        assert trips[0]["realized_pnl"] == pytest.approx(result["metrics"]["net_pnl"], abs=0.01)

    def test_multi_round_trip_run_produces_win_rate_and_profit_factor(self):
        provider = _SeriesProvider("SYN:OSC", _oscillating_bars("SYN:OSC", cycles=3))
        result = run_backtest(
            _base_spec(
                template_type="sma_crossover",
                asset_ids=["SYN:OSC"],
                signal_parameters={"short_window": 3, "long_window": 8},
                start_date="2024-01-02",
                end_date="2024-12-31",
            ),
            data_provider=provider,
        )
        assert result["run_status"] == "completed"
        metrics = result["metrics"]
        summary = metrics["round_trip_summary"]
        assert metrics["closed_trade_count"] >= 2, "oscillating series should produce several round trips"
        assert summary["win_count"] + summary["loss_count"] + summary["flat_count"] == metrics["closed_trade_count"]
        assert metrics["win_rate_pct"] == pytest.approx(
            summary["win_count"] / metrics["closed_trade_count"] * 100, abs=0.01
        )
        # Both sides of the ledger are exercised: a one-sided series would let a
        # zero denominator pass unnoticed.
        assert summary["win_count"] >= 1
        assert summary["loss_count"] >= 1
        assert summary["gross_loss"] > 0
        assert isinstance(metrics["profit_factor"], float)
        assert metrics["profit_factor"] == pytest.approx(
            summary["gross_profit"] / summary["gross_loss"], abs=0.01
        )
        assert metrics["pnl_cross_check"]["consistent"] is True
        assert summary["open_shares"] == 0

        breakdown = metrics["day_of_week_breakdown"]
        attributed = sum(r["closed_trades"] for r in breakdown["rows"])
        assert attributed + breakdown["unattributed_trades"] == len(result["round_trips"])
        assert sum(r["realized_pnl"] for r in breakdown["rows"]) == pytest.approx(
            summary["net_realized_pnl"], abs=0.01
        )

        # numpy scalars make the run row unserializable, so json.dumps is the check.
        json.dumps(metrics)

    def test_methodology_documents_the_new_journal_fields(self):
        methodology = run_backtest(_base_spec())["methodology_summary"]
        semantics = methodology["journal_field_semantics"]
        assert set(semantics) >= {"actor", "planned_risk", "planned_r", "realized_r", "stop_price"}
        assert methodology["parameters"]["risk_parameters_declared"] is False
        assert any("stop" in c for c in methodology["constraints"])

    def test_run_without_trades_reports_no_closed_round_trips(self):
        result = run_backtest(_base_spec(initial_cash=1))
        assert result["run_status"] == "completed"
        assert result["transactions"] == []
        assert result["metrics"]["closed_trade_count"] == 0
        assert result["metrics"]["win_rate_pct"]["value"] is None
        assert result["round_trips"] == []


class TestRiskPlanSurvivesTheApi:
    """The attribution code can only honour a plan that reaches it.

    StrategySpec did not declare ``risk_parameters`` and experiments.py stores
    ``strategy_spec.model_dump()``, so a declared stop/target was dropped at the
    API boundary: every run through the product had an undeclared plan no matter
    what the user asked for.
    """

    def test_declared_risk_parameters_survive_experiment_creation(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Risk plan round trip",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
                "risk_parameters": {"stop_pct": 0.05, "target_pct": 0.1},
            },
        })
        assert resp.status_code == 201, resp.text

        versions = client.get(
            f"/api/v1/experiments/{resp.json()['id']}/versions", headers=auth_headers
        )
        assert versions.status_code == 200
        spec = versions.json()[0]["strategy_spec"]
        assert spec["risk_parameters"] == {"stop_pct": 0.05, "target_pct": 0.1}

    def test_undeclared_risk_parameters_stay_absent_rather_than_defaulted(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "No declared risk plan",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            },
        })
        assert resp.status_code == 201, resp.text

        versions = client.get(
            f"/api/v1/experiments/{resp.json()['id']}/versions", headers=auth_headers
        )
        spec = versions.json()[0]["strategy_spec"]
        # An empty dict means "nothing declared", which the attribution layer
        # must keep reading as undefined rather than as a zero-risk trade.
        assert spec["risk_parameters"] == {}
