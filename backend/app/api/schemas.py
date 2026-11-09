from datetime import datetime
from typing import Any, Optional, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALLOWED_TEMPLATES = ("buy_and_hold", "sma_crossover", "price_change_fixed_hold")
ALLOWED_DEMO_ASSETS = ("DEMO-ALPHA", "DEMO-BETA", "DEMO-BENCHMARK")


class StrategySpec(BaseModel):
    schema_version: str = "1.0"
    template_type: str = Field(..., pattern="^(buy_and_hold|sma_crossover|price_change_fixed_hold)$")
    asset_ids: List[str] = Field(..., min_length=1)
    frequency: str = Field(default="1d", pattern="^1d$")
    start_date: str
    end_date: str
    initial_cash: float = Field(..., gt=0)
    signal_parameters: dict = Field(default_factory=dict)
    entry_rule: dict = Field(default_factory=dict)
    exit_rule: dict = Field(default_factory=dict)
    sizing_rule: dict = Field(default_factory=dict)
    execution_timing: dict = Field(default_factory=lambda: {"execute_at": "next_open"})
    commission_rate: float = Field(default=0.0, ge=0, le=0.05)
    slippage_rate: float = Field(default=0.0, ge=0, le=0.05)
    benchmark_id: Optional[str] = None
    currency: str = Field(default="USD", pattern="^USD$")
    user_approved_at: Optional[str] = None

    @field_validator("asset_ids")
    @classmethod
    def validate_asset_ids(cls, v):
        for aid in v:
            if aid not in ALLOWED_DEMO_ASSETS:
                raise ValueError(f"Unknown asset_id: {aid}. Allowed: {ALLOWED_DEMO_ASSETS}")
        return v

    @field_validator("benchmark_id")
    @classmethod
    def validate_benchmark_id(cls, v):
        if v is not None and v not in ALLOWED_DEMO_ASSETS:
            raise ValueError(f"Unknown benchmark_id: {v}. Allowed: {ALLOWED_DEMO_ASSETS}")
        return v

    @field_validator("end_date")
    @classmethod
    def validate_dates(cls, v, info):
        if "start_date" in info.data and v <= info.data["start_date"]:
            raise ValueError("end_date must be after start_date")
        return v


class ExperimentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    original_idea: Optional[str] = None
    research_type: str = Field(default="quantitative", pattern="^(quantitative|fundamental)$")
    hypothesis: Optional[str] = None
    falsification_conditions: Optional[str] = None
    benchmark: Optional[str] = None
    scope: Optional[dict] = None
    strategy_spec: StrategySpec


class ExperimentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    original_idea: Optional[str] = None
    hypothesis: Optional[str] = None
    falsification_conditions: Optional[str] = None
    benchmark: Optional[str] = None
    scope: Optional[dict] = None
    strategy_spec: Optional[StrategySpec] = None
    change_reason: Optional[str] = None


class ExperimentResponse(BaseModel):
    id: UUID
    owner_id: UUID
    workspace_id: Optional[UUID]
    title: str
    original_idea: Optional[str]
    research_type: str
    hypothesis: Optional[str]
    falsification_conditions: Optional[str]
    benchmark: Optional[str]
    scope: Optional[dict]
    status: str
    visibility: str
    current_version_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExperimentVersionResponse(BaseModel):
    id: UUID
    experiment_id: UUID
    version_number: int
    strategy_spec: dict
    assumptions: Optional[dict]
    change_reason: Optional[str]
    created_by: UUID
    created_at: datetime
    user_approved_at: Optional[datetime]
    config_hash: Optional[str]
    locked_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ResearchRunResponse(BaseModel):
    id: UUID
    experiment_version_id: UUID
    dataset_snapshot_id: Optional[UUID]
    dataset_checksum: Optional[str]
    engine_version: str
    config_hash: str
    status: str
    metrics: Optional[dict]
    warnings: Optional[list]
    methodology_json: Optional[dict]
    error_details: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ResearchNoteCreate(BaseModel):
    content: str = Field(..., min_length=1)
    version_id: Optional[UUID] = None


