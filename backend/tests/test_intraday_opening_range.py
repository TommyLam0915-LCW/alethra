"""Tests for the intraday (5-minute) opening-range-breakout engine.

These use a clearly-labelled SYNTHETIC 5-minute fixture. Passing here proves the
mechanics (signal timing, next-bar fill, stop/target/gap resolution, session-end
forced close, cost reconciliation) are correct and that one parsed config drives
both the batch backtest and the incremental forward replay identically. It is
NOT evidence that the rule is profitable on real markets, and it does not
exercise the live advance API or UI, which still step by day.
"""

from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from app.engine import intraday
from app.engine.intraday import (
    INTRADAY_TIMEFRAMES,
    SESSION_END,
    OpeningRangeForwardReplay,
    parse_intraday_config,
    run_opening_range_backtest,
)


# ---------------------------------------------------------------------------
# Synthetic 5-minute fixture helpers (labelled synthetic; not real market data)
# ---------------------------------------------------------------------------

def _session(day: date, specs, start=time(9, 30), step_min=5):
    """Build bar rows for one session from (open, high, low, close, volume)."""
    rows = []
    t = datetime.combine(day, start)
    for (o, h, l, c, v) in specs:
        rows.append({"timestamp": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
        t += timedelta(minutes=step_min)
    return rows


def _frame(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _spec(**overrides):
    base = {
        "template_type": "opening_range_breakout",
        "timeframe": "5m",
        "asset_ids": ["DEMO-ALPHA"],
        "signal_parameters": {
            "opening_range_bars": 3,
            "volume_window_bars": 2,
            "volume_min_ratio": 1.0,
        },
        "risk_parameters": {},
    }
    base.update(overrides)
    return base


def _run(spec, rows, initial_cash=10000.0, commission_rate=0.0, slippage_rate=0.0):
    config = parse_intraday_config(spec)
    txns, warnings = run_opening_range_backtest(
        symbol=spec["asset_ids"][0],
        bars=_frame(rows),
        initial_cash=initial_cash,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
        config=config,
    )
    return txns, warnings


DAY1 = date(2024, 1, 2)
DAY2 = date(2024, 1, 3)

# Opening range = first 3 bars: OR_high 102, OR_low 99. Bar 4 closes 102.5 > 102
# with volume 2000 >= 1.0 * mean(1000, 1000) → breakout signal, fills at bar 5 open.
_OR_AND_SIGNAL = [
    (100.0, 101.0, 99.0, 100.5, 1000),   # 09:30 OR
    (100.5, 102.0, 100.0, 101.0, 1000),  # 09:35 OR (high 102)
    (101.0, 101.5, 100.0, 101.0, 1000),  # 09:40 OR completes (OR_high 102, OR_low 99)
    (101.0, 103.0, 101.0, 102.5, 2000),  # 09:45 close 102.5 > 102 → signal
]
_ENTRY_BAR = (102.5, 103.0, 102.0, 102.8, 1000)  # 09:50 entry fills at open 102.5


# ---------------------------------------------------------------------------
# Config parsing / validation
# ---------------------------------------------------------------------------

def test_parse_rejects_daily_timeframe():
    with pytest.raises(ValueError, match="not an intraday timeframe"):
        parse_intraday_config(_spec(timeframe="1d"))


def test_parse_accepts_all_intraday_timeframes():
    for tf in sorted(INTRADAY_TIMEFRAMES):
        cfg = parse_intraday_config(_spec(timeframe=tf))
        assert cfg.timeframe == tf


def test_parse_validates_opening_range_bars():
    spec = _spec()
    spec["signal_parameters"]["opening_range_bars"] = 0
    with pytest.raises(ValueError, match="opening_range_bars"):
        parse_intraday_config(spec)


def test_parse_validates_risk_per_trade_pct():
    spec = _spec()
    spec["signal_parameters"]["risk_per_trade_pct"] = 1.5
    with pytest.raises(ValueError, match="risk_per_trade_pct"):
        parse_intraday_config(spec)


# ---------------------------------------------------------------------------
# No-entry scenarios
# ---------------------------------------------------------------------------

def test_no_entry_when_no_breakout():
    rows = _session(DAY1, [
        (100.0, 101.0, 99.0, 100.5, 1000),
        (100.5, 102.0, 100.0, 101.0, 1000),
        (101.0, 101.5, 100.0, 101.0, 1000),
        (101.0, 101.9, 100.5, 101.5, 1000),  # close 101.5 <= OR_high 102 → no signal
        (101.5, 102.0, 101.0, 101.8, 1000),  # close still <= 102
    ])
    txns, _ = _run(_spec(), rows)
    assert txns == []


def test_no_entry_when_volume_filter_blocks():
    # Breakout close above OR_high but signal-bar volume below the past-only baseline.
    rows = _session(DAY1, [
        (100.0, 101.0, 99.0, 100.5, 1000),
        (100.5, 102.0, 100.0, 101.0, 1000),
        (101.0, 101.5, 100.0, 101.0, 1000),
        (101.0, 103.0, 101.0, 102.5, 100),  # breakout close but volume 100 < mean(1000,1000)
        (102.5, 103.0, 102.0, 102.8, 1000),
    ])
    txns, _ = _run(_spec(), rows)
    assert txns == []


def test_volume_filter_is_past_only_no_future_leak():
    # A huge volume bar AFTER the signal bar must not enable the earlier signal.
    rows = _session(DAY1, [
        (100.0, 101.0, 99.0, 100.5, 1000),
        (100.5, 102.0, 100.0, 101.0, 1000),
        (101.0, 101.5, 100.0, 101.0, 1000),
        (101.0, 103.0, 101.0, 102.5, 100),   # signal bar, low volume → blocked
        (102.5, 103.0, 102.0, 102.8, 999999),  # future volume cannot retroactively allow it
    ])
    txns, _ = _run(_spec(), rows)
    assert txns == []


# ---------------------------------------------------------------------------
# Entry + target exit
# ---------------------------------------------------------------------------

def test_entry_then_target_exit():
    spec = _spec(risk_parameters={"target_pct": 0.02})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,                            # 09:50 entry fills at 102.5
        (102.8, 105.0, 102.5, 104.9, 1000),    # 09:55 high 105 >= target 104.55 → target
    ])
    txns, _ = _run(spec, rows)
    assert len(txns) == 2
    buy, sell = txns
    assert buy["side"] == "BUY"
    assert buy["execution_price_before_costs"] == 102.5
    assert sell["side"] == "SELL"
    assert sell["exit_reason"] == intraday.exit_rules.TARGET
    # Target fills at the declared level exactly (102.5 * 1.02), not the better high.
    assert sell["execution_price_before_costs"] == round(102.5 * 1.02, 4)


# ---------------------------------------------------------------------------
# Stop exit (intra-bar, gap-through, and same-bar ambiguity)
# ---------------------------------------------------------------------------

def test_stop_exit_intrabar():
    spec = _spec(risk_parameters={"stop_pct": 0.02})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,                            # entry at 102.5, stop = 100.45
        (102.5, 103.0, 100.0, 101.0, 1000),    # low 100 <= 100.45 → stop at 100.45
    ])
    txns, _ = _run(spec, rows)
    sell = txns[-1]
    assert sell["exit_reason"] == intraday.exit_rules.STOP
    assert sell["execution_price_before_costs"] == round(102.5 * 0.98, 4)


