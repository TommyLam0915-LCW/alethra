"""Bounded robustness studies: plan, create, execute, resume, cancel."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.schemas import RobustnessPlanRequest, RobustnessStudyCreate
from app.auth.api_keys import check_api_key_scope
from app.auth.entitlements import (
    get_quota_status,
    release_reservation,
    require_entitlement,
    reserve_quota,
    settle_reservation,
)
from app.auth.jwt import get_current_user
from app.auth.provenance import request_actor_provenance
from app.database import get_db
from app.lib.timeutil import utcnow
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.quota import QuotaReservation
from app.models.robustness import RobustnessAttempt, RobustnessStudy
from app.models.user import User
from app.services.robustness import (
    RobustnessRefused,
    build_plan,
    execute_study,
    study_authorization,
    study_payload,
)

router = APIRouter(tags=["robustness"])

QUOTA_CHECK_PATH = "/api/v1/usage/quota?quota_type=research_run_limit&period=monthly"


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


def _insufficient_quota(quota: dict, estimated_attempts: int) -> HTTPException:
    remaining = quota.get("remaining")
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": "insufficient_quota",
            "quota_type": "research_run",
            "unit": "study",
            "required": 1,
            "estimated_attempts": estimated_attempts,
            "remaining": remaining,
            "used": quota.get("used"),
            "limit": quota.get("limit"),
            "source": quota.get("source"),
            "check_url": QUOTA_CHECK_PATH,
            "message": (
                f"This study needs 1 research-run authorization for its "
                f"{estimated_attempts} backtests, and {remaining} remain, so the study "
                "did not start. The whole study is one unit; it is not charged per "
                f"backtest. Check used versus limit at GET {QUOTA_CHECK_PATH}."
            ),
        },
    )


def _locked_study(db: Session, study_id: UUID, user: User) -> RobustnessStudy:
    """Read the study under a row lock, serialising claim attempts."""
    study = (
        db.query(RobustnessStudy)
        .filter(RobustnessStudy.id == study_id, RobustnessStudy.user_id == user.id)
        .with_for_update()
        .first()
    )
    if not study:
        raise HTTPException(status_code=404, detail="Study not found")
    return study


def _refuse_if_running(
    db: Session,
    study: RobustnessStudy,
    *,
    estimated_attempts: int,
    own_reservation_id: UUID = None,
) -> None:
    """409 while another request holds this study's authorization.

    A settled or released claim is history; only a ``reserved`` one means a
    run is executing right now, and a second concurrent run would duplicate
    its cells.
    """
    other_id = study.authorization_reservation_id
    if other_id is None or other_id == own_reservation_id:
        return
    other = (
        db.query(QuotaReservation).filter(QuotaReservation.id == other_id).first()
    )
    if other is not None and other.status == "reserved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "study_running",
                "message": (
                    "Another run of this study is already in progress under its "
                    "single authorization. Wait for it to finish, then re-read the "
                    "study to see which cells remain."
                ),
                "estimated_attempts": estimated_attempts,
            },
        )


def _finalize_authorization(db: Session, reservation_id: UUID, study: RobustnessStudy) -> None:
    """Settle the one unit if any backtest ran; release it if none did."""
    attempts = _attempts(db, study)
    if any(attempt.run_id is not None for attempt in attempts):
        settle_reservation(db, reservation_id)
    else:
        release_reservation(db, reservation_id)
        # No evidence was produced, so the unit was not consumed. Clearing the
        # claim lets a corrected retry charge again.
        db.execute(
            update(RobustnessStudy)
            .where(
                RobustnessStudy.id == study.id,
                RobustnessStudy.authorization_reservation_id == reservation_id,
            )
            .values(authorization_reservation_id=None)
            .execution_options(synchronize_session=False)
        )
    db.commit()


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
    current_user: User = Depends(require_entitlement()),
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

    if not plan["ready"]:
        raise _insufficient_quota(plan["quota"], plan["estimated_attempts"])

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
    current_user: User = Depends(require_entitlement()),
    _scope_check=Depends(check_api_key_scope("write")),
):
    """Execute the planned cells; re-running only fills missing cells.

    A study consumes one research-run authorization on its first execution,
    however many cells it contains. Continuing a study that already ran is
    free, so a partial study can be finished without a second unit.
    """
    study = _locked_study(db, study_id, current_user)
    experiment = _owned_experiment(db, study.experiment_id, current_user)
    version = _owned_version(db, experiment, study.version_id)

    authorization = study_authorization(study, _attempts(db, study))
    estimated_attempts = authorization["estimated_attempts"]
    reservation = None

    try:
        if not authorization["charge_on_next_run"]:
            # Already charged (or legacy evidence exists): a concurrent run
            # must not duplicate the remaining cells.
            _refuse_if_running(
                db, study, estimated_attempts=estimated_attempts
            )
        else:
            quota = get_quota_status(db, current_user.id, "research_run")
            if not quota.get("unlimited") and (quota.get("remaining") or 0) < 1:
                raise _insufficient_quota(quota, estimated_attempts)
            try:
                reservation = reserve_quota(
                    db, current_user.id, "research_run", amount=1
                )
            except HTTPException as exc:
                if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    raise _insufficient_quota(
                        get_quota_status(db, current_user.id, "research_run"),
                        estimated_attempts,
                    )
                raise
            # Atomic claim: only one request can attach a unit to this study.
            claimed = db.execute(
                update(RobustnessStudy)
                .where(
                    RobustnessStudy.id == study.id,
                    RobustnessStudy.authorization_reservation_id.is_(None),
                )
                .values(authorization_reservation_id=reservation.id)
                .execution_options(synchronize_session=False)
            ).rowcount
            # Commit before executing so a later refusal can release a known
            # reservation instead of losing track of an uncommitted row.
            db.commit()
            if not claimed:
                release_reservation(db, reservation.id)
                db.commit()
                reservation = None
                db.refresh(study)
                _refuse_if_running(
                    db, study, estimated_attempts=estimated_attempts
                )
    except HTTPException:
        db.rollback()
        raise

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
        db.rollback()
        if reservation is not None:
            _finalize_authorization(db, reservation.id, study)
        raise _refused(exc)
    except Exception:
        db.rollback()
        if reservation is not None:
            _finalize_authorization(db, reservation.id, study)
        raise

    if reservation is not None:
        _finalize_authorization(db, reservation.id, study)
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