class ResearchNoteResponse(BaseModel):
    id: UUID
    experiment_id: UUID
    version_id: Optional[UUID]
    author_id: UUID
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditEventResponse(BaseModel):
    id: UUID
    entity_type: str
    entity_id: str
    event_type: str
    actor_id: Optional[UUID]
    actor_type: str
    payload_json: Optional[dict]
    recorded_before_outcome: bool
    rules_locked: bool
    data_cutoff_at: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VersionDiffEntry(BaseModel):
    field: str
    old_value: Any
    new_value: Any


class VersionDiffResponse(BaseModel):
    version_a_id: UUID
    version_b_id: UUID
    version_a_number: int
    version_b_number: int
    diffs: List[VersionDiffEntry]


class SimulationSessionCreate(BaseModel):
    initial_virtual_cash: float = Field(..., gt=0)


class SimulationSessionResponse(BaseModel):
    id: UUID
    experiment_version_id: UUID
    owner_id: UUID
    initial_virtual_cash: float
    started_at: Optional[datetime]
    data_cutoff_at: str
    current_snapshot_date: Optional[str]
    status: str
    simulation_policy_version: str
    paper_equity: Optional[float]
    current_position_json: Optional[dict]
    warnings: Optional[list]
    total_decisions: int
    observed_days: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimulationDecisionResponse(BaseModel):
    id: UUID
    session_id: UUID
    step_number: int
    snapshot_date: str
    data_cutoff_at: str
    engine_version: str
    decision_type: str
    decision_detail: Optional[dict]
    paper_cash: float
    paper_position: Optional[dict]
    paper_equity: float
    warnings: Optional[list]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimulationAdvanceResponse(BaseModel):
    session: SimulationSessionResponse
    decision: SimulationDecisionResponse


class PaperPortfolioCreate(BaseModel):
    initial_virtual_cash: float = Field(..., gt=0)
    currency: str = Field(default="USD", pattern="^USD$")
    simulation_session_id: Optional[UUID] = None


class PaperPortfolioResponse(BaseModel):
    id: UUID
    owner_id: UUID
    simulation_session_id: Optional[UUID]
    currency: str
    initial_virtual_cash: float
    current_virtual_cash: float
    status: str
    created_at: datetime
    closed_at: Optional[datetime]
    total_realized_pnl: float
    total_unrealized_pnl: float

    model_config = ConfigDict(from_attributes=True)


class PaperPositionResponse(BaseModel):
    id: UUID
    portfolio_id: UUID
    asset_id: str
    quantity: float
    average_entry_price: float
    opened_at: datetime
    closed_at: Optional[datetime]
    realized_pnl: Optional[float]
    unrealized_pnl: Optional[float]
    status: str

    model_config = ConfigDict(from_attributes=True)


class PaperOrderCreate(BaseModel):
    asset_id: str = Field(..., pattern="^DEMO-")
    side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: float = Field(..., gt=0)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    rationale: Optional[str] = None
    expected_outcome: Optional[str] = None
    expected_timeframe_days: Optional[int] = Field(None, gt=0)
    self_reported_confidence: Optional[str] = Field(
        None, pattern="^(low|medium|high|not_provided)$"
    )
    falsification_condition: Optional[str] = None


class PaperOrderResponse(BaseModel):
    id: UUID
    portfolio_id: UUID
    decision_id: Optional[UUID]
    asset_id: str
    side: str
    quantity: float
    order_type: str
    created_at: datetime
    status: str
    model_or_strategy_version_id: Optional[UUID]
    data_cutoff_at: Optional[str]
    rationale: Optional[str]
    risk_check_status: str

    model_config = ConfigDict(from_attributes=True)


class PaperFillResponse(BaseModel):
    id: UUID
    paper_order_id: UUID
    execution_date: str
    observed_price: float
    execution_price_before_costs: float
    slippage_amount: float
    commission_amount: float
    quantity: float
    gross_amount: float
    net_cash_change: float
    simulated_fill_reason: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class DecisionRecordCreate(BaseModel):
    actor_type: str = Field(
        ..., pattern="^(human|ai_draft|human_approved_ai_draft|system)$"
    )
    decision_type: str = Field(
        ..., pattern="^(no_action|entry|exit|pause|retire|override_request)$"
    )
    simulation_session_id: Optional[UUID] = None
    linked_experiment_version_id: Optional[UUID] = None
    rationale: Optional[str] = None
    expected_outcome: Optional[str] = None
    expected_timeframe_days: Optional[int] = Field(None, gt=0)
    self_reported_confidence: Optional[str] = Field(
        None, pattern="^(low|medium|high|not_provided)$"
    )
    falsification_condition: Optional[str] = None
    risk_flags: Optional[dict] = None


