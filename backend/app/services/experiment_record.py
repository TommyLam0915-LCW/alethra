"""Assemble one experiment's complete research record.

The export file and the in-app record panel must describe the same state, so
both read this module instead of each rebuilding the queries. Every section is
present even when empty: "nothing was recorded" is itself a decision-review
finding and must not look like an omitted feature.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.lib.brand import get_brand
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import HumanDecisionEvent
from app.models.research_note import ResearchNote
from app.models.research_run import ResearchRun, RunStatus
from app.models.robustness import RobustnessAttempt, RobustnessStudy
from app.models.saved_comparison import SavedComparison
from app.models.simulation import SimulationDecision, SimulationSession
from app.services.robustness import study_payload

RECORD_SCHEMA_VERSION = "experiment-record-2"

# A research record is about what was concluded, not a second copy of the trade
# book. These run result fields are part of the run itself and stay out of the
# record; the coverage block names them so the omission is explicit.
EXCLUDED_RUN_FIELDS = (
    "runs.result_json.transactions",
    "runs.result_json.round_trips",
    "runs.result_json.daily_equity_curve",
    "runs.result_json.daily_accounting",
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _revision_meta(version: ExperimentVersion) -> Optional[Dict[str, Any]]:
    meta = (version.assumptions or {}).get("revision")
    return meta if isinstance(meta, dict) else None


def _revision_entry(version: ExperimentVersion) -> Optional[Dict[str, Any]]:
    meta = _revision_meta(version)
    if meta is None:
        return None
    return {
        "version_id": str(version.id),
        "version_number": version.version_number,
        "base_version_id": meta.get("of_version_id"),
        "base_version_number": meta.get("of_version_number"),
        "change_reason": version.change_reason,
        "change_kind": meta.get("change_kind"),
        "changed_fields": list(meta.get("changed_fields") or []),
        "expectation": meta.get("expectation"),
        "outcome": meta.get("outcome"),
        "met_expectation": meta.get("met_expectation"),
        "outcome_recorded_at": meta.get("outcome_recorded_at"),
        "created_at": _iso(version.created_at),
    }


def _decision_entry(decision: SimulationDecision) -> Dict[str, Any]:
    detail = decision.decision_detail or {}
    return {
        "id": str(decision.id),
        "step_number": decision.step_number,
        "snapshot_date": decision.snapshot_date,
        "bar_timestamp": _iso(decision.bar_timestamp),
        "data_cutoff_at": decision.data_cutoff_at,
        "decision_type": _enum_value(decision.decision_type),
        "action": detail.get("action"),
        "quantity": detail.get("quantity"),
        "symbol": detail.get("symbol"),
        "rationale": detail.get("rationale"),
        "reflection": detail.get("reflection"),
        "paper_cash": decision.paper_cash,
        "paper_equity": decision.paper_equity,
        "warnings": decision.warnings or [],
    }


def _human_decision_entry(event: HumanDecisionEvent) -> Dict[str, Any]:
    return {
        "id": str(event.id),
        "action": _enum_value(event.action),
        "quantity": event.quantity,
        "rationale": event.rationale,
        "execution_status": _enum_value(event.execution_status),
        "server_instrument_id": event.server_instrument_id,
        "server_snapshot_date": event.server_snapshot_date,
        "server_data_cutoff_at": event.server_data_cutoff_at,
        "supersedes_event_id": (
            str(event.supersedes_event_id) if event.supersedes_event_id else None
        ),
        "created_at": _iso(event.created_at),
    }


def build_experiment_record(db: Session, experiment: Experiment) -> Dict[str, Any]:
    versions = (
        db.query(ExperimentVersion)
        .filter(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version_number.asc())
        .all()
    )
    version_ids = [version.id for version in versions]
    version_number_by_id = {version.id: version.version_number for version in versions}

    runs = (
        db.query(ResearchRun)
        .filter(ResearchRun.experiment_version_id.in_(version_ids))
        .order_by(ResearchRun.started_at.desc())
        .all()
        if version_ids
        else []
    )
    latest_completed_run = next(
        (run for run in runs if run.status == RunStatus.completed), None
    )

    notes = (
        db.query(ResearchNote)
        .filter(ResearchNote.experiment_id == experiment.id)
        .order_by(ResearchNote.created_at.asc())
        .all()
    )

    studies = (
        db.query(RobustnessStudy)
        .filter(RobustnessStudy.experiment_id == experiment.id)
        .order_by(RobustnessStudy.created_at.asc())
        .all()
    )
    study_ids = [study.id for study in studies]
    attempts_by_study: Dict[Any, List[RobustnessAttempt]] = {
        study_id: [] for study_id in study_ids
    }
    if study_ids:
        for attempt in (
            db.query(RobustnessAttempt)
            .filter(RobustnessAttempt.study_id.in_(study_ids))
            .order_by(RobustnessAttempt.created_at.asc())
            .all()
        ):
            attempts_by_study[attempt.study_id].append(attempt)

    version_id_by_run_id = {run.id: run.experiment_version_id for run in runs}

    study_by_run_id: Dict[Any, Dict[str, Any]] = {}
    for study in studies:
        for attempt in attempts_by_study[study.id]:
            if attempt.run_id:
                study_by_run_id[attempt.run_id] = {
                    "study_id": str(study.id),
                    "kind": study.kind,
                    "cell_key": attempt.cell_key,
                    "label": attempt.label,
                    "role": attempt.role,
                }

    saved_comparisons = (
        db.query(SavedComparison)
        .filter(
            SavedComparison.experiment_id == experiment.id,
            SavedComparison.author_id == experiment.owner_id,
        )
        .order_by(SavedComparison.created_at.asc())
        .all()
    )

    sessions = (
        db.query(SimulationSession)
        .filter(
            SimulationSession.experiment_version_id.in_(version_ids),
            SimulationSession.owner_id == experiment.owner_id,
        )
        .order_by(SimulationSession.created_at.asc())
        .all()
        if version_ids
        else []
    )
    session_ids = [session.id for session in sessions]
    decisions_by_session: Dict[Any, List[SimulationDecision]] = {
        session_id: [] for session_id in session_ids
    }
    human_by_session: Dict[Any, List[HumanDecisionEvent]] = {
        session_id: [] for session_id in session_ids
    }
    if session_ids:
        for decision in (
            db.query(SimulationDecision)
            .filter(SimulationDecision.session_id.in_(session_ids))
            .order_by(SimulationDecision.step_number.asc())
            .all()
        ):
            decisions_by_session[decision.session_id].append(decision)
        for human_event in (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.simulation_session_id.in_(session_ids))
            .order_by(HumanDecisionEvent.created_at.asc())
            .all()
        ):
            human_by_session[human_event.simulation_session_id].append(human_event)

    run_ids = [run.id for run in runs]
    note_ids = [note.id for note in notes]
    entity_ids = (
        [str(experiment.id)]
        + [str(version_id) for version_id in version_ids]
        + [str(run_id) for run_id in run_ids]
        + [str(note_id) for note_id in note_ids]
    )
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_id.in_(entity_ids))
        .order_by(AuditEvent.created_at.asc())
        .all()
    )

    current_version = next(
        (v for v in versions if v.id == experiment.current_version_id),
        versions[-1] if versions else None,
    )

    revisions = [
        entry for entry in (_revision_entry(version) for version in versions) if entry
    ]

    revision_by_version_id = {entry["version_id"]: entry for entry in revisions}

    coverage = {
        "versions": len(versions),
        "revisions": len(revisions),
        "runs": len(runs),
        "completed_runs": sum(1 for run in runs if run.status == RunStatus.completed),
        "robustness_studies": len(studies),
        "robustness_attempts": sum(len(items) for items in attempts_by_study.values()),
        "saved_comparisons": len(saved_comparisons),
        "simulation_sessions": len(sessions),
        "simulation_decisions": sum(
            len(items) for items in decisions_by_session.values()
        ),
        "human_decisions": sum(len(items) for items in human_by_session.values()),
        "notes": len(notes),
        "events": len(events),
        "excluded": list(EXCLUDED_RUN_FIELDS),
    }

    brand = get_brand()

    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": {
            "id": str(experiment.id),
            "title": experiment.title,
            "original_idea": experiment.original_idea,
            "hypothesis": experiment.hypothesis,
            "falsification_conditions": experiment.falsification_conditions,
            "research_type": experiment.research_type,
            "status": _enum_value(experiment.status),
        },
        "coverage": coverage,
        "decision_review": {
            "revisions": revisions,
            "robustness_studies": [
                study_payload(study, attempts_by_study[study.id]) for study in studies
            ],
            "saved_comparisons": [
                {
                    "id": str(saved.id),
                    "run_a_id": str(saved.run_a_id),
                    "run_b_id": str(saved.run_b_id),
                    "run_a_version_number": version_number_by_id.get(
                        version_id_by_run_id.get(saved.run_a_id)
                    ),
                    "run_b_version_number": version_number_by_id.get(
                        version_id_by_run_id.get(saved.run_b_id)
                    ),
                    "purpose": saved.purpose,
                    "notes": saved.notes,
                    "created_at": _iso(saved.created_at),
                    "updated_at": _iso(saved.updated_at),
                }
                for saved in saved_comparisons
            ],
            "simulation_sessions": [
                {
                    "id": str(session.id),
                    "version_id": str(session.experiment_version_id),
                    "version_number": version_number_by_id.get(
                        session.experiment_version_id
                    ),
                    "status": _enum_value(session.status),
                    "timeframe": session.timeframe or "1d",
                    "data_cutoff_at": session.data_cutoff_at,
                    "current_snapshot_date": session.current_snapshot_date,
                    "initial_virtual_cash": session.initial_virtual_cash,
                    "paper_equity": session.paper_equity,
                    "observed_days": session.observed_days,
                    "created_at": _iso(session.created_at),
                    "decisions": [
                        _decision_entry(decision)
                        for decision in decisions_by_session[session.id]
                    ],
                    "human_decisions": [
                        _human_decision_entry(human_event)
                        for human_event in human_by_session[session.id]
                    ],
                }
                for session in sessions
            ],
        },
        "version_history": [
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "config_hash": version.config_hash,
                "change_reason": version.change_reason,
                "created_at": _iso(version.created_at),
                "strategy_spec": version.strategy_spec,
                "revision": revision_by_version_id.get(str(version.id)),
            }
            for version in versions
        ],
        "current_version": (
            {
                "id": str(current_version.id),
                "version_number": current_version.version_number,
                "config_hash": current_version.config_hash,
                "strategy_spec": current_version.strategy_spec,
            }
            if current_version
            else None
        ),
        "runs": [
            {
                "id": str(run.id),
                "experiment_version_id": str(run.experiment_version_id),
                "version_number": version_number_by_id.get(run.experiment_version_id),
                "engine_version": run.engine_version,
                "config_hash": run.config_hash,
                "dataset_checksum": run.dataset_checksum,
                "status": _enum_value(run.status),
                "metrics": run.metrics,
                "warnings": run.warnings,
                "methodology": run.methodology_json,
                "error_details": run.error_details,
                "started_at": _iso(run.started_at),
                "completed_at": _iso(run.completed_at),
                "robustness": study_by_run_id.get(run.id),
            }
            for run in runs
        ],
        "events": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "actor_type": event.actor_type,
                "payload": event.payload_json,
                "recorded_before_outcome": event.recorded_before_outcome,
                "rules_locked": event.rules_locked,
                "data_cutoff_at": event.data_cutoff_at,
                "created_at": _iso(event.created_at),
            }
            for event in events
        ],
        "notes": [
            {
                "id": str(note.id),
                "content": note.content,
                "version_id": str(note.version_id) if note.version_id else None,
                "created_at": _iso(note.created_at),
            }
            for note in notes
        ],
        "methodology": (
            latest_completed_run.methodology_json if latest_completed_run else None
        ),
        "disclaimer": brand.disclaimer,
        "disclaimer_zh": brand.disclaimer_zh,
        "disclaimer_short": brand.disclaimer_short,
        "disclaimer_short_zh": brand.disclaimer_short_zh,
    }
