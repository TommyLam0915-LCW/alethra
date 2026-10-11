import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from app.models.audit_event import AuditEvent

EVENTS_BEFORE_OUTCOME = {
    "experiment_created",
    "hypothesis_updated",
    "falsification_condition_added",
    "dataset_selected",
    "strategy_template_selected",
    "parameter_changed",
    "strategy_version_created",
    "research_run_started",
    "strategy_locked",
    "research_note_added",
    "simulation_created",
    "simulation_advanced",
    "simulation_paused",
    "simulation_resumed",
}

EVENTS_AFTER_OUTCOME = {
    "research_run_completed",
    "research_run_failed",
    "warning_generated",
    "export_generated",
    "simulation_completed",
    "simulation_retired",
}


def emit_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    event_type: str,
    actor_id: Optional[uuid.UUID] = None,
    actor_type: str = "human",
    payload: Optional[Dict[str, Any]] = None,
    rules_locked: bool = False,
    data_cutoff_at: Optional[str] = None,
) -> AuditEvent:
    recorded_before_outcome = event_type in EVENTS_BEFORE_OUTCOME

    event = AuditEvent(
        entity_type=entity_type,
        entity_id=str(entity_id),
        event_type=event_type,
        actor_id=actor_id,
        actor_type=actor_type,
        payload_json=payload,
        recorded_before_outcome=recorded_before_outcome,
        rules_locked=rules_locked,
        data_cutoff_at=data_cutoff_at,
    )
    db.add(event)
    db.flush()
    return event