def test_stop_gap_through_fills_at_worse_open():
    spec = _spec(risk_parameters={"stop_pct": 0.02})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,                            # entry 102.5, stop 100.45
        (99.0, 99.5, 98.0, 98.5, 1000),        # opens 99 BELOW stop → fills at open 99
    ])
    txns, _ = _run(spec, rows)
    sell = txns[-1]
    assert sell["exit_reason"] == intraday.exit_rules.STOP
    # Fills at the worse open (99), not at the declared stop (100.45).
    assert sell["execution_price_before_costs"] == 99.0
    assert sell["execution_price_before_costs"] < round(102.5 * 0.98, 4)


def test_same_bar_stop_and_target_resolves_stop_first():
    spec = _spec(risk_parameters={"stop_pct": 0.02, "target_pct": 0.02})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,                            # entry 102.5, stop 100.45, target 104.55
        (102.5, 105.0, 100.0, 102.0, 1000),    # high>=target AND low<=stop on one bar
    ])
    txns, _ = _run(spec, rows)
    sell = txns[-1]
    # Conservative, disclosed assumption: stop fills first when a bar touches both.
    assert sell["exit_reason"] == intraday.exit_rules.STOP


def test_entry_bar_range_is_not_exit_evidence():
    # The entry bar's own low dips below the stop, but levels only apply from the
    # bar AFTER entry, so no exit fires on the entry bar.
    spec = _spec(risk_parameters={"stop_pct": 0.02})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        (102.5, 103.0, 95.0, 102.8, 1000),     # entry bar; low 95 < stop but ignored
        (102.8, 103.0, 102.5, 102.9, 1000),    # next bar, no stop touch → still holding
    ])
    txns, warnings = _run(spec, rows)
    # No mid-session exit; position flattens at session end instead.
    assert txns[0]["side"] == "BUY"
    assert txns[-1]["side"] == "SELL"
    assert txns[-1]["exit_reason"] == SESSION_END