class DecisionRecordResponse(BaseModel):
    id: UUID
    owner_id: UUID
    simulation_session_id: Optional[UUID]
    actor_type: str
    decision_type: str
    decision_time: datetime
    data_cutoff_at: Optional[str]
    linked_experiment_version_id: Optional[UUID]
    linked_model_research_record_id: Optional[UUID]
    rationale: Optional[str]
    expected_outcome: Optional[str]
    expected_timeframe_days: Optional[int]
    self_reported_confidence: str
    falsification_condition: Optional[str]
    risk_flags: Optional[dict]
    outcome_status: str

    model_config = ConfigDict(from_attributes=True)


class OutcomeEvaluationCreate(BaseModel):
    actual_outcome_summary: Optional[str] = None
    net_simulated_return_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    hypothesis_status: Optional[str] = Field(
        None,
        pattern="^(supported|partially_supported|evidence_insufficient|contradicted)$",
    )
    evaluation_method: Optional[str] = None
    user_reflection: Optional[str] = None
    data_quality_flags: Optional[dict] = None


class OutcomeEvaluationResponse(BaseModel):
    id: UUID
    decision_id: UUID
    evaluated_at: datetime
    actual_outcome_summary: Optional[str]
    net_simulated_return_pct: Optional[float]
    max_drawdown_pct: Optional[float]
    hypothesis_status: Optional[str]
    evaluation_method: Optional[str]
    user_reflection: Optional[str]
    data_quality_flags: Optional[dict]

    model_config = ConfigDict(from_attributes=True)


class OverrideRequestCreate(BaseModel):
    original_rationale: str
    override_reason: str
    expected_outcome: Optional[str] = None
    expected_timeframe_days: Optional[int] = Field(None, gt=0)
    self_reported_confidence: Optional[str] = Field(
        None, pattern="^(low|medium|high|not_provided)$"
    )
    falsification_condition: Optional[str] = None


class RiskCheckConfig(BaseModel):
    max_position_pct: float = Field(default=1.0, gt=0, le=1.0)
    max_daily_orders: int = Field(default=10, gt=0)
    allowed_assets: List[str] = Field(default_factory=lambda: list(ALLOWED_DEMO_ASSETS))
    max_drawdown_warning_pct: float = Field(default=0.20, gt=0, le=1.0)


# ── Phase 4: Model Research Records ──────────────────────────────────

ALLOWED_MODEL_STATUSES = (
    "draft",
    "researching",
    "historical_test_complete",
    "locked_for_simulation",
    "forward_observation",
    "evidence_insufficient",
    "retired",
    "revoked",
)

VALID_STATUS_TRANSITIONS = {
    "draft": {"researching", "revoked"},
    "researching": {"historical_test_complete", "evidence_insufficient", "retired", "revoked"},
    "historical_test_complete": {"locked_for_simulation", "evidence_insufficient", "retired", "revoked"},
    "locked_for_simulation": {"forward_observation", "evidence_insufficient", "retired", "revoked"},
    "forward_observation": {"evidence_insufficient", "retired", "revoked"},
    "evidence_insufficient": {"retired", "revoked"},
    "retired": set(),
    "revoked": set(),
}

ALLOWED_MODEL_TYPES = (
    "rule_based",
    "prompt_based_research_assistant",
    "future_external_model_reference",
)

ALLOWED_EVALUATION_TYPES = (
    "historical",
    "forward_simulation",
    "methodology_review",
    "behavior_consistency",
)

ALLOWED_EVALUATOR_TYPES = (
    "system",
    "human",
    "future_ai_assistant",
)

