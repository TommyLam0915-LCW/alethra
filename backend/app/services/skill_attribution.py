"""Skill attribution analytics for the trading journal.

Aggregates trades by rule_id (skill) and computes per-skill performance
metrics: total P&L, win rate, Sharpe ratio, max drawdown, profit factor.
Also provides cross-tabulation by instrument and interval, plus a heatmap
matrix for visualization.

Read-only service built on top of the journal's trade data.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.services.journal_service import _decimal, _money, _rule_family
from app.services.strategy_research import RULE_BY_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trades_per_year(interval_minutes: Optional[int]) -> int:
    """Annualized trade frequency factor for Sharpe ratio.

    daily (1440) -> 252
    4h (240)     -> 252 * 6
    1h (60)      -> 252 * 24
    15m (15)     -> 252 * 96
    """
    if interval_minutes is None or interval_minutes <= 0:
        return 252
    bars_per_day = 1440 // interval_minutes
    if bars_per_day <= 0:
        return 252
    return 252 * bars_per_day


def _sharpe_ratio(pnls: List[Decimal], trades_per_year: int) -> Optional[Decimal]:
    """Compute annualized Sharpe ratio: mean / stdev * sqrt(trades_per_year).

    Returns None when stdev is zero (single trade or all identical returns).
    """
    n = len(pnls)
    if n < 2:
        return None
    mean = sum(pnls, Decimal("0")) / Decimal(str(n))
    variance = sum((p - mean) ** 2 for p in pnls) / Decimal(str(n))
    if variance <= 0:
        return None
    stdev = variance.sqrt()
    if stdev == 0:
        return None
    return mean / stdev * Decimal(str(math.sqrt(trades_per_year)))


def _max_drawdown_pct(pnls: List[Decimal]) -> Decimal:
    """Max drawdown as a percentage of peak equity (starting from 0)."""
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * Decimal("100")
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _profit_factor(wins: List[Decimal], losses: List[Decimal]) -> Optional[Decimal]:
    """Sum of wins / abs(sum of losses). None when no losses."""
    gross_wins = sum(wins, Decimal("0")) if wins else Decimal("0")
    gross_losses = abs(sum(losses, Decimal("0"))) if losses else Decimal("0")
    if gross_losses == 0:
        return None
    return gross_wins / gross_losses


def _compute_group_metrics(trades: List[dict], interval_minutes: Optional[int] = None) -> dict:
    """Compute all metrics for a group of trade dicts."""
    total = len(trades)
    if total == 0:
        return {
            "total_pnl": "0",
            "trade_count": 0,
            "win_count": 0,
            "win_rate": "0",
            "avg_trade_pnl": "0",
            "expectancy": "0",
            "sharpe_ratio": None,
            "max_drawdown_pct": "0",
            "profit_factor": None,
        }

    pnls = [_decimal(t["net_pnl"]) for t in trades]
    total_pnl = sum(pnls, Decimal("0"))
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count = len(wins)
    win_rate = Decimal(str(win_count)) / Decimal(str(total)) * Decimal("100")
    avg_trade_pnl = total_pnl / Decimal(str(total))
    expectancy = avg_trade_pnl

    tpy = _trades_per_year(interval_minutes)
    sharpe = _sharpe_ratio(pnls, tpy)
    max_dd = _max_drawdown_pct(pnls)
    pf = _profit_factor(wins, losses)

    return {
        "total_pnl": _money(total_pnl),
        "trade_count": total,
        "win_count": win_count,
        "win_rate": _money(win_rate.quantize(Decimal("0.01"))),
        "avg_trade_pnl": _money(avg_trade_pnl.quantize(Decimal("0.00000001"))),
        "expectancy": _money(expectancy.quantize(Decimal("0.00000001"))),
        "sharpe_ratio": _money(sharpe.quantize(Decimal("0.0001"))) if sharpe is not None else None,
        "max_drawdown_pct": _money(max_dd.quantize(Decimal("0.01"))),
        "profit_factor": _money(pf.quantize(Decimal("0.01"))) if pf is not None else None,
    }


# ---------------------------------------------------------------------------
# Internal: fetch all trades with run metadata (single JOIN)
# ---------------------------------------------------------------------------

def _fetch_all_trades(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[dict]:
    """Fetch all filtered trades with run metadata in a single query."""
    T = StrategyStudyTrade
    R = StrategyStudyRun

    stmt = (
        select(
            T.rule_id,
            T.rule_label_zh,
            T.net_pnl,
            T.outcome,
            R.instrument_id.label("run_instrument_id"),
            R.interval_minutes.label("run_interval_minutes"),
        )
        .join(R, T.run_id == R.id)
        .where(R.user_id == user_id)
    )

    if instrument_id is not None:
        stmt = stmt.where(R.instrument_id == instrument_id)
    if interval_minutes is not None:
        stmt = stmt.where(R.interval_minutes == interval_minutes)
    if date_from is not None:
        stmt = stmt.where(T.entry_time >= date_from)
    if date_to is not None:
        stmt = stmt.where(T.entry_time <= date_to)

    stmt = stmt.order_by(T.entry_time.asc())

    rows = db.execute(stmt).all()
    return [
        {
            "rule_id": row.rule_id,
            "rule_label_zh": row.rule_label_zh,
            "net_pnl": row.net_pnl,
            "outcome": row.outcome,
            "instrument_id": row.run_instrument_id,
            "interval_minutes": row.run_interval_minutes,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def skill_attribution(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Aggregate trades by rule_id (skill). For each skill return:
    - rule_id, rule_label_zh, rule_family
    - total_pnl, trade_count, win_count, win_rate
    - avg_trade_pnl, expectancy
    - sharpe_ratio (annualized)
    - max_drawdown_pct
    - profit_factor

    Also return cross-tabulation:
    - cross_instrument: [{ rule_id, instrument_id, total_pnl, trade_count, win_rate }]
    - cross_interval: [{ rule_id, interval_minutes, total_pnl, trade_count, win_rate }]
    """
    trades = _fetch_all_trades(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
    )

    # Group by rule_id
    by_rule: Dict[str, List[dict]] = defaultdict(list)
    for t in trades:
        by_rule[t["rule_id"]].append(t)

    # Compute per-skill metrics
    skills = []
    for rule_id, rule_trades in by_rule.items():
        rule_obj = RULE_BY_ID.get(rule_id)
        label_zh = rule_obj.label_zh if rule_obj else (rule_trades[0]["rule_label_zh"] or rule_id)
        family = rule_obj.family if rule_obj else _rule_family(rule_id)

        metrics = _compute_group_metrics(rule_trades, interval_minutes)
        skills.append({
            "rule_id": rule_id,
            "rule_label_zh": label_zh,
            "rule_family": family,
            **metrics,
        })

    # Sort by total_pnl DESC
    skills.sort(key=lambda s: Decimal(s["total_pnl"]), reverse=True)

    # Cross-tabulation: by (rule_id, instrument_id)
    by_rule_instrument: Dict[tuple, List[dict]] = defaultdict(list)
    for t in trades:
        by_rule_instrument[(t["rule_id"], t["instrument_id"])].append(t)

    cross_instrument = []
    for (rule_id, inst_id), group in by_rule_instrument.items():
        pnls = [_decimal(t["net_pnl"]) for t in group]
        total_pnl = sum(pnls, Decimal("0"))
        total_count = len(group)
        win_count = sum(1 for p in pnls if p > 0)
        win_rate = Decimal(str(win_count)) / Decimal(str(total_count)) * Decimal("100") if total_count > 0 else Decimal("0")
        cross_instrument.append({
            "rule_id": rule_id,
            "instrument_id": inst_id,
            "total_pnl": _money(total_pnl),
            "trade_count": total_count,
            "win_rate": _money(win_rate.quantize(Decimal("0.01"))),
        })

    # Cross-tabulation: by (rule_id, interval_minutes)
    by_rule_interval: Dict[tuple, List[dict]] = defaultdict(list)
    for t in trades:
        by_rule_interval[(t["rule_id"], t["interval_minutes"])].append(t)

    cross_interval = []
    for (rule_id, intv), group in by_rule_interval.items():
        pnls = [_decimal(t["net_pnl"]) for t in group]
        total_pnl = sum(pnls, Decimal("0"))
        total_count = len(group)
        win_count = sum(1 for p in pnls if p > 0)
        win_rate = Decimal(str(win_count)) / Decimal(str(total_count)) * Decimal("100") if total_count > 0 else Decimal("0")
        cross_interval.append({
            "rule_id": rule_id,
            "interval_minutes": intv,
            "total_pnl": _money(total_pnl),
            "trade_count": total_count,
            "win_rate": _money(win_rate.quantize(Decimal("0.01"))),
        })

    return {
        "skills": skills,
        "cross_instrument": cross_instrument,
        "cross_interval": cross_interval,
    }