# ---------------------------------------------------------------------------
# Session-end forced close / no cross-day hold
# ---------------------------------------------------------------------------

def test_session_end_forced_close_no_overnight_carry():
    spec = _spec()  # no stop/target declared → only session-end can close it
    rows = (
        _session(DAY1, _OR_AND_SIGNAL + [
            _ENTRY_BAR,                        # 09:50 entry
            (102.8, 103.2, 102.4, 103.0, 1000),
            (103.0, 103.4, 102.8, 103.2, 1000),  # last bar of DAY1 → forced close
        ])
        + _session(DAY2, [                    # a second session proves day1 closed flat
            (103.0, 104.0, 102.0, 103.5, 1000),
            (103.5, 104.0, 103.0, 103.8, 1000),
            (103.8, 104.2, 103.5, 104.0, 1000),
            (104.0, 104.5, 103.8, 104.2, 1000),
        ])
    )
    txns, _ = _run(spec, rows)
    sells = [t for t in txns if t["side"] == "SELL"]
    assert any(s["exit_reason"] == SESSION_END for s in sells)
    # The DAY1 position closed on DAY1's last bar timestamp, not carried into DAY2.
    day1_last = datetime.combine(DAY1, time(9, 30)) + timedelta(minutes=5 * 6)
    session_end_sell = next(s for s in sells if s["exit_reason"] == SESSION_END)
    assert session_end_sell["execution_date"] == day1_last.isoformat()
    # After the forced close the engine is flat going into DAY2.
    assert all(t["shares_after"] == 0 for t in sells)


def test_allow_cross_day_disables_session_end_close():
    spec = _spec()
    spec["signal_parameters"]["allow_cross_day"] = True
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,
        (102.8, 103.2, 102.4, 103.0, 1000),
        (103.0, 103.4, 102.8, 103.2, 1000),  # last bar of DAY1
    ])
    txns, _ = _run(spec, rows)
    # Only the entry; no session-end close because overnight carry is allowed and
    # no stop/target was declared. (End-of-data close is not applied by this
    # engine — the batch run simply ends with the position still open.)
    assert [t["side"] for t in txns] == ["BUY"]


# ---------------------------------------------------------------------------
# Cost / cash / P&L reconciliation
# ---------------------------------------------------------------------------

