"""Intraday seasonality analysis for crypto assets.

Tests whether specific hours-of-day or days-of-week carry systematically
positive or negative returns.  All statistical claims carry multiple-testing
corrections (Bonferroni and Benjamini-Hochberg FDR) because scanning 24 hours,
7 weekdays and 168 hour×weekday slots without correction guarantees false
positives.

Stage 1 is purely diagnostic: it reports which slots *look* significant so the
owner can decide whether to build a trading rule around them (Stage 2).

Data source is always live Kraken OHLCV; the engine never fabricates bars.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from scipy import stats as sp_stats

from app.providers.crypto_registry import get_crypto_provider
from app.services.crypto_series import CandleSeries

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class SlotStat:
    """One time slot's return statistics."""
    key: int
    label: str
    avg_return_pct: float
    std_return_pct: float
    n_bars: int
    t_stat: float
    p_value: float
    bonferroni_significant: bool = False
    fdr_significant: bool = False


@dataclass
class SeasonalityResult:
    """Full seasonality analysis for one instrument/interval."""
    instrument_id: str
    interval_minutes: int
    bars: int
    by_hour: List[SlotStat]
    by_day_of_week: List[SlotStat]
    by_hour_x_day: List[SlotStat]
    significant_slots: Dict[str, object] = field(default_factory=dict)
    dataset_provenance: Dict[str, object] = field(default_factory=dict)


def _compute_returns(series: CandleSeries) -> List[tuple[datetime, float]]:
    """Per-bar log returns as (open_time, return_pct).

    Skips the first bar (no prior close for comparison).
    """
    returns: List[tuple[datetime, float]] = []
    prev_close: Optional[Decimal] = None

    for candle in series.completed:
        if prev_close is not None and prev_close > ZERO:
            ret = float((candle.close - prev_close) / prev_close * HUNDRED)
            returns.append((candle.open_time, ret))
        prev_close = candle.close

    return returns


def _group_returns(
    returns: List[tuple[datetime, float]],
    key_fn,
    n_slots: int,
    label_fn,
) -> List[SlotStat]:
    """Group returns by a key function and compute per-slot statistics."""
    groups: Dict[int, List[float]] = {i: [] for i in range(n_slots)}

    for ts, ret in returns:
        k = key_fn(ts)
        if 0 <= k < n_slots:
            groups[k].append(ret)

    stats: List[SlotStat] = []
    for i in range(n_slots):
        vals = groups[i]
        n = len(vals)
        label = label_fn(i)

        if n < 2:
            stats.append(SlotStat(
                key=i, label=label,
                avg_return_pct=0.0, std_return_pct=0.0,
                n_bars=n, t_stat=0.0, p_value=1.0,
            ))
            continue

        mean_r = sum(vals) / n
        var_r = sum((v - mean_r) ** 2 for v in vals) / (n - 1)
        std_r = math.sqrt(var_r) if var_r > 0 else 0.0

        if std_r > 1e-12:
            t_stat, p_value = sp_stats.ttest_1samp(vals, 0.0, alternative="two-sided")
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = 0.0
            p_value = 1.0

        stats.append(SlotStat(
            key=i, label=label,
            avg_return_pct=round(mean_r, 6),
            std_return_pct=round(std_r, 6),
            n_bars=n,
            t_stat=round(t_stat, 4),
            p_value=round(p_value, 6),
        ))

    return stats


def _apply_bonferroni(slots: List[SlotStat], alpha: float = 0.05) -> None:
    """Mark slots significant under Bonferroni correction."""
    n = len(slots)
    if n == 0:
        return
    threshold = alpha / n
    for s in slots:
        s.bonferroni_significant = s.p_value < threshold


def _apply_bh_fdr(slots: List[SlotStat], q: float = 0.05) -> None:
    """Benjamini-Hochberg FDR correction.

    Sort by p-value ascending, find the largest k where p_(k) <= (k/m)*q,
    then reject all hypotheses 1..k.
    """
    m = len(slots)
    if m == 0:
        return

    indexed = sorted(enumerate(slots), key=lambda pair: pair[1].p_value)

    cutoff_idx = -1
    for rank, (_, slot) in enumerate(indexed, start=1):
        critical = (rank / m) * q
        if slot.p_value <= critical:
            cutoff_idx = rank

    for i, slot in indexed:
        slot.fdr_significant = False

    if cutoff_idx >= 0:
        for _, slot in indexed[:cutoff_idx]:
            slot.fdr_significant = True


