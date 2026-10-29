"""Safe selection and rollback of a personal model version.

``ModelResearchRecord.active_version_id`` may only change through this module.
Every rule is re-read from the database at operation time — a stored comparison
id is evidence, never an authorisation, so an old comparison cannot revive a
revoked version, a missing artifact or a strategy outside the current consent
scope.

The record row is locked for the whole decision, and the caller states which
active version it believes is current. A mismatch is refused instead of
silently overwriting a change another operation already made. The alias change
and the append-only selection history are written in the caller's transaction.
"""

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.ml.personal_trainer import PERSONAL_TASK_TYPE
from app.models.model_research import (
    ModelResearchRecord,
    ModelStatus,
    ModelType,
    ModelVersion,
)
from app.models.personal_model_selection import (
    PersonalModelComparisonRun,
    PersonalModelSelection,
    PersonalModelSelectionAction,
)
from app.services.model_comparison import ComparisonRefused, _artifact_digest
from app.services.personalization import _scope_strategy_ids, get_active_consent


class SelectionRefused(ValueError):
    """A selection the product must refuse (not a server error)."""

    def __init__(
        self,
        code: str,
        message_zh: str,
        details: Optional[dict] = None,
        *,
        not_found: bool = False,
    ):
        super().__init__(code)
        self.code = code
        self.message_zh = message_zh
        self.details = details or {}
        self.not_found = not_found


def _not_found(code: str, message_zh: str) -> SelectionRefused:
    # A foreign owner's record and a missing record answer the same way.
    return SelectionRefused(code, message_zh, not_found=True)


def _locked_record(
    db: Session, owner_id: uuid.UUID, record_id: uuid.UUID
) -> ModelResearchRecord:
    record = (
        db.query(ModelResearchRecord)
        .filter(ModelResearchRecord.id == record_id)
        .with_for_update()
        .first()
    )
    if record is None or record.owner_id != owner_id:
        raise _not_found("record_not_found", "找不到此帳號擁有的模型記錄。")
    return record


def _locked_version(
    db: Session, owner_id: uuid.UUID, version_id: uuid.UUID
) -> ModelVersion:
    version = (
        db.query(ModelVersion)
        .filter(ModelVersion.id == version_id)
        .with_for_update()
        .first()
    )
    if version is None:
        raise _not_found("version_not_found", "找不到此模型版本。")
    return version


def _peek_version(db: Session, version_id: uuid.UUID) -> ModelVersion:
    version = db.query(ModelVersion).filter(ModelVersion.id == version_id).first()
    if version is None:
        raise _not_found("version_not_found", "找不到此模型版本。")
    return version


def _assert_expected_active(
    record: ModelResearchRecord,
    expected: Optional[uuid.UUID],
    enforce: bool,
) -> None:
    if not enforce:
        return
    current = record.active_version_id
    if current == expected:
        return
    raise SelectionRefused(
        "stale_active_version",
        "使用中版本已被另一操作改變，未套用本次切換；請重新載入後再試。",
        {
            "expected_active_version_id": str(expected) if expected else None,
            "actual_active_version_id": str(current) if current else None,
        },
    )


def _assert_eligible(
    db: Session,
    owner_id: uuid.UUID,
    record: ModelResearchRecord,
    version: ModelVersion,
) -> dict:
    """Re-check every rule that makes a version usable *now*."""
    spec = version.model_specification_json or {}
    if version.model_research_record_id != record.id:
        raise SelectionRefused(
            "version_not_in_record", "此版本不屬於指定的模型記錄。"
        )
    if (
        version.model_type is not ModelType.personal_decision_model
        or spec.get("task_type") != PERSONAL_TASK_TYPE
    ):
        raise SelectionRefused(
            "not_a_personal_decision_model",
            "此版本不是以個人決策訓練的模型，不能設為使用中版本。",
        )
    if version.revoked_at is not None:
        raise SelectionRefused(
            "model_version_revoked",
            "此模型版本已撤銷，不能設為使用中版本。",
        )
    if version.retired_at is not None:
        raise SelectionRefused(
            "model_version_retired",
            "此模型版本已退役，不能設為使用中版本。",
        )
    if record.status in (ModelStatus.revoked, ModelStatus.retired):
        raise SelectionRefused(
            "record_not_active",
            "此模型記錄已撤銷或退役，不能切換使用中版本。",
            {"record_status": record.status.value},
        )

    artifact_id = spec.get("artifact_id")
    if not artifact_id:
        raise SelectionRefused(
            "artifact_missing", "此版本沒有可用的模型 artifact。"
        )
    try:
        digest = _artifact_digest(artifact_id)
    except ComparisonRefused as exc:
        raise SelectionRefused(exc.code, exc.message_zh, exc.details)
    if not spec.get("artifact_sha256") or digest != spec["artifact_sha256"]:
        raise SelectionRefused(
            "artifact_checksum_mismatch",
            "模型 artifact 內容與版本記錄的 hash 不一致，不能設為使用中版本。",
            {"recorded": spec.get("artifact_sha256"), "actual": digest},
        )

    raw_strategy = spec.get("strategy_version_id")
    if not raw_strategy:
        raise SelectionRefused(
            "strategy_scope_missing",
            "此版本沒有記錄所屬策略版本，無法核對同意範圍。",
        )
    try:
        strategy_version_id = uuid.UUID(str(raw_strategy))
    except (ValueError, TypeError):
        raise SelectionRefused(
            "strategy_scope_missing", "此版本的策略版本識別碼無效。"
        )
    consent = get_active_consent(db, owner_id)
    if consent is None:
        raise SelectionRefused(
            "consent_not_active",
            "目前沒有有效的個人化訓練同意，不能切換使用中版本。",
        )
    if strategy_version_id not in set(_scope_strategy_ids(consent)):
        raise SelectionRefused(
            "consent_scope_mismatch",
            "此策略版本不在目前同意的範圍內，不能切換使用中版本。",
            {"strategy_version_id": str(strategy_version_id)},
        )
    return spec


