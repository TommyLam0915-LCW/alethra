from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.model_research import (
    ModelResearchRecord,
    ModelStatus,
    ModelVersion,
    ModelEvaluation,
    PersonalResearchMemory,
    MemoryType,
    PrivacyScope,
    ModelCard,
)
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.api.schemas import (
    ModelResearchRecordCreate,
    ModelResearchRecordUpdate,
    ModelResearchRecordResponse,
    ModelVersionCreate,
    ModelVersionFromStrategy,
    ActiveVersionSet,
    ModelVersionRollback,
    ModelVersionResponse,
    ModelEvaluationCreate,
    ModelEvaluationResponse,
    PersonalResearchMemoryCreate,
    PersonalResearchMemoryConfirm,
    PersonalResearchMemoryResponse,
    ModelCardCreate,
    ModelCardUpdate,
    ModelCardResponse,
    VALID_STATUS_TRANSITIONS,
)
from app.services import emit_event

router = APIRouter(prefix="/model-research", tags=["model-research"])

_MISSING = object()
SPEC_DIFF_LIMIT = 200


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _flatten_spec(value, prefix: str = "") -> dict:
    """Flatten nested spec JSON into {dotted.path: leaf} for comparison."""
    if isinstance(value, dict):
        flat = {}
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_spec(value[key], child))
        return flat
    return {prefix: value}


def _spec_changes(spec_a: dict, spec_b: dict):
    flat_a = _flatten_spec(spec_a or {})
    flat_b = _flatten_spec(spec_b or {})
    changes = []
    for path in sorted(set(flat_a) | set(flat_b)):
        before = flat_a.get(path, _MISSING)
        after = flat_b.get(path, _MISSING)
        if before == after:
            continue
        if before is _MISSING:
            kind = "added"
        elif after is _MISSING:
            kind = "removed"
        else:
            kind = "changed"
        changes.append({
            "path": path,
            "kind": kind,
            "a": None if before is _MISSING else before,
            "b": None if after is _MISSING else after,
        })
    return changes[:SPEC_DIFF_LIMIT], len(changes) > SPEC_DIFF_LIMIT


def _get_record_with_ownership(
    record_id: UUID, db: Session, current_user: User
) -> ModelResearchRecord:
    record = db.query(ModelResearchRecord).filter(ModelResearchRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Model research record not found")
    if record.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return record


def _require_editable(record: ModelResearchRecord) -> None:
    if record.status not in (ModelStatus.draft, ModelStatus.researching):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot add versions when record status is '{record.status.value}'",
        )


def _insert_model_version(
    db: Session,
    record: ModelResearchRecord,
    current_user: User,
    *,
    actor_type: str = "unknown",
    model_type: str,
    model_specification_json: dict,
    linked_experiment_version_ids: Optional[List[UUID]] = None,
    source: Optional[str] = None,
) -> ModelVersion:
    existing_count = db.query(ModelVersion).filter(
        ModelVersion.model_research_record_id == record.id
    ).count()

    version = ModelVersion(
        model_research_record_id=record.id,
        version_number=existing_count + 1,
        model_type=model_type,
        model_specification_json=model_specification_json,
        linked_experiment_version_ids=(
            [str(v) for v in linked_experiment_version_ids]
            if linked_experiment_version_ids else None
        ),
    )
    db.add(version)
    db.flush()

    payload = {
        "record_id": str(record.id),
        "version_number": version.version_number,
    }
    if source:
        payload["source"] = source
    emit_event(
        db,
        actor_type=actor_type,
        entity_type="model_version",
        entity_id=version.id,
        event_type="model_version_created",
        actor_id=current_user.id,
        payload=payload,
    )
    return version


# ── ModelResearchRecord CRUD ──────────────────────────────────────────


@router.post("", response_model=ModelResearchRecordResponse, status_code=status.HTTP_201_CREATED)
def create_model_research_record(
    payload: ModelResearchRecordCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = ModelResearchRecord(
        owner_id=current_user.id,
        title=payload.title,
        description=payload.description,
        purpose=payload.purpose,
        status=ModelStatus.draft,
        allowed_asset_universe=payload.allowed_asset_universe,
        allowed_research_actions=payload.allowed_research_actions,
    )
    db.add(record)
    db.flush()

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_research_record",
        entity_id=record.id,
        event_type="model_research_created",
        actor_id=current_user.id,
        payload={"title": payload.title},
    )

    db.commit()
    db.refresh(record)
    return record


