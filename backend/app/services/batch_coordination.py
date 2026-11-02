"""Minimal write-batch coordination for delegated customer operations.

One customer batch has exactly one designated executor. The registry answers
four operational questions without a scheduler:

* may this executor claim the batch, or is it already claimed elsewhere?
* did another executor already complete it (read back the result, do not
  resend the writes)?
* is any batch still in flight, so a dev-line deployment must wait?
* what was read back before acting, and what caps were actually consumed?

Nothing here retries a write, and a completed result is never overwritten.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.models.operation_batch import BATCH_STATUSES, OperationBatch

CLAIMED = "claimed"
COMPLETED = "completed"
ABANDONED = "abandoned"


class BatchConflict(Exception):
    """A coordination request the contract refuses (not a server error)."""

    def __init__(
        self, code: str, message_zh: str, details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _normalize_executor(executor: str) -> str:
    normalized = (executor or "").strip()
    if not normalized:
        raise BatchConflict(
            "executor_required", "每個批次必須指定一個具名的寫入執行者。"
        )
    if len(normalized) > 160:
        raise BatchConflict("executor_too_long", "執行者名稱過長。")
    return normalized


def _normalize_batch_key(batch_key: str) -> str:
    normalized = (batch_key or "").strip()
    if not normalized:
        raise BatchConflict("batch_key_required", "批次必須有穩定的 batch_key。")
    if len(normalized) > 120:
        raise BatchConflict("batch_key_too_long", "batch_key 過長。")
    return normalized


def batch_payload(batch: OperationBatch) -> Dict[str, Any]:
    return {
        "id": str(batch.id),
        "owner_id": str(batch.owner_id),
        "batch_key": batch.batch_key,
        "executor": batch.executor,
        "status": batch.status,
        "intended_operations": batch.intended_operations,
        "caps": batch.caps,
        "precheck": batch.precheck,
        "claimed_at": _iso(batch.claimed_at),
        "completed_at": _iso(batch.completed_at),
        "abandoned_at": _iso(batch.abandoned_at),
        "abandon_reason": batch.abandon_reason,
        "result": batch.result_json,
    }


def _find(
    db: Session, owner_id: uuid.UUID, batch_key: str
) -> Optional[OperationBatch]:
    return (
        db.query(OperationBatch)
        .filter(
            OperationBatch.owner_id == owner_id,
            OperationBatch.batch_key == batch_key,
        )
        .one_or_none()
    )


def claim_batch(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_key: str,
    executor: str,
    intended_operations: Optional[Dict[str, Any]] = None,
    caps: Optional[Dict[str, Any]] = None,
    precheck: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Claim the batch for one executor, or report why it cannot be claimed.

    Idempotent for the same executor: claiming twice returns the existing
    claim. A different executor gets ``batch_in_progress``; a completed batch
    returns its stored result instead of running the writes again.
    """
    key = _normalize_batch_key(batch_key)
    who = _normalize_executor(executor)

    for _attempt in range(2):
        existing = _find(db, owner_id, key)
        if existing is not None:
            return _claim_outcome(existing, who)

        batch = OperationBatch(
            owner_id=owner_id,
            batch_key=key,
            executor=who,
            status=CLAIMED,
            intended_operations=intended_operations or {},
            caps=caps or {},
            precheck=precheck or {},
        )
        db.add(batch)
        try:
            db.commit()
        except IntegrityError:
            # Another executor inserted the same (owner, key) between the read
            # and this insert. Re-read and report; never retry the insert with
            # a different identity.
            db.rollback()
            continue
        db.refresh(batch)
        payload = batch_payload(batch)
        payload["outcome"] = "claimed"
        return payload

    existing = _find(db, owner_id, key)
    if existing is None:
        raise BatchConflict(
            "batch_claim_unresolved",
            "批次搶佔時發生衝突，但讀不回既有記錄；請先查詢再決定。",
        )
    return _claim_outcome(existing, who)


def _claim_outcome(batch: OperationBatch, executor: str) -> Dict[str, Any]:
    payload = batch_payload(batch)
    if batch.status == COMPLETED:
        payload["outcome"] = "already_completed"
        return payload
    if batch.status == ABANDONED:
        raise BatchConflict(
            "batch_abandoned",
            "此批次已被放棄；請用新的 batch_key 重建，唔要重送舊批次。",
            {"batch": payload},
        )
    if batch.executor != executor:
        raise BatchConflict(
            "batch_in_progress",
            "此批次已由另一個執行者持有；請查詢該批次，唔要平行重做。",
            {"batch": payload},
        )
    payload["outcome"] = "already_claimed"
    return payload


