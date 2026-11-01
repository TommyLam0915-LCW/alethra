"""Read-only strategy template catalogue and spec preflight.

Both endpoints answer from the contract the create path already uses: the
StrategySpec schema for fields and the engine's signal-parameter rules for
template parameters. Neither endpoint writes a row, consumes AI usage,
dispatches a job, calls a provider, or executes a strategy.
"""
import typing
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import ValidationError

from app.api.schemas import ALLOWED_TEMPLATES, StrategySpec
from app.auth.jwt import get_current_user
from app.engine.backtest import SIGNAL_PARAMETER_RULES, validate_signal_parameters
from app.engine.intraday import INTRADAY_TIMEFRAMES
from app.models.user import User

router = APIRouter(tags=["strategy-templates"])

SCOPE_NOTE = (
    "A contract match only: it does not establish data availability, strategy "
    "behaviour, expected returns, or authorization to execute."
)

TEMPLATE_COPY: Dict[str, Dict[str, Any]] = {
    "buy_and_hold": {
        "label": "Buy and hold",
        "purpose": (
            "Buy the instrument at the first tradable session open, hold it, and "
            "exit only on a declared stop or target, or at the end of the data."
        ),
        "known_limits": [
            "Daily bars only; the batch backtest refuses intraday specs.",
            "Long-only: the engine does not place short sales.",
            "Only the first asset in asset_ids is traded.",
            "entry_rule, exit_rule and sizing_rule are stored but not interpreted; "
            "the template logic decides the trades.",
            "No signal parameters are required for this template.",
        ],
    },
    "sma_crossover": {
        "label": "SMA crossover",
        "purpose": (
            "Go long when the short simple moving average crosses above the long "
            "one, and exit on a cross-down, a declared stop or target, or the end "
            "of the data."
        ),
        "known_limits": [
            "Daily bars only; the batch backtest refuses intraday specs.",
            "Long-only: the engine does not place short sales.",
            "Only the first asset in asset_ids is traded.",
            "short_window must be below long_window; otherwise the run refuses.",
            "At least long_window + 1 bars are required or the run reports no trades.",
            "entry_rule, exit_rule and sizing_rule are stored but not interpreted.",
        ],
    },
    "price_change_fixed_hold": {
        "label": "Price change, fixed hold",
        "purpose": (
            "Buy after a close-to-close percentage move over a lookback window, "
            "then exit after a fixed number of sessions, a declared stop or "
            "target, or the end of the data."
        ),
        "known_limits": [
            "Daily bars only; the batch backtest refuses intraday specs.",
            "Long-only: the engine does not place short sales.",
            "Only the first asset in asset_ids is traded.",
            "threshold_pct is a fraction, not a percentage number (0.01 to 0.50).",
            "At least lookback_days + 1 bars are required or the run reports no trades.",
            "entry_rule, exit_rule and sizing_rule are stored but not interpreted.",
        ],
    },
}

UNSUPPORTED_TEMPLATES: List[Dict[str, str]] = [
    {
        "name": "mean_reversion_zscore",
        "label": "Mean reversion (z-score)",
        "reason": "No mean-reversion engine is implemented; a spec using it would not run.",
    },
    {
        "name": "breakout_channel",
        "label": "Channel breakout",
        "reason": "No breakout engine is implemented; a spec using it would not run.",
    },
    {
        "name": "cross_sectional_ranking",
        "label": "Cross-sectional ranking",
        "reason": "The daily engine trades one asset at a time; no cross-sectional engine is implemented.",
    },
]

_CONSTRAINT_ATTRS = ("pattern", "min_length", "max_length", "gt", "ge", "lt", "le")


def _type_name(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union:
        present = [arg for arg in args if arg is not type(None)]
        return f"{_type_name(present[0])} or null" if present else "null"
    if origin in (list, List):
        return f"list of {_type_name(args[0])}" if args else "list"
    if origin is dict:
        return "object"
    simple = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}
    if annotation in simple:
        return simple[annotation]
    return getattr(annotation, "__name__", str(annotation))


def _field_contract() -> List[Dict[str, Any]]:
    """The StrategySpec fields, read from the schema itself rather than restated."""
    fields = []
    for name, field in StrategySpec.model_fields.items():
        entry: Dict[str, Any] = {
            "name": name,
            "type": _type_name(field.annotation),
            "required": field.is_required(),
        }
        constraints = {}
        for meta in field.metadata:
            for attr in _CONSTRAINT_ATTRS:
                value = getattr(meta, attr, None)
                if value is not None:
                    constraints[attr] = value
        if constraints:
            entry["constraints"] = constraints
        fields.append(entry)
    return fields


def _template_entry(name: str) -> Dict[str, Any]:
    copy = TEMPLATE_COPY[name]
    signal_parameters = [
        {
            "name": param,
            "type": rule["type"],
            "min": rule["min"],
            "max": rule["max"],
            "required": True,
        }
        for param, rule in SIGNAL_PARAMETER_RULES[name].items()
    ]
    return {
        "name": name,
        "label": copy["label"],
        "purpose": copy["purpose"],
        "required_fields": [
            field_name
            for field_name, field in StrategySpec.model_fields.items()
            if field.is_required()
        ],
        "signal_parameters": signal_parameters,
        "known_limits": copy["known_limits"],
        "supported": True,
    }


def _validation_errors(exc: ValidationError) -> List[Dict[str, str]]:
    errors = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ())) or "spec"
        errors.append({"field": field, "message": error.get("msg", "Invalid value")})
    return errors


def _result(status: str, template_type: Optional[str], errors=None, reason=None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": status,
        "template_type": template_type,
        "errors": errors or [],
        "scope_note": SCOPE_NOTE,
    }
    if reason:
        payload["reason"] = reason
    return payload


@router.get("/strategy-templates")
def list_strategy_templates(user: User = Depends(get_current_user)):
    return {
        "templates": [_template_entry(name) for name in ALLOWED_TEMPLATES],
        "unsupported": UNSUPPORTED_TEMPLATES,
        "spec_fields": _field_contract(),
        "scope_note": SCOPE_NOTE,
    }


@router.post("/strategy-specs/preflight")
def preflight_strategy_spec(
    spec: Dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
):
    template_type = spec.get("template_type")
    if isinstance(template_type, str) and template_type not in ALLOWED_TEMPLATES:
        return _result(
            "unsupported",
            template_type,
            reason=f"Template '{template_type}' is not implemented, so no run can use it.",
        )

    try:
        parsed = StrategySpec.model_validate(spec)
    except ValidationError as exc:
        return _result(
            "invalid",
            template_type if isinstance(template_type, str) else None,
            errors=_validation_errors(exc),
        )

    timeframe = str(spec.get("timeframe", "1d"))
    if timeframe in INTRADAY_TIMEFRAMES:
        return _result(
            "unsupported",
            parsed.template_type,
            errors=[
                {
                    "field": "timeframe",
                    "message": (
                        "The batch backtest does not execute intraday specs; use the "
                        "per-bar forward replay instead."
                    ),
                }
            ],
            reason="This capability is not supported on this path.",
        )

    signal_errors = validate_signal_parameters(parsed.template_type, parsed.signal_parameters)
    if signal_errors:
        return _result(
            "invalid",
            parsed.template_type,
            errors=[
                {"field": "signal_parameters", "message": message}
                for message in signal_errors
            ],
        )

    return _result("valid", parsed.template_type)
