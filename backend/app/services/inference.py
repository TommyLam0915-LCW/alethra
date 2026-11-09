"""Module E: Model execution runtime — inference engine."""
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from sqlalchemy.orm import Session

from app.models.inference import InferenceRun, InferenceRunStatus
from app.models.model_research import ModelVersion, ModelType
from app.providers import get_data_provider
from app.lib.timeutil import utcnow


def _compute_sma(closes: List[float], window: int) -> List[Optional[float]]:
    """Compute simple moving average. Returns list aligned with closes (None where insufficient data)."""
    result: List[Optional[float]] = []
    for i in range(len(closes)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - window + 1 : i + 1]) / window)
    return result


def _evaluate_rule_based(
    rules: List[Dict[str, Any]],
    bars: List[Any],
) -> List[Dict[str, Any]]:
    """Evaluate rule-based model specification against market bars.

    Supported rule format (in model_specification_json['rules']):
      {"condition": "close > sma_20", "then": "BUY"}
      {"condition": "close < sma_20", "then": "SELL"}
      (default -> HOLD)

    Each bar gets a signal based on the first matching rule.
    """
    if not bars:
        return []

    closes = [bar.close for bar in bars]
    dates = [bar.date for bar in bars]

    # Pre-compute indicator series referenced in rules
    indicator_cache: Dict[str, List[Optional[float]]] = {}
    for rule in rules:
        condition = rule.get("condition", "")
        for token in _extract_indicator_tokens(condition):
            if token not in indicator_cache:
                series = _compute_indicator(token, closes)
                indicator_cache[token] = series

    signals: List[Dict[str, Any]] = []
    for i, bar in enumerate(bars):
        signal = "HOLD"
        rationale_parts: List[str] = []

        for rule in rules:
            condition = rule.get("condition", "")
            then_action = rule.get("then", "HOLD").upper()
            if then_action not in ("BUY", "SELL", "HOLD"):
                then_action = "HOLD"

            if _evaluate_condition(condition, i, closes, indicator_cache):
                signal = then_action
                rationale_parts.append(f"Rule matched: {condition} -> {then_action}")
                break

        if not rationale_parts:
            rationale_parts.append("No rule matched; default HOLD")

        signals.append({
            "date": dates[i],
            "signal": signal,
            "confidence": 0.5 if signal == "HOLD" else 0.7,
            "rationale": "; ".join(rationale_parts),
        })

    return signals


def _extract_indicator_tokens(condition: str) -> List[str]:
    """Extract indicator tokens like 'sma_20' from a condition string."""
    import re
    return re.findall(r"sma_\d+", condition)


def _compute_indicator(token: str, closes: List[float]) -> List[Optional[float]]:
    """Compute an indicator series from a token like 'sma_20'."""
    parts = token.split("_")
    if parts[0] == "sma" and len(parts) == 2:
        window = int(parts[1])
        return _compute_sma(closes, window)
    return [None] * len(closes)


def _evaluate_condition(
    condition: str,
    index: int,
    closes: List[float],
    indicator_cache: Dict[str, List[Optional[float]]],
) -> bool:
    """Evaluate a single condition string at a given bar index."""
    import re

    condition = condition.strip()
    if not condition:
        return False

    # Parse patterns like "close > sma_20", "close < sma_10"
    match = re.match(r"(close)\s*([><=]+)\s*(sma_\d+)", condition)
    if match:
        left_val = closes[index]
        right_token = match.group(3)
        op = match.group(2)

        series = indicator_cache.get(right_token)
        if series is None or index >= len(series) or series[index] is None:
            return False
        right_val = series[index]

        if op == ">":
            return left_val > right_val
        elif op == "<":
            return left_val < right_val
        elif op == ">=":
            return left_val >= right_val
        elif op == "<=":
            return left_val <= right_val
        elif op == "==" or op == "=":
            return left_val == right_val

    # Unknown condition format — cannot evaluate
    return False


def run_inference(
    db: Session,
    model_version_id: uuid.UUID,
    user_id: uuid.UUID,
    input_spec: Dict[str, Any],
) -> InferenceRun:
    """Create and execute an inference run.

    input_spec should contain:
      - symbol: str (e.g. "DEMO-ALPHA")
      - start_date: str (e.g. "2024-01-02")
      - end_date: str (e.g. "2024-02-13")
      - parameters: dict (optional, passed through to rules)
    """
    model_version = db.query(ModelVersion).filter(ModelVersion.id == model_version_id).first()
    if not model_version:
        raise ValueError(f"Model version {model_version_id} not found")

    run = InferenceRun(
        model_version_id=model_version_id,
        user_id=user_id,
        input_spec=input_spec,
        status=InferenceRunStatus.pending,
    )
    db.add(run)
    db.flush()

    # Transition to running
    run.status = InferenceRunStatus.running
    db.flush()

    model_type = model_version.model_type
    if isinstance(model_type, str):
        model_type_enum = ModelType(model_type)
    else:
        model_type_enum = model_type

    try:
        if model_type_enum == ModelType.prompt_based_research_assistant:
            run.output_signals = [
                {
                    "date": input_spec.get("end_date", ""),
                    "signal": "HOLD",
                    "confidence": 1.0,
                    "rationale": "Prompt-based models do not execute — descriptive only",
                }
            ]
            run.status = InferenceRunStatus.completed

        elif model_type_enum == ModelType.future_external_model_reference:
            run.status = InferenceRunStatus.failed
            run.error_details = "External model references are not yet executable"

        elif model_type_enum == ModelType.rule_based:
            symbol = input_spec.get("symbol")
            start_date = input_spec.get("start_date")
            end_date = input_spec.get("end_date")

            if not symbol or not start_date or not end_date:
                raise ValueError("input_spec requires symbol, start_date, and end_date")

            provider = get_data_provider()
            bars = provider.get_bars(symbol, start_date, end_date)

            if not bars:
                raise ValueError(f"No data available for {symbol} from {start_date} to {end_date}")

            spec = model_version.model_specification_json or {}
            rules = spec.get("rules", [])

            signals = _evaluate_rule_based(rules, bars)
            run.output_signals = signals
            run.status = InferenceRunStatus.completed

        else:
            run.status = InferenceRunStatus.failed
            run.error_details = f"Unsupported model type: {model_type_enum}"

    except Exception as e:
        run.status = InferenceRunStatus.failed
        run.error_details = str(e)

    run.completed_at = utcnow()
    db.flush()
    return run
