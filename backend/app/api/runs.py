from datetime import datetime, timezone
from typing import List, Optional
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
from app.api.schemas import ResearchRunResponse, VersionDiffResponse, VersionDiffEntry, RunComparisonResponse, RunComparisonField
from app.engine.backtest import run_backtest, ENGINE_VERSION
from app.providers import get_data_provider
from app.auth.entitlements import require_entitlement, reserve_quota, settle_reservation, release_reservation
from app.auth.api_keys import check_api_key_scope
from app.services import emit_event

router = APIRouter(tags=["runs"])


@router.post("/versions/{version_id}/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    version_id: UUID,
    data_source: str = Query("auto", pattern="^(auto|stored|provider)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_entitlement(quota_type="research_run")),
    _scope_check=Depends(check_api_key_scope("write")),
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

    reservation = reserve_quota(db, current_user.id, "research_run")

    try:
        run.status = RunStatus.running
        run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()

        data_provider = get_data_provider()
        result = run_backtest(
            version.strategy_spec,
            data_provider=data_provider,
            db_session=db,
            data_source=data_source,
        )

        run.status = RunStatus.completed
        run.metrics = result["metrics"]
        run.warnings = result.get("warnings", [])
        run.dataset_checksum = result.get("dataset_checksum")
        run.methodology_json = result.get("methodology_summary")
        run.result_json = {
            "transactions": result.get("transactions", []),
            "daily_equity_curve": result.get("daily_equity_curve", []),
            "daily_accounting": result.get("daily_accounting", []),
            "data_provider": result.get("data_provider"),
            "feed_mode": result.get("feed_mode"),
            "data_source_used": result.get("data_source_used"),
            "data_nature": result.get("data_nature"),
            "dataset_checksum": result.get("dataset_checksum"),
            "start_date": result.get("start_date"),
            "end_date": result.get("end_date"),
            "initial_cash": result.get("initial_cash"),
            "final_equity": result.get("final_equity"),
        }
        run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        settle_reservation(db, reservation.id)

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
        release_reservation(db, reservation.id)

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


METRICS_FIELDS = ["total_return_pct", "final_equity", "max_drawdown_pct", "total_trades", "sharpe_ratio"]
CONDITION_FIELDS = [
    "template_type", "asset_ids", "start_date", "end_date",
    "initial_cash", "commission_rate", "slippage_rate",
    "benchmark_id", "engine_version",
]


def _resolve_field(
    field: str,
    run: ResearchRun,
    spec: dict,
    result: Optional[dict],
) -> tuple:
    """Resolve a field's value for one side, independently.

    Returns (value, source_kind, field_path). Priority:
    1. run column (engine_version, config_hash, dataset_checksum)
    2. run.result_json (run-captured execution window and totals)
    3. run.metrics (derived metrics)
    4. version.strategy_spec (version metadata, may have drifted from run-captured)
    5. absent
    """
    if field == "engine_version":
        return run.engine_version, "run_column", "research_runs.engine_version"
    if field == "config_hash":
        return run.config_hash, "run_column", "research_runs.config_hash"
    if field == "dataset_checksum":
        # Prefer the durable run column; fall back to result_json mirror for older rows
        if run.dataset_checksum is not None:
            return run.dataset_checksum, "run_column", "research_runs.dataset_checksum"
        if result and field in result:
            return result[field], "run_result", f"result_json.{field}"
        return None, "absent", None
    if result and field in result:
        return result[field], "run_result", f"result_json.{field}"
    if run.metrics and field in run.metrics:
        return run.metrics[field], "run_metrics", f"metrics.{field}"
    if field in spec:
        return spec[field], "version_spec", f"strategy_spec.{field}"
    return None, "absent", None


_PROVENANCE_BY_KIND = {
    "run_column": "captured",
    "run_result": "captured",
    "run_metrics": "derived",
    "version_spec": "current",
    "absent": "absent",
}

_SOURCE_CATEGORY_BY_KIND = {
    "run_column": "run_record",
    "run_result": "run_record",
    "run_metrics": "run_record",
    "version_spec": "version_metadata",
}


def _provenance_from_kind(kind: str) -> str:
    return _PROVENANCE_BY_KIND.get(kind, "absent")


def _get_evidence_detail(
    run: ResearchRun,
    version: ExperimentVersion,
    kind: str,
    path: Optional[str],
) -> Optional[dict]:
    if kind == "absent":
        return None
    record_id = str(version.id) if kind == "version_spec" else str(run.id)
    detail = {
        "source_category": _SOURCE_CATEGORY_BY_KIND.get(kind, "run_record"),
        "record_id": record_id,
        "field_path": path,
    }
    if kind == "run_metrics":
        detail["derivation"] = f"Computed from run {run.id} execution data"
    if kind == "version_spec":
        detail["note"] = "Read from current ExperimentVersion.strategy_spec; may not equal the spec at run execution time if the version was never locked."
    return detail


def _compare_field(
    field: str,
    run_a: ResearchRun,
    run_b: ResearchRun,
    version_a: ExperimentVersion,
    version_b: ExperimentVersion,
    spec_a: dict,
    spec_b: dict,
    result_a: Optional[dict],
    result_b: Optional[dict],
) -> RunComparisonField:
    val_a, kind_a, path_a = _resolve_field(field, run_a, spec_a, result_a)
    val_b, kind_b, path_b = _resolve_field(field, run_b, spec_b, result_b)

    if val_a is None and val_b is None:
        comparison_status = "insufficient"
    elif val_a is None or val_b is None:
        # One-sided missing: not a meaningful difference
        comparison_status = "insufficient"
    elif val_a == val_b:
        comparison_status = "match"
    else:
        comparison_status = "differ"

    prov_a = _provenance_from_kind(kind_a)
    prov_b = _provenance_from_kind(kind_b)

    evidence_a = _get_evidence_detail(run_a, version_a, kind_a, path_a)
    evidence_b = _get_evidence_detail(run_b, version_b, kind_b, path_b)

    return RunComparisonField(
        field=field,
        value_a=val_a,
        value_b=val_b,
        comparison_status=comparison_status,
        provenance_a=prov_a,
        provenance_b=prov_b,
        evidence_detail={"run_a": evidence_a, "run_b": evidence_b},
    )


def _effective_period(run: ResearchRun, result: Optional[dict], spec: dict) -> tuple:
    """Return (start, end, source) preferring run-captured result_json dates."""
    if result and result.get("start_date") and result.get("end_date"):
        return result["start_date"], result["end_date"], "run_captured"
    if spec.get("start_date") and spec.get("end_date"):
        return spec["start_date"], spec["end_date"], "version_spec"
    return None, None, "unknown"


@router.get("/runs/compare", response_model=RunComparisonResponse)
def compare_runs(
    run_a: UUID = Query(..., description="First run ID"),
    run_b: UUID = Query(..., description="Second run ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Compare two research runs from the same experiment."""
    # Fetch both runs with ownership check
    run_a_obj = db.query(ResearchRun).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).join(
        Experiment, ExperimentVersion.experiment_id == Experiment.id
    ).filter(
        ResearchRun.id == run_a,
        Experiment.owner_id == current_user.id
    ).first()

    run_b_obj = db.query(ResearchRun).join(
        ExperimentVersion, ResearchRun.experiment_version_id == ExperimentVersion.id
    ).join(
        Experiment, ExperimentVersion.experiment_id == Experiment.id
    ).filter(
        ResearchRun.id == run_b,
        Experiment.owner_id == current_user.id
    ).first()

    if not run_a_obj or not run_b_obj:
        raise HTTPException(status_code=404, detail="Run not found or not authorized")

    # Verify same experiment
    version_a = db.query(ExperimentVersion).filter(ExperimentVersion.id == run_a_obj.experiment_version_id).first()
    version_b = db.query(ExperimentVersion).filter(ExperimentVersion.id == run_b_obj.experiment_version_id).first()

    if not version_a or not version_b:
        raise HTTPException(status_code=404, detail="Version not found")

    if version_a.experiment_id != version_b.experiment_id:
        raise HTTPException(status_code=400, detail="Runs must be from the same experiment")

    # Prevent comparing a run with itself
    if run_a == run_b:
        raise HTTPException(status_code=400, detail="Cannot compare a run with itself")

    # Only completed runs may be compared — pending/running/failed lack the
    # result_json/metrics needed for a meaningful comparison and would
    # silently produce empty or misleading field rows.
    if run_a_obj.status != RunStatus.completed or run_b_obj.status != RunStatus.completed:
        raise HTTPException(
            status_code=400,
            detail="Only completed runs can be compared",
        )

    # Get strategy specs
    spec_a = version_a.strategy_spec or {}
    spec_b = version_b.strategy_spec or {}

    # Get result_json
    result_a = run_a_obj.result_json or {}
    result_b = run_b_obj.result_json or {}

    # Compare metrics
    metrics_comparison = []
    for field in METRICS_FIELDS:
        metrics_comparison.append(_compare_field(field, run_a_obj, run_b_obj, version_a, version_b, spec_a, spec_b, result_a, result_b))

    # Compare conditions
    conditions_comparison = []
    for field in CONDITION_FIELDS:
        conditions_comparison.append(_compare_field(field, run_a_obj, run_b_obj, version_a, version_b, spec_a, spec_b, result_a, result_b))

    # Check period overlap
    period_overlap = None
    start_a, end_a, src_a = _effective_period(run_a_obj, result_a, spec_a)
    start_b, end_b, src_b = _effective_period(run_b_obj, result_b, spec_b)
    periods_known = start_a is not None and end_a is not None and start_b is not None and end_b is not None
    periods_differ = periods_known and (start_a != start_b or end_a != end_b)
    # If either side's period cannot be established (unknown), we cannot
    # claim the two runs covered comparable windows.
    periods_incomplete = not periods_known

    if periods_known:
        overlap_start = max(start_a, start_b)
        overlap_end = min(end_a, end_b)
        if overlap_start <= overlap_end:
            period_overlap = {
                "run_a_period": {"start": start_a, "end": end_a, "source": src_a},
                "run_b_period": {"start": start_b, "end": end_b, "source": src_b},
                "overlap": {"start": overlap_start, "end": overlap_end},
            }

    # Build limitations
    limitations = []
    suppress_return_delta = False
    if periods_differ:
        limitations.append("兩個 runs 日期範圍唔同，完整期間嘅累計回報唔可以直接比較")
        suppress_return_delta = True
    if periods_incomplete:
        limitations.append("無法確定其中一個或多個 run 嘅完整日期範圍，累計回報唔可以直接比較")
        suppress_return_delta = True

    if suppress_return_delta:
        # Suppress return delta when periods differ or are unknown, but preserve recorded values
        for field_comp in metrics_comparison:
            if field_comp.field == "total_return_pct":
                field_comp.comparison_status = "insufficient"
                # Keep value_a and value_b as recorded; frontend will not show delta

    # Check for missing critical fields (dataset_checksum resolved from run column,
    # data_provider resolved from result_json — provider_id column does not exist here)
    missing_fields = []
    checksum_a, _, _ = _resolve_field("dataset_checksum", run_a_obj, spec_a, result_a)
    checksum_b, _, _ = _resolve_field("dataset_checksum", run_b_obj, spec_b, result_b)
    if not checksum_a or not checksum_b:
        missing_fields.append("dataset_checksum")
    provider_a = (result_a or {}).get("data_provider")
    provider_b = (result_b or {}).get("data_provider")
    if not provider_a or not provider_b:
        missing_fields.append("data_provider")

    if missing_fields:
        limitations.append(f"部分資料來源資訊缺失：{', '.join(missing_fields)}")

    return RunComparisonResponse(
        run_a_id=run_a_obj.id,
        run_b_id=run_b_obj.id,
        experiment_id=version_a.experiment_id,
        experiment_version_a_id=version_a.id,
        experiment_version_b_id=version_b.id,
        status_a=run_a_obj.status,
        status_b=run_b_obj.status,
        metrics_comparison=metrics_comparison,
        conditions_comparison=conditions_comparison,
        limitations=limitations,
        period_overlap=period_overlap,
    )
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


