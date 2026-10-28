"""Declared stops and targets must actually execute, in both engines.

Before ``app.engine.exit_rules`` existed a risk plan was recorded in the journal
and then ignored: the backtester held every position to the end of the data and
the forward simulator held it to the holding-period clock, while the journal
printed a stop price and an R multiple as though the level had been honoured.

Only daily OHLC bars are available, so the intra-bar path is unknowable. Every
expectation below therefore pins the *disclosed conservative* convention rather
than a guess: a stop fills at min(bar_open, stop), a target fills at the target
and never at a better gapped open, and a bar that touches both is resolved as
stop-first. The fixture bars are written out longhand so the arithmetic in the
assertions can be checked by hand against them.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.engine import exit_rules
from app.engine.backtest import run_backtest
from app.engine.simulation import ForwardSimulator
from app.engine.strategies import (
    run_buy_and_hold,
    run_price_change_fixed_hold,
    run_sma_crossover,
)

SYMBOL = "US:TEST"


def _frame(rows):
    """rows: (date_str, open, high, low, close) -> the DataFrame the engine eats."""
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(d),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
            }
            for d, o, h, lo, c in rows
        ]
    )


# Entry bar (0) opens at 100. Bar 3 trades down through a 95 stop; the last bar
# closes far above it, so a strategy that ignored the stop would look profitable.
STOP_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
    ("2024-01-03", 100.0, 102.0, 99.5, 101.0),
    ("2024-01-04", 101.0, 103.0, 100.0, 102.0),
    ("2024-01-05", 102.0, 104.0, 93.0, 94.0),
    ("2024-01-08", 94.0, 96.0, 92.0, 95.0),
    ("2024-01-09", 95.0, 120.0, 94.0, 119.0),
]

# Bar 1 opens below the stop: the first tradable price is the open, so the fill
# is 90 and not the declared 95. Reporting 95 here would invent a price nobody
# could have traded at.
GAP_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
    ("2024-01-03", 90.0, 91.0, 88.0, 89.0),
    ("2024-01-04", 89.0, 92.0, 88.0, 91.0),
]

# Bar 2 opens above the 110 target. A limit order resting at the target fills at
# the target, so the better open must not be credited.
TARGET_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
    ("2024-01-03", 100.0, 105.0, 99.0, 104.0),
    ("2024-01-04", 112.0, 118.0, 111.0, 116.0),
    ("2024-01-05", 116.0, 117.0, 115.0, 116.5),
]

# The entry bar itself dips below the stop. Its range is not evidence: the entry
# filled at that bar's open and the rest of the session is unknowable, so the
# earliest a stop can fire is the following session (2024-01-10).
SMA_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
    ("2024-01-03", 100.0, 101.0, 97.0, 98.0),
    ("2024-01-04", 98.0, 99.0, 95.0, 96.0),
    ("2024-01-05", 96.0, 98.0, 95.0, 97.0),
    ("2024-01-08", 97.0, 100.0, 96.0, 99.0),
    ("2024-01-09", 100.0, 102.0, 93.0, 101.0),  # cross-up executes at this open
    ("2024-01-10", 99.0, 99.5, 94.0, 94.5),  # stop 95 fires here
    ("2024-01-11", 92.0, 93.0, 89.0, 90.0),  # cross-down would execute here
    ("2024-01-12", 90.0, 91.0, 87.0, 88.0),
]

# A -10% one-day drop signals at bar 1, enters at bar 2's open, and the
# two-session holding clock expires at bar 4's close.
FIXED_HOLD_BARS = [
    ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
    ("2024-01-03", 95.0, 96.0, 89.0, 90.0),
    ("2024-01-04", 90.0, 91.0, 89.0, 90.5),
    ("2024-01-05", 90.5, 92.0, 90.0, 91.0),
    ("2024-01-08", 91.0, 96.0, 90.0, 95.0),
]


class _FakeStoredBar:
    def __init__(self, bar_date, open_, high, low, close):
        self.bar_date = bar_date
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


def _stored(rows):
    return [
        _FakeStoredBar(date(int(r[0][:4]), int(r[0][5:7]), int(r[0][8:10])), r[1], r[2], r[3], r[4])
        for r in rows
    ]


class TestResolveLevels:
    def test_absent_plan_is_undeclared_not_zero(self):
        assert exit_rules.resolve_levels(None, 100.0) == (None, None)
        assert exit_rules.resolve_levels({}, 100.0) == (None, None)

    def test_percentages_resolve_against_the_entry_price(self):
        assert exit_rules.resolve_levels({"stop_pct": 0.05, "target_pct": 0.10}, 100.0) == (95.0, 110.0)

    def test_absolute_levels_win_over_percentages(self):
        stop, target = exit_rules.resolve_levels(
            {"stop_price": 92.0, "stop_pct": 0.05, "target_price": 108.0, "target_pct": 0.10}, 100.0
        )
        assert (stop, target) == (92.0, 108.0)

    def test_levels_that_define_no_long_risk_are_dropped_and_said_so(self):
        assert exit_rules.resolve_levels({"stop_price": 105.0}, 100.0) == (None, None)
        assert exit_rules.resolve_levels({"target_price": 95.0}, 100.0) == (None, None)

        stop_warnings = exit_rules.unusable_warnings({"stop_price": 105.0}, None, None)
        assert len(stop_warnings) == 1 and "at or above the entry price" in stop_warnings[0]
        target_warnings = exit_rules.unusable_warnings({"target_price": 95.0}, None, None)
        assert len(target_warnings) == 1 and "at or below the entry price" in target_warnings[0]
        # A plan that was honoured produces no warning, otherwise the disclosure
        # would be noise and would train readers to ignore it.
        assert exit_rules.unusable_warnings({"stop_price": 95.0}, 95.0, None) == []

    def test_plan_is_read_from_the_spec_the_same_way_everywhere(self):
        spec = {"risk_parameters": {"stop_pct": 0.05}}
        assert exit_rules.plan_from_spec(spec) == {
            "stop_price": None,
            "stop_pct": 0.05,
            "target_price": None,
            "target_pct": None,
        }
        assert exit_rules.plan_from_spec({}) == {
            "stop_price": None,
            "stop_pct": None,
            "target_price": None,
            "target_pct": None,
        }


class TestBarResolution:
    def test_stop_touched_intra_bar_fills_at_the_stop(self):
        bar = {"open": 102.0, "high": 104.0, "low": 93.0, "close": 94.0}
        decision = exit_rules.evaluate_bar(bar, 95.0, None)
        assert decision.reason == exit_rules.STOP
        assert decision.observed_price == 95.0
        assert decision.level == 95.0

    def test_gap_through_the_stop_fills_at_the_worse_open(self):
        bar = {"open": 90.0, "high": 91.0, "low": 88.0, "close": 89.0}
        decision = exit_rules.evaluate_bar(bar, 95.0, None)
        assert decision.reason == exit_rules.STOP
        assert decision.observed_price == 90.0, "a gapped stop must not be credited at the level"
        assert "gapped" in decision.note

    def test_target_fills_at_the_target_not_at_a_better_open(self):
        bar = {"open": 112.0, "high": 118.0, "low": 111.0, "close": 116.0}
        decision = exit_rules.evaluate_bar(bar, None, 110.0)
        assert decision.reason == exit_rules.TARGET
        assert decision.observed_price == 110.0
        assert "gapped above" in decision.note

    def test_bar_touching_both_levels_resolves_to_the_stop(self):
        bar = {"open": 100.0, "high": 112.0, "low": 93.0, "close": 96.0}
        decision = exit_rules.evaluate_bar(bar, 95.0, 110.0)
        assert decision.reason == exit_rules.STOP
        assert decision.observed_price == 95.0

    def test_no_level_touched_and_no_clock_means_no_exit(self):
        bar = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
        assert exit_rules.evaluate_bar(bar, 95.0, 110.0) is None
        assert exit_rules.evaluate_bar(bar, None, None) is None

    def test_clock_exit_uses_the_close_and_loses_to_a_triggered_level(self):
        bar = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
        decision = exit_rules.evaluate_bar(bar, None, None, time_exit=True)
        assert decision.reason == exit_rules.TIME
        assert decision.observed_price == 100.5

        crashing = {"open": 100.0, "high": 101.0, "low": 90.0, "close": 91.0}
        assert exit_rules.evaluate_bar(crashing, 95.0, None, time_exit=True).reason == exit_rules.STOP


class TestBuyAndHoldStopExecution:
    def test_stop_closes_the_position_instead_of_holding_to_the_end(self):
        transactions, warnings = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(STOP_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            risk_plan={"stop_pct": 0.05},
        )

        assert len(transactions) == 2
        entry, exit_ = transactions
        assert entry["side"] == "BUY"
        assert entry["quantity"] == 100
        assert entry["execution_price_before_costs"] == 100.0
        assert entry["exit_reason"] is None

        assert exit_["side"] == "SELL"
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["execution_date"] == "2024-01-05"
        assert exit_["observed_price"] == 95.0
        assert exit_["execution_price_before_costs"] == 95.0
        assert exit_["cash_after"] == 9_500.0
        assert exit_["shares_after"] == 0
        # Had the stop been ignored the position would have ridden to 119.
        assert exit_["execution_date"] != STOP_BARS[-1][0]
        assert warnings == []

    def test_without_a_plan_the_same_bars_hold_to_the_end_and_say_so(self):
        """Negative control: the stop above is doing the work, not the fixture."""
        transactions, _ = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(STOP_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
        )
        exit_ = transactions[-1]
        assert exit_["exit_reason"] == exit_rules.END_OF_DATA
        assert exit_["execution_date"] == "2024-01-09"
        assert exit_["observed_price"] == 119.0
        assert exit_["cash_after"] == 11_900.0

    def test_gapped_stop_fills_at_the_open_and_the_loss_exceeds_one_r(self):
        transactions, _ = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(GAP_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            risk_plan={"stop_pct": 0.05},
        )
        exit_ = transactions[-1]
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["execution_date"] == "2024-01-03"
        assert exit_["observed_price"] == 90.0
        assert exit_["cash_after"] == 9_000.0
        assert "gapped" in exit_["rationale"]

    def test_target_fills_at_the_target(self):
        transactions, _ = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(TARGET_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            risk_plan={"target_pct": 0.10},
        )
        exit_ = transactions[-1]
        assert exit_["exit_reason"] == exit_rules.TARGET
        assert exit_["execution_date"] == "2024-01-04"
        assert exit_["observed_price"] == 110.0
        assert exit_["cash_after"] == 11_000.0

    def test_entry_bar_range_cannot_trigger_the_stop(self):
        """No look-ahead and no same-bar exit: bar 0 dips to 99 with a 99.5 stop."""
        rows = [
            ("2024-01-02", 100.0, 101.0, 99.0, 100.0),  # entry bar dips under the stop
            ("2024-01-03", 100.0, 102.0, 99.8, 101.0),
            ("2024-01-04", 101.0, 103.0, 100.0, 102.0),
        ]
        transactions, _ = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(rows),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            risk_plan={"stop_price": 99.5},
        )
        exit_ = transactions[-1]
        assert exit_["execution_date"] != "2024-01-02", "the entry bar's own range is not exit evidence"
        assert exit_["exit_reason"] == exit_rules.END_OF_DATA

    def test_costs_are_still_charged_on_a_stop_exit(self):
        transactions, _ = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(STOP_BARS),
            initial_cash=10_000,
            commission_rate=0.001,
            slippage_rate=0.001,
            risk_plan={"stop_pct": 0.05},
        )
        entry, exit_ = transactions

        entry_price = round(100.0 * 1.001, 4)  # 100.1
        shares = int(10_000 / (entry_price * 1.001))
        assert entry["quantity"] == shares == 99
        assert entry["execution_price_before_costs"] == pytest.approx(entry_price)

        # The stop is resolved against the price paid, not the quoted open.
        stop = round(entry_price * 0.95, 4)
        assert stop == pytest.approx(95.095)
        entry_gross = round(shares * entry_price, 4)
        cash_after_buy = 10_000 - entry_gross - round(entry_gross * 0.001, 4)

        # The booked price is the price the money used: the declared stop moved
        # by slippage and rounded to the precision the journal reports, and every
        # cost on this leg reconciles against that one number.
        sell_price = round(stop * (1 - 0.001), 4)
        gross = round(shares * sell_price, 4)
        commission = round(gross * 0.001, 4)

        assert exit_["observed_price"] == pytest.approx(stop)
        assert exit_["execution_price_before_costs"] == pytest.approx(sell_price)
        assert exit_["gross_amount"] == pytest.approx(gross)
        assert exit_["commission_amount"] == pytest.approx(commission)
        assert exit_["slippage_amount"] == pytest.approx(
            round(abs(stop - sell_price) * shares, 4)
        )
        assert exit_["net_cash_change"] == pytest.approx(gross - commission)
        assert exit_["cash_after"] == pytest.approx(round(cash_after_buy + gross - commission, 4))
        assert exit_["cash_after"] != pytest.approx(shares * stop), "costs must not vanish on the exit leg"


class TestSmaStopExecution:
    def _run(self, risk_plan):
        return run_sma_crossover(
            symbol=SYMBOL,
            ohlcv=_frame(SMA_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            short_window=2,
            long_window=3,
            risk_plan=risk_plan,
        )

    def test_cross_up_entry_is_unchanged_by_the_exit_rules(self):
        transactions, _ = self._run(None)
        entry = transactions[0]
        assert entry["side"] == "BUY"
        assert entry["signal_date"] == "2024-01-08"
        assert entry["execution_date"] == "2024-01-09"
        assert entry["observed_price"] == 100.0

    def test_without_a_plan_the_cross_down_closes_it_as_a_signal_exit(self):
        transactions, _ = self._run(None)
        exit_ = transactions[-1]
        assert exit_["exit_reason"] == exit_rules.SIGNAL
        assert exit_["execution_date"] == "2024-01-11"
        assert exit_["observed_price"] == 92.0
        assert exit_["cash_after"] == 9_200.0

    def test_a_declared_stop_fires_before_the_cross_down_ever_arrives(self):
        transactions, _ = self._run({"stop_pct": 0.05})
        assert len(transactions) == 2
        exit_ = transactions[-1]
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["execution_date"] == "2024-01-10"
        assert exit_["observed_price"] == 95.0
        assert exit_["cash_after"] == 9_500.0
        # The entry bar dipped to 93, below the 95 stop, and must not have fired.
        assert exit_["execution_date"] != "2024-01-09"

    def test_no_re_entry_on_the_session_a_stop_fired(self):
        transactions, _ = self._run({"stop_pct": 0.05})
        assert [t["execution_date"] for t in transactions] == ["2024-01-09", "2024-01-10"]


class TestFixedHoldStopExecution:
    def _run(self, risk_plan=None, holding_days=2, rows=None):
        return run_price_change_fixed_hold(
            symbol=SYMBOL,
            ohlcv=_frame(rows or FIXED_HOLD_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            lookback_days=1,
            threshold_pct=0.05,
            holding_days=holding_days,
            risk_plan=risk_plan,
        )

    def test_holding_clock_exit_keeps_its_exact_session_price_and_wording(self):
        transactions, _ = self._run(None)
        entry, exit_ = transactions
        assert entry["execution_date"] == "2024-01-04"
        assert entry["quantity"] == 111
        assert entry["cash_after"] == pytest.approx(10.0)
        assert exit_["exit_reason"] == exit_rules.TIME
        assert exit_["execution_date"] == "2024-01-08"
        assert exit_["observed_price"] == 95.0
        assert exit_["rationale"] == "Fixed holding period exit after 2 sessions"
        assert exit_["cash_after"] == pytest.approx(10_555.0)

    def test_a_stop_fires_before_the_clock_when_price_collapses_first(self):
        rows = [
            ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
            ("2024-01-03", 95.0, 96.0, 89.0, 90.0),  # -10% signal
            ("2024-01-04", 90.0, 91.0, 89.0, 90.5),  # entry at 90, stop 85.5
            ("2024-01-05", 88.0, 89.0, 84.0, 85.0),  # stop fires, clock would be 01-08
            ("2024-01-08", 85.0, 96.0, 84.0, 95.0),
        ]
        transactions, _ = self._run({"stop_pct": 0.05}, holding_days=2, rows=rows)
        entry, exit_ = transactions
        assert entry["execution_date"] == "2024-01-04"
        assert entry["execution_price_before_costs"] == 90.0
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["execution_date"] == "2024-01-05"
        assert exit_["observed_price"] == 85.5
        assert "Fixed holding period" not in exit_["rationale"]

    def test_a_target_can_also_close_before_the_clock(self):
        transactions, _ = self._run({"target_pct": 0.05}, holding_days=4)
        entry, exit_ = transactions
        assert entry["execution_price_before_costs"] == 90.0
        assert exit_["exit_reason"] == exit_rules.TARGET
        assert exit_["execution_date"] == "2024-01-08"
        assert exit_["observed_price"] == 94.5, "target fills at the level, not at the 96.0 high"

    def test_scheduled_exit_on_the_final_session_is_a_time_exit_not_end_of_data(self):
        """The clock lands on the last bar here; the loop must still reach it."""
        transactions, warnings = self._run(None, holding_days=2)
        exit_ = transactions[-1]
        assert exit_["execution_date"] == FIXED_HOLD_BARS[-1][0]
        assert exit_["exit_reason"] == exit_rules.TIME
        assert exit_["exit_reason"] != exit_rules.END_OF_DATA
        assert not any("force-closed" in w for w in warnings)


class TestForwardSimulatorUsesTheSamePolicy:
    """The replay and the forward simulator must not disagree about a fill."""

    def _sim(self, rows, spec_extra=None):
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": [SYMBOL],
            "signal_parameters": {},
        }
        spec.update(spec_extra or {})
        sim = ForwardSimulator(
            strategy_spec=spec,
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            dataset_cutoff_at="2024-01-09",
            engine_version="test",
        )
        frame = _frame(rows)
        results = []
        for i in range(len(frame)):
            window = frame.iloc[: i + 1]
            results.append(sim.step(window, rows[i][0]))
        return sim, results

    def test_stop_fills_on_the_same_session_at_the_same_price_as_the_backtester(self):
        _, results = self._sim(STOP_BARS, {"risk_parameters": {"stop_pct": 0.05}})
        exits = [r for r in results if r.decision_type == "simulated_exit"]
        assert len(exits) == 1
        detail = exits[0].decision_detail
        assert exits[0].snapshot_date == "2024-01-05"
        assert detail["exit_reason"] == exit_rules.STOP
        assert detail["observed_price"] == 95.0
        assert detail["execution_price"] == 95.0
        assert detail["exit_level"] == 95.0
        assert exits[0].paper_cash == 9_500.0

        replay_exit = run_buy_and_hold(
            symbol=SYMBOL,
            ohlcv=_frame(STOP_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            risk_plan={"stop_pct": 0.05},
        )[0][-1]
        assert replay_exit["execution_date"] == exits[0].snapshot_date
        assert replay_exit["observed_price"] == detail["observed_price"]
        assert replay_exit["cash_after"] == exits[0].paper_cash
        assert replay_exit["exit_reason"] == detail["exit_reason"]

    def test_gapped_stop_also_matches(self):
        _, results = self._sim(GAP_BARS, {"risk_parameters": {"stop_pct": 0.05}})
        exit_ = [r for r in results if r.decision_type == "simulated_exit"][0]
        assert exit_.decision_detail["observed_price"] == 90.0
        assert exit_.paper_cash == 9_000.0

    def test_holding_clock_survived_the_move_into_the_shared_exit_path(self):
        spec_extra = {
            "template_type": "price_change_fixed_hold",
            "signal_parameters": {"lookback_days": 1, "threshold_pct": 0.05, "holding_days": 2},
        }
        sim, results = self._sim(FIXED_HOLD_BARS, spec_extra)
        entries = [r for r in results if r.decision_type == "simulated_entry"]
        exits = [r for r in results if r.decision_type == "simulated_exit"]
        assert len(entries) == 1 and entries[0].snapshot_date == "2024-01-04"
        assert entries[0].decision_detail["quantity"] == 111
        assert len(exits) == 1
        assert exits[0].snapshot_date == "2024-01-08"
        assert exits[0].decision_detail["exit_reason"] == exit_rules.TIME
        assert exits[0].decision_detail["rationale"] == "Fixed holding period exit after 2 sessions"
        assert exits[0].paper_cash == 10_555.0
        assert sim._holding_steps_remaining is None

        replay = run_price_change_fixed_hold(
            symbol=SYMBOL,
            ohlcv=_frame(FIXED_HOLD_BARS),
            initial_cash=10_000,
            commission_rate=0.0,
            slippage_rate=0.0,
            lookback_days=1,
            threshold_pct=0.05,
            holding_days=2,
        )[0]
        assert replay[-1]["execution_date"] == exits[0].snapshot_date
        assert replay[-1]["cash_after"] == exits[0].paper_cash

    def test_stop_pre_empts_the_holding_clock_in_forward_simulation(self):
        rows = [
            ("2024-01-02", 100.0, 101.0, 99.0, 100.0),
            ("2024-01-03", 95.0, 96.0, 89.0, 90.0),
            ("2024-01-04", 90.0, 91.0, 89.0, 90.5),
            ("2024-01-05", 88.0, 89.0, 84.0, 85.0),
            ("2024-01-08", 85.0, 96.0, 84.0, 95.0),
        ]
        spec_extra = {
            "template_type": "price_change_fixed_hold",
            "signal_parameters": {"lookback_days": 1, "threshold_pct": 0.05, "holding_days": 2},
            "risk_parameters": {"stop_pct": 0.05},
        }
        _, results = self._sim(rows, spec_extra)
        exits = [r for r in results if r.decision_type == "simulated_exit"]
        assert len(exits) == 1
        assert exits[0].snapshot_date == "2024-01-05"
        assert exits[0].decision_detail["exit_reason"] == exit_rules.STOP
        assert exits[0].decision_detail["observed_price"] == 85.5

    def test_levels_do_not_survive_into_the_next_position(self):
        sim, _ = self._sim(GAP_BARS, {"risk_parameters": {"stop_pct": 0.05}})
        assert sim.shares == 0
        assert sim.stop_level is None
        assert sim.target_level is None
        assert sim.entry_price is None

    def test_undeclared_plan_leaves_the_simulator_unprotected_and_says_so(self):
        sim, results = self._sim(STOP_BARS)
        assert sim.stop_level is None
        assert [r.decision_type for r in results].count("simulated_exit") == 0


class TestBacktestJournalReportsTheLevelItTraded:
    def _run(self, rows, template="buy_and_hold", risk=None, signal=None):
        spec = {
            "template_type": template,
            "asset_ids": [SYMBOL],
            "start_date": rows[0][0],
            "end_date": rows[-1][0],
            "initial_cash": 10_000,
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
        }
        if risk is not None:
            spec["risk_parameters"] = risk
        if signal is not None:
            spec["signal_parameters"] = signal
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage:
            mock_storage.return_value.get_bars.return_value = _stored(rows)
            return run_backtest(spec, db_session=MagicMock(), data_source="stored")

    def test_stop_fill_reaches_the_journal_at_exactly_minus_one_r(self):
        result = self._run(STOP_BARS, risk={"stop_pct": 0.05})
        assert result["run_status"] == "completed"
        entry, exit_ = result["transactions"]
        assert entry["stop_price"] == 95.0
        assert entry["planned_risk"] == 500.0
        assert exit_["stop_price"] == 95.0, "the exit inherits the plan it was traded under"
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["realized_pnl"] == -500.0
        assert exit_["realized_r"] == -1.0

    def test_gapped_stop_is_recorded_as_worse_than_one_r_not_floored(self):
        result = self._run(GAP_BARS, risk={"stop_pct": 0.05})
        exit_ = result["transactions"][-1]
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["realized_pnl"] == -1_000.0
        assert exit_["realized_r"] == -2.0, "a gap through the stop is a real 2R loss"

    def test_target_fill_reaches_the_journal(self):
        result = self._run(TARGET_BARS, risk={"stop_pct": 0.05, "target_pct": 0.10})
        entry, exit_ = result["transactions"]
        assert entry["planned_r"] == 2.0
        assert exit_["exit_reason"] == exit_rules.TARGET
        assert exit_["realized_pnl"] == 1_000.0
        assert exit_["realized_r"] == 2.0

    def test_sma_stop_exit_is_journalled_with_its_level(self):
        result = self._run(
            SMA_BARS,
            template="sma_crossover",
            risk={"stop_pct": 0.05},
            signal={"short_window": 2, "long_window": 3},
        )
        exit_ = result["transactions"][-1]
        assert exit_["exit_reason"] == exit_rules.STOP
        assert exit_["execution_date"] == "2024-01-10"
        assert exit_["stop_price"] == 95.0
        assert exit_["realized_r"] == -1.0

    def test_undeclared_plan_reports_null_levels_rather_than_zeros(self):
        result = self._run(STOP_BARS, risk={})
        entry, exit_ = result["transactions"]
        assert entry["stop_price"] is None
        assert entry["planned_risk"] is None
        assert entry["planned_r"] is None
        assert exit_["realized_r"] is None
        assert exit_["exit_reason"] == exit_rules.END_OF_DATA
