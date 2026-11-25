"""The two facts a strategy version must satisfy before anything binds to it.

``experiment_versions`` carries neither an owner column nor a status column:
ownership belongs to the parent experiment, and the state that makes a version
usable is the freeze stamp ``locked_at``, written once when its owner commits to
the contract (the same precondition the replay lane and the agent-strategy
service already apply). A check that asks the version row for the columns that
are not there does not fail safe — it raises, so every request naming a version
gets the same server error whether that version was valid, forged, or someone
else's.

Two entry points bind a caller to a strategy version — a paper decision's
lineage, and a paper account's run binding — so the rule is stated here once and
the refusals are identical whichever door was used.
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion


def validate_strategy_version(
    db: Session, *, version_id: UUID, owner_id: UUID
) -> ExperimentVersion:
    """Return the caller's own frozen version, or refuse with a reason code.

    Missing and not-owned answer the same way, because confirming that a foreign
    version id exists would leak which strategy research belongs to another
    account.
    """
    version = (
        db.query(ExperimentVersion)
        .join(Experiment, Experiment.id == ExperimentVersion.experiment_id)
        .filter(
            ExperimentVersion.id == version_id,
            Experiment.owner_id == owner_id,
        )
        .first()
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason": "version_not_found",
                "message": (
                    f"Experiment version {version_id} does not exist or does not "
                    "belong to this account."
                ),
            },
        )
    if version.locked_at is None:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "version_not_usable",
                "state": "unlocked",
                "message": (
                    "Experiment version is not locked; only a version its owner "
                    "has frozen may be bound to an account or a decision."
                ),
            },
        )
    return version
