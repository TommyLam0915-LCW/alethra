from datetime import datetime
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
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
from app.api.schemas import (
    ModelResearchRecordCreate,
    ModelResearchRecordUpdate,
    ModelResearchRecordResponse,
    ModelVersionCreate,
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


def _get_record_with_ownership(
    record_id: UUID, db: Session, current_user: User
) -> ModelResearchRecord:
    record = db.query(ModelResearchRecord).filter(ModelResearchRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Model research record not found")
    if record.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return record


# ── ModelResearchRecord CRUD ──────────────────────────────────────────


@router.post("", response_model=ModelResearchRecordResponse, status_code=status.HTTP_201_CREATED)
def create_model_research_record(
    payload: ModelResearchRecordCreate,
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

    record.updated_at = datetime.utcnow()

    if payload.status:
        emit_event(
            db,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = _get_record_with_ownership(record_id, db, current_user)

    if record.status not in (ModelStatus.draft, ModelStatus.researching):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot add versions when record status is '{record.status.value}'",
        )

    existing_count = db.query(ModelVersion).filter(
        ModelVersion.model_research_record_id == record_id
    ).count()

    version = ModelVersion(
        model_research_record_id=record.id,
        version_number=existing_count + 1,
        model_type=payload.model_type,
        model_specification_json=payload.model_specification_json,
        prompt_version=payload.prompt_version,
        input_schema_version=payload.input_schema_version,
        output_schema_version=payload.output_schema_version,
        linked_experiment_version_ids=payload.linked_experiment_version_ids,
        linked_dataset_snapshot_ids=payload.linked_dataset_snapshot_ids,
        linked_simulation_session_ids=payload.linked_simulation_session_ids,
    )
    db.add(version)
    db.flush()

    emit_event(
        db,
        entity_type="model_version",
        entity_id=version.id,
        event_type="model_version_created",
        actor_id=current_user.id,
        payload={
            "record_id": str(record_id),
            "version_number": version.version_number,
        },
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


@router.post(
    "/{record_id}/versions/{version_id}/lock",
    response_model=ModelVersionResponse,
)
def lock_model_version(
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

    if version.locked_at is not None:
        raise HTTPException(status_code=400, detail="Version already locked")
    if version.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Cannot lock a revoked version")

    version.locked_at = datetime.utcnow()

    emit_event(
        db,
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

    if version.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Version already revoked")

    version.revoked_at = datetime.utcnow()
    version.revocation_reason = reason

    emit_event(
        db,
        entity_type="model_version",
        entity_id=version.id,
        event_type="model_version_revoked",
        actor_id=current_user.id,
        payload={
            "version_number": version.version_number,
            "reason": reason,
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
        memory.user_confirmed_at = datetime.utcnow()

    db.add(memory)
    db.flush()

    emit_event(
        db,
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
        memory.user_confirmed_at = datetime.utcnow()
        event_type = "memory_confirmed"
    else:
        memory.active = False
        event_type = "memory_rejected"

    emit_event(
        db,
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

    card.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(card)
    return card