def _assert_comparison_context(
    db: Session,
    owner_id: uuid.UUID,
    record: ModelResearchRecord,
    version: ModelVersion,
    comparison_run_id: Optional[uuid.UUID],
) -> Optional[PersonalModelComparisonRun]:
    if comparison_run_id is None:
        return None
    run = (
        db.query(PersonalModelComparisonRun)
        .filter(
            PersonalModelComparisonRun.id == comparison_run_id,
            PersonalModelComparisonRun.owner_id == owner_id,
        )
        .first()
    )
    if run is None:
        raise _not_found("comparison_not_found", "找不到此比較紀錄。")
    if run.record_id != record.id:
        raise SelectionRefused(
            "comparison_record_mismatch", "此比較紀錄不屬於指定的模型記錄。"
        )
    if version.id not in (run.version_a_id, run.version_b_id):
        raise SelectionRefused(
            "version_not_in_comparison", "此版本不屬於所引用的比較紀錄。"
        )
    return run


def _comparison_evidence(run: Optional[PersonalModelComparisonRun]) -> Optional[dict]:
    """What a stored comparison may be cited for — never a quality pass."""
    if run is None:
        return None
    stored = run.result_json or {}
    return {
        "comparison_run_id": str(run.id),
        "mode": run.mode,
        "evaluation_purpose": stored.get("evaluation_purpose"),
        "evaluation_purpose_zh": stored.get("evaluation_purpose_zh"),
        "rankable": bool(stored.get("rankable")),
        "independent_out_of_sample": bool(stored.get("independent_out_of_sample")),
        "summary_zh": stored.get("summary_zh"),
        "warnings": stored.get("warnings") or [],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "note_zh": (
            "比較紀錄只說明當時採用的評估用途與限制，"
            "唔代表此版本通過品質審查或適合實際交易。"
        ),
    }


def select_version(
    db: Session,
    owner_id: uuid.UUID,
    *,
    version_id: uuid.UUID,
    rationale: str,
    comparison_run_id: Optional[uuid.UUID],
    expected_active_version_id: Optional[uuid.UUID],
    enforce_expected: bool = True,
) -> Dict[str, Any]:
    peek = _peek_version(db, version_id)
    # Lock order is always record then version, so select and rollback cannot
    # deadlock each other on the same record.
    record = _locked_record(db, owner_id, peek.model_research_record_id)
    version = _locked_version(db, owner_id, version_id)
    _assert_expected_active(record, expected_active_version_id, enforce_expected)
    _assert_eligible(db, owner_id, record, version)
    run = _assert_comparison_context(
        db, owner_id, record, version, comparison_run_id
    )

    if record.active_version_id == version.id:
        return {
            "already_active": True,
            "record": record,
            "version": version,
            "selection": None,
            "previous_version_id": None,
            "comparison_evidence": _comparison_evidence(run),
        }

    previous = record.active_version_id
    record.active_version_id = version.id
    selection = PersonalModelSelection(
        owner_id=owner_id,
        record_id=record.id,
        version_id=version.id,
        previous_version_id=previous,
        comparison_run_id=comparison_run_id,
        action=PersonalModelSelectionAction.select,
        rationale=rationale,
    )
    db.add(selection)
    db.flush()
    return {
        "already_active": False,
        "record": record,
        "version": version,
        "selection": selection,
        "previous_version_id": previous,
        "comparison_evidence": _comparison_evidence(run),
    }


def rollback(
    db: Session,
    owner_id: uuid.UUID,
    *,
    record_id: uuid.UUID,
    rationale: Optional[str],
    expected_active_version_id: Optional[uuid.UUID],
) -> Dict[str, Any]:
    from app.services.model_comparison import latest_selection

    record = _locked_record(db, owner_id, record_id)
    _assert_expected_active(record, expected_active_version_id, True)

    latest = latest_selection(db, owner_id, record.id)
    if latest is None or latest.previous_version_id is None:
        raise SelectionRefused(
            "no_previous_version",
            "沒有可回退的版本；此模型尚未有切換紀錄。",
        )

    target = _locked_version(db, owner_id, latest.previous_version_id)
    _assert_eligible(db, owner_id, record, target)

    current = record.active_version_id
    if current == target.id:
        raise SelectionRefused(
            "already_at_previous_version", "目前已在此版本，沒有改動。"
        )

    record.active_version_id = target.id
    selection = PersonalModelSelection(
        owner_id=owner_id,
        record_id=record.id,
        version_id=target.id,
        previous_version_id=current,
        comparison_run_id=None,
        action=PersonalModelSelectionAction.rollback,
        rationale=rationale or None,
    )
    db.add(selection)
    db.flush()
    return {
        "record": record,
        "version": target,
        "selection": selection,
        "previous_version_id": current,
    }
