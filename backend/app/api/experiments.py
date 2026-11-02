import copy
from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment, ExperimentStatus
from app.models.experiment_version import ExperimentVersion
from app.models.audit_event import AuditEvent
from app.models.research_note import ResearchNote
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.api.schemas import (
    AdHocBacktestResponse,
    AdHocDurableRunHint,
    ExperimentCreate,
    ExperimentUpdate,
    ExperimentLockRequest,
    ExperimentLockResponse,
    ExperimentResponse,
    ExperimentVersionResponse,
    LatestRunSummary,
    ResearchNoteCreate,
    ResearchNoteResponse,
    AuditEventResponse,
    VersionDiffResponse,
    VersionDiffEntry,
    RevisionCreate,
    RevisionOutcomeUpdate,
    RevisionResponse,
    StrategySpec,
)
from app.engine.backtest import ENGINE_VERSION, compute_config_hash, run_backtest
from app.lib.error_safety import safe_error_message
from app.services import emit_event
from app.services.experiment_record import build_experiment_record
from app.method_checks import evaluate_checks, MethodReviewResponse
from app.method_checks.loader import load_review_data

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("/", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(
    experiment_data: ExperimentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = Experiment(
        owner_id=current_user.id,
        title=experiment_data.title,
        original_idea=experiment_data.original_idea,
        research_type=experiment_data.research_type,
        hypothesis=experiment_data.hypothesis,
        falsification_conditions=experiment_data.falsification_conditions,
        benchmark=experiment_data.benchmark,
        scope=experiment_data.scope,
        status=ExperimentStatus.draft
    )
    db.add(experiment)
    db.flush()

    spec_dict = experiment_data.strategy_spec.model_dump()
    version = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=1,
        strategy_spec=spec_dict,
        assumptions={},
        config_hash=compute_config_hash(spec_dict),
        user_approved_at=datetime.now(timezone.utc).replace(tzinfo=None) if experiment_data.strategy_spec.user_approved_at else None,
        created_by=current_user.id
    )
    db.add(version)
    db.flush()

    experiment.current_version_id = version.id

    data_cutoff = spec_dict.get("end_date")

    emit_event(db,
        entity_type="experiment", entity_id=experiment.id,
        event_type="experiment_created", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"title": experiment_data.title},
        data_cutoff_at=data_cutoff)

    emit_event(db,
        entity_type="experiment", entity_id=experiment.id,
        event_type="dataset_selected", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"asset_ids": spec_dict["asset_ids"], "benchmark_id": spec_dict.get("benchmark_id")},
        data_cutoff_at=data_cutoff)

    emit_event(db,
        entity_type="version", entity_id=version.id,
        event_type="strategy_template_selected", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"template_type": spec_dict["template_type"], "version_number": 1},
        data_cutoff_at=data_cutoff)

    db.commit()
    db.refresh(experiment)
    return experiment


@router.get("/", response_model=List[ExperimentResponse])
def list_experiments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiments = db.query(Experiment).filter(
        Experiment.owner_id == current_user.id
    ).order_by(Experiment.updated_at.desc()).all()

    latest_runs = _latest_runs_by_experiment(db, [e.id for e in experiments])

    payloads: List[ExperimentResponse] = []
    for experiment in experiments:
        payload = ExperimentResponse.model_validate(experiment)
        payload.latest_run = latest_runs.get(experiment.id)
        payloads.append(payload)
    return payloads


def _latest_runs_by_experiment(db: Session, experiment_ids: List[UUID]) -> dict:
    """Newest research run per experiment (by completion, else start)."""
    if not experiment_ids:
        return {}

    from app.models.research_run import ResearchRun

    ordering_key = func.coalesce(
        ResearchRun.completed_at, ResearchRun.started_at
    )
    rows = (
        db.query(ResearchRun, ExperimentVersion.experiment_id)
        .join(ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id)
        .filter(ExperimentVersion.experiment_id.in_(experiment_ids))
        .order_by(ordering_key.desc().nullslast(), ResearchRun.id.desc())
        .all()
    )

    latest: dict = {}
    for run, experiment_id in rows:
        if experiment_id in latest:
            continue
        metrics = run.metrics or {}
        latest[experiment_id] = LatestRunSummary(
            id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            started_at=run.started_at,
            completed_at=run.completed_at,
            total_return_pct=metrics.get("total_return_pct"),
            max_drawdown_pct=metrics.get("max_drawdown_pct"),
            total_trades=metrics.get("total_trades"),
            trading_days=metrics.get("trading_days"),
        )
    return latest


