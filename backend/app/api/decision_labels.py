"""Owner review surface for recorded human decisions.

The owner can find their decisions under a strategy version, see the context
the system knew when the decision was recorded, and confirm, correct or
exclude it. Every change is a new append-only revision; the recorded decision
is never rewritten.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.decision_label import DecisionLabel
from app.models.human_decision import HumanDecisionEvent
from app.models.user import User
from app.services import emit_event
from app.services.decision_labels import (
    LabelRefused,
    label_payload,
    latest_labels,
    record_label,
    revision_history,
)

router = APIRouter(prefix="/decision-review", tags=["decision-review"])


class DecisionLabelRequest(BaseModel):
    state: str = Field(description="confirmed | excluded")
    action_label: Optional[str] = Field(
        default=None,
        description="buy | sell | hold. Omit on confirm to use the recorded "
        "action. Required (with a note) only when correcting the action.",
    )
    note: Optional[str] = None
    exclusion_reason: Optional[str] = Field(
        default=None,
        description="data_entry_error | test_trade | outside_strategy | other",
    )


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _get_owned_event(
    event_id: uuid.UUID, db: Session, current_user: User
) -> HumanDecisionEvent:
    event = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.id == event_id
    ).first()
    # 404 for another owner's event: existence is not disclosed.
    if not event or event.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return event


def _superseded_ids(events: List[HumanDecisionEvent]) -> set:
    return {
        event.supersedes_event_id
        for event in events
        if event.supersedes_event_id
    }


def _event_row(
    event: HumanDecisionEvent,
    label: Optional[DecisionLabel],
    revision_count: int,
) -> dict:
    action = event.action.value if hasattr(event.action, "value") else str(event.action)
    label_body = label_payload(label)
    usable = bool(
        label_body
        and label_body["state"] == "confirmed"
        and label_body["action_label"] in ("buy", "sell", "hold")
    )
    return {
        "decision_event_id": str(event.id),
        "simulation_session_id": str(event.simulation_session_id),
        "strategy_version_id": (
            str(event.server_strategy_version_id)
            if event.server_strategy_version_id
            else None
        ),
        "as_known_context": {
            "instrument_id": event.server_instrument_id,
            "step_number": event.server_step_number,
            "snapshot_date": event.server_snapshot_date,
            "data_cutoff_at": event.server_data_cutoff_at,
        },
        "recorded_action": action,
        "quantity": event.quantity,
        "rationale": event.rationale,
        "recorded_at": _iso(event.created_at),
        "supersedes_event_id": (
            str(event.supersedes_event_id) if event.supersedes_event_id else None
        ),
        "label": label_body,
        "revision_count": revision_count,
        "usable_for_training": usable,
    }


@router.get("")
def list_decisions_for_review(
    strategy_version_id: Optional[uuid.UUID] = None,
    simulation_session_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(HumanDecisionEvent).filter(
        HumanDecisionEvent.owner_id == current_user.id
    )
    if strategy_version_id:
        query = query.filter(
            HumanDecisionEvent.server_strategy_version_id == strategy_version_id
        )
    if simulation_session_id:
        query = query.filter(
            HumanDecisionEvent.simulation_session_id == simulation_session_id
        )
    events = query.order_by(HumanDecisionEvent.created_at.desc()).all()

    superseded = _superseded_ids(events)
    current = [event for event in events if event.id not in superseded]
    latest = latest_labels(db, current_user.id, [event.id for event in current])

    all_labels = (
        db.query(DecisionLabel.decision_event_id)
        .filter(
            DecisionLabel.owner_id == current_user.id,
            DecisionLabel.decision_event_id.in_([e.id for e in current]),
        )
        .all()
        if current
        else []
    )
    revision_counts: dict = {}
    for (event_id,) in all_labels:
        revision_counts[event_id] = revision_counts.get(event_id, 0) + 1

    rows = [
        _event_row(event, latest.get(event.id), revision_counts.get(event.id, 0))
        for event in current
    ]
    confirmed = sum(1 for row in rows if row["usable_for_training"])
    excluded = sum(
        1
        for row in rows
        if row["label"] and row["label"]["state"] == "excluded"
    )
    return {
        "strategy_version_id": (
            str(strategy_version_id) if strategy_version_id else None
        ),
        "summary": {
            "total_current_decisions": len(rows),
            "confirmed": confirmed,
            "excluded": excluded,
            "unreviewed": len(rows) - confirmed - excluded,
        },
        "decisions": rows,
        "note": "只有 owner 親自確認的決策才可以成為訓練標籤；"
        "系統訊號不會自動成為人類標籤。",
    }


@router.get("/{event_id}")
def get_decision_review(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = _get_owned_event(event_id, db, current_user)
    history = revision_history(db, current_user.id, event_id)
    current = history[-1] if history else None
    return {
        "decision": _event_row(event, current, len(history)),
        "revisions": [label_payload(label) for label in history],
    }


@router.get("/{event_id}/labels")
def list_label_revisions(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_event(event_id, db, current_user)
    history = revision_history(db, current_user.id, event_id)
    return {
        "decision_event_id": str(event_id),
        "revisions": [label_payload(label) for label in history],
    }


@router.post("/{event_id}/labels", status_code=status.HTTP_201_CREATED)
def create_label_revision(
    event_id: uuid.UUID,
    payload: DecisionLabelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = _get_owned_event(event_id, db, current_user)
    try:
        label = record_label(
            db,
            event=event,
            author=current_user,
            state=payload.state,
            action_label=payload.action_label,
            note=payload.note,
            exclusion_reason=payload.exclusion_reason,
        )
    except LabelRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    emit_event(
        db,
        entity_type="decision_label",
        entity_id=str(label.id),
        event_type="decision_label_recorded",
        actor_id=current_user.id,
        payload={
            "decision_event_id": str(event.id),
            "revision_number": label.revision_number,
            "state": label.state.value,
            "action_label": label.action_label,
            "exclusion_reason": (
                label.exclusion_reason.value if label.exclusion_reason else None
            ),
            "supersedes_label_id": (
                str(label.supersedes_label_id) if label.supersedes_label_id else None
            ),
        },
    )
    db.commit()
    db.refresh(label)
    return label_payload(label)
