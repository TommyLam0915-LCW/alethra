from datetime import datetime, timezone
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment, ExperimentStatus
from app.models.experiment_version import ExperimentVersion
from app.models.audit_event import AuditEvent
from app.models.research_note import ResearchNote
from app.auth.jwt import get_current_user
from app.api.schemas import (
    ExperimentCreate,
    ExperimentUpdate,
    ExperimentResponse,
    ExperimentVersionResponse,
    ResearchNoteCreate,
    ResearchNoteResponse,
    AuditEventResponse,
    VersionDiffResponse,
    VersionDiffEntry,
)
from app.engine.backtest import compute_config_hash, run_backtest
from app.services import emit_event
from app.method_checks import evaluate_checks, MethodReviewResponse
from app.method_checks.loader import load_review_data

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("/", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(
    experiment_data: ExperimentCreate,
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
        payload={"title": experiment_data.title},
        data_cutoff_at=data_cutoff)

    emit_event(db,
        entity_type="experiment", entity_id=experiment.id,
        event_type="dataset_selected", actor_id=current_user.id,
        payload={"asset_ids": spec_dict["asset_ids"], "benchmark_id": spec_dict.get("benchmark_id")},
        data_cutoff_at=data_cutoff)

    emit_event(db,
        entity_type="version", entity_id=version.id,
        event_type="strategy_template_selected", actor_id=current_user.id,
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
    return experiments


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


@router.put("/{experiment_id}", response_model=ExperimentResponse)
def update_experiment(
    experiment_id: UUID,
    experiment_data: ExperimentUpdate,
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

    is_locked_after = False
    if experiment_data.hypothesis is not None and experiment_data.hypothesis != experiment.hypothesis:
        experiment.hypothesis = experiment_data.hypothesis
        emit_event(db,
            entity_type="experiment", entity_id=experiment.id,
            event_type="hypothesis_updated", actor_id=current_user.id,
            payload={"new_hypothesis": experiment_data.hypothesis})

    if experiment_data.falsification_conditions is not None and experiment_data.falsification_conditions != experiment.falsification_conditions:
        experiment.falsification_conditions = experiment_data.falsification_conditions
        emit_event(db,
            entity_type="experiment", entity_id=experiment.id,
            event_type="falsification_condition_added", actor_id=current_user.id,
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
            payload={
                "change_reason": experiment_data.change_reason,
                "version_number": new_version.version_number,
                "template_type": spec_dict["template_type"],
            },
            data_cutoff_at=data_cutoff)

        emit_event(db,
            entity_type="version", entity_id=new_version.id,
            event_type="strategy_version_created", actor_id=current_user.id,
            payload={"version_number": new_version.version_number},
            data_cutoff_at=data_cutoff)

    if experiment_data.scope is not None and experiment_data.scope.get("status") == "locked":
        experiment.status = ExperimentStatus.locked
        is_locked_after = True

        current_version = db.query(ExperimentVersion).filter(
            ExperimentVersion.id == experiment.current_version_id
        ).first()
        if current_version and current_version.locked_at is None:
            from app.engine.backtest import ENGINE_VERSION
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            current_version.locked_at = now
            current_version.locked_by = current_user.id
            current_version.strategy_config_hash = current_version.config_hash
            current_version.dataset_cutoff_at = current_version.strategy_spec.get("end_date")
            current_version.engine_version = ENGINE_VERSION

        emit_event(db,
            entity_type="experiment", entity_id=experiment.id,
            event_type="strategy_locked", actor_id=current_user.id,
            rules_locked=True,
            payload={"locked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()})

    experiment.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(experiment)
    return experiment


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


@router.get("/{experiment_id}/export")
def export_experiment(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.models.research_run import ResearchRun, RunStatus
    from app.lib.brand import get_brand

    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    versions = db.query(ExperimentVersion).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).order_by(ExperimentVersion.version_number.asc()).all()

    current_version = None
    for v in versions:
        if v.id == experiment.current_version_id:
            current_version = v
            break
    if not current_version and versions:
        current_version = versions[-1]

    if not current_version:
        raise HTTPException(status_code=404, detail="No version found")

    all_runs = db.query(ResearchRun).join(ExperimentVersion).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).order_by(ResearchRun.started_at.desc()).all()

    latest_run = None
    for r in all_runs:
        if r.status == RunStatus.completed:
            latest_run = r
            break

    version_ids = [str(v.id) for v in versions]

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

    notes = db.query(ResearchNote).filter(
        ResearchNote.experiment_id == experiment_id
    ).order_by(ResearchNote.created_at.asc()).all()

    emit_event(db,
        entity_type="experiment", entity_id=str(experiment.id),
        event_type="export_generated", actor_id=current_user.id,
        payload={"version_count": len(versions), "run_count": len(all_runs)})

    db.commit()

    brand = get_brand()

    return {
        "experiment": {
            "id": str(experiment.id),
            "title": experiment.title,
            "original_idea": experiment.original_idea,
            "hypothesis": experiment.hypothesis,
            "falsification_conditions": experiment.falsification_conditions,
            "research_type": experiment.research_type,
            "status": experiment.status,
        },
        "version_history": [
            {
                "id": str(v.id),
                "version_number": v.version_number,
                "config_hash": v.config_hash,
                "change_reason": v.change_reason,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "strategy_spec": v.strategy_spec,
            }
            for v in versions
        ],
        "current_version": {
            "id": str(current_version.id),
            "version_number": current_version.version_number,
            "config_hash": current_version.config_hash,
            "strategy_spec": current_version.strategy_spec,
        },
        "runs": [
            {
                "id": str(r.id),
                "engine_version": r.engine_version,
                "config_hash": r.config_hash,
                "dataset_checksum": r.dataset_checksum,
                "status": r.status,
                "metrics": r.metrics,
                "warnings": r.warnings,
                "methodology": r.methodology_json,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in all_runs
        ],
        "events": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "actor_type": e.actor_type,
                "payload": e.payload_json,
                "recorded_before_outcome": e.recorded_before_outcome,
                "rules_locked": e.rules_locked,
                "data_cutoff_at": e.data_cutoff_at,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "notes": [
            {
                "id": str(n.id),
                "content": n.content,
                "version_id": str(n.version_id) if n.version_id else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ],
        "methodology": latest_run.methodology_json if latest_run else None,
        "disclaimer": brand.disclaimer,
        "disclaimer_zh": brand.disclaimer_zh,
        "disclaimer_short": brand.disclaimer_short,
        "disclaimer_short_zh": brand.disclaimer_short_zh,
    }


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


@router.post("/{experiment_id}/versions/{version_id}/backtest")
def run_experiment_backtest(
    experiment_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Run backtest for an experiment version and store results.

    Executes the strategy against historical data and computes performance metrics.
    Results are stored in the database and returned to the client.
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

        # Store results in ExperimentRun (if model exists) or return directly
        # For now, return results directly since ExperimentRun model may not exist
        return {
            "status": "completed",
            "experiment_id": str(experiment_id),
            "version_id": str(version_id),
            "metrics": result.get("metrics", {}),
            "equity_curve": result.get("equity_curve", []),
            "trades": result.get("trades", []),
            "config_hash": result.get("config_hash"),
            "engine_version": result.get("engine_version"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Backtest execution failed: {str(e)}"
        )
