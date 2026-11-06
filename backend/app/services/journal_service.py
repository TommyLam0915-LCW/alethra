"""Unified trading journal service.

Reads across all strategy_study_runs + strategy_study_trades for one owner and
returns flat, enriched trade dicts plus aggregates (summary, cumulative P&L,
equity curve, filter options).

Nothing here writes; the journal is a read-only view on top of the durable
study records.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.services.strategy_research import RULE_BY_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value, "f")


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _rule_family(rule_id: str) -> str:
    """Map a rule_id to its family via RULE_BY_ID, falling back to 'unknown'."""
    rule = RULE_BY_ID.get(rule_id)
    return rule.family if rule else "unknown"


# Columns used for the CSV export
_JOURNAL_CSV_COLUMNS = (
    "run_id",
    "trade_id",
    "instrument_id",
    "interval_minutes",
    "trade_index",
    "fold",
    "direction",
    "rule_id",
    "rule_family",
    "rule_label_zh",
    "exit_id",
    "exit_label_zh",
    "exit_reason",
    "outcome",
    "signal_time",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "quantity",
    "gross_pnl",
    "commission",
    "slippage_cost",
    "total_cost",
    "net_pnl",
    "cash_after",
    "bars_held",
    "mfe_pct",
    "mae_pct",
    "cost_ratio_pct",
    "pnl_reason_en",
    "pnl_reason_zh",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return format(Decimal(str(value)), "f")
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# Core query builder
# ---------------------------------------------------------------------------

def _build_journal_stmt(
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
    sort_by: str = "entry_time",
    sort_dir: str = "desc",
    limit: int = 200,
    offset: int = 0,
):
    """Build the base SELECT joining trades to runs with dynamic filters."""
    T = StrategyStudyTrade
    R = StrategyStudyRun

    stmt = (
        select(
            T,
            R.instrument_id.label("run_instrument_id"),
            R.interval_minutes.label("run_interval_minutes"),
            R.settings_json.label("run_settings_json"),
            R.id.label("run_id"),
        )
        .join(R, T.run_id == R.id)
        .where(R.user_id == user_id)
    )

    if instrument_id is not None:
        stmt = stmt.where(R.instrument_id == instrument_id)
    if interval_minutes is not None:
        stmt = stmt.where(R.interval_minutes == interval_minutes)
    if rule_id is not None:
        stmt = stmt.where(T.rule_id == rule_id)
    if exit_reason is not None:
        stmt = stmt.where(T.exit_reason == exit_reason)
    if date_from is not None:
        stmt = stmt.where(T.entry_time >= date_from)
    if date_to is not None:
        stmt = stmt.where(T.entry_time <= date_to)
    if pnl_min is not None:
        stmt = stmt.where(T.net_pnl >= pnl_min)
    if pnl_max is not None:
        stmt = stmt.where(T.net_pnl <= pnl_max)

    # rule_family filter is applied in Python after fetch (rule_id -> family
    # mapping lives in RULE_BY_ID, not in the DB). We still push the other
    # filters to SQL for efficiency.
    if rule_family is not None:
        # Collect rule_ids belonging to this family
        family_rule_ids = [
            r.rule_id for r in RULE_BY_ID.values() if r.family == rule_family
        ]
        if family_rule_ids:
            stmt = stmt.where(T.rule_id.in_(family_rule_ids))
        else:
            # No rules in this family — return an impossible condition so the
            # query returns zero rows.
            stmt = stmt.where(T.rule_id == "__no_such_rule__")

    # Sorting
    sort_column = getattr(T, sort_by, T.entry_time)
    if sort_dir == "asc":
        stmt = stmt.order_by(sort_column.asc(), T.id.asc())
    else:
        stmt = stmt.order_by(sort_column.desc(), T.id.desc())

    stmt = stmt.limit(limit).offset(offset)
    return stmt


def _row_to_dict(trade: StrategyStudyTrade, run_instrument_id: str,
                 run_interval_minutes: int, run_settings_json: dict,
                 run_id: UUID) -> dict:
    """Flatten a (trade, run_columns) row into a journal dict."""
    return {
        "run_id": str(run_id),
        "trade_id": str(trade.id),
        "instrument_id": run_instrument_id,
        "interval_minutes": run_interval_minutes,
        "starting_cash": _money(
            _decimal(run_settings_json.get("starting_cash", "0"))
            if run_settings_json else Decimal("0")
        ),
        "trade_index": trade.trade_index,
        "fold": trade.fold,
        "direction": trade.direction,
        "rule_id": trade.rule_id,
        "rule_family": _rule_family(trade.rule_id),
        "rule_label_zh": trade.rule_label_zh,
        "exit_id": trade.exit_id,
        "exit_label_zh": trade.exit_label_zh,
        "exit_reason": trade.exit_reason,
        "outcome": trade.outcome,
        "signal_time": _iso(trade.signal_time),
        "entry_time": _iso(trade.entry_time),
        "exit_time": _iso(trade.exit_time),
        "entry_price": _money(trade.entry_price),
        "exit_price": _money(trade.exit_price),
        "quantity": _money(trade.quantity),
        "gross_pnl": _money(trade.gross_pnl),
        "commission": _money(trade.commission),
        "slippage_cost": _money(trade.slippage_cost),
        "total_cost": _money(trade.total_cost),
        "net_pnl": _money(trade.net_pnl),
        "cash_after": _money(trade.cash_after),
        "bars_held": trade.bars_held,
        "mfe_pct": _money(trade.mfe_pct),
        "mae_pct": _money(trade.mae_pct),
        "cost_ratio_pct": _money(trade.cost_ratio_pct),
        "pnl_reason_zh": trade.pnl_reason_zh,
        "pnl_reason_en": trade.pnl_reason_en,
    }


def _fetch_rows(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
    sort_by: str = "entry_time",
    sort_dir: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> List[dict]:
    """Execute the journal query and return enriched dicts."""
    stmt = _build_journal_stmt(
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    rows = db.execute(stmt).all()
    return [
        _row_to_dict(
            row.StrategyStudyTrade,
            row.run_instrument_id,
            row.run_interval_minutes,
            row.run_settings_json,
            row.run_id,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Count query (for pagination metadata)
# ---------------------------------------------------------------------------

def _count_trades(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
) -> int:
    T = StrategyStudyTrade
    R = StrategyStudyRun

    stmt = (
        select(func.count(T.id))
        .join(R, T.run_id == R.id)
        .where(R.user_id == user_id)
    )

    if instrument_id is not None:
        stmt = stmt.where(R.instrument_id == instrument_id)
    if interval_minutes is not None:
        stmt = stmt.where(R.interval_minutes == interval_minutes)
    if rule_id is not None:
        stmt = stmt.where(T.rule_id == rule_id)
    if exit_reason is not None:
        stmt = stmt.where(T.exit_reason == exit_reason)
    if date_from is not None:
        stmt = stmt.where(T.entry_time >= date_from)
    if date_to is not None:
        stmt = stmt.where(T.entry_time <= date_to)
    if pnl_min is not None:
        stmt = stmt.where(T.net_pnl >= pnl_min)
    if pnl_max is not None:
        stmt = stmt.where(T.net_pnl <= pnl_max)
    if rule_family is not None:
        family_rule_ids = [
            r.rule_id for r in RULE_BY_ID.values() if r.family == rule_family
        ]
        if family_rule_ids:
            stmt = stmt.where(T.rule_id.in_(family_rule_ids))
        else:
            stmt = stmt.where(T.rule_id == "__no_such_rule__")

    return db.execute(stmt).scalar() or 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_journal_trades(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
    sort_by: str = "entry_time",
    sort_dir: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> List[dict]:
    """JOIN strategy_study_runs + strategy_study_trades with filters.

    Returns flat dicts enriched with run-level metadata (instrument_id,
    interval_minutes, starting_cash from settings_json).
    """
    return _fetch_rows(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


def journal_summary(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
) -> dict:
    """Aggregate: total_trades, total_pnl, win_rate, avg_trade_pnl,
    profit_factor, max_drawdown_pct, best/worst trade.

    Fetches all matching trades (no pagination) to compute aggregates.
    """
    trades = _fetch_rows(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by="entry_time",
        sort_dir="asc",
        limit=100_000,
        offset=0,
    )

    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "total_pnl": "0",
            "win_rate": "0",
            "avg_trade_pnl": "0",
            "profit_factor": None,
            "max_drawdown_pct": "0",
            "best_trade": None,
            "worst_trade": None,
        }

    pnls = [_decimal(t["net_pnl"]) for t in trades]
    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count = len(wins)
    win_rate = Decimal(str(win_count)) / Decimal(str(total_trades)) * Decimal("100")
    avg_trade_pnl = total_pnl / Decimal(str(total_trades))

    gross_wins = sum(wins) if wins else Decimal("0")
    gross_losses = abs(sum(losses)) if losses else Decimal("0")
    if gross_losses > 0:
        profit_factor = gross_wins / gross_losses
    else:
        profit_factor = None  # No losses — undefined

    # Max drawdown from running equity (starting from 0)
    equity = Decimal("0")
    peak = Decimal("0")
    max_drawdown_pct = Decimal("0")
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * Decimal("100")
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

    best = max(trades, key=lambda t: _decimal(t["net_pnl"]))
    worst = min(trades, key=lambda t: _decimal(t["net_pnl"]))

    return {
        "total_trades": total_trades,
        "total_pnl": _money(total_pnl),
        "win_rate": _money(win_rate.quantize(Decimal("0.01"))),
        "avg_trade_pnl": _money(avg_trade_pnl.quantize(Decimal("0.00000001"))),
        "profit_factor": _money(profit_factor.quantize(Decimal("0.01"))) if profit_factor is not None else None,
        "max_drawdown_pct": _money(max_drawdown_pct.quantize(Decimal("0.01"))),
        "best_trade": {
            "trade_id": best["trade_id"],
            "net_pnl": best["net_pnl"],
            "entry_time": best["entry_time"],
            "instrument_id": best["instrument_id"],
            "rule_label_zh": best["rule_label_zh"],
        },
        "worst_trade": {
            "trade_id": worst["trade_id"],
            "net_pnl": worst["net_pnl"],
            "entry_time": worst["entry_time"],
            "instrument_id": worst["instrument_id"],
            "rule_label_zh": worst["rule_label_zh"],
        },
    }


def cumulative_pnl_series(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
) -> List[dict]:
    """Returns [{ trade_date, net_pnl, cumulative_pnl, trade_id,
    instrument_id, rule_label_zh }, ...] ordered by entry_time ASC.

    Computes a running sum of net_pnl.
    """
    trades = _fetch_rows(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by="entry_time",
        sort_dir="asc",
        limit=100_000,
        offset=0,
    )

    cumulative = Decimal("0")
    result = []
    for t in trades:
        net = _decimal(t["net_pnl"])
        cumulative += net
        result.append({
            "trade_date": t["entry_time"],
            "net_pnl": _money(net),
            "cumulative_pnl": _money(cumulative),
            "trade_id": t["trade_id"],
            "instrument_id": t["instrument_id"],
            "rule_label_zh": t["rule_label_zh"],
        })
    return result


def equity_curve_series(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
) -> List[dict]:
    """Returns [{ date, equity, drawdown_pct }, ...].

    Starting cash from first run's settings_json.starting_cash.
    Tracks peak equity for drawdown calculation.
    """
    trades = _fetch_rows(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by="entry_time",
        sort_dir="asc",
        limit=100_000,
        offset=0,
    )

    if not trades:
        return []

    # Starting cash from the first run encountered
    starting_cash = _decimal(trades[0].get("starting_cash", "10000"))
    equity = starting_cash
    peak = starting_cash
    result = []

    for t in trades:
        net = _decimal(t["net_pnl"])
        equity += net
        if equity > peak:
            peak = equity
        if peak > 0:
            dd_pct = (peak - equity) / peak * Decimal("100")
        else:
            dd_pct = Decimal("0")
        result.append({
            "date": t["entry_time"],
            "equity": _money(equity),
            "drawdown_pct": _money(dd_pct.quantize(Decimal("0.01"))),
        })

    return result


def get_journal_filter_options(
    db: Session,
    *,
    user_id: UUID,
) -> dict:
    """Returns available filter values: instruments, rule_ids with families,
    intervals, exit_reasons -- derived from existing runs.
    """
    R = StrategyStudyRun
    T = StrategyStudyTrade

    # Distinct instruments
    instruments_stmt = (
        select(R.instrument_id)
        .where(R.user_id == user_id)
        .distinct()
        .order_by(R.instrument_id)
    )
    instruments = list(db.execute(instruments_stmt).scalars().all())

    # Distinct intervals
    intervals_stmt = (
        select(R.interval_minutes)
        .where(R.user_id == user_id)
        .distinct()
        .order_by(R.interval_minutes)
    )
    intervals = list(db.execute(intervals_stmt).scalars().all())

    # Distinct rule_ids with their families (from DB, enriched by RULE_BY_ID)
    rules_stmt = (
        select(T.rule_id, T.rule_label_zh)
        .join(R, T.run_id == R.id)
        .where(R.user_id == user_id)
        .distinct()
        .order_by(T.rule_id)
    )
    rule_rows = db.execute(rules_stmt).all()
    rule_ids = [
        {
            "rule_id": r.rule_id,
            "rule_label_zh": r.rule_label_zh,
            "family": _rule_family(r.rule_id),
        }
        for r in rule_rows
    ]

    # Distinct rule families
    families = sorted({r["family"] for r in rule_ids})

    # Distinct exit_reasons
    exit_reasons_stmt = (
        select(T.exit_reason)
        .join(R, T.run_id == R.id)
        .where(R.user_id == user_id)
        .distinct()
        .order_by(T.exit_reason)
    )
    exit_reasons = list(db.execute(exit_reasons_stmt).scalars().all())

    return {
        "instruments": instruments,
        "intervals": intervals,
        "rule_ids": rule_ids,
        "rule_families": families,
        "exit_reasons": exit_reasons,
    }


def journal_trades_csv_content(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    pnl_min: Optional[Decimal] = None,
    pnl_max: Optional[Decimal] = None,
    exit_reason: Optional[str] = None,
) -> str:
    """Generate CSV content for all filtered trades across all runs."""
    trades = _fetch_rows(
        db,
        user_id=user_id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        sort_by="entry_time",
        sort_dir="asc",
        limit=100_000,
        offset=0,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(_JOURNAL_CSV_COLUMNS)
    for t in trades:
        writer.writerow([
            _csv_cell(t.get(col)) for col in _JOURNAL_CSV_COLUMNS
        ])
    return buffer.getvalue()
