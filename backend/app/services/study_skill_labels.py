"""Read the stored per-trade study records back as skill-selection labels.

``strategy_study_trades`` was written by the research lane and read by nobody:
the studies accumulated evidence rows that no consumer could turn into a
decision. This module is that consumer. It answers one question — for this
instrument and grain, which rule-and-exit cell did well *out of sample, after
costs* — and it refuses rather than dress up a thin or unstable answer.

What it will not do:

* treat a simulated or fixture run as evidence: unless ``allow_non_live`` is
  passed, a run whose ``data_nature`` is not ``live`` is excluded and counted;
* read a run whose dataset integrity flag is false or missing — a series with
  gaps, off-grid rows or duplicates is a broken measuring stick, not a sample;
* pool in-sample grid cells: ``persist_study`` only ever stores the
  out-of-sample trades, and a trade whose fold is not a ``test`` fold in the
  run's own walk-forward block disqualifies the run;
* report an empty ranking as a pass: with no qualifying run the result carries a
  ``refusal`` code and ``cells == []``, and callers must read the code.

The output is a ranking with its denominator attached. It is a proposal to the
owner, never an applied change: nothing in here selects a strategy version, and
``auto_apply`` is false in every return.
"""

import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy_study import StrategyStudyRun

LANE = "strategy_research_simulated"
QUALIFYING_DATA_NATURE = ("live",)

REFUSALS = {
    "no_runs": "沒有符合條件的研究紀錄可供讀取。",
    "no_cell_with_enough_trades": "沒有任何細胞的樣本外筆數達到門檻。",
    "unstable_across_folds": "最佳細胞的折間符號翻轉超過上限。",
    "tie_for_best": "有兩個以上細胞在樣本外期望值上並列第一，拒絕自動選擇。",
}


def _test_folds(run: StrategyStudyRun) -> set:
    block = run.walk_forward_json or {}
    folds = block.get("folds") or []
    return {fold.get("fold") for fold in folds if fold.get("role") == "test"}


def _candidate_runs(
    db: Session,
    *,
    user_id: Optional[uuid.UUID],
    instrument_id: Optional[str],
    interval_minutes: Optional[int],
    limit: int,
) -> List[StrategyStudyRun]:
    statement = select(StrategyStudyRun).where(StrategyStudyRun.lane == LANE)
    if user_id is not None:
        statement = statement.where(StrategyStudyRun.user_id == user_id)
    if instrument_id:
        statement = statement.where(StrategyStudyRun.instrument_id == instrument_id)
    if interval_minutes is not None:
        statement = statement.where(StrategyStudyRun.interval_minutes == interval_minutes)
    statement = statement.order_by(
        StrategyStudyRun.created_at.desc(), StrategyStudyRun.id.desc()
    ).limit(limit)
    return list(db.scalars(statement).all())


