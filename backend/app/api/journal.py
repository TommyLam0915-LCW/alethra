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
