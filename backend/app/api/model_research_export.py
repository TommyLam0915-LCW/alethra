import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.jwt import get_current_user
from app.models.user import User
from app.models.model_research import (
    ModelResearchRecord,
    ModelResearchExport,
    ExportStatus,
    ModelVersion,
    ModelEvaluation,
    ModelCard,
    PersonalResearchMemory,
)
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_note import ResearchNote
from app.models.simulation import SimulationSession, SimulationDecision
from app.api.schemas import (
    ModelResearchExportCreate,
    ModelResearchExportResponse,
    PACKAGE_SCHEMA_VERSION,
    MANDATORY_EXCLUDED_CAPABILITIES,
    PACKAGE_DISCLAIMER,
)
from app.services import emit_event
from app.engine.backtest import ENGINE_VERSION


router = APIRouter(tags=["model-research-export"])


def _json_serialize(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def _compute_checksum(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, default=_json_serialize)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_package(
    db: Session,
    record: ModelResearchRecord,
    version: ModelVersion,
    include_private_notes: bool,
) -> dict:
    versions = (
        db.query(ModelVersion)
        .filter(ModelVersion.model_research_record_id == record.id)
        .order_by(ModelVersion.version_number)
        .all()
    )

    evaluations = (
        db.query(ModelEvaluation)
        .filter(ModelEvaluation.model_research_record_id == record.id)
        .all()
    )

    historical_evals = [e for e in evaluations if e.evaluation_type.value == "historical"]
    forward_evals = [e for e in evaluations if e.evaluation_type.value == "forward_simulation"]

    card = (
        db.query(ModelCard)
        .filter(ModelCard.model_version_id == version.id)
        .first()
    )

    memories = (
        db.query(PersonalResearchMemory)
        .filter(
            PersonalResearchMemory.owner_id == record.owner_id,
            PersonalResearchMemory.active.is_(True),
            PersonalResearchMemory.user_confirmed_at.isnot(None),
        )
        .all()
    )

    linked_experiment_ids = version.linked_experiment_version_ids or []
    linked_experiment_ids_str = [str(eid) for eid in linked_experiment_ids]

    experiment_versions = []
    strategy_specs = []
    if linked_experiment_ids_str:
        experiment_versions = (
            db.query(ExperimentVersion)
            .filter(ExperimentVersion.id.in_(linked_experiment_ids_str))
            .all()
        )
        experiment_ids = list(set(ev.experiment_id for ev in experiment_versions))
        experiments = db.query(Experiment).filter(Experiment.id.in_(experiment_ids)).all() if experiment_ids else []

        for ev in experiment_versions:
            strategy_specs.append({
                "experiment_version_id": str(ev.id),
                "version_number": ev.version_number,
                "strategy_spec": ev.strategy_spec,
                "config_hash": ev.config_hash,
                "change_reason": ev.change_reason,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            })

    linked_simulation_ids = version.linked_simulation_session_ids or []
    decisions = []
    if linked_simulation_ids:
        sim_sessions = (
            db.query(SimulationSession)
            .filter(SimulationSession.id.in_(linked_simulation_ids))
            .all()
        )
        for session in sim_sessions:
            session_decisions = (
                db.query(SimulationDecision)
                .filter(SimulationDecision.simulation_session_id == session.id)
                .all()
            )
            for d in session_decisions:
                decisions.append({
                    "decision_id": str(d.id),
                    "simulation_session_id": str(d.simulation_session_id),
                    "decision_type": d.decision_type.value if d.decision_type else None,
                    "rationale": d.rationale,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                })

    notes = []
    if include_private_notes:
        linked_experiment_ids_set = set(str(ev.experiment_id) for ev in experiment_versions) if experiment_versions else set()
        if linked_experiment_ids_set:
            notes_rows = (
                db.query(ResearchNote)
                .filter(ResearchNote.experiment_id.in_(list(linked_experiment_ids_set)))
                .order_by(ResearchNote.created_at)
                .all()
            )
            for n in notes_rows:
                notes.append({
                    "id": str(n.id),
                    "experiment_id": str(n.experiment_id),
                    "version_id": str(n.version_id) if n.version_id else None,
                    "content": n.content,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                })

    linked_dataset_ids = version.linked_dataset_snapshot_ids or []

    data_cutoff = card.data_cutoff if card else (
        historical_evals[0].data_cutoff_at if historical_evals else "unknown"
    )

    evidence_status = record.status.value
    if forward_evals:
        evidence_status = "has_forward_evidence"
    elif historical_evals:
        evidence_status = "has_historical_evidence"

    version_history = []
    for v in versions:
        version_history.append({
            "version_id": str(v.id),
            "version_number": v.version_number,
            "model_type": v.model_type.value if v.model_type else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "locked_at": v.locked_at.isoformat() if v.locked_at else None,
            "revoked_at": v.revoked_at.isoformat() if v.revoked_at else None,
            "revocation_reason": v.revocation_reason,
        })

    package_files = {}

    model_spec = {
        "model_version_id": str(version.id),
        "version_number": version.version_number,
        "model_type": version.model_type.value if version.model_type else None,
        "specification": version.model_specification_json,
        "prompt_version": version.prompt_version,
        "input_schema_version": version.input_schema_version,
        "output_schema_version": version.output_schema_version,
        "linked_experiment_version_ids": linked_experiment_ids_str,
        "linked_dataset_snapshot_ids": [str(d) for d in linked_dataset_ids],
        "linked_simulation_session_ids": [str(s) for s in (version.linked_simulation_session_ids or [])],
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "locked_at": version.locked_at.isoformat() if version.locked_at else None,
        "revoked_at": version.revoked_at.isoformat() if version.revoked_at else None,
        "revocation_reason": version.revocation_reason,
        "label": "research_metadata",
    }
    package_files["model-specification.json"] = model_spec

    if card:
        card_md = _build_model_card_md(card, record, version)
        package_files["model-card.md"] = card_md

        card_json = {
            "id": str(card.id),
            "model_version_id": str(card.model_version_id),
            "purpose": card.purpose,
            "intended_research_use": card.intended_research_use,
            "excluded_use": card.excluded_use,
            "data_sources": card.data_sources,
            "data_cutoff": card.data_cutoff,
            "linked_experiments": card.linked_experiments,
            "evaluation_evidence": card.evaluation_evidence,
            "known_limitations": card.known_limitations,
            "risk_flags": card.risk_flags,
            "version_history": card.version_history,
            "revocation_process": card.revocation_process,
            "disclaimer": card.disclaimer,
            "label": "research_metadata",
        }
    else:
        card_json = {"note": "No model card exists for this version.", "label": "research_metadata"}

    research_summary_md = _build_research_summary_md(record, version, evaluations, card)
    package_files["research-summary.md"] = research_summary_md

    package_files["strategy-spec.json"] = {
        "experiment_versions": strategy_specs,
        "label": "historical_simulation",
    }

    memory_list = []
    for m in memories:
        memory_list.append({
            "id": str(m.id),
            "memory_type": m.memory_type.value if m.memory_type else None,
            "content": m.content,
            "privacy_scope": m.privacy_scope.value if m.privacy_scope else None,
            "user_confirmed_at": m.user_confirmed_at.isoformat() if m.user_confirmed_at else None,
            "general_model_training_opt_in": m.general_model_training_opt_in,
        })
    package_files["approved-research-memory.json"] = {
        "memories": memory_list,
        "include_private_notes": include_private_notes,
        "notes": notes,
        "label": "research_metadata",
    }

    package_files["dataset-lineage.json"] = {
        "dataset_snapshot_ids": [str(d) for d in linked_dataset_ids],
        "source": "demo",
        "note": "All datasets are synthetic demo data. No real market data is included.",
        "label": "research_metadata",
    }

    hist_eval_list = []
    for e in historical_evals:
        hist_eval_list.append({
            "id": str(e.id),
            "evaluation_type": e.evaluation_type.value,
            "result_summary": e.result_summary,
            "metrics": e.metrics_json,
            "warning_flags": e.warning_flags,
            "evaluation_date": e.evaluation_date.isoformat() if e.evaluation_date else None,
            "data_cutoff_at": e.data_cutoff_at,
            "engine_version": e.engine_version,
            "evaluator": e.evaluator.value if e.evaluator else None,
            "methodology_version": e.methodology_version,
            "label": "historical_simulation",
        })
    package_files["historical-evaluations.json"] = {
        "evaluations": hist_eval_list,
        "label": "historical_simulation",
    }

    fwd_eval_list = []
    for e in forward_evals:
        fwd_eval_list.append({
            "id": str(e.id),
            "evaluation_type": e.evaluation_type.value,
            "result_summary": e.result_summary,
            "metrics": e.metrics_json,
            "warning_flags": e.warning_flags,
            "evaluation_date": e.evaluation_date.isoformat() if e.evaluation_date else None,
            "data_cutoff_at": e.data_cutoff_at,
            "engine_version": e.engine_version,
            "evaluator": e.evaluator.value if e.evaluator else None,
            "methodology_version": e.methodology_version,
            "label": "paper_simulation",
        })
    package_files["forward-simulation-evaluations.json"] = {
        "evaluations": fwd_eval_list,
        "label": "paper_simulation",
    }

    package_files["decision-ledger.json"] = {
        "decisions": decisions,
        "label": "paper_simulation",
    }

    risk_flags_data = card.risk_flags if card else []
    package_files["risk-flags.json"] = {
        "risk_flags": risk_flags_data,
        "label": "research_metadata",
    }

    methodology_md = _build_methodology_md(record, version, evaluations)
    package_files["methodology.md"] = methodology_md

    limitations_md = _build_limitations_md(card)
    package_files["limitations.md"] = limitations_md

    package_files["version-history.json"] = {
        "versions": version_history,
        "label": "research_metadata",
    }

    file_checksums = {}
    for fname, content in package_files.items():
        if isinstance(content, str):
            file_checksums[fname] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        else:
            raw = json.dumps(content, sort_keys=True, default=_json_serialize)
            file_checksums[fname] = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    integrity = {
        "algorithm": "sha256",
        "file_checksums": file_checksums,
        "note": "Checksum confirms package consistency, not investment correctness or independent audit.",
    }
    package_files["integrity.json"] = integrity

    all_content = json.dumps(package_files, sort_keys=True, default=_json_serialize)
    package_checksum = hashlib.sha256(all_content.encode("utf-8")).hexdigest()

    manifest = {
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "model_research_record_id": str(record.id),
        "model_version_id": str(version.id),
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "model_status": record.status.value,
        "evidence_status": evidence_status,
        "engine_version": ENGINE_VERSION,
        "dataset_snapshot_ids": [str(d) for d in linked_dataset_ids],
        "data_cutoff_at": data_cutoff,
        "package_checksum": package_checksum,
        "disclaimer": PACKAGE_DISCLAIMER,
        "excluded_capabilities": MANDATORY_EXCLUDED_CAPABILITIES,
        "included_files": sorted(package_files.keys()),
    }
    package_files["manifest.json"] = manifest

    manifest_raw = json.dumps(manifest, sort_keys=True, default=_json_serialize)
    integrity["file_checksums"]["manifest.json"] = hashlib.sha256(manifest_raw.encode("utf-8")).hexdigest()

    return package_files


def _build_model_card_md(card: ModelCard, record: ModelResearchRecord, version: ModelVersion) -> str:
    lines = [
        f"# Model Card: {record.title}",
        "",
        f"**Version**: {version.version_number}",
        f"**Model Type**: {version.model_type.value if version.model_type else 'unknown'}",
        f"**Data Cutoff**: {card.data_cutoff}",
        "",
        "## Purpose",
        card.purpose,
        "",
        "## Intended Research Use",
        card.intended_research_use,
        "",
        "## Excluded Use",
        card.excluded_use,
        "",
        "## Data Sources",
    ]
    if isinstance(card.data_sources, list):
        for ds in card.data_sources:
            lines.append(f"- {ds}")
    elif isinstance(card.data_sources, dict):
        lines.append(json.dumps(card.data_sources, indent=2))
    else:
        lines.append(str(card.data_sources))

    lines.extend([
        "",
        "## Known Limitations",
    ])
    if isinstance(card.known_limitations, list):
        for lim in card.known_limitations:
            lines.append(f"- {lim}")
    else:
        lines.append(str(card.known_limitations))

    if card.risk_flags:
        lines.extend(["", "## Risk Flags"])
        if isinstance(card.risk_flags, list):
            for rf in card.risk_flags:
                if isinstance(rf, dict):
                    lines.append(f"- **{rf.get('flag', 'unknown')}** ({rf.get('severity', '?')}): {rf.get('description', '')}")
                else:
                    lines.append(f"- {rf}")
        else:
            lines.append(json.dumps(card.risk_flags, indent=2))

    lines.extend([
        "",
        "## Revocation Process",
        card.revocation_process,
        "",
        "## Disclaimer",
        card.disclaimer,
        "",
        "---",
        PACKAGE_DISCLAIMER,
    ])
    return "\n".join(lines)


def _build_research_summary_md(
    record: ModelResearchRecord,
    version: ModelVersion,
    evaluations: list,
    card: Optional[ModelCard],
) -> str:
    lines = [
        f"# Research Summary: {record.title}",
        "",
        f"**Status**: {record.status.value}",
        f"**Purpose**: {record.purpose}",
        "",
    ]
    if record.description:
        lines.extend(["## Description", record.description, ""])

    lines.extend([
        "## Model Version",
        f"- Version: {version.version_number}",
        f"- Type: {version.model_type.value if version.model_type else 'unknown'}",
        f"- Created: {version.created_at.isoformat() if version.created_at else 'unknown'}",
    ])
    if version.locked_at:
        lines.append(f"- Locked: {version.locked_at.isoformat()}")
    if version.revoked_at:
        lines.append(f"- **REVOKED**: {version.revoked_at.isoformat()}")
        if version.revocation_reason:
            lines.append(f"- Revocation Reason: {version.revocation_reason}")

    lines.extend(["", "## Evaluations", f"Total: {len(evaluations)}"])
    for e in evaluations:
        lines.append(f"- [{e.evaluation_type.value}] {e.result_summary or 'No summary'} ({e.data_cutoff_at})")

    if card:
        lines.extend(["", "## Model Card", f"Card exists: {card.data_cutoff}"])

    lines.extend([
        "",
        "---",
        PACKAGE_DISCLAIMER,
    ])
    return "\n".join(lines)


def _build_methodology_md(
    record: ModelResearchRecord,
    version: ModelVersion,
    evaluations: list,
) -> str:
    lines = [
        f"# Methodology: {record.title}",
        "",
        "## Research Objective",
        record.purpose,
        "",
        "## Model Specification",
        "",
        "```json",
        json.dumps(version.model_specification_json, indent=2, default=_json_serialize),
        "```",
        "",
        "## Evaluation Methods",
    ]
    for e in evaluations:
        lines.extend([
            f"### {e.evaluation_type.value}",
            f"- Engine: {e.engine_version}",
            f"- Evaluator: {e.evaluator.value if e.evaluator else 'unknown'}",
            f"- Data Cutoff: {e.data_cutoff_at}",
        ])
        if e.methodology_version:
            lines.append(f"- Methodology Version: {e.methodology_version}")
        if e.result_summary:
            lines.append(f"- Result: {e.result_summary}")
        lines.append("")

    lines.extend([
        "---",
        PACKAGE_DISCLAIMER,
    ])
    return "\n".join(lines)


def _build_limitations_md(card: Optional[ModelCard]) -> str:
    lines = [
        "# Known Limitations",
        "",
    ]
    if card and card.known_limitations:
        if isinstance(card.known_limitations, list):
            for lim in card.known_limitations:
                lines.append(f"- {lim}")
        else:
            lines.append(str(card.known_limitations))
    else:
        lines.append("No model card exists. Limitations have not been formally documented.")

    lines.extend([
        "",
        "## General Limitations",
        "- All data is synthetic demo data (DEMO-ALPHA, DEMO-BETA, DEMO-BENCHMARK)",
        "- No real market data is included",
        "- Backtest results do not represent future performance",
        "- Paper trading does not reproduce real-money psychology or full market liquidity",
        "- No live trading is executed",
        "",
        "---",
        PACKAGE_DISCLAIMER,
    ])
    return "\n".join(lines)


@router.post(
    "/model-research-records/{record_id}/exports",
    response_model=ModelResearchExportResponse,
    status_code=201,
)
def create_export(
    record_id: uuid.UUID,
    body: ModelResearchExportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = db.query(ModelResearchRecord).filter(ModelResearchRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Model research record not found")
    if record.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    version = db.query(ModelVersion).filter(ModelVersion.id == body.model_version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Model version not found")
    if version.model_research_record_id != record.id:
        raise HTTPException(status_code=404, detail="Version does not belong to this record")

    try:
        package = _build_package(db, record, version, body.include_private_notes)
        checksum = None
        for fname, content in package.items():
            if fname == "manifest.json":
                continue
        manifest = package.get("manifest.json", {})
        checksum = manifest.get("package_checksum")

        export = ModelResearchExport(
            owner_id=current_user.id,
            model_research_record_id=record.id,
            model_version_id=version.id,
            status=ExportStatus.completed,
            include_private_notes=body.include_private_notes,
            package_json=package,
            package_checksum=checksum,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(export)
        db.flush()

        emit_event(
            db,
            entity_type="model_research_export",
            entity_id=export.id,
            event_type="export_generated",
            actor_id=current_user.id,
            payload={
                "record_id": str(record.id),
                "version_id": str(version.id),
                "include_private_notes": body.include_private_notes,
            },
        )
        db.commit()
        db.refresh(export)
    except Exception as e:
        db.rollback()
        export = ModelResearchExport(
            owner_id=current_user.id,
            model_research_record_id=record.id,
            model_version_id=version.id,
            status=ExportStatus.failed,
            include_private_notes=body.include_private_notes,
            error_message=str(e),
        )
        db.add(export)
        db.commit()
        db.refresh(export)

    return export


@router.get(
    "/exports/{export_id}",
    response_model=ModelResearchExportResponse,
)
def get_export(
    export_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export = db.query(ModelResearchExport).filter(ModelResearchExport.id == export_id).first()
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    if export.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return export


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    export = db.query(ModelResearchExport).filter(ModelResearchExport.id == export_id).first()
    if not export:
        raise HTTPException(status_code=404, detail="Export not found")
    if export.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    if export.status != ExportStatus.completed:
        raise HTTPException(status_code=400, detail="Export is not completed")
    if not export.package_json:
        raise HTTPException(status_code=400, detail="Package data is missing")

    return JSONResponse(content=export.package_json)
