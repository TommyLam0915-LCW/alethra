"""Database loader for method checks."""
from typing import Any, Optional, Tuple
from uuid import UUID
from sqlalchemy.orm import Session
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User


def load_review_data(
    db: Session,
    experiment_id: UUID,
    current_user: User,
) -> Tuple[Experiment, Optional[ExperimentVersion], Optional[ResearchRun]]:
    """Load experiment, version, and latest completed run for review.

    Run selection: latest completed run across ALL versions of the experiment,
    ordered by completed_at DESC NULLS LAST, id DESC.
    The version is then loaded from the selected run's experiment_version_id.

    Args:
        db: Database session
        experiment_id: Experiment UUID
        current_user: Current authenticated user

    Returns:
        Tuple of (experiment, version, run)
        version and run may be None

    Raises:
        No exceptions - returns None for missing data
    """
    experiment = db.query(Experiment).filter(
        Experiment.id == experiment_id,
        Experiment.owner_id == current_user.id,
    ).first()

    if not experiment:
        return None, None, None

    version_ids = [v.id for v in db.query(ExperimentVersion.id).filter(
        ExperimentVersion.experiment_id == experiment_id,
    ).all()]

    run = None
    if version_ids:
        run = db.query(ResearchRun).filter(
            ResearchRun.experiment_version_id.in_(version_ids),
            ResearchRun.status == RunStatus.completed,
        ).order_by(
            ResearchRun.completed_at.desc().nullslast(),
            ResearchRun.id.desc(),
        ).first()

    version = None
    if run and run.experiment_version_id:
        version = db.query(ExperimentVersion).filter(
            ExperimentVersion.id == run.experiment_version_id,
        ).first()

    return experiment, version, run
