"""Auto-learning scheduler worker.

Runs two periodic jobs:
- Daily (02:00 UTC): scheduled research across instruments x intervals
- Weekly (Sunday 04:00 UTC): pattern analysis + parameter proposals

The scheduler is opt-in via ``settings.enable_learning_scheduler``. It uses
APScheduler's in-process BackgroundScheduler — no Redis or Celery required.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.lib.timeutil import utcnow_aware
from app.models.learning_job import LearningJob, LearningProposal
from app.models.strategy_study import StrategyStudyRun
from app.services.pattern_analysis import (
    analyze_skill_patterns,
    propose_parameter_adjustment,
    should_deploy,
    validate_proposal,
)
from app.services.strategy_research import (
    EXIT_BY_ID,
    ExitConfig,
    ResearchSettings,
    run_study,
)
from app.services.strategy_lab import LabBar
from app.services.strategy_study_store import persist_study

logger = logging.getLogger(__name__)

# Default instruments and intervals for scheduled research
SCHEDULED_INSTRUMENTS = ["CRYPTO:KRAKEN:BTC-USD", "CRYPTO:KRAKEN:ETH-USD", "CRYPTO:KRAKEN:SOL-USD"]
SCHEDULED_INTERVALS = [1440, 240, 60, 15]

# Legacy exit IDs that may appear in stored trades but are not in the current
# EXIT_BY_ID registry.  Map them to approximate ExitConfig objects so the
# proposal pipeline can still operate on older data.
_LEGACY_EXIT_CONFIGS: dict[str, ExitConfig] = {
    "exit_event_catalyst_1": ExitConfig("exit_event_catalyst_1", "事件催化 (緊)", Decimal("1.0"), Decimal("1.5"), 6, "Legacy"),
    "exit_event_catalyst_2": ExitConfig("exit_event_catalyst_2", "事件催化 (中)", Decimal("1.5"), Decimal("2.0"), 8, "Legacy"),
    "exit_event_catalyst_3": ExitConfig("exit_event_catalyst_3", "事件催化 (寬)", Decimal("2.0"), Decimal("2.5"), 10, "Legacy"),
    "exit_mean_reversion_1": ExitConfig("exit_mean_reversion_1", "均值回歸 (緊)", Decimal("0.8"), Decimal("1.2"), 4, "Legacy"),
    "exit_mean_reversion_2": ExitConfig("exit_mean_reversion_2", "均值回歸 (中)", Decimal("1.2"), Decimal("1.8"), 6, "Legacy"),
    "exit_mean_reversion_3": ExitConfig("exit_mean_reversion_3", "均值回歸 (寬)", Decimal("1.8"), Decimal("2.5"), 8, "Legacy"),
    "exit_trend_following_1": ExitConfig("exit_trend_following_1", "趨勢跟隨 (緊)", Decimal("1.0"), Decimal("1.5"), 8, "Legacy"),
    "exit_trend_following_2": ExitConfig("exit_trend_following_2", "趨勢跟隨 (中)", Decimal("1.5"), Decimal("2.0"), 12, "Legacy"),
    "exit_trend_following_3": ExitConfig("exit_trend_following_3", "趨勢跟隨 (寬)", Decimal("2.0"), Decimal("3.0"), 16, "Legacy"),
    "exit_intraday_momentum_1": ExitConfig("exit_intraday_momentum_1", "日內動量 (緊)", Decimal("0.6"), Decimal("1.0"), 3, "Legacy"),
    "exit_intraday_momentum_2": ExitConfig("exit_intraday_momentum_2", "日內動量 (中)", Decimal("1.0"), Decimal("1.5"), 5, "Legacy"),
    "exit_intraday_momentum_3": ExitConfig("exit_intraday_momentum_3", "日內動量 (寬)", Decimal("1.5"), Decimal("2.0"), 8, "Legacy"),
}


def _resolve_exit_config(exit_id: str) -> Optional[ExitConfig]:
    """Look up an ExitConfig by ID, falling back to legacy configs."""
    return EXIT_BY_ID.get(exit_id) or _LEGACY_EXIT_CONFIGS.get(exit_id)


def create_learning_scheduler() -> BackgroundScheduler:
    """Create and configure the learning scheduler."""
    scheduler = BackgroundScheduler()

    # Daily: run research across all instrument x interval combos
    scheduler.add_job(
        run_scheduled_research,
        CronTrigger(hour=2, minute=0),
        id="daily_research",
        kwargs={"scope": "daily"},
        replace_existing=True,
    )

    # Weekly: pattern analysis + parameter proposals
    scheduler.add_job(
        run_pattern_analysis_job,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="weekly_analysis",
        replace_existing=True,
    )

    logger.info("Learning scheduler created: daily_research@02:00, weekly_analysis@Sun 04:00")
    return scheduler


def _get_first_user_id(db: Session) -> Optional[UUID]:
    """Get the first active user ID for scheduled jobs."""
    from app.models.user import User

    user = db.scalars(select(User).limit(1)).first()
    return user.id if user else None


def run_scheduled_research(scope: str = "daily"):
    """For each instrument x interval combo, run strategy_research.run_study()
    and persist via strategy_study_store.persist_study().

    Creates a LearningJob record to track the batch.
    """
    db = SessionLocal()
    try:
        user_id = _get_first_user_id(db)
        if user_id is None:
            logger.warning("Scheduled research skipped: no users in database")
            return

        job = LearningJob(
            user_id=user_id,
            status="running",
            job_type="scheduled_study",
            config_json={
                "scope": scope,
                "instruments": SCHEDULED_INSTRUMENTS,
                "intervals": SCHEDULED_INTERVALS,
            },
            started_at=utcnow_aware(),
        )
        db.add(job)
        db.flush()
        logger.info("Learning job %s started: %s research", job.id, scope)

        results: List[dict] = []
        errors: List[str] = []

        for instrument_id in SCHEDULED_INSTRUMENTS:
            for interval in SCHEDULED_INTERVALS:
                try:
                    result = _run_single_study(db, user_id, instrument_id, interval)
                    results.append(
                        {
                            "instrument_id": instrument_id,
                            "interval_minutes": interval,
                            "trade_count": result.get("walk_forward", {}).get("out_of_sample", {}).get("trades", 0),
                            "status": "ok",
                        }
                    )
                    logger.info(
                        "Study completed: %s @ %dm — %d trades",
                        instrument_id,
                        interval,
                        result.get("walk_forward", {}).get("out_of_sample", {}).get("trades", 0),
                    )
                except Exception as exc:
                    error_msg = f"{instrument_id} @ {interval}m: {exc}"
                    errors.append(error_msg)
                    logger.warning("Study failed for %s: %s", error_msg, traceback.format_exc())

        job.status = "completed" if not errors else "completed"
        job.completed_at = utcnow_aware()
        job.result_json = {
            "results": results,
            "errors": errors,
            "total_combos": len(SCHEDULED_INSTRUMENTS) * len(SCHEDULED_INTERVALS),
            "successful": len(results),
            "failed": len(errors),
        }
        if errors:
            job.error_details = "\n".join(errors)
        db.commit()
        logger.info(
            "Learning job %s completed: %d/%d successful",
            job.id,
            len(results),
            len(results) + len(errors),
        )
    except Exception:
        db.rollback()
        logger.exception("Scheduled research failed catastrophically")
    finally:
        db.close()


def _run_single_study(
    db: Session, user_id: UUID, instrument_id: str, interval_minutes: int
) -> dict:
    """Run a single study for one instrument/interval combo.

    Fetches bars from the most recent stored run (to avoid re-fetching from API),
    then runs the study and persists it.
    """
    # Try to reuse bars from the most recent stored run
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
    existing_run = db.scalars(stmt).first()

    if existing_run and existing_run.bars_json:
        # Reuse stored bars
        bars = _bars_from_json(existing_run.bars_json, interval_minutes)
    else:
        # No stored bars available — fetch from provider
        from app.providers.crypto_registry import get_crypto_provider

        provider = get_crypto_provider()
        series = provider.get_candle_series(
            instrument_id, interval_minutes=interval_minutes, limit=720
        )
        bars = [
            LabBar(
                open_time=candle.open_time,
                interval_minutes=interval_minutes,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for candle in series.completed
        ]

    if len(bars) < 120:
        raise ValueError(f"Insufficient bars: {len(bars)} < 120")

    result = run_study(bars, settings=ResearchSettings(), include_trades=True)

    dataset = {
        "instrument_id": instrument_id,
        "interval_minutes": interval_minutes,
        "provider_id": "kraken",
        "data_nature": "venue_completed_bars",
        "bars": len(bars),
        "first_open_time": bars[0].open_time.isoformat(),
        "last_open_time": bars[-1].open_time.isoformat(),
        "fingerprint": None,
        "integrity_ok": True,
        "gap_count": 0,
        "missing_intervals": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    persist_study(db, user_id=user_id, result=result, bars=bars, dataset=dataset)
    return result


def _bars_from_json(bars_json: list, interval_minutes: int) -> List[LabBar]:
    """Reconstruct LabBar list from stored bars_json."""
    from datetime import datetime, timezone

    bars: List[LabBar] = []
    for row in bars_json:
        open_time = row.get("open_time")
        if isinstance(open_time, str):
            parsed = datetime.fromisoformat(open_time)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            open_time = parsed
        bars.append(
            LabBar(
                open_time=open_time,
                interval_minutes=interval_minutes,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
        )
    return bars


def run_pattern_analysis_job():
    """Weekly pattern analysis + parameter proposals.

    1. Fetch all trades from the last N runs per instrument/interval.
    2. Group by (rule_id, exit_id) and compute metrics.
    3. Identify patterns: best exit configs per skill, trending vs ranging.
    4. Generate LearningProposal records for parameter adjustments.
    5. For each proposal, run validation backtest.
    6. If validation passes, mark as deployable (never auto-deploy).
    """
    db = SessionLocal()
    try:
        user_id = _get_first_user_id(db)
        if user_id is None:
            logger.warning("Pattern analysis skipped: no users in database")
            return

        job = LearningJob(
            user_id=user_id,
            status="running",
            job_type="pattern_analysis",
            config_json={"lookback_runs": 50},
            started_at=utcnow_aware(),
        )
        db.add(job)
        db.flush()
        logger.info("Pattern analysis job %s started", job.id)

        # Run analysis
        analysis = analyze_skill_patterns(db, user_id=user_id, lookback_runs=50)

        # Generate proposals from exit config performance
        proposals_created = 0
        for exit_id, metrics in analysis.get("per_exit_config", {}).items():
            exit_config = _resolve_exit_config(exit_id)
            if exit_config is None:
                continue

            adjustment = propose_parameter_adjustment(
                current_exit=exit_config,
                performance_data=metrics,
            )
            if adjustment is None:
                continue

            # Find the instrument/interval from the most recent run using this exit
            recent_run = _find_recent_run_for_exit(db, user_id, exit_id)
            if recent_run is None:
                continue

            # Create proposal
            proposal = LearningProposal(
                user_id=user_id,
                learning_job_id=job.id,
                rule_id=_find_best_rule_for_exit(db, user_id, recent_run.id, exit_id),
                exit_id=exit_id,
                instrument_id=recent_run.instrument_id,
                interval_minutes=recent_run.interval_minutes,
                baseline_exit_config=adjustment["baseline_exit_config"],
                proposed_exit_config=adjustment["proposed_exit_config"],
                rationale_json={
                    "pattern": adjustment["pattern"],
                    "baseline_metrics": metrics,
                    "rationale": adjustment["rationale"],
                },
                status="pending",
            )
            db.add(proposal)
            db.flush()

            # Validate the proposal
            validation = validate_proposal(
                db,
                user_id=user_id,
                proposal={
                    "instrument_id": recent_run.instrument_id,
                    "interval_minutes": recent_run.interval_minutes,
                    "rule_id": proposal.rule_id,
                    "baseline_exit_config": {
                        **adjustment["baseline_exit_config"],
                        "exit_id": exit_id,
                    },
                    "proposed_exit_config": adjustment["proposed_exit_config"],
                },
            )

            proposal.validation_result = validation
            if validation.get("passed"):
                proposal.status = "validated"
                proposal.validated_at = utcnow_aware()
                logger.info(
                    "Proposal %s validated: Sharpe %.2f -> %.2f",
                    proposal.id,
                    validation.get("sharpe_before", 0),
                    validation.get("sharpe_after", 0),
                )
            elif validation.get("error") in ("no_baseline_run", "insufficient_bars"):
                # Validation could not run due to missing data — keep as
                # ``pending`` for manual review rather than rejecting.
                proposal.status = "pending"
                logger.info(
                    "Proposal %s pending (validation skipped: %s)",
                    proposal.id,
                    validation.get("error"),
                )
            else:
                proposal.status = "rejected"
                logger.info("Proposal %s rejected by validation", proposal.id)

            proposals_created += 1

        job.status = "completed"
        job.completed_at = utcnow_aware()
        job.result_json = {
            "analysis": analysis,
            "proposals_created": proposals_created,
        }
        db.commit()
        logger.info(
            "Pattern analysis job %s completed: %d proposals", job.id, proposals_created
        )
    except Exception:
        db.rollback()
        logger.exception("Pattern analysis job failed")
    finally:
        db.close()


def _find_recent_run_for_exit(
    db: Session, user_id: UUID, exit_id: str
) -> Optional[StrategyStudyRun]:
    """Find the most recent run that has trades using this exit_id."""
    from app.models.strategy_study import StrategyStudyTrade

    stmt = (
        select(StrategyStudyRun)
        .join(StrategyStudyTrade, StrategyStudyTrade.run_id == StrategyStudyRun.id)
        .where(
            StrategyStudyRun.user_id == user_id,
            StrategyStudyTrade.exit_id == exit_id,
        )
        .order_by(StrategyStudyRun.created_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _find_best_rule_for_exit(
    db: Session, user_id: UUID, run_id: UUID, exit_id: str
) -> str:
    """Find the rule_id with the most trades for a given exit in a run."""
    from app.models.strategy_study import StrategyStudyTrade
    from collections import Counter

    trades = db.scalars(
        select(StrategyStudyTrade).where(
            StrategyStudyTrade.run_id == run_id,
            StrategyStudyTrade.exit_id == exit_id,
        )
    ).all()

    if not trades:
        return "breakout_trend"  # fallback default

    rule_counts = Counter(t.rule_id for t in trades)
    return rule_counts.most_common(1)[0][0]