ALLOWED_MEMORY_TYPES = (
    "explicit_preference",
    "approved_constraint",
    "recurring_methodology_issue",
    "reflection",
    "accepted_ai_draft",
    "rejected_ai_draft",
)

ALLOWED_PRIVACY_SCOPES = (
    "private_only",
    "approved_for_personal_ai",
)


class ModelResearchRecordCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    purpose: str = Field(..., min_length=1)
    allowed_asset_universe: Optional[List[str]] = None
    allowed_research_actions: Optional[List[str]] = None

    @field_validator("allowed_asset_universe")
    @classmethod
    def validate_asset_universe(cls, v):
        if v is not None:
            for aid in v:
                if aid not in ALLOWED_DEMO_ASSETS:
                    raise ValueError(f"Unknown asset_id: {aid}. Allowed: {ALLOWED_DEMO_ASSETS}")
        return v


class ModelResearchRecordUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    purpose: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(None, pattern="^(draft|researching|historical_test_complete|locked_for_simulation|forward_observation|evidence_insufficient|retired|revoked)$")
    allowed_asset_universe: Optional[List[str]] = None
    allowed_research_actions: Optional[List[str]] = None

    @field_validator("allowed_asset_universe")
    @classmethod
    def validate_asset_universe(cls, v):
        if v is not None:
            for aid in v:
                if aid not in ALLOWED_DEMO_ASSETS:
                    raise ValueError(f"Unknown asset_id: {aid}. Allowed: {ALLOWED_DEMO_ASSETS}")
        return v


class ModelResearchRecordResponse(BaseModel):
    id: UUID
    owner_id: UUID
    title: str
    description: Optional[str]
    purpose: str
    status: str
    allowed_asset_universe: Optional[list]
    allowed_research_actions: Optional[list]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelVersionCreate(BaseModel):
    model_type: str = Field(..., pattern="^(rule_based|prompt_based_research_assistant|future_external_model_reference)$")
    model_specification_json: dict
    prompt_version: Optional[str] = Field(None, max_length=50)
    input_schema_version: Optional[str] = Field(None, max_length=50)
    output_schema_version: Optional[str] = Field(None, max_length=50)
    linked_experiment_version_ids: Optional[List[UUID]] = None
    linked_dataset_snapshot_ids: Optional[List[UUID]] = None
    linked_simulation_session_ids: Optional[List[UUID]] = None


class ModelVersionResponse(BaseModel):
    id: UUID
    model_research_record_id: UUID
    version_number: int
    model_type: str
    model_specification_json: dict
    prompt_version: Optional[str]
    input_schema_version: Optional[str]
    output_schema_version: Optional[str]
    linked_experiment_version_ids: Optional[list]
    linked_dataset_snapshot_ids: Optional[list]
    linked_simulation_session_ids: Optional[list]
    created_at: datetime
    locked_at: Optional[datetime]
    retired_at: Optional[datetime]
    revoked_at: Optional[datetime]
    revocation_reason: Optional[str]
    model_card_id: Optional[UUID]

    model_config = ConfigDict(from_attributes=True)


class ModelEvaluationCreate(BaseModel):
    model_version_id: UUID
    evaluation_type: str = Field(
        ..., pattern="^(historical|forward_simulation|methodology_review|behavior_consistency)$"
    )
    result_summary: Optional[str] = None
    metrics_json: Optional[dict] = None
    warning_flags: Optional[list] = None
    evaluation_date: str
    data_cutoff_at: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    engine_version: str = Field(..., min_length=1)
    evaluator: str = Field(..., pattern="^(system|human|future_ai_assistant)$")
    methodology_version: Optional[str] = Field(None, max_length=50)