@router.get("/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


def _freeze_version(
    db: Session,
    experiment: Experiment,
    version: ExperimentVersion,
    user: User,
    reason: str | None = None,
    actor_type: str = "unknown",
) -> datetime:
    """Freeze the reviewed contract of one version and record it once.

    The lock time is always the server's clock: a lock never inherits the
    version's creation time or the spec's data cutoff, because those describe
    the research, not the moment the owner committed to it.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    version.locked_at = now
    version.locked_by = user.id
    version.strategy_config_hash = version.config_hash
    version.dataset_cutoff_at = (version.strategy_spec or {}).get("end_date")
    version.engine_version = ENGINE_VERSION
    if reason:
        version.lock_reason = reason
    experiment.status = ExperimentStatus.locked
    experiment.updated_at = now
    emit_event(
        db,
        entity_type="experiment",
        entity_id=experiment.id,
        event_type="strategy_locked",
        actor_id=user.id,
        actor_type=actor_type,
        rules_locked=True,
        payload={
            "locked_at": now.isoformat(),
            "version_id": str(version.id),
            "version_number": version.version_number,
            "dataset_cutoff_at": version.dataset_cutoff_at,
        },
    )
    return now


@router.put("/{experiment_id}", response_model=ExperimentResponse)
def update_experiment(
    experiment_id: UUID,
    experiment_data: ExperimentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    if experiment.status == ExperimentStatus.locked:
        raise HTTPException(status_code=400, detail="Cannot modify locked experiment")

    if (
        experiment_data.strategy_spec is not None
        and experiment_data.scope is not None
        and experiment_data.scope.get("status") == "locked"
    ):
        raise HTTPException(
            status_code=400,
            detail="Strategy parameters cannot be changed and locked in the same request. "
                   "Save the new version first, then lock it.",
        )

    is_locked_after = False
    if experiment_data.hypothesis is not None and experiment_data.hypothesis != experiment.hypothesis:
        experiment.hypothesis = experiment_data.hypothesis
        emit_event(db,
            entity_type="experiment", entity_id=experiment.id,
            event_type="hypothesis_updated", actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            payload={"new_hypothesis": experiment_data.hypothesis})

    if experiment_data.falsification_conditions is not None and experiment_data.falsification_conditions != experiment.falsification_conditions:
        experiment.falsification_conditions = experiment_data.falsification_conditions
        emit_event(db,
            entity_type="experiment", entity_id=experiment.id,
            event_type="falsification_condition_added", actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            payload={"new_falsification_conditions": experiment_data.falsification_conditions})

    if experiment_data.title is not None:
        experiment.title = experiment_data.title
    if experiment_data.original_idea is not None:
        experiment.original_idea = experiment_data.original_idea
    if experiment_data.benchmark is not None:
        experiment.benchmark = experiment_data.benchmark
    if experiment_data.scope is not None:
        experiment.scope = experiment_data.scope

    if experiment_data.strategy_spec is not None:
        if not experiment_data.change_reason:
            raise HTTPException(
                status_code=400,
                detail="change_reason is required when modifying strategy parameters"
            )

        max_version = db.query(ExperimentVersion).filter(
            ExperimentVersion.experiment_id == experiment_id
        ).count()

        spec_dict = experiment_data.strategy_spec.model_dump()
        data_cutoff = spec_dict.get("end_date")

        new_version = ExperimentVersion(
            experiment_id=experiment.id,
            version_number=max_version + 1,
            strategy_spec=spec_dict,
            assumptions={},
            change_reason=experiment_data.change_reason,
            config_hash=compute_config_hash(spec_dict),
            user_approved_at=datetime.now(timezone.utc).replace(tzinfo=None) if experiment_data.strategy_spec.user_approved_at else None,
            created_by=current_user.id
        )
        db.add(new_version)
        db.flush()
        experiment.current_version_id = new_version.id

        emit_event(db,
            entity_type="version", entity_id=new_version.id,
            event_type="parameter_changed", actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            payload={
                "change_reason": experiment_data.change_reason,
                "version_number": new_version.version_number,
                "template_type": spec_dict["template_type"],
            },
            data_cutoff_at=data_cutoff)

        emit_event(db,
            entity_type="version", entity_id=new_version.id,
            event_type="strategy_version_created", actor_id=current_user.id,
            actor_type=request_actor_provenance(request),
            payload={"version_number": new_version.version_number},
            data_cutoff_at=data_cutoff)

    if experiment_data.scope is not None and experiment_data.scope.get("status") == "locked":
        experiment.status = ExperimentStatus.locked
        is_locked_after = True

        current_version = db.query(ExperimentVersion).filter(
            ExperimentVersion.id == experiment.current_version_id
        ).first()
        # A version that was already frozen keeps its original lock time and
        # audit event; this path only syncs the experiment-level status.
        if current_version is not None and current_version.locked_at is None:
            _freeze_version(db, experiment, current_version, current_user, actor_type=request_actor_provenance(request))

    experiment.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(experiment)
    return experiment


@router.post(
    "/{experiment_id}/versions/{version_id}/lock",
    response_model=ExperimentLockResponse,
)
def lock_version(
    experiment_id: UUID,
    version_id: UUID,
    lock_data: ExperimentLockRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Freeze the current version's strategy contract.

    Separate from ``PUT /experiments/{id}`` so a lock can never be bundled with
    a parameter change, and so an experiment that is already marked locked but
    whose version never got a lock stamp can be repaired without direct SQL.
    """
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id,
        ExperimentVersion.experiment_id == experiment_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if experiment.current_version_id != version.id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Only the experiment's current version can be locked. A revision "
                "of a locked experiment is a lineage record and cannot move the "
                "current pointer; to carry this version's research into a new "
                "lockable experiment, POST "
                f"/api/v1/experiments/{experiment_id}/versions/{version_id}/continue"
            ),
        )

    if not lock_data.confirm:
        raise HTTPException(
            status_code=400,
            detail="Locking a version requires explicit confirmation",
        )

    if version.locked_at is not None:
        if experiment.status != ExperimentStatus.locked:
            experiment.status = ExperimentStatus.locked
            experiment.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        return ExperimentLockResponse(
            experiment_id=experiment.id,
            version_id=version.id,
            experiment_status=ExperimentStatus.locked.value,
            locked_at=version.locked_at,
            strategy_config_hash=version.strategy_config_hash,
            already_locked=True,
        )

    _freeze_version(db, experiment, version, current_user, reason=lock_data.reason, actor_type=request_actor_provenance(request))
    db.commit()
    db.refresh(experiment)
    db.refresh(version)
    return ExperimentLockResponse(
        experiment_id=experiment.id,
        version_id=version.id,
        experiment_status=ExperimentStatus.locked.value,
        locked_at=version.locked_at,
        strategy_config_hash=version.strategy_config_hash,
        already_locked=False,
    )


