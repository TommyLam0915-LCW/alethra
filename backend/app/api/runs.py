from datetime import datetime, timezone
from typing import List
from uuid import UUID
import hashlib
import json
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.auth.jwt import get_current_user
from app.api.schemas import ResearchRunResponse, VersionDiffResponse, VersionDiffEntry
from app.engine.backtest import run_backtest, ENGINE_VERSION
from app.providers import get_data_provider
from app.auth.entitlements import require_entitlement, consume_quota
from app.services import emit_event

router = APIRouter(tags=["runs"])


@router.post("/versions/{version_id}/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    version_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_entitlement(quota_type="research_run"))
):
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    experiment = db.query(Experiment).filter(
        Experiment.id == version.experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.engine.backtest import compute_config_hash
    config_hash = compute_config_hash(version.strategy_spec)

    run = ResearchRun(
        experiment_version_id=version.id,
        engine_version=ENGINE_VERSION,
        config_hash=config_hash,
        status=RunStatus.pending
    )
    db.add(run)
    db.flush()

    data_cutoff = version.strategy_spec.get("end_date")
    is_locked = experiment.status == "locked"

    emit_event(db,
        entity_type="run", entity_id=run.id,
        event_type="research_run_started", actor_id=current_user.id,
        payload={"version_id": str(version.id), "version_number": version.version_number},
        rules_locked=is_locked,
        data_cutoff_at=data_cutoff)

    try:
        run.status = RunStatus.running
        run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()

        data_provider = get_data_provider()
        result = run_backtest(version.strategy_spec, data_provider=data_provider)

        run.status = RunStatus.completed
        run.metrics = result["metrics"]
        run.warnings = result.get("warnings", [])
        run.dataset_checksum = result.get("dataset_checksum")
        run.methodology_json = result.get("methodology_summary")
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        consume_quota(db, current_user.id, "research_run")

        emit_event(db,
            entity_type="run", entity_id=run.id,
            event_type="research_run_completed", actor_id=current_user.id,
            payload={
                "total_return_pct": result["metrics"].get("total_return_pct"),
                "total_trades": result["metrics"].get("total_trades"),
            },
            rules_locked=is_locked,
            data_cutoff_at=data_cutoff)

        for w in result.get("warnings", []):
            emit_event(db,
                entity_type="run", entity_id=run.id,
                event_type="warning_generated", actor_id=current_user.id,
                actor_type="system",
                payload={"warning": w},
                rules_locked=is_locked,
                data_cutoff_at=data_cutoff)

        db.commit()
        db.refresh(run)
        return run

    except Exception as e:
        run.status = RunStatus.failed
        run.error_details = str(e)
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        emit_event(db,
            entity_type="run", entity_id=run.id,
            event_type="research_run_failed", actor_id=current_user.id,
            payload={"error": str(e)},
            rules_locked=is_locked,
            data_cutoff_at=data_cutoff)

        db.commit()
        db.refresh(run)
        return run


@router.get("/runs/{run_id}", response_model=ResearchRunResponse)
def get_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    run = db.query(ResearchRun).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).join(
        Experiment, ExperimentVersion.experiment_id == Experiment.id
    ).filter(
        ResearchRun.id == run_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/experiments/{experiment_id}/runs", response_model=List[ResearchRunResponse])
def list_runs(
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

    runs = db.query(ResearchRun).join(ExperimentVersion).filter(
        ExperimentVersion.experiment_id == experiment_id
    ).order_by(ResearchRun.started_at.desc()).all()
    return runs


COMPARE_FIELDS = [
    "template_type", "asset_ids", "start_date", "end_date",
    "initial_cash", "commission_rate", "slippage_rate",
    "signal_parameters", "benchmark_id",
]


@router.get("/versions/compare", response_model=VersionDiffResponse)
def compare_versions(
    v1: UUID = Query(...),
    v2: UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    version_a = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == v1
    ).first()
    version_b = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == v2
    ).first()
    if not version_a or not version_b:
        raise HTTPException(status_code=404, detail="Version not found")

    experiment = db.query(Experiment).filter(
        Experiment.id == version_a.experiment_id,
        Experiment.owner_id == current_user.id
    ).first()
    if not experiment:
        raise HTTPException(status_code=403, detail="Not authorized")

    spec_a = version_a.strategy_spec or {}
    spec_b = version_b.strategy_spec or {}

    diffs = []
    for field in COMPARE_FIELDS:
        val_a = spec_a.get(field)
        val_b = spec_b.get(field)
        if val_a != val_b:
            diffs.append(VersionDiffEntry(field=field, old_value=val_a, new_value=val_b))

    return VersionDiffResponse(
        version_a_id=version_a.id,
        version_b_id=version_b.id,
        version_a_number=version_a.version_number,
        version_b_number=version_b.version_number,
        diffs=diffs,
    )