class ModelEvaluationResponse(BaseModel):
    id: UUID
    model_version_id: UUID
    model_research_record_id: UUID
    evaluation_type: str
    result_summary: Optional[str]
    metrics_json: Optional[dict]
    warning_flags: Optional[list]
    evaluation_date: datetime
    data_cutoff_at: str
    engine_version: str
    evaluator: str
    methodology_version: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PersonalResearchMemoryCreate(BaseModel):
    memory_type: str = Field(
        ..., pattern="^(explicit_preference|approved_constraint|recurring_methodology_issue|reflection|accepted_ai_draft|rejected_ai_draft)$"
    )
    content: str = Field(..., min_length=1)
    source_entity_type: Optional[str] = Field(None, max_length=50)
    source_entity_id: Optional[UUID] = None
    privacy_scope: str = Field(
        default="private_only",
        pattern="^(private_only|approved_for_personal_ai)$",
    )
    general_model_training_opt_in: bool = False
    user_confirmed: bool = False

    @field_validator("general_model_training_opt_in")
    @classmethod
    def validate_training_opt_in(cls, v):
        if v is True:
            raise ValueError(
                "general_model_training_opt_in cannot be set to true via API. "
                "This is a user-controlled setting only."
            )
        return v


class PersonalResearchMemoryConfirm(BaseModel):
    confirmed: bool = Field(..., description="User must explicitly confirm or reject")


class PersonalResearchMemoryResponse(BaseModel):
    id: UUID
    owner_id: UUID
    memory_type: str
    content: str
    source_entity_type: Optional[str]
    source_entity_id: Optional[UUID]
    created_at: datetime
    user_confirmed_at: Optional[datetime]
    is_confirmed: bool
    active: bool
    privacy_scope: str
    general_model_training_opt_in: bool

    model_config = ConfigDict(from_attributes=True)


class ModelCardCreate(BaseModel):
    model_version_id: UUID
    purpose: str = Field(..., min_length=1)
    intended_research_use: str = Field(..., min_length=1)
    excluded_use: str = Field(..., min_length=1)
    data_sources: List[str] = Field(..., min_length=1)
    data_cutoff: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    linked_experiments: Optional[List[UUID]] = None
    evaluation_evidence: Optional[dict] = None
    known_limitations: List[str] = Field(..., min_length=1)
    risk_flags: Optional[List[str]] = None
    version_history: Optional[list] = None
    revocation_process: str = Field(..., min_length=1)
    disclaimer: str = Field(..., min_length=1)


class ModelCardUpdate(BaseModel):
    purpose: Optional[str] = Field(None, min_length=1)
    intended_research_use: Optional[str] = Field(None, min_length=1)
    excluded_use: Optional[str] = Field(None, min_length=1)
    data_sources: Optional[List[str]] = Field(None, min_length=1)
    data_cutoff: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    linked_experiments: Optional[List[UUID]] = None
    evaluation_evidence: Optional[dict] = None
    known_limitations: Optional[List[str]] = Field(None, min_length=1)
    risk_flags: Optional[List[str]] = None
    version_history: Optional[list] = None
    revocation_process: Optional[str] = Field(None, min_length=1)
    disclaimer: Optional[str] = Field(None, min_length=1)


class ModelCardResponse(BaseModel):
    id: UUID
    model_version_id: UUID
    purpose: str
    intended_research_use: str
    excluded_use: str
    data_sources: list
    data_cutoff: str
    linked_experiments: Optional[list]
    evaluation_evidence: Optional[dict]
    known_limitations: list
    risk_flags: Optional[list]
    version_history: Optional[list]
    revocation_process: str
    disclaimer: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Phase 5: Model Research Package Export ──

PACKAGE_SCHEMA_VERSION = "1.0.0"

MANDATORY_EXCLUDED_CAPABILITIES = [
    "no_broker_credentials",
    "no_exchange_credentials",
    "no_automatic_execution_instruction",
    "no_buy_sell_hold_command",
    "no_target_weight",
    "no_real_money_allocation_recommendation",
    "no_claim_of_profitability",
    "no_live_deployment_authorization",
]

PACKAGE_DISCLAIMER = (
    "This export package is a research artifact. It is not an execution package, "
    "trading signal, investment recommendation, or deployment approval. "
    "All results are for research reference only and do not represent future performance. "
    "No live trades are executed, no broker connections are made, and no exchange credentials are included."
)


class ModelResearchExportCreate(BaseModel):
    model_version_id: UUID
    include_private_notes: bool = False


