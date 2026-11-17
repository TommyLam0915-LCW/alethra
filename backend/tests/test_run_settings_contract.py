"""Contract: a proposal replay is priced with the costs the recorded run actually used.

`validate_proposal` reconstructs `ResearchSettings` from `StrategyStudyRun.settings_json`
because the baseline trades it compares against were simulated with those numbers. The
reader used to ask for keys the writer does not emit (`commission_rate`, `slippage_rate`,
while `strategy_research` records `*_per_side`) and fall back to literals (`"10000"`,
`"1000"`, `"0.001"`, `"2.0"`, `14`), so the proposed side was priced with money the
database never wrote down — and a cost difference could be reported as a P&L difference.

These nodes pin the three states that matter:
  * recorded costs reach the replay, under the names the writer emits;
  * the legacy spelling still works, because rows written before the rename exist;
  * nothing recorded at all is refused, rather than defaulted.

The assertions stop at the `ResearchSettings` boundary: what the replay then does with
those numbers is `test_recommendation_logic_repairs.py`'s subject.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.pattern_analysis import WARMUP_BARS, validate_proposal

RULE_ID = "breakout_trend"
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"


def _run(settings_json, *, trade_count=10):
    """A stored run with bars and baseline trades; only settings_json varies per test."""
    trades = []
    for i in range(trade_count):
        trade = MagicMock()
        trade.rule_id = RULE_ID
        trade.exit_id = "exit_1"
        trade.net_pnl = float(10 + i)
        trade.cash_after = float(50000 + 10 * (i + 1))
        trades.append(trade)
    run = MagicMock()
    run.id = uuid.uuid4()
    run.bars_json = [
        {
            "open_time": "2024-01-01T00:00:00",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 1000,
        }
    ] * (WARMUP_BARS + 20)
    run.trades = trades
    run.settings_json = settings_json
    return run


def _proposal():
    return {
        "instrument_id": INSTRUMENT,
        "interval_minutes": 1440,
        "rule_id": RULE_ID,
        "baseline_exit_config": {"exit_id": "exit_1"},
        "proposed_exit_config": {"target_atr": "2.0", "stop_atr": "1.5", "time_stop_bars": 6},
    }


def _settings_kwargs(run):
    """Call validate_proposal far enough to build ResearchSettings, and return its kwargs.

    Everything downstream of the rebuild is stubbed with the same recipe
    `test_recommendation_logic_repairs.py` uses: the numbers it returns are deliberately
    irrelevant, because these assertions are about what the rebuild was handed.
    """
    db = MagicMock()
    db.scalars.return_value.first.return_value = run
    with patch("app.services.pattern_analysis.ResearchSettings") as settings_class:
        settings_class.return_value = MagicMock(
            starting_cash=Decimal("50000"),
            notional_per_trade=Decimal("5000"),
            commission_rate=Decimal("0.002"),
            slippage_rate=Decimal("0.002"),
            atr_window=20,
            cost_multiple=Decimal("3.0"),
        )
        with patch("app.services.pattern_analysis.prepare_indicators", return_value={}):
            with patch("app.services.pattern_analysis.fold_bounds", return_value=[]):
                with patch(
                    "app.services.pattern_analysis.summarize",
                    return_value={"win_rate": 0.5, "total_pnl": Decimal("-80"),
                                  "max_drawdown_pct": Decimal("0"), "sharpe": Decimal("0")},
                ):
                    with patch(
                        "app.services.pattern_analysis._compute_sharpe_from_research_trades",
                        return_value=Decimal("1.0"),
                    ):
                        with patch(
                            "app.services.pattern_analysis._compute_max_dd_from_research_trades",
                            return_value=Decimal("100"),
                        ):
                            with patch(
                                "app.services.pattern_analysis._compute_sharpe",
                                return_value=Decimal("0.5"),
                            ):
                                with patch(
                                    "app.services.pattern_analysis._compute_max_drawdown",
                                    return_value=Decimal("200"),
                                ):
                                    validate_proposal(
                                        db, user_id=uuid.uuid4(), proposal=_proposal()
                                    )
        assert settings_class.called, "validate_proposal never reached the settings rebuild"
        return settings_class.call_args[1]


PER_SIDE = {
    "starting_cash": "100000",
    "notional_per_trade": "10000",
    "commission_rate_per_side": "0.002600",
    "slippage_rate_per_side": "0.000500",
    "atr_window": 14,
    "cost_multiple": "2.000000",
}
LEGACY = {
    "starting_cash": "50000",
    "notional_per_trade": "5000",
    "commission_rate": "0.002",
    "slippage_rate": "0.002",
    "atr_window": 20,
    "cost_multiple": "3.0",
}


def test_recorded_per_side_costs_reach_the_replay():
    """The names strategy_research writes are the ones the replay must read."""
    kwargs = _settings_kwargs(_run(dict(PER_SIDE)))
    assert str(kwargs["starting_cash"]) == "100000"
    assert str(kwargs["notional_per_trade"]) == "10000"
    assert str(kwargs["commission_rate"]) == "0.002600"
    assert str(kwargs["slippage_rate"]) == "0.000500"
    assert kwargs["atr_window"] == 14
    assert str(kwargs["cost_multiple"]) == "2.000000"


def test_slippage_is_not_silently_doubled_back_to_the_old_literal():
    """The defect in one line: a run recorded at 0.0005 used to be replayed at 0.001.

    0.001 is what the reader's `.get("slippage_rate", "0.001")` produced for every real
    run, because the recorded key is `slippage_rate_per_side`. Half the assumed cost,
    applied to whichever side of the comparison the run happened to be on.
    """
    kwargs = _settings_kwargs(_run(dict(PER_SIDE)))
    assert str(kwargs["slippage_rate"]) != "0.001"
    assert str(kwargs["slippage_rate"]) == "0.000500"


def test_legacy_key_spelling_is_still_read():
    """Rows written before the rename stay replayable — the fallback is load-bearing."""
    kwargs = _settings_kwargs(_run(dict(LEGACY)))
    assert str(kwargs["commission_rate"]) == "0.002"
    assert str(kwargs["slippage_rate"]) == "0.002"
    assert kwargs["atr_window"] == 20


def test_per_side_wins_when_both_spellings_are_present():
    """Ambiguity resolves toward the recorded per-side value, not the bare key."""
    both = dict(LEGACY)
    both.update(PER_SIDE)
    kwargs = _settings_kwargs(_run(both))
    assert str(kwargs["commission_rate"]) == "0.002600"
    assert str(kwargs["slippage_rate"]) == "0.000500"


def test_unrecorded_costs_are_refused_instead_of_defaulted():
    """An empty settings blob produces a refusal, and no ResearchSettings at all."""
    db = MagicMock()
    db.scalars.return_value.first.return_value = _run({})
    with patch("app.services.pattern_analysis.ResearchSettings") as settings_class:
        try:
            result = validate_proposal(db, user_id=uuid.uuid4(), proposal=_proposal())
        except TypeError as exc:
            pytest.fail(
                "the rebuild ran on an empty settings blob; downstream then received a "
                f"mocked setting instead of a recorded number: {exc}"
            )
    assert result["passed"] is False
    assert result["error"] == "unrecorded_run_settings"
    assert not settings_class.called, (
        "the rebuild ran anyway, which means the refusal still fabricates capital"
    )


def test_refusal_names_every_field_it_could_not_read():
    """The message is the fix's user-visible half: it says what is missing, not that
    something failed."""
    partial = {"starting_cash": "100000", "atr_window": 14}
    db = MagicMock()
    db.scalars.return_value.first.return_value = _run(dict(partial))
    result = validate_proposal(db, user_id=uuid.uuid4(), proposal=_proposal())
    assert result["error"] == "unrecorded_run_settings"
    for name in (
        "notional_per_trade",
        "commission_rate",
        "slippage_rate",
        "cost_multiple",
    ):
        assert name in result["message"], result["message"]
    assert "starting_cash" not in result["message"]
    assert "atr_window" not in result["message"]


def test_a_null_value_counts_as_unrecorded():
    """`{"slippage_rate_per_side": null}` is absence, not zero cost."""
    recorded = dict(PER_SIDE)
    recorded["slippage_rate_per_side"] = None
    db = MagicMock()
    db.scalars.return_value.first.return_value = _run(recorded)
    result = validate_proposal(db, user_id=uuid.uuid4(), proposal=_proposal())
    assert result["error"] == "unrecorded_run_settings"
    assert "slippage_rate" in result["message"]
