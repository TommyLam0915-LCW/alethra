"""Read-only strategy template catalogue and spec preflight.

Both endpoints answer from the contract the create path already uses: the
StrategySpec schema for fields and the engine's signal-parameter rules for
template parameters. Neither endpoint writes a row, consumes AI usage,
dispatches a job, calls a provider, or executes a strategy.
"""
import typing
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ValidationError

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

class SignalParameterContract(BaseModel):
    name: str
    type: str
    min: Optional[float] = None
    max: Optional[float] = None
    required: bool = True
    unit: str
    conflict: Optional[str] = None


class SpecFieldContract(BaseModel):
    name: str
    type: str
    required: bool
    executed_by_engine: bool
    semantics: str
    default: Optional[Any] = None
    constraints: Optional[Dict[str, Any]] = None


class TemplateEntry(BaseModel):
    name: str
    label: str
    purpose: str
    required_fields: List[str]
    signal_parameters: List[SignalParameterContract]
    known_limits: List[str]
    supported: bool = True


class UnsupportedTemplate(BaseModel):
    name: str
    label: str
    reason: str


class SectionKey(BaseModel):
    key: str
    unit: str
    note: str


class SectionContract(BaseModel):
    section: str
    executed_by_engine: bool
    note: str
    keys: List[SectionKey] = []


class TemplateListResponse(BaseModel):
    templates: List[TemplateEntry]
    unsupported: List[UnsupportedTemplate]
    spec_fields: List[SpecFieldContract]
    sections: List[SectionContract]
    scope_note: str


class ValidationIssue(BaseModel):
    field: str
    message: str


class PreflightResponse(BaseModel):
    status: Literal["valid", "invalid", "unsupported"]
    template_type: Optional[str] = None
    errors: List[ValidationIssue] = []
    reason: Optional[str] = None
    scope_note: str


# Which spec blocks the engine reads. Derived from the engine modules, not from
# what the schema happens to store: entry_rule, exit_rule, sizing_rule and
# execution_timing are persisted and diffed between versions but no engine
# module reads them, so they must never be described as active risk controls.
_EXECUTED_FIELDS = {
    "template_type",
    "asset_ids",
    "frequency",
    "start_date",
    "end_date",
    "initial_cash",
    "signal_parameters",
    "risk_parameters",
    "commission_rate",
    "slippage_rate",
    "benchmark_id",
}

_FIELD_SEMANTICS: Dict[str, str] = {
    "schema_version": "Stored label on the spec; the engine does not read it.",
    "template_type": "Selects the engine implementation; an unknown value is refused.",
    "asset_ids": "Every id is loaded for bars; the daily engine trades only the first one.",
    "frequency": "Must be 1d on the batch path; intraday specs are refused there.",
    "start_date": "Inclusive first date of the window the engine loads bars for.",
    "end_date": "Inclusive last date of that window.",
    "initial_cash": "Starting cash used for position size and the equity curve.",
    "signal_parameters": "Read by the selected template; keys and bounds are listed per template.",
    "entry_rule": "Stored and diffed between versions; the template decides entries.",
    "exit_rule": "Stored and diffed; exits come from the template plus the risk_parameters levels.",
    "sizing_rule": "Stored and diffed; the template decides size from initial_cash.",
    "risk_parameters": "Executed as a stop/target plan; see the risk_parameters section.",
    "execution_timing": "Stored only; entries always execute at the next session's open.",
    "commission_rate": "Charged on every fill as a fraction of notional.",
    "slippage_rate": "Moves the execution price; zero is reported as a warning.",
    "benchmark_id": "Loaded and aligned for the benchmark curve; unavailable data is warned, never filled in.",
    "currency": "Stored label; the engine treats prices in one unit of account.",
    "user_approved_at": "Recorded on the spec; it does not gate or change execution.",
}

_SIGNAL_PARAMETER_UNITS: Dict[str, str] = {
    "short_window": "sessions (integer count)",
    "long_window": "sessions (integer count)",
    "lookback_days": "sessions (integer count)",
    "threshold_pct": "fraction of price (0.01 = 1%)",
    "holding_days": "sessions after execution",
}

_SIGNAL_PARAMETER_CONFLICTS: Dict[str, str] = {
    "short_window": "Must be strictly below long_window, or the run refuses.",
    "long_window": "Must be strictly above short_window; at least long_window + 1 bars are needed or the run reports no trades.",
    "lookback_days": "At least lookback_days + 1 bars are needed or the run reports no trades.",
    "holding_days": "The fixed hold counts from the execution session, not the signal session.",
}

