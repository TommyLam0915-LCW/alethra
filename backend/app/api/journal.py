"""Journal routes.

Read-only views over the unified trading journal. Every route is owner-scoped
via the standard ``get_current_user`` dependency. The journal aggregates
trades across all stored strategy_study_runs for one user.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.lib.export_headers import (
    EXPORT_ROW_LIMIT_HEADER,
    EXPORT_ROWS_EXPORTED_HEADER,
    EXPORT_ROWS_MATCHED_HEADER,
    EXPORT_TRUNCATED_HEADER,
)
from app.lib.measurement import measured, ratio, summed
from app.models.user import User
from app.services import journal_service as svc
from app.services import skill_attribution as skill_svc

router = APIRouter(prefix="/journal", tags=["journal"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared filter kwargs builder
# ---------------------------------------------------------------------------

def _filter_kwargs(
    *,
    user_id,
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
    return dict(
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
    )


# ---------------------------------------------------------------------------
# GET /journal/trades
# ---------------------------------------------------------------------------

@router.get("/trades")
def journal_trades(
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    sort_by: str = Query("entry_time"),
    sort_dir: str = Query("desc"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns { trades: [...], summary: {...}, total_count: int }.

    Paginated list of trades across all runs, enriched with run-level
    metadata. Summary stats are computed over the full filtered set
    (ignoring pagination).
    """
    fk = _filter_kwargs(
        user_id=current_user.id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
    )
    trades = svc.list_journal_trades(
        db, **fk, sort_by=sort_by, sort_dir=sort_dir, limit=limit, offset=offset,
    )
    summary = svc.journal_summary(db, **fk)
    total_count = svc._count_trades(db, **fk)
    return {"trades": trades, "summary": summary, "total_count": total_count}


# ---------------------------------------------------------------------------
# GET /journal/trades.csv
# ---------------------------------------------------------------------------

