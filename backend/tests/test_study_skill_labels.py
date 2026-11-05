"""Reading ``strategy_study_trades`` back as skill-selection labels.

The research lane wrote per-trade rows that nothing read, so the studies could
not feed a decision. ``app.services.study_skill_labels`` is that reader, and
these tests pin the part that matters: it refuses instead of dressing up a thin,
contaminated or unstable answer, and an empty ranking is never a pass.

The exclusions are the load-bearing ones:

* a run whose ``data_nature`` is not ``live`` is out unless a caller explicitly
  asks for non-live rows (a development harness reading a synthetic run);
* ``dataset_integrity_ok`` has to be ``True`` — a series with gaps or duplicate
  rows is a broken measuring stick;
* the walk-forward block has to name ``test`` folds, and every stored trade has
  to belong to one of them (in-sample cells cannot be pooled into a label);
* the trade rows have to match ``trade_count`` — a short read is not a sample.

Everything returned is a proposal with ``auto_apply`` false and, for the best
cell, ``requires_owner_decision`` true. Nothing here selects or locks a strategy
version.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.conftest import TestSession
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.models.user import User
from app.services import study_skill_labels as labels

INSTRUMENT = "KRAKEN:BTC-USD"
INTERVAL = 1440
ANCHOR = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _new_user(db) -> uuid.UUID:
    user = User(
        id=uuid.uuid4(),
        email=f"label-{uuid.uuid4().hex[:10]}@example.test",
        password_hash="test-only",
        display_name="label test",
    )
    db.add(user)
    db.flush()
    return user.id


def _trade(db, run, *, index, fold, rule="sma_crossover", exit_rule="fixed_exit",
           net="10", cost="2", outcome="win"):
    trade = StrategyStudyTrade(
        run_id=run.id,
        trade_index=index,
        fold=fold,
        rule_id=rule,
        rule_label_zh="均線交叉",
        exit_id=exit_rule,
        exit_label_zh="固定停損",
        direction="long",
        exit_reason="signal_flip",
        outcome=outcome,
        signal_time=ANCHOR + timedelta(days=index),
        entry_time=ANCHOR + timedelta(days=index),
        exit_time=ANCHOR + timedelta(days=index + 1),
        entry_price="50000",
        exit_price="50100",
        quantity="1",
        gross_pnl=str(Decimal(net) + Decimal(cost)),
        commission=str(Decimal(cost) / 2),
        slippage_cost=str(Decimal(cost) / 2),
        total_cost=cost,
        net_pnl=net,
        cash_after="1000000",
        bars_held=1,
        pnl_reason_zh="測試用",
        pnl_reason_en="test fixture",
    )
    db.add(trade)
    return trade


def _run(db, user_id, *, trades, data_nature="live", integrity=True,
         test_folds=(1, 2, 3), instrument=INSTRUMENT, interval=INTERVAL,
         cost_rate=0.002, trade_count=None, lane=labels.LANE):
    """A stored study: ``trades`` is ``[(fold, rule, exit, net, cost, outcome)]``."""
    run = StrategyStudyRun(
        id=uuid.uuid4(),
        user_id=user_id,
        lane=lane,
        research_version="live3-test",
        instrument_id=instrument,
        interval_minutes=interval,
        provider_id="kraken",
        data_nature=data_nature,
        bar_count=720,
        first_open_time=ANCHOR,
        last_open_time=ANCHOR + timedelta(days=720),
        fetched_at=ANCHOR,
        dataset_integrity_ok=integrity,
        dataset_gap_count=0,
        settings_json={"round_trip_cost_rate": cost_rate,
                       "initial_cash": 1_000_000},
        walk_forward_json={
            "folds": [
                {"fold": fold, "role": "test"} for fold in test_folds
            ] + [{"fold": 0, "role": "train"}]
        },
        bars_json=[],
        trade_count=len(trades) if trade_count is None else trade_count,
    )
    db.add(run)
    db.flush()
    for index, (fold, rule, exit_rule, net, cost, outcome) in enumerate(trades):
        _trade(db, run, index=index, fold=fold, rule=rule, exit_rule=exit_rule,
               net=net, cost=cost, outcome=outcome)
    db.commit()
    db.refresh(run)
    return run


def _specs(count, fold, *, net="10", cost="2", rule="sma_crossover",
           exit_rule="fixed_exit", start=0):
    """``count`` trades in one fold as ``(fold, rule, exit, net, cost, outcome)``."""
    return [
        (fold, rule, exit_rule, net, cost, "win" if Decimal(net) > 0 else "loss")
        for _ in range(start, start + count)
    ]


@pytest.fixture
def owner_id():
    with TestSession() as db:
        yield _new_user(db)


# ---------------------------------------------------------------------------
# What gets excluded, and the count that says so
# ---------------------------------------------------------------------------


class TestExclusions:

    def test_a_synthetic_run_is_not_a_label(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(10, 1), data_nature="synthetic")

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_considered"] == 1
            assert result["runs_included"] == 0
            assert result["runs_excluded"] == {"data_nature_not_live": 1}
            assert result["cells"] == []
            assert result["auto_apply"] is False

    def test_an_explicit_non_live_read_is_loud_about_the_denominator(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(10, 1), data_nature="fixture")

            result = labels.skill_labels(db, user_id=owner_id, allow_non_live=True)

            assert result["runs_included"] == 1
            assert result["runs_excluded"] == {}
            assert result["cells"][0]["out_of_sample_trades"] == 10

    @pytest.mark.parametrize("integrity", [False, None])
    def test_a_broken_series_is_excluded(self, owner_id, integrity):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(10, 1), integrity=integrity)

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_excluded"] == {"dataset_integrity_not_true": 1}
            assert result["cells"] == []

    def test_a_trade_in_a_non_test_fold_disqualifies_the_whole_run(self, owner_id):
        with TestSession() as db:
            # fold 9 is not in this run's walk-forward test set.
            _run(db, owner_id, trades=_specs(5, 1) + _specs(5, 9, start=5))

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_excluded"] == {"trade_outside_test_folds": 1}
            assert result["cells"] == []

    def test_a_run_without_test_folds_is_excluded(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(5, 1), test_folds=())

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_excluded"] == {"no_test_folds_in_walk_forward": 1}

    def test_missing_trade_rows_are_not_a_zero_expectancy_label(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=[], test_folds=(1,))
            _run(db, owner_id, trades=_specs(4, 1), trade_count=9)

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_excluded"] == {
                "no_out_of_sample_trades": 1,
                "trade_rows_do_not_match_trade_count": 1,
            }
            assert result["cells"] == []

    def test_another_owner_runs_are_not_read(self, owner_id):
        with TestSession() as db:
            stranger = _new_user(db)
            _run(db, stranger, trades=_specs(10, 1))

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_considered"] == 0

    def test_other_lanes_are_not_read(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(10, 1),
                 lane="some_other_research_lane")

            result = labels.skill_labels(db, user_id=owner_id)

            assert result["runs_considered"] == 0


# ---------------------------------------------------------------------------
# The ranking itself
# ---------------------------------------------------------------------------


class TestRanking:

    def test_costs_keep_cells_apart(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(6, 1), cost_rate=0.002)
            _run(db, owner_id, trades=_specs(6, 1), cost_rate=0.0006)

            result = labels.skill_labels(db, user_id=owner_id)

            bands = {cell["round_trip_cost_rate"] for cell in result["cells"]}
            assert bands == {"0.0006", "0.002"}, (
                "a 0.06% cell and a 0.2% cell are different measuring sticks"
            )
            assert all(cell["out_of_sample_trades"] == 6 for cell in result["cells"])

    def test_fold_expectancy_and_win_rate_are_computed_per_cell(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=(
                _specs(2, 1, net="100", cost="2")
                + _specs(2, 2, net="-50", cost="2", start=2)
                + _specs(2, 3, net="10", cost="2", start=4)
            ))

            cell = labels.skill_labels(db, user_id=owner_id)["cells"][0]

            assert cell["out_of_sample_trades"] == 6
            assert cell["tested_folds"] == 3
            assert cell["positive_folds"] == 2
            # Signs run +, -, + across the three test folds.
            assert cell["fold_sign_flips"] == 2
            assert Decimal(cell["expectancy_per_trade"]) == Decimal("20")
            assert cell["fold_expectancy"] == {"1": "100.00000000",
                                               "2": "-50.00000000",
                                               "3": "10.00000000"}
            assert Decimal(cell["win_rate"]) == Decimal(4) / Decimal(6)
            assert Decimal(cell["total_cost"]) == Decimal("12")
            assert cell["rule_label_zh"] == "均線交叉"

    def test_the_best_expected_cell_sorts_first(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(10, 1, net="1", rule="rsi_reversal"))
            _run(db, owner_id, trades=_specs(10, 1, net="40", rule="sma_crossover")
                 + _specs(10, 2, net="40", rule="sma_crossover", start=10))

            cells = labels.skill_labels(db, user_id=owner_id)["cells"]

            assert [cell["rule_id"] for cell in cells] == [
                "sma_crossover", "rsi_reversal"
            ]
            assert Decimal(cells[0]["expectancy_per_trade"]) == Decimal("40")
            assert cells[0]["run_count"] == 1
            assert cells[1]["out_of_sample_trades"] == 10

    def test_trades_from_two_runs_of_the_same_cell_are_pooled(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(4, 1))
            _run(db, owner_id, trades=_specs(4, 2))

            cells = labels.skill_labels(db, user_id=owner_id)["cells"]

            assert len(cells) == 1
            assert cells[0]["out_of_sample_trades"] == 8
            assert cells[0]["run_count"] == 2
            assert len(cells[0]["run_ids"]) == 2

    def test_interval_and_instrument_split_the_cells(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(4, 1))
            _run(db, owner_id, trades=_specs(4, 1), interval=240)
            _run(db, owner_id, trades=_specs(4, 1), instrument="KRAKEN:ETH-USD")

            cells = labels.skill_labels(db, user_id=owner_id)["cells"]

            assert len(cells) == 3
            single = labels.skill_labels(db, user_id=owner_id, interval_minutes=240)
            assert [c["interval_minutes"] for c in single["cells"]] == [240]


# ---------------------------------------------------------------------------
# Proposals: what it will not offer
# ---------------------------------------------------------------------------


class TestProposals:

    def test_no_qualifying_run_refuses_instead_of_passing(self, owner_id):
        with TestSession() as db:
            result = labels.propose_skill(db, user_id=owner_id, min_trades=1)

            assert result["cells"] == []
            assert result["proposal"] is None
            assert result["refusal"] == "no_runs"
            assert result["auto_apply"] is False

    def test_a_thin_cell_is_refused(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(3, 1))

            result = labels.propose_skill(db, user_id=owner_id, min_trades=30)

            assert result["proposal"] is None
            assert result["refusal"] == "no_cell_with_enough_trades"

    def test_a_negative_cell_is_not_a_proposal(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(40, 1, net="-5", cost="2"))

            result = labels.propose_skill(db, user_id=owner_id, min_trades=30)

            assert result["proposal"] is None
            assert result["refusal"] == "no_cell_with_enough_trades"

    def test_a_tie_is_refused_rather_than_broken_arbitrarily(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=_specs(30, 1, net="10", rule="alpha"))
            _run(db, owner_id, trades=_specs(30, 1, net="10", rule="beta"))

            result = labels.propose_skill(db, user_id=owner_id, min_trades=30)

            assert result["proposal"] is None
            assert result["refusal"] == "tie_for_best"
            assert sorted(name for name, _ in result["tied_cells"]) == ["alpha", "beta"]

    def test_a_proposal_stays_inert_and_names_its_thresholds(self, owner_id):
        with TestSession() as db:
            _run(db, owner_id, trades=(
                _specs(12, 1, net="30") + _specs(12, 2, net="30", start=12)
                + _specs(12, 3, net="30", start=24)
            ))

            result = labels.propose_skill(
                db, user_id=owner_id, min_trades=30, min_positive_folds=3,
                max_fold_flips=2,
            )

            proposal = result["proposal"]
            assert proposal is not None
            assert proposal["auto_apply"] is False
            assert proposal["requires_owner_decision"] is True
            assert proposal["stability_flag"] == "within_bounds"
            assert proposal["criteria"] == {"min_trades": 30,
                                            "min_positive_folds": 3,
                                            "max_fold_flips": 2}
            assert proposal["positive_folds"] == 3
            assert proposal["out_of_sample_trades"] == 36
            assert result["refusal"] is None
            assert result["auto_apply"] is False

    def test_an_unstable_best_cell_is_flagged_not_hidden(self, owner_id):
        with TestSession() as db:
            # 4 test folds alternating in sign: expectancy is positive overall,
            # but the path is not.
            _run(db, owner_id, test_folds=(1, 2, 3, 4), trades=(
                _specs(10, 1, net="100")
                + _specs(10, 2, net="-80", start=10)
                + _specs(10, 3, net="100", start=20)
                + _specs(10, 4, net="-80", start=30)
            ))

            result = labels.propose_skill(
                db, user_id=owner_id, min_trades=30, min_positive_folds=3,
                max_fold_flips=2,
            )

            assert result["refusal"] == "unstable_across_folds"
            assert result["proposal"]["stability_flag"] == "unstable_across_folds"
            assert result["proposal"]["auto_apply"] is False
            assert result["proposal"]["fold_sign_flips"] == 3

    def test_the_refusal_messages_are_part_of_the_module(self):
        for code in ("no_runs", "no_cell_with_enough_trades",
                     "unstable_across_folds", "tie_for_best"):
            assert code in labels.REFUSALS
        assert labels.QUALIFYING_DATA_NATURE == ("live",)