class ModelResearchExportResponse(BaseModel):
    id: UUID
    owner_id: UUID
    model_research_record_id: UUID
    model_version_id: UUID
    status: str
    include_private_notes: bool
    package_checksum: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class PackageManifest(BaseModel):
    package_schema_version: str
    model_research_record_id: UUID
    model_version_id: UUID
    generated_at: datetime
    model_status: str
    evidence_status: str
    engine_version: str
    dataset_snapshot_ids: List[str]
    data_cutoff_at: str
    package_checksum: str
    disclaimer: str
    excluded_capabilities: List[str]
    included_files: List[str]


# ── Phase 6: Pilot Operations & Product Validation ──

ALLOWED_INTENDED_USER_TYPES = (
    "individual_researcher",
    "educator",
    "creator",
    "ai_builder",
    "small_research_team",
)

ALLOWED_USAGE_EVENT_TYPES = (
    "signup_completed",
    "pilot_application_submitted",
    "first_experiment_created",
    "first_run_completed",
    "strategy_locked",
    "forward_simulation_started",
    "paper_simulation_revisited",
    "second_experiment_created",
    "ai_draft_approved",
    "export_generated",
    "support_request_created",
)

ALLOWED_COST_TYPES = (
    "ai_inference",
    "compute",
    "data",
    "storage",
    "support_time",
    "other",
)

ALLOWED_COST_SOURCES = ("actual", "estimate")

ALLOWED_RESOLUTION_STATUSES = ("open", "in_progress", "resolved", "wont_fix")

ALLOWED_SUPPORT_TIERS = ("standard", "priority", "dedicated")

ALLOWED_HYPOTHESIS_STATUSES = (
    "untested",
    "evidence_emerging",
    "insufficient_evidence",
    "contradicted",
)

PILOT_DISCLAIMER = (
    "Internal pilot pricing hypothesis — not public pricing and not market validated."
)


class PilotApplicationCreate(BaseModel):
    intended_user_type: str = Field(
        ..., pattern="^(individual_researcher|educator|creator|ai_builder|small_research_team)$"
    )
    current_research_workflow: Optional[str] = None
    tools_currently_used: Optional[str] = None
    primary_pain_point: Optional[str] = None
    expected_usage_frequency: Optional[str] = Field(None, max_length=100)
    willingness_to_interview: bool = False
    consent_to_contact: bool = False


class PilotApplicationResponse(BaseModel):
    id: UUID
    user_id: UUID
    intended_user_type: str
    current_research_workflow: Optional[str]
    tools_currently_used: Optional[str]
    primary_pain_point: Optional[str]
    expected_usage_frequency: Optional[str]
    willingness_to_interview: bool
    consent_to_contact: bool
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PilotEntitlementCreate(BaseModel):
    user_id: UUID
    pilot_access: bool = True
    research_run_limit: int = Field(default=50, gt=0)
    ai_usage_limit: int = Field(default=20, gt=0)
    export_limit: int = Field(default=5, gt=0)
    forward_simulation_limit: int = Field(default=10, gt=0)
    expiry_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    support_tier: str = Field(default="standard", pattern="^(standard|priority|dedicated)$")


class PilotEntitlementUpdate(BaseModel):
    pilot_access: Optional[bool] = None
    research_run_limit: Optional[int] = Field(None, gt=0)
    ai_usage_limit: Optional[int] = Field(None, gt=0)
    export_limit: Optional[int] = Field(None, gt=0)
    forward_simulation_limit: Optional[int] = Field(None, gt=0)
    expiry_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    support_tier: Optional[str] = Field(None, pattern="^(standard|priority|dedicated)$")


class PilotEntitlementResponse(BaseModel):
    id: UUID
    user_id: UUID
    pilot_access: bool
    research_run_limit: int
    ai_usage_limit: int
    export_limit: int
    forward_simulation_limit: int
    expiry_date: Optional[datetime]
    support_tier: str
    granted_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UsageEventCreate(BaseModel):
    event_type: str = Field(
        ...,
        pattern="^(signup_completed|pilot_application_submitted|first_experiment_created|first_run_completed|strategy_locked|forward_simulation_started|paper_simulation_revisited|second_experiment_created|ai_draft_approved|export_generated|support_request_created)$",
    )
    metadata_json: Optional[dict] = None