def _build_significant_slots(
    by_hour: List[SlotStat],
    by_day: List[SlotStat],
    by_hour_x_day: List[SlotStat],
) -> Dict[str, object]:
    """Summarize which slots survive multiple-testing correction."""
    return {
        "bonferroni_threshold": round(0.05 / len(by_hour_x_day), 6) if by_hour_x_day else None,
        "fdr_q": 0.05,
        "significant_hours_bonferroni": [s.key for s in by_hour if s.bonferroni_significant],
        "significant_hours_fdr": [s.key for s in by_hour if s.fdr_significant],
        "significant_days_bonferroni": [s.key for s in by_day if s.bonferroni_significant],
        "significant_days_fdr": [s.key for s in by_day if s.fdr_significant],
        "significant_hour_x_day_bonferroni": [
            {"day": s.key // 24, "hour": s.key % 24}
            for s in by_hour_x_day if s.bonferroni_significant
        ],
        "significant_hour_x_day_fdr": [
            {"day": s.key // 24, "hour": s.key % 24}
            for s in by_hour_x_day if s.fdr_significant
        ],
    }


def analyze_seasonality(
    instrument_id: str = "CRYPTO:KRAKEN:BTC-USD",
    interval_minutes: int = 60,
    limit: int = 720,
) -> SeasonalityResult:
    """Run full seasonality analysis on one instrument.

    Fetches live Kraken OHLCV, computes per-bar returns, groups by hour/day,
    and applies Bonferroni + BH-FDR corrections.
    """
    provider = get_crypto_provider()
    series = provider.get_candle_series(
        instrument_id,
        interval_minutes=interval_minutes,
        limit=limit,
    )

    returns = _compute_returns(series)

    if len(returns) < 10:
        return SeasonalityResult(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            bars=len(series.completed),
            by_hour=[],
            by_day_of_week=[],
            by_hour_x_day=[],
            significant_slots={"error": "insufficient_returns", "n": len(returns)},
            dataset_provenance={
                "instrument_id": instrument_id,
                "bars": len(series.completed),
                "fingerprint": series.fingerprint,
                "integrity_ok": series.integrity_ok,
                "data_nature": "live",
                "provider_id": "kraken",
            },
        )

    by_hour = _group_returns(
        returns,
        key_fn=lambda ts: ts.hour,
        n_slots=24,
        label_fn=lambda i: f"{i:02d}:00 UTC",
    )

    by_day = _group_returns(
        returns,
        key_fn=lambda ts: ts.weekday(),
        n_slots=7,
        label_fn=lambda i: DAY_NAMES[i],
    )

    by_hour_x_day = _group_returns(
        returns,
        key_fn=lambda ts: ts.weekday() * 24 + ts.hour,
        n_slots=168,
        label_fn=lambda i: f"{DAY_NAMES[i // 24]} {i % 24:02d}:00",
    )

    _apply_bonferroni(by_hour)
    _apply_bonferroni(by_day)
    _apply_bonferroni(by_hour_x_day)
    _apply_bh_fdr(by_hour)
    _apply_bh_fdr(by_day)
    _apply_bh_fdr(by_hour_x_day)

    sig = _build_significant_slots(by_hour, by_day, by_hour_x_day)

    provenance = {
        "instrument_id": instrument_id,
        "bars": len(series.completed),
        "returns_computed": len(returns),
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "first_bar_time": series.completed[0].open_time.isoformat() if series.completed else None,
        "last_bar_time": series.completed[-1].open_time.isoformat() if series.completed else None,
        "data_nature": "live",
        "provider_id": "kraken",
    }

    return SeasonalityResult(
        instrument_id=instrument_id,
        interval_minutes=interval_minutes,
        bars=len(series.completed),
        by_hour=by_hour,
        by_day_of_week=by_day,
        by_hour_x_day=by_hour_x_day,
        significant_slots=sig,
        dataset_provenance=provenance,
    )


def _slot_to_dict(s: SlotStat) -> dict:
    return {
        "key": s.key,
        "label": s.label,
        "avg_return_pct": s.avg_return_pct,
        "std_return_pct": s.std_return_pct,
        "n_bars": s.n_bars,
        "t_stat": s.t_stat,
        "p_value": s.p_value,
        "bonferroni_significant": s.bonferroni_significant,
        "fdr_significant": s.fdr_significant,
    }


def result_to_dict(result: SeasonalityResult) -> dict:
    """Serialize SeasonalityResult to JSON-safe dict."""
    return {
        "lane": "seasonality_analysis",
        "research_version": "1.0.0",
        "instrument_id": result.instrument_id,
        "interval_minutes": result.interval_minutes,
        "bars": result.bars,
        "by_hour": [_slot_to_dict(s) for s in result.by_hour],
        "by_day_of_week": [_slot_to_dict(s) for s in result.by_day_of_week],
        "by_hour_x_day": [_slot_to_dict(s) for s in result.by_hour_x_day],
        "significant_slots": result.significant_slots,
        "dataset": result.dataset_provenance,
    }
