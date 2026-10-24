from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models.user import User
from app.models.simulation import SimulationSession
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import (
    HumanDecisionEvent,
    HumanDecisionAction,
    HumanDecisionExecutionStatus,
)
from app.auth.jwt import get_current_user
from app.api.schemas import (
    HumanDecisionCreate,
    HumanDecisionCorrect,
    HumanDecisionResponse,
)
from app.lib.timeutil import utcnow
from app.services import emit_event

router = APIRouter(prefix="/human-decisions", tags=["human-decisions"])


def _get_session_with_ownership(
    session_id: UUID, db: Session, current_user: User
) -> SimulationSession:
    session = db.query(SimulationSession).filter(
        SimulationSession.id == session_id
    ).first()
    if not session:
        raise HTTPException(
            status_code=404, detail="Simulation session not found"
        )
    if session.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return session


def _derive_server_context(
    session: SimulationSession, db: Session
) -> dict:
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == session.experiment_version_id
    ).first()
    if not version:
        raise HTTPException(
            status_code=404, detail="Experiment version not found for session"
        )
    spec = version.strategy_spec or {}
    asset_ids = spec.get("asset_ids", [])
    instrument_id = asset_ids[0] if asset_ids else "UNKNOWN"
    step_number = (session.observed_days or 0) + 1
    snapshot_date = session.current_snapshot_date or spec.get("start_date", "")
    data_cutoff_at = session.data_cutoff_at or spec.get("end_date", "")
    return {
        "strategy_version_id": version.id,
        "instrument_id": instrument_id,
        "step_number": step_number,
        "snapshot_date": snapshot_date,
        "data_cutoff_at": data_cutoff_at,
    }


def _event_to_response(event: HumanDecisionEvent) -> HumanDecisionResponse:
    return HumanDecisionResponse(
        id=event.id,
        owner_id=event.owner_id,
        simulation_session_id=event.simulation_session_id,
        idempotency_key=event.idempotency_key,
        action=event.action.value if isinstance(event.action, HumanDecisionAction) else event.action,
        quantity=event.quantity,
        rationale=event.rationale,
        server_strategy_version_id=event.server_strategy_version_id,
        server_instrument_id=event.server_instrument_id,
        server_step_number=event.server_step_number,
        server_snapshot_date=event.server_snapshot_date,
        server_data_cutoff_at=event.server_data_cutoff_at,
        execution_status=event.execution_status.value if isinstance(event.execution_status, HumanDecisionExecutionStatus) else event.execution_status,
        supersedes_event_id=event.supersedes_event_id,
        metadata_json=event.metadata_json,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


@router.post("", response_model=HumanDecisionResponse, status_code=status.HTTP_201_CREATED)
def create_human_decision(
    body: HumanDecisionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(body.simulation_session_id, db, current_user)
    ctx = _derive_server_context(session, db)

    existing = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.owner_id == current_user.id,
        HumanDecisionEvent.simulation_session_id == body.simulation_session_id,
        HumanDecisionEvent.idempotency_key == body.idempotency_key,
    ).first()

    if existing:
        same_content = (
            existing.action.value == body.action
            and existing.quantity == body.quantity
            and existing.rationale == body.rationale
        )
        if same_content:
            return _event_to_response(existing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key conflict: same key with different content",
        )

    event = HumanDecisionEvent(
        owner_id=current_user.id,
        simulation_session_id=body.simulation_session_id,
        idempotency_key=body.idempotency_key,
        action=HumanDecisionAction(body.action),
        quantity=body.quantity,
        rationale=body.rationale,
        server_strategy_version_id=ctx["strategy_version_id"],
        server_instrument_id=ctx["instrument_id"],
        server_step_number=ctx["step_number"],
        server_snapshot_date=ctx["snapshot_date"],
        server_data_cutoff_at=ctx["data_cutoff_at"],
        execution_status=HumanDecisionExecutionStatus.record_only,
        metadata_json=body.metadata,
        supersedes_event_id=None,
    )
    db.add(event)
    try:
        db.flush()
        emit_event(
            db,
            entity_type="human_decision",
            entity_id=str(event.id),
            event_type="human_decision_created",
            actor_id=current_user.id,
            actor_type="human",
            payload={
                "action": body.action,
                "quantity": body.quantity,
                "idempotency_key": body.idempotency_key,
                "simulation_session_id": str(body.simulation_session_id),
            },
            data_cutoff_at=ctx["data_cutoff_at"],
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key conflict (concurrent)",
        )
    db.refresh(event)
    return _event_to_response(event)


@router.get("", response_model=List[HumanDecisionResponse])
def list_human_decisions(
    simulation_session_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.owner_id == current_user.id
    )
    if simulation_session_id is not None:
        query = query.filter(
            HumanDecisionEvent.simulation_session_id == simulation_session_id
        )
    events = query.order_by(HumanDecisionEvent.created_at.asc()).all()
    return [_event_to_response(e) for e in events]


@router.get("/{event_id}", response_model=HumanDecisionResponse)
def get_human_decision(
    event_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.id == event_id,
        HumanDecisionEvent.owner_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Human decision event not found")
    return _event_to_response(event)


@router.post(
    "/{event_id}/correct",
    response_model=HumanDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_human_decision(
    event_id: UUID,
    body: HumanDecisionCorrect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    original = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.id == event_id,
        HumanDecisionEvent.owner_id == current_user.id,
    ).first()
    if not original:
        raise HTTPException(status_code=404, detail="Human decision event not found")

    session = _get_session_with_ownership(original.simulation_session_id, db, current_user)
    ctx = _derive_server_context(session, db)

    correction = HumanDecisionEvent(
        owner_id=current_user.id,
        simulation_session_id=original.simulation_session_id,
        idempotency_key=f"correction-{original.idempotency_key}-{utcnow().isoformat()}",
        action=HumanDecisionAction(body.action),
        quantity=body.quantity,
        rationale=body.rationale,
        server_strategy_version_id=ctx["strategy_version_id"],
        server_instrument_id=ctx["instrument_id"],
        server_step_number=ctx["step_number"],
        server_snapshot_date=ctx["snapshot_date"],
        server_data_cutoff_at=ctx["data_cutoff_at"],
        execution_status=HumanDecisionExecutionStatus.record_only,
        metadata_json={**(body.metadata or {}), "correction_reason": body.correction_reason},
        supersedes_event_id=original.id,
    )
    db.add(correction)
    try:
        db.flush()
        emit_event(
            db,
            entity_type="human_decision",
            entity_id=str(correction.id),
            event_type="human_decision_corrected",
            actor_id=current_user.id,
            actor_type="human",
            payload={
                "action": body.action,
                "quantity": body.quantity,
                "supersedes_event_id": str(original.id),
                "correction_reason": body.correction_reason,
                "simulation_session_id": str(original.simulation_session_id),
            },
            data_cutoff_at=ctx["data_cutoff_at"],
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key conflict (concurrent)",
        )
    db.refresh(correction)
    return _event_to_response(correction)
