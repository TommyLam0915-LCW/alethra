"""Bounded robustness studies: plan, create, execute, resume, cancel."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.schemas import RobustnessPlanRequest, RobustnessStudyCreate
from app.auth.api_keys import check_api_key_scope
from app.auth.entitlements import require_entitlement
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.database import get_db
from app.lib.timeutil import utcnow
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.robustness import RobustnessAttempt, RobustnessStudy
from app.models.user import User
from app.services.robustness import (
    RobustnessRefused,
    build_plan,
    execute_study,
    study_payload,
)

router = APIRouter(tags=["robustness"])


def _owned_experiment(db: Session, experiment_id: UUID, user: User) -> Experiment:
    experiment = (
        db.query(Experiment)
        .filter(Experiment.id == experiment_id, Experiment.owner_id == user.id)
        .first()
    )
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


def _owned_version(
    db: Session, experiment: Experiment, version_id: UUID
) -> ExperimentVersion:
    version = (
        db.query(ExperimentVersion)
        .filter(
            ExperimentVersion.id == version_id,
            ExperimentVersion.experiment_id == experiment.id,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


def _owned_study(db: Session, study_id: UUID, user: User) -> RobustnessStudy:
    study = (
        db.query(RobustnessStudy)
        .filter(RobustnessStudy.id == study_id, RobustnessStudy.user_id == user.id)
        .first()
    )
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    return study


def _attempts(db: Session, study: RobustnessStudy):
    return (
        db.query(RobustnessAttempt)
        .filter(RobustnessAttempt.study_id == study.id)
        .order_by(RobustnessAttempt.created_at, RobustnessAttempt.cell_key)
        .all()
    )


def _refused(exc: RobustnessRefused) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "robustness_refused",
            "message": "; ".join(exc.reasons),
            "reasons": exc.reasons,
        },
    )


def _config_dict(payload: RobustnessPlanRequest):
    return payload.config.model_dump() if payload.config else None


@router.post("/experiments/{experiment_id}/robustness/plan")
def plan_robustness_study(
    experiment_id: UUID,
    payload: RobustnessPlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    """Dry run: what would this study do, cost, and refuse to do?"""
    experiment = _owned_experiment(db, experiment_id, current_user)
    version = _owned_version(db, experiment, payload.version_id)
    try:
        return build_plan(
            db,
            user=current_user,
            experiment=experiment,
            version=version,
            kind=payload.kind,
            config=_config_dict(payload),
        )
    except RobustnessRefused as exc:
        raise _refused(exc)


@router.post(
    "/experiments/{experiment_id}/robustness",
    status_code=status.HTTP_201_CREATED,
)
def create_robustness_study(
    experiment_id: UUID,
    payload: RobustnessStudyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_entitlement(quota_type="research_run")),
    _scope_check=Depends(check_api_key_scope("write")),
):
    experiment = _owned_experiment(db, experiment_id, current_user)
    version = _owned_version(db, experiment, payload.version_id)

    if payload.request_id:
        existing = (
            db.query(RobustnessStudy)
            .filter(
                RobustnessStudy.user_id == current_user.id,
                RobustnessStudy.request_id == str(payload.request_id),
            )
            .first()
        )
        if existing:
            return study_payload(existing, _attempts(db, existing))

    try:
        plan = build_plan(
            db,
            user=current_user,
            experiment=experiment,
            version=version,
            kind=payload.kind,
            config=_config_dict(payload),
        )
    except RobustnessRefused as exc:
        raise _refused(exc)

    quota = plan["quota"]
    if not plan["ready"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "insufficient_quota",
                "quota_type": "research_run",
                "required": quota["required"],
                "remaining": quota.get("remaining"),
                "message": (
                    f"This plan needs {quota['required']} research runs but only "
                    f"{quota.get('remaining')} remain, so the study did not start. Reduce the number of attempts, "
                    "or check used versus limit on the Plans and usage page."
                ),
            },
        )

    study = RobustnessStudy(
        experiment_id=experiment.id,
        version_id=version.id,
        user_id=current_user.id,
        kind=payload.kind,
        status="planned",
        request_id=str(payload.request_id) if payload.request_id else None,
        config=plan["config"],
        plan=plan,
    )
    db.add(study)
    db.commit()
    db.refresh(study)
    return study_payload(study, [])


@router.get("/experiments/{experiment_id}/robustness")
def list_robustness_studies(
    experiment_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    experiment = _owned_experiment(db, experiment_id, current_user)
    studies = (
        db.query(RobustnessStudy)
        .filter(
            RobustnessStudy.experiment_id == experiment.id,
            RobustnessStudy.user_id == current_user.id,
        )
        .order_by(RobustnessStudy.created_at.desc())
        .limit(50)
        .all()
    )
    return [study_payload(study, _attempts(db, study)) for study in studies]


@router.get("/robustness/{study_id}")
def get_robustness_study(
    study_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("read")),
):
    study = _owned_study(db, study_id, current_user)
    return study_payload(study, _attempts(db, study))


@router.post("/robustness/{study_id}/run")
def run_robustness_study(
    study_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_entitlement(quota_type="research_run")),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Execute the planned cells; re-running only fills missing cells."""
    study = _owned_study(db, study_id, current_user)
    experiment = _owned_experiment(db, study.experiment_id, current_user)
    version = _owned_version(db, experiment, study.version_id)
    try:
        execute_study(
            db,
            study=study,
            version=version,
            experiment=experiment,
            user=current_user,
            actor_type=request_actor_provenance(request),
        )
    except RobustnessRefused as exc:
        raise _refused(exc)
    db.refresh(study)
    return study_payload(study, _attempts(db, study))


@router.post("/robustness/{study_id}/cancel")
def cancel_robustness_study(
    study_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _scope_check=Depends(check_api_key_scope("write")),
):
    study = _owned_study(db, study_id, current_user)
    if study.status in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "study_finished",
                "message": "This study has finished and cannot be cancelled; completed attempts and results are kept.",
            },
        )
    study.status = "cancelled"
    study.cancelled_at = utcnow()
    db.commit()
    db.refresh(study)
    return study_payload(study, _attempts(db, study))
