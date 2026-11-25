"""The controlled daily/weekly research service.

Four ticks, all on a UTC clock:

- Daily (02:00 UTC): strategy research for every account the service serves.
- Daily (03:00 UTC): one step of the bound dataset for every open simulation
  session, filed under ``simulation_daily``.
- Weekly (Sunday 04:00 UTC): pattern analysis + parameter proposals.
- Daily (05:00 UTC): the model estimates the owners who opted in, filed under
  ``daily_model_estimate``.

The service is opt-in per process (``settings.enable_learning_scheduler``) and
uses APScheduler's in-process ``BackgroundScheduler`` — no Redis or Celery. That
switch is one for all four ticks, so ``settings.learning_scheduler_jobs`` names
which of them this process actually registers. Leaving it unset registers the
three ticks the switch has always meant and not ``daily_model_estimate``: a
deployment that enabled this service before the estimate lane existed does not
gain a fifth, billing tick from an upgrade alone, so the lane has to be named.
Naming one lane cannot start another. It
stops with the platform kill switch, serves at most a bounded number of
accounts per tick, runs at most one scheduled tick per account per day (every
API worker process starts its own scheduler, so the guard reads the ledger it
would write rather than trusting a process-local flag), studies only bars whose
provenance it can state, and keeps one ledger row per request: a manual trigger
advances the row the API recorded instead of writing a second one beside it.
The two research ticks place no order and touch no portfolio. The simulation
tick does book the fills a locked rule fires on, in a paper account only, and so
it honours the execution kill switch rather than the training one, steps no
session the owner has paused or retired, and never places a real order.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, List, Optional, Sequence
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.engine.intraday import INTRADAY_TIMEFRAMES
from app.lib.timeutil import utcnow_aware
from app.models.kill_switch import KillSwitchScope
from app.models.learning_job import LearningJob, LearningProposal
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.simulation import SimulationSession, SimulationStatus
from app.models.strategy_study import StrategyStudyRun
from app.services.crypto_series import _fingerprint as candle_fingerprint
from app.services.daily_estimates import (
    DAILY_ESTIMATE_HOUR_UTC,
    run_scheduled_daily_estimates,
)
from app.services.kill_switch import is_kill_switch_active
from app.services.pattern_analysis import (
    analyze_skill_patterns,
    propose_parameter_adjustment,
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

# Every tick is documented as UTC, so every tick is scheduled in UTC. A bare
# CronTrigger resolves to the host timezone and quietly moves the batch.
SERVICE_TIMEZONE = "UTC"
DAILY_HOUR_UTC = 2
SIMULATION_HOUR_UTC = 3
WEEKLY_HOUR_UTC = 4

DAILY_JOB_TYPE = "scheduled_study"
WEEKLY_JOB_TYPE = "pattern_analysis"
SIMULATION_JOB_TYPE = "simulation_daily"

# Only ``manual`` (an explicit owner request) escapes the once-a-day guard.
MANUAL_SCOPE = "manual"

# The same pair of terminal statuses the portfolio entry points refuse. A
# status this filter cannot read (NULL) is not served either: research spends
# provider quota, and an unreadable account state is not a claim on it.
TERMINAL_PORTFOLIO_STATUSES = (PortfolioStatus.retired, PortfolioStatus.completed)

# The two statuses ``POST /simulations/{id}/advance`` accepts and no others: a
# paused, completed or retired session is the owner's own decision to stop the
# replay, so the service has no claim on it. NULL is not in this set either —
# an unreadable session state is not a claim on the service's writes.
SIMULATION_SERVED_STATUSES = (SimulationStatus.pending, SimulationStatus.active)

# One tick steps at most this many of one account's open sessions. The ceiling
# is per account, not global: an account with 40 open sessions gets the first
# 20 by creation order and the rest on the next day, rather than the tick
# spending an unbounded afternoon on one owner.
SIMULATION_SESSIONS_PER_OWNER = 20

# Default instruments and intervals for scheduled research
SCHEDULED_INSTRUMENTS = ["CRYPTO:KRAKEN:BTC-USD", "CRYPTO:KRAKEN:ETH-USD", "CRYPTO:KRAKEN:SOL-USD"]
SCHEDULED_INTERVALS = [1440, 240, 60, 15]

# A study shorter than this cannot fill the walk-forward folds it reports.
MIN_STUDY_BARS = 120

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


def _lane_definitions() -> List[tuple]:
    """Every tick this build knows, in clock order.

    One entry per lane: the id the ledger and the configuration both use, the
    callable, a freshly built trigger and the scope it files under. Triggers
    are built per call so two schedulers never share one job's state.
    """
    return [
        # Daily: run research across all instrument x interval combos
        (
            "daily_research",
            run_scheduled_research,
            CronTrigger(hour=DAILY_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE),
            {"scope": "daily"},
        ),
        # Daily, one hour after the research tick: step every open simulation
        # session of every served account by one stored bar. Not sooner: the
        # two ticks would otherwise share a minute of the same clock and the
        # later one would inherit whatever the first one left in progress.
        (
            "daily_simulation",
            run_scheduled_simulations,
            CronTrigger(hour=SIMULATION_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE),
            {"scope": "daily"},
        ),
        # Weekly: pattern analysis + parameter proposals
        (
            "weekly_analysis",
            run_pattern_analysis_job,
            CronTrigger(
                day_of_week="sun", hour=WEEKLY_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE
            ),
            {"scope": "weekly"},
        ),
        # Daily, one hour after the replay tick: the model estimates the owner
        # explicitly opted into. Registering the job does not opt anyone in —
        # the tick asks ``estimate_owner_ids`` first and a host where nobody
        # declared a scope serves nobody and writes no ledger row. It runs no
        # research and no collection: those are the lanes above, each with its
        # own hour.
        (
            "daily_model_estimate",
            run_scheduled_daily_estimates,
            CronTrigger(
                hour=DAILY_ESTIMATE_HOUR_UTC, minute=0, timezone=SERVICE_TIMEZONE
            ),
            {"scope": "daily"},
        ),
    ]


def registered_lane_ids() -> List[str]:
    """Which of the known ticks this process registers.

    ``settings.learning_scheduler_jobs`` names the schedule; leaving it unset
    takes ``DEFAULT_LEARNING_SCHEDULER_LANES`` rather than the registry, so a
    lane this build knows but the deployment was never configured to want stays
    off through an upgrade. Naming a lane cannot widen the schedule either: a
    name the worker has no definition for is refused when the settings are read,
    and every registered lane still has to pass its own opt-in, its own kill
    switch and its own owner scope before it does anything.
    """
    allowed = set(get_settings().registered_learning_lanes)
    lanes = [lane_id for lane_id, _func, _trigger, _kwargs in _lane_definitions()]
    return [lane for lane in lanes if lane in allowed]


def create_learning_scheduler() -> BackgroundScheduler:
    """Create and configure the scheduler, registering only the named lanes."""
    scheduler = BackgroundScheduler(timezone=SERVICE_TIMEZONE)

    registered = set(registered_lane_ids())
    named = []
    for lane_id, func, trigger, kwargs in _lane_definitions():
        if lane_id not in registered:
            continue
        scheduler.add_job(func, trigger, id=lane_id, kwargs=kwargs, replace_existing=True)
        named.append(f"{lane_id}@{trigger}")

    # The log names the lanes actually added, not the lanes this build knows
    # how to add: an operator reads it to confirm that the tick they meant to
    # isolate is the only one on this clock.
    logger.info(
        "Learning scheduler created (%s): %s",
        SERVICE_TIMEZONE,
        ", ".join(named) if named else "no lane registered",
    )
    return scheduler


# ── who the service runs for, and whether this tick may run at all ──────


def service_owner_ids(db: Session, *, limit: int) -> List[UUID]:
    """The owners one tick serves: those holding an account it researches for.

    ``select(User).limit(1)`` named neither an owner (a user with no paper
    account cannot use a research record) nor an order (no ORDER BY, so
    whichever row the planner returned first absorbed the work). The two
    terminal statuses — the same pair the six portfolio entry points refuse —
    end an account's claim on the service.
    """
    rows = (
        db.query(PaperPortfolio.owner_id)
        .filter(PaperPortfolio.status.notin_(TERMINAL_PORTFOLIO_STATUSES))
        .distinct()
        .order_by(PaperPortfolio.owner_id)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def simulation_owner_ids(db: Session, *, limit: int) -> List[UUID]:
    """The owners one simulation tick serves: those with a session it may step.

    The research lane asks who holds a live paper account; this lane asks a
    different question, because the work differs - a live account with no open
    replay has nothing for this tick to step, and a session in any status the
    advance endpoint itself refuses is not a claim on the service. Same shape
    as ``service_owner_ids`` on purpose: ordered, distinct and bounded, so the
    ceiling cuts the same way for both lanes.
    """
    rows = (
        db.query(SimulationSession.owner_id)
        .filter(SimulationSession.status.in_(SIMULATION_SERVED_STATUSES))
        .distinct()
        .order_by(SimulationSession.owner_id)
        .limit(limit)
        .all()
    )
    return [row[0] for row in rows]


def _already_scheduled(
    db: Session, *, owner_id: UUID, job_type: str, scheduled_for: str
) -> bool:
    """Whether this account already holds a record for this day.

    The guard reads the ledger because the thing it guards is the ledger: a
    restart, a redeploy or a second API worker would otherwise re-run the
    whole grid and add a second set of "daily" records for the same day.
    """
    recent = (
        db.query(LearningJob)
        .filter(LearningJob.user_id == owner_id, LearningJob.job_type == job_type)
        .order_by(LearningJob.created_at.desc())
        .limit(200)
        .all()
    )
    return any(
        (job.config_json or {}).get("scheduled_for") == scheduled_for for job in recent
    )


def _tick_targets(
    db: Session,
    *,
    user_id: Optional[UUID],
    job_type: str,
    scheduled_for: Optional[str],
    owners_of: Callable[[Session, int], List[UUID]] = service_owner_ids,
    kill_switch_scope: KillSwitchScope = KillSwitchScope.training,
) -> tuple[List[UUID], Optional[str]]:
    """Kill switch, who is served, and the same-day guard, in that order.

    The two arguments that differ per lane arrive as defaults, so the existing
    research and analysis ticks get exactly what they got before: which
    accounts are asked about, and which switch can stop the tick, are the only
    two things a second lane has any reason to change.
    """
    try:
        # The default scope is the one the training worker already honours, so
        # one switch stops both learning lanes; a lane that writes to a book
        # passes ``execution`` instead, which is what that switch claims to
        # cover. The check raises if the switch cannot be read, and a tick that
        # cannot read the stop control is not authorised to proceed: unreadable
        # means do nothing.
        if is_kill_switch_active(db, kill_switch_scope):
            return [], "kill_switch_active"
    except Exception as exc:
        logger.error("Unable to read the kill switch, tick not run: %s", exc)
        return [], "kill_switch_unreadable"

    owners = (
        [user_id]
        if user_id is not None
        else owners_of(db, limit=get_settings().learning_scheduler_max_owners)
    )
    if not owners:
        return [], "no_accounts_served"

    if scheduled_for is not None:
        owners = [
            owner
            for owner in owners
            if not _already_scheduled(
                db, owner_id=owner, job_type=job_type, scheduled_for=scheduled_for
            )
        ]
        if not owners:
            return [], "already_ran_today"

    return owners, None


def _refuse_adopted_job(db: Session, job_id: Optional[UUID], *, reason: str) -> None:
    """Close a request this tick did not serve, so its row is never left queued.

    Only a row the API recorded in ``pending`` is written: a scheduled tick
    that skips creates nothing, because a ledger padded with records of work
    that was never offered would bury the runs that were.
    """
    if job_id is None:
        return
    job = (
        db.query(LearningJob)
        .filter(LearningJob.id == job_id, LearningJob.status == "pending")
        .first()
    )
    if job is None:
        return
    job.status = "failed"
    job.completed_at = utcnow_aware()
    job.error_details = f"refused: {reason}"
    db.commit()
    logger.info("Learning job %s refused (%s)", job_id, reason)


def _run_tick(
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    tick_owner: Callable[..., dict],
    user_id: Optional[UUID] = None,
    job_id: Optional[UUID] = None,
    owners_of: Callable[[Session, int], List[UUID]] = service_owner_ids,
    kill_switch_scope: KillSwitchScope = KillSwitchScope.training,
    **work,
) -> dict:
    """One service tick over the accounts it serves."""
    db = SessionLocal()
    try:
        owners, skipped = _tick_targets(
            db,
            user_id=user_id,
            job_type=job_type,
            scheduled_for=scheduled_for,
            owners_of=owners_of,
            kill_switch_scope=kill_switch_scope,
        )
        if skipped:
            logger.info("Learning %s tick skipped (%s)", job_type, skipped)
            _refuse_adopted_job(db, job_id, reason=skipped)
            return {"skipped": skipped, "jobs": []}
        return {
            "skipped": None,
            "jobs": [
                tick_owner(
                    db,
                    owner,
                    scope=scope,
                    job_type=job_type,
                    scheduled_for=scheduled_for,
                    job_id=job_id,
                    **work,
                )
                for owner in owners
            ],
        }
    except Exception:
        db.rollback()
        logger.exception("Learning %s tick failed before any job was written", job_type)
        _refuse_adopted_job(db, job_id, reason="tick_error")
        return {"skipped": "tick_error", "jobs": []}
    finally:
        db.close()


def _open_job(
    db: Session,
    owner_id: UUID,
    *,
    job_type: str,
    config: dict,
    job_id: Optional[UUID],
) -> LearningJob:
    """The row this tick writes its outcome into.

    A manual trigger has already recorded the request, so the tick advances
    that row instead of leaving a ``pending`` stub beside a second record of
    the same work. The owner id is part of the lookup, so another account's
    job id cannot be adopted.
    """
    job = None
    if job_id is not None:
        job = (
            db.query(LearningJob)
            .filter(LearningJob.id == job_id, LearningJob.user_id == owner_id)
            .first()
        )

    if job is None:
        job = LearningJob(
            user_id=owner_id,
            status="running",
            job_type=job_type,
            config_json=config,
            started_at=utcnow_aware(),
        )
        db.add(job)
    else:
        job.status = "running"
        job.config_json = config
        job.started_at = utcnow_aware()
    db.commit()
    return job


# ── the daily research tick ─────────────────────────────────────────────


def run_research_cycle(
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    user_id: Optional[UUID] = None,
    job_id: Optional[UUID] = None,
    instruments: Optional[Sequence[str]] = None,
    intervals: Optional[Sequence[int]] = None,
) -> dict:
    """One research grid per served account, recorded under ``job_type``.

    The daily cron and the interval collector are the same work; what differs
    is the clock that asks for it and the ledger type it is filed under, so
    the freshness check, the owner resolution, the kill switch and the terminal
    status exist once instead of twice.
    """
    return _run_tick(
        scope=scope,
        job_type=job_type,
        scheduled_for=scheduled_for,
        tick_owner=_research_for_owner,
        user_id=user_id,
        job_id=job_id,
        instruments=list(instruments or SCHEDULED_INSTRUMENTS),
        intervals=list(intervals or SCHEDULED_INTERVALS),
    )


def run_scheduled_research(
    scope: str = "daily",
    user_id: Optional[UUID] = None,
    *,
    job_id: Optional[UUID] = None,
    instruments: Optional[Sequence[str]] = None,
    intervals: Optional[Sequence[int]] = None,
) -> dict:
    """Run the research grid once per served account and record the outcome.

    The return value says what the tick did, including when it did nothing:
    every early exit names its reason rather than looking like a run.
    """
    return run_research_cycle(
        scope=scope,
        job_type=DAILY_JOB_TYPE,
        scheduled_for=(
            None if scope == MANUAL_SCOPE else utcnow_aware().date().isoformat()
        ),
        user_id=user_id,
        job_id=job_id,
        instruments=instruments,
        intervals=intervals,
    )


def _research_for_owner(
    db: Session,
    owner_id: UUID,
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    job_id: Optional[UUID] = None,
    instruments: Optional[List[str]] = None,
    intervals: Optional[List[int]] = None,
) -> dict:
    """One account's research tick: the study grid, then one truthful row.

    The row is durable as ``running`` before the first study, so a tick that
    died mid-way leaves a visible record of having existed instead of vanishing
    with the uncommitted flush it used to hold.
    """
    instruments = list(instruments or SCHEDULED_INSTRUMENTS)
    intervals = list(intervals or SCHEDULED_INTERVALS)

    config: dict = {
        "scope": scope,
        "instruments": instruments,
        "intervals": intervals,
    }
    if scheduled_for is not None:
        config["scheduled_for"] = scheduled_for

    job = _open_job(
        db, owner_id, job_type=job_type, config=config, job_id=job_id
    )
    logger.info("Learning job %s started: %s research", job.id, scope)

    studied: List[dict] = []
    skipped: List[dict] = []
    errors: List[str] = []

    try:
        for instrument_id in instruments:
            for interval in intervals:
                try:
                    summary = _run_single_study(db, owner_id, instrument_id, interval)
                except Exception as exc:
                    db.rollback()
                    errors.append(f"{instrument_id} @ {interval}m: {exc}")
                    logger.warning(
                        "Study failed for %s @ %sm: %s\n%s",
                        instrument_id,
                        interval,
                        exc,
                        traceback.format_exc(),
                    )
                    continue
                if summary["status"] == "skipped":
                    skipped.append(summary)
                else:
                    studied.append(summary)
    except Exception as exc:
        # Not one combo failing: the session or the loop itself broke. It is an
        # error, and the terminal write below still happens.
        errors.append(f"tick aborted: {exc}")
        logger.exception("Learning research tick for %s aborted", owner_id)

    # ``completed`` used to be the value on both branches of this test, so a
    # tick in which nothing succeeded still reported success.
    if errors and not studied:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "completed"

    job.status = status
    job.completed_at = utcnow_aware()
    job.result_json = {
        "scheduled_for": scheduled_for,
        "total_combos": len(instruments) * len(intervals),
        "studied": len(studied),
        "skipped": len(skipped),
        "failed": len(errors),
        "results": studied,
        "skipped_detail": skipped,
        "errors": errors,
    }
    job.error_details = "\n".join(errors) if errors else None
    db.commit()

    logger.info(
        "Learning job %s %s: %d studied, %d skipped, %d failed",
        job.id,
        status,
        len(studied),
        len(skipped),
        len(errors),
    )
    return {"job_id": str(job.id), "user_id": str(owner_id), "status": status}


def _combo_summary(
    instrument_id: str,
    interval_minutes: int,
    *,
    status: str,
    reason: Optional[str] = None,
    trades: int = 0,
    bars: int = 0,
    data_nature: Optional[str] = None,
    fingerprint: Optional[str] = None,
    integrity_ok: Optional[bool] = None,
    is_stale: Optional[bool] = None,
    reused_from_run_id: Optional[str] = None,
) -> dict:
    """One combo's line in ``result_json``: what was studied, on which bars."""
    return {
        "instrument_id": instrument_id,
        "interval_minutes": interval_minutes,
        "status": status,
        "reason": reason,
        "trades": trades,
        "bars": bars,
        "data_nature": data_nature,
        "fingerprint": fingerprint,
        "integrity_ok": integrity_ok,
        "is_stale": is_stale,
        "reused_from_run_id": reused_from_run_id,
    }


