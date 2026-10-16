"""Pydantic models for method checks."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from app.lib.timeutil import utcnow


class CheckResult(BaseModel):
    """Single rule check result."""
    rule_id: str = Field(..., description="Rule identifier (e.g., RC-001)")
    rule_version: str = Field(default="1.0", description="Rule version")
    status: str = Field(..., description="PASS, WARNING, INSUFFICIENT_INFORMATION, NOT_APPLICABLE")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Evidence data")
    explanation: str = Field(default="", description="Deterministic explanation")
    limitations: List[str] = Field(default_factory=list, description="Known limitations")


class MethodReviewResponse(BaseModel):
    """Complete method review response."""
    rule_set_version: str = Field(default="1.0", description="Rule set version")
    experiment_id: UUID = Field(..., description="Experiment ID")
    version_id: Optional[UUID] = Field(None, description="Version ID (nullable if no version)")
    run_id: Optional[UUID] = Field(None, description="Run ID (nullable if no run)")
    run_selection: str = Field(
        default="latest_completed_by_completed_at_then_id",
        description="Run selection policy"
    )
    config_hash_match: Optional[bool] = Field(
        None,
        description="Config hash match from RC-010 (true/false/null)"
    )
    completed_at_available: Optional[bool] = Field(
        None,
        description="Whether the selected run has a valid completed_at timestamp. "
                    "Null if no run selected. False means ordering fell back to ID, "
                    "not proof of chronological recency."
    )
    generated_at: datetime = Field(default_factory=utcnow, description="Generation timestamp")
    checks: List[CheckResult] = Field(default_factory=list, description="Ordered list of check results")