def complete_batch(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_key: str,
    executor: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """Record read-back evidence and close the batch.

    ``result`` must name the objects that exist after the writes and the caps
    actually consumed. A second completion never overwrites the first: it
    returns ``already_completed`` with the original result.
    """
    key = _normalize_batch_key(batch_key)
    who = _normalize_executor(executor)
    if not isinstance(result, dict):
        raise BatchConflict("result_required", "完成批次必須提供回讀結果。")
    objects = result.get("objects")
    if not isinstance(objects, list):
        raise BatchConflict(
            "result_objects_required",
            "完成前必須回讀實際存在的物件（objects 清單）。",
        )
    caps_consumed = result.get("caps_consumed")
    if not isinstance(caps_consumed, dict):
        raise BatchConflict(
            "result_caps_required",
            "完成前必須回讀實際消耗的額度（caps_consumed）。",
        )

    batch = _find(db, owner_id, key)
    if batch is None:
        raise BatchConflict("batch_not_claimed", "此批次從未被任何執行者持有。")
    if batch.status == COMPLETED:
        payload = batch_payload(batch)
        payload["outcome"] = "already_completed"
        return payload
    if batch.status == ABANDONED:
        raise BatchConflict(
            "batch_abandoned",
            "此批次已被放棄，唔可以用它記錄完成。",
            {"batch": batch_payload(batch)},
        )
    if batch.executor != who:
        raise BatchConflict(
            "batch_claimed_by_other_executor",
            "此批次由另一個執行者持有，只有該執行者可以完成它。",
            {"batch": batch_payload(batch)},
        )

    now = utcnow()
    updated = (
        db.query(OperationBatch)
        .filter(
            OperationBatch.id == batch.id,
            OperationBatch.status == CLAIMED,
            OperationBatch.executor == who,
        )
        .update(
            {
                OperationBatch.status: COMPLETED,
                OperationBatch.completed_at: now,
                OperationBatch.result_json: {
                    "objects": objects,
                    "caps_consumed": caps_consumed,
                    "notes": result.get("notes"),
                },
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 0:
        current = _find(db, owner_id, key)
        if current is not None and current.status == COMPLETED:
            payload = batch_payload(current)
            payload["outcome"] = "already_completed"
            return payload
        raise BatchConflict(
            "batch_completion_raced",
            "完成時批次狀態已改變；請重新查詢，唔要重送寫入。",
        )
    db.refresh(batch)
    payload = batch_payload(batch)
    payload["outcome"] = "completed"
    return payload


def abandon_batch(
    db: Session,
    *,
    owner_id: uuid.UUID,
    batch_key: str,
    executor: str,
    reason: str,
) -> Dict[str, Any]:
    """Release a claim that will never complete, with a stated reason."""
    key = _normalize_batch_key(batch_key)
    who = _normalize_executor(executor)
    normalized_reason = (reason or "").strip()
    if not normalized_reason:
        raise BatchConflict("abandon_reason_required", "放棄批次必須註明原因。")

    batch = _find(db, owner_id, key)
    if batch is None:
        raise BatchConflict("batch_not_claimed", "此批次從未被任何執行者持有。")
    if batch.status == COMPLETED:
        payload = batch_payload(batch)
        payload["outcome"] = "already_completed"
        return payload
    if batch.status == ABANDONED:
        payload = batch_payload(batch)
        payload["outcome"] = "already_abandoned"
        return payload
    if batch.executor != who:
        raise BatchConflict(
            "batch_claimed_by_other_executor",
            "只有持有此批次的執行者可以放棄它。",
            {"batch": batch_payload(batch)},
        )

    now = utcnow()
    updated = (
        db.query(OperationBatch)
        .filter(
            OperationBatch.id == batch.id,
            OperationBatch.status == CLAIMED,
            OperationBatch.executor == who,
        )
        .update(
            {
                OperationBatch.status: ABANDONED,
                OperationBatch.abandoned_at: now,
                OperationBatch.abandon_reason: normalized_reason,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 0:
        current = _find(db, owner_id, key)
        if current is not None and current.status == ABANDONED:
            payload = batch_payload(current)
            payload["outcome"] = "already_abandoned"
            return payload
        if current is not None and current.status == COMPLETED:
            payload = batch_payload(current)
            payload["outcome"] = "already_completed"
            return payload
        raise BatchConflict(
            "batch_abandon_raced",
            "放棄時批次狀態已改變；請重新查詢。",
        )
    db.refresh(batch)
    payload = batch_payload(batch)
    payload["outcome"] = "abandoned"
    return payload


def batch_status(
    db: Session, *, owner_id: uuid.UUID, batch_key: str
) -> Optional[Dict[str, Any]]:
    batch = _find(db, owner_id, _normalize_batch_key(batch_key))
    return batch_payload(batch) if batch else None


def deployment_precheck(db: Session) -> Dict[str, Any]:
    """Refuse a dev-line deployment while any batch is still claimed.

    A database migrated before this registry existed has no table, so no batch
    can be in flight; that case is reported explicitly instead of failing the
    deployment or pretending the table was checked.
    """
    from sqlalchemy import inspect

    checked_at = utcnow().isoformat()
    if not inspect(db.get_bind()).has_table(OperationBatch.__tablename__):
        return {
            "safe_to_deploy": True,
            "registry_present": False,
            "in_progress_count": 0,
            "counts": {status: 0 for status in BATCH_STATUSES},
            "in_progress": [],
            "checked_at": checked_at,
            "note_zh": (
                "此資料庫尚未套用批次登記表的 migration，因此不可能有已登記的批次；"
                "先套用 migration，再以同一指令覆核。"
            ),
        }

    batches: List[OperationBatch] = (
        db.query(OperationBatch).order_by(OperationBatch.claimed_at).all()
    )
    in_progress = [batch for batch in batches if batch.status == CLAIMED]
    counts = {status: 0 for status in BATCH_STATUSES}
    for batch in batches:
        counts[batch.status] = counts.get(batch.status, 0) + 1
    return {
        "safe_to_deploy": len(in_progress) == 0,
        "registry_present": True,
        "in_progress_count": len(in_progress),
        "counts": counts,
        "in_progress": [batch_payload(batch) for batch in in_progress],
        "checked_at": checked_at,
        "note_zh": (
            "只檢查是否有批次仍處於 claimed。completed／abandoned 不阻塞部署，"
            "但會保留在記錄中供回讀。"
        ),
    }
