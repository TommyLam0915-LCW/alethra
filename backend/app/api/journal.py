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
    """CSV export of all filtered trades across all runs."""
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
    content = svc.journal_trades_csv_content(db, **fk)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="journal-trades.csv"'},
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
    """Returns { baseline_cash: str, series: [{ name, points }] }.

    When group_by is set, the equity curve is split into sub-series by the
    given dimension. Otherwise a single series named 'all' is returned.
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
            return {"baseline_cash": "10000", "series": []}
        # baseline_cash is the starting cash before any trades; fetch it from
        # the first trade's run settings, or default to 10000.
        all_trades = svc._fetch_rows(
            db, **fk, sort_by="entry_time", sort_dir="asc", limit=1, offset=0,
        )
        baseline = all_trades[0]["starting_cash"] if all_trades else "10000"
        return {"baseline_cash": baseline, "series": [{"name": "all", "points": points}]}

    # Grouped: fetch all trades unsorted, split by group key, compute per-group
    trades = svc._fetch_rows(
        db, **fk, sort_by="entry_time", sort_dir="asc", limit=100_000, offset=0,
    )
    if not trades:
        return {"baseline_cash": "10000", "series": []}

    baseline_cash = trades[0].get("starting_cash", "10000")
    groups: dict = {}
    for t in trades:
        key = t.get(group_by) or t.get("instrument_id", "unknown")
        groups.setdefault(key, []).append(t)

    series_list = []
    for name, group_trades in sorted(groups.items()):
        equity = Decimal(str(baseline_cash))
        peak = equity
        pts = []
        for t in group_trades:
            net = Decimal(str(t["net_pnl"]))
            equity += net
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * Decimal("100") if peak > 0 else Decimal("0")
            pts.append({
                "date": t["entry_time"],
                "equity": svc._money(equity),
                "drawdown_pct": svc._money(dd.quantize(Decimal("0.01"))),
            })
        series_list.append({"name": str(name), "points": pts})

    return {"baseline_cash": svc._money(Decimal(str(baseline_cash))), "series": series_list}


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
        
        if not trades_result or trades_result[0] == 0:
            continue
        
        total_trades = trades_result[0]
        wins = trades_result[1]
        losses = trades_result[2]
        total_pnl = float(trades_result[3] or 0)
        avg_pnl = float(trades_result[4] or 0)
        avg_win = float(trades_result[5] or 0)
        avg_loss = float(trades_result[6] or 0)
        win_rate = wins / total_trades if total_trades > 0 else 0
        
        # Compute Sharpe (simplified)
        pnl_values = db.execute(text('''
            SELECT net_pnl FROM strategy_study_trades WHERE run_id = :run_id
        '''), {'run_id': run_id}).fetchall()
        
        pnls = [float(p[0]) for p in pnl_values]
        if len(pnls) >= 2:
            mean = sum(pnls) / len(pnls)
            variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            stdev = variance ** 0.5 if variance > 0 else 0
            sharpe = mean / stdev if stdev > 0 else 0
        else:
            sharpe = 0
        
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
    
    # Find best/worst per metric
    if comparisons:
        best_sharpe = max(comparisons, key=lambda x: x['sharpe'])
        worst_sharpe = min(comparisons, key=lambda x: x['sharpe'])
        best_pnl = max(comparisons, key=lambda x: x['total_pnl'])
        worst_pnl = min(comparisons, key=lambda x: x['total_pnl'])
        best_win_rate = max(comparisons, key=lambda x: x['win_rate'])
        worst_win_rate = min(comparisons, key=lambda x: x['win_rate'])
        
        highlights = {
            'best_sharpe': best_sharpe['run_id'],
            'worst_sharpe': worst_sharpe['run_id'],
            'best_pnl': best_pnl['run_id'],
            'worst_pnl': worst_pnl['run_id'],
            'best_win_rate': best_win_rate['run_id'],
            'worst_win_rate': worst_win_rate['run_id'],
        }
    else:
        highlights = {}
    
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
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    pnl_min: Optional[Decimal] = Query(None),
    pnl_max: Optional[Decimal] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Unified view merging research trades and paper trading fills.

    source=all: both research and paper trades
    source=research: only strategy_study_trades
    source=paper: only paper_fills + paper_orders

    Each trade includes a 'source' field indicating origin.
    """
    from sqlalchemy import text
    
    unified_trades = []
    
    # Fetch research trades
    if source in ('all', 'research'):
        research_query = '''
            SELECT 
                'research' as source,
                t.id,
                t.run_id,
                r.instrument_id,
                t.rule_id,
                t.exit_id,
                t.entry_time,
                t.exit_time,
                t.entry_price,
                t.exit_price,
                t.net_pnl,
                t.exit_reason,
                t.bars_held,
                t.mfe_pct,
                t.mae_pct,
                r.interval_minutes
            FROM strategy_study_trades t
            JOIN strategy_study_runs r ON t.run_id = r.id
            WHERE r.user_id = :user_id
        '''
        
        params = {'user_id': str(current_user.id)}
        conditions = []
        
        if instrument_id:
            conditions.append('r.instrument_id = :instrument_id')
            params['instrument_id'] = instrument_id
        
        if rule_id:
            conditions.append('t.rule_id = :rule_id')
            params['rule_id'] = rule_id
        
        if date_from:
            conditions.append('t.entry_time >= :date_from')
            params['date_from'] = date_from
        
        if date_to:
            conditions.append('t.entry_time <= :date_to')
            params['date_to'] = date_to
        
        if pnl_min is not None:
            conditions.append('t.net_pnl >= :pnl_min')
            params['pnl_min'] = pnl_min
        
        if pnl_max is not None:
            conditions.append('t.net_pnl <= :pnl_max')
            params['pnl_max'] = pnl_max
        
        if conditions:
            research_query += ' AND ' + ' AND '.join(conditions)
        
        research_query += ' ORDER BY t.entry_time DESC LIMIT :limit OFFSET :offset'
        params['limit'] = limit
        params['offset'] = offset
        
        research_result = db.execute(text(research_query), params).fetchall()
        
        for row in research_result:
            unified_trades.append({
                'source': row[0],
                'id': str(row[1]),
                'run_id': str(row[2]),
                'instrument_id': row[3],
                'rule_id': row[4],
                'exit_id': row[5],
                'entry_time': row[6].isoformat() if row[6] else None,
                'exit_time': row[7].isoformat() if row[7] else None,
                'entry_price': float(row[8]) if row[8] else None,
                'exit_price': float(row[9]) if row[9] else None,
                'net_pnl': float(row[10]) if row[10] else None,
                'exit_reason': row[11],
                'bars_held': row[12],
                'mfe': float(row[13]) if row[13] else None,
                'mae': float(row[14]) if row[14] else None,
                'interval_minutes': row[15],
            })
    
    # Fetch paper trades
    if source in ('all', 'paper'):
        paper_query = '''
            SELECT 
                'paper' as source,
                f.id,
                f.paper_order_id,
                o.asset_id,
                f.bar_timestamp,
                f.execution_price_before_costs,
                f.quantity,
                o.side,
                o.order_type,
                o.created_at as order_created_at
            FROM paper_fills f
            JOIN paper_orders o ON f.paper_order_id = o.id
            JOIN paper_portfolios p ON o.portfolio_id = p.id
            WHERE p.owner_id = :user_id
        '''
        
        params = {'user_id': str(current_user.id)}
        conditions = []
        
        if instrument_id:
            conditions.append('o.asset_id = :instrument_id')
            params['instrument_id'] = instrument_id
        
        if date_from:
            conditions.append('f.bar_timestamp >= :date_from')
            params['date_from'] = date_from
        
        if date_to:
            conditions.append('f.bar_timestamp <= :date_to')
            params['date_to'] = date_to
        
        if conditions:
            paper_query += ' AND ' + ' AND '.join(conditions)
        
        paper_query += ' ORDER BY f.bar_timestamp DESC LIMIT :limit OFFSET :offset'
        params['limit'] = limit
        params['offset'] = offset
        
        paper_result = db.execute(text(paper_query), params).fetchall()
        
        for row in paper_result:
            unified_trades.append({
                'source': row[0],
                'id': str(row[1]),
                'order_id': str(row[2]),
                'instrument_id': row[3],
                'entry_time': row[4].isoformat() if row[4] else None,
                'entry_price': float(row[5]) if row[5] else None,
                'quantity': float(row[6]) if row[6] else None,
                'side': row[7],
                'order_type': row[8],
                'created_at': row[9].isoformat() if row[9] else None,
                'net_pnl': None,  # Paper fills don't have PnL directly
            })
    
    # Sort by time
    unified_trades.sort(
        key=lambda x: x.get('entry_time') or '',
        reverse=True
    )
    
    # Compute summary
    research_trades = [t for t in unified_trades if t['source'] == 'research']
    paper_trades = [t for t in unified_trades if t['source'] == 'paper']
    
    research_pnl = sum(t['net_pnl'] or 0 for t in research_trades)
    research_wins = len([t for t in research_trades if (t.get('net_pnl') or 0) > 0])
    research_win_rate = research_wins / len(research_trades) if research_trades else 0
    
    return {
        'trades': unified_trades,
        'summary': {
            'total_count': len(unified_trades),
            'research_count': len(research_trades),
            'paper_count': len(paper_trades),
            'research_total_pnl': research_pnl,
            'research_win_rate': research_win_rate,
        },
        'filters_applied': {
            'source': source,
            'instrument_id': instrument_id,
            'rule_id': rule_id,
            'date_from': date_from.isoformat() if date_from else None,
            'date_to': date_to.isoformat() if date_to else None,
            'pnl_min': float(pnl_min) if pnl_min else None,
            'pnl_max': float(pnl_max) if pnl_max else None,
        },
    }
