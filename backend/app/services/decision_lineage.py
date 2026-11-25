"""One refusal set for the inference a decision claims as its own origin.

Two formal entry points accept ``linked_inference_id``: the paper desk
(``POST /paper-portfolio/{portfolio_id}/decisions``) and the replay's own
submission (``POST /simulations/{session_id}/manual-decision``). Kept in one
place because two copies of these rules would let one lane record an inference
the other refuses, and the exported journal would then carry a lineage identity
that only some writes were held to.

The version leg runs at both doors, each against the version that door is
recording: the paper desk compares against the version in the request and, when
the request names none, records the version the origin itself names — held to
the same eligibility and run-binding checks a stated version is, so naming
nothing cannot be a way of verifying less. The replay compares against its own
session's ``experiment_version_id``, because there the strategy is whatever the
user was watching and not a thing the request may claim. Measured 2026-11-21,
the replay lane could otherwise book an inference produced by a model trained
from a different strategy version than the decision row it was written beside,
so the exported journal carried a lineage whose two halves disagreed.
"""

import uuid
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.billing import UsageRecord


def check_inference_attribution(
    db: Session,
    *,
    owner_id: UUID,
    inference_id: UUID,
    portfolio_id: UUID,
) -> dict:
    """Refuse an inference this decision may not attribute to itself; else metadata.

    An inference belongs to this decision only when the same owner made it, it
    was written by the personal-model prediction path, and it was generated for
    this exact skill account. A missing, NULL or non-text ``skill_unit_id`` in
    the metadata is refused rather than read as "every skill".

    The metadata is returned because it is the only place the origin's strategy
    version is written, and a door that omits the version in its own request has
    to read that claim before it can check it.
    """
    usage_record = db.query(UsageRecord).filter(
        UsageRecord.id == inference_id,
        UsageRecord.user_id == owner_id,
    ).first()
    if usage_record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "reason": "inference_not_found",
                "message": f"Inference {inference_id} not found for this owner.",
            },
        )

    metadata = usage_record.metadata_json
    if not isinstance(metadata, dict):
        # A list/string/number in this column means the row was not written by
        # the prediction path. Refuse it rather than crash on ``.get``.
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_metadata_unreadable",
                "message": "The linked usage record carries no metadata object, so it cannot be a decision's inference lineage.",
            },
        )

    if metadata.get("prediction_type") != "personal_decision":
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_not_model_execution",
                "message": "Linked inference is not a model-execution prediction.",
            },
        )

    inference_skill_unit_id = metadata.get("skill_unit_id")
    if (
        not isinstance(inference_skill_unit_id, str)
        or inference_skill_unit_id != str(portfolio_id)
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_skill_mismatch",
                "inference_skill_unit_id": inference_skill_unit_id,
                "portfolio_id": str(portfolio_id),
                "message": "Linked inference was not generated for this skill account.",
            },
        )

    return metadata


def check_inference_version(
    metadata: dict, *, strategy_version_id: Optional[UUID]
) -> Optional[uuid.UUID]:
    """Return the version this origin names; refuse when a claim contradicts it.

    ``strategy_version_id`` is the version the *door* is recording against: the
    one the paper-desk request names, or the replay session's own. With nothing
    named there is nothing to contradict, so the origin's own claim is returned
    unread as a refusal — and ``None`` is returned only for a row written before
    the column existed, which is the one legacy shape the versionless contract
    keeps serving.
    """
    inference_strategy_version_id = metadata.get("strategy_version_id")
    origin_version: Optional[uuid.UUID] = None
    if isinstance(inference_strategy_version_id, str):
        try:
            # Parsed rather than compared as text: the id a row carries can be
            # spelled with or without dashes depending on how it was written, and
            # a spelling must not decide whether an account's version agreed.
            origin_version = uuid.UUID(inference_strategy_version_id)
        except ValueError:
            origin_version = None
    elif inference_strategy_version_id is not None:
        origin_version = None

    if origin_version is None and inference_strategy_version_id is not None:
        # The row names something in this position that is not a version. Reading
        # it as "no version" would put a false statement — this origin predates
        # the column — into the record, so it is refused for what it is.
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_version_unnamed",
                "inference_strategy_version_id": inference_strategy_version_id,
                "decision_version": str(strategy_version_id) if strategy_version_id else None,
                "message": (
                    "The linked inference names something that is not a readable "
                    "strategy version id, so the version its model was trained "
                    "from cannot be recorded or verified."
                ),
            },
        )

    if strategy_version_id is None:
        return origin_version

    if origin_version is None:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_version_unnamed",
                "inference_strategy_version_id": inference_strategy_version_id,
                "decision_version": str(strategy_version_id),
                "message": (
                    "The linked inference does not name the strategy version its "
                    "model was trained from, so it cannot be recorded as the "
                    "execution behind a decision that names one."
                ),
            },
        )
    if origin_version != strategy_version_id:
        raise HTTPException(
            status_code=400,
            detail={
                "reason": "inference_version_mismatch",
                "inference_strategy_version_id": str(origin_version),
                "decision_version": str(strategy_version_id),
                "message": (
                    "Linked inference was produced by a model trained from a "
                    "different strategy version than the one this decision names."
                ),
            },
        )
    return origin_version


def validate_linked_inference(
    db: Session,
    *,
    owner_id: UUID,
    inference_id: UUID,
    portfolio_id: UUID,
    strategy_version_id: Optional[UUID] = None,
) -> Optional[uuid.UUID]:
    """Refuse an inference this decision may not claim; else return its origin.

    Attribution first, version second, always in that order: a row that is not
    this owner's model execution for this account is refused for that reason
    even when its version would also have disagreed, because the door has no
    business reading a foreign row's claims in order to find a way to reject it.

    Returns the strategy version the origin names — which is what the paper desk
    records when the request did not name one itself.
    """
    metadata = check_inference_attribution(
        db, owner_id=owner_id, inference_id=inference_id, portfolio_id=portfolio_id
    )
    return check_inference_version(metadata, strategy_version_id=strategy_version_id)
