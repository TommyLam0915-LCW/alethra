"""Bounded robustness studies over one strategy version.

A study never edits the experiment, its versions or its current pointer: it
only measures. Every cell is executed through the same accounting path as a
manual backtest (``execute_backtest_run``), so each attempt is a real research
run with its own quota reservation, data identity and stored result.

Bounding is the point. The plan is built and priced before anything runs, the
attempt cap is enforced up front, and a refusal is explained rather than
silently truncated. Every attempt — including failures and skips — is kept, so
the aggregate cannot be read as "the best run we kept".
"""

import copy
import statistics
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.auth.entitlements import get_quota_status
from app.data.demo_data import get_demo_ohlcv
from app.lib.timeutil import utcnow
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import RunStatus
from app.models.robustness import RobustnessAttempt, RobustnessStudy
from app.models.user import User
from app.services import emit_event
from app.services.market_data_storage import MarketDataStorage
from app.services.research_runs import execute_backtest_run

MAX_ATTEMPTS_DEFAULT = 12
MAX_ATTEMPTS_HARD = 24

DEFAULT_CONFIG: Dict[str, Any] = {
    "slices": 3,
    "folds": 2,
    "train_ratio": 0.6,
    "gap_bars": 1,
    "max_attempts": MAX_ATTEMPTS_DEFAULT,
}

# Engine-backed bounds (see backend/app/engine/backtest.py parameter validation).
PARAM_BOUNDS: Dict[str, Dict[str, tuple]] = {
    "sma_crossover": {
        "short_window": (2, 60),
        "long_window": (3, 200),
    },
    "price_change_fixed_hold": {
        "lookback_days": (1, 60),
        "threshold_pct": (0.01, 0.50),
        "holding_days": (1, 120),
    },
    "buy_and_hold": {},
}

SENSITIVITY_STEPS = (-0.2, 0.2)
MAX_SENSITIVITY_PARAMS = 2
MAX_WALK_FORWARD_CANDIDATES = 3

SELECTION_RULES = {
    "cost_scenarios": "不設選擇規則：列出全部成本情境，不以單一情境代表整體。",
    "period_slices": "不設選擇規則：列出全部期間分段，不以最佳分段代表整體。",
    "parameter_sensitivity": "不設選擇規則：列出全部單因子變動，不以最佳參數代表整體。",
    "walk_forward": (
        "每折只在訓練期以總報酬最高者選參數（同分取最接近原設定者），"
        "選定後才在之後的測試期量測一次；不會以測試期結果回頭挑選參數。"
    ),
}


class RobustnessRefused(Exception):
    """The plan cannot be built or executed as requested, with stated reasons."""

    def __init__(self, reasons):
        if isinstance(reasons, str):
            reasons = [reasons]
        self.reasons = [r for r in reasons if r]
        super().__init__("；".join(self.reasons))


class QuotaExhausted(Exception):
    def __init__(self, detail):
        self.detail = detail
        super().__init__("research_run quota exhausted")


# ── Data calendar ────────────────────────────────────────────────────────────

