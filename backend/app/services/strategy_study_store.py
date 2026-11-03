"""Durable, owner-scoped records for the strategy research lane.

The research engine is pure: it turns completed venue bars into a study. This
module is the review surface on top of it. A completed study keeps the exact
bars it read, the settings it used, and every out-of-sample buy/sell with its
prices, costs, exit rule and a mechanical earn/loss explanation.

Nothing here is a broker order, a real-money fill or a profitability claim.
The lane stays ``strategy_research_simulated``; the rows exist so the customer
can review, export and re-check what the simulation did.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.services.strategy_lab import LabBar
from app.services.strategy_research import LANE

TRADE_COLUMNS = (
    "trade_index",
    "fold",
    "direction",
    "rule_id",
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

# Same guard as the paper execution export: a spreadsheet must not evaluate
# user-visible text as a formula.
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


def _dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bar_payload(bar: LabBar) -> dict:
    return {
        "open_time": _iso(bar.open_time),
        "interval_minutes": bar.interval_minutes,
        "open": format(bar.open, "f"),
        "high": format(bar.high, "f"),
        "low": format(bar.low, "f"),
        "close": format(bar.close, "f"),
        "volume": format(bar.volume, "f"),
    }


# -- persistence ------------------------------------------------------------


def persist_study(
    db: Session,
    *,
    user_id: UUID,
    result: Dict[str, Any],
    bars: Sequence[LabBar],
    dataset: Dict[str, Any],
) -> StrategyStudyRun:
    """Store one completed study and its out-of-sample trades.

    ``result`` must come from ``run_study(..., include_trades=True)``: the
    durable record is the walk-forward evidence block, not the in-sample grid.
    """
    trade_rows = result.get("out_of_sample_trades")
    if not isinstance(trade_rows, list):
        raise ValueError("study result must include out_of_sample_trades to be stored")
    if not bars:
        raise ValueError("study result has no bars to store")

    run = StrategyStudyRun(
        user_id=user_id,
        lane=str(result.get("lane") or LANE),
        research_version=str(result.get("research_version") or "unknown"),
        instrument_id=str(dataset["instrument_id"]),
        interval_minutes=int(dataset["interval_minutes"]),
        provider_id=str(dataset["provider_id"]),
        data_nature=str(dataset.get("data_nature") or "unknown"),
        bar_count=len(bars),
        first_open_time=_dt(bars[0].open_time),
        last_open_time=_dt(bars[-1].open_time),
        fetched_at=_dt(dataset.get("fetched_at")),
        dataset_fingerprint=dataset.get("fingerprint"),
        dataset_integrity_ok=dataset.get("integrity_ok"),
        dataset_gap_count=dataset.get("gap_count"),
        dataset_missing_intervals=dataset.get("missing_intervals"),
        settings_json=result["settings"],
        walk_forward_json=result["walk_forward"],
        highlights_json=result.get("highlights"),
        disclosures_json=result.get("disclosures"),
        bars_json=[_bar_payload(bar) for bar in bars],
        trade_count=len(trade_rows),
    )

    try:
        db.add(run)
        db.flush()
        for index, row in enumerate(trade_rows):
            net_pnl = _decimal(row["net_pnl"])
            outcome = row.get("outcome")
            if outcome is None:
                outcome = "win" if net_pnl > 0 else "loss" if net_pnl < 0 else "flat"
            db.add(
                StrategyStudyTrade(
                    run_id=run.id,
                    trade_index=index,
                    fold=row.get("fold"),
                    rule_id=str(row["rule_id"]),
                    rule_label_zh=row.get("rule_label_zh"),
                    exit_id=str(row["exit_id"]),
                    exit_label_zh=row.get("exit_label_zh"),
                    direction=str(row["direction"]),
                    exit_reason=str(row["exit_reason"]),
                    outcome=str(outcome),
                    signal_time=_dt(row["signal_time"]),
                    entry_time=_dt(row["entry_time"]),
                    exit_time=_dt(row["exit_time"]),
                    entry_price=_decimal(row["entry_price"]),
                    exit_price=_decimal(row["exit_price"]),
                    quantity=_decimal(row["quantity"]),
                    gross_pnl=_decimal(row["gross_pnl"]),
                    commission=_decimal(row["commission"]),
                    slippage_cost=_decimal(row["slippage_cost"]),
                    total_cost=_decimal(row["total_cost"]),
                    net_pnl=net_pnl,
                    cash_after=_decimal(row["cash_after"]),
                    bars_held=int(row["bars_held"]),
                    mfe_pct=_decimal(row["mfe_pct"]) if row.get("mfe_pct") is not None else None,
                    mae_pct=_decimal(row["mae_pct"]) if row.get("mae_pct") is not None else None,
                    cost_ratio_pct=(
                        _decimal(row["cost_ratio_pct"])
                        if row.get("cost_ratio_pct") is not None
                        else None
                    ),
                    pnl_reason_zh=str(row["pnl_reason_zh"]),
                    pnl_reason_en=str(row["pnl_reason_en"]),
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(run)
    return run


# -- reads ------------------------------------------------------------------


def list_studies(
    db: Session,
    *,
    user_id: UUID,
    limit: int = 20,
    instrument_id: Optional[str] = None,
    interval_minutes: Optional[int] = None,
) -> List[StrategyStudyRun]:
    statement = select(StrategyStudyRun).where(StrategyStudyRun.user_id == user_id)
    if instrument_id:
        statement = statement.where(StrategyStudyRun.instrument_id == instrument_id)
    if interval_minutes is not None:
        statement = statement.where(
            StrategyStudyRun.interval_minutes == interval_minutes
        )
    statement = statement.order_by(
        StrategyStudyRun.created_at.desc(), StrategyStudyRun.id.desc()
    ).limit(limit)
    return list(db.scalars(statement).all())


def get_study(
    db: Session, *, user_id: UUID, run_id: UUID
) -> Optional[StrategyStudyRun]:
    statement = (
        select(StrategyStudyRun)
        .options(selectinload(StrategyStudyRun.trades))
        .where(StrategyStudyRun.user_id == user_id, StrategyStudyRun.id == run_id)
    )
    return db.scalars(statement).first()


# -- serializers ------------------------------------------------------------


def trade_payload(trade: StrategyStudyTrade) -> dict:
    return {
        "trade_index": trade.trade_index,
        "fold": trade.fold,
        "direction": trade.direction,
        "rule_id": trade.rule_id,
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


def run_summary(run: StrategyStudyRun) -> dict:
    return {
        "id": str(run.id),
        "created_at": _iso(run.created_at),
        "lane": run.lane,
        "research_version": run.research_version,
        "instrument_id": run.instrument_id,
        "interval_minutes": run.interval_minutes,
        "provider_id": run.provider_id,
        "data_nature": run.data_nature,
        "bar_count": run.bar_count,
        "first_open_time": _iso(run.first_open_time),
        "last_open_time": _iso(run.last_open_time),
        "fetched_at": _iso(run.fetched_at),
        "dataset_fingerprint": run.dataset_fingerprint,
        "dataset_integrity_ok": run.dataset_integrity_ok,
        "dataset_gap_count": run.dataset_gap_count,
        "dataset_missing_intervals": run.dataset_missing_intervals,
        "settings": run.settings_json,
        "walk_forward": run.walk_forward_json,
        "highlights": run.highlights_json,
        "trade_count": run.trade_count,
        "bars_available": bool(run.bars_json),
        "simulated": True,
        "orders_supported": False,
    }


def run_detail(run: StrategyStudyRun, *, include_bars: bool = False) -> dict:
    payload = run_summary(run)
    payload["disclosures"] = run.disclosures_json or []
    payload["trades"] = [trade_payload(trade) for trade in run.trades]
    if include_bars:
        payload["bars"] = run.bars_json or []
    return payload


# -- export -----------------------------------------------------------------


def trades_csv(run: StrategyStudyRun) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(TRADE_COLUMNS)
    for trade in run.trades:
        writer.writerow(
            [
                trade.trade_index,
                trade.fold,
                trade.direction,
                trade.rule_id,
                trade.rule_label_zh,
                trade.exit_id,
                trade.exit_label_zh,
                trade.exit_reason,
                trade.outcome,
                _iso(trade.signal_time),
                _iso(trade.entry_time),
                _iso(trade.exit_time),
                trade.entry_price,
                trade.exit_price,
                trade.quantity,
                trade.gross_pnl,
                trade.commission,
                trade.slippage_cost,
                trade.total_cost,
                trade.net_pnl,
                trade.cash_after,
                trade.bars_held,
                trade.mfe_pct,
                trade.mae_pct,
                trade.cost_ratio_pct,
                trade.pnl_reason_en,
                trade.pnl_reason_zh,
            ]
        )
    return buffer.getvalue()


__all__ = [
    "TRADE_COLUMNS",
    "get_study",
    "list_studies",
    "persist_study",
    "run_detail",
    "run_summary",
    "trade_payload",
    "trades_csv",
]
