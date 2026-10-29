"""Append-only review labels for recorded human decisions.

The recorded decision event is immutable. A review writes a new
``DecisionLabel`` revision that supersedes the previous one, so the owner can
always see how a label changed, who changed it and why.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.decision_label import (
    DecisionExclusionReason,
    DecisionLabel,
    DecisionLabelState,
)
from app.models.human_decision import HumanDecisionEvent
from app.models.user import User

VALID_ACTIONS = {"buy", "sell", "hold"}


class LabelRefused(ValueError):
    """A label request the contract must reject (not a server error)."""


def latest_labels(
    db: Session, owner_id: uuid.UUID, event_ids: List[uuid.UUID]
) -> Dict[uuid.UUID, DecisionLabel]:
    """Return the newest revision per decision event, scoped to the owner."""
    if not event_ids:
        return {}
    rows = (
        db.query(DecisionLabel)
        .filter(
            DecisionLabel.owner_id == owner_id,
            DecisionLabel.decision_event_id.in_(event_ids),
        )
        .order_by(
            DecisionLabel.decision_event_id,
            DecisionLabel.revision_number.desc(),
        )
        .all()
    )
    latest: Dict[uuid.UUID, DecisionLabel] = {}
    for row in rows:
        latest.setdefault(row.decision_event_id, row)
    return latest


def revision_history(
    db: Session, owner_id: uuid.UUID, event_id: uuid.UUID
) -> List[DecisionLabel]:
    return (
        db.query(DecisionLabel)
        .filter(
            DecisionLabel.owner_id == owner_id,
            DecisionLabel.decision_event_id == event_id,
        )
        .order_by(DecisionLabel.revision_number)
        .all()
    )


def record_label(
    db: Session,
    *,
    event: HumanDecisionEvent,
    author: User,
    state: str,
    action_label: Optional[str],
    note: Optional[str],
    exclusion_reason: Optional[str],
) -> DecisionLabel:
    """Append a review revision for one of the author's own decisions.

    Rules enforced here, not in the UI:

    * the event must belong to the author;
    * a confirmation defaults to the action the owner actually recorded —
      a HOLD is never invented for a buy/sell decision;
    * changing the action (a correction) requires a note explaining it;
    * an exclusion requires a reason and carries no training action.
    """
    if event.owner_id != author.id:
        raise LabelRefused("decision_not_owned")

    try:
        label_state = DecisionLabelState(state)
    except ValueError:
        raise LabelRefused("invalid_state")

    recorded_action = (
        event.action.value if hasattr(event.action, "value") else str(event.action)
    )
    normalized_note = (note or "").strip() or None

    if label_state is DecisionLabelState.confirmed:
        if exclusion_reason:
            raise LabelRefused("exclusion_reason_not_allowed_on_confirm")
        resolved_action = (action_label or recorded_action).strip()
        if resolved_action not in VALID_ACTIONS:
            raise LabelRefused("invalid_action_label")
        if resolved_action != recorded_action and not normalized_note:
            raise LabelRefused("correction_requires_note")
        resolved_reason = None
    else:
        if not exclusion_reason:
            raise LabelRefused("exclusion_reason_required")
        try:
            resolved_reason = DecisionExclusionReason(exclusion_reason)
        except ValueError:
            raise LabelRefused("invalid_exclusion_reason")
        if action_label:
            raise LabelRefused("action_label_not_allowed_on_exclusion")
        resolved_action = None

    history = revision_history(db, author.id, event.id)
    previous = history[-1] if history else None

    label = DecisionLabel(
        owner_id=author.id,
        decision_event_id=event.id,
        revision_number=(previous.revision_number + 1) if previous else 1,
        state=label_state,
        action_label=resolved_action,
        note=normalized_note,
        exclusion_reason=resolved_reason,
        author_id=author.id,
        supersedes_label_id=previous.id if previous else None,
        metadata_json={
            "recorded_action": recorded_action,
            "action_overridden": (
                label_state is DecisionLabelState.confirmed
                and resolved_action != recorded_action
            ),
        },
    )
    db.add(label)
    db.flush()
    return label


def label_payload(label: Optional[DecisionLabel]) -> Optional[Dict[str, Any]]:
    if label is None:
        return None
    return {
        "id": str(label.id),
        "revision_number": label.revision_number,
        "state": label.state.value if hasattr(label.state, "value") else label.state,
        "action_label": label.action_label,
        "note": label.note,
        "exclusion_reason": (
            label.exclusion_reason.value
            if hasattr(label.exclusion_reason, "value")
            else label.exclusion_reason
        ),
        "author_id": str(label.author_id),
        "supersedes_label_id": (
            str(label.supersedes_label_id) if label.supersedes_label_id else None
        ),
        "created_at": _iso(label.created_at),
        "recorded_action": (label.metadata_json or {}).get("recorded_action"),
        "action_overridden": bool(
            (label.metadata_json or {}).get("action_overridden")
        ),
    }


def _iso(value: datetime) -> Optional[str]:
    return value.isoformat() if value else None