@router.get("", response_model=List[ModelResearchRecordResponse])
def list_model_research_records(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ModelResearchRecord).filter(ModelResearchRecord.owner_id == current_user.id)
    if status_filter:
        query = query.filter(ModelResearchRecord.status == status_filter)
    return query.order_by(ModelResearchRecord.created_at.desc()).all()


@router.get("/{record_id}", response_model=ModelResearchRecordResponse)
def get_model_research_record(
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_record_with_ownership(record_id, db, current_user)


@router.patch("/{record_id}", response_model=ModelResearchRecordResponse)
def update_model_research_record(
    record_id: UUID,
    payload: ModelResearchRecordUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _get_record_with_ownership(record_id, db, current_user)

    if payload.status is not None:
        current_status = record.status.value if hasattr(record.status, "value") else record.status
        allowed = VALID_STATUS_TRANSITIONS.get(current_status, set())
        if payload.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from '{current_status}' to '{payload.status}'. "
                       f"Allowed: {sorted(allowed)}",
            )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "status":
            setattr(record, field, ModelStatus(value))
        else:
            setattr(record, field, value)

    record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if payload.status:
        emit_event(
            db,
            actor_type=request_actor_provenance(request),
            entity_type="model_research_record",
            entity_id=record.id,
            event_type="model_research_status_changed",
            actor_id=current_user.id,
            payload={"old_status": current_status, "new_status": payload.status},
        )

    db.commit()
    db.refresh(record)
    return record


# ── ModelVersion CRUD ─────────────────────────────────────────────────


@router.post(
    "/{record_id}/versions",
    response_model=ModelVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_model_version(
    record_id: UUID,
    payload: ModelVersionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _get_record_with_ownership(record_id, db, current_user)
    _require_editable(record)

    version = _insert_model_version(
        db,
        record,
        current_user,
        actor_type=request_actor_provenance(request),
        model_type=payload.model_type,
        model_specification_json=payload.model_specification_json,
        linked_experiment_version_ids=payload.linked_experiment_version_ids,
    )
    version.prompt_version = payload.prompt_version
    version.input_schema_version = payload.input_schema_version
    version.output_schema_version = payload.output_schema_version
    version.linked_dataset_snapshot_ids = (
        [str(v) for v in payload.linked_dataset_snapshot_ids]
        if payload.linked_dataset_snapshot_ids else None
    )
    version.linked_simulation_session_ids = (
        [str(v) for v in payload.linked_simulation_session_ids]
        if payload.linked_simulation_session_ids else None
    )

    db.commit()
    db.refresh(version)
    return version


@router.post(
    "/{record_id}/versions/from-strategy",
    response_model=ModelVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_model_version_from_strategy(
    record_id: UUID,
    payload: ModelVersionFromStrategy,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Freeze one of the caller's own strategy versions into a model version.

    The strategy spec is copied, never referenced live, so later revisions of
    the experiment cannot silently change what this version represents.
    """
    record = _get_record_with_ownership(record_id, db, current_user)
    _require_editable(record)

    source = (
        db.query(ExperimentVersion)
        .join(Experiment, ExperimentVersion.experiment_id == Experiment.id)
        .filter(
            ExperimentVersion.id == payload.experiment_version_id,
            Experiment.owner_id == current_user.id,
        )
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Experiment version not found")

    experiment = db.query(Experiment).filter(Experiment.id == source.experiment_id).first()
    model_specification_json = {
        "source": "strategy_version",
        "experiment_id": str(source.experiment_id),
        "experiment_title": experiment.title if experiment else None,
        "experiment_version_id": str(source.id),
        "experiment_version_number": source.version_number,
        "strategy_spec": source.strategy_spec,
        "config_hash": source.config_hash,
        "change_reason": source.change_reason,
        "note": payload.note,
    }

    version = _insert_model_version(
        db,
        record,
        current_user,
        actor_type=request_actor_provenance(request),
        model_type=payload.model_type,
        model_specification_json=model_specification_json,
        linked_experiment_version_ids=[source.id],
        source="strategy_version",
    )

    db.commit()
    db.refresh(version)
    return version


@router.get("/{record_id}/versions", response_model=List[ModelVersionResponse])
def list_model_versions(
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_record_with_ownership(record_id, db, current_user)
    return db.query(ModelVersion).filter(
        ModelVersion.model_research_record_id == record_id
    ).order_by(ModelVersion.version_number).all()


@router.get("/{record_id}/versions/compare")
def compare_model_versions(
    record_id: UUID,
    version_a_id: UUID,
    version_b_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Side-by-side view of two versions of the same record.

    The response lists differences only; it deliberately contains no ranking
    or recommendation, so a comparison cannot be mistaken for proof that one
    version is better.
    """
    record = _get_record_with_ownership(record_id, db, current_user)
    if version_a_id == version_b_id:
        raise HTTPException(status_code=400, detail="Choose two different versions")

    versions = db.query(ModelVersion).filter(
        ModelVersion.id.in_([version_a_id, version_b_id]),
        ModelVersion.model_research_record_id == record.id,
    ).all()
    by_id = {v.id: v for v in versions}
    version_a = by_id.get(version_a_id)
    version_b = by_id.get(version_b_id)
    if not version_a or not version_b:
        raise HTTPException(status_code=404, detail="Model version not found in this record")

    def _summary(version: ModelVersion) -> dict:
        return {
            "id": str(version.id),
            "version_number": version.version_number,
            "model_type": version.model_type.value,
            "created_at": _iso(version.created_at),
            "locked_at": _iso(version.locked_at),
            "revoked_at": _iso(version.revoked_at),
            "is_active": record.active_version_id == version.id,
        }

    evaluations = db.query(ModelEvaluation).filter(
        ModelEvaluation.model_version_id.in_([version_a.id, version_b.id])
    ).order_by(ModelEvaluation.evaluation_date).all()

    def _eval_entry(evaluation: ModelEvaluation) -> dict:
        return {
            "id": str(evaluation.id),
            "evaluation_type": evaluation.evaluation_type.value,
            "result_summary": evaluation.result_summary,
            "data_cutoff_at": evaluation.data_cutoff_at,
            "engine_version": evaluation.engine_version,
            "evaluator": evaluation.evaluator.value,
        }

    changes, truncated = _spec_changes(
        version_a.model_specification_json, version_b.model_specification_json
    )

    return {
        "record_id": str(record.id),
        "version_a": _summary(version_a),
        "version_b": _summary(version_b),
        "spec_changes": changes,
        "spec_changes_truncated": truncated,
        "evaluations": {
            "a": [_eval_entry(e) for e in evaluations if e.model_version_id == version_a.id],
            "b": [_eval_entry(e) for e in evaluations if e.model_version_id == version_b.id],
        },
        "note": "比較只陳列兩個版本的差異；系統不會自動判定哪個版本更好。",
    }


@router.get(
    "/{record_id}/versions/{version_id}",
    response_model=ModelVersionResponse,
)
def get_model_version(
    record_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_record_with_ownership(record_id, db, current_user)
    version = db.query(ModelVersion).filter(
        ModelVersion.id == version_id,
        ModelVersion.model_research_record_id == record_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found")
    return version


@router.post("/{record_id}/active-version", response_model=ModelResearchRecordResponse)
def set_active_model_version(
    record_id: UUID,
    payload: ActiveVersionSet,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Point the record at the version the owner currently uses.

    Repeating the same choice is a no-op (and emits no event), so a retried
    request cannot produce a second switch in the history.
    """
    record = _get_record_with_ownership(record_id, db, current_user)
    version = db.query(ModelVersion).filter(
        ModelVersion.id == payload.version_id,
        ModelVersion.model_research_record_id == record.id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found in this record")
    if version.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Cannot activate a revoked version")

    if record.active_version_id == version.id:
        return record

    previous_id = record.active_version_id
    previous = (
        db.query(ModelVersion).filter(ModelVersion.id == previous_id).first()
        if previous_id else None
    )

    record.active_version_id = version.id

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_research_record",
        entity_id=record.id,
        event_type="model_active_version_set",
        actor_id=current_user.id,
        payload={
            "from_version_id": str(previous_id) if previous_id else None,
            "from_version_number": previous.version_number if previous else None,
            "to_version_id": str(version.id),
            "to_version_number": version.version_number,
            "reason": payload.reason,
        },
    )

    db.commit()
    db.refresh(record)
    return record


@router.post("/{record_id}/rollback", response_model=ModelResearchRecordResponse)
def rollback_model_version(
    record_id: UUID,
    payload: ModelVersionRollback,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move the active pointer back to an older version.

    The rollback is recorded as an event with its reason; nothing is deleted
    or rewritten. Choosing the already-active version is a no-op.
    """
    record = _get_record_with_ownership(record_id, db, current_user)
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Rollback reason is required")

    target = db.query(ModelVersion).filter(
        ModelVersion.id == payload.to_version_id,
        ModelVersion.model_research_record_id == record.id,
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Model version not found in this record")
    if target.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Cannot roll back to a revoked version")

    if record.active_version_id == target.id:
        return record
    if not record.active_version_id:
        raise HTTPException(status_code=400, detail="No active version to roll back from")

    current = db.query(ModelVersion).filter(
        ModelVersion.id == record.active_version_id
    ).first()
    if current is None:
        raise HTTPException(status_code=409, detail="Active version no longer exists; set an active version first")
    if target.version_number >= current.version_number:
        raise HTTPException(
            status_code=400,
            detail="Rollback target must be older than the active version",
        )

    record.active_version_id = target.id

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_research_record",
        entity_id=record.id,
        event_type="model_version_rolled_back",
        actor_id=current_user.id,
        payload={
            "from_version_id": str(current.id),
            "from_version_number": current.version_number,
            "to_version_id": str(target.id),
            "to_version_number": target.version_number,
            "reason": reason,
        },
    )

    db.commit()
    db.refresh(record)
    return record


@router.post(
    "/{record_id}/versions/{version_id}/lock",
    response_model=ModelVersionResponse,
)
def lock_model_version(
    record_id: UUID,
    version_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_record_with_ownership(record_id, db, current_user)
    version = db.query(ModelVersion).filter(
        ModelVersion.id == version_id,
        ModelVersion.model_research_record_id == record_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found")

    if version.locked_at is not None:
        raise HTTPException(status_code=400, detail="Version already locked")
    if version.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Cannot lock a revoked version")

    version.locked_at = datetime.now(timezone.utc).replace(tzinfo=None)

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_version",
        entity_id=version.id,
        event_type="model_version_locked",
        actor_id=current_user.id,
        payload={"version_number": version.version_number},
    )

    db.commit()
    db.refresh(version)
    return version


@router.post(
    "/{record_id}/versions/{version_id}/revoke",
    response_model=ModelVersionResponse,
)
def revoke_model_version(
    record_id: UUID,
    version_id: UUID,
    reason: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _get_record_with_ownership(record_id, db, current_user)
    version = db.query(ModelVersion).filter(
        ModelVersion.id == version_id,
        ModelVersion.model_research_record_id == record_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found")

    if version.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Version already revoked")

    version.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    version.revocation_reason = reason

    cleared_active = record.active_version_id == version.id
    if cleared_active:
        record.active_version_id = None

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_version",
        entity_id=version.id,
        event_type="model_version_revoked",
        actor_id=current_user.id,
        payload={
            "version_number": version.version_number,
            "reason": reason,
            "cleared_active": cleared_active,
        },
    )

    db.commit()
    db.refresh(version)
    return version


# ── ModelEvaluation CRUD ──────────────────────────────────────────────


@router.post(
    "/{record_id}/evaluations",
    response_model=ModelEvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_model_evaluation(
    record_id: UUID,
    payload: ModelEvaluationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _get_record_with_ownership(record_id, db, current_user)

    version = db.query(ModelVersion).filter(
        ModelVersion.id == payload.model_version_id,
        ModelVersion.model_research_record_id == record_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found in this record")

    evaluation = ModelEvaluation(
        model_version_id=version.id,
        model_research_record_id=record.id,
        evaluation_type=payload.evaluation_type,
        result_summary=payload.result_summary,
        metrics_json=payload.metrics_json,
        warning_flags=payload.warning_flags,
        evaluation_date=datetime.fromisoformat(payload.evaluation_date),
        data_cutoff_at=payload.data_cutoff_at,
        engine_version=payload.engine_version,
        evaluator=payload.evaluator,
        methodology_version=payload.methodology_version,
    )
    db.add(evaluation)
    db.flush()

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_evaluation",
        entity_id=evaluation.id,
        event_type="model_evaluation_created",
        actor_id=current_user.id,
        payload={
            "evaluation_type": payload.evaluation_type,
            "model_version_id": str(version.id),
        },
    )

    db.commit()
    db.refresh(evaluation)
    return evaluation


@router.get(
    "/{record_id}/evaluations",
    response_model=List[ModelEvaluationResponse],
)
def list_model_evaluations(
    record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_record_with_ownership(record_id, db, current_user)
    return db.query(ModelEvaluation).filter(
        ModelEvaluation.model_research_record_id == record_id
    ).order_by(ModelEvaluation.created_at.desc()).all()


# ── PersonalResearchMemory ────────────────────────────────────────────

memory_router = APIRouter(prefix="/personal-memory", tags=["personal-memory"])


@memory_router.post(
    "",
    response_model=PersonalResearchMemoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_personal_memory(
    payload: PersonalResearchMemoryCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = PersonalResearchMemory(
        owner_id=current_user.id,
        memory_type=MemoryType(payload.memory_type),
        content=payload.content,
        source_entity_type=payload.source_entity_type,
        source_entity_id=payload.source_entity_id,
        privacy_scope=PrivacyScope(payload.privacy_scope),
        general_model_training_opt_in=False,
    )

    if payload.user_confirmed:
        memory.user_confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(memory)
    db.flush()

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="personal_research_memory",
        entity_id=memory.id,
        event_type="memory_created",
        actor_id=current_user.id,
        payload={
            "memory_type": payload.memory_type,
            "confirmed": payload.user_confirmed,
        },
    )

    db.commit()
    db.refresh(memory)
    return _memory_to_response(memory)


@memory_router.get("", response_model=List[PersonalResearchMemoryResponse])
def list_personal_memories(
    memory_type_filter: Optional[str] = None,
    confirmed_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(PersonalResearchMemory).filter(
        PersonalResearchMemory.owner_id == current_user.id,
        PersonalResearchMemory.active == True,
    )
    if memory_type_filter:
        query = query.filter(PersonalResearchMemory.memory_type == memory_type_filter)
    if confirmed_only:
        query = query.filter(PersonalResearchMemory.user_confirmed_at.isnot(None))
    return query.order_by(PersonalResearchMemory.created_at.desc()).all()


@memory_router.get("/{memory_id}", response_model=PersonalResearchMemoryResponse)
def get_personal_memory(
    memory_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = db.query(PersonalResearchMemory).filter(
        PersonalResearchMemory.id == memory_id,
        PersonalResearchMemory.owner_id == current_user.id,
    ).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_response(memory)


@memory_router.post(
    "/{memory_id}/confirm",
    response_model=PersonalResearchMemoryResponse,
)
def confirm_personal_memory(
    memory_id: UUID,
    payload: PersonalResearchMemoryConfirm,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = db.query(PersonalResearchMemory).filter(
        PersonalResearchMemory.id == memory_id,
        PersonalResearchMemory.owner_id == current_user.id,
    ).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    if payload.confirmed:
        memory.user_confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        event_type = "memory_confirmed"
    else:
        memory.active = False
        event_type = "memory_rejected"

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="personal_research_memory",
        entity_id=memory.id,
        event_type=event_type,
        actor_id=current_user.id,
        payload={"confirmed": payload.confirmed},
    )

    db.commit()
    db.refresh(memory)
    return _memory_to_response(memory)


@memory_router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def deactivate_personal_memory(
    memory_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = db.query(PersonalResearchMemory).filter(
        PersonalResearchMemory.id == memory_id,
        PersonalResearchMemory.owner_id == current_user.id,
    ).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    memory.active = False

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="personal_research_memory",
        entity_id=memory.id,
        event_type="memory_deactivated",
        actor_id=current_user.id,
    )

    db.commit()


@memory_router.put(
    "/{memory_id}/training-opt-in",
    response_model=PersonalResearchMemoryResponse,
)
def set_training_opt_in(
    memory_id: UUID,
    opt_in: bool,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    memory = db.query(PersonalResearchMemory).filter(
        PersonalResearchMemory.id == memory_id,
        PersonalResearchMemory.owner_id == current_user.id,
    ).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    if not memory.is_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Memory must be confirmed before changing training opt-in",
        )

    memory.general_model_training_opt_in = opt_in

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="personal_research_memory",
        entity_id=memory.id,
        event_type="training_opt_in_changed",
        actor_id=current_user.id,
        payload={"opt_in": opt_in},
    )

    db.commit()
    db.refresh(memory)
    return _memory_to_response(memory)


def _memory_to_response(memory: PersonalResearchMemory) -> dict:
    return {
        "id": memory.id,
        "owner_id": memory.owner_id,
        "memory_type": memory.memory_type.value if hasattr(memory.memory_type, "value") else memory.memory_type,
        "content": memory.content,
        "source_entity_type": memory.source_entity_type,
        "source_entity_id": memory.source_entity_id,
        "created_at": memory.created_at,
        "user_confirmed_at": memory.user_confirmed_at,
        "is_confirmed": memory.is_confirmed,
        "active": memory.active,
        "privacy_scope": memory.privacy_scope.value if hasattr(memory.privacy_scope, "value") else memory.privacy_scope,
        "general_model_training_opt_in": memory.general_model_training_opt_in,
    }


# ── ModelCard ─────────────────────────────────────────────────────────

card_router = APIRouter(prefix="/model-cards", tags=["model-cards"])


@card_router.post(
    "",
    response_model=ModelCardResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_model_card(
    payload: ModelCardCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    version = db.query(ModelVersion).filter(ModelVersion.id == payload.model_version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found")

    record = db.query(ModelResearchRecord).filter(
        ModelResearchRecord.id == version.model_research_record_id
    ).first()
    if not record or record.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    card = ModelCard(
        model_version_id=payload.model_version_id,
        purpose=payload.purpose,
        intended_research_use=payload.intended_research_use,
        excluded_use=payload.excluded_use,
        data_sources=payload.data_sources,
        data_cutoff=payload.data_cutoff,
        linked_experiments=payload.linked_experiments,
        evaluation_evidence=payload.evaluation_evidence,
        known_limitations=payload.known_limitations,
        risk_flags=payload.risk_flags,
        version_history=payload.version_history,
        revocation_process=payload.revocation_process,
        disclaimer=payload.disclaimer,
    )
    db.add(card)
    db.flush()

    version.model_card_id = card.id

    emit_event(
        db,
        actor_type=request_actor_provenance(request),
        entity_type="model_card",
        entity_id=card.id,
        event_type="model_card_created",
        actor_id=current_user.id,
        payload={"model_version_id": str(payload.model_version_id)},
    )

    db.commit()
    db.refresh(card)
    return card


@card_router.get("/{card_id}", response_model=ModelCardResponse)
def get_model_card(
    card_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = db.query(ModelCard).filter(ModelCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Model card not found")

    version = db.query(ModelVersion).filter(ModelVersion.id == card.model_version_id).first()
    if version:
        record = db.query(ModelResearchRecord).filter(
            ModelResearchRecord.id == version.model_research_record_id
        ).first()
        if record and record.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    return card


@card_router.patch("/{card_id}", response_model=ModelCardResponse)
def update_model_card(
    card_id: UUID,
    payload: ModelCardUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    card = db.query(ModelCard).filter(ModelCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Model card not found")

    version = db.query(ModelVersion).filter(ModelVersion.id == card.model_version_id).first()
    if version:
        record = db.query(ModelResearchRecord).filter(
            ModelResearchRecord.id == version.model_research_record_id
        ).first()
        if not record or record.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(card, field, value)

    card.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(card)
    return card