@router.post(
    "/{experiment_id}/versions/{version_id}/continue",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
def continue_research(
    experiment_id: UUID,
    version_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Continue one version's research in a new, lockable experiment.

    A locked experiment cannot move ``current_version_id``: replay and forward
    simulation bind that pointer and require it to be locked. Instead of
    weakening that contract, the version is copied into a new experiment whose
    version 1 is current and unlocked, with an explicit lineage pointer back to
    the source version. The source is never modified.

    The copied approval stamp is cleared: an approval given for the source
    version is not an approval of the new strategy contract.
    """
    source = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="Experiment not found")

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id,
        ExperimentVersion.experiment_id == experiment_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # The new experiment starts unapproved. Dropping the key (rather than
    # writing False) keeps the copied spec schema-valid: the field is an ISO
    # timestamp, not a boolean.
    spec = copy.deepcopy(version.strategy_spec or {})
    spec.pop("user_approved_at", None)

    suffix = " (continued)"
    title = source.title
    if len(title) + len(suffix) > 500:
        title = title[: 500 - len(suffix)]
    title = f"{title}{suffix}"

    continuation = Experiment(
        owner_id=current_user.id,
        workspace_id=source.workspace_id,
        title=title,
        original_idea=source.original_idea,
        research_type=source.research_type,
        hypothesis=source.hypothesis,
        falsification_conditions=source.falsification_conditions,
        benchmark=source.benchmark,
        scope=copy.deepcopy(source.scope),
        status=ExperimentStatus.draft,
        visibility=source.visibility,
        continued_from_experiment_id=source.id,
        continued_from_version_id=version.id,
    )
    db.add(continuation)
    db.flush()

    lineage = {
        "experiment_id": str(source.id),
        "version_id": str(version.id),
        "version_number": version.version_number,
    }
    new_version = ExperimentVersion(
        experiment_id=continuation.id,
        version_number=1,
        strategy_spec=spec,
        assumptions={"continued_from": lineage},
        change_reason=(
            f"Continued from experiment {source.id} version {version.version_number}"
        ),
        config_hash=compute_config_hash(spec),
        user_approved_at=None,
        created_by=current_user.id,
    )
    db.add(new_version)
    db.flush()

    continuation.current_version_id = new_version.id
    data_cutoff = spec.get("end_date")

    emit_event(
        db,
        entity_type="experiment",
        entity_id=continuation.id,
        event_type="experiment_created",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"title": title, "continued_from": lineage},
        data_cutoff_at=data_cutoff,
    )
    emit_event(
        db,
        entity_type="experiment",
        entity_id=continuation.id,
        event_type="experiment_continued",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            **lineage,
            "source_experiment_id": str(source.id),
            "source_version_id": str(version.id),
            "new_version_id": str(new_version.id),
        },
        data_cutoff_at=data_cutoff,
    )
    emit_event(
        db,
        entity_type="experiment",
        entity_id=continuation.id,
        event_type="dataset_selected",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "asset_ids": spec.get("asset_ids", []),
            "benchmark_id": spec.get("benchmark_id"),
        },
        data_cutoff_at=data_cutoff,
    )

    db.commit()
    db.refresh(continuation)
    return continuation


@router.get("/{experiment_id}/versions", response_model=List[ExperimentVersionResponse])
def list_versions(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    versions = db.query(ExperimentVersion).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).order_by(ExperimentVersion.version_number.desc()).all()
    return versions


PERIOD_FIELDS = frozenset({"start_date", "end_date"})
# Approval stamps are set per save, so they must never read as a strategy edit.
IGNORED_CHANGE_FIELDS = frozenset({"user_approved_at"})
# Execution-affecting keys the revision authoring surface does not cover.
INTRADAY_KEYS = frozenset({"session", "timeframe"})


def _canonical_spec(spec: dict | None) -> dict | None:
    """Normalize a stored spec through the schema, or None if it cannot be.

    Versions created before the schema grew defaults lack fields such as
    schema_version/frequency, and some carry fixture-only keys (dataset,
    synthetic). Comparing raw dicts would report those as research changes, and
    copying them forward would carry stale dataset claims into a new version.
    """
    try:
        model = StrategySpec.model_validate(spec or {})
    except ValidationError:
        return None
    return model.model_dump(exclude={"user_approved_at"})


def _flatten_spec(spec: dict | None, prefix: str = "") -> dict:
    flat: dict = {}
    for key, value in (spec or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_spec(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _classify_change(old_spec: dict, new_spec: dict) -> tuple[List[str], str]:
    old_flat = _flatten_spec(old_spec)
    new_flat = _flatten_spec(new_spec)
    changed = sorted(
        key
        for key in set(old_flat) | set(new_flat)
        if key not in IGNORED_CHANGE_FIELDS
        and old_flat.get(key) != new_flat.get(key)
    )
    period_changed = any(key in PERIOD_FIELDS for key in changed)
    strategy_changed = any(key not in PERIOD_FIELDS for key in changed)
    if period_changed and strategy_changed:
        return changed, "strategy_and_period"
    if period_changed:
        return changed, "period_only"
    return changed, "strategy_only"


def _research_signature(spec: dict | None) -> str:
    """Hash the research configuration, ignoring per-save approval stamps.

    Canonical form first so a revision cannot slip past duplicate detection by
    differing only in schema defaults.
    """
    canonical = _canonical_spec(spec)
    if canonical is None:
        canonical = {
            key: value
            for key, value in (spec or {}).items()
            if key not in IGNORED_CHANGE_FIELDS
        }
    return compute_config_hash(canonical)


def _revision_meta(version: ExperimentVersion) -> dict | None:
    assumptions = version.assumptions or {}
    meta = assumptions.get("revision")
    return meta if isinstance(meta, dict) else None


def _build_revision_response(
    experiment: Experiment,
    version: ExperimentVersion,
    changed_fields: List[str],
    change_kind: str,
) -> RevisionResponse:
    return RevisionResponse(
        version=ExperimentVersionResponse.model_validate(version),
        is_current=experiment.current_version_id == version.id,
        changed_fields=changed_fields,
        change_kind=change_kind,
    )


@router.post(
    "/{experiment_id}/revisions",
    response_model=RevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_revision(
    experiment_id: UUID,
    payload: RevisionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Derive a new version from an existing one without rewriting the source.

    A revision is a research hypothesis, so it is refused when nothing differs
    from the source and when an identical configuration already exists: neither
    would produce new evidence, only a duplicate row.
    """
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    base = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == payload.base_version_id,
        ExperimentVersion.experiment_id == experiment_id,
    ).first()
    if not base:
        raise HTTPException(status_code=404, detail="Base version not found")

    existing_versions = db.query(ExperimentVersion).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).all()

    if payload.request_id is not None:
        for version in existing_versions:
            meta = _revision_meta(version)
            if meta and meta.get("request_id") == str(payload.request_id):
                return _build_revision_response(
                    experiment,
                    version,
                    list(meta.get("changed_fields") or []),
                    meta.get("change_kind") or "strategy_only",
                )

    base_spec = base.strategy_spec or {}
    intraday_keys = sorted(set(base_spec) & INTRADAY_KEYS)
    base_canonical = _canonical_spec(base_spec)
    if intraday_keys or base_canonical is None:
        unsupported = ", ".join(intraday_keys) if intraday_keys else "非日線頻率"
        raise HTTPException(
            status_code=400,
            detail=(
                f"來源版本含有目前不支援的設定（{unsupported}），"
                "修訂介面只支援日線策略，不會自動降級為日線；"
                "請以支援的日線策略建立新實驗。"
            ),
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    spec_dict = payload.strategy_spec.model_dump()
    spec_dict["user_approved_at"] = now.isoformat()

    changed_fields, change_kind = _classify_change(base_canonical, spec_dict)
    if not changed_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                "這個修訂與來源版本完全相同，沒有可研究的變更；"
                "請修改至少一個欄位，或直接沿用來源版本。"
            ),
        )

    signature = _research_signature(spec_dict)
    for version in existing_versions:
        if _research_signature(version.strategy_spec) == signature:
            raise HTTPException(
                status_code=400,
                detail=f"相同設定的版本已存在（v{version.version_number}），未建立重複版本。",
            )

    next_number = (
        db.query(func.max(ExperimentVersion.version_number))
        .filter(ExperimentVersion.experiment_id == experiment_id)
        .scalar()
        or 0
    ) + 1

    revision_meta = {
        "of_version_id": str(base.id),
        "of_version_number": base.version_number,
        "expectation": payload.expectation,
        "changed_fields": changed_fields,
        "change_kind": change_kind,
        "request_id": str(payload.request_id) if payload.request_id else None,
        "outcome": None,
        "met_expectation": None,
        "outcome_recorded_at": None,
        "outcome_recorded_by": None,
    }

    revision = ExperimentVersion(
        experiment_id=experiment.id,
        version_number=next_number,
        strategy_spec=spec_dict,
        assumptions={"revision": revision_meta},
        change_reason=payload.change_reason.strip(),
        config_hash=compute_config_hash(spec_dict),
        user_approved_at=now,
        created_by=current_user.id,
    )
    db.add(revision)
    db.flush()

    # Moving the current pointer would break the replay/simulation entry, which
    # binds the experiment's current version and requires it to be locked.
    source_locked = base.locked_at is not None
    becomes_current = (
        experiment.status != ExperimentStatus.locked and not source_locked
    )
    if becomes_current:
        experiment.current_version_id = revision.id
        experiment.updated_at = now

    data_cutoff = spec_dict.get("end_date")
    emit_event(
        db,
        entity_type="version",
        entity_id=revision.id,
        event_type="strategy_version_created",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "version_number": next_number,
            "revision_of_version_id": str(base.id),
            "revision_of_version_number": base.version_number,
            "change_kind": change_kind,
            "changed_fields": changed_fields,
        },
        rules_locked=source_locked,
        data_cutoff_at=data_cutoff,
    )
    emit_event(
        db,
        entity_type="version",
        entity_id=revision.id,
        event_type="parameter_changed",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "change_reason": payload.change_reason.strip(),
            "expectation": payload.expectation,
            "version_number": next_number,
            "template_type": spec_dict["template_type"],
            "changed_fields": changed_fields,
        },
        rules_locked=source_locked,
        data_cutoff_at=data_cutoff,
    )

    db.commit()
    db.refresh(revision)
    db.refresh(experiment)

    return _build_revision_response(
        experiment, revision, changed_fields, change_kind
    )


