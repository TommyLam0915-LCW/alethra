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
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, case, cast, false, func, literal_column, or_, select, String
from sqlalchemy.orm import Session

from app.models.paper_portfolio import (
    DecisionRecord,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
)
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


def _family_rule_ids(rule_family: str) -> List[str]:
    """The rule ids belonging to one family.

    The family-to-rule mapping lives in ``RULE_BY_ID``, not in a table, so a
    family filter can only reach the database as the list of ids it names. An
    empty list is not "no filter": it is a family this build knows no rule in,
    and every caller must turn it into a condition that matches nothing rather
    than quietly dropping the filter and returning everything.
    """
    return [
        rule.rule_id for rule in RULE_BY_ID.values() if rule.family == rule_family
    ]


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

# Rows a single CSV export writes. Above it the file is complete only by
# accident, so the export reports the limit next to the rows it actually wrote.
CSV_EXPORT_ROW_LIMIT = 100_000

# The columns a spreadsheet has to be able to compute on. ``_row_to_dict``
# renders money as text for the JSON body, so these arrive at the writer as
# strings, and a string is what the formula guard below rewrites. Without the
# column type every loss would be exported as ``'-123.45`` — left-aligned, and
# no client can add it.
_JOURNAL_CSV_NUMERIC_COLUMNS = frozenset({
    "interval_minutes",
    "trade_index",
    "fold",
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
})

