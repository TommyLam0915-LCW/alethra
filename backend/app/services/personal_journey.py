"""Read-only journey for the personal decision-model loop.

One coherent answer to five questions, derived from the same server state the
product acts on:

    研究緊甚麼 -> 做過甚麼 -> 得到甚麼 -> 仲缺甚麼 -> 下一步做甚麼

The missing-data section speaks in counts of usable decisions and action
classes, not only eligibility codes. The worker section reports liveness,
pending/running jobs, the reason a job is waiting, failures and automatic
recovery (stale claim reclaim) so a queued job never looks silently stuck.

Nothing here writes, charges quota or fabricates progress.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.lib.timeutil import utcnow
from app.models.decision_label import DecisionLabelState
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import HumanDecisionEvent
from app.models.kill_switch import KillSwitchScope
from app.models.model_research import (
    ModelEvaluation,
    ModelResearchRecord,
    ModelType,
    ModelVersion,
)
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.training import TrainingJob, TrainingJobStatus
from app.services.decision_labels import latest_labels
from app.services.kill_switch import is_kill_switch_active
from app.services.personal_dataset import (
    EXCLUSION_LABELS_ZH,
    MIN_CONFIRMED_DECISIONS,
    preview_snapshot,
)
from app.services.personal_training import PERSONAL_TRAINING_TYPE

HEARTBEAT_ENV = "TRAINING_WORKER_HEARTBEAT_PATH"
STALE_CLAIM_SECONDS = 900
USABLE_ACTIONS = ("buy", "sell", "hold")
ACTION_ZH = {"buy": "買入", "sell": "賣出", "hold": "觀望"}


class JourneyRefused(Exception):
    def __init__(self, code: str, message_zh: str):
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


def _short_id(value: Optional[str]) -> str:
    return f"{str(value)[:8]}…" if value else "—"


def _fmt_instant(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _owned_version(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> ExperimentVersion:
    version = (
        db.query(ExperimentVersion)
        .join(Experiment, ExperimentVersion.experiment_id == Experiment.id)
        .filter(
            ExperimentVersion.id == strategy_version_id,
            Experiment.owner_id == owner_id,
        )
        .first()
    )
    if version is None:
        raise JourneyRefused(
            "strategy_version_not_found",
            "找不到此帳號擁有的策略版本。",
        )
    return version


def _review_summary(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> Dict[str, int]:
    events = (
        db.query(HumanDecisionEvent)
        .filter(
            HumanDecisionEvent.owner_id == owner_id,
            HumanDecisionEvent.server_strategy_version_id == strategy_version_id,
        )
        .all()
    )
    superseded = {
        event.supersedes_event_id
        for event in events
        if event.supersedes_event_id
    }
    current = [event for event in events if event.id not in superseded]
    labels = latest_labels(db, owner_id, [event.id for event in current])

    confirmed = 0
    excluded = 0
    for event in current:
        label = labels.get(event.id)
        if label is None:
            continue
        if label.state is DecisionLabelState.excluded:
            excluded += 1
        elif label.action_label in USABLE_ACTIONS:
            confirmed += 1

    return {
        "total_current_decisions": len(current),
        "confirmed": confirmed,
        "excluded": excluded,
        "unreviewed": len(current) - confirmed - excluded,
    }


def _personal_jobs(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> List[TrainingJob]:
    rows = (
        db.query(TrainingJob)
        .filter(TrainingJob.user_id == owner_id)
        .order_by(TrainingJob.created_at.desc())
        .all()
    )
    out = []
    for job in rows:
        spec = job.strategy_spec_snapshot or {}
        if spec.get("training_type") != PERSONAL_TRAINING_TYPE:
            continue
        if str(spec.get("strategy_version_id")) != str(strategy_version_id):
            continue
        out.append(job)
    return out


def _personal_versions(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> List[ModelVersion]:
    rows = (
        db.query(ModelVersion)
        .join(
            ModelResearchRecord,
            ModelVersion.model_research_record_id == ModelResearchRecord.id,
        )
        .filter(
            ModelResearchRecord.owner_id == owner_id,
            ModelVersion.model_type == ModelType.personal_decision_model,
        )
        .order_by(ModelVersion.version_number)
        .all()
    )
    return [
        version
        for version in rows
        if str((version.model_specification_json or {}).get("strategy_version_id"))
        == str(strategy_version_id)
    ]


def _latest_evaluations(
    db: Session, version_ids: List[uuid.UUID]
) -> Dict[uuid.UUID, ModelEvaluation]:
    if not version_ids:
        return {}
    rows = (
        db.query(ModelEvaluation)
        .filter(ModelEvaluation.model_version_id.in_(version_ids))
        .order_by(ModelEvaluation.evaluation_date.desc())
        .all()
    )
    out: Dict[uuid.UUID, ModelEvaluation] = {}
    for row in rows:
        out.setdefault(row.model_version_id, row)
    return out


def _worker_status(pending_jobs: List[TrainingJob], running_jobs: List[TrainingJob]) -> Dict[str, Any]:
    path = os.environ.get(HEARTBEAT_ENV)
    now = utcnow()
    status: Dict[str, Any] = {
        "state": "unknown",
        "label_zh": "未能確認背景 worker 狀態",
        "detail_zh": "此環境未設定 worker 心跳路徑，因此無法判斷排隊中的工作是否有人處理。",
        "last_beat_at": None,
        "current_job_id": None,
        "pending_jobs": len(pending_jobs),
        "running_jobs": len(running_jobs),
        "waiting_reason_zh": None,
        "stale_claim_seconds": STALE_CLAIM_SECONDS,
    }

    if path:
        status["heartbeat_path_configured"] = True
        heartbeat: Optional[dict] = None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                heartbeat = json.load(handle)
        except (OSError, ValueError):
            heartbeat = None

        if heartbeat is None:
            status.update(
                {
                    "state": "offline",
                    "label_zh": "背景 worker 未運行",
                    "detail_zh": "找不到 worker 心跳檔；排隊中的工作不會被處理，直到 worker 啟動。",
                }
            )
        else:
            beat_raw = heartbeat.get("beat_at")
            age: Optional[float] = None
            if beat_raw:
                try:
                    beat_at = datetime.fromisoformat(str(beat_raw))
                    age = max(0.0, (now - beat_at).total_seconds())
                except ValueError:
                    age = None
            interval = heartbeat.get("poll_interval_seconds") or 5
            stale_after = max(30.0, 3.0 * float(interval))
            status["last_beat_at"] = beat_raw
            status["current_job_id"] = heartbeat.get("current_job_id")
            status["worker_pid"] = heartbeat.get("pid")

            if age is not None and age > stale_after:
                status.update(
                    {
                        "state": "stale",
                        "label_zh": "背景 worker 心跳已過期",
                        "detail_zh": (
                            f"最後心跳在 {int(age)} 秒前（容許 {int(stale_after)} 秒）；"
                            "worker 可能已停止，重啟後會接續排隊中的工作。"
                        ),
                    }
                )
            elif heartbeat.get("state") == "working":
                status.update(
                    {
                        "state": "working",
                        "label_zh": "背景 worker 正在處理工作",
                        "detail_zh": (
                            f"正在處理工作 {_short_id(heartbeat.get('current_job_id'))}。"
                        ),
                    }
                )
            else:
                status.update(
                    {
                        "state": "idle",
                        "label_zh": "背景 worker 閒置待命",
                        "detail_zh": (
                            f"心跳在 {int(age) if age is not None else 0} 秒前；"
                            f"每 {interval:g} 秒檢查一次排隊工作。"
                        ),
                    }
                )
    else:
        status["heartbeat_path_configured"] = False

    # A reason is only attached when something in THIS strategy is waiting.
    if pending_jobs and not running_jobs:
        state = status["state"]
        if state in ("offline", "stale", "unknown"):
            status["waiting_reason_zh"] = (
                f"有 {len(pending_jobs)} 個訓練工作排隊，但背景 worker 未確認在運行；"
                "worker 啟動後會自動處理，毋須重新提交。"
            )
        elif state == "working":
            status["waiting_reason_zh"] = (
                f"有 {len(pending_jobs)} 個訓練工作排隊；worker 正處理另一個工作，"
                "完成後會自動接續。"
            )
        else:
            status["waiting_reason_zh"] = (
                f"有 {len(pending_jobs)} 個訓練工作排隊；worker 下次輪詢會處理。"
            )
    elif running_jobs:
        claimed = (running_jobs[0].strategy_spec_snapshot or {}).get("claimed_at")
        age = None
        if claimed:
            try:
                age = max(
                    0.0,
                    (now - datetime.fromisoformat(str(claimed))).total_seconds(),
                )
            except ValueError:
                age = None
        if age is not None and age > STALE_CLAIM_SECONDS:
            status["waiting_reason_zh"] = (
                f"工作 {_short_id(running_jobs[0].id)} 停留在執行中已超過 "
                f"{int(age // 60)} 分鐘；worker 重啟後會自動排回隊列。"
            )
        else:
            status["waiting_reason_zh"] = (
                f"工作 {_short_id(running_jobs[0].id)} 正在執行。"
            )
    return status


def _kill_switch_active(db: Session) -> Optional[bool]:
    try:
        return is_kill_switch_active(db, KillSwitchScope.training)
    except Exception:
        # Display-only surface: an unreadable switch is reported as unknown,
        # not as "safe to proceed".
        return None


def _humanize_reason(reason: Dict[str, Any]) -> Dict[str, Any]:
    code = reason.get("code")
    measured = reason.get("measured")
    requirement = reason.get("requirement")

    if code == "INSUFFICIENT_CONFIRMED_DECISIONS":
        have = int(measured or 0)
        need = int(requirement or MIN_CONFIRMED_DECISIONS)
        text = (
            f"仲欠 {max(0, need - have)} 筆可用決策：現有 {have} 筆可用，"
            f"最低需要 {need} 筆。"
        )
    elif code == "SINGLE_CLASS_DATASET":
        classes = measured if isinstance(measured, dict) else {}
        seen = "、".join(
            f"{ACTION_ZH.get(key, key)} {value} 筆" for key, value in classes.items()
        )
        text = (
            f"可用決策只有 {len(classes)} 個動作類別"
            + (f"（{seen}）" if seen else "")
            + "；至少要 2 個類別才能訓練分類模型。"
        )
    elif code == "CONSENT_MISSING":
        text = "尚未記錄個人化訓練同意；建立快照前需要先在此策略版本授權。"
    elif code == "CONSENT_SCOPE_MISMATCH":
        text = "目前的個人化訓練同意不包含此策略版本；更新同意範圍後才可建立快照。"
    else:
        text = reason.get("message_zh") or code or "未說明的資料缺口"

    return {
        "code": code,
        "text_zh": text,
        "measured": measured,
        "requirement": requirement,
        "raw_message_zh": reason.get("message_zh"),
    }


def build_journey(
    db: Session, owner_id: uuid.UUID, strategy_version_id: uuid.UUID
) -> Dict[str, Any]:
    version = _owned_version(db, owner_id, strategy_version_id)
    experiment = (
        db.query(Experiment).filter(Experiment.id == version.experiment_id).first()
    )
    preview = preview_snapshot(db, owner_id, strategy_version_id)
    review = _review_summary(db, owner_id, strategy_version_id)

    snapshots = (
        db.query(PersonalizationDatasetSnapshot)
        .filter(
            PersonalizationDatasetSnapshot.owner_id == owner_id,
            PersonalizationDatasetSnapshot.strategy_version_id == strategy_version_id,
        )
        .order_by(PersonalizationDatasetSnapshot.created_at.desc())
        .all()
    )
    jobs = _personal_jobs(db, owner_id, strategy_version_id)
    pending_jobs = [job for job in jobs if job.status is TrainingJobStatus.pending]
    running_jobs = [job for job in jobs if job.status is TrainingJobStatus.running]
    versions = _personal_versions(db, owner_id, strategy_version_id)
    evaluations = _latest_evaluations(db, [item.id for item in versions])

    record_ids = {item.model_research_record_id for item in versions}
    records = {
        record.id: record
        for record in db.query(ModelResearchRecord)
        .filter(ModelResearchRecord.id.in_(record_ids))
        .all()
    } if record_ids else {}
    active_version_id = next(
        (
            record.active_version_id
            for record in records.values()
            if record.active_version_id is not None
        ),
        None,
    )

    spec = version.strategy_spec or {}
    consent = preview.get("consent") or {}
    reasons = list(preview.get("reasons") or [])
    can_build = bool(preview.get("can_build"))
    exclusion_breakdown = preview.get("exclusion_breakdown") or {}
    class_distribution = preview.get("class_distribution") or {}

    latest_snapshot = snapshots[0] if snapshots else None
    latest_job = jobs[0] if jobs else None
    latest_model = versions[-1] if versions else None
    latest_evaluation = (
        evaluations.get(latest_model.id) if latest_model is not None else None
    )

    # --- 1. 研究緊甚麼 -----------------------------------------------------
    instruments = spec.get("asset_ids") or []
    period = f"{spec.get('start_date') or '—'} 至 {spec.get('end_date') or '—'}"
    nature = preview.get("data_nature_by_instrument") or {}
    nature_text = "；".join(
        f"{instrument}（{', '.join(providers)}）"
        for instrument, providers in nature.items()
    )
    researching_summary = (
        f"策略「{experiment.title if experiment else '未命名'}」研究 "
        f"{'、'.join(instruments) if instruments else '未指定標的'}，期間 {period}。"
    )
    if nature_text:
        researching_summary += f" 已儲存資料性質：{nature_text}。"
    if spec.get("synthetic"):
        researching_summary += " 此候選環境的市場資料與決策均為合成 fixture，不是真實成交記錄。"

    # --- 2. 做過甚麼 -------------------------------------------------------
    done_summary = (
        f"已覆核 {review['total_current_decisions']} 筆決策："
        f"{review['confirmed']} 筆確認可用、{review['excluded']} 筆排除、"
        f"{review['unreviewed']} 筆未覆核；"
        f"已建立 {len(snapshots)} 個凍結快照、{len(jobs)} 個訓練工作、"
        f"{len(versions)} 個模型版本。"
    )
    if consent.get("in_scope"):
        done_summary += " 個人化訓練同意已涵蓋此策略版本。"

    excluded_note = []
    for code, count in exclusion_breakdown.items():
        if not count:
            continue
        label = EXCLUSION_LABELS_ZH.get(code, code)
        excluded_note.append(f"{count} 筆因「{label}」未納入")

    # --- 3. 得到甚麼 -------------------------------------------------------
    result_summary = "尚未建立可訓練的資料快照，暫時未有模型結果。"
    result_facts: List[Dict[str, Any]] = []
    if latest_model is not None:
        result_summary = (
            f"已有 {len(versions)} 個模型版本；最新為版本 {latest_model.version_number}"
        )
        if latest_evaluation is not None:
            metrics = latest_evaluation.metrics_json or {}
            accuracy = (metrics.get("metrics") or {}).get("accuracy")
            baseline = (metrics.get("baseline") or {}).get("accuracy")
            if isinstance(accuracy, float):
                result_summary += f"，測試準確度 {accuracy:.3f}"
                if isinstance(baseline, float):
                    result_summary += f"（baseline {baseline:.3f}）"
            warnings = latest_evaluation.warning_flags or []
            if warnings:
                result_summary += f"；有 {len(warnings)} 項評估警告需閱讀。"
        result_summary += f"；訓練使用 {latest_model.model_specification_json.get('n_rows') or '—'} 筆已確認決策。"
        result_facts.append(
            {
                "label_zh": "使用中版本",
                "value_zh": (
                    f"版本 {next((v.version_number for v in versions if v.id == active_version_id), '—')}"
                    if active_version_id
                    else "尚未啟用任何版本"
                ),
            }
        )
        result_facts.append(
            {
                "label_zh": "快照 checksum",
                "value_zh": _short_id(
                    latest_model.model_specification_json.get("snapshot_checksum")
                ),
            }
        )
    elif latest_snapshot is not None:
        result_summary = (
            f"已建立凍結快照 {_short_id(str(latest_snapshot.id))}，"
            f"納入 {latest_snapshot.included_count} 筆可用決策"
            f"（checksum {_short_id(latest_snapshot.checksum)}）；尚未訓練模型。"
        )
        result_facts.append(
            {
                "label_zh": "快照涵蓋期間",
                "value_zh": (
                    f"{latest_snapshot.date_range_start or '—'} 至 "
                    f"{latest_snapshot.date_range_end or '—'}"
                ),
            }
        )

    # --- 4. 仲缺甚麼 -------------------------------------------------------
    missing = [_humanize_reason(reason) for reason in reasons]
    worker = _worker_status(pending_jobs, running_jobs)
    kill_active = _kill_switch_active(db)

    if latest_job is not None and latest_job.status is TrainingJobStatus.failed:
        missing.append(
            {
                "code": "TRAINING_JOB_FAILED",
                "text_zh": (
                    "最近一次訓練工作失敗"
                    + (f"：{latest_job.error_details}。" if latest_job.error_details else "；可重新提交訓練。")
                    + " 已建立的快照與版本不受影響。"
                ),
                "measured": None,
                "requirement": None,
                "raw_message_zh": latest_job.error_details,
            }
        )
    reclaimed_jobs = [
        job
        for job in jobs
        if (job.strategy_spec_snapshot or {}).get("reclaimed_at")
    ]
    if reclaimed_jobs:
        latest_reclaim = reclaimed_jobs[0]
        missing.append(
            {
                "code": "JOB_RECLAIMED",
                "text_zh": (
                    f"工作 {_short_id(latest_reclaim.id)} 曾被判定停滯，"
                    "已自動排回隊列重試（沒有遺失資料）；如仍未完成，可重新提交。"
                ),
                "measured": None,
                "requirement": None,
                "raw_message_zh": None,
            }
        )
    if kill_active:
        missing.append(
            {
                "code": "TRAINING_KILL_SWITCH",
                "text_zh": "訓練緊急開關目前開啟：新工作不會被 worker 領取，現有工作亦可能暫停。",
                "measured": None,
                "requirement": None,
                "raw_message_zh": None,
            }
        )
    if not missing:
        missing.append(
            {
                "code": "NONE",
                "text_zh": "目前沒有阻擋下一步的資料缺口。",
                "measured": None,
                "requirement": None,
                "raw_message_zh": None,
                "clear": True,
            }
        )

    # --- 5. 下一步做甚麼 ---------------------------------------------------
    if not consent.get("in_scope"):
        next_action = {
            "kind": "grant_consent",
            "label_zh": "先記錄個人化訓練同意",
            "detail_zh": "在上方「個人化訓練同意」面板勾選此策略版本，確認後記錄同意。",
        }
    elif not can_build:
        next_action = {
            "kind": "review_decisions",
            "label_zh": "補足可用的已確認決策",
            "detail_zh": missing[0]["text_zh"],
        }
    elif latest_snapshot is None:
        next_action = {
            "kind": "create_snapshot",
            "label_zh": "建立凍結資料快照",
            "detail_zh": "在下方「訓練資料」按「建立快照」；系統會固定這批已確認決策與 checksum。",
        }
    elif latest_job is None or latest_job.status is TrainingJobStatus.failed:
        next_action = {
            "kind": "start_training",
            "label_zh": "開始訓練個人決策模型",
            "detail_zh": "選擇最新快照後按「開始訓練」；工作會交由背景 worker 執行，完成後自動更新。",
        }
    elif latest_job.status in (TrainingJobStatus.pending, TrainingJobStatus.running):
        next_action = {
            "kind": "wait_worker",
            "label_zh": "等待背景 worker 完成訓練",
            "detail_zh": worker.get("waiting_reason_zh")
            or "工作已排隊；背景 worker 會自動處理。",
        }
    elif latest_model is not None and active_version_id is None:
        next_action = {
            "kind": "activate_model",
            "label_zh": "啟用一個模型版本",
            "detail_zh": "在下方模型版本按「啟用」，之後推論會解析到使用中版本。",
        }
    elif latest_model is not None:
        next_action = {
            "kind": "predict",
            "label_zh": "以使用中版本執行一次推論",
            "detail_zh": "在下方填寫標的與日期，按「執行推論」查看模型對你決策的估計。",
        }
    else:
        next_action = {
            "kind": "none",
            "label_zh": "暫無可執行的下一步",
            "detail_zh": "請先處理上方列出的資料缺口。",
        }

    steps = [
        {
            "key": "researching",
            "title_zh": "研究緊甚麼",
            "state": "ready",
            "summary_zh": researching_summary,
            "facts": [
                {"label_zh": "策略版本", "value_zh": f"v{version.version_number}"},
                {"label_zh": "期間", "value_zh": period},
                {"label_zh": "標的", "value_zh": "、".join(instruments) or "—"},
            ],
        },
        {
            "key": "done",
            "title_zh": "做過甚麼",
            "state": "ready",
            "summary_zh": done_summary,
            "facts": [
                {"label_zh": "已確認決策", "value_zh": str(review["confirmed"])},
                {"label_zh": "已排除", "value_zh": str(review["excluded"])},
                {"label_zh": "未覆核", "value_zh": str(review["unreviewed"])},
            ],
            "notes_zh": excluded_note,
        },
        {
            "key": "result",
            "title_zh": "得到甚麼",
            "state": "ready" if (versions or snapshots) else "empty",
            "summary_zh": result_summary,
            "facts": result_facts,
        },
        {
            "key": "missing",
            "title_zh": "仲缺甚麼",
            "state": "clear" if missing[0].get("clear") else "blocked",
            "summary_zh": (
                "目前沒有阻擋下一步的資料缺口。"
                if missing[0].get("clear")
                else f"有 {len([m for m in missing if not m.get('clear')])} 項需要處理。"
            ),
            "items": missing,
        },
        {
            "key": "next",
            "title_zh": "下一步做甚麼",
            "state": "ready",
            "summary_zh": next_action["label_zh"],
            "action": next_action,
        },
    ]

    jobs_payload = []
    for job in jobs[:5]:
        job_spec = job.strategy_spec_snapshot or {}
        model_version = next(
            (
                item
                for item in versions
                if str((item.model_specification_json or {}).get("training_job_id"))
                == str(job.id)
            ),
            None,
        )
        jobs_payload.append(
            {
                "job_id": str(job.id),
                "status": job.status.value,
                "snapshot_id": job_spec.get("snapshot_id"),
                "created_at": _fmt_instant(job.created_at),
                "completed_at": _fmt_instant(job.completed_at),
                "claimed_at": job_spec.get("claimed_at"),
                "reclaimed_at": job_spec.get("reclaimed_at"),
                "error_details": job.error_details,
                "model_version_id": str(model_version.id) if model_version else None,
            }
        )

    return {
        "strategy_version_id": str(strategy_version_id),
        "generated_at": utcnow().isoformat(),
        "strategy": {
            "title": experiment.title if experiment else None,
            "version_number": version.version_number,
            "instruments": instruments,
            "period": period,
            "synthetic": bool(spec.get("synthetic")),
        },
        "counts": {
            **review,
            "usable_rows": preview.get("included_count") or 0,
            "excluded_rows": preview.get("excluded_count") or 0,
            "minimum_usable_rows": MIN_CONFIRMED_DECISIONS,
            "snapshots": len(snapshots),
            "training_jobs": len(jobs),
            "model_versions": len(versions),
        },
        "class_distribution": class_distribution,
        "exclusion_breakdown": exclusion_breakdown,
        "consent": consent,
        "steps": steps,
        "missing": missing,
        "next_action": next_action,
        "worker": worker,
        "jobs": jobs_payload,
        "data_nature_by_instrument": nature,
        "not_claimed": [
            "此頁只描述工程狀態，不評價模型是否有效或是否賺錢。",
            "合成 fixture 只證明流程可執行，不代表真實市場表現。",
        ],
    }
