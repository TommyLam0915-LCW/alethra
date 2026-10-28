"""Deterministic regressions for the price_change_fixed_hold replay clock.

These pin two properties that no existing test asserted:
  1. the liquidation lands exactly ``holding_days`` sessions after the entry
     execution bar, and fills at that session's close (the convention documented
     in the backtest methodology summary and used by the forward simulator);
  2. an entry signal that qualifies *inside* the holding window is ignored, so a
     position is never doubled up.

Both use hand-built synthetic OHLCV with opens deliberately offset from closes,
so an open/close mix-up cannot pass by coincidence.
"""

import pandas as pd
import pytest

from app.engine.strategies import run_price_change_fixed_hold

LOOKBACK_DAYS = 2
THRESHOLD_PCT = 0.05
HOLDING_DAYS = 3
COMMISSION_RATE = 0.001
SLIPPAGE_RATE = 0.001
INITIAL_CASH = 100_000.0


def _frame(closes):
    dates = pd.bdate_range("2024-01-01", periods=len(closes))
    opens = [c + 2.0 for c in closes]
    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": [max(o, c) + 1.0 for o, c in zip(opens, closes)],
        "low": [min(o, c) - 1.0 for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [1_000_000] * len(closes),
    })


def _run(closes):
    return run_price_change_fixed_hold(
        symbol="SYNTH-A",
        ohlcv=_frame(closes),
        initial_cash=INITIAL_CASH,
        commission_rate=COMMISSION_RATE,
        slippage_rate=SLIPPAGE_RATE,
        lookback_days=LOOKBACK_DAYS,
        threshold_pct=THRESHOLD_PCT,
        holding_days=HOLDING_DAYS,
    )


def test_exit_fills_at_the_close_of_the_bar_holding_days_after_entry():
    # Signal on bar 3 (90 vs 100 = -10%), entry executed on bar 4, so the exit
    # belongs on bar 4 + 3 = bar 7.
    closes = [100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0]
    ohlcv = _frame(closes)
    txns, warnings = _run(closes)

    assert warnings == []
    assert [t["side"] for t in txns] == ["BUY", "SELL"]

    buy, sell = txns
    assert buy["execution_date"] == ohlcv.iloc[4]["date"].strftime("%Y-%m-%d")
    assert buy["signal_date"] == ohlcv.iloc[3]["date"].strftime("%Y-%m-%d")

    expected_exit_date = ohlcv.iloc[4 + HOLDING_DAYS]["date"].strftime("%Y-%m-%d")
    assert sell["execution_date"] == expected_exit_date

    exit_close = closes[4 + HOLDING_DAYS]
    assert sell["observed_price"] == round(exit_close, 4)
    assert sell["execution_price_before_costs"] == round(exit_close * (1 - SLIPPAGE_RATE), 4)
    assert sell["rationale"] == f"Fixed holding period exit after {HOLDING_DAYS} sessions"


def test_costs_are_booked_once_and_cash_is_continuous_across_the_round_trip():
    closes = [100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.0, 101.0, 102.0]
    txns, _ = _run(closes)
    buy, sell = txns

    entry_open = closes[4] + 2.0
    entry_price = entry_open * (1 + SLIPPAGE_RATE)
    assert buy["execution_price_before_costs"] == round(entry_price, 4)
    assert buy["quantity"] == int(INITIAL_CASH / (entry_price * (1 + COMMISSION_RATE)))

    qty = buy["quantity"]
    assert buy["commission_amount"] == round(round(qty * entry_price, 4) * COMMISSION_RATE, 4)
    assert buy["cash_after"] == round(INITIAL_CASH - qty * entry_price - buy["commission_amount"], 4)

    exit_close = closes[7]
    exit_price = exit_close * (1 - SLIPPAGE_RATE)
    assert sell["quantity"] == qty
    assert sell["commission_amount"] == round(round(qty * exit_price, 4) * COMMISSION_RATE, 4)
    assert sell["cash_after"] == round(buy["cash_after"] + qty * exit_price - sell["commission_amount"], 4)
    assert sell["shares_after"] == 0


def test_a_signal_inside_the_holding_window_is_ignored_not_doubled_up():
    # Bar 6 qualifies as an entry (85 vs 95 = -10.5%) but falls inside the
    # holding window opened at bar 4, so it must be skipped; the next qualifying
    # drop after the exit (bar 9) opens the second position.
    closes = [100.0, 100.0, 100.0, 90.0, 95.0, 88.0, 85.0, 98.0,
              90.0, 88.0, 87.0, 86.0, 85.0, 84.0, 83.0, 82.0]
    ohlcv = _frame(closes)
    txns, warnings = _run(closes)

    assert warnings == []
    assert [t["side"] for t in txns] == ["BUY", "SELL", "BUY", "SELL"]
    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]

    assert txns[0]["execution_date"] == dates[4]
    assert txns[1]["execution_date"] == dates[7]
    assert txns[2]["execution_date"] == dates[10]
    assert txns[3]["execution_date"] == dates[13]

    # No fill at all on the ignored bar, and never two positions at once.
    assert dates[6] not in [t["execution_date"] for t in txns]
    for txn in txns:
        assert txn["shares_after"] >= 0


def test_open_position_at_end_of_data_is_force_closed_not_silently_dropped():
    # 7 bars (indices 0-6); the scheduled exit index is 3 + 1 + 3 = 7, past the
    # end of the data, so the force-close path is what runs.
    closes = [100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0]
    ohlcv = _frame(closes)
    txns, warnings = _run(closes)

    assert [t["side"] for t in txns] == ["BUY", "SELL"]
    assert txns[1]["execution_date"] == ohlcv.iloc[-1]["date"].strftime("%Y-%m-%d")
    assert txns[1]["rationale"] == "Forced exit: end of data with open position"
    assert txns[1]["shares_after"] == 0
    assert len(warnings) == 1
    assert "force-closed" in warnings[0]


@pytest.mark.parametrize("holding_days", [1, 2, 5])
def test_exit_session_count_tracks_the_holding_days_parameter(holding_days):
    closes = [100.0, 100.0, 100.0, 90.0, 95.0, 96.0, 97.0, 98.0, 99.0,
              100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    ohlcv = _frame(closes)
    txns, warnings = run_price_change_fixed_hold(
        symbol="SYNTH-A",
        ohlcv=ohlcv,
        initial_cash=INITIAL_CASH,
        commission_rate=COMMISSION_RATE,
        slippage_rate=SLIPPAGE_RATE,
        lookback_days=LOOKBACK_DAYS,
        threshold_pct=THRESHOLD_PCT,
        holding_days=holding_days,
    )

    assert warnings == []
    assert [t["side"] for t in txns] == ["BUY", "SELL"]
    entry_idx = 4
    assert txns[1]["execution_date"] == ohlcv.iloc[entry_idx + holding_days]["date"].strftime("%Y-%m-%d")