class UsageEventResponse(BaseModel):
    id: UUID
    user_id: UUID
    event_type: str
    metadata_json: Optional[dict]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CostRecordCreate(BaseModel):
    owner_id: Optional[UUID] = None
    cost_type: str = Field(
        ..., pattern="^(ai_inference|compute|data|storage|support_time|other)$"
    )
    amount: float = Field(..., gt=0)
    currency: str = Field(default="USD", max_length=10)
    source: str = Field(..., pattern="^(actual|estimate)$")
    method_of_estimation: Optional[str] = None
    linked_entity: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None

    @model_validator(mode="after")
    def validate_estimation_method(self):
        if self.source == "estimate" and not self.method_of_estimation:
            raise ValueError("method_of_estimation is required when source is 'estimate'")
        return self


class CostRecordResponse(BaseModel):
    id: UUID
    owner_id: Optional[UUID]
    cost_type: str
    amount: float
    currency: str
    source: str
    method_of_estimation: Optional[str]
    linked_entity: Optional[str]
    description: Optional[str]
    recorded_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SupportLogCreate(BaseModel):
    user_id: UUID
    category: str = Field(..., min_length=1, max_length=100)
    time_spent_minutes: int = Field(..., gt=0)
    resolution_status: str = Field(
        default="open", pattern="^(open|in_progress|resolved|wont_fix)$"
    )
    linked_entity: Optional[str] = Field(None, max_length=255)
    recurring_issue_tag: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class SupportLogUpdate(BaseModel):
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    time_spent_minutes: Optional[int] = Field(None, gt=0)
    resolution_status: Optional[str] = Field(
        None, pattern="^(open|in_progress|resolved|wont_fix)$"
    )
    linked_entity: Optional[str] = Field(None, max_length=255)
    recurring_issue_tag: Optional[str] = Field(None, max_length=100)
    notes: Optional[str] = None


class SupportLogResponse(BaseModel):
    id: UUID
    user_id: UUID
    category: str
    time_spent_minutes: int
    resolution_status: str
    linked_entity: Optional[str]
    recurring_issue_tag: Optional[str]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProductHypothesisCreate(BaseModel):
    statement: str = Field(..., min_length=1)
    evidence_criteria: Optional[str] = None
    evidence_notes: Optional[str] = None


class ProductHypothesisUpdate(BaseModel):
    statement: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(
        None,
        pattern="^(untested|evidence_emerging|insufficient_evidence|contradicted)$",
    )
    evidence_criteria: Optional[str] = None
    evidence_notes: Optional[str] = None


class ProductHypothesisResponse(BaseModel):
    id: UUID
    statement: str
    status: str
    evidence_criteria: Optional[str]
    evidence_notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PilotFunnelSummary(BaseModel):
    total_users: int
    applications_submitted: int
    applications_approved: int
    first_experiment_created: int
    first_run_completed: int
    strategy_locked: int
    forward_simulation_started: int
    export_generated: int
    support_request_created: int


class ActivationMetrics(BaseModel):
    total_signed_up: int
    activated_at_least_one_experiment: int
    activation_rate: float
    completed_first_run: int
    completion_rate: float


class CostSummary(BaseModel):
    total_cost: float
    total_actual: float
    total_estimate: float
    cost_per_user: float
    cost_per_active_user: float
    breakdown_by_type: dict


class AdminDashboardResponse(BaseModel):
    funnel: PilotFunnelSummary
    activation: ActivationMetrics
    cost_summary: CostSummary
    disclaimer: str = PILOT_DISCLAIMER


class ProductHypothesisDashboardResponse(BaseModel):
    hypotheses: List[ProductHypothesisResponse]
    total: int
    by_status: dict
    disclaimer: str = PILOT_DISCLAIMER
