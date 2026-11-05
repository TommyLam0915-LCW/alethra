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
from typing import Any, Dict, Optional

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
) -> Dict[str, Any]:
    """Persist the authenticated owner's own decision to switch one artifact on.

    There is no ``approved_by_user_id`` parameter and no ``channel`` parameter:
    the route supplies the authenticated user and this function is the owner-UI
    path, so neither can be claimed on someone else's behalf or by another
    provenance.
    """
    job = owned_job(db, owner_id, job_id)
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
        gate_snapshot_json={**requirements(job), "recorded_through": __name__},
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
        select(AgentModelActivationApproval).where(
            AgentModelActivationApproval.id == approval_id,
            AgentModelActivationApproval.owner_id == owner_id,
        )
    ).first()
    if approval is None:
        _refused("agent_activation_approval_not_found",
                 "找不到屬於此 owner 的啟用同意。", {"approval_id": str(approval_id)})
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