def dataset_fingerprint(instrument_id: str, interval_minutes: int, bars: Sequence) -> str:
    """The digest of one bar window, computed the way the venue computes it.

    Both the fetched and the reused branch come through here, so a stored
    fingerprint and a freshly fetched one describe the same window when the
    bars do — which is what the no-new-bars check compares.
    """
    return candle_fingerprint(instrument_id, interval_minutes, bars)


def _provider_of(instrument_id: str) -> str:
    """``CRYPTO:KRAKEN:BTC-USD`` -> ``kraken``; anything else is not guessed."""
    parts = instrument_id.split(":")
    return parts[1].lower() if len(parts) >= 2 and parts[1] else "unknown"


def _latest_stored_bars(
    db: Session, user_id: UUID, instrument_id: str, interval_minutes: int
) -> Optional[tuple[StrategyStudyRun, List[LabBar]]]:
    run = (
        db.query(StrategyStudyRun)
        .filter(
            StrategyStudyRun.user_id == user_id,
            StrategyStudyRun.instrument_id == instrument_id,
            StrategyStudyRun.interval_minutes == interval_minutes,
        )
        .order_by(StrategyStudyRun.created_at.desc())
        .first()
    )
    if run is None or not run.bars_json:
        return None
    return run, _bars_from_json(run.bars_json, interval_minutes)