_RISK_PARAMETER_KEYS = [
    SectionKey(
        key="stop_price",
        unit="price (same unit as the bars)",
        note="Absolute stop level; for a long position it must sit below the entry price.",
    ),
    SectionKey(
        key="stop_pct",
        unit="fraction of entry (0.05 = 5%)",
        note="Converted to stop_price = entry * (1 - stop_pct); ignored when stop_price is set.",
    ),
    SectionKey(
        key="target_price",
        unit="price (same unit as the bars)",
        note="Absolute target level; it must sit above the entry price.",
    ),
    SectionKey(
        key="target_pct",
        unit="fraction of entry (0.02 = 2%)",
        note="Converted to target_price = entry * (1 + target_pct); ignored when target_price is set.",
    ),
]


def _sections() -> List[SectionContract]:
    """The section-level contract, with the engine's actual read path as source."""
    return [
        SectionContract(
            section="signal_parameters",
            executed_by_engine=True,
            note=(
                "Read by the selected template. Unknown keys are stored but the "
                "engine does not read them."
            ),
        ),
        SectionContract(
            section="risk_parameters",
            executed_by_engine=True,
            note=(
                "Executed as a long-only stop/target plan. A price key wins over the "
                "matching percent key. A stop at or above the entry, or a target at "
                "or below it, is not simulated and is reported as a warning. An "
                "absent block means no stop and no target, not zero."
            ),
            keys=_RISK_PARAMETER_KEYS,
        ),
        SectionContract(
            section="entry_rule",
            executed_by_engine=False,
            note="Stored and diffed between versions; the template decides entries.",
        ),
        SectionContract(
            section="exit_rule",
            executed_by_engine=False,
            note=(
                "Stored and diffed; exits come from the template logic plus the "
                "risk_parameters levels, never from this block."
            ),
        ),
        SectionContract(
            section="sizing_rule",
            executed_by_engine=False,
            note="Stored and diffed; position size is decided by the template from initial_cash.",
        ),
        SectionContract(
            section="execution_timing",
            executed_by_engine=False,
            note=(
                "Stored only. Signal-driven entries always execute at the next "
                "session's open; changing this block does not change execution."
            ),
        ),
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
            "executed_by_engine": name in _EXECUTED_FIELDS,
            "semantics": _FIELD_SEMANTICS.get(name, "Stored; the engine does not read it."),
            "default": None if field.is_required() else field.default,
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
            "unit": _SIGNAL_PARAMETER_UNITS.get(param, "see the template bounds"),
            "conflict": _SIGNAL_PARAMETER_CONFLICTS.get(param),
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


@router.get(
    "/strategy-templates",
    response_model=TemplateListResponse,
    summary="List strategy templates and their field contract",
    description=(
        "Read-only catalogue of the implemented templates plus the StrategySpec "
        "field contract. Each field and section states whether the engine reads "
        "it: entry_rule, exit_rule, sizing_rule and execution_timing are stored "
        "and diffed between versions but no engine module consumes them, while "
        "signal_parameters and risk_parameters are executed. Lists no traded "
        "instrument, provider entitlement or execution authorization."
    ),
)
def list_strategy_templates(user: User = Depends(get_current_user)):
    return {
        "templates": [_template_entry(name) for name in ALLOWED_TEMPLATES],
        "unsupported": UNSUPPORTED_TEMPLATES,
        "spec_fields": _field_contract(),
        "sections": _sections(),
        "scope_note": SCOPE_NOTE,
    }


@router.post(
    "/strategy-specs/preflight",
    response_model=PreflightResponse,
    summary="Check a strategy spec against the engine contract",
    description=(
        "Read-only check of a StrategySpec-shaped object. Returns status "
        "'valid', 'invalid' (with per-field errors) or 'unsupported' (template "
        "or timeframe not implemented). It does not create an experiment, "
        "version or run, load market data, consume quota, or authorize "
        "execution."
    ),
)
def preflight_strategy_spec(
    spec: Dict[str, Any] = Body(
        ...,
        description=(
            "A StrategySpec-shaped object. It is validated rather than used to "
            "create anything, so it may be incomplete or malformed: the errors "
            "come back in the response body instead of a 422."
        ),
    ),
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
