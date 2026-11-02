"""Agent strategy learning: candidate signals, outcomes and dataset snapshots.

This is deliberately separate from ``personal_decision_mimicry``. The task is
to judge a rule strategy's own entry candidates (take or skip assistance), not
to imitate the owner's choices and not to forecast market direction.

Three additive tables:

* ``agent_candidates`` — one row per rule signal, with the feature snapshot and
  the data-availability instant that make it reproducible.
* ``agent_candidate_outcomes`` — one row per candidate, separating what was
  actually executed from a deterministic counterfactual and from an unknown or
  immature outcome. A non-take state is never stored as a loss.
* ``agent_dataset_snapshots`` — a manifest of candidates, exclusions and the
  chronological split plan. A refused snapshot records why it was refused and
  is never a training set.

Nothing here reads or writes paper fills. Actual paper executions are linked
only by identifier for traceability.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.lib.timeutil import utcnow


class AgentCandidate(Base):
    __tablename__ = "agent_candidates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    strategy_version_id = Column(
        UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False
    )
    instrument_id = Column(String(64), nullable=False)

    candidate_kind = Column(String(16), nullable=False, default="entry")
    rule_key = Column(String(64), nullable=False)
    rule_parameters_json = Column(JSONB, nullable=False)
    logged_action = Column(String(32), nullable=False, default="rule_entry_signal")
    created_by_actor_type = Column(String(16), nullable=False, default="unknown")

    signal_date = Column(String(10), nullable=False)
    decision_instant = Column(DateTime, nullable=False)
    decision_instant_source = Column(String(32), nullable=False)
    data_cutoff_at = Column(String(10), nullable=False)

    feature_schema_version = Column(String(64), nullable=False)
    feature_names_json = Column(JSONB, nullable=False)
    features_json = Column(JSONB, nullable=False)
    feature_bar_date = Column(String(10), nullable=True)

    execution_policy_version = Column(String(64), nullable=False)
    execution_feasibility = Column(String(24), nullable=False)
    rejection_reason = Column(String(64), nullable=True)

    dedupe_key = Column(String(220), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "strategy_version_id",
            "instrument_id",
            "dedupe_key",
            name="uq_agent_candidate_identity",
        ),
        Index("ix_agent_candidates_version", "strategy_version_id"),
        Index("ix_agent_candidates_signal_date", "signal_date"),
    )


class AgentCandidateOutcome(Base):
    __tablename__ = "agent_candidate_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_candidates.id"),
        nullable=False,
    )

    # An outcome is append-only per candidate: a new evaluation becomes a new
    # version and the previous version is superseded, never rewritten, so a
    # frozen snapshot keeps pointing at the exact result it was built from.
    version_seq = Column(Integer, nullable=False, default=1, server_default="1")
    is_current = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    superseded_at = Column(DateTime, nullable=True)
    # observed_round_trip | counterfactual_replay | exit_policy_mismatch | ...
    selection_reason = Column(String(64), nullable=True)

    # observed | counterfactual | immature | unavailable
    outcome_status = Column(String(16), nullable=False)
    # mature | immature | unavailable
    label_maturity = Column(String(16), nullable=False)
    label_eligible = Column(Boolean, nullable=False, default=False)
    # paper_fill | counterfactual_replay | none
    outcome_source = Column(String(32), nullable=False)
    counterfactual = Column(Boolean, nullable=False, default=False)

    observed_order_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_orders.id"), nullable=True
    )
    observed_fill_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_fills.id"), nullable=True
    )

    entry_execution_date = Column(String(10), nullable=True)
    exit_execution_date = Column(String(10), nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    gross_return_pct = Column(Float, nullable=True)
    net_return_pct = Column(Float, nullable=True)

    cost_model_version = Column(String(64), nullable=False)
    cost_model_json = Column(JSONB, nullable=False)
    exit_policy = Column(String(64), nullable=False)
    exclusion_reason = Column(String(64), nullable=True)
    notes_json = Column(JSONB, nullable=True)
    evaluated_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "version_seq",
            name="uq_agent_candidate_outcome_version",
        ),
        # Exactly one selected result per candidate. A partial unique index
        # enforces the rule in the database, not only in the service layer.
        Index(
            "uq_agent_candidate_outcome_current",
            "candidate_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current"),
        ),
        Index("ix_agent_candidate_outcome_candidate", "candidate_id"),
    )


class AgentDatasetSnapshot(Base):
    __tablename__ = "agent_dataset_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    strategy_version_id = Column(
        UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False
    )

    purpose = Column(String(64), nullable=False, default="agent_strategy_screening")
    # The authorization basis actually used, not necessarily owner consent.
    # ``not_configured`` marks a refused audit row; ``test_only_fixture`` marks
    # a dataset frozen under the development authorization fixture. No value
    # here ever means "training is approved".
    consent_policy_version = Column(String(64), nullable=False)
    # not_configured | test_only_fixture | active | revoked
    consent_status = Column(String(32), nullable=False)
    # ready | refused
    status = Column(String(16), nullable=False)

    candidate_ids_json = Column(JSONB, nullable=False)
    rows_json = Column(JSONB, nullable=False)
    feature_names_json = Column(JSONB, nullable=False)
    label_definition_json = Column(JSONB, nullable=False)
    exclusion_breakdown_json = Column(JSONB, nullable=False)
    outcome_distribution_json = Column(JSONB, nullable=False)
    split_manifest_json = Column(JSONB, nullable=False)

    included_count = Column(Integer, nullable=False, default=0)
    excluded_count = Column(Integer, nullable=False, default=0)
    date_range_start = Column(String(10), nullable=True)
    date_range_end = Column(String(10), nullable=True)
    data_cutoff_at = Column(String(10), nullable=True)
    checksum = Column(String(64), nullable=False)
    failure_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_agent_snapshots_version", "strategy_version_id"),
        Index("ix_agent_snapshots_status", "status"),
    )


class AgentStrategyConsent(Base):
    """One row per consent decision the owner made for themselves.

    ``owner_id`` and ``accepted_by_user_id`` are stored separately and the
    service layer requires them to be the same authenticated user, so a request
    cannot record consent on someone else's behalf.
    """

    __tablename__ = "agent_strategy_consents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    accepted_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    purpose = Column(String(64), nullable=False, default="agent_strategy_screening")
    policy_version = Column(String(64), nullable=False)
    acceptance_text_version = Column(String(64), nullable=False)
    acknowledgement_json = Column(JSONB, nullable=False)
    # active | superseded | revoked
    status = Column(String(16), nullable=False, default="active")
    accepted_at = Column(DateTime, default=utcnow, nullable=False)
    superseded_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoke_reason = Column(String(64), nullable=True)

    __table_args__ = (
        # At most one live consent per owner and purpose, enforced by the DB.
        Index(
            "uq_agent_strategy_consent_active",
            "owner_id",
            "purpose",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_agent_strategy_consent_owner", "owner_id"),
    )


class AgentStrategyTrainingJob(Base):
    """A bounded training run over one frozen dataset snapshot.

    The row keeps the artifact checksum, dataset checksum and every lineage
    field the training contract requires, so a later inference can prove which
    exact dataset and artifact produced it. ``activation_status`` never becomes
    ACTIVE in this slice.
    """

    __tablename__ = "agent_strategy_training_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    snapshot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_dataset_snapshots.id"),
        nullable=False,
    )
    strategy_version_id = Column(
        UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False
    )
    # completed | failed | refused
    status = Column(String(16), nullable=False)
    training_contract_version = Column(String(64), nullable=False)
    derived_label_version = Column(String(64), nullable=False)
    dataset_checksum = Column(String(64), nullable=False)
    hyperparameters_json = Column(JSONB, nullable=False)
    hyperparameters_hash = Column(String(64), nullable=False)
    row_counts_json = Column(JSONB, nullable=False)
    metrics_json = Column(JSONB, nullable=True)
    lineage_json = Column(JSONB, nullable=False)
    artifact_path = Column(String(512), nullable=True)
    artifact_checksum = Column(String(64), nullable=True)
    artifact_metadata_checksum = Column(String(64), nullable=True)
    activation_status = Column(
        String(32), nullable=False, default="NOT_APPROVED"
    )
    failure_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        # Identical retries return the completed job instead of a copy.
        Index(
            "uq_agent_training_job_completed",
            "owner_id",
            "snapshot_id",
            "hyperparameters_hash",
            unique=True,
            postgresql_where=text("status = 'completed'"),
            sqlite_where=text("status = 'completed'"),
        ),
        Index("ix_agent_training_jobs_owner", "owner_id"),
        Index("ix_agent_training_jobs_snapshot", "snapshot_id"),
    )
