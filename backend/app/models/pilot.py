import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base
import enum


class IntendedUserType(str, enum.Enum):
    individual_researcher = "individual_researcher"
    educator = "educator"
    creator = "creator"
    ai_builder = "ai_builder"
    small_research_team = "small_research_team"


class PilotApplicationStatus(str, enum.Enum):
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    waitlisted = "waitlisted"


class UsageEventType(str, enum.Enum):
    signup_completed = "signup_completed"
    pilot_application_submitted = "pilot_application_submitted"
    first_experiment_created = "first_experiment_created"
    first_run_completed = "first_run_completed"
    strategy_locked = "strategy_locked"
    forward_simulation_started = "forward_simulation_started"
    paper_simulation_revisited = "paper_simulation_revisited"
    second_experiment_created = "second_experiment_created"
    ai_draft_approved = "ai_draft_approved"
    export_generated = "export_generated"
    support_request_created = "support_request_created"


class CostType(str, enum.Enum):
    ai_inference = "ai_inference"
    compute = "compute"
    data = "data"
    storage = "storage"
    support_time = "support_time"
    other = "other"


class CostSource(str, enum.Enum):
    actual = "actual"
    estimate = "estimate"


class ResolutionStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    wont_fix = "wont_fix"


class SupportTier(str, enum.Enum):
    standard = "standard"
    priority = "priority"
    dedicated = "dedicated"


class HypothesisStatus(str, enum.Enum):
    untested = "untested"
    evidence_emerging = "evidence_emerging"
    insufficient_evidence = "insufficient_evidence"
    contradicted = "contradicted"


class PilotApplication(Base):
    __tablename__ = "pilot_applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    intended_user_type = Column(SAEnum(IntendedUserType), nullable=False)
    current_research_workflow = Column(Text, nullable=True)
    tools_currently_used = Column(Text, nullable=True)
    primary_pain_point = Column(Text, nullable=True)
    expected_usage_frequency = Column(String(100), nullable=True)
    willingness_to_interview = Column(Boolean, default=False, nullable=False)
    consent_to_contact = Column(Boolean, default=False, nullable=False)
    status = Column(SAEnum(PilotApplicationStatus), default=PilotApplicationStatus.submitted, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PilotEntitlement(Base):
    __tablename__ = "pilot_entitlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    pilot_access = Column(Boolean, default=True, nullable=False)
    research_run_limit = Column(Integer, default=50, nullable=False)
    ai_usage_limit = Column(Integer, default=20, nullable=False)
    export_limit = Column(Integer, default=5, nullable=False)
    forward_simulation_limit = Column(Integer, default=10, nullable=False)
    expiry_date = Column(DateTime, nullable=True)
    support_tier = Column(SAEnum(SupportTier), default=SupportTier.standard, nullable=False)
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(SAEnum(UsageEventType), nullable=False, index=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class CostRecord(Base):
    __tablename__ = "cost_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    cost_type = Column(SAEnum(CostType), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    source = Column(SAEnum(CostSource), nullable=False)
    method_of_estimation = Column(Text, nullable=True)
    linked_entity = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SupportLog(Base):
    __tablename__ = "support_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    time_spent_minutes = Column(Integer, nullable=False)
    resolution_status = Column(SAEnum(ResolutionStatus), default=ResolutionStatus.open, nullable=False)
    linked_entity = Column(String(255), nullable=True)
    recurring_issue_tag = Column(String(100), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ProductHypothesis(Base):
    __tablename__ = "product_hypotheses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    statement = Column(Text, nullable=False)
    status = Column(SAEnum(HypothesisStatus), default=HypothesisStatus.untested, nullable=False)
    evidence_criteria = Column(Text, nullable=True)
    evidence_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