def skill_labels(
    db: Session,
    *,
    user_id: Optional[uuid.UUID] = None,
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    allow_non_live: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """Turn stored study trades into per-cell out-of-sample label blocks."""
    runs = _candidate_runs(db, user_id=user_id, instrument_id=instrument_id,
                           interval_minutes=interval_minutes, limit=limit)
    excluded: Dict[str, int] = defaultdict(int)
    usable: List[StrategyStudyRun] = []
    for run in runs:
        if not allow_non_live and run.data_nature not in QUALIFYING_DATA_NATURE:
            excluded["data_nature_not_live"] += 1
            continue
        if run.dataset_integrity_ok is not True:
            excluded["dataset_integrity_not_true"] += 1
            continue
        test_folds = _test_folds(run)
        if not test_folds:
            excluded["no_test_folds_in_walk_forward"] += 1
            continue
        if run.trade_count != len(run.trades):
            excluded["trade_rows_do_not_match_trade_count"] += 1
            continue
        if not run.trades:
            excluded["no_out_of_sample_trades"] += 1
            continue
        if any(trade.fold not in test_folds for trade in run.trades):
            excluded["trade_outside_test_folds"] += 1
            continue
        usable.append(run)

    cells: Dict[tuple, Dict[str, Any]] = {}
    for run in usable:
        settings = run.settings_json or {}
        band = str(settings.get("round_trip_cost_rate"))
        for trade in run.trades:
            key = (run.instrument_id, run.interval_minutes, trade.rule_id,
                   trade.exit_id, band)
            cell = cells.setdefault(key, {
                "instrument_id": run.instrument_id,
                "interval_minutes": run.interval_minutes,
                "rule_id": trade.rule_id,
                "rule_label_zh": trade.rule_label_zh,
                "exit_id": trade.exit_id,
                "exit_label_zh": trade.exit_label_zh,
                "round_trip_cost_rate": band,
                "run_ids": set(),
                "folds": defaultdict(lambda: {"trades": 0, "net_pnl": Decimal("0")}),
                "trades": 0,
                "wins": 0,
                "net_pnl": Decimal("0"),
                "total_cost": Decimal("0"),
            })
            cell["run_ids"].add(str(run.id))
            if cell["rule_label_zh"] is None:
                cell["rule_label_zh"] = trade.rule_label_zh
            if cell["exit_label_zh"] is None:
                cell["exit_label_zh"] = trade.exit_label_zh
            fold = cell["folds"][trade.fold]
            fold["trades"] += 1
            fold["net_pnl"] += trade.net_pnl
            cell["trades"] += 1
            cell["net_pnl"] += trade.net_pnl
            cell["total_cost"] += trade.total_cost
            if trade.outcome == "win":
                cell["wins"] += 1

    ranked: List[Dict[str, Any]] = []
    for cell in cells.values():
        fold_expectancies = {
            fold: (block["net_pnl"] / Decimal(block["trades"]))
            for fold, block in sorted(cell["folds"].items())
        }
        signs = [1 if value > 0 else -1 if value < 0 else 0
                 for value in fold_expectancies.values()]
        flips = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
        expectancy = (cell["net_pnl"] / Decimal(cell["trades"])) if cell["trades"] else None
        ranked.append({
            **{k: v for k, v in cell.items() if k != "folds"},
            "run_count": len(cell["run_ids"]),
            "run_ids": sorted(cell["run_ids"]),
            "tested_folds": len(fold_expectancies),
            "positive_folds": sum(1 for value in fold_expectancies.values() if value > 0),
            "fold_expectancy": {str(f): str(v) for f, v in fold_expectancies.items()},
            "fold_sign_flips": flips,
            "out_of_sample_trades": cell["trades"],
            "win_rate": (Decimal(cell["wins"]) / Decimal(cell["trades"]))
            if cell["trades"] else None,
            "expectancy_per_trade": str(expectancy) if expectancy is not None else None,
            "net_pnl": str(cell["net_pnl"]),
            "total_cost": str(cell["total_cost"]),
        })

    ranked.sort(key=lambda row: (
        -Decimal(row["expectancy_per_trade"]) if row["expectancy_per_trade"] else 0,
        row["fold_sign_flips"],
        -row["out_of_sample_trades"],
        row["rule_id"], row["exit_id"],
    ))
    return {
        "runs_considered": len(runs),
        "runs_included": len(usable),
        "runs_excluded": dict(excluded),
        "cells": ranked,
        "auto_apply": False,
    }


def propose_skill(
    db: Session,
    *,
    user_id: uuid.UUID,
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    min_trades: int = 30,
    min_positive_folds: int = 3,
    max_fold_flips: int = 2,
) -> Dict[str, Any]:
    """Rank the recorded labels and, only if they hold up, name a best cell.

    The thresholds are stated here so a caller can see what a proposal cost; the
    proposal itself is inert. Choosing a strategy version is the owner's action.
    """
    labels = skill_labels(db, user_id=user_id, instrument_id=instrument_id,
                          interval_minutes=interval_minutes)
    passing = [
        cell for cell in labels["cells"]
        if cell["out_of_sample_trades"] >= min_trades
        and cell["expectancy_per_trade"] is not None
        and Decimal(cell["expectancy_per_trade"]) > 0
    ]
    if not labels["cells"]:
        return {**labels, "proposal": None, "refusal": "no_runs",
                "refusal_zh": REFUSALS["no_runs"],
                "criteria": {"min_trades": min_trades,
                             "min_positive_folds": min_positive_folds,
                             "max_fold_flips": max_fold_flips}}
    if not passing:
        return {**labels, "proposal": None,
                "refusal": "no_cell_with_enough_trades",
                "refusal_zh": REFUSALS["no_cell_with_enough_trades"],
                "criteria": {"min_trades": min_trades}}

    best = passing[0]
    tied = [cell for cell in passing[1:]
            if Decimal(cell["expectancy_per_trade"]) == Decimal(best["expectancy_per_trade"])]
    if tied:
        return {**labels, "proposal": None, "refusal": "tie_for_best",
                "refusal_zh": REFUSALS["tie_for_best"],
                "tied_cells": [[cell["rule_id"], cell["exit_id"]] for cell in [best] + tied]}

    instability = (best["fold_sign_flips"] > max_fold_flips
                   or best["positive_folds"] < min_positive_folds)
    proposal = {
        **best,
        "auto_apply": False,
        "requires_owner_decision": True,
        "stability_flag": "unstable_across_folds" if instability else "within_bounds",
        "criteria": {"min_trades": min_trades,
                     "min_positive_folds": min_positive_folds,
                     "max_fold_flips": max_fold_flips},
    }
    return {**labels, "proposal": proposal,
            "refusal": "unstable_across_folds" if instability else None,
            "refusal_zh": REFUSALS["unstable_across_folds"] if instability else None}