# A cell starting with any of these could be read as a formula by Excel/Sheets.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _guard_formula(text: str) -> str:
    """Neutralise spreadsheet CSV-formula injection (OWASP) in a text cell.

    A leading single quote makes a spreadsheet show the literal text instead of
    evaluating it. Numeric cells never come here — see ``_csv_cell``.
    """
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _csv_cell(value: Any, column: str = "") -> str:
    """Render one cell, keeping the number/text contract per column.

    A numeric column is normalised to plain fixed-point text and never guarded,
    because ``-`` is both a minus sign and a formula prefix. A value there that
    does not parse to a finite decimal is not a number, so it is written as
    guarded text rather than silently coerced — the cell stays visibly wrong
    instead of becoming a plausible zero. An unknown column is treated as text,
    which is the fail-closed direction. An empty cell always means null.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if column in _JOURNAL_CSV_NUMERIC_COLUMNS:
        try:
            number = Decimal(text)
        except InvalidOperation:
            return _guard_formula(text)
        if number.is_finite():
            return format(number, "f")
        return _guard_formula(text)
    return _guard_formula(text)


# ---------------------------------------------------------------------------
# Core query builder
# ---------------------------------------------------------------------------

def _journal_conditions(
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
) -> List[Any]:
    """The one research-leg filter, as a list of SQL conditions.

    Rows, counts and aggregates used to build this predicate three times over —
    once in ``_build_journal_stmt``, once in ``_count_trades``, once more per
    filter added to either — and a list beside a number only means something
    while the two copies agree. A journal page is a claim about one set of
    trades, so the set is written down once and every reader takes it from here.
    """
    T = StrategyStudyTrade
    R = StrategyStudyRun

    conditions = [R.user_id == user_id]

    if instrument_id is not None:
        conditions.append(R.instrument_id == instrument_id)
    if interval_minutes is not None:
        conditions.append(R.interval_minutes == interval_minutes)
    if rule_id is not None:
        conditions.append(T.rule_id == rule_id)
    if exit_reason is not None:
        conditions.append(T.exit_reason == exit_reason)
    if date_from is not None:
        conditions.append(T.entry_time >= date_from)
    if date_to is not None:
        conditions.append(T.entry_time <= date_to)
    if pnl_min is not None:
        conditions.append(T.net_pnl >= pnl_min)
    if pnl_max is not None:
        conditions.append(T.net_pnl <= pnl_max)

    if rule_family is not None:
        # The family-to-rule mapping lives in RULE_BY_ID, not in the database,
        # so the filter reaches SQL as the list of ids the family names.
        family_rule_ids = _family_rule_ids(rule_family)
        if family_rule_ids:
            conditions.append(T.rule_id.in_(family_rule_ids))
        else:
            # No rules in this family. FALSE keeps the filter in the query as a
            # condition that matches nothing; dropping it here would hand back
            # every row to a caller who asked for a family that has none.
            conditions.append(false())

    return conditions


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
        .where(*_journal_conditions(
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
        ))
    )

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
            _decimal(run_settings_json["starting_cash"])
            if run_settings_json
            and run_settings_json.get("starting_cash") is not None
            else None
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
        .where(*_journal_conditions(
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
        ))
    )

    return db.execute(stmt).scalar() or 0


# ---------------------------------------------------------------------------
# Paper leg
# ---------------------------------------------------------------------------

# The filter keys a paper fill cannot answer, named here so the endpoint can
# say which half of the request it did not carry out. A fill is one leg of a
# round trip: it has no settled P&L, no run interval and no exit reason.
# Accepting ``pnl_min`` and applying it to the research rows only produces a
# page of losing fills under a filter that reads as though it had been honoured.
PAPER_INAPPLICABLE_FILTERS = (
    "pnl_min",
    "pnl_max",
    "interval_minutes",
    "exit_reason",
)


def _enum_text(value: Any) -> Optional[str]:
    """Render an ``SAEnum`` column as the name it stores.

    ``str(OrderSide.BUY)`` is not ``"BUY"`` for a ``str``-mixed enum under every
    Python, and the journal hands the result straight to a browser. ``.name`` is
    the token the column actually wrote.
    """
    if value is None:
        return None
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)


def _utc_naive(value: datetime) -> datetime:
    """The instant in the convention ``paper_fills.bar_timestamp`` is written in.

    That column is a plain ``DateTime`` the replay fills with naive UTC values,
    so a tz-aware bound would be compared against a different representation of
    the same time than the one stored.
    """
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _utc_date_text(value: datetime) -> str:
    """The calendar day an incoming bound falls on, in UTC."""
    return _utc_naive(value).strftime("%Y-%m-%d")


def _fill_day_key() -> Any:
    """The calendar day of a fill, as text, across both stored granularities.

    ``bar_timestamp`` is nullable by design: a per-bar replay fill records the
    bar it filled on, a daily fill records only ``execution_date``. Coercing the
    date into a timestamp to compare the two does not work — SQLite parses
    ``CAST('2026-08-01' AS DATETIME)`` into the INTEGER ``2026``, so every
    date-only fill sorts below every real instant and falls out of a date filter
    without an error being raised. The day is therefore compared as the text
    both columns hold it in.
    """
    return func.coalesce(
        func.substr(cast(PaperFill.bar_timestamp, String), 1, 10),
        PaperFill.execution_date,
    )


def _paper_conditions(
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Any]:
    """The paper-leg filter, as a list of SQL conditions.

    Rule attribution reaches a fill through ``decision_records.source_rule_id``
    (the replay stamps the upstream study rule onto the decision that ordered
    the fill), joined outer so a fill with no decision record stays visible:
    hiding it would make recorded history disappear from the journal. A rule
    filter then narrows it back down, because an unattributable fill matches no
    rule.

    A date bound is applied at the precision the row carries — the exact instant
    when the fill recorded one, the day when it recorded only a day. Anything
    else silently drops the daily fills, which is what the previous
    ``bar_timestamp``-only comparison did.
    """
    F = PaperFill
    O = PaperOrder
    P = PaperPortfolio
    D = DecisionRecord

    conditions = [P.owner_id == user_id]

    if instrument_id is not None:
        conditions.append(O.asset_id == instrument_id)
    if rule_id is not None:
        conditions.append(D.source_rule_id == rule_id)
    if rule_family is not None:
        family_rule_ids = _family_rule_ids(rule_family)
        if family_rule_ids:
            conditions.append(D.source_rule_id.in_(family_rule_ids))
        else:
            conditions.append(false())
    if date_from is not None:
        conditions.append(
            or_(
                and_(F.bar_timestamp.is_(not None), F.bar_timestamp >= _utc_naive(date_from)),
                and_(F.bar_timestamp.is_(None), F.execution_date >= _utc_date_text(date_from)),
            )
        )
    if date_to is not None:
        conditions.append(
            or_(
                and_(F.bar_timestamp.is_(not None), F.bar_timestamp <= _utc_naive(date_to)),
                and_(F.bar_timestamp.is_(None), F.execution_date <= _utc_date_text(date_to)),
            )
        )

    return conditions


_PAPER_SELECT_COLUMNS = (
    PaperFill.id.label("fill_id"),
    PaperFill.paper_order_id.label("order_id"),
    PaperFill.execution_date.label("execution_date"),
    PaperFill.bar_timestamp.label("bar_timestamp"),
    PaperFill.quantity.label("quantity"),
    PaperFill.execution_price_before_costs.label("fill_price"),
    PaperFill.observed_price.label("observed_price"),
    PaperFill.commission_amount.label("commission_amount"),
    PaperFill.slippage_amount.label("slippage_amount"),
    PaperFill.net_cash_change.label("net_cash_change"),
    PaperFill.simulated_fill_reason.label("fill_reason"),
    PaperOrder.asset_id.label("instrument_id"),
    PaperOrder.side.label("side"),
    PaperOrder.order_type.label("order_type"),
    PaperOrder.created_at.label("order_created_at"),
    PaperOrder.decision_id.label("decision_id"),
    DecisionRecord.source_rule_id.label("source_rule_id"),
    DecisionRecord.source_run_id.label("source_run_id"),
    DecisionRecord.decision_type.label("decision_type"),
)


def _paper_stmt(*, user_id: UUID, fetch_limit: int, fetch_offset: int = 0, **filters):
    """One paper query: the filter, the unified order, and a row budget.

    The order is the projection of the merged journal order onto this leg — day
    descending, the instant inside the day, unrecorded instants last, then the
    id — so taking the first N rows here and the first N rows of the research
    leg and merging them is the same page as ordering the whole population and
    slicing it.
    """
    stmt = (
        select(*_PAPER_SELECT_COLUMNS)
        .join(PaperOrder, PaperFill.paper_order_id == PaperOrder.id)
        .join(PaperPortfolio, PaperOrder.portfolio_id == PaperPortfolio.id)
        .outerjoin(DecisionRecord, PaperOrder.decision_id == DecisionRecord.id)
        .where(
            *_paper_conditions(
                user_id=user_id,
                instrument_id=filters.get("instrument_id"),
                rule_id=filters.get("rule_id"),
                rule_family=filters.get("rule_family"),
                date_from=filters.get("date_from"),
                date_to=filters.get("date_to"),
            )
        )
        .order_by(
            _fill_day_key().desc(),
            PaperFill.bar_timestamp.desc().nulls_last(),
            PaperFill.id.desc(),
        )
        .limit(fetch_limit)
        .offset(fetch_offset)
    )
    return stmt


def _paper_row_to_dict(row: Any) -> dict:
    """One fill, in the shape the unified journal shows.

    ``time_precision`` is not decoration: a daily fill knows its day and nothing
    more, and a reader who cannot tell the two apart will read ``execution_date``
    as a midnight that was never observed.
    """
    bar_timestamp = row.bar_timestamp
    return {
        "source": "paper",
        "id": str(row.fill_id),
        "order_id": str(row.order_id),
        "decision_id": str(row.decision_id) if row.decision_id else None,
        "run_id": str(row.source_run_id) if row.source_run_id else None,
        "instrument_id": row.instrument_id,
        "rule_id": row.source_rule_id,
        "rule_family": _rule_family(row.source_rule_id) if row.source_rule_id else None,
        "decision_type": _enum_text(row.decision_type),
        "side": _enum_text(row.side),
        "order_type": _enum_text(row.order_type),
        "time_precision": "bar" if bar_timestamp is not None else "day",
        "entry_time": _iso(bar_timestamp),
        "execution_date": row.execution_date,
        "entry_price": _money(_decimal(row.fill_price)),
        "observed_price": _money(_decimal(row.observed_price)),
        "quantity": _money(_decimal(row.quantity)),
        "commission": _money(_decimal(row.commission_amount)),
        "slippage_cost": _money(_decimal(row.slippage_amount)),
        "net_cash_change": _money(_decimal(row.net_cash_change)),
        # A fill is one side of a round trip. Realised P&L belongs to the
        # position it closes, and nothing here recomputes it per fill, so the
        # cell says "not recorded" instead of being left to read as zero.
        "net_pnl": None,
        "pnl_status": "fill_level_pnl_not_recorded",
        "exit_reason": None,
        "exit_time": None,
        "exit_price": None,
        "bars_held": None,
        "interval_minutes": None,
        "fill_reason": row.fill_reason,
        "created_at": _iso(row.order_created_at),
    }


def list_paper_fills(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[dict]:
    """The paper leg of the unified journal, newest first."""
    rows = db.execute(
        _paper_stmt(
            user_id=user_id,
            fetch_limit=limit,
            fetch_offset=offset,
            instrument_id=instrument_id,
            rule_id=rule_id,
            rule_family=rule_family,
            date_from=date_from,
            date_to=date_to,
        )
    ).all()
    return [_paper_row_to_dict(row) for row in rows]


def count_paper_fills(
    db: Session,
    *,
    user_id: UUID,
    instrument_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    rule_family: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    F = PaperFill
    stmt = (
        select(func.count(F.id))
        .select_from(F)
        .join(PaperOrder, F.paper_order_id == PaperOrder.id)
        .join(PaperPortfolio, PaperOrder.portfolio_id == PaperPortfolio.id)
        .outerjoin(DecisionRecord, PaperOrder.decision_id == DecisionRecord.id)
        .where(
            *_paper_conditions(
                user_id=user_id,
                instrument_id=instrument_id,
                rule_id=rule_id,
                rule_family=rule_family,
                date_from=date_from,
                date_to=date_to,
            )
        )
    )
    return db.execute(stmt).scalar() or 0


# ---------------------------------------------------------------------------
# Research aggregates over the whole filtered population
# ---------------------------------------------------------------------------

def journal_research_aggregates(
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
    """Count, total P&L and win rate over every matching research trade.

    Aggregated in SQL rather than over the rows already fetched, because a
    summary that adds up the current page is a different number from a summary
    of the filter and is labelled as neither. ``strategy_study_trades.net_pnl``
    is NOT NULL, so every counted trade is a measured trade and the win rate's
    denominator has no silent gaps in it.
    """
    T = StrategyStudyTrade
    R = StrategyStudyRun

    stmt = (
        select(
            func.count(T.id),
            func.sum(T.net_pnl),
            func.sum(case((T.net_pnl > 0, 1), else_=0)),
        )
        .join(R, T.run_id == R.id)
        .where(
            *_journal_conditions(
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
        )
    )
    total_count, total_pnl, win_count = db.execute(stmt).one()
    total_count = int(total_count or 0)

    return {
        "research_count": total_count,
        "research_total_pnl": _money(
            _decimal(total_pnl) if total_pnl is not None else Decimal("0")
        ),
        "research_wins": int(win_count or 0),
        # A fraction, not percent points — the trades summary reports points and
        # the frontend gives the two scales different names.
        "research_win_rate": (
            float(int(win_count or 0) / total_count) if total_count else None
        ),
    }


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
        # This is the reachable "no trades match your filter" case. The additive
        # total and the flat equity curve really are 0, but the win rate and the
        # average trade are ratios over a denominator that has no observations:
        # reporting "0.0%" and "0.00" there claims every trade lost. profit_factor
        # below already returns None for its undefined case; these follow it.
        return {
            "total_trades": 0,
            "total_pnl": "0",
            "win_rate": None,
            "avg_trade_pnl": None,
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

    Starting cash comes from the first run's settings_json.starting_cash; when
    that run records none the curve is cumulative net P&L from zero.
    drawdown_pct is None while the running peak is not positive, because a
    percentage of a zero-or-negative peak has no answer.
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

    # Starting cash from the first run encountered; absent means no baseline.
    recorded_cash = trades[0].get("starting_cash")
    equity = _decimal(recorded_cash) if recorded_cash is not None else Decimal("0")
    peak = equity
    result = []

    for t in trades:
        net = _decimal(t["net_pnl"])
        equity += net
        if equity > peak:
            peak = equity
        dd_pct = (peak - equity) / peak * Decimal("100") if peak > 0 else None
        result.append({
            "date": t["entry_time"],
            "equity": _money(equity),
            "drawdown_pct": (
                _money(dd_pct.quantize(Decimal("0.01"))) if dd_pct is not None else None
            ),
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


def journal_trades_csv_export(
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
    """Build the export file and report how much of the filter it left out.

    The body cannot say this about itself: it holds exactly the rows that were
    written, so a filter matching more than ``CSV_EXPORT_ROW_LIMIT`` produces a
    file that looks complete and is not. ``rows_matched`` is counted over the
    same filter with the same predicate the list view uses, and the caller
    carries all four numbers to the client.
    """
    filters = dict(
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
    rows_matched = _count_trades(db, **filters)
    trades = _fetch_rows(
        db,
        **filters,
        sort_by="entry_time",
        sort_dir="asc",
        limit=CSV_EXPORT_ROW_LIMIT,
        offset=0,
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(_JOURNAL_CSV_COLUMNS)
    for t in trades:
        writer.writerow([
            _csv_cell(t.get(col), col) for col in _JOURNAL_CSV_COLUMNS
        ])
    rows_exported = len(trades)
    return {
        "content": buffer.getvalue(),
        "rows_exported": rows_exported,
        "rows_matched": rows_matched,
        "row_limit": CSV_EXPORT_ROW_LIMIT,
        "truncated": rows_exported < rows_matched,
    }


# ---------------------------------------------------------------------------
# Unified journal (research trades + paper fills, one range)
# ---------------------------------------------------------------------------

# Columns both legs are rendered into. A fill and a study trade are different
# objects, so the shared frame is stated here rather than assumed by whichever
# client reads the two sources' JSON side by side.
_UNIFIED_CSV_COLUMNS = (
    "source",
    "id",
    "run_id",
    "order_id",
    "decision_id",
    "instrument_id",
    "rule_id",
    "rule_family",
    "decision_type",
    "side",
    "order_type",
    "time_precision",
    "entry_time",
    "execution_date",
    "exit_time",
    "entry_price",
    "exit_price",
    "observed_price",
    "quantity",
    "gross_pnl",
    "commission",
    "slippage_cost",
    "total_cost",
    "net_cash_change",
    "net_pnl",
    "pnl_status",
    "cash_after",
    "exit_id",
    "exit_reason",
    "outcome",
    "bars_held",
    "interval_minutes",
    "mfe_pct",
    "mae_pct",
    "fill_reason",
    "created_at",
)

_UNIFIED_CSV_NUMERIC_COLUMNS = frozenset({
    "entry_price",
    "exit_price",
    "observed_price",
    "quantity",
    "gross_pnl",
    "commission",
    "slippage_cost",
    "total_cost",
    "net_cash_change",
    "net_pnl",
    "cash_after",
    "bars_held",
    "interval_minutes",
    "mfe_pct",
    "mae_pct",
})

# ``_csv_cell`` decides number-vs-text by column name, so the unified export has
# to register its numeric columns with the guard rather than rely on the
# research export's set having picked up the same names.
_JOURNAL_CSV_NUMERIC_COLUMNS = _JOURNAL_CSV_NUMERIC_COLUMNS | _UNIFIED_CSV_NUMERIC_COLUMNS


def _research_row_to_unified(trade: dict) -> dict:
    """One research trade widened into the unified frame.

    The keys the paper leg has no answer for are sent as null rather than
    omitted: a client that sees the key missing cannot tell "this source does
    not record it" from "this record is incomplete".
    """
    return {
        "source": "research",
        "id": trade["trade_id"],
        "run_id": trade["run_id"],
        "order_id": None,
        "decision_id": None,
        "instrument_id": trade["instrument_id"],
        "rule_id": trade["rule_id"],
        "rule_family": trade["rule_family"],
        "decision_type": None,
        "side": trade["direction"],
        "order_type": None,
        "time_precision": "bar",
        "entry_time": trade["entry_time"],
        "execution_date": None,
        "exit_time": trade["exit_time"],
        "entry_price": trade["entry_price"],
        "exit_price": trade["exit_price"],
        "observed_price": None,
        "quantity": trade["quantity"],
        "gross_pnl": trade["gross_pnl"],
        "commission": trade["commission"],
        "slippage_cost": trade["slippage_cost"],
        "total_cost": trade["total_cost"],
        "net_cash_change": None,
        "net_pnl": trade["net_pnl"],
        "pnl_status": "settled",
        "cash_after": trade["cash_after"],
        "exit_id": trade["exit_id"],
        "exit_reason": trade["exit_reason"],
        "outcome": trade["outcome"],
        "bars_held": trade["bars_held"],
        "interval_minutes": trade["interval_minutes"],
        "mfe_pct": trade["mfe_pct"],
        "mae_pct": trade["mae_pct"],
        "fill_reason": None,
        "created_at": None,
    }


def unified_sort_key(row: dict) -> tuple:
    """The one ordering the unified journal uses, for rows from either leg.

    Every part is a fixed-width UTC string, so a lexicographic comparison is a
    chronological one across sources: both legs render their instant through
    ``_iso``, and a fill with no instant takes part through ``execution_date``,
    which is the same text up to the day. Within one day a fill that recorded
    no clock time sorts after the ones that did — the day is all it claims, so
    placing it before instants it cannot compare against would be inventing an
    order. The source and id break the last ties, which makes the whole
    sequence deterministic and a page stable between requests.
    """
    instant = row["entry_time"] or row["execution_date"] or ""
    return (instant[:10], instant, row["source"], row["id"])


_UNIFIED_RESEARCH_FILTERS = (
    "instrument_id",
    "rule_id",
    "rule_family",
    "interval_minutes",
    "date_from",
    "date_to",
    "pnl_min",
    "pnl_max",
    "exit_reason",
)

_UNIFIED_PAPER_FILTERS = (
    "instrument_id",
    "rule_id",
    "rule_family",
    "date_from",
    "date_to",
)


def _split_unified_filters(**filters: Any) -> tuple:
    """Separate the keys each leg can honour from the ones it cannot."""
    research = {name: filters.get(name) for name in _UNIFIED_RESEARCH_FILTERS}
    paper = {name: filters.get(name) for name in _UNIFIED_PAPER_FILTERS}
    return research, paper


def _merge_unified_rows(
    db: Session,
    *,
    source: str,
    user_id: UUID,
    budget: int,
    filters: dict,
) -> List[dict]:
    """The first ``budget`` unified rows of the filtered population, newest first.

    Each leg is read to the same budget and merged, which is the standard
    k-way-merge page: a row inside the first ``budget`` of the merged sequence
    is necessarily inside the first ``budget`` of its own leg, so no page is
    assembled from rows the other leg had already skipped. The per-leg order is
    the projection of ``unified_sort_key`` onto that leg, so this and a
    sort-then-slice of the whole population agree.
    """
    research_filters, paper_filters = _split_unified_filters(**filters)
    rows: List[dict] = []

    if source in ("all", "research"):
        rows.extend(
            _research_row_to_unified(trade)
            for trade in list_journal_trades(
                db,
                user_id=user_id,
                **research_filters,
                sort_by="entry_time",
                sort_dir="desc",
                limit=budget,
                offset=0,
            )
        )
    if source in ("all", "paper"):
        rows.extend(
            list_paper_fills(
                db,
                user_id=user_id,
                **paper_filters,
                limit=budget,
                offset=0,
            )
        )

    rows.sort(key=unified_sort_key, reverse=True)
    return rows


def unified_limitations(source: str, filters: dict) -> List[str]:
    """What this response did not do, in the caller's own vocabulary.

    A filter that is silently narrower than it reads is worse than one that is
    refused: the caller has no way to notice. These sentences are generated
    from the request, so a caller that asked for nothing sees nothing.
    """
    notes: List[str] = []
    ignored = [name for name in PAPER_INAPPLICABLE_FILTERS if filters.get(name) is not None]
    if source in ("all", "paper") and ignored:
        notes.append(
            f"{', '.join(ignored)} filtered the research leg only: a paper fill "
            "has no settled P&L, no run interval and no exit reason, so the "
            "paper rows in this response carry no trace of that filter."
        )
    if source in ("all", "paper") and (
        filters.get("date_from") is not None or filters.get("date_to") is not None
    ):
        notes.append(
            "Date bounds reach a fill that recorded a bar timestamp at the "
            "instant and a daily fill only at the calendar day, because the day "
            "is the finest time that fill stores."
        )
    if source in ("all", "paper"):
        notes.append(
            "Paper rows send net_pnl as null with pnl_status "
            "'fill_level_pnl_not_recorded': a fill is one leg of a round trip "
            "and realised P&L is settled per position, so no per-fill figure "
            "was computed here."
        )
    return notes


def unified_journal_page(
    db: Session,
    *,
    user_id: UUID,
    source: str = "all",
    limit: int = 200,
    offset: int = 0,
    **filters: Any,
) -> dict:
    """One page of the merged journal, plus the summary of the whole filter.

    The page and the population are reported separately because they are two
    different claims. Counting the rows on this page and labelling it a summary
    of the filter makes ``research_count`` change when the caller changes
    ``limit``, and a win rate over twenty of two hundred trades is that
    number's only plausible misreading.
    """
    research_filters, paper_filters = _split_unified_filters(**filters)
    budget = offset + limit

    page_rows = _merge_unified_rows(
        db, source=source, user_id=user_id, budget=budget, filters=filters
    )[offset:offset + limit]

    research_agg: Optional[dict] = None
    if source in ("all", "research"):
        research_agg = journal_research_aggregates(db, user_id=user_id, **research_filters)

    paper_count: Optional[int] = None
    if source in ("all", "paper"):
        paper_count = count_paper_fills(db, user_id=user_id, **paper_filters)

    research_count = research_agg["research_count"] if research_agg else None
    total_count = (research_count or 0) + (paper_count or 0)

    applied = {
        "source": source,
        "instrument_id": filters.get("instrument_id"),
        "rule_id": filters.get("rule_id"),
        "rule_family": filters.get("rule_family"),
        "interval_minutes": filters.get("interval_minutes"),
        "date_from": _iso(filters["date_from"]) if filters.get("date_from") else None,
        "date_to": _iso(filters["date_to"]) if filters.get("date_to") else None,
        "pnl_min": _money(filters["pnl_min"]) if filters.get("pnl_min") is not None else None,
        "pnl_max": _money(filters["pnl_max"]) if filters.get("pnl_max") is not None else None,
        "exit_reason": filters.get("exit_reason"),
    }

    return {
        "trades": page_rows,
        "summary": {
            "total_count": total_count,
            "research_count": research_count,
            "paper_count": paper_count,
            "research_total_pnl": research_agg["research_total_pnl"] if research_agg else None,
            "research_wins": research_agg["research_wins"] if research_agg else None,
            "research_win_rate": research_agg["research_win_rate"] if research_agg else None,
        },
        "page": {
            "returned": len(page_rows),
            "limit": limit,
            "offset": offset,
            "order": "unified_sort_key descending",
        },
        "filter_scope": {
            "research": list(_UNIFIED_RESEARCH_FILTERS),
            "paper": list(_UNIFIED_PAPER_FILTERS),
            "ignored_for_paper": [
                name for name in PAPER_INAPPLICABLE_FILTERS
                if filters.get(name) is not None
            ],
        },
        "limitations": unified_limitations(source, filters),
        "filters_applied": applied,
    }


def unified_journal_csv_export(
    db: Session,
    *,
    user_id: UUID,
    source: str = "all",
    **filters: Any,
) -> dict:
    """Export the merged journal over the same range the list view shows.

    The unified tab's Export button used to hand the caller the research-only
    export while the table beside it listed paper fills, so the file and the
    screen were two different filters under one label. This shares
    ``_merge_unified_rows`` and the counts with the page.
    """
    rows = _merge_unified_rows(
        db,
        source=source,
        user_id=user_id,
        budget=CSV_EXPORT_ROW_LIMIT,
        filters=filters,
    )[:CSV_EXPORT_ROW_LIMIT]

    research_filters, paper_filters = _split_unified_filters(**filters)
    rows_matched = 0
    if source in ("all", "research"):
        rows_matched += _count_trades(db, user_id=user_id, **research_filters)
    if source in ("all", "paper"):
        rows_matched += count_paper_fills(db, user_id=user_id, **paper_filters)

    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(_UNIFIED_CSV_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(row.get(col), col) for col in _UNIFIED_CSV_COLUMNS])

    rows_exported = len(rows)
    return {
        "content": buffer.getvalue(),
        "rows_exported": rows_exported,
        "rows_matched": rows_matched,
        "row_limit": CSV_EXPORT_ROW_LIMIT,
        "truncated": rows_exported < rows_matched,
        "source": source,
    }