def _calendar(db: Session, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Trading dates the engine will actually use for this spec.

    Stored bars win because that is what the engine prefers; when nothing is
    stored, only built-in demo assets can be sliced (and they are labelled
    synthetic). A mixed or ambiguous dataset is refused instead of guessed.
    """
    asset_ids = list(spec.get("asset_ids") or [])
    start, end = spec.get("start_date"), spec.get("end_date")
    if not asset_ids or not start or not end:
        raise RobustnessRefused("版本缺少資產或期間，無法建立研究計劃。")

    storage = MarketDataStorage(db)
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    stored = {
        aid: storage.get_bars(instrument_id=aid, start_date=start_d, end_date=end_d)
        for aid in asset_ids
    }

    have = [aid for aid, bars in stored.items() if bars]
    if have and len(have) != len(asset_ids):
        missing = [aid for aid in asset_ids if not stored[aid]]
        raise RobustnessRefused(
            f"部分資產只有內建示範資料、部分已有儲存資料（缺少：{'、'.join(missing)}）。"
            "穩健性研究要求一致的單一資料來源：請先為所有資產擷取資料，或改用同一來源的資產。"
        )

    if have:
        provider_ids: set = set()
        dates: set = set()
        for aid, bars in stored.items():
            providers = {b.provider_id for b in bars if b.provider_id}
            if len(providers) > 1:
                raise RobustnessRefused(
                    f"{aid} 在期間內有多個資料供應商（{'、'.join(sorted(providers))}）。"
                    "穩健性研究需要綁定單一供應商，否則分段會混用兩個價格序列。"
                )
            provider_ids |= providers
            dates.update(b.bar_date.isoformat() for b in bars)
        nature_map = storage.get_nature_map(asset_ids)
        natures = {
            aid: MarketDataStorage.resolve_nature(
                nature_map, aid, {b.provider_id for b in stored[aid] if b.provider_id}
            )
            for aid in asset_ids
        }
        return {
            "source": "stored",
            "providers": sorted(provider_ids),
            "natures": natures,
            "dates": sorted(dates),
        }

    demo = get_demo_ohlcv(asset_ids, start, end)
    missing = [aid for aid in asset_ids if aid not in demo]
    if missing:
        raise RobustnessRefused(
            f"無法確定 {'、'.join(missing)} 的交易日曆：未有已儲存資料，亦不是內建示範資產。"
            "請先擷取資料，再進行期間切分或 walk-forward。"
        )
    dates = sorted(
        {
            row["date"].strftime("%Y-%m-%d")
            for df in demo.values()
            for _, row in df.iterrows()
        }
    )
    if not dates:
        raise RobustnessRefused("此期間內沒有任何資料，無法建立研究計劃。")
    return {
        "source": "builtin_demo",
        "providers": ["builtin_demo"],
        "natures": {aid: "synthetic" for aid in asset_ids},
        "dates": dates,
    }


def _required_bars(spec: Dict[str, Any]) -> int:
    """Bars a slice needs before the template can produce a signal at all."""
    template = spec.get("template_type")
    params = spec.get("signal_parameters") or {}
    if template == "sma_crossover":
        return int(params.get("long_window") or 20) + 1
    if template == "price_change_fixed_hold":
        lookback = int(params.get("lookback_days") or 5)
        holding = int(params.get("holding_days") or 5)
        return lookback + min(holding, 5) + 1
    return 3


# ── Cell builders ────────────────────────────────────────────────────────────

def _cell(spec, *, key, label, role, overrides, start, end, fold=None, notes=None):
    cell = {
        "cell_key": key,
        "label": label,
        "role": role,
        "spec": spec,
        "overrides": overrides or {},
        "period_start": start,
        "period_end": end,
    }
    if fold is not None:
        cell["fold"] = fold
    if notes:
        cell["notes"] = notes
    return cell


def _cost_levels(rate: Optional[float]) -> List[float]:
    base = float(rate or 0.0)
    if base > 0:
        return sorted({0.0, round(base, 6), round(min(base * 2, 0.05), 6)})
    return [0.0, 0.001, 0.005]


def _cost_cells(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    commission = _cost_levels(spec.get("commission_rate"))
    slippage = _cost_levels(spec.get("slippage_rate"))
    base_commission = float(spec.get("commission_rate") or 0.0)
    base_slippage = float(spec.get("slippage_rate") or 0.0)
    cells = []
    for c in commission:
        for s in slippage:
            is_base = c == base_commission and s == base_slippage
            cell_spec = copy.deepcopy(spec)
            cell_spec["commission_rate"] = c
            cell_spec["slippage_rate"] = s
            cells.append(
                _cell(
                    cell_spec,
                    key=f"cost:c={c}:s={s}",
                    label=(
                        "基準設定（原成本）"
                        if is_base
                        else f"手續費 {c * 100:.2f}% ／ 滑價 {s * 100:.2f}%"
                    ),
                    role="baseline" if is_base else "scenario",
                    overrides={"commission_rate": c, "slippage_rate": s},
                    start=spec["start_date"],
                    end=spec["end_date"],
                )
            )
    return cells


def _period_cells(spec: Dict[str, Any], dates: List[str], slices: int) -> List[Dict[str, Any]]:
    need = _required_bars(spec)
    total = len(dates)
    if total < slices * need:
        raise RobustnessRefused(
            f"交易日共有 {total} 日；切分為 {slices} 段時每段至少需要 {need} 日（此策略的訊號暖機需求），"
            "所以分段後每段都不能產生訊號。請減少段數或延長期間。"
        )
    base = total // slices
    remainder = total % slices
    cells = []
    offset = 0
    for i in range(slices):
        size = base + (1 if i < remainder else 0)
        chunk = dates[offset : offset + size]
        offset += size
        if not chunk:
            continue
        cell_spec = copy.deepcopy(spec)
        cell_spec["start_date"] = chunk[0]
        cell_spec["end_date"] = chunk[-1]
        cells.append(
            _cell(
                cell_spec,
                key=f"slice:{i + 1}",
                label=f"第 {i + 1} 段：{chunk[0]} → {chunk[-1]}（{len(chunk)} 個交易日）",
                role="slice",
                overrides={"start_date": chunk[0], "end_date": chunk[-1]},
                start=chunk[0],
                end=chunk[-1],
            )
        )
    return cells


def _valid_combination(template: str, params: Dict[str, Any]) -> bool:
    bounds = PARAM_BOUNDS.get(template, {})
    for name, (lo, hi) in bounds.items():
        value = params.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if value < lo or value > hi:
            return False
    if template == "sma_crossover":
        short = params.get("short_window")
        long = params.get("long_window")
        if not isinstance(short, int) or not isinstance(long, int):
            return False
        if short >= long:
            return False
    return True


def _sensitivity_variants(spec: Dict[str, Any]) -> List[tuple]:
    """One-at-a-time ±20% variants around the base, capped at two parameters."""
    template = spec.get("template_type")
    bounds = PARAM_BOUNDS.get(template, {})
    params = spec.get("signal_parameters") or {}
    if not bounds:
        return []
    variants = []
    for name in list(bounds.keys())[:MAX_SENSITIVITY_PARAMS]:
        base_value = params.get(name)
        if not isinstance(base_value, (int, float)) or isinstance(base_value, bool):
            continue
        for step in SENSITIVITY_STEPS:
            raw = base_value * (1 + step)
            value = int(round(raw)) if isinstance(base_value, int) else round(raw, 4)
            lo, hi = bounds[name]
            if value < lo or value > hi or value == base_value:
                continue
            candidate_params = {**params, name: value}
            if not _valid_combination(template, candidate_params):
                continue
            variants.append((name, value))
    return variants


def _sensitivity_cells(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    template = spec.get("template_type")
    if not PARAM_BOUNDS.get(template):
        raise RobustnessRefused(
            f"{template} 沒有可調的數值參數，無法進行單因子敏感度分析。"
            "可改用期間切分、成本情境或 walk-forward。"
        )
    variants = _sensitivity_variants(spec)
    if not variants:
        raise RobustnessRefused(
            "以現有參數值加減 20% 後沒有合法組合（可能已到引擎允許的上下限），"
            "無法建立單因子敏感度分析。"
        )
    cells = [
        _cell(
            copy.deepcopy(spec),
            key="param:base",
            label="基準設定（原參數）",
            role="baseline",
            overrides={},
            start=spec["start_date"],
            end=spec["end_date"],
        )
    ]
    for name, value in variants:
        cell_spec = copy.deepcopy(spec)
        cell_spec["signal_parameters"] = {**cell_spec.get("signal_parameters", {}), name: value}
        cells.append(
            _cell(
                cell_spec,
                key=f"param:{name}={value}",
                label=f"{name} = {value}（原值 {spec.get('signal_parameters', {}).get(name)}）",
                role="parameter",
                overrides={name: value},
                start=spec["start_date"],
                end=spec["end_date"],
            )
        )
    return cells


def _forward_candidates(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    variants = _sensitivity_variants(spec)[: MAX_WALK_FORWARD_CANDIDATES - 1]
    candidates = [{"key": "base", "overrides": {}}]
    for name, value in variants:
        candidates.append({"key": f"{name}={value}", "overrides": {name: value}})
    return candidates


def _walk_forward_cells(
    spec: Dict[str, Any],
    dates: List[str],
    folds: int,
    train_ratio: float,
    gap_bars: int,
) -> List[Dict[str, Any]]:
    need = _required_bars(spec)
    total = len(dates)
    base = total // folds
    remainder = total % folds
    fold_sizes = [base + (1 if i < remainder else 0) for i in range(folds)]
    candidates = _forward_candidates(spec)

    windows = []
    offset = 0
    for i, size in enumerate(fold_sizes):
        chunk = dates[offset : offset + size]
        offset += size
        train_n = int(size * train_ratio)
        train = chunk[:train_n]
        test = chunk[train_n + gap_bars :]
        if len(train) < need or len(test) < need:
            raise RobustnessRefused(
                f"第 {i + 1} 折只有 {size} 個交易日：訓練期 {len(train)} 日、測試期 {len(test)} 日，"
                f"而此策略每段至少需要 {need} 日。請減少折數或延長期間。"
            )
        windows.append((train, test))

    cells = []
    for i, (train, test) in enumerate(windows):
        fold_id = f"fold:{i + 1}"
        for candidate in candidates:
            cell_spec = copy.deepcopy(spec)
            cell_spec["signal_parameters"] = {
                **cell_spec.get("signal_parameters", {}),
                **candidate["overrides"],
            }
            cell_spec["start_date"] = train[0]
            cell_spec["end_date"] = train[-1]
            cells.append(
                _cell(
                    cell_spec,
                    key=f"{fold_id}:cand:{candidate['key']}",
                    label=f"第 {i + 1} 折 訓練期 {train[0]} → {train[-1]}：{candidate['key']}",
                    role="selection",
                    overrides=candidate["overrides"],
                    start=train[0],
                    end=train[-1],
                    fold=fold_id,
                )
            )
        test_spec = copy.deepcopy(spec)
        test_spec["start_date"] = test[0]
        test_spec["end_date"] = test[-1]
        cells.append(
            _cell(
                test_spec,
                key=f"{fold_id}:test",
                label=f"第 {i + 1} 折 測試期 {test[0]} → {test[-1]}",
                role="measurement",
                overrides={},
                start=test[0],
                end=test[-1],
                fold=fold_id,
                notes=(
                    f"訓練期 {train[0]} → {train[-1]}；中間相隔 {gap_bars} 個交易日，"
                    "該段不屬於訓練亦不屬於測試。"
                ),
            )
        )
    return cells


def _cells_for_kind(
    kind: str, spec: Dict[str, Any], calendar: Dict[str, Any], config: Dict[str, Any]
) -> List[Dict[str, Any]]:
    if kind == "cost_scenarios":
        return _cost_cells(spec)
    if kind == "period_slices":
        return _period_cells(spec, calendar["dates"], int(config["slices"]))
    if kind == "parameter_sensitivity":
        return _sensitivity_cells(spec)
    if kind == "walk_forward":
        return _walk_forward_cells(
            spec,
            calendar["dates"],
            int(config["folds"]),
            float(config["train_ratio"]),
            int(config["gap_bars"]),
        )
    raise RobustnessRefused(f"不支援的研究種類：{kind}")


def _disclosures(
    kind: str, calendar: Dict[str, Any], config: Dict[str, Any]
) -> List[str]:
    source_label = (
        "已儲存市場資料"
        if calendar["source"] == "stored"
        else "內建合成示範資料（非真實市場價格）"
    )
    disclosures = [
        "全部嘗試（包括失敗與跳過）都會列出；不會只以最佳結果代表策略穩健性。",
        f"資料日曆：{source_label}；{calendar['dates'][0]} → {calendar['dates'][-1]}"
        f"，共 {len(calendar['dates'])} 個交易日。每個嘗試都會記錄實際使用的資料來源、"
        "資料性質與資料集校驗碼。",
        "研究不會新增、改寫或鎖定策略版本；每個嘗試都綁定來源版本，只量測、不改規則。",
    ]
    if kind == "cost_scenarios":
        disclosures.append("只改手續費與滑價，其餘設定與來源版本相同；每個情境都是一次獨立回測。")
    elif kind == "period_slices":
        disclosures.append("分段連續且互不重疊；不同市況下的差異不是策略改動，也不代表未來表現。")
    elif kind == "parameter_sensitivity":
        disclosures.append(
            "單因子 ±20% 變動，其餘參數固定；沒有做多參數聯合搜尋，"
            "所以結果只反映該參數在目前值附近的敏感度。"
        )
    elif kind == "walk_forward":
        disclosures.append(
            f"每折的訓練期與測試期在時間上分開，中間相隔 {int(config['gap_bars'])} 個交易日；"
            f"測試期只在參數選定後量測一次。只有 {int(config['folds'])} 折，"
            "不可把各折當作彼此獨立的重複實驗或統計樣本。"
        )
    return disclosures


# ── Plan ─────────────────────────────────────────────────────────────────────

def build_plan(
    db: Session,
    *,
    user: User,
    experiment: Experiment,
    version: ExperimentVersion,
    kind: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = {**DEFAULT_CONFIG, **(config or {})}
    spec = version.strategy_spec or {}

    frequency = spec.get("frequency") or "1d"
    if frequency != "1d":
        raise RobustnessRefused(
            f"穩健性研究目前只支援日線策略；此版本是 {frequency}，不會自動降級為日線後再計算。"
        )
    template = spec.get("template_type")
    if template not in PARAM_BOUNDS:
        raise RobustnessRefused(f"不支援的策略範本：{template}。")

    calendar = _calendar(db, spec)
    cells = _cells_for_kind(kind, spec, calendar, merged)
    if not cells:
        raise RobustnessRefused("此計劃沒有任何可執行的嘗試。")

    estimated = len(cells)
    max_attempts = int(merged["max_attempts"])
    if estimated > max_attempts:
        raise RobustnessRefused(
            f"計劃需要 {estimated} 次回測，超過本次上限 {max_attempts} 次。"
            f"請減少段數／折數，或明確提高上限（最多 {MAX_ATTEMPTS_HARD} 次）。"
        )

    quota = get_quota_status(db, user.id, "research_run")
    unlimited = bool(quota.get("unlimited"))
    remaining = quota.get("remaining")
    sufficient = unlimited or (remaining is not None and remaining >= estimated)

    disclosures = _disclosures(kind, calendar, merged)
    requested = (spec.get("start_date"), spec.get("end_date"))
    effective = (calendar["dates"][0], calendar["dates"][-1])
    if requested != effective:
        disclosures.append(
            f"請求期間為 {requested[0]} → {requested[1]}，但期間內只有 "
            f"{effective[0]} → {effective[1]} 的報價；分段以實際有報價的日期為準。"
        )

    return {
        "kind": kind,
        "version_id": str(version.id),
        "version_number": version.version_number,
        "template_type": template,
        "source_period": {
            "start_date": spec.get("start_date"),
            "end_date": spec.get("end_date"),
        },
        "calendar": {
            "source": calendar["source"],
            "providers": calendar["providers"],
            "natures": calendar["natures"],
            "first_date": calendar["dates"][0],
            "last_date": calendar["dates"][-1],
            "bars": len(calendar["dates"]),
        },
        "required_bars": _required_bars(spec),
        "estimated_attempts": estimated,
        "max_attempts": max_attempts,
        "config": merged,
        "cells": cells,
        "selection_rule": SELECTION_RULES[kind],
        "disclosures": disclosures,
        "quota": {**quota, "required": estimated, "sufficient": sufficient},
        "ready": sufficient,
    }


# ── Execution ────────────────────────────────────────────────────────────────

def _reusable_attempt(
    db: Session, study: RobustnessStudy, cell_key: str
) -> Optional[RobustnessAttempt]:
    return (
        db.query(RobustnessAttempt)
        .filter(
            RobustnessAttempt.study_id == study.id,
            RobustnessAttempt.cell_key == cell_key,
        )
        .first()
    )


def _run_cell(
    db: Session,
    *,
    study: RobustnessStudy,
    version: ExperimentVersion,
    experiment: Experiment,
    user: User,
    cell: Dict[str, Any],
) -> RobustnessAttempt:
    attempt = _reusable_attempt(db, study, cell["cell_key"])
    if attempt is not None and attempt.status == "completed":
        return attempt
    if attempt is None:
        attempt = RobustnessAttempt(
            study_id=study.id,
            cell_key=cell["cell_key"],
            label=cell["label"],
            role=cell["role"],
            spec=cell["spec"],
            overrides=cell.get("overrides") or {},
            period_start=cell.get("period_start"),
            period_end=cell.get("period_end"),
            status="pending",
        )
        db.add(attempt)
    else:
        attempt.label = cell["label"]
        attempt.role = cell["role"]
        attempt.spec = cell["spec"]
        attempt.overrides = cell.get("overrides") or {}
        attempt.period_start = cell.get("period_start")
        attempt.period_end = cell.get("period_end")
        attempt.status = "pending"
        attempt.metrics = None
        attempt.dataset_checksum = None
        attempt.data_identity = None
        attempt.error_details = None
        attempt.completed_at = None
    # Committed before the run so a crash mid-run leaves a resumable cell
    # instead of an invisible attempt.
    db.commit()

    try:
        run = execute_backtest_run(
            db,
            user=user,
            version=version,
            experiment=experiment,
            strategy_spec=cell["spec"],
            event_payload={
                "robustness_study_id": str(study.id),
                "cell_key": cell["cell_key"],
            },
        )
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            # The reservation never happened; drop the pending run row and the
            # started event that were flushed before the quota check.
            db.rollback()
            fresh = db.query(RobustnessAttempt).filter(RobustnessAttempt.id == attempt.id).first()
            if fresh is not None:
                fresh.status = "skipped"
                fresh.error_details = "研究執行額度不足，此嘗試沒有執行。"
                fresh.completed_at = utcnow()
                db.commit()
            raise QuotaExhausted(getattr(exc, "detail", None))
        raise

    attempt.run_id = run.id
    attempt.status = "completed" if run.status == RunStatus.completed else "failed"
    attempt.metrics = run.metrics
    attempt.dataset_checksum = run.dataset_checksum
    result = run.result_json or {}
    attempt.data_identity = {
        key: result.get(key)
        for key in (
            "data_provider",
            "feed_mode",
            "data_source_used",
            "data_nature",
            "start_date",
            "end_date",
        )
    }
    attempt.error_details = run.error_details
    attempt.completed_at = utcnow()
    db.commit()
    return attempt


def _with_overrides(spec: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(spec)
    if overrides:
        merged["signal_parameters"] = {**merged.get("signal_parameters", {}), **overrides}
    return merged


def _candidate_distance(overrides: Dict[str, Any], base_params: Dict[str, Any]) -> float:
    distance = 0.0
    for name, value in (overrides or {}).items():
        base_value = base_params.get(name)
        if isinstance(base_value, (int, float)) and base_value:
            distance += abs(float(value) - float(base_value)) / abs(float(base_value))
    return distance


def _execute_walk_forward(
    db: Session,
    *,
    study: RobustnessStudy,
    version: ExperimentVersion,
    experiment: Experiment,
    user: User,
) -> None:
    plan_cells = (study.plan or {}).get("cells") or []
    fold_ids = []
    for cell in plan_cells:
        fold = cell.get("fold")
        if fold and fold not in fold_ids:
            fold_ids.append(fold)

    base_params = (version.strategy_spec or {}).get("signal_parameters") or {}

    for fold_id in fold_ids:
        db.refresh(study)
        if study.status == "cancelled":
            return

        fold_cells = [c for c in plan_cells if c.get("fold") == fold_id]
        selection_cells = [c for c in fold_cells if c["role"] == "selection"]
        measurement_cell = next(
            (c for c in fold_cells if c["role"] == "measurement"), None
        )
        if measurement_cell is None:
            continue

        candidates = []
        for cell in selection_cells:
            attempt = _run_cell(
                db,
                study=study,
                version=version,
                experiment=experiment,
                user=user,
                cell=cell,
            )
            if attempt.status == "completed" and isinstance(
                (attempt.metrics or {}).get("total_return_pct"), (int, float)
            ):
                candidates.append(attempt)

        if not candidates:
            attempt = _reusable_attempt(db, study, measurement_cell["cell_key"])
            if attempt is None:
                attempt = RobustnessAttempt(
                    study_id=study.id,
                    cell_key=measurement_cell["cell_key"],
                    label=measurement_cell["label"],
                    role="measurement",
                    spec=measurement_cell["spec"],
                    overrides={"selected_from": None},
                    period_start=measurement_cell.get("period_start"),
                    period_end=measurement_cell.get("period_end"),
                    status="pending",
                )
                db.add(attempt)
            if attempt.status != "completed":
                # Without a usable train result there is nothing to select; do
                # not invent parameters and call the test result a measurement.
                attempt.status = "skipped"
                attempt.error_details = (
                    "訓練期沒有可用的回測結果，因此沒有在測試期量測。"
                )
                attempt.completed_at = utcnow()
            db.commit()
            continue

        best = max(
            candidates,
            key=lambda a: (
                float(a.metrics["total_return_pct"]),
                -_candidate_distance(a.overrides or {}, base_params),
            ),
        )
        selected = dict(best.overrides or {})
        measurement_spec = _with_overrides(measurement_cell["spec"], selected)
        cell = {
            **measurement_cell,
            "spec": measurement_spec,
            "overrides": {
                "fold": fold_id,
                "selected_from": best.cell_key,
                "selected": selected,
            },
        }
        _run_cell(
            db,
            study=study,
            version=version,
            experiment=experiment,
            user=user,
            cell=cell,
        )


def compute_aggregate(
    study: RobustnessStudy, attempts: List[RobustnessAttempt]
) -> Dict[str, Any]:
    is_walk_forward = study.kind == "walk_forward"
    measured = (
        [a for a in attempts if a.role == "measurement"]
        if is_walk_forward
        else [a for a in attempts if a.role != "selection"]
    )
    completed = [
        a
        for a in measured
        if a.status == "completed"
        and isinstance((a.metrics or {}).get("total_return_pct"), (int, float))
    ]
    returns = [float(a.metrics["total_return_pct"]) for a in completed]
    drawdowns = [
        float(a.metrics["max_drawdown_pct"])
        for a in completed
        if isinstance((a.metrics or {}).get("max_drawdown_pct"), (int, float))
    ]
    counts = {
        status: sum(1 for a in attempts if a.status == status)
        for status in ("pending", "completed", "failed", "skipped")
    }

    stats = None
    if returns:
        stats = {
            "metric": "total_return_pct",
            "count": len(returns),
            "median": round(statistics.median(returns), 4),
            "mean": round(statistics.fmean(returns), 4),
            "min": round(min(returns), 4),
            "max": round(max(returns), 4),
            "spread": round(max(returns) - min(returns), 4),
            "positive_count": sum(1 for r in returns if r > 0),
        }

    def summarize(attempt: Optional[RobustnessAttempt]) -> Optional[Dict[str, Any]]:
        if attempt is None:
            return None
        return {
            "cell_key": attempt.cell_key,
            "label": attempt.label,
            "total_return_pct": float(attempt.metrics["total_return_pct"]),
        }

    best = (
        max(completed, key=lambda a: float(a.metrics["total_return_pct"]))
        if completed
        else None
    )
    worst = (
        min(completed, key=lambda a: float(a.metrics["total_return_pct"]))
        if completed
        else None
    )

    folds = None
    if is_walk_forward:
        folds = []
        for attempt in measured:
            selected = (attempt.overrides or {}).get("selected")
            folds.append(
                {
                    "fold": attempt.cell_key.split(":")[1] if ":" in attempt.cell_key else None,
                    "period": {
                        "start_date": attempt.period_start,
                        "end_date": attempt.period_end,
                    },
                    "selected": selected,
                    "selected_from": (attempt.overrides or {}).get("selected_from"),
                    "status": attempt.status,
                    "total_return_pct": (
                        float(attempt.metrics["total_return_pct"])
                        if attempt.status == "completed"
                        and isinstance((attempt.metrics or {}).get("total_return_pct"), (int, float))
                        else None
                    ),
                }
            )

    return {
        "scope": "measurement_only" if is_walk_forward else "all_attempts",
        "attempts_total": len(attempts),
        "counts": counts,
        "return_stats": stats,
        "drawdown_median": round(statistics.median(drawdowns), 4) if drawdowns else None,
        "best_attempt": summarize(best),
        "worst_attempt": summarize(worst),
        "selection_rule": (study.plan or {}).get("selection_rule"),
        "note": (
            "最佳單一結果不是穩健性證據：請同時看中位數、最差結果、分散程度與失敗次數。"
        ),
        "folds": folds,
    }


def execute_study(
    db: Session,
    *,
    study: RobustnessStudy,
    version: ExperimentVersion,
    experiment: Experiment,
    user: User,
) -> RobustnessStudy:
    if study.status == "completed":
        return study
    if study.status == "cancelled":
        raise RobustnessRefused("此研究已取消，不會再執行剩餘嘗試。")

    plan_cells = (study.plan or {}).get("cells") or []
    if not plan_cells:
        raise RobustnessRefused("此研究沒有計劃內容，無法執行。")

    study.status = "running"
    study.started_at = study.started_at or utcnow()
    study.completed_at = None
    study.error_details = None
    db.commit()

    emit_event(
        db,
        entity_type="experiment",
        entity_id=experiment.id,
        event_type="robustness_study_started",
        actor_id=user.id,
        payload={
            "study_id": str(study.id),
            "kind": study.kind,
            "estimated_attempts": len(plan_cells),
        },
        rules_locked=experiment.status == "locked",
    )
    db.commit()

    quota_stopped = False
    try:
        if study.kind == "walk_forward":
            _execute_walk_forward(
                db,
                study=study,
                version=version,
                experiment=experiment,
                user=user,
            )
        else:
            for cell in plan_cells:
                db.refresh(study)
                if study.status == "cancelled":
                    break
                _run_cell(
                    db,
                    study=study,
                    version=version,
                    experiment=experiment,
                    user=user,
                    cell=cell,
                )
    except QuotaExhausted:
        quota_stopped = True

    db.refresh(study)
    attempts = (
        db.query(RobustnessAttempt)
        .filter(RobustnessAttempt.study_id == study.id)
        .all()
    )
    completed = sum(1 for a in attempts if a.status == "completed")
    unresolved = sum(1 for a in attempts if a.status in ("pending", "skipped"))

    if study.status == "cancelled":
        study.cancelled_at = study.cancelled_at or utcnow()
        final_status = "cancelled"
    elif quota_stopped:
        final_status = "partial"
        study.error_details = "研究執行額度不足，剩餘嘗試沒有執行；已完成的結果保留，可在額度恢復後續跑。"
    elif len(attempts) < len(plan_cells) or unresolved:
        final_status = "partial"
        if not study.error_details:
            study.error_details = "部分嘗試未能執行，已完成的結果保留。"
    elif completed == len(attempts):
        final_status = "completed"
        study.completed_at = utcnow()
    elif completed == 0:
        final_status = "failed"
        study.completed_at = utcnow()
    else:
        final_status = "partial"

    study.status = final_status
    study.aggregate = compute_aggregate(study, attempts)

    emit_event(
        db,
        entity_type="experiment",
        entity_id=experiment.id,
        event_type=(
            "robustness_study_completed" if final_status == "completed"
            else "robustness_study_stopped"
        ),
        actor_id=user.id,
        payload={
            "study_id": str(study.id),
            "kind": study.kind,
            "status": final_status,
            "attempts_completed": completed,
            "attempts_total": len(attempts),
        },
        rules_locked=experiment.status == "locked",
    )
    db.commit()
    db.refresh(study)
    return study


def attempt_payload(attempt: RobustnessAttempt) -> Dict[str, Any]:
    return {
        "id": str(attempt.id),
        "cell_key": attempt.cell_key,
        "label": attempt.label,
        "role": attempt.role,
        "overrides": attempt.overrides,
        "period_start": attempt.period_start,
        "period_end": attempt.period_end,
        "run_id": str(attempt.run_id) if attempt.run_id else None,
        "status": attempt.status,
        "metrics": attempt.metrics,
        "dataset_checksum": attempt.dataset_checksum,
        "data_identity": attempt.data_identity,
        "error_details": attempt.error_details,
        "completed_at": attempt.completed_at.isoformat() if attempt.completed_at else None,
    }


def study_payload(study: RobustnessStudy, attempts: List[RobustnessAttempt]) -> Dict[str, Any]:
    return {
        "id": str(study.id),
        "experiment_id": str(study.experiment_id),
        "version_id": str(study.version_id),
        "kind": study.kind,
        "status": study.status,
        "config": study.config,
        "plan": study.plan,
        "aggregate": compute_aggregate(study, attempts),
        "error_details": study.error_details,
        "created_at": study.created_at.isoformat() if study.created_at else None,
        "started_at": study.started_at.isoformat() if study.started_at else None,
        "completed_at": study.completed_at.isoformat() if study.completed_at else None,
        "cancelled_at": study.cancelled_at.isoformat() if study.cancelled_at else None,
        "attempts": [attempt_payload(a) for a in attempts],
    }