def skill_heatmap_data(
    db: Session,
    *,
    user_id: UUID,
    metric: str = "net_pnl",
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """Returns { rows: [rule_id...], cols: [instrument_id...], cells: [[value...]] }
    for rendering a heatmap visualization.

    Each cell is the metric value for that (skill, instrument) combination.
    Rows are skills sorted by total_pnl DESC.
    Columns are instruments sorted alphabetically.

    Supported metrics: net_pnl, win_rate, sharpe, trade_count
    """
    trades = _fetch_all_trades(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
    )

    if not trades:
        return {"rows": [], "cols": [], "cells": []}

    # Group by (rule_id, instrument_id)
    by_key: Dict[tuple, List[dict]] = defaultdict(list)
    for t in trades:
        by_key[(t["rule_id"], t["instrument_id"])].append(t)

    # Determine all rule_ids and instrument_ids
    all_rule_ids = sorted({t["rule_id"] for t in trades})
    all_instruments = sorted({t["instrument_id"] for t in trades})

    # Compute total_pnl per rule_id for sorting rows
    rule_total_pnl: Dict[str, Decimal] = {}
    for rule_id in all_rule_ids:
        rule_trades = [t for t in trades if t["rule_id"] == rule_id]
        pnls = [_decimal(t["net_pnl"]) for t in rule_trades]
        rule_total_pnl[rule_id] = sum(pnls, Decimal("0"))

    # Sort rows by total_pnl DESC
    rows = sorted(all_rule_ids, key=lambda r: rule_total_pnl[r], reverse=True)
    cols = all_instruments

    # Build cells matrix
    cells = []
    for rule_id in rows:
        row_cells = []
        for inst_id in cols:
            group = by_key.get((rule_id, inst_id), [])
            value = _compute_cell_metric(group, metric)
            row_cells.append(value)
        cells.append(row_cells)

    return {
        "rows": rows,
        "cols": cols,
        "cells": cells,
    }


def _compute_cell_metric(trades: List[dict], metric: str) -> Optional[str]:
    """Compute a single metric value for a cell (skill x instrument)."""
    if not trades:
        return None

    pnls = [_decimal(t["net_pnl"]) for t in trades]
    total_count = len(trades)

    if metric == "net_pnl":
        total = sum(pnls, Decimal("0"))
        return _money(total)

    elif metric == "win_rate":
        wins = sum(1 for p in pnls if p > 0)
        rate = Decimal(str(wins)) / Decimal(str(total_count)) * Decimal("100")
        return _money(rate.quantize(Decimal("0.01")))

    elif metric == "sharpe":
        sharpe = _sharpe_ratio(pnls, 252)  # default annualization
        if sharpe is None:
            return None
        return _money(sharpe.quantize(Decimal("0.0001")))

    elif metric == "trade_count":
        return str(total_count)

    else:
        # Fallback: net_pnl
        total = sum(pnls, Decimal("0"))
        return _money(total)
