import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, Integer, Float, ForeignKey, Enum as SAEnum, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class ModelStatus(str, enum.Enum):
    draft = "draft"
    researching = "researching"
    historical_test_complete = "historical_test_complete"
    locked_for_simulation = "locked_for_simulation"
    forward_observation = "forward_observation"
    evidence_insufficient = "evidence_insufficient"
    retired = "retired"
    revoked = "revoked"


class ModelType(str, enum.Enum):
    rule_based = "rule_based"
    prompt_based_research_assistant = "prompt_based_research_assistant"
    future_external_model_reference = "future_external_model_reference"


class EvaluationType(str, enum.Enum):
    historical = "historical"
    forward_simulation = "forward_simulation"
    methodology_review = "methodology_review"
    behavior_consistency = "behavior_consistency"


class EvaluatorType(str, enum.Enum):
    system = "system"
    human = "human"
    future_ai_assistant = "future_ai_assistant"


class MemoryType(str, enum.Enum):
    explicit_preference = "explicit_preference"
    approved_constraint = "approved_constraint"
    recurring_methodology_issue = "recurring_methodology_issue"
    reflection = "reflection"
    accepted_ai_draft = "accepted_ai_draft"
    rejected_ai_draft = "rejected_ai_draft"


class PrivacyScope(str, enum.Enum):
    private_only = "private_only"
    approved_for_personal_ai = "approved_for_personal_ai"


class ExportStatus(str, enum.Enum):
    pending = "pending"
    generating = "generating"
    completed = "completed"
    failed = "failed"


class ModelResearchRecord(Base):
    __tablename__ = "model_research_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    purpose = Column(Text, nullable=False)
    status = Column(SAEnum(ModelStatus), default=ModelStatus.draft, nullable=False)
    allowed_asset_universe = Column(JSONB, nullable=True)
    allowed_research_actions = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    versions = relationship("ModelVersion", back_populates="model_research_record", order_by="ModelVersion.version_number")
    evaluations = relationship("ModelEvaluation", back_populates="model_research_record")


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_research_record_id = Column(UUID(as_uuid=True), ForeignKey("model_research_records.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    model_type = Column(SAEnum(ModelType), nullable=False)
    model_specification_json = Column(JSONB, nullable=False)
    prompt_version = Column(String(50), nullable=True)
    input_schema_version = Column(String(50), nullable=True)
    output_schema_version = Column(String(50), nullable=True)
    linked_experiment_version_ids = Column(JSONB, nullable=True)
    linked_dataset_snapshot_ids = Column(JSONB, nullable=True)
    linked_simulation_session_ids = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    locked_at = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revocation_reason = Column(Text, nullable=True)
    model_card_id = Column(UUID(as_uuid=True), ForeignKey("model_cards.id"), nullable=True)

    model_research_record = relationship("ModelResearchRecord", back_populates="versions")
    model_card = relationship("ModelCard", foreign_keys=[model_card_id])
    evaluations = relationship("ModelEvaluation", back_populates="model_version")


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False)
    model_research_record_id = Column(UUID(as_uuid=True), ForeignKey("model_research_records.id"), nullable=False)
    evaluation_type = Column(SAEnum(EvaluationType), nullable=False)
    result_summary = Column(Text, nullable=True)
    metrics_json = Column(JSONB, nullable=True)
    warning_flags = Column(JSONB, nullable=True)
    evaluation_date = Column(DateTime, nullable=False)
    data_cutoff_at = Column(String(10), nullable=False)
    engine_version = Column(String(50), nullable=False)
    evaluator = Column(SAEnum(EvaluatorType), nullable=False)
    methodology_version = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    model_version = relationship("ModelVersion", back_populates="evaluations")
    model_research_record = relationship("ModelResearchRecord", back_populates="evaluations")


class PersonalResearchMemory(Base):
    __tablename__ = "personal_research_memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    memory_type = Column(SAEnum(MemoryType), nullable=False)
    content = Column(Text, nullable=False)
    source_entity_type = Column(String(50), nullable=True)
    source_entity_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    user_confirmed_at = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    privacy_scope = Column(SAEnum(PrivacyScope), default=PrivacyScope.private_only, nullable=False)
    general_model_training_opt_in = Column(Boolean, default=False, nullable=False)

    @property
    def is_confirmed(self):
        return self.user_confirmed_at is not None


class ModelCard(Base):
    __tablename__ = "model_cards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False)
    purpose = Column(Text, nullable=False)
    intended_research_use = Column(Text, nullable=False)
    excluded_use = Column(Text, nullable=False)
    data_sources = Column(JSONB, nullable=False)
    data_cutoff = Column(String(10), nullable=False)
    linked_experiments = Column(JSONB, nullable=True)
    evaluation_evidence = Column(JSONB, nullable=True)
    known_limitations = Column(JSONB, nullable=False)
    risk_flags = Column(JSONB, nullable=True)
    version_history = Column(JSONB, nullable=True)
    revocation_process = Column(Text, nullable=False)
    disclaimer = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    versions = relationship("ModelVersion", foreign_keys=[model_version_id])


class ModelResearchExport(Base):
    __tablename__ = "model_research_exports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    model_research_record_id = Column(UUID(as_uuid=True), ForeignKey("model_research_records.id"), nullable=False)
    model_version_id = Column(UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False)
    status = Column(SAEnum(ExportStatus), default=ExportStatus.pending, nullable=False)
    include_private_notes = Column(Boolean, default=False, nullable=False)
    package_json = Column(JSONB, nullable=True)
    package_checksum = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    model_research_record = relationship("ModelResearchRecord")
    model_version = relationship("ModelVersion")