"""Pydantic schemas for research drafts (Phase 2A)."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re


class ResearchDraftBase(BaseModel):
    """Base schema for research draft."""
    title: str = Field(..., min_length=1, max_length=500)
    hypothesis: Optional[str] = None
    falsification_criteria: Optional[str] = None
    strategy_template: Optional[str] = Field(None, max_length=100)
    asset_symbol: Optional[str] = Field(None, max_length=50)
    benchmark_symbol: Optional[str] = Field(None, max_length=50)
    initial_cash: Optional[str] = Field(None, max_length=50)
    commission_rate: Optional[str] = Field(None, max_length=50)
    slippage_rate: Optional[str] = Field(None, max_length=50)
    research_rules: Optional[Dict[str, Any]] = None

    @field_validator("asset_symbol", "benchmark_symbol")
    @classmethod
    def validate_symbol_format(cls, v):
        """Validate symbol format - allow legitimate research terminology."""
        if v is not None:
            if not re.match(r"^[A-Z0-9\-\.]+$", v):
                raise ValueError("Symbol must contain only uppercase letters, numbers, hyphens, and dots")
        return v

    @field_validator("strategy_template")
    @classmethod
    def validate_strategy_template(cls, v):
        """Validate strategy template is one of the allowed templates."""
        if v is not None:
            if v not in ALLOWED_TEMPLATES:
                raise ValueError(f"Strategy template must be one of: {', '.join(ALLOWED_TEMPLATES)}")
        return v


class ResearchDraftCreate(ResearchDraftBase):
    """Schema for creating a research draft."""
    experiment_id: Optional[UUID] = None


class ResearchDraftUpdate(BaseModel):
    """Schema for updating a research draft."""
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    hypothesis: Optional[str] = None
    falsification_criteria: Optional[str] = None
    strategy_template: Optional[str] = Field(None, max_length=100)
    asset_symbol: Optional[str] = Field(None, max_length=50)
    benchmark_symbol: Optional[str] = Field(None, max_length=50)
    initial_cash: Optional[str] = Field(None, max_length=50)
    commission_rate: Optional[str] = Field(None, max_length=50)
    slippage_rate: Optional[str] = Field(None, max_length=50)
    research_rules: Optional[Dict[str, Any]] = None
    experiment_id: Optional[UUID] = None

    @field_validator("asset_symbol", "benchmark_symbol")
    @classmethod
    def validate_symbol_format(cls, v):
        if v is not None:
            if not re.match(r"^[A-Z0-9\-\.]+$", v):
                raise ValueError("Symbol must contain only uppercase letters, numbers, hyphens, and dots")
        return v

    @field_validator("strategy_template")
    @classmethod
    def validate_strategy_template(cls, v):
        if v is not None:
            if v not in ALLOWED_TEMPLATES:
                raise ValueError(f"Strategy template must be one of: {', '.join(ALLOWED_TEMPLATES)}")
        return v


class ResearchDraftResponse(ResearchDraftBase):
    """Schema for research draft response."""
    id: UUID
    user_id: UUID
    experiment_id: Optional[UUID] = None
    source_conversation_id: Optional[UUID] = None
    status: str
    assistant_questions: Optional[List[Dict[str, Any]]] = None
    assistant_responses: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResearchDraftRevisionResponse(BaseModel):
    """Schema for research draft revision response."""
    id: UUID
    draft_id: UUID
    revision_number: str
    title: Optional[str] = None
    hypothesis: Optional[str] = None
    falsification_criteria: Optional[str] = None
    strategy_template: Optional[str] = None
    asset_symbol: Optional[str] = None
    benchmark_symbol: Optional[str] = None
    initial_cash: Optional[str] = None
    commission_rate: Optional[str] = None
    slippage_rate: Optional[str] = None
    research_rules: Optional[Dict[str, Any]] = None
    status: str
    assistant_questions: Optional[List[Dict[str, Any]]] = None
    assistant_responses: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResearchDraftApproval(BaseModel):
    """Schema for approving a research draft."""
    action: str = Field(..., pattern="^(accept|reject)$")
    notes: Optional[str] = None


class ResearchDraftQuestion(BaseModel):
    """Schema for assistant question."""
    field: str
    question: str
    context: Optional[str] = None


class ResearchDraftQuestionsResponse(BaseModel):
    """Schema for assistant questions response."""
    draft_id: UUID
    questions: List[ResearchDraftQuestion]
    generated_at: datetime


class ResearchDraftAnswer(BaseModel):
    """Schema for answering assistant questions."""
    answers: Dict[str, str]


class MockAssistantProviderRequest(BaseModel):
    """Schema for mock assistant provider request."""
    draft_fields: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None
