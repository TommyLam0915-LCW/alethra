"""The single accounting path for one strategy backtest.

A manual run (``POST /versions/{id}/runs``) and one cell of a robustness study
must reserve quota, execute the engine, settle or release, and persist the same
fields. Keeping that in one function is what makes "a study attempt is a real
research run" true instead of a parallel bookkeeping lane that can drift.

Transaction boundaries (2026-11-15, durable admission):

* **admission** — one transaction: version row lock, re-read of the active run
  under that lock, run row, quota reservation with its linkage,
  ``research_run_started`` audit, ``commit``. Nothing computes before it.
* **execution** — writes nothing, but it is **not transaction-free**. The engine
  reads the stored dataset through this same session, so its first SELECT opens
  a fresh transaction after the admission commit and that transaction is still
  open when the terminal write begins. Measured, not inferred: the admission
  test asserts ``in_transaction() is False`` at engine entry and ``True`` after
  the engine returns. What is claimed is only that no outcome data reaches the
  database outside the terminal transaction.
* **terminal** — one transaction: the conditional status write is taken first,
  and it is what acquires the right to touch the ledger. Only the writer whose
  UPDATE matched the row settles or releases, and only then does it write its own
  terminal audit event, so outcome, billing and audit commit together or not at
  all. A rollback therefore discards the half-written outcome and leaves the
  committed admitted row untouched. The dataset snapshot of a stored series is
  registered in this same transaction, after that write is won: a run that was
  never published leaves no snapshot behind, and the execution phase stays
  write-free.

So an independent observer sees a committed non-terminal run for exactly the
window in which a process intends to compute or is computing. That is what
makes an interrupted attempt observable instead of invisible. Ageing that
observation into a recovery decision is not wired in here:
``reclaim_abandoned_research_runs`` reads the aged rows, lists them and refuses
to act, because age is the only signal available and a slow backtest and a
killed one are the same row. It follows that this module has no release entry at
all — the only "proof" such an entry could be handed is a caller-supplied run
id, and that is a claim about a transaction, not evidence of one. An interrupted
attempt therefore keeps its version blocked and its quota unit held until an
outcome is written by the writer that owns it or by a recorded human decision.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.entitlements import (
    release_reservation,
    reserve_quota,
    settle_reservation,
)
from app.config import get_settings
from app.engine.backtest import ENGINE_VERSION, compute_config_hash, run_backtest
from app.lib.error_safety import safe_error_message
from app.lib.timeutil import utcnow
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User
from app.models.quota import QuotaReservation
from app.providers import get_data_provider
from app.services import emit_event
from app.services.dataset_registry import register_stored_bars_snapshot


logger = logging.getLogger(__name__)

RESEARCH_QUOTA_TYPE = "research_run"
NON_TERMINAL_RUN_STATUSES = (RunStatus.pending, RunStatus.running)
TERMINAL_RUN_STATUSES = (RunStatus.completed, RunStatus.failed)

RECOVERY_REFUSAL_REASON = (
    "Age-based recovery refused to act: an aged non-terminal row is not proof "
    "its process died, and this lane has no lease or heartbeat to check. No run "
    "was closed and no reservation was released. The row stays active and its "
    "version keeps refusing new admissions until an outcome is written by the "
    "writer that owns it or by an explicit, recorded operator decision."
)


class BacktestRefused(Exception):
    """The engine declined to compute a book, so there is no result to publish."""


# The lock order this module keeps, and the reason it can keep it: admission is
# the only writer of research_runs rows, and it always takes the version row
# before the quota rows. Reversing it somewhere else is what would deadlock.
LOCK_ORDER = (
    "experiment_versions -> research_runs -> quota_counters/users -> quota_reservations"
)


def active_run_conflict_detail(run_id: UUID) -> str:
    """The 409 a caller gets when this version already has an active run."""
    return (
        "A committed run of this version is still active as run "
        f"{run_id}. Admission is serialised on the version row, so a second "
        "run would double-bill the same work; it is not proof the first "
        "process is alive — the platform cannot see processes, only rows. "
        "Wait for an outcome on that run before starting another."
    )


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


# Only a series read wholly from storage can be registered: see
# ``_dataset_snapshot_reason`` for what each other case means to a reader.
SNAPSHOT_REGISTERED = "registered_stored_bars"


def _dataset_snapshot_reason(result: Dict[str, Any]) -> str:
    """Say, before anything is written, whether this run can name its dataset.

    The answer goes into the run's own record, because NULL on its own reads
    as an oversight. A fetched series lives in the venue's rolling window and
    cannot be reproduced from this database later, and a mixed read would put
    part of the price path outside the snapshot while the record implied it was
    inside. Both are refused, in words.
    """
    source_used = result.get("data_source_used")
    if source_used == "stored":
        if not result.get("dataset_checksum"):
            return "stored_series_without_checksum"
        if not result.get("dataset_coverage"):
            return "stored_series_without_coverage"
        return SNAPSHOT_REGISTERED
    if source_used == "mixed":
        return "mixed_sources_not_registered"
    return "provider_only"


def _register_dataset_snapshot(
    db: Session, result: Dict[str, Any]
) -> Tuple[Optional[UUID], str]:
    """Register the stored series this run priced off; return its id and the reason.

    Provenance is worth less than the result it describes: if the registry
    cannot write — a schema this build has not migrated, for instance — the
    backtest is still a completed run with its checksum, and the reason says
    the snapshot is missing rather than pretending it exists.
    """
    reason = _dataset_snapshot_reason(result)
    if reason != SNAPSHOT_REGISTERED:
        return None, reason
    coverage = result.get("dataset_coverage") or {}
    asset_ids = sorted(coverage)
    provider_ids = sorted(
        {
            provider
            for entry in coverage.values()
            for provider in (entry.get("provider_ids") or [])
        }
    )
    try:
        snapshot_id = register_stored_bars_snapshot(
            db,
            asset_ids=asset_ids,
            provider_ids=provider_ids,
            start_date=str(result["start_date"]),
            end_date=str(result["end_date"]),
            checksum=result.get("dataset_checksum"),
        )
    except SQLAlchemyError:
        logger.exception("stored dataset snapshot could not be registered")
        return None, "registry_write_failed"
    if snapshot_id is None:
        # The classifier above and the registry refuse the same two shapes, so
        # reaching here means the two have drifted. Reporting that is better
        # than recording a reason that promises a snapshot id was produced.
        return None, "registry_refused"
    return snapshot_id, reason


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
    actor_type: str = "unknown",
    charge_quota: bool = True,
) -> ResearchRun:
    """Run one backtest, billed against the user's research_run quota.

    Three transactions, in this order: admission (commits the attempt before
    the engine starts), execution (no writes), terminal (outcome plus
    settlement or release plus audit, one commit). See
    ``_admit_research_run`` for the lock and what a refusal leaves behind.

    ``strategy_spec`` overrides the version's stored spec for this one run
    (a study cell changes costs, parameters or the period). The run row stays
    bound to the source version and records the hash of the spec actually used,
    so a variant can never be mistaken for the published version.

    ``charge_quota=False`` keeps the run rows and evidence intact but leaves
    billing to the caller: the run is admitted with no reservation and nothing
    links the caller's unit to it, so neither settlement nor release here can
    touch it. Robustness studies use it because a whole study is charged as one
    authorization unit, not one unit per backtest.

    Terminal state and ledger are one contract: the conditional status write is
    what acquires the right to settle, release or audit, so a writer that did
    not win it neither bills the run nor adds a second outcome to a row somebody
    else already closed. A billed run whose linkage is missing or is not legally
    settleable is not published as completed either — the write is rolled back
    and the specific inconsistency is reported instead.

    Raises 409 if this version already has a committed active run, 429 if the
    allowance is spent, and never reports an outcome it could not record.
    """
    spec = strategy_spec if strategy_spec is not None else version.strategy_spec
    config_hash = compute_config_hash(spec)
    data_cutoff = spec.get("end_date")
    is_locked = experiment.status == "locked"

    # Everything the caller is billed for is decided and made durable here, in
    # one transaction that commits before a single bar is read.
    run = _admit_research_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        spec=spec,
        config_hash=config_hash,
        event_payload=event_payload,
        actor_type=actor_type,
        charge_quota=charge_quota,
    )

    try:
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

        result_payload = {
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
        completed_values = {
            "status": RunStatus.completed,
            "metrics": result["metrics"],
            "warnings": result.get("warnings", []),
            "dataset_checksum": result.get("dataset_checksum"),
            "methodology_json": result.get("methodology_summary"),
            "result_json": result_payload,
            "completed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }

        # The terminal write is taken before anything is touched, because its
        # rowcount is what acquires the right to touch them: the row has been
        # committed since admission, so whoever closed it first is visible to
        # this predicate, and an UPDATE without the status condition would flip
        # that terminal row back to completed and bill a unit the ledger already
        # released. Winning it is not a claim that the ledger agrees — that is
        # what the read below decides — but losing it ends this process's claim
        # to write at all.
        published = (
            db.query(ResearchRun)
            .filter(
                ResearchRun.id == run.id,
                ResearchRun.status.in_(NON_TERMINAL_RUN_STATUSES),
            )
            .update(completed_values, synchronize_session=False)
        )

        if published != 1:
            # This process holds a result it is not allowed to publish. Nothing
            # of ours reached the database — the UPDATE above matched zero rows,
            # so the rollback discards only the engine's reads — and the answer
            # is the outcome that is on record, not a new one: no settlement, no
            # release, no event of ours. What the row and its ledger say is
            # reported here rather than repaired by the writer that lost.
            db.rollback()
            current, unit_state = _committed_state(db, run.id)
            detail = (
                f"Result of run {run.id} discarded: the row is already {current}, "
                "so this process settled nothing, released nothing and wrote no "
                "outcome of its own."
            )
            if unit_state == "reserved":
                detail += (
                    f" Its attributed quota unit is still reserved, so the "
                    "writer that closed the row left it unbilled — reported, "
                    "not repaired here."
                )
            logger.error(detail)
            db.refresh(run)
            return run

        # Provenance is taken only by the writer that owns the outcome, and it
        # commits in the same terminal transaction: a process that lost the
        # conditional UPDATE above registers nothing, and the execution phase
        # stays write-free. The snapshot names the stored rows the engine read,
        # so the run can be reconciled against market_data_bars afterwards
        # instead of only against a checksum nobody can dereference.
        snapshot_id, snapshot_reason = _register_dataset_snapshot(db, result)
        run.dataset_snapshot_id = snapshot_id
        run.result_json = dict(result_payload, dataset_snapshot_reason=snapshot_reason)
        db.flush()

        # The billing contract was decided by the admission, not by this call
        # site: a run admitted as billed has exactly one unit that names it, is
        # owned by this user and is still ``reserved`` — anything else means the
        # completed write above is a claim the ledger cannot support, so it is
        # not committed. A run admitted as unbilled has no linkage at all, which
        # is what keeps a study cell away from the study's own unit: not a
        # special case, but the absence of the row that would make it reachable.
        unit_id = _linked_reservation(db, run_id=run.id, user_id=user.id)
        if charge_quota:
            if unit_id is None:
                raise RuntimeError(
                    f"Run {run.id} computed a result but holds no research_run "
                    "quota unit attributed to it, so it was not published as "
                    "completed: the admission reserved and attributed the unit in "
                    "the same transaction it committed the run in, so a billed "
                    "run without a linkage is a ledger this writer cannot settle "
                    "honestly. Nothing was committed."
                )
            if not _reservation_is_open(db, unit_id):
                raise RuntimeError(
                    f"Run {run.id} computed a result but its attributed quota "
                    f"unit {unit_id} is not in reserved, so it was not published "
                    "as completed: it can no longer be settled, and committing a "
                    "result beside a ledger that will never be billed is the "
                    "disagreement this rule exists to refuse. Nothing was "
                    "committed."
                )
            settle_reservation(db, unit_id)
        elif unit_id is not None:
            raise RuntimeError(
                f"Run {run.id} was admitted as unbilled, yet quota unit "
                f"{unit_id} attributes itself to it, so it was not published as "
                "completed: billing it here would take a unit that belongs to "
                "another lane, and leaving it reserved while claiming the work "
                "is finished would bill nothing for a result on record. Nothing "
                "was committed."
            )

        emit_event(
            db,
            entity_type="run",
            entity_id=run.id,
            event_type="research_run_completed",
            actor_id=user.id,
            actor_type=actor_type,
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
        # Also the way a ledger-contract refusal above reaches the row: the
        # completed write it refused is discarded by the rollback below, so the
        # run ends as failed with that reason instead of as completed with a
        # billing the ledger does not support.
        logger.exception("Research run failed")
        # Roll back before recording anything: an exception raised mid-publication
        # can leave the completed write, its settlement and part of its audit
        # applied in this session, and a partially published result reads as
        # evidence. The admitted row is already committed and survives the
        # rollback; only this transaction's unfinished outcome is discarded.
        db.rollback()

        try:
            # The write-off acquires the row first, so that the release and the
            # failure event below can only ever be acts of the writer that
            # actually closed the run. Done in the other order, a sweep that had
            # already closed the row and settled its unit would have its ledger
            # walked back to ``released`` by the late writer's own transaction.
            flipped = (
                db.query(ResearchRun)
                .filter(
                    ResearchRun.id == run.id,
                    ResearchRun.status.in_(NON_TERMINAL_RUN_STATUSES),
                )
                .update(
                    {
                        "status": RunStatus.failed,
                        "error_details": _run_failure_detail(e),
                        "completed_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    },
                    synchronize_session=False,
                )
            )
            if flipped != 1:
                # Somebody else closed this run, and their outcome is the
                # record. Nothing is committed from this branch: no release of
                # the unit their transaction already acted on, no second failure
                # event, and no repair of a ledger this process did not close —
                # only the specific state they left is reported.
                db.rollback()
                current, unit_state = _committed_state(db, run.id)
                logger.error(
                    "Write-off of run %s abandoned: the row is already %s and "
                    "its attributed quota unit is %s, so this process wrote no "
                    "outcome and changed no ledger for a run it did not close.",
                    run.id,
                    current,
                    unit_state if unit_state is not None else "unattributed",
                )
                db.refresh(run)
                return run

            # The same acquisition rule as the success path, from the other
            # side: a unit is handed back only if this call was the one admitted
            # to bill it. An unbilled run has no unit of its own, so nothing here
            # can reach a study's — and if the ledger nevertheless attributes one
            # to this run, that is a disagreement to report, not one to settle by
            # releasing it.
            unit_id = (
                _linked_reservation(db, run_id=run.id, user_id=user.id)
                if charge_quota
                else None
            )
            if unit_id is not None and _reservation_is_open(db, unit_id):
                release_reservation(db, unit_id)

            emit_event(
                db,
                entity_type="run",
                entity_id=run.id,
                event_type="research_run_failed",
                actor_id=user.id,
                actor_type=actor_type,
                payload={"error": _run_failure_detail(e)},
                rules_locked=is_locked,
                data_cutoff_at=data_cutoff,
            )

            db.commit()
        except Exception:
            # Neither a half-written failure record nor a success claim: close
            # the transaction and let the caller see that no outcome exists.
            db.rollback()
            logger.exception(
                "Run %s could not be written off as failed. Its admitted row "
                "is still active and its quota unit was neither settled nor "
                "released.",
                run.id,
            )
            raise

        db.refresh(run)
        return run


def _committed_state(db: Session, run_id: UUID) -> Tuple[Optional[str], Optional[str]]:
    """The run's committed status, and the state of the unit attributed to it.

    Every caller reads this immediately after its own ``rollback()``, so the
    session holds no write of ours and what it returns is the committed state
    somebody else left. ``None`` for the unit means no research_run quota
    attributes itself to the run at all — an unbilled study cell, or a ledger
    predating the linkage — which is a different fact from a unit that exists in
    some state.
    """
    row = (
        db.query(ResearchRun.status)
        .filter(ResearchRun.id == run_id)
        .one_or_none()
    )
    unit_row = (
        db.query(QuotaReservation.status)
        .filter(
            QuotaReservation.research_run_id == run_id,
            QuotaReservation.quota_type == RESEARCH_QUOTA_TYPE,
        )
        .one_or_none()
    )
    return (
        row[0] if row is not None else None,
        unit_row[0] if unit_row is not None else None,
    )


def _reservation_is_open(db: Session, reservation_id: UUID) -> bool:
    """Whether the reservation is still in ``reserved`` and may be settled.

    Both settle and release raise when the row was already closed, so a writer
    that races whoever else holds this run's unit must look before it acts.

    ``scalar()`` rather than ``one_or_none()`` because a one-column ORM query
    hands back a row, and a row bound as a uuid is a ``StatementError``, not a
    lookup miss.
    """
    return (
        db.query(QuotaReservation.status)
        .filter(QuotaReservation.id == reservation_id)
        .scalar()
    ) == "reserved"


def _linked_reservation(
    db: Session, *, run_id: UUID, user_id: Optional[UUID] = None
) -> Optional[UUID]:
    """The quota unit this run holds, read from the ledger by linkage.

    ``quota_reservations.research_run_id`` is written once, in the admission
    transaction, so settlement does not have to trust an object this process
    happened to carry, and a robustness study's unit (linked to the study, with
    this column NULL) is structurally out of reach here. A row predating the
    linkage is NULL and therefore unattributed: it is never settled or released
    from a run.

    ``user_id`` adds the owner half of the contract the admission wrote — unit,
    run and account named by one row — so settlement cannot be handed a unit
    attributed to this run under another owner. It is optional only because the
    recovery reads want the linkage alone.

    The row is unwrapped rather than returned: the value goes straight into a
    uuid bind, and ``one_or_none()`` on a one-column query is a row. Keeping
    ``one_or_none()`` is deliberate — more than one unit naming this run is a
    broken constraint and must raise, not pick one.
    """
    query = db.query(QuotaReservation.id).filter(
        QuotaReservation.research_run_id == run_id,
        QuotaReservation.quota_type == RESEARCH_QUOTA_TYPE,
    )
    if user_id is not None:
        query = query.filter(QuotaReservation.user_id == user_id)
    row = query.one_or_none()
    return row[0] if row is not None else None


def _link_reservation_to_run(
    db: Session, *, reservation_id: UUID, user_id: UUID, run_id: UUID
) -> None:
    """Attach a held unit to its run exactly once, or refuse the admission.

    Conditional on ``research_run_id IS NULL`` plus the owner, quota type and
    ``reserved`` status, so the rowcount is the answer to "did this admission
    take the unit". Anything other than one row means the unit is not this
    run's to bill, and the caller rolls the admission back.
    """
    linked = (
        db.query(QuotaReservation)
        .filter(
            QuotaReservation.id == reservation_id,
            QuotaReservation.user_id == user_id,
            QuotaReservation.quota_type == RESEARCH_QUOTA_TYPE,
            QuotaReservation.status == "reserved",
            QuotaReservation.research_run_id.is_(None),
        )
        .update({"research_run_id": run_id}, synchronize_session=False)
    )
    if linked != 1:
        raise RuntimeError(
            "The reserved research unit could not be attributed to this run; "
            "the admission was refused instead of billing an unowned unit."
        )


def _admit_research_run(
    db: Session,
    *,
    user: User,
    version: ExperimentVersion,
    experiment: Experiment,
    spec: Dict[str, Any],
    config_hash: str,
    event_payload: Optional[Dict[str, Any]],
    actor_type: str,
    charge_quota: bool,
) -> ResearchRun:
    """Commit the attempt, then and only then let the engine start.

    One transaction, in this order:

    1. ``SELECT ... FOR UPDATE`` on the ``experiment_versions`` row — the shared
       lock every admission of this version takes, from the API route and from a
       study cell alike. Lock order is ``LOCK_ORDER``: version, then run, then
       the quota rows ``reserve_quota`` serialises on.
    2. **Re-read** the active run while holding that lock. Whatever the caller
       queried before is not evidence: an admission committed in between is
       visible only now.
    3. Run row (``running``, ``started_at``), quota reservation, its linkage,
       and the ``research_run_started`` audit event.
    4. ``commit``.

    The refusal in step 2 and any failure in 3-4 roll the transaction back, so
    a refused or failed admission leaves no run row, no reservation and no
    audit event — nothing for a later commit in this session to publish. The
    engine is not called until this returns, which is what makes the committed
    row a true statement about the window in which it is being computed.
    """
    started_payload: Dict[str, Any] = {
        "version_id": str(version.id),
        "version_number": version.version_number,
        "durable_admission": True,
    }
    if event_payload:
        started_payload.update(event_payload)

    try:
        db.query(ExperimentVersion.id).filter(
            ExperimentVersion.id == version.id
        ).with_for_update().first()

        live = find_live_research_run(db, version_id=version.id)
        if live is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=active_run_conflict_detail(live.id),
            )

        run = ResearchRun(
            experiment_version_id=version.id,
            engine_version=ENGINE_VERSION,
            config_hash=config_hash,
            status=RunStatus.running,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(run)
        db.flush()

        if charge_quota:
            reservation = reserve_quota(db, user.id, RESEARCH_QUOTA_TYPE)
            _link_reservation_to_run(
                db, reservation_id=reservation.id, user_id=user.id, run_id=run.id
            )

        emit_event(
            db,
            entity_type="run",
            entity_id=run.id,
            event_type="research_run_started",
            actor_id=user.id,
            actor_type=actor_type,
            payload=started_payload,
            rules_locked=experiment.status == "locked",
            data_cutoff_at=spec.get("end_date"),
        )

        db.commit()
    except BaseException:
        # Covers the 409, a 429 from reserve_quota, a linkage refusal and a
        # commit failure alike: an attempt that was not admitted leaves no
        # rows behind, and no lock is held while the caller responds.
        db.rollback()
        raise

    return run


def find_live_research_run(db: Session, *, version_id: UUID) -> Optional[ResearchRun]:
    """A committed run for this version whose process may still be executing.

    Since durable admission this is the state the request path actually leaves
    behind: ``_admit_research_run`` commits a ``running`` row before the engine
    starts, so a second request — or an independent connection — sees the
    attempt while it computes, and an interrupted attempt leaves a row that can
    be read instead of a gap that cannot.

    This is a read of the visible state, not a lock. Only the caller that holds
    the version row lock may act on a null result: read without the lock, two
    admissions can both see nothing and both proceed.
    """
    return (
        db.query(ResearchRun)
        .filter(
            ResearchRun.experiment_version_id == version_id,
            ResearchRun.status.in_(NON_TERMINAL_RUN_STATUSES),
        )
        .first()
    )


def reclaim_abandoned_research_runs(
    db: Session,
    *,
    version_id: Optional[UUID] = None,
    stale_after_seconds: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """List aged runs and refuse to touch them.

    This used to close them. Since durable admission it deliberately does
    nothing to the database, for one reason: age is the only signal available
    here, and an aged row is not evidence that its process died — a slow
    backtest and a killed one look identical from the ledger. Closing on that
    guess writes a false ``failed`` outcome into the audit trail, hands back a
    quota unit an executing run is about to settle, and reports a cleanup that
    was never a cleanup.

    So the entry is fail-closed and writes nothing at all: no row is closed, and
    this module has no release entry for a sweep to call. A caller-supplied list
    of run ids is not proof — it is a claim about a transaction, and the previous
    revision of this file released a unit for a run its own test had never
    closed. Ownership of a quota unit is held by exactly one thing: the writer
    that won the conditional terminal UPDATE for the run the admission attributed
    the unit to, inside ``execute_backtest_run``. A dead process leaves no such
    writer, so nothing is released, and the return value says that instead of
    returning an empty list a caller could read as "nothing was abandoned".
    ``candidate_run_ids`` is what it would have acted on, for operators to see.

    Consequence, stated plainly: an interrupted attempt keeps its ``running``
    row, holds its quota unit, and keeps its version refusing new admissions
    until something writes an outcome. Writing that outcome requires a lease, a
    heartbeat, or an explicit operator decision — none of which is implemented
    here, and none of which should be guessed from a timeout.
    """
    if stale_after_seconds is None:
        stale_after_seconds = get_settings().research_run_stale_after_seconds
    reference_time = now or utcnow()
    cutoff = reference_time - timedelta(seconds=stale_after_seconds)

    # A read, with no lock and no write: what is observable is not what this
    # pass is allowed to change.
    candidates = (
        db.query(ResearchRun.id)
        .filter(
            ResearchRun.status.in_(NON_TERMINAL_RUN_STATUSES),
            ResearchRun.started_at.isnot(None),
            ResearchRun.started_at < cutoff,
        )
        .order_by(ResearchRun.started_at)
    )
    if version_id is not None:
        candidates = candidates.filter(ResearchRun.experiment_version_id == version_id)
    candidate_run_ids: List[UUID] = [row[0] for row in candidates.all()]

    if candidate_run_ids:
        logger.warning(
            "Recovery declined for %d aged research run(s) (%s): age is not "
            "proof of death, so no row was closed and no reservation released. "
            "Their versions stay blocked.",
            len(candidate_run_ids),
            ", ".join(str(run_id) for run_id in candidate_run_ids),
        )

    return {
        "refused": True,
        "reason": RECOVERY_REFUSAL_REASON,
        "candidate_run_ids": candidate_run_ids,
        "run_ids": [],
        "released_reservation_ids": [],
    }