def test_commission_slippage_cash_reconciliation():
    spec = _spec(risk_parameters={"target_pct": 0.02})
    commission_rate = 0.001
    slippage_rate = 0.0005
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,
        (102.8, 106.0, 102.5, 105.0, 1000),  # high well above target → target exit
    ])
    txns, _ = _run(spec, rows, initial_cash=10000.0,
                   commission_rate=commission_rate, slippage_rate=slippage_rate)
    buy, sell = txns

    # Entry leg: exec = next-bar open * (1 + slippage); cash debited gross+commission.
    expected_entry_exec = round(102.5 * (1 + slippage_rate), 4)
    assert buy["execution_price_before_costs"] == expected_entry_exec
    gross_buy = round(buy["quantity"] * expected_entry_exec, 4)
    assert buy["commission_amount"] == round(gross_buy * commission_rate, 4)
    assert buy["net_cash_change"] == round(-(gross_buy + buy["commission_amount"]), 4)

    # Exit leg: target level = round(entry_exec * 1.02, 4); fills there, then
    # slippage applied on the sell; cash credited gross-commission.
    expected_target = round(expected_entry_exec * 1.02, 4)
    assert sell["observed_price"] == expected_target
    expected_sell_exec = round(expected_target * (1 - slippage_rate), 4)
    assert sell["execution_price_before_costs"] == expected_sell_exec
    gross_sell = round(sell["quantity"] * expected_sell_exec, 4)
    assert sell["commission_amount"] == round(gross_sell * commission_rate, 4)
    assert sell["net_cash_change"] == round(gross_sell - sell["commission_amount"], 4)

    # Final cash equals initial cash plus the sum of every net cash change.
    final_cash = sell["cash_after"]
    assert final_cash == round(10000.0 + buy["net_cash_change"] + sell["net_cash_change"], 4)

    # Round-trip net P&L (net of both commissions and slippage) equals the cash delta.
    round_trip_net = round(buy["net_cash_change"] + sell["net_cash_change"], 4)
    assert round(final_cash - 10000.0, 4) == round_trip_net


def test_risk_sized_position_caps_shares_by_stop_distance():
    spec = _spec(risk_parameters={"stop_pct": 0.02})
    spec["signal_parameters"]["risk_per_trade_pct"] = 0.01  # risk 1% of cash = 100
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,
        (102.8, 103.2, 102.4, 103.0, 1000),
        (103.0, 103.4, 102.8, 103.2, 1000),
    ])
    txns, _ = _run(spec, rows, initial_cash=10000.0)
    buy = txns[0]
    entry = buy["execution_price_before_costs"]       # 102.5
    stop = round(entry * 0.98, 4)                      # 100.45
    per_share_risk = entry - stop                      # ~2.05
    # Sized so (entry-stop)*qty <= 100 → qty <= ~48, well under max-affordable ~97.
    assert buy["quantity"] == int(100.0 / per_share_risk)
    assert buy["quantity"] < int(10000.0 / entry)


# ---------------------------------------------------------------------------
# One config, two consumers: backtest == forward replay
# ---------------------------------------------------------------------------

def test_backtest_and_forward_replay_produce_identical_fills():
    spec = _spec(risk_parameters={"stop_pct": 0.02, "target_pct": 0.03})
    rows = _session(DAY1, _OR_AND_SIGNAL + [
        _ENTRY_BAR,
        (102.8, 103.5, 102.4, 103.2, 1200),
        (103.2, 106.5, 103.0, 106.0, 1500),   # high 106.5 >= target 105.575 → target
        (106.0, 106.2, 105.5, 105.8, 1000),
    ])
    config = parse_intraday_config(spec)

    bt_txns, bt_warnings = run_opening_range_backtest(
        symbol=spec["asset_ids"][0], bars=_frame(rows),
        initial_cash=10000.0, commission_rate=0.001, slippage_rate=0.0005, config=config,
    )

    replay = OpeningRangeForwardReplay(
        symbol=spec["asset_ids"][0], config=config,
        initial_cash=10000.0, commission_rate=0.001, slippage_rate=0.0005,
    )
    rp_txns = replay.run(_frame(rows))

    # The very same config, read through the very same parser and state machine,
    # yields identical fills on every economic field — so the engine cannot
    # honour the spec in one path and ignore it in the other. transaction_id is a
    # per-fill random UUID by design, so it is the one field excluded.
    def _sans_id(txns):
        return [{k: v for k, v in t.items() if k != "transaction_id"} for t in txns]

    assert _sans_id(rp_txns) == _sans_id(bt_txns)
    assert replay.warnings == bt_warnings
    assert any(t["exit_reason"] == intraday.exit_rules.TARGET for t in bt_txns)