def _run_single_study(
    db: Session, user_id: UUID, instrument_id: str, interval_minutes: int
) -> dict:
    """Study one instrument/interval pair and persist where its bars came from.

    The venue is asked first, because that is what makes a daily study daily.
    Stored bars are a fallback for an unreachable venue, and when the fallback
    window is identical to the window the previous study already covered the
    honest outcome is "skipped": re-persisting an unchanged study would add a
    record without adding information.

    The integrity fields are the series' own verdict. They used to be the
    constants ``None``/``True``/``0``/``0``, which reported an unverified
    window as verified; ``None`` now means "this lane did not check it", which
    is what the column already allowed.
    """
    try:
        from app.providers.crypto_registry import get_crypto_provider

        series = get_crypto_provider().get_candle_series(
            instrument_id, interval_minutes=interval_minutes, limit=720
        )
    except Exception as exc:
        stored = _latest_stored_bars(db, user_id, instrument_id, interval_minutes)
        if stored is None:
            raise
        previous, bars = stored
        fingerprint = dataset_fingerprint(instrument_id, interval_minutes, bars)
        if fingerprint == previous.dataset_fingerprint:
            logger.info(
                "Study skipped for %s @ %sm: stored bars unchanged since run %s",
                instrument_id,
                interval_minutes,
                previous.id,
            )
            return _combo_summary(
                instrument_id,
                interval_minutes,
                status="skipped",
                reason="no_new_bars",
                bars=len(bars),
                data_nature="stored_bars_reused",
                fingerprint=fingerprint,
                reused_from_run_id=str(previous.id),
            )
        dataset = {
            "instrument_id": instrument_id,
            "interval_minutes": interval_minutes,
            "provider_id": previous.provider_id or _provider_of(instrument_id),
            "data_nature": "stored_bars_reused",
            "fingerprint": fingerprint,
            "integrity_ok": None,
            "gap_count": None,
            "missing_intervals": None,
            "fetched_at": previous.fetched_at,
            "is_stale": None,
        }
        return _study_and_record(
            db,
            user_id,
            instrument_id,
            interval_minutes,
            bars,
            dataset,
            fallback_from=exc,
            reused_from_run_id=str(previous.id),
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
    dataset = {
        "instrument_id": instrument_id,
        "interval_minutes": interval_minutes,
        "provider_id": _provider_of(instrument_id),
        "data_nature": "venue_completed_bars",
        "fingerprint": series.fingerprint,
        "integrity_ok": series.integrity_ok,
        "gap_count": len(series.gaps),
        "missing_intervals": series.missing_intervals,
        "fetched_at": series.fetched_at,
        "is_stale": series.is_stale,
    }
    return _study_and_record(
        db, user_id, instrument_id, interval_minutes, bars, dataset
    )


def _study_and_record(
    db: Session,
    user_id: UUID,
    instrument_id: str,
    interval_minutes: int,
    bars: List[LabBar],
    dataset: dict,
    *,
    fallback_from: Optional[Exception] = None,
    reused_from_run_id: Optional[str] = None,
) -> dict:
    if len(bars) < MIN_STUDY_BARS:
        raise ValueError(
            f"Insufficient bars: {len(bars)} < {MIN_STUDY_BARS}"
            + (f" (venue unreachable: {fallback_from})" if fallback_from else "")
        )

    result = run_study(bars, settings=ResearchSettings(), include_trades=True)
    persist_study(db, user_id=user_id, result=result, bars=bars, dataset=dataset)

    trades = result.get("walk_forward", {}).get("out_of_sample", {}).get("trades", 0)
    logger.info(
        "Study completed: %s @ %dm — %d trades (%s)",
        instrument_id,
        interval_minutes,
        trades,
        dataset["data_nature"],
    )
    return _combo_summary(
        instrument_id,
        interval_minutes,
        status="studied",
        trades=trades,
        bars=len(bars),
        data_nature=dataset["data_nature"],
        fingerprint=dataset["fingerprint"],
        integrity_ok=dataset["integrity_ok"],
        is_stale=dataset.get("is_stale"),
        reused_from_run_id=reused_from_run_id,
    )


def _bars_from_json(bars_json: list, interval_minutes: int) -> List[LabBar]:
    """Reconstruct LabBar list from stored bars_json."""
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


# ── the daily simulation tick ───────────────────────────────────────────


def run_simulation_cycle(
    *,
    scope: str,
    scheduled_for: Optional[str],
    user_id: Optional[UUID] = None,
    job_id: Optional[UUID] = None,
) -> dict:
    """Step each served account's open sessions once, filed as one day's work.

    This is the third consumer of the same tick body the daily cron and the
    interval collector use: the same kill-switch read, the same owner
    resolution shape, the same ledger, the same terminal truth table. What
    differs is the two things a second lane genuinely has a reason to differ
    on — which accounts it serves, and which switch can stop it.
    """
    return _run_tick(
        scope=scope,
        job_type=SIMULATION_JOB_TYPE,
        scheduled_for=scheduled_for,
        tick_owner=_advance_for_owner,
        user_id=user_id,
        job_id=job_id,
        owners_of=simulation_owner_ids,
        kill_switch_scope=KillSwitchScope.execution,
    )


def run_scheduled_simulations(
    scope: str = "daily",
    user_id: Optional[UUID] = None,
    *,
    job_id: Optional[UUID] = None,
) -> dict:
    """Advance every open replay of every served account by one stored bar.

    Argument-compatible with ``run_scheduled_research`` for the three values a
    dispatch carries (``scope``, ``user_id``, ``job_id``). It deliberately does
    not accept ``instruments`` or ``intervals``: a replay reads the dataset
    bound to its own session, so naming a different instrument here would be a
    parameter the tick cannot honour.
    """
    return run_simulation_cycle(
        scope=scope,
        scheduled_for=(
            None if scope == MANUAL_SCOPE else utcnow_aware().date().isoformat()
        ),
        user_id=user_id,
        job_id=job_id,
    )


def _advance_for_owner(
    db: Session,
    owner_id: UUID,
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    job_id: Optional[UUID] = None,
) -> dict:
    """One account's replay tick: one step per open session, then one truthful row.

    ``advance_daily_step`` is the function the user's own advance button runs,
    imported here rather than reimplemented: a service that advanced a session
    by a different rule than the workspace would put the fills the owner never
    agreed to in the owner's own book. The row is durable as ``running`` before
    the first session is stepped, so a tick that died mid-way leaves a visible
    record of having existed.

    A session whose dataset has run out is counted as work done, not as an
    error - completing the replay is what stepping it to the end means - while
    the sessions this lane cannot serve are named in ``skipped_detail`` instead
    of quietly reducing the number of sessions the row claims to have covered.
    """
    from app.api.simulations import advance_daily_step

    config: dict = {
        "scope": scope,
        "statuses_served": [status.value for status in SIMULATION_SERVED_STATUSES],
        "sessions_per_owner": SIMULATION_SESSIONS_PER_OWNER,
    }
    if scheduled_for is not None:
        config["scheduled_for"] = scheduled_for

    job = _open_job(
        db, owner_id, job_type=job_type, config=config, job_id=job_id
    )
    logger.info("Learning job %s started: %s simulation step", job.id, scope)

    sessions = (
        db.query(SimulationSession)
        .filter(
            SimulationSession.owner_id == owner_id,
            SimulationSession.status.in_(SIMULATION_SERVED_STATUSES),
        )
        .order_by(SimulationSession.created_at, SimulationSession.id)
        .limit(SIMULATION_SESSIONS_PER_OWNER)
        .all()
    )

    stepped: List[dict] = []
    skipped: List[dict] = []
    errors: List[str] = []

    for session in sessions:
        # The id is taken before the step because the step commits, and a
        # committed ORM object is expired - the row the record names has to be
        # the row that was actually stepped.
        session_id = str(session.id)
        timeframe = session.timeframe

        if timeframe in INTRADAY_TIMEFRAMES:
            # A daily clock cannot drive a bar clock: one tick per day would
            # need roughly forty-eight ticks to replay one session day, so the
            # bar lanes stay on their own entry point rather than being
            # advanced at a pace their own timeframe does not describe.
            skipped.append({
                "session_id": session_id,
                "timeframe": timeframe,
                "reason": "intraday_timeframe_needs_a_bar_clock",
            })
            continue

        version = session.experiment_version
        if version is None:
            errors.append(f"{session_id}: associated version not found")
            continue

        try:
            outcome = advance_daily_step(
                db,
                session=session,
                version=version,
                actor_id=owner_id,
                actor_type="system",
            )
        except HTTPException as exc:
            db.rollback()
            errors.append(f"{session_id}: {exc.detail}")
            logger.warning(
                "Simulation step refused for session %s: %s", session_id, exc.detail
            )
            continue
        except Exception as exc:
            db.rollback()
            errors.append(f"{session_id}: {exc}")
            logger.warning(
                "Simulation step failed for session %s: %s\n%s",
                session_id,
                exc,
                traceback.format_exc(),
            )
            continue

        stepped.append({
            "session_id": session_id,
            "outcome": outcome["outcome"],
            "step_number": outcome["step_number"],
            "snapshot_date": outcome["snapshot_date"],
            "timeframe": timeframe or "1d",
        })

    advanced = sum(1 for entry in stepped if entry["outcome"] == "advanced")
    exhausted = sum(1 for entry in stepped if entry["outcome"] == "completed")

    # Same table as the research tick: nothing succeeding is a failure, some
    # succeeding is partial, and a tick that reported no error is completed.
    if errors and not stepped:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "completed"

    job.status = status
    job.completed_at = utcnow_aware()
    job.result_json = {
        "scheduled_for": scheduled_for,
        "sessions_offered": len(sessions),
        "advanced": advanced,
        "exhausted": exhausted,
        "skipped": len(skipped),
        "failed": len(errors),
        "results": stepped,
        "skipped_detail": skipped,
        "errors": errors,
    }
    job.error_details = "\n".join(errors) if errors else None
    db.commit()

    logger.info(
        "Learning job %s %s: %d advanced, %d exhausted, %d skipped, %d failed",
        job.id,
        status,
        advanced,
        exhausted,
        len(skipped),
        len(errors),
    )
    return {"job_id": str(job.id), "user_id": str(owner_id), "status": status}


# ── the weekly analysis tick ────────────────────────────────────────────


def run_pattern_analysis_job(
    scope: str = "weekly",
    user_id: Optional[UUID] = None,
    *,
    job_id: Optional[UUID] = None,
) -> dict:
    """Run one pattern-analysis tick per served account."""
    return _run_tick(
        scope=scope,
        job_type=WEEKLY_JOB_TYPE,
        scheduled_for=(
            None if scope == MANUAL_SCOPE else utcnow_aware().date().isoformat()
        ),
        tick_owner=_analysis_for_owner,
        user_id=user_id,
        job_id=job_id,
    )


def _analysis_for_owner(
    db: Session,
    owner_id: UUID,
    *,
    scope: str,
    job_type: str,
    scheduled_for: Optional[str],
    job_id: Optional[UUID] = None,
) -> dict:
    """Weekly pattern analysis + parameter proposals for one account.

    1. Collect this owner's trades from their last N runs per instrument.
    2. Group by (rule_id, exit_id) and compute metrics.
    3. Identify patterns: best exit configs per skill, trending vs ranging.
    4. Generate LearningProposal records for parameter adjustments.
    5. Validate each proposal with a walk-forward backtest.
    6. A validated proposal is still only deployable by the owner's own POST.
    """
    config: dict = {"scope": scope, "lookback_runs": 50}
    if scheduled_for is not None:
        config["scheduled_for"] = scheduled_for

    job = _open_job(
        db, owner_id, job_type=job_type, config=config, job_id=job_id
    )
    logger.info("Pattern analysis job %s started", job.id)

    errors: List[str] = []
    analysis: dict = {}
    proposals_created = 0

    try:
        analysis = analyze_skill_patterns(db, user_id=owner_id, lookback_runs=50)

        for exit_id, metrics in analysis.get("per_exit_config", {}).items():
            try:
                proposals_created += _propose_for_exit(
                    db, owner_id=owner_id, job=job, exit_id=exit_id, metrics=metrics
                )
            except Exception as exc:
                db.rollback()
                errors.append(f"{exit_id}: {exc}")
                logger.warning(
                    "Proposal failed for %s: %s\n%s",
                    exit_id,
                    exc,
                    traceback.format_exc(),
                )
    except Exception as exc:
        errors.append(f"tick aborted: {exc}")
        logger.exception("Pattern analysis job %s aborted", job.id)

    if errors and not analysis:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "completed"

    job.status = status
    job.completed_at = utcnow_aware()
    job.result_json = {
        "scheduled_for": scheduled_for,
        "analysis": analysis,
        "proposals_created": proposals_created,
        "failed": len(errors),
        "errors": errors,
    }
    job.error_details = "\n".join(errors) if errors else None
    db.commit()
    logger.info(
        "Pattern analysis job %s %s: %d proposals, %d failed",
        job.id,
        status,
        proposals_created,
        len(errors),
    )
    return {"job_id": str(job.id), "user_id": str(owner_id), "status": status}


def _propose_for_exit(
    db: Session, *, owner_id: UUID, job: LearningJob, exit_id: str, metrics: dict
) -> int:
    """One exit config's weekly proposal. Returns 1 when a row was written."""
    exit_config = _resolve_exit_config(exit_id)
    if exit_config is None:
        return 0

    adjustment = propose_parameter_adjustment(
        current_exit=exit_config,
        performance_data=metrics,
    )
    if adjustment is None:
        return 0

    # Find the instrument/interval from the most recent run using this exit
    recent_run = _find_recent_run_for_exit(db, owner_id, exit_id)
    if recent_run is None:
        return 0

    # Evidence gating: propose_parameter_adjustment gates on trade_count >= 30
    # and marks all small-sample output as "hypothesis", not evidence-based.
    evidence_status = adjustment.get("evidence_status", "hypothesis")

    proposal = LearningProposal(
        user_id=owner_id,
        learning_job_id=job.id,
        rule_id=_find_best_rule_for_exit(db, owner_id, recent_run.id, exit_id),
        exit_id=exit_id,
        instrument_id=recent_run.instrument_id,
        interval_minutes=recent_run.interval_minutes,
        baseline_exit_config=adjustment["baseline_exit_config"],
        proposed_exit_config=adjustment["proposed_exit_config"],
        rationale_json={
            "pattern": adjustment["pattern"],
            "baseline_metrics": metrics,
            "rationale": adjustment["rationale"],
            "evidence_status": evidence_status,
        },
        status="pending",
    )
    db.add(proposal)
    db.flush()

    validation = validate_proposal(
        db,
        user_id=owner_id,
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

    # A hypothesis cannot be validated into a deployment: it stays pending for
    # the owner to review, whatever the backtest above happened to show.
    if evidence_status == "hypothesis":
        proposal.status = "pending"
        logger.info(
            "Proposal %s remains pending (hypothesis, not evidence-based)", proposal.id
        )
    elif validation.get("passed"):
        proposal.status = "validated"
        proposal.validated_at = utcnow_aware()
        logger.info(
            "Proposal %s validated: Sharpe %.2f -> %.2f",
            proposal.id,
            validation.get("sharpe_before", 0),
            validation.get("sharpe_after", 0),
        )
    elif validation.get("error") in (
        "no_baseline_run",
        "insufficient_bars",
        "unknown_rule",
    ):
        # Validation could not run because the data or the rule is missing —
        # kept as ``pending`` for manual review rather than rejected.
        proposal.status = "pending"
        logger.info(
            "Proposal %s pending (validation skipped: %s)",
            proposal.id,
            validation.get("error"),
        )
    else:
        proposal.status = "rejected"
        logger.info("Proposal %s rejected by validation", proposal.id)

    return 1


def _find_recent_run_for_exit(
    db: Session, user_id: UUID, exit_id: str
) -> Optional[StrategyStudyRun]:
    """Find the most recent run of this owner that has trades on this exit."""
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
