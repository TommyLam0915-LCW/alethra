"""The one gate that may say a trained model is switched on.

``AgentStrategyTrainingJob.activation_status`` used to be a literal written by
the trainer, so "NOT_APPROVED" was a string rather than a judgment: nothing on
disk could change it and nothing proved the column was being *read*. It is now
derived here from a recorded approval row.

Three things make an approval the owner's and nobody else's, and all three are
checked on every read rather than at write time:

* ``approved_by_user_id == owner_id`` — the recorder acted for themselves;
* ``channel == "owner_ui"`` — the authenticated route is the only writer that
  can set it, so a seed script or a CLI row stays non-qualifying forever;
* the approval binds the exact ``artifact_checksum``, ``dataset_checksum`` and
  ``training_contract_version`` of the job it was given for, so the next
  training run inherits nothing.

The default is ``NOT_APPROVED`` for every failure, including "no job", "no
approval", "approval older than the artifact" and "consent withdrawn". This
module never grants on its own: it only reports what the recorded facts say.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_strategy import (
    AgentModelActivationApproval,
    AgentStrategyConsent,
    AgentStrategyTrainingJob,
)

ACTIVATED = "APPROVED"
NOT_APPROVED = "NOT_APPROVED"

CHANNEL_OWNER_UI = "owner_ui"
QUALIFYING_CHANNELS = (CHANNEL_OWNER_UI,)

ACTIVATION_POLICY_VERSION = "agent-model-activation-v1"
ACTIVATION_ACK_TEXT_VERSION = "activation-ack-zh-1"
ACTIVATION_ACK_TEXT_ZH = (
    "我已讀過該模型的訓練契約、資料快照雜湊與指標，並同意讓這一個產物"
    "（artifact checksum 相符者）在我的帳戶內作為 take／skip 輔助啟用。"
    "我理解此同意不構成自動實盤、不構成對其他產物的授權，且隨時可以撤回。"
)

PURPOSE = "agent_strategy_screening"


def _refused(code: str, message_zh: str, details: Optional[dict] = None):
    # Late import: agent_strategy owns the refusal type and imports this module
    # for its own activation report.
    from app.services.agent_strategy import AgentStrategyRefused

    raise AgentStrategyRefused(code, message_zh, details)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_ack(text: str) -> str:
    return "".join((text or "").split())


def owned_job(db: Session, owner_id: uuid.UUID, job_id: uuid.UUID
              ) -> AgentStrategyTrainingJob:
    job = db.scalars(
        select(AgentStrategyTrainingJob).where(
            AgentStrategyTrainingJob.id == job_id,
            AgentStrategyTrainingJob.owner_id == owner_id,
        )
    ).first()
    if job is None:
        _refused("agent_activation_job_not_found",
                 "找不到屬於此 owner 的訓練紀錄。", {"job_id": str(job_id)})
    return job


def live_approval(db: Session, job_id: uuid.UUID
                  ) -> Optional[AgentModelActivationApproval]:
    return db.scalars(
        select(AgentModelActivationApproval)
        .where(
            AgentModelActivationApproval.job_id == job_id,
            AgentModelActivationApproval.decision == "approved",
        )
        .order_by(AgentModelActivationApproval.approved_at.desc())
    ).first()


def approval_for_job(db: Session, owner_id: uuid.UUID, job_id: uuid.UUID
                     ) -> AgentModelActivationApproval:
    approval = live_approval(db, job_id)
    if approval is None or str(approval.owner_id) != str(owner_id):
        _refused("agent_activation_approval_not_found",
                 "此 owner 對該產物沒有生效中的啟用同意。",
                 {"job_id": str(job_id)})
    return approval


def approval_covers_skill_unit(
    approval: Optional[AgentModelActivationApproval],
    skill_unit_id: Optional[uuid.UUID],
) -> bool:
    """Check if an approval covers a specific skill unit.

    Returns True only if:
    - approval exists AND
    - (approval.skill_unit_id matches skill_unit_id OR both are None)

    An approval with NULL skill_unit_id does NOT cover any skill unit — it only
    authorizes the artifact in isolation. An approval with a specific
    skill_unit_id covers only that skill unit. This prevents old NULL-scope
    approvals from being misinterpreted as "all skills" authorization.
    """
    if approval is None:
        return False
    if approval.skill_unit_id is None and skill_unit_id is None:
        # Both NULL: approval covers artifact in isolation, no skill-unit scope.
        # This is NOT "all skills" — it's "no skill unit specified".
        return True
    if approval.skill_unit_id is None or skill_unit_id is None:
        # One is NULL, the other is not: no match.
        return False
    # Both non-NULL: must match exactly.
    return str(approval.skill_unit_id) == str(skill_unit_id)


def check_skill_unit_activation(
    db: Session,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    skill_unit_id: Optional[uuid.UUID],
) -> Dict[str, Any]:
    """Verify that an activation approval covers the specified skill unit.

    Returns a dict with:
    - authorized: bool
    - reason_code: str (if not authorized)
    - approval_id: str (if exists)
    - skill_unit_match: bool

    This is the inference-time gate: even if a job is activated, the activation
    must cover the specific skill unit being used. An approval with NULL
    skill_unit_id does NOT authorize all skill units.
    """
    approval = live_approval(db, job_id)
    if approval is None:
        return {
            "authorized": False,
            "reason_code": "no_approval",
            "approval_id": None,
            "skill_unit_match": False,
        }
    if str(approval.owner_id) != str(owner_id):
        return {
            "authorized": False,
            "reason_code": "approval_not_owned",
            "approval_id": str(approval.id),
            "skill_unit_match": False,
        }
    covers = approval_covers_skill_unit(approval, skill_unit_id)
    return {
        "authorized": covers,
        "reason_code": None if covers else "skill_unit_mismatch",
        "approval_id": str(approval.id),
        "skill_unit_match": covers,
        "approval_skill_unit_id": str(approval.skill_unit_id) if approval.skill_unit_id else None,
        "requested_skill_unit_id": str(skill_unit_id) if skill_unit_id else None,
    }


def check_model_version_inference_authorization(
    db: Session,
    owner_id: uuid.UUID,
    model_version_id: uuid.UUID,
    model_spec: dict,
    skill_unit_id: uuid.UUID,
) -> Dict[str, Any]:
    """Verify that a personal model version is authorized for inference.

    Personal model authorization chain (NOT agent strategy):
    1. ModelVersion has training_job_id in its spec
    2. PersonalModelSelection links skill_unit_id -> this version, as the latest
       selection that skill unit made for this model record
    3. TrainingJob (general table) exists, is owned by the caller, and completed
    4. Artifact checksum matches (if present in both)

    This function verifies the SPECIFIC skill_unit_id provided in the request.
    The request must specify which skill unit is being used, and we verify that
    specific skill unit has a selection for this model version.

    Personal models do NOT require AgentModelActivationApproval — that is for
    agent strategy models only. Personal models rely on:
    - Owner's consent (checked at training time, not inference time)
    - Completed training job
    - Valid selection for the skill unit

    Returns a dict with:
    - authorized: bool
    - reason_code: str (if not authorized)
    - training_job_id: str (if present)
    - skill_unit_id: str (the specific skill unit being verified)
    """
    from app.models.model_research import ModelVersion
    from app.models.personal_model_selection import PersonalModelSelection
    from app.models.training import TrainingJob, TrainingJobStatus

    training_job_id_str = model_spec.get("training_job_id")
    artifact_sha256 = model_spec.get("artifact_sha256")

    if not training_job_id_str:
        return {
            "authorized": False,
            "reason_code": "no_training_job",
            "training_job_id": None,
            "skill_unit_id": str(skill_unit_id),
        }

    try:
        training_job_id = uuid.UUID(training_job_id_str)
    except (ValueError, AttributeError):
        return {
            "authorized": False,
            "reason_code": "invalid_training_job_id",
            "training_job_id": training_job_id_str,
            "skill_unit_id": str(skill_unit_id),
        }

    # The version must be the one this skill unit is currently using, not merely
    # one it used once. Selecting a newer version for the same skill is how the
    # owner stops the older one, so a stale row cannot keep authorizing inference.
    version_row = (
        db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    )
    if version_row is None:
        return {
            "authorized": False,
            "reason_code": "version_not_found",
            "training_job_id": str(training_job_id),
            "skill_unit_id": str(skill_unit_id),
        }

    selection = (
        db.query(PersonalModelSelection)
        .filter(
            PersonalModelSelection.owner_id == owner_id,
            PersonalModelSelection.record_id == version_row.model_research_record_id,
            PersonalModelSelection.skill_unit_id == skill_unit_id,
        )
        .order_by(
            PersonalModelSelection.created_at.desc(),
            PersonalModelSelection.id.desc(),
        )
        .first()
    )

    if selection is None:
        return {
            "authorized": False,
            "reason_code": "no_skill_unit_selection",
            "training_job_id": str(training_job_id),
            "skill_unit_id": str(skill_unit_id),
        }

    if selection.version_id != model_version_id:
        return {
            "authorized": False,
            "reason_code": "superseded_skill_unit_selection",
            "training_job_id": str(training_job_id),
            "skill_unit_id": str(skill_unit_id),
            "currently_selected_version_id": str(selection.version_id),
        }

    # Verify the training job exists, is completed, and belongs to this owner
    training_job = (
        db.query(TrainingJob)
        .filter(
            TrainingJob.id == training_job_id,
            TrainingJob.user_id == owner_id,
        )
        .first()
    )

    if training_job is None:
        return {
            "authorized": False,
            "reason_code": "training_job_not_found",
            "training_job_id": str(training_job_id),
            "skill_unit_id": str(skill_unit_id),
        }

    if training_job.status != TrainingJobStatus.completed:
        return {
            "authorized": False,
            "reason_code": "training_job_not_completed",
            "training_job_id": str(training_job_id),
            "skill_unit_id": str(skill_unit_id),
            "job_status": training_job.status.value if training_job.status else None,
        }

    # Verify artifact checksum matches (if present in training job results)
    if artifact_sha256 and training_job.results:
        job_artifact_sha256 = training_job.results.get("artifact_sha256")
        if job_artifact_sha256 and job_artifact_sha256 != artifact_sha256:
            return {
                "authorized": False,
                "reason_code": "artifact_mismatch",
                "training_job_id": str(training_job_id),
                "skill_unit_id": str(skill_unit_id),
            }

    return {
        "authorized": True,
        "reason_code": None,
        "training_job_id": str(training_job_id),
        "skill_unit_id": str(skill_unit_id),
    }


def activation_state(db: Session, job: Optional[AgentStrategyTrainingJob]
                     ) -> Dict[str, Any]:
    """Report the activation of one artifact, reading only recorded facts."""
    base = {
        "activation_status": NOT_APPROVED,
        "reason_code": "no_job",
        "approval_id": None,
        "bindings": {},
        # Release to a customer environment is a second judgment and this gate
        # does not make it; an approved model here is still local-trial only.
        "movable_to_customer_environment": False,
        "activation_policy_version": ACTIVATION_POLICY_VERSION,
    }
    if job is None:
        return base
    if job.status != "completed":
        return {**base, "reason_code": "job_not_completed",
                "job_id": str(job.id), "job_status": job.status}

    approval = live_approval(db, job.id)
    if approval is None:
        return {**base, "reason_code": "no_recorded_approval",
                "job_id": str(job.id), "bindings": {"approval_present": False}}

    consent = db.scalars(
        select(AgentStrategyConsent).where(
            AgentStrategyConsent.owner_id == job.owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "active",
        )
    ).first()

    conditions = {
        "channel_qualifies": approval.channel in QUALIFYING_CHANNELS,
        "self_owned": str(approval.approved_by_user_id) == str(job.owner_id),
        "owner_is_recorder_target": str(approval.owner_id) == str(job.owner_id),
        "artifact_bound": approval.artifact_checksum == job.artifact_checksum,
        "dataset_bound": approval.dataset_checksum == job.dataset_checksum,
        "contract_bound": (
            approval.training_contract_version == job.training_contract_version
        ),
        "approval_follows_artifact": (
            _as_utc(approval.approved_at) is not None
            and _as_utc(job.completed_at) is not None
            and _as_utc(approval.approved_at) >= _as_utc(job.completed_at)
        ),
        "consent_still_active": consent is not None,
        "policy_version_matches": (
            approval.approval_policy_version == ACTIVATION_POLICY_VERSION
        ),
    }

    for reason_code, key in (
        ("approval_channel_not_owner_ui", "channel_qualifies"),
        ("approval_not_self_owned", "self_owned"),
        ("approval_not_for_this_owner", "owner_is_recorder_target"),
        ("approval_artifact_mismatch", "artifact_bound"),
        ("approval_dataset_mismatch", "dataset_bound"),
        ("approval_contract_mismatch", "contract_bound"),
        ("approval_precedes_artifact", "approval_follows_artifact"),
        ("owner_consent_not_active", "consent_still_active"),
        ("approval_policy_version_mismatch", "policy_version_matches"),
    ):
        if not conditions[key]:
            return {**base, "reason_code": reason_code, "job_id": str(job.id),
                    "approval_id": str(approval.id), "bindings": conditions}

    return {
        **base,
        "activation_status": ACTIVATED,
        "reason_code": None,
        "job_id": str(job.id),
        "approval_id": str(approval.id),
        "bindings": conditions,
        "approved_at": _as_utc(approval.approved_at).isoformat(),
        "artifact_checksum": approval.artifact_checksum,
        # Unified skill model: report the skill-unit scope of this approval.
        # NULL means "artifact in isolation, no skill-unit scope" — NOT "all
        # skill units". Inference-time checks must verify the scope matches.
        "skill_unit_id": str(approval.skill_unit_id) if approval.skill_unit_id else None,
        "skill_unit_scope": "specific" if approval.skill_unit_id else "none",
    }


def activation_report(db: Session, job: Optional[AgentStrategyTrainingJob]
                      ) -> Dict[str, Any]:
    """The shape the capability and metadata blocks have always carried."""
    state = activation_state(db, job)
    return {
        "model_activation": state["activation_status"],
        "activation_available": state["activation_status"] == ACTIVATED,
        "activation_reason_code": state["reason_code"],
        "movable_to_customer_environment": state["movable_to_customer_environment"],
    }


def owner_activation_summary(db: Session, owner_id: uuid.UUID) -> Dict[str, Any]:
    """What the recorded approvals say about this owner right now.

    Used by reports that have an owner but no single artifact in scope. It is a
    count of facts on disk, not a permission: an owner with an approved job may
    still be refused a new training run, and vice versa.
    """
    jobs = db.scalars(
        select(AgentStrategyTrainingJob).where(
            AgentStrategyTrainingJob.owner_id == owner_id,
            AgentStrategyTrainingJob.status == "completed",
        )
    ).all()
    states = {str(job.id): activation_state(db, job)["activation_status"] for job in jobs}
    return {
        "model_activation": ACTIVATED if any(
            status == ACTIVATED for status in states.values()) else NOT_APPROVED,
        "completed_job_count": len(jobs),
        "approved_job_count": sum(1 for s in states.values() if s == ACTIVATED),
        "approved_job_ids": [jid for jid, s in states.items() if s == ACTIVATED],
    }


def requirements(job: AgentStrategyTrainingJob) -> Dict[str, Any]:
    """What the owner has to echo back for an approval to bind this artifact."""
    return {
        "job_id": str(job.id),
        "strategy_version_id": str(job.strategy_version_id),
        "artifact_checksum": job.artifact_checksum,
        "dataset_checksum": job.dataset_checksum,
        "training_contract_version": job.training_contract_version,
        "approval_policy_version": ACTIVATION_POLICY_VERSION,
        "acknowledgement_text_version": ACTIVATION_ACK_TEXT_VERSION,
        "acknowledgement_text_zh": ACTIVATION_ACK_TEXT_ZH,
        "consent_status": "active",
    }


def record_activation_approval(
    db: Session,
    owner_id: uuid.UUID,
    *,
    job_id: uuid.UUID,
    artifact_checksum: str,
    policy_version: str,
    acknowledgement_text_version: str,
    acknowledgement: str,
    skill_unit_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """Persist the authenticated owner's own decision to switch one artifact on.

    There is no ``approved_by_user_id`` parameter and no ``channel`` parameter:
    the route supplies the authenticated user and this function is the owner-UI
    path, so neither can be claimed on someone else's behalf or by another
    provenance.

    When ``skill_unit_id`` is provided, the approval is scoped to that portfolio
    (skill unit). An approval with NULL ``skill_unit_id`` does NOT authorize all
    skill units — it only covers the artifact in isolation, without any
    skill-unit scope. Inference-time checks must verify the approval covers the
    specific skill unit being used.
    """
    job = owned_job(db, owner_id, job_id)

    # Lock the job row to prevent concurrent approval creation.
    # This serializes approval attempts for the same job, ensuring the
    # "one active approval per job" policy holds under concurrency.
    job = db.scalars(
        select(AgentStrategyTrainingJob)
        .where(AgentStrategyTrainingJob.id == job_id)
        .with_for_update()
    ).first()

    if job.status != "completed":
        _refused("agent_activation_job_not_completed",
                 "只有已完成的訓練紀錄可以被啟用，未記錄任何同意。",
                 {"job_status": job.status})
    if not job.artifact_checksum:
        _refused("agent_activation_artifact_missing",
                 "該訓練紀錄沒有產物雜湊，無東西可綁定。", {})
    if artifact_checksum != job.artifact_checksum:
        _refused("agent_activation_artifact_mismatch",
                 "所附產物雜湊與訓練紀錄不符，未記錄任何同意。",
                 {"expected": job.artifact_checksum, "received": artifact_checksum})
    if policy_version != ACTIVATION_POLICY_VERSION:
        _refused("agent_activation_policy_version_mismatch",
                 "啟用政策版本不符，未記錄任何同意。",
                 {"expected": ACTIVATION_POLICY_VERSION, "received": policy_version})
    if acknowledgement_text_version != ACTIVATION_ACK_TEXT_VERSION:
        _refused("agent_activation_text_version_mismatch",
                 "確認文字版本不符，未記錄任何同意。",
                 {"expected": ACTIVATION_ACK_TEXT_VERSION,
                  "received": acknowledgement_text_version})
    if _normalise_ack(acknowledgement) != _normalise_ack(ACTIVATION_ACK_TEXT_ZH):
        _refused("agent_activation_acknowledgement_mismatch",
                 "確認文字與啟用政策全文不一致，未記錄任何同意。",
                 {"persisted": False})

    consent = db.scalars(
        select(AgentStrategyConsent).where(
            AgentStrategyConsent.owner_id == owner_id,
            AgentStrategyConsent.purpose == PURPOSE,
            AgentStrategyConsent.status == "active",
        )
    ).first()
    if consent is None:
        _refused("agent_activation_consent_not_active",
                 "此 owner 沒有有效的同意紀錄，不能啟用模型。", {})

    if live_approval(db, job.id) is not None:
        _refused("agent_activation_already_approved",
                 "此產物已有生效中的啟用同意；如需換成別份，先撤回。",
                 {"job_id": str(job.id)})

    approval = AgentModelActivationApproval(
        owner_id=owner_id,
        approved_by_user_id=owner_id,
        job_id=job.id,
        strategy_version_id=job.strategy_version_id,
        artifact_checksum=job.artifact_checksum,
        dataset_checksum=job.dataset_checksum,
        training_contract_version=job.training_contract_version,
        consent_record_id=consent.id,
        approval_policy_version=ACTIVATION_POLICY_VERSION,
        acknowledgement_text_version=ACTIVATION_ACK_TEXT_VERSION,
        acknowledgement=ACTIVATION_ACK_TEXT_ZH,
        channel=CHANNEL_OWNER_UI,
        decision="approved",
        skill_unit_id=skill_unit_id,
        gate_snapshot_json={**requirements(job), "recorded_through": __name__,
                           "skill_unit_id": str(skill_unit_id) if skill_unit_id else None},
    )
    db.add(approval)
    db.flush()
    state = activation_state(db, job)
    job.activation_status = state["activation_status"]
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(approval)
    return {"approval_id": str(approval.id), **state}


def revoke_activation_approval(
    db: Session, owner_id: uuid.UUID, *, approval_id: uuid.UUID, reason: Optional[str] = None
) -> Dict[str, Any]:
    approval = db.scalars(
        select(AgentModelActivationApproval)
        .where(
            AgentModelActivationApproval.id == approval_id,
            AgentModelActivationApproval.owner_id == owner_id,
        )
        .with_for_update()
    ).first()
    if approval is None:
        _refused("agent_activation_approval_not_found",
                 "找不到屬於此 owner 的啟用同意。", {"approval_id": str(approval_id)})
    if approval.decision == "revoked":
        # Already revoked, idempotent return
        job = db.scalars(
            select(AgentStrategyTrainingJob).where(
                AgentStrategyTrainingJob.id == approval.job_id
            )
        ).first()
        return {"revoked": True, "approval_id": str(approval.id),
                "activation_status": job.activation_status if job else NOT_APPROVED,
                "already_revoked": True}
    approval.decision = "revoked"
    approval.revoked_at = datetime.now(timezone.utc)
    approval.revoke_reason = (reason or "owner_revoked")[:64]
    job = db.scalars(
        select(AgentStrategyTrainingJob).where(
            AgentStrategyTrainingJob.id == approval.job_id
        )
    ).first()
    db.flush()
    if job is not None:
        job.activation_status = activation_state(db, job)["activation_status"]
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"revoked": True, "approval_id": str(approval.id),
            "activation_status": job.activation_status if job else NOT_APPROVED}


def refresh_job_activation(db: Session, job: AgentStrategyTrainingJob) -> Dict[str, Any]:
    """Re-read the gate and mirror it onto the job column."""
    state = activation_state(db, job)
    if job.activation_status != state["activation_status"]:
        job.activation_status = state["activation_status"]
        db.commit()
        db.refresh(job)
    return state


def list_activations_by_skill_unit(
    db: Session,
    owner_id: uuid.UUID,
    skill_unit_id: Optional[uuid.UUID],
) -> List[Dict[str, Any]]:
    """List all activation approvals for a specific skill unit.

    Returns approvals where skill_unit_id matches exactly. An approval with NULL
    skill_unit_id is NOT included when querying for a specific skill unit, and
    vice versa — NULL-scope approvals only appear when querying with
    skill_unit_id=None.

    This prevents old NULL-scope approvals from being misinterpreted as "all
    skills" authorization. Each approval covers exactly the skill unit it was
    recorded for (or no skill unit at all, if NULL).
    """
    if skill_unit_id is None:
        # Query for approvals with NULL skill_unit_id (artifact-only scope).
        approvals = db.scalars(
            select(AgentModelActivationApproval).where(
                AgentModelActivationApproval.owner_id == owner_id,
                AgentModelActivationApproval.decision == "approved",
                AgentModelActivationApproval.skill_unit_id.is_(None),
            ).order_by(AgentModelActivationApproval.approved_at.desc())
        ).all()
    else:
        # Query for approvals scoped to this specific skill unit.
        approvals = db.scalars(
            select(AgentModelActivationApproval).where(
                AgentModelActivationApproval.owner_id == owner_id,
                AgentModelActivationApproval.decision == "approved",
                AgentModelActivationApproval.skill_unit_id == skill_unit_id,
            ).order_by(AgentModelActivationApproval.approved_at.desc())
        ).all()

    return [
        {
            "approval_id": str(a.id),
            "job_id": str(a.job_id),
            "skill_unit_id": str(a.skill_unit_id) if a.skill_unit_id else None,
            "artifact_checksum": a.artifact_checksum,
            "approved_at": _as_utc(a.approved_at).isoformat() if a.approved_at else None,
            "activation_status": ACTIVATED,
        }
        for a in approvals
    ]


def revoke_activations_for_skill_unit(
    db: Session,
    owner_id: uuid.UUID,
    skill_unit_id: uuid.UUID,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Revoke all activation approvals scoped to a specific skill unit.

    This is a precise-scope revocation: only approvals with skill_unit_id
    matching the parameter are revoked. Approvals with NULL skill_unit_id or
    with a different skill_unit_id are not affected.

    Returns the count of revoked approvals and their IDs.
    """
    approvals = db.scalars(
        select(AgentModelActivationApproval).where(
            AgentModelActivationApproval.owner_id == owner_id,
            AgentModelActivationApproval.decision == "approved",
            AgentModelActivationApproval.skill_unit_id == skill_unit_id,
        )
    ).all()

    revoked_ids = []
    affected_job_ids = set()
    for approval in approvals:
        approval.decision = "revoked"
        approval.revoked_at = datetime.now(timezone.utc)
        approval.revoke_reason = (reason or "skill_unit_revoked")[:64]
        revoked_ids.append(str(approval.id))
        affected_job_ids.add(approval.job_id)

    if not revoked_ids:
        return {
            "revoked": False,
            "reason_code": "no_matching_approvals",
            "message_zh": "此技能單元沒有生效中的啟用同意可撤回。",
            "skill_unit_id": str(skill_unit_id),
            "revoked_count": 0,
            "revoked_approval_ids": [],
        }

    # Refresh activation status for all affected jobs.
    for job_id in affected_job_ids:
        job = db.scalars(
            select(AgentStrategyTrainingJob).where(
                AgentStrategyTrainingJob.id == job_id
            )
        ).first()
        if job is not None:
            job.activation_status = activation_state(db, job)["activation_status"]

    db.flush()
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "revoked": True,
        "skill_unit_id": str(skill_unit_id),
        "revoked_count": len(revoked_ids),
        "revoked_approval_ids": revoked_ids,
    }
