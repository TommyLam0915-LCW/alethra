"""Pattern analysis for the auto-learning loop.

Aggregates trades from stored strategy-study runs, identifies performance
patterns by exit config and market regime, proposes parameter adjustments,
and validates them with walk-forward backtests before the owner may deploy.

Nothing here auto-deploys. A proposal that passes validation is marked
``validated`` and waits for the owner's explicit deploy decision.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun, StrategyStudyTrade
from app.services.strategy_research import (
    EXIT_BY_ID,
    EXIT_CONFIGS,
    ExitConfig,
    LabBar,
    ResearchSettings,
    RULE_BY_ID,
    RULES,
    _simulate_range,
    fold_bounds,
    prepare_indicators,
    summarize,
    WARMUP_BARS,
)

logger = logging.getLogger(__name__)

ZERO = Decimal(0)
DEPLOY_CRITERIA = {
    "sharpe_improvement_min": Decimal("0.2"),
    "max_drawdown_decrease_min_pct": Decimal("10.0"),
}


def _decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


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


def _bars_from_json(bars_json: list, interval_minutes: int) -> List[LabBar]:
    """Reconstruct LabBar list from stored bars_json."""
    from app.services.strategy_lab import LabBar

    bars: List[LabBar] = []
    for row in bars_json:
        bars.append(
            LabBar(
                open_time=_dt(row["open_time"]),
                interval_minutes=interval_minutes,
                open=_decimal(row["open"]),
                high=_decimal(row["high"]),
                low=_decimal(row["low"]),
                close=_decimal(row["close"]),
                volume=_decimal(row["volume"]),
            )
        )
    return bars


def _compute_sharpe(trades: Sequence[StrategyStudyTrade]) -> Optional[Decimal]:
    """Compute Sharpe-like ratio: mean(net_pnl) / stdev(net_pnl)."""
    if len(trades) < 2:
        return None
    pnls = [_decimal(t.net_pnl) for t in trades]
    mean = sum(pnls, ZERO) / Decimal(len(pnls))
    variance = sum((p - mean) ** 2 for p in pnls) / Decimal(len(pnls) - 1)
    stdev = variance.sqrt() if variance >= 0 else ZERO
    if stdev == ZERO:
        return None
    return mean / stdev


def _compute_max_drawdown(trades: Sequence[StrategyStudyTrade]) -> Decimal:
    """Compute max drawdown from trade cash_after series."""
    if not trades:
        return ZERO
    peak = _decimal(trades[0].cash_after)
    max_dd = ZERO
    for t in trades:
        cash = _decimal(t.cash_after)
        if cash > peak:
            peak = cash
        if peak > ZERO:
            dd = (peak - cash) / peak * Decimal(100)
            if dd > max_dd:
                max_dd = dd
    return max_dd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_skill_patterns(
    db: Session,
    *,
    user_id: UUID,
    lookback_runs: int = 50,
) -> dict:
    """Aggregate trades by (rule_id, exit_id, instrument_id, interval_minutes).

    Returns per-exit-config metrics, per-skill-instrument metrics, a market
    regime analysis, and a list of parameter-adjustment recommendations.
    """
    # Fetch recent runs for this user
    stmt = (
        select(StrategyStudyRun)
        .where(StrategyStudyRun.user_id == user_id)
        .order_by(StrategyStudyRun.created_at.desc())
        .limit(lookback_runs)
    )
    runs = list(db.scalars(stmt).all())
    if not runs:
        return {
            "per_exit_config": {},
            "per_skill_instrument": {},
            "market_regime_analysis": {},
            "recommendations": [],
            "runs_analyzed": 0,
        }

    # Group trades by exit config
    exit_trades: Dict[str, List[StrategyStudyTrade]] = defaultdict(list)
    # Group trades by (rule_id, instrument_id)
    skill_inst_trades: Dict[tuple, List[StrategyStudyTrade]] = defaultdict(list)
    # Group runs by trend strength for regime analysis
    regime_runs: Dict[str, List[StrategyStudyRun]] = defaultdict(list)

    for run in runs:
        # Determine market regime from trend_strength in bars
        regime = _classify_regime(run)
        regime_runs[regime].append(run)

        for trade in run.trades:
            exit_trades[trade.exit_id].append(trade)
            key = (trade.rule_id, run.instrument_id)
            skill_inst_trades[key].append(trade)

    # Compute per-exit-config metrics
    per_exit_config: Dict[str, dict] = {}
    for exit_id, trades in exit_trades.items():
        wins = [t for t in trades if _decimal(t.net_pnl) > ZERO]
        per_exit_config[exit_id] = {
            "win_rate": float(len(wins)) / len(trades) if trades else 0,
            "avg_pnl": float(
                sum(_decimal(t.net_pnl) for t in trades) / Decimal(len(trades))
            )
            if trades
            else 0,
            "trade_count": len(trades),
            "sharpe": float(_compute_sharpe(trades))
            if _compute_sharpe(trades) is not None
            else None,
        }

    # Compute per-skill-instrument metrics
    per_skill_instrument: Dict[str, dict] = {}
    for (rule_id, instrument_id), trades in skill_inst_trades.items():
        wins = [t for t in trades if _decimal(t.net_pnl) > ZERO]
        per_skill_instrument[f"{rule_id}|{instrument_id}"] = {
            "win_rate": float(len(wins)) / len(trades) if trades else 0,
            "avg_pnl": float(
                sum(_decimal(t.net_pnl) for t in trades) / Decimal(len(trades))
            )
            if trades
            else 0,
            "trade_count": len(trades),
            "sharpe": float(_compute_sharpe(trades))
            if _compute_sharpe(trades) is not None
            else None,
        }

    # Market regime analysis
    market_regime_analysis: Dict[str, dict] = {}
    for regime, regime_run_list in regime_runs.items():
        regime_trades: List[StrategyStudyTrade] = []
        for r in regime_run_list:
            regime_trades.extend(r.trades)
        if regime_trades:
            wins = [t for t in regime_trades if _decimal(t.net_pnl) > ZERO]
            market_regime_analysis[regime] = {
                "run_count": len(regime_run_list),
                "trade_count": len(regime_trades),
                "win_rate": float(len(wins)) / len(regime_trades),
                "avg_pnl": float(
                    sum(_decimal(t.net_pnl) for t in regime_trades)
                    / Decimal(len(regime_trades))
                ),
            }

    # Generate recommendations
    recommendations = _generate_recommendations(per_exit_config, per_skill_instrument)

    return {
        "per_exit_config": per_exit_config,
        "per_skill_instrument": per_skill_instrument,
        "market_regime_analysis": market_regime_analysis,
        "recommendations": recommendations,
        "runs_analyzed": len(runs),
    }


def _classify_regime(run: StrategyStudyRun) -> str:
    """Classify a run's market regime from price action in bars.

    High net price displacement relative to the total range = trending.
    Low net displacement relative to the range = ranging.
    """
    if not run.bars_json:
        return "unknown"
    bars = run.bars_json
    if len(bars) < 10:
        return "unknown"
    closes = [_decimal(b["close"]) for b in bars]
    if not closes:
        return "unknown"
    high = max(closes)
    low = min(closes)
    total_range = high - low
    if total_range == ZERO:
        return "ranging"
    # Net displacement: how far did price travel from start to end?
    net_displacement = abs(closes[-1] - closes[0])
    # If net displacement is a large fraction of total range, it's trending
    ratio = net_displacement / total_range
    if ratio > Decimal("0.5"):
        return "trending"
    return "ranging"


def _generate_recommendations(
    per_exit_config: Dict[str, dict],
    per_skill_instrument: Dict[str, dict],
) -> List[dict]:
    """Generate parameter adjustment recommendations from performance data.
    
    Evidence gating:
    - trade_count < 30: insufficient_evidence (no actionable recommendation)
    - trade_count >= 30 AND sharpe < 0: parameter_adjustment (evidence-supported)
    
    These are mutually exclusive — a config cannot be both "insufficient evidence"
    and "evidence-supported adjustment" at the same time.
    """
    recommendations: List[dict] = []

    for exit_id, metrics in per_exit_config.items():
        trade_count = metrics.get("trade_count", 0)
        sharpe = metrics.get("sharpe")
        
        # Insufficient evidence: small sample size
        if trade_count < 30:
            recommendations.append(
                {
                    "type": "insufficient_evidence",
                    "exit_id": exit_id,
                    "reason": (
                        f"Exit config {exit_id} has only {trade_count} trades. "
                        f"Cannot draw reliable conclusions from small samples. "
                        f"Need >= 30 trades for evidence-based recommendations."
                    ),
                    "metrics": metrics,
                    "evidence_status": "insufficient",
                }
            )
            # Do NOT generate parameter_adjustment for insufficient evidence
            continue
        
        # Sufficient evidence: can recommend parameter adjustment
        if sharpe is not None and sharpe < 0:
            recommendations.append(
                {
                    "type": "parameter_adjustment",
                    "exit_id": exit_id,
                    "reason": (
                        f"Exit config {exit_id} has negative Sharpe ({sharpe:.2f}) "
                        f"with {trade_count} trades. Parameter adjustment recommended."
                    ),
                    "metrics": metrics,
                    "evidence_status": "sufficient",
                }
            )

    return recommendations


def propose_parameter_adjustment(
    *,
    current_exit: ExitConfig,
    performance_data: dict,
) -> Optional[dict]:
    """Given performance data for an exit config, propose interpolation.

    If the exit config has poor metrics, suggest adjusting target_atr toward
    a neighboring config that performs better. Returns None if no clear
    improvement direction exists.
    
    Evidence gating:
    - trade_count < 30: returns None (insufficient evidence)
    - trade_count >= 30: may return a proposal with evidence_status
    """
    trade_count = performance_data.get("trade_count", 0)
    
    # Insufficient evidence: cannot make reliable proposals
    if trade_count < 30:
        return None

    win_rate = performance_data.get("win_rate", 0)
    sharpe = performance_data.get("sharpe")
    avg_pnl = _decimal(performance_data.get("avg_pnl", 0))

    # Find neighboring exit configs (same family, different target_atr)
    current_target = current_exit.target_atr
    current_stop = current_exit.stop_atr

    # Determine adjustment direction
    # If win_rate is high but avg_pnl is low → target is too tight, widen it
    # If win_rate is low and avg_pnl is very negative → target may be too wide
    if win_rate > 0.5 and avg_pnl < ZERO and sharpe is not None and sharpe < ZERO:
        # Tight target with high WR but losing money: costs eating profits
        # Propose widening target_atr by 20%
        new_target = (current_target * Decimal("1.2")).quantize(Decimal("0.1"))
        if new_target == current_target:
            new_target = current_target + Decimal("0.5")
        return {
            "exit_id": current_exit.exit_id,
            "baseline_exit_config": {
                "target_atr": str(current_target),
                "stop_atr": str(current_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "proposed_exit_config": {
                "target_atr": str(new_target),
                "stop_atr": str(current_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "pattern": "high_win_rate_low_pnl",
            "evidence_status": "hypothesis",
            "rationale": (
                f"Win rate is {win_rate:.0%} but avg PnL is negative "
                f"({trade_count} trades). "
                f"Widening target from {current_target} to {new_target} ATR "
                f"to let winners run further. "
                f"This is a hypothesis, not evidence-based conclusion."
            ),
        }

    if win_rate < 0.35 and sharpe is not None and sharpe < ZERO:
        # Low win rate: stop may be too tight, widen it
        new_stop = (current_stop * Decimal("1.3")).quantize(Decimal("0.1"))
        if new_stop == current_stop:
            new_stop = current_stop + Decimal("0.5")
        return {
            "exit_id": current_exit.exit_id,
            "baseline_exit_config": {
                "target_atr": str(current_target),
                "stop_atr": str(current_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "proposed_exit_config": {
                "target_atr": str(current_target),
                "stop_atr": str(new_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "pattern": "low_win_rate",
            "evidence_status": "hypothesis",
            "rationale": (
                f"Win rate is only {win_rate:.0%} ({trade_count} trades). "
                f"Widening stop from {current_stop} to {new_stop} ATR "
                f"to reduce premature stop-outs. "
                f"This is a hypothesis, not evidence-based conclusion."
            ),
        }

    # Moderate-negative Sharpe: both win rate and PnL are sub-par but not
    # extreme.  Nudge target_atr wider (10 %) so winners have more room.
    if sharpe is not None and sharpe < ZERO and avg_pnl < ZERO:
        new_target = (current_target * Decimal("1.1")).quantize(Decimal("0.1"))
        if new_target == current_target:
            new_target = current_target + Decimal("0.5")
        return {
            "exit_id": current_exit.exit_id,
            "baseline_exit_config": {
                "target_atr": str(current_target),
                "stop_atr": str(current_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "proposed_exit_config": {
                "target_atr": str(new_target),
                "stop_atr": str(current_stop),
                "time_stop_bars": current_exit.time_stop_bars,
            },
            "pattern": "negative_sharpe_pnl",
            "evidence_status": "hypothesis",
            "rationale": (
                f"Sharpe {sharpe:.2f} and avg PnL {avg_pnl:.2f} are both negative "
                f"({trade_count} trades). "
                f"Widening target from {current_target} to {new_target} ATR "
                f"to let winners run further. "
                f"This is a hypothesis, not evidence-based conclusion."
            ),
        }

    return None


def validate_proposal(
    db: Session,
    *,
    user_id: UUID,
    proposal: dict,
) -> dict:
    """Run a walk-forward backtest with the proposed exit config.

    Uses the bars from the most recent run for the same instrument/interval
    (read from StrategyStudyRun.bars_json). Compares Sharpe ratio, max
    drawdown, and win rate against baseline.
    """
    instrument_id = proposal.get("instrument_id", "")
    interval_minutes = proposal.get("interval_minutes", 1440)
    proposed_config = proposal.get("proposed_exit_config", {})
    baseline_config = proposal.get("baseline_exit_config", {})
    rule_id = proposal.get("rule_id", "")

    # Find the most recent run for this instrument/interval
    stmt = (
        select(StrategyStudyRun)
        .where(
            StrategyStudyRun.user_id == user_id,
            StrategyStudyRun.instrument_id == instrument_id,
            StrategyStudyRun.interval_minutes == interval_minutes,
        )
        .order_by(StrategyStudyRun.created_at.desc())
        .limit(1)
    )
    run = db.scalars(stmt).first()

    if run is None or not run.bars_json:
        return {
            "passed": False,
            "error": "no_baseline_run",
            "message": f"No stored run found for {instrument_id} @ {interval_minutes}m.",
        }

    # Reconstruct bars
    bars = _bars_from_json(run.bars_json, interval_minutes)
    if len(bars) < WARMUP_BARS + 10:
        return {
            "passed": False,
            "error": "insufficient_bars",
            "message": f"Only {len(bars)} bars stored; need at least {WARMUP_BARS + 10}.",
        }

    # Get baseline trades from the stored run
    baseline_trades = [
        t for t in run.trades if t.rule_id == rule_id and t.exit_id == baseline_config.get("exit_id", "")
    ]

    # Fail closed: if no baseline trades exist, cannot validate
    if not baseline_trades:
        return {
            "passed": False,
            "error": "no_baseline_trades",
            "message": f"No baseline trades found for rule {rule_id} and exit {baseline_config.get('exit_id', '')}. Cannot validate without baseline data.",
        }

    # Build a synthetic ExitConfig for the proposed parameters
    try:
        proposed_exit = ExitConfig(
            exit_id="proposed_validation",
            label_zh="Proposed (validation)",
            target_atr=_decimal(proposed_config.get("target_atr", "1.0")),
            stop_atr=_decimal(proposed_config.get("stop_atr", "1.0")),
            time_stop_bars=int(proposed_config.get("time_stop_bars", 6)),
            thesis_zh="Auto-generated for validation backtest.",
        )
    except Exception as exc:
        return {
            "passed": False,
            "error": "invalid_proposed_config",
            "message": str(exc),
        }

    # Find the rule
    rule = RULE_BY_ID.get(rule_id)
    if rule is None:
        return {
            "passed": False,
            "error": "unknown_rule",
            "message": f"Rule {rule_id} not in current registry. Cannot validate — rejecting.",
        }

    # Reconstruct ResearchSettings from the original run (not default)
    # This ensures baseline and proposed use same costs, capital, windows
    run_settings_dict = run.settings_json or {}
    settings = ResearchSettings(
        starting_cash=_decimal(run_settings_dict.get("starting_cash", "10000")),
        notional_per_trade=_decimal(run_settings_dict.get("notional_per_trade", "1000")),
        commission_rate=_decimal(run_settings_dict.get("commission_rate", "0.001")),
        slippage_rate=_decimal(run_settings_dict.get("slippage_rate", "0.001")),
        atr_window=int(run_settings_dict.get("atr_window", 14)),
        cost_multiple=_decimal(run_settings_dict.get("cost_multiple", "2.0")),
    )
    ind = prepare_indicators(bars, settings.atr_window)
    bounds = fold_bounds(len(bars), WARMUP_BARS, min(5, len(bars) // 30 or 1))

    proposed_trades_all = []
    for number, (start, end) in enumerate(bounds):
        if number == 0:
            continue
        trades, _ = _simulate_range(
            bars, ind, rule, proposed_exit, settings, start, end
        )
        proposed_trades_all.extend(trades)

    # Compute metrics for proposed
    proposed_summary = summarize(proposed_trades_all, starting_cash=settings.starting_cash)
    proposed_sharpe = _compute_sharpe_from_research_trades(proposed_trades_all)
    proposed_max_dd = _compute_max_dd_from_research_trades(proposed_trades_all)
    proposed_win_rate = _decimal(proposed_summary.get("win_rate", 0))

    # Compute baseline metrics
    baseline_sharpe = _compute_sharpe(baseline_trades)
    baseline_max_dd = _compute_max_drawdown(baseline_trades)
    baseline_trades_list = list(baseline_trades)
    baseline_wr = (
        Decimal(len([t for t in baseline_trades_list if _decimal(t.net_pnl) > ZERO]))
        / Decimal(len(baseline_trades_list))
        if baseline_trades_list
        else ZERO
    )

    # Fail closed: if baseline Sharpe cannot be computed (insufficient data), reject
    if baseline_sharpe is None:
        return {
            "passed": False,
            "error": "insufficient_baseline_data",
            "message": f"Baseline has {len(baseline_trades_list)} trades but Sharpe cannot be computed (need >= 2 trades with variance). Cannot validate.",
        }

    # Fail closed: if proposed Sharpe cannot be computed, reject
    if proposed_sharpe is None:
        return {
            "passed": False,
            "error": "insufficient_proposed_data",
            "message": f"Proposed config produced {len(proposed_trades_all)} trades but Sharpe cannot be computed. Cannot validate.",
        }

    # Compare — use actual computed values, not 0.0 fallbacks
    sharpe_before = float(baseline_sharpe)
    sharpe_after = float(proposed_sharpe)
    max_dd_before = float(baseline_max_dd)
    max_dd_after = float(proposed_max_dd)

    passed = should_deploy(
        {
            "sharpe_before": sharpe_before,
            "sharpe_after": sharpe_after,
            "max_dd_before": max_dd_before,
            "max_dd_after": max_dd_after,
        }
    )

    return {
        "passed": passed,
        "sharpe_before": sharpe_before,
        "sharpe_after": sharpe_after,
        "max_dd_before": max_dd_before,
        "max_dd_after": max_dd_after,
        "win_rate_before": float(baseline_wr),
        "win_rate_after": float(proposed_win_rate),
        "trades_before": len(baseline_trades_list),
        "trades_after": len(proposed_trades_all),
        "metrics_delta": {
            "sharpe_delta": sharpe_after - sharpe_before,
            "max_dd_delta": max_dd_before - max_dd_after,
            "win_rate_delta": float(proposed_win_rate) - float(baseline_wr),
        },
    }


def _compute_sharpe_from_research_trades(trades: list) -> Optional[Decimal]:
    """Compute Sharpe from ResearchTrade-like objects (net_pnl attribute)."""
    if len(trades) < 2:
        return None
    pnls = [t.net_pnl for t in trades]
    mean = sum(pnls, ZERO) / Decimal(len(pnls))
    variance = sum((p - mean) ** 2 for p in pnls) / Decimal(len(pnls) - 1)
    stdev = variance.sqrt() if variance >= 0 else ZERO
    if stdev == ZERO:
        return None
    return mean / stdev


def _compute_max_dd_from_research_trades(trades: list) -> Decimal:
    """Compute max drawdown from ResearchTrade-like objects."""
    if not trades:
        return ZERO
    peak = trades[0].cash_after
    max_dd = ZERO
    for t in trades:
        if t.cash_after > peak:
            peak = t.cash_after
        if peak > ZERO:
            dd = (peak - t.cash_after) / peak * Decimal(100)
            if dd > max_dd:
                max_dd = dd
    return max_dd


def should_deploy(validation: dict) -> bool:
    """Check if validation meets deploy criteria.

    Sharpe improves >= 0.2 OR max drawdown decreases >= 10%.
    """
    sharpe_before = _decimal(validation.get("sharpe_before", 0))
    sharpe_after = _decimal(validation.get("sharpe_after", 0))
    max_dd_before = _decimal(validation.get("max_dd_before", 0))
    max_dd_after = _decimal(validation.get("max_dd_after", 0))

    sharpe_improvement = sharpe_after - sharpe_before
    if sharpe_improvement >= DEPLOY_CRITERIA["sharpe_improvement_min"]:
        return True

    if max_dd_before > ZERO:
        dd_decrease_pct = (max_dd_before - max_dd_after) / max_dd_before * Decimal(100)
        if dd_decrease_pct >= DEPLOY_CRITERIA["max_drawdown_decrease_min_pct"]:
            return True

    return False
