"""The single accounting path for one strategy backtest.

A manual run (``POST /versions/{id}/runs``) and one cell of a robustness study
must reserve quota, execute the engine, settle or release, and persist the same
fields. Keeping that in one function is what makes "a study attempt is a real
research run" true instead of a parallel bookkeeping lane that can drift.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.entitlements import (
    release_reservation,
    reserve_quota,
    settle_reservation,
)
from app.engine.backtest import ENGINE_VERSION, compute_config_hash, run_backtest
from app.lib.error_safety import safe_error_message
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User
from app.providers import get_data_provider
from app.services import emit_event


logger = logging.getLogger(__name__)


class BacktestRefused(Exception):
    """The engine declined to compute a book, so there is no result to publish."""


# PostgreSQL `undefined_column` / `undefined_table`: the database structure
# does not match the running code (the 2026-11-01 volume_decimal incident).
_SCHEMA_PG_CODES = {"42703", "42P01"}


def _run_failure_detail(exc: BaseException) -> str:
    """A run-failure message the UI can render without exposing SQL.

    ``str(SQLAlchemyError)`` embeds the failing statement, which names internal
    tables and columns; ``error_details`` is shown verbatim. The customer gets
    the classification and a reference, while the full exception still reaches
    the server log.
    """
    if isinstance(exc, SQLAlchemyError):
        pgcode = getattr(getattr(exc, "orig", None), "pgcode", None)
        if pgcode in _SCHEMA_PG_CODES:
            return (
                "The database structure does not match the running application, "
                "so this run could not read its dataset. No quota was consumed. "
                "Quote this run id when reporting it."
            )
        return (
            "The database could not complete this run, so no result was "
            "recorded. No quota was consumed. Quote this run id when "
            "reporting it."
        )
    return safe_error_message(exc)


def execute_backtest_run(
    db: Session,
    *,
    user: User,
    version: ExperimentVersion,
    experiment: Experiment,
    strategy_spec: Optional[Dict[str, Any]] = None,
    data_source: str = "auto",
    provider_id: Optional[str] = None,
    event_payload: Optional[Dict[str, Any]] = None,
) -> ResearchRun:
    """Run one backtest, billed against the user's research_run quota.

    ``strategy_spec`` overrides the version's stored spec for this one run
    (a study cell changes costs, parameters or the period). The run row stays
    bound to the source version and records the hash of the spec actually used,
    so a variant can never be mistaken for the published version.
    """
    spec = strategy_spec if strategy_spec is not None else version.strategy_spec
    config_hash = compute_config_hash(spec)

    run = ResearchRun(
        experiment_version_id=version.id,
        engine_version=ENGINE_VERSION,
        config_hash=config_hash,
        status=RunStatus.pending,
    )
    db.add(run)
    db.flush()

    data_cutoff = spec.get("end_date")
    is_locked = experiment.status == "locked"
    started_payload: Dict[str, Any] = {
        "version_id": str(version.id),
        "version_number": version.version_number,
    }
    if event_payload:
        started_payload.update(event_payload)

    emit_event(
        db,
        entity_type="run",
        entity_id=run.id,
        event_type="research_run_started",
        actor_id=user.id,
        payload=started_payload,
        rules_locked=is_locked,
        data_cutoff_at=data_cutoff,
    )

    reservation = reserve_quota(db, user.id, "research_run")

    try:
        run.status = RunStatus.running
        run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()

        data_provider = get_data_provider()
        result = run_backtest(
            spec,
            data_provider=data_provider,
            db_session=db,
            data_source=data_source,
            provider_id=provider_id,
        )

        if result.get("run_status") == "error":
            # A refusal is not a backtest that found no trades. Publishing it as
            # completed with empty transactions would read as evidence about the
            # strategy, and would charge the run against the user's quota.
            raise BacktestRefused("; ".join(result.get("errors") or ["backtest refused"]))

        run.status = RunStatus.completed
        run.metrics = result["metrics"]
        run.warnings = result.get("warnings", [])
        run.dataset_checksum = result.get("dataset_checksum")
        run.methodology_json = result.get("methodology_summary")
        run.result_json = {
            "transactions": result.get("transactions", []),
            "round_trips": result.get("round_trips", []),
            "daily_equity_curve": result.get("daily_equity_curve", []),
            "daily_accounting": result.get("daily_accounting", []),
            "data_provider": result.get("data_provider"),
            "feed_mode": result.get("feed_mode"),
            "data_source_used": result.get("data_source_used"),
            "data_nature": result.get("data_nature"),
            "dataset_checksum": result.get("dataset_checksum"),
            "dataset_coverage": result.get("dataset_coverage"),
            "bars_used": result.get("bars_used"),
            "start_date": result.get("start_date"),
            "end_date": result.get("end_date"),
            "initial_cash": result.get("initial_cash"),
            "final_equity": result.get("final_equity"),
        }
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        settle_reservation(db, reservation.id)

        emit_event(
            db,
            entity_type="run",
            entity_id=run.id,
            event_type="research_run_completed",
            actor_id=user.id,
            payload={
                "total_return_pct": result["metrics"].get("total_return_pct"),
                "total_trades": result["metrics"].get("total_trades"),
            },
            rules_locked=is_locked,
            data_cutoff_at=data_cutoff,
        )

        for w in result.get("warnings", []):
            emit_event(
                db,
                entity_type="run",
                entity_id=run.id,
                event_type="warning_generated",
                actor_id=user.id,
                actor_type="system",
                payload={"warning": w},
                rules_locked=is_locked,
                data_cutoff_at=data_cutoff,
            )

        db.commit()
        db.refresh(run)
        return run

    except Exception as e:
        logger.exception("Research run failed")
        release_reservation(db, reservation.id)

        run.status = RunStatus.failed
        run.error_details = _run_failure_detail(e)
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        emit_event(
            db,
            entity_type="run",
            entity_id=run.id,
            event_type="research_run_failed",
            actor_id=user.id,
            payload={"error": _run_failure_detail(e)},
            rules_locked=is_locked,
            data_cutoff_at=data_cutoff,
        )

        db.commit()
        db.refresh(run)
        return run