@router.patch(
    "/{experiment_id}/revisions/{version_id}/outcome",
    response_model=ExperimentVersionResponse,
)
def record_revision_outcome(
    experiment_id: UUID,
    version_id: UUID,
    payload: RevisionOutcomeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record whether a revision met its stated expectation.

    This is an annotation on a research outcome, not a strategy edit: the spec,
    hash and lock identity are left untouched.
    """
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id,
        ExperimentVersion.experiment_id == experiment_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    meta = _revision_meta(version)
    if meta is None:
        raise HTTPException(
            status_code=400,
            detail="此版本不是修訂版本，沒有可記錄的預期結果。",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    updated_meta = {
        **meta,
        "outcome": payload.outcome.strip(),
        "met_expectation": payload.met_expectation,
        "outcome_recorded_at": now.isoformat(),
        "outcome_recorded_by": str(current_user.id),
    }
    version.assumptions = {
        **(version.assumptions or {}),
        "revision": updated_meta,
    }

    emit_event(
        db,
        entity_type="version",
        entity_id=version.id,
        event_type="revision_outcome_recorded",
        actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "version_number": version.version_number,
            "met_expectation": payload.met_expectation,
        },
        data_cutoff_at=(version.strategy_spec or {}).get("end_date"),
    )

    db.commit()
    db.refresh(version)
    return version


@router.get("/{experiment_id}/timeline", response_model=List[AuditEventResponse])
def get_timeline(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    version_ids = [str(v.id) for v in db.query(ExperimentVersion.id).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).all()]

    from app.models.research_run import ResearchRun
    run_ids = [str(r.id) for r in db.query(ResearchRun.id).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).all()]

    note_ids = [str(n.id) for n in db.query(ResearchNote.id).filter(
        ResearchNote.experiment_id == experiment_id
    ).all()]

    entity_ids = [str(experiment.id)] + version_ids + run_ids + note_ids

    events = db.query(AuditEvent).filter(
        AuditEvent.entity_id.in_(entity_ids)
    ).order_by(AuditEvent.created_at.asc()).all()
    return events


@router.post("/{experiment_id}/notes", response_model=ResearchNoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(
    experiment_id: UUID,
    note_data: ResearchNoteCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    note = ResearchNote(
        experiment_id=experiment.id,
        version_id=note_data.version_id,
        author_id=current_user.id,
        content=note_data.content,
    )
    db.add(note)
    db.flush()

    emit_event(db,
        entity_type="note", entity_id=note.id,
        event_type="research_note_added", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={"experiment_id": str(experiment.id), "version_id": str(note_data.version_id) if note_data.version_id else None})

    db.commit()
    db.refresh(note)
    return note


@router.get("/{experiment_id}/notes", response_model=List[ResearchNoteResponse])
def list_notes(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    notes = db.query(ResearchNote).filter(
        ResearchNote.experiment_id == experiment.id
    ).order_by(ResearchNote.created_at.desc()).all()
    return notes


def _owned_experiment(db: Session, experiment_id: UUID, user_id: UUID) -> Experiment:
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == user_id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


@router.get("/{experiment_id}/record")
def get_experiment_record(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Read-only view of the same record the export file contains.

    Reading the panel must not create an export event; only downloading does.
    """
    experiment = _owned_experiment(db, experiment_id, current_user.id)
    return build_experiment_record(db, experiment)


@router.get("/{experiment_id}/export")
def export_experiment(
    experiment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    experiment = _owned_experiment(db, experiment_id, current_user.id)
    record = build_experiment_record(db, experiment)

    emit_event(db,
        entity_type="experiment", entity_id=str(experiment.id),
        event_type="export_generated", actor_id=current_user.id,
        actor_type=request_actor_provenance(request),
        payload={
            "version_count": record["coverage"]["versions"],
            "run_count": record["coverage"]["runs"],
            "schema_version": record["schema_version"],
        })

    db.commit()

    return record


@router.get("/{experiment_id}/method-review", response_model=MethodReviewResponse)
def get_method_review(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Evaluate 10 deterministic method checks for an experiment."""
    experiment, version, run = load_review_data(db, experiment_id, current_user)

    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Extract strategy_spec from version (or use empty dict)
    strategy_spec = version.strategy_spec if version else {}
    metrics = run.metrics if run else {}

    checks, config_hash_match = evaluate_checks(experiment, version, run, strategy_spec, metrics)

    completed_at_available = None
    if run is not None:
        completed_at_available = getattr(run, "completed_at", None) is not None

    return MethodReviewResponse(
        rule_set_version="1.0",
        experiment_id=experiment.id,
        version_id=version.id if version else None,
        run_id=run.id if run else None,
        run_selection="latest_completed_by_completed_at_then_id",
        config_hash_match=config_hash_match,
        completed_at_available=completed_at_available,
        checks=checks,
    )


@router.post(
    "/{experiment_id}/versions/{version_id}/backtest",
    response_model=AdHocBacktestResponse,
    summary="Run a backtest without persisting a research run",
    description=(
        "Executes the version's strategy against stored data and returns the "
        "computed metrics, curves and trades directly. The result is NOT "
        "stored: persisted is always false, run_id is always null, no research "
        "run row is created, no quota is reserved or settled, and the response "
        "cannot be cited, compared or saved as a run reference. Use "
        "POST /api/v1/versions/{version_id}/runs for a durable, auditable, "
        "quota-metered run. This path keeps its previous 200 behaviour for "
        "existing callers."
    ),
)
def run_experiment_backtest(
    experiment_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Run backtest for an experiment version and return the results.

    Executes the strategy against historical data and computes performance
    metrics. This ad-hoc endpoint does not persist a run row; use
    POST /versions/{version_id}/runs for a durable, auditable run.
    """
    # Load experiment and verify ownership
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Load version and verify it belongs to this experiment
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id,
        ExperimentVersion.experiment_id == experiment_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Experiment version not found")

    # Extract strategy spec from version
    strategy_spec = version.strategy_spec or {}

    try:
        # Run backtest engine
        result = run_backtest(strategy_spec, db_session=db)

        # Computed and returned, never stored: no run row, no quota movement.
        computed_at = datetime.now(timezone.utc).isoformat()
        return AdHocBacktestResponse(
            status=result.get("run_status", "error"),
            experiment_id=str(experiment_id),
            version_id=str(version_id),
            metrics=result.get("metrics", {}),
            equity_curve=result.get("daily_equity_curve", []),
            benchmark_equity_curve=result.get("benchmark_equity_curve", []),
            trades=result.get("transactions", []),
            round_trips=result.get("round_trips", []),
            config_hash=result.get("config_hash"),
            engine_version=result.get("engine_version"),
            data_provider=result.get("data_provider"),
            feed_mode=result.get("feed_mode"),
            data_source_used=result.get("data_source_used"),
            data_nature=result.get("data_nature"),
            dataset_checksum=result.get("dataset_checksum"),
            warnings=result.get("warnings", []),
            errors=result.get("errors", []),
            computed_at=computed_at,
            completed_at=computed_at,
            durable_run=AdHocDurableRunHint(
                path="/api/v1/versions/{version_id}/runs",
                note=(
                    "Substitute this version's id to create a stored research "
                    "run (id, dataset checksum, metrics) that can be read back, "
                    "compared and cited; it reserves and settles research-run "
                    "quota. Requires entitlement and the write API-key scope."
                ),
            ),
        )
    except Exception as e:
        # str(e) can carry absolute paths, a DSN or a token; the engine's own
        # traceback still goes to the server log.
        raise HTTPException(
            status_code=500,
            detail=f"Backtest execution failed: {safe_error_message(e)}"
        )
