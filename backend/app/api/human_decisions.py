from datetime import date, datetime, time, timedelta
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from app.auth.provenance import request_actor_provenance
from app.api.schemas import (
    HumanDecisionCreate,
    HumanDecisionCorrect,
    HumanDecisionResponse,
)
from app.lib.timeutil import utcnow
from app.engine.intraday import INTRADAY_TIMEFRAMES
from app.models.decision_label import DecisionLabel
from app.services import emit_event
from app.services.decision_labels import event_confirmation, latest_labels

router = APIRouter(prefix="/human-decisions", tags=["human-decisions"])

_BAR_DURATION = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}


def _decision_instant(session: SimulationSession, spec: dict) -> Optional[datetime]:
    """When the decision happened on the simulation's market clock.

    A daily replay cursor at date D means the owner has observed the completed
    bar for D, so the instant is the end of D. An intraday cursor points at the
    bar's start timestamp, so the instant is that bar's close. The wall clock is
    never used: the decision's as-of boundary is the market clock it was made on.
    """
    timeframe = session.timeframe or "1d"
    if timeframe in INTRADAY_TIMEFRAMES and session.current_bar_timestamp is not None:
        return session.current_bar_timestamp + _BAR_DURATION[timeframe]

    raw_date = (session.current_snapshot_date or spec.get("start_date") or "").strip()
    try:
        day = date.fromisoformat(raw_date)
    except (ValueError, TypeError):
        return None
    if session.current_snapshot_date:
        day = day + timedelta(days=1)
    return datetime.combine(day, time.min)


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
        "decision_instant": _decision_instant(session, spec),
    }


def _event_to_response(
    event: HumanDecisionEvent,
    label: Optional[DecisionLabel] = None,
) -> HumanDecisionResponse:
    confirmation = event_confirmation(event, label)
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
        decision_instant=event.decision_instant,
        execution_status=event.execution_status.value if isinstance(event.execution_status, HumanDecisionExecutionStatus) else event.execution_status,
        actor_type=event.actor_type or "unknown",
        supersedes_event_id=event.supersedes_event_id,
        metadata_json=event.metadata_json,
        confirmation_status=confirmation["status"],
        confirmed_by=confirmation["confirmed_by"],
        confirmed_at=confirmation["confirmed_at"],
        confirmation_label_id=confirmation["label_id"],
        confirmation_provenance=confirmation["provenance"],
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


@router.post("", response_model=HumanDecisionResponse, status_code=status.HTTP_201_CREATED)
def create_human_decision(
    body: HumanDecisionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = _get_session_with_ownership(body.simulation_session_id, db, current_user)
    ctx = _derive_server_context(session, db)
    # Provenance is the credential the server verified, never a field the caller
    # sets. An API-key call is an agent's decision even if it claims otherwise.
    actor_type = request_actor_provenance(request)

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
            label = latest_labels(
                db, current_user.id, [existing.id]
            ).get(existing.id)
            return _event_to_response(existing, label)
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
        decision_instant=ctx["decision_instant"],
        execution_status=HumanDecisionExecutionStatus.record_only,
        metadata_json=body.metadata,
        supersedes_event_id=None,
        actor_type=actor_type,
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
            actor_type=actor_type,
            payload={
                "action": body.action,
                "quantity": body.quantity,
                "idempotency_key": body.idempotency_key,
                "simulation_session_id": str(body.simulation_session_id),
                "actor_type": actor_type,
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
    labels = latest_labels(db, current_user.id, [event.id for event in events])
    return [_event_to_response(e, labels.get(e.id)) for e in events]


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
    label = latest_labels(db, current_user.id, [event.id]).get(event.id)
    return _event_to_response(event, label)


@router.post(
    "/{event_id}/correct",
    response_model=HumanDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def correct_human_decision(
    event_id: UUID,
    body: HumanDecisionCorrect,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    actor_type = request_actor_provenance(request)
    original = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.id == event_id,
        HumanDecisionEvent.owner_id == current_user.id,
    ).first()
    if not original:
        raise HTTPException(status_code=404, detail="Human decision event not found")

    _get_session_with_ownership(original.simulation_session_id, db, current_user)

    # A correction is another label revision of the SAME decision, so it keeps
    # the original's server context and as-of instant. Re-deriving from the
    # session would move the sample to a later cursor and change what the owner
    # could have observed when they actually decided.
    ctx = {
        "strategy_version_id": original.server_strategy_version_id,
        "instrument_id": original.server_instrument_id,
        "step_number": original.server_step_number,
        "snapshot_date": original.server_snapshot_date,
        "data_cutoff_at": original.server_data_cutoff_at,
        "decision_instant": original.decision_instant,
    }

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
        decision_instant=ctx["decision_instant"],
        execution_status=HumanDecisionExecutionStatus.record_only,
        metadata_json={**(body.metadata or {}), "correction_reason": body.correction_reason},
        supersedes_event_id=original.id,
        actor_type=actor_type,
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
            actor_type=actor_type,
            payload={
                "action": body.action,
                "quantity": body.quantity,
                "supersedes_event_id": str(original.id),
                "correction_reason": body.correction_reason,
                "simulation_session_id": str(original.simulation_session_id),
                "actor_type": actor_type,
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