@router.get("/trades.csv")
def journal_trades_csv(
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """CSV export of all filtered trades across all runs.

    The row-count headers carry what the export could not put in the file: it
    stops at ``journal_service.CSV_EXPORT_ROW_LIMIT``, and a 200 with a
    well-formed body says nothing about how many matching trades are missing.
    """
    fk = _filter_kwargs(
        user_id=current_user.id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
    )
    export = svc.journal_trades_csv_export(db, **fk)
    return Response(
        content=export["content"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="journal-trades.csv"',
            EXPORT_ROWS_EXPORTED_HEADER: str(export["rows_exported"]),
            EXPORT_ROWS_MATCHED_HEADER: str(export["rows_matched"]),
            EXPORT_ROW_LIMIT_HEADER: str(export["row_limit"]),
            EXPORT_TRUNCATED_HEADER: (
                "true" if export["truncated"] else "false"
            ),
        },
    )


# ---------------------------------------------------------------------------
# GET /journal/filters
# ---------------------------------------------------------------------------

@router.get("/filters")
def journal_filter_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns available filter values derived from existing runs."""
    return svc.get_journal_filter_options(db, user_id=current_user.id)


# ---------------------------------------------------------------------------
# GET /journal/cumulative-pnl
# ---------------------------------------------------------------------------

@router.get("/cumulative-pnl")
def journal_cumulative_pnl(
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns { points: [...], total_pnl: str }."""
    fk = _filter_kwargs(
        user_id=current_user.id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
    )
    points = svc.cumulative_pnl_series(db, **fk)
    total_pnl = points[-1]["cumulative_pnl"] if points else "0"
    return {"points": points, "total_pnl": total_pnl}


# ---------------------------------------------------------------------------
# GET /journal/equity-curve
# ---------------------------------------------------------------------------

@router.get("/equity-curve")
def journal_equity_curve(
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    group_by: Optional[str] = Query(None, pattern="^(instrument|rule_family)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns { baseline_cash: str | null, series: [{ name, points }] }.

    When group_by is set, the equity curve is split into sub-series by the
    given dimension. Otherwise a single series named 'all' is returned.
    baseline_cash is None when the runs behind the curve record no starting
    cash (the curve then reads as cumulative net P&L from zero), and each
    point's drawdown_pct is None while the running peak is not positive.
    """
    fk = _filter_kwargs(
        user_id=current_user.id,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
    )

    if group_by is None:
        points = svc.equity_curve_series(db, **fk)
        if not points:
            return {"baseline_cash": None, "series": []}
        # baseline_cash is the starting cash before any trades, taken from the
        # first trade's run settings; a run that records none leaves it None.
        all_trades = svc._fetch_rows(
            db, **fk, sort_by="entry_time", sort_dir="asc", limit=1, offset=0,
        )
        baseline = all_trades[0]["starting_cash"] if all_trades else None
        return {"baseline_cash": baseline, "series": [{"name": "all", "points": points}]}

    # Grouped: fetch all trades unsorted, split by group key, compute per-group
    trades = svc._fetch_rows(
        db, **fk, sort_by="entry_time", sort_dir="asc", limit=100_000, offset=0,
    )
    if not trades:
        return {"baseline_cash": None, "series": []}

    baseline_cash = trades[0].get("starting_cash")
    groups: dict = {}
    for t in trades:
        key = t.get(group_by) or t.get("instrument_id", "unknown")
        groups.setdefault(key, []).append(t)

    series_list = []
    for name, group_trades in sorted(groups.items()):
        equity = (
            Decimal(str(baseline_cash)) if baseline_cash is not None
            else Decimal("0")
        )
        peak = equity
        pts = []
        for t in group_trades:
            net = Decimal(str(t["net_pnl"]))
            equity += net
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * Decimal("100") if peak > 0 else None
            pts.append({
                "date": t["entry_time"],
                "equity": svc._money(equity),
                "drawdown_pct": (
                    svc._money(dd.quantize(Decimal("0.01"))) if dd is not None else None
                ),
            })
        series_list.append({"name": str(name), "points": pts})

    return {"baseline_cash": baseline_cash, "series": series_list}


# ---------------------------------------------------------------------------
# GET /journal/skill-attribution
# ---------------------------------------------------------------------------

@router.get("/skill-attribution")
def journal_skill_attribution(
    instrument_id: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns { skills: [...], cross_instrument: [...], cross_interval: [...] }.

    Per-skill analytics: each skill (rule_id) gets total_pnl, trade_count,
    win_rate, sharpe_ratio, max_drawdown_pct, profit_factor, etc.
    Cross-tabulations break metrics down by instrument and interval.
    """
    return skill_svc.skill_attribution(
        db,
        user_id=current_user.id,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------------------------
# GET /journal/skill-heatmap
# ---------------------------------------------------------------------------

@router.get("/skill-heatmap")
def journal_skill_heatmap(
    metric: str = Query("net_pnl", pattern="^(net_pnl|win_rate|sharpe|trade_count)$"),
    instrument_id: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Returns heatmap matrix data: { rows, cols, cells }.

    Rows are skills (rule_ids) sorted by total_pnl DESC.
    Columns are instruments sorted alphabetically.
    Each cell is the requested metric for that (skill, instrument) pair.
    """
    return skill_svc.skill_heatmap_data(
        db,
        user_id=current_user.id,
        metric=metric,
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
    )


# ---------------------------------------------------------------------------
# GET /journal/compare
# ---------------------------------------------------------------------------

@router.get("/compare")
def journal_compare(
    run_ids: str = Query(..., description="Comma-separated list of run IDs to compare"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Compare multiple research runs side-by-side.

    Returns metrics for each run: total trades, win rate, Sharpe, max drawdown,
    total PnL, avg trade PnL. Highlights best/worst per metric.
    """
    from sqlalchemy import text
    
    run_id_list = [rid.strip() for rid in run_ids.split(',') if rid.strip()]
    
    if not run_id_list:
        return {"error": "no_run_ids", "message": "Provide at least one run ID."}
    
    if len(run_id_list) > 10:
        return {"error": "too_many_runs", "message": "Maximum 10 runs per comparison."}
    
    comparisons = []
    
    for run_id in run_id_list:
        # Fetch run metadata
        run_result = db.execute(text('''
            SELECT id, instrument_id, interval_minutes, trade_count, created_at
            FROM strategy_study_runs
            WHERE id = :run_id AND user_id = :user_id
        '''), {'run_id': run_id, 'user_id': str(current_user.id)}).fetchone()
        
        if not run_result:
            continue
        
        # Fetch trade metrics
        trades_result = db.execute(text('''
            SELECT 
                COUNT(*) as total_trades,
                COUNT(CASE WHEN net_pnl > 0 THEN 1 END) as wins,
                COUNT(CASE WHEN net_pnl <= 0 THEN 1 END) as losses,
                SUM(net_pnl) as total_pnl,
                AVG(net_pnl) as avg_pnl,
                AVG(CASE WHEN net_pnl > 0 THEN net_pnl END) as avg_win,
                AVG(CASE WHEN net_pnl <= 0 THEN net_pnl END) as avg_loss
            FROM strategy_study_trades
            WHERE run_id = :run_id
        '''), {'run_id': run_id}).fetchone()
        
        if not trades_result:
            # COUNT(*) always returns one row, so a missing row means the query
            # itself did not run. A run with zero trades is real data and must
            # stay in the comparison: dropping it would silently shrink the set
            # the user asked to compare and move the best/worst highlights.
            continue
        
        total_trades = trades_result[0]
        wins = trades_result[1]
        losses = trades_result[2]
        # SUM over no trades is the empty sum, so 0 is the honest answer there.
        # AVG and the win rate have no value at all when the set is empty -- SQL
        # returns NULL precisely because there is no answer -- so they stay None
        # instead of being read back as a measured zero.
        total_pnl = summed(trades_result[3])
        avg_pnl = measured(trades_result[4])
        avg_win = measured(trades_result[5])
        avg_loss = measured(trades_result[6])
        win_rate = ratio(wins, total_trades)
        
        # Compute Sharpe (simplified)
        pnl_values = db.execute(text('''
            SELECT net_pnl FROM strategy_study_trades WHERE run_id = :run_id
        '''), {'run_id': run_id}).fetchall()
        
        pnls = [float(p[0]) for p in pnl_values]
        if len(pnls) >= 2:
            mean = sum(pnls) / len(pnls)
            variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            stdev = variance ** 0.5 if variance > 0 else 0
            sharpe = mean / stdev if stdev > 0 else None
        else:
            sharpe = None
        
        # Compute max drawdown
        cash_result = db.execute(text('''
            SELECT cash_after FROM strategy_study_trades 
            WHERE run_id = :run_id ORDER BY entry_time
        '''), {'run_id': run_id}).fetchall()
        
        cash_values = [float(c[0]) for c in cash_result]
        if cash_values:
            peak = cash_values[0]
            max_dd = 0
            for cash in cash_values:
                if cash > peak:
                    peak = cash
                if peak > 0:
                    dd = (peak - cash) / peak * 100
                    if dd > max_dd:
                        max_dd = dd
        else:
            max_dd = 0
        
        comparisons.append({
            'run_id': run_id,
            'instrument_id': run_result[1],
            'interval_minutes': run_result[2],
            'created_at': run_result[4].isoformat() if run_result[4] else None,
            'total_trades': total_trades,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'sharpe': sharpe,
            'max_drawdown_pct': max_dd,
        })
    
    # Find best/worst per metric.
    #
    # A highlight is a claim about which run is best, so an unmeasured metric
    # cannot take part in the ranking: sorting on None either raises or lets
    # whichever row happens to come first win by default. Each key is emitted
    # only when at least one compared run actually measured that metric, so the
    # client renders no highlight rather than a fabricated one. max_drawdown is
    # highlighted by the *lowest* drawdown, which is the better outcome.
    highlights = {}
    for key, field, lower_is_better in (
        ('sharpe', 'sharpe', False),
        ('pnl', 'total_pnl', False),
        ('win_rate', 'win_rate', False),
        ('max_drawdown', 'max_drawdown_pct', True),
    ):
        ranked = [c for c in comparisons if c[field] is not None]
        if not ranked:
            continue
        best_fn = min if lower_is_better else max
        worst_fn = max if lower_is_better else min
        highlights[f'best_{key}'] = best_fn(ranked, key=lambda c: c[field])['run_id']
        highlights[f'worst_{key}'] = worst_fn(ranked, key=lambda c: c[field])['run_id']

    return {
        'comparisons': comparisons,
        'highlights': highlights,
        'count': len(comparisons),
    }


# ---------------------------------------------------------------------------
# GET /journal/unified
# ---------------------------------------------------------------------------

@router.get("/unified")
def journal_unified(
    source: str = Query("all", pattern="^(all|research|paper)$"),
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Unified journal: research trades and paper fills over one filter.

    Every filter the trades endpoint takes is accepted here and reaches at
    least one leg. ``filter_scope`` says which keys reached which leg and
    ``limitations`` names, in the caller's own words, the ones this response
    could not honour — a paper fill has no settled P&L, no run interval and no
    exit reason, so a P&L filter narrows the research rows and nothing else.

    ``trades`` is one page of the merged population, ordered newest first by
    ``journal_service.unified_sort_key``; ``summary`` counts and totals the
    whole filtered set, not the page; ``page`` is what came back.
    """
    return svc.unified_journal_page(
        db,
        user_id=current_user.id,
        source=source,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /journal/unified.csv
# ---------------------------------------------------------------------------

@router.get("/unified.csv")
def journal_unified_csv(
    source: str = Query("all", pattern="^(all|research|paper)$"),
    instrument_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    rule_family: Optional[str] = Query(None),
    interval_minutes: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    exit_reason: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """CSV export of the merged journal, over the range the unified tab shows.

    The unified table lists both sources; ``/journal/trades.csv`` exports the
    research rows only. Exporting one while displaying the other puts two
    different filters behind one button, so the merged view exports from the
    merged query and carries the same row-budget headers.
    """
    export = svc.unified_journal_csv_export(
        db,
        user_id=current_user.id,
        source=source,
        instrument_id=instrument_id,
        rule_id=rule_id,
        rule_family=rule_family,
        interval_minutes=interval_minutes,
        date_from=date_from,
        date_to=date_to,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        exit_reason=exit_reason,
    )
    return Response(
        content=export["content"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="journal-unified.csv"',
            EXPORT_ROWS_EXPORTED_HEADER: str(export["rows_exported"]),
            EXPORT_ROWS_MATCHED_HEADER: str(export["rows_matched"]),
            EXPORT_ROW_LIMIT_HEADER: str(export["row_limit"]),
            EXPORT_TRUNCATED_HEADER: (
                "true" if export["truncated"] else "false"
            ),
        },
    )
