from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.auth.jwt import get_current_user
from app.api.schemas import (
    ResearchRunResponse,
    VersionDiffResponse,
    VersionDiffEntry,
    RunComparisonResponse,
    RunComparisonField,
    RunComparisonSide,
    RunComparability,
    ComparabilityDimension,
)
from app.engine.backtest import compute_config_hash
from app.auth.entitlements import require_entitlement
from app.auth.api_keys import check_api_key_scope
from app.services.research_runs import execute_backtest_run

router = APIRouter(tags=["runs"])


@router.post("/versions/{version_id}/runs", response_model=ResearchRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    version_id: UUID,
    data_source: str = Query("auto", pattern="^(auto|stored|provider)$"),
    provider_id: Optional[str] = Query(None, max_length=50),
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

    return execute_backtest_run(
        db,
        user=current_user,
        version=version,
        experiment=experiment,
        data_source=data_source,
        provider_id=provider_id,
    )


# Every execution-affecting setting a revision can change. Metadata the authoring
# surface does not edit (schema_version, currency, approval stamps) is excluded so
# it cannot pose as a research change.
COMPARE_FIELDS = [
    "template_type", "asset_ids", "frequency", "timeframe", "session",
    "start_date", "end_date", "initial_cash", "commission_rate",
    "slippage_rate", "benchmark_id", "signal_parameters", "entry_rule",
    "exit_rule", "sizing_rule", "risk_parameters", "execution_timing",
]

# A version stored before a field existed must read the same as one that stores
# the schema default, otherwise comparing it with a new version reports a change
# the user never made.
FIELD_DEFAULTS = {
    "frequency": "1d",
    "timeframe": "1d",
    "session": None,
    "signal_parameters": {},
    "entry_rule": {},
    "exit_rule": {},
    "sizing_rule": {},
    "risk_parameters": {},
    "execution_timing": {"execute_at": "next_open"},
}


def _compare_value(spec: dict, field: str):
    value = spec.get(field)
    return FIELD_DEFAULTS.get(field) if value is None else value


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
        val_a = _compare_value(spec_a, field)
        val_b = _compare_value(spec_b, field)
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


def _spec_source(run: ResearchRun, version: ExperimentVersion) -> tuple:
    """Whether the current version spec is provably the spec this run executed.

    The run stores a ``config_hash`` of the spec it was executed with, so the
    current version's spec can be checked against it. A match proves the fields
    read here are the run's own; a mismatch means they are current metadata and
    must not be presented as what the run used.
    """
    spec = version.strategy_spec or {}
    if compute_config_hash(spec) == run.config_hash:
        return (
            "run_verified",
            "The current version spec hashes to this run's config_hash, so it is "
            "the spec the run executed.",
        )
    return (
        "version_current_unverified",
        "The current version spec does not hash to this run's config_hash; these "
        "field values are current version metadata and may differ from what the "
        "run executed.",
    )


def _comparability_dimension(
    name: str,
    value_a,
    value_b,
    *,
    missing_reason: str,
    differ_reason: str,
) -> ComparabilityDimension:
    if value_a is None or value_b is None:
        return ComparabilityDimension(
            dimension=name,
            status="unknown",
            value_a=value_a,
            value_b=value_b,
            reason=missing_reason,
        )
    if value_a == value_b:
        return ComparabilityDimension(
            dimension=name,
            status="match",
            value_a=value_a,
            value_b=value_b,
        )
    return ComparabilityDimension(
        dimension=name,
        status="differ",
        value_a=value_a,
        value_b=value_b,
        reason=differ_reason,
    )


def _append_limitation(limitations: List[str], message: str) -> None:
    if message not in limitations:
        limitations.append(message)


def _cost_values(run: ResearchRun, spec: dict, result: Optional[dict]) -> dict:
    commission, _, _ = _resolve_field("commission_rate", run, spec, result)
    slippage, _, _ = _resolve_field("slippage_rate", run, spec, result)
    return {"commission_rate": commission, "slippage_rate": slippage}


def _cost_dimension(run_a, run_b, spec_a, spec_b, result_a, result_b) -> ComparabilityDimension:
    costs_a = _cost_values(run_a, spec_a, result_a)
    costs_b = _cost_values(run_b, spec_b, result_b)
    complete_a = all(v is not None for v in costs_a.values())
    complete_b = all(v is not None for v in costs_b.values())
    if not complete_a or not complete_b:
        return ComparabilityDimension(
            dimension="costs",
            status="unknown",
            value_a=costs_a,
            value_b=costs_b,
            reason="Commission or slippage is missing on at least one side, so the "
            "cost basis cannot be compared.",
        )
    if costs_a == costs_b:
        return ComparabilityDimension(
            dimension="costs", status="match", value_a=costs_a, value_b=costs_b
        )
    return ComparabilityDimension(
        dimension="costs",
        status="differ",
        value_a=costs_a,
        value_b=costs_b,
        reason="The two runs used different commission or slippage settings, so "
        "their returns are not on the same cost basis.",
    )


@router.get("/runs/compare", response_model=RunComparisonResponse)
def compare_runs(
    run_a: UUID = Query(..., description="First run ID"),
    run_b: UUID = Query(..., description="Second run ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Compare two completed runs owned by the current user.

    Runs may belong to the same experiment or to two different experiments of
    the same owner. Each run's experiment/version identity is preserved, and a
    ``comparability`` report states whether dataset, period, costs, engine and
    spec provenance actually match. A cross-experiment comparison is never
    labelled fair by default: the dimension checks decide it.
    """
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

    version_a = db.query(ExperimentVersion).filter(ExperimentVersion.id == run_a_obj.experiment_version_id).first()
    version_b = db.query(ExperimentVersion).filter(ExperimentVersion.id == run_b_obj.experiment_version_id).first()

    if not version_a or not version_b:
        raise HTTPException(status_code=404, detail="Version not found")

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

    same_experiment = version_a.experiment_id == version_b.experiment_id

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

    spec_source_a, spec_note_a = _spec_source(run_a_obj, version_a)
    spec_source_b, spec_note_b = _spec_source(run_b_obj, version_b)

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
        _append_limitation(limitations, "兩個 runs 日期範圍唔同，完整期間嘅累計回報唔可以直接比較")
        suppress_return_delta = True
    if periods_incomplete:
        _append_limitation(limitations, "無法確定其中一個或多個 run 嘅完整日期範圍，累計回報唔可以直接比較")
        suppress_return_delta = True

    if suppress_return_delta:
        # Suppress return delta when periods differ or are unknown, but preserve recorded values
        for field_comp in metrics_comparison:
            if field_comp.field == "total_return_pct":
                field_comp.comparison_status = "insufficient"
                # Keep value_a and value_b as recorded; frontend will not show delta

    # Comparability inputs: only these decide whether the two results sit on
    # the same basis. An unknown input is never treated as a match.
    checksum_a, _, _ = _resolve_field("dataset_checksum", run_a_obj, spec_a, result_a)
    checksum_b, _, _ = _resolve_field("dataset_checksum", run_b_obj, spec_b, result_b)
    provider_a = (result_a or {}).get("data_provider")
    provider_b = (result_b or {}).get("data_provider")

    dataset_dim = _comparability_dimension(
        "dataset",
        {"checksum": checksum_a, "provider": provider_a},
        {"checksum": checksum_b, "provider": provider_b},
        missing_reason="The dataset checksum or data provider is missing on at least "
        "one side, so the data basis cannot be compared.",
        differ_reason="The two runs used a different dataset checksum or data "
        "provider, so their returns are not on the same data basis.",
    )

    if periods_known:
        period_dim = _comparability_dimension(
            "period",
            {"start": start_a, "end": end_a},
            {"start": start_b, "end": end_b},
            missing_reason="The full period is unknown on at least one side.",
            differ_reason="The two runs cover different date ranges, so their "
            "cumulative returns are not directly comparable.",
        )
    else:
        period_dim = ComparabilityDimension(
            dimension="period",
            status="unknown",
            value_a={"start": start_a, "end": end_a},
            value_b={"start": start_b, "end": end_b},
            reason="The full period is unknown on at least one side.",
        )

    costs_dim = _cost_dimension(run_a_obj, run_b_obj, spec_a, spec_b, result_a, result_b)

    engine_dim = _comparability_dimension(
        "engine_version",
        run_a_obj.engine_version,
        run_b_obj.engine_version,
        missing_reason="The engine version is missing on at least one side.",
        differ_reason="The two runs used different engine versions, so their "
        "results are not directly comparable.",
    )

    spec_source_dim = ComparabilityDimension(
        dimension="spec_source",
        status=(
            "match"
            if spec_source_a == "run_verified" and spec_source_b == "run_verified"
            else "unknown"
        ),
        value_a={
            "source": spec_source_a,
            "config_hash": run_a_obj.config_hash,
            "experiment_version_id": str(version_a.id),
            "experiment_version_number": version_a.version_number,
        },
        value_b={
            "source": spec_source_b,
            "config_hash": run_b_obj.config_hash,
            "experiment_version_id": str(version_b.id),
            "experiment_version_number": version_b.version_number,
        },
        reason=(
            None
            if spec_source_a == "run_verified" and spec_source_b == "run_verified"
            else "At least one side's strategy spec is read from a current version "
            "that is not proven (by config_hash) to be the spec the run executed."
        ),
    )

    dimensions = [dataset_dim, period_dim, costs_dim, engine_dim, spec_source_dim]
    fair_comparison = all(d.status == "match" for d in dimensions)

    if not same_experiment:
        _append_limitation(
            limitations,
            "兩個 run 屬於唔同實驗；平台保留各自嘅實驗與版本身分，只有可比性檢查全部一致時，"
            "先會標示為同一基礎嘅比較。",
        )
    if dataset_dim.status == "differ":
        _append_limitation(
            limitations,
            "兩個 run 嘅 dataset checksum 或數據提供者唔同，數據基礎唔一致，回報差異唔可以直接歸因。",
        )
    elif dataset_dim.status == "unknown":
        _append_limitation(
            limitations,
            "未能確認兩個 run 嘅 dataset checksum 或數據提供者係否相同，數據基礎嘅一致性未成立。",
        )
    if costs_dim.status == "differ":
        _append_limitation(
            limitations,
            "兩個 run 嘅佣金或滑價設定唔同，回報唔係喺同一個成本基礎上計算。",
        )
    elif costs_dim.status == "unknown":
        _append_limitation(
            limitations,
            "未能確認兩個 run 嘅佣金或滑價設定係否相同，成本基礎嘅一致性未成立。",
        )
    if engine_dim.status == "differ":
        _append_limitation(
            limitations,
            "兩個 run 使用唔同引擎版本，結果唔可以直接比較。",
        )
    if spec_source_dim.status != "match":
        _append_limitation(
            limitations,
            "最少一邊嘅策略規格未能用 config_hash 證實就係該 run 當時使用嘅版本；規格欄位只屬目前版本資料。",
        )

    # Check for missing critical fields (dataset_checksum resolved from run column,
    # data_provider resolved from result_json — provider_id column does not exist here)
    missing_fields = []
    if not checksum_a or not checksum_b:
        missing_fields.append("dataset_checksum")
    if not provider_a or not provider_b:
        missing_fields.append("data_provider")

    if missing_fields:
        _append_limitation(limitations, f"部分資料來源資訊缺失：{', '.join(missing_fields)}")

    sides = [
        RunComparisonSide(
            run_id=run_a_obj.id,
            experiment_id=version_a.experiment_id,
            experiment_version_id=version_a.id,
            experiment_version_number=version_a.version_number,
            status=run_a_obj.status.value,
            spec_source=spec_source_a,
            spec_source_note=spec_note_a,
        ),
        RunComparisonSide(
            run_id=run_b_obj.id,
            experiment_id=version_b.experiment_id,
            experiment_version_id=version_b.id,
            experiment_version_number=version_b.version_number,
            status=run_b_obj.status.value,
            spec_source=spec_source_b,
            spec_source_note=spec_note_b,
        ),
    ]

    return RunComparisonResponse(
        run_a_id=run_a_obj.id,
        run_b_id=run_b_obj.id,
        experiment_id=version_a.experiment_id,
        experiment_a_id=version_a.experiment_id,
        experiment_b_id=version_b.experiment_id,
        experiment_version_a_id=version_a.id,
        experiment_version_b_id=version_b.id,
        status_a=run_a_obj.status,
        status_b=run_b_obj.status,
        sides=sides,
        metrics_comparison=metrics_comparison,
        conditions_comparison=conditions_comparison,
        limitations=limitations,
        period_overlap=period_overlap,
        comparability=RunComparability(
            same_experiment=same_experiment,
            fair_comparison=fair_comparison,
            dimensions=dimensions,
        ),
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


